#
# Copyright (c) 2016 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
import inspect
import itertools
import os
import shutil
import subprocess
import sys
import tempfile
import typing
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import ClassVar, Optional, Union

from .crosscompileproject import CrossCompileProject
from .llvm import BuildLLVMMonoRepoBase
from ..project import (
    BuildType,
    CheriConfig,
    ComputedDefaultValue,
    CPUArchitecture,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
    MakeOptions,
    Project,
    ReuseOtherProjectRepository,
)
from ..simple_project import SimpleProject, TargetAliasWithDependencies, _clear_line_sequence, flush_stdio
from ...config.compilation_targets import CompilationTargets, FreeBSDTargetInfo
from ...config.loader import ConfigOptionBase
from ...config.target_info import AutoVarInit, CrossCompileTarget
from ...config.target_info import CompilerType as FreeBSDToolchainKind
from ...processutils import latest_system_clang_tool, print_command
from ...utils import OSInfo, ThreadJoiner, cached_property, classproperty, is_jenkins_build


def _arch_suffixed_custom_install_dir(prefix: str) -> "ComputedDefaultValue[Path]":
    def inner(config: CheriConfig, project: Project):
        xtarget = project.crosscompile_target
        # Check that we don't accidentally inherit the FreeBSD install directories for CheriBSD
        if not isinstance(project, BuildCHERIBSD) and xtarget.is_hybrid_or_purecap_cheri():
            raise ValueError(f"{project.target} should not build for CHERI architectures")
        return config.output_root / (prefix + project.build_configuration_suffix(xtarget))
    return ComputedDefaultValue(function=inner, as_string="$INSTALL_ROOT/" + prefix + "-<arch>")


def freebsd_reuse_build_dir(config: CheriConfig, project: "SimpleProject") -> Path:
    build_freebsd = BuildFreeBSD.get_instance(project, config)
    return build_freebsd.default_build_dir(config, build_freebsd)


def cheribsd_reuse_build_dir(config: CheriConfig, project: "SimpleProject") -> Path:
    build_cheribsd = BuildCHERIBSD.get_instance(project, config)
    return build_cheribsd.default_build_dir(config, build_cheribsd)


def _clear_dangerous_make_env_vars() -> None:
    # remove any environment variables that could interfere with bmake running
    for k, v in os.environ.copy().items():
        if k in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKE_TERMERR", "MAKE_TERMOUT", "MAKE"):
            os.unsetenv(k)
            del os.environ[k]


class KernelABI(Enum):
    NOCHERI = "no-cheri"
    HYBRID = "hybrid"
    PURECAP = "purecap"


class ConfigPlatform(Enum):
    QEMU = "qemu"
    FVP = "fvp"
    GFE = "gfe"
    AWS = "aws-f1"

    @classmethod
    def fpga_platforms(cls) -> "set[ConfigPlatform]":
        return {cls.GFE, cls.AWS}


class CheriBSDConfig:
    """
    Cheribuild configuration descriptor for a CheriBSD kernel configuration file
    """

    def __init__(self, kernconf: str, platforms: "set[ConfigPlatform]", kernel_abi=KernelABI.NOCHERI, default=False,
                 caprevoke=False, mfsroot=False, debug=False, benchmark=False, fuzzing=False, fett=False):
        self.kernconf = kernconf
        self.platforms = platforms
        self.kernel_abi = kernel_abi
        self.default = default
        self.caprevoke = caprevoke
        self.mfsroot = mfsroot
        self.debug = debug
        self.benchmark = benchmark
        self.fuzzing = fuzzing
        self.fett = fett

    def __repr__(self) -> str:
        flags = [key for key, val in self.__dict__.items() if isinstance(val, bool) and val]
        return "CheriBSDConfig({kernconf} {platform}:{kernel_abi} [{flags}])".format(
            kernconf=self.kernconf, platform=self.platforms, kernel_abi=self.kernel_abi.value, flags=" ".join(flags))


class KernelConfigFactory:
    kernconf_components: "typing.OrderedDict[str, Optional[str]]" = OrderedDict([(k, None) for k in (
        "kabi_name", "caprevoke", "platform_name", "flags")])
    separator: str = "_"
    platform_name_map: "dict[ConfigPlatform, Optional[str]]" = {}

    def get_kabi_name(self, kernel_abi) -> Optional[str]:
        if kernel_abi == KernelABI.NOCHERI:
            return None
        elif kernel_abi == KernelABI.HYBRID:
            return "CHERI"
        elif kernel_abi == KernelABI.PURECAP:
            return f"CHERI{self.separator}PURECAP"

    def get_platform_name(self, platforms: "set[ConfigPlatform]") -> Optional[str]:
        for platform in platforms:
            # Only use the first matching platform in the set
            if platform in self.platform_name_map:
                return self.platform_name_map[platform]
        assert False, "Should not be reached..."

    def get_flag_names(self, platforms: "set[ConfigPlatform]", kernel_abi: KernelABI, mfsroot=False, fuzzing=False,
                       benchmark=False, default=False, caprevoke=False):
        flags = []
        if mfsroot:
            flags.append(f"MFS{self.separator}ROOT")
        if fuzzing:
            flags.append("FUZZ")
        if benchmark:
            flags.append("NODEBUG")
        return flags

    def _prepare_kernconf_context(self, platforms: "set[ConfigPlatform]", kernel_abi, base_context=None, **kwargs):
        if base_context is None:
            base_context = self.kernconf_components
        ctx: "typing.OrderedDict[str, Optional[str]]" = OrderedDict(base_context)
        if "kabi_name" in ctx:
            ctx["kabi_name"] = self.get_kabi_name(kernel_abi)
        if "platform_name" in ctx:
            ctx["platform_name"] = self.get_platform_name(platforms)
        if "caprevoke" in ctx and kwargs.get("caprevoke", False):
            ctx["caprevoke"] = "CAPREVOKE"
        if "flags" in ctx:
            flag_list = self.get_flag_names(platforms, kernel_abi, **kwargs)
            if flag_list:
                ctx["flags"] = self.separator.join(flag_list)
        return ctx

    def make_config(self, platforms: "set[ConfigPlatform]", kernel_abi, base_context=None, **kwargs):
        kernconf_ctx = self._prepare_kernconf_context(platforms, kernel_abi, base_context=base_context, **kwargs)
        valid_ctx_items = (v for v in kernconf_ctx.values() if v is not None)
        kernconf = self.separator.join(valid_ctx_items)
        return CheriBSDConfig(kernconf, platforms, kernel_abi=kernel_abi, **kwargs)


class RISCVKernelConfigFactory(KernelConfigFactory):
    kernconf_components: "typing.OrderedDict[str, Optional[str]]" = OrderedDict([(k, None) for k in (
        "kabi_name", "caprevoke", "platform_name", "flags")])
    separator: str = "-"
    platform_name_map: "dict[ConfigPlatform, Optional[str]]" = {
        ConfigPlatform.QEMU: "QEMU",
        ConfigPlatform.GFE: "GFE",
        ConfigPlatform.AWS: None,
    }

    def get_flag_names(self, platforms: "set[ConfigPlatform]", kernel_abi: KernelABI, default=False, caprevoke=False,
                       mfsroot=False, debug=False, benchmark=False, fuzzing=False, fett=False):
        if ConfigPlatform.GFE in platforms:
            # Suppress mfsroot flag as it is implied for GFE configurations
            mfsroot = False
        flags = []
        if fett:
            flags.append("FETT")
        flags += super().get_flag_names(platforms, kernel_abi, mfsroot=mfsroot, fuzzing=fuzzing, benchmark=benchmark,
                                        caprevoke=caprevoke)
        return flags

    def make_all(self) -> "list[CheriBSDConfig]":
        configs = []
        # Generate QEMU kernels
        for kernel_abi in KernelABI:
            configs.append(self.make_config({ConfigPlatform.QEMU}, kernel_abi, default=True))
            configs.append(self.make_config({ConfigPlatform.QEMU}, kernel_abi, benchmark=True, default=True))
            configs.append(self.make_config({ConfigPlatform.QEMU}, kernel_abi, mfsroot=True, default=True))
            configs.append(
                self.make_config({ConfigPlatform.QEMU}, kernel_abi, mfsroot=True, benchmark=True, default=True))
        # Generate FPGA kernels
        for kernel_abi in KernelABI:
            configs.append(self.make_config({ConfigPlatform.GFE}, kernel_abi, mfsroot=True, default=True))
            configs.append(
                self.make_config({ConfigPlatform.GFE}, kernel_abi, mfsroot=True, benchmark=True, default=True))
            configs.append(self.make_config({ConfigPlatform.AWS}, kernel_abi, fett=True))
            configs.append(self.make_config({ConfigPlatform.AWS}, kernel_abi, fett=True, benchmark=True))

        # Generate default FETT kernels
        configs.append(self.make_config({ConfigPlatform.QEMU}, KernelABI.HYBRID, fett=True, default=True))

        # Caprevoke kernels
        for kernel_abi in KernelABI:
            configs.append(self.make_config({ConfigPlatform.QEMU}, kernel_abi, caprevoke=True, default=True))
            configs.append(
                self.make_config({ConfigPlatform.QEMU}, kernel_abi, caprevoke=True, benchmark=True, default=True))
            configs.append(
                self.make_config({ConfigPlatform.QEMU}, kernel_abi, caprevoke=True, mfsroot=True, default=True))
            configs.append(
                self.make_config({ConfigPlatform.QEMU}, kernel_abi, caprevoke=True, benchmark=True, mfsroot=True,
                                 default=True))
            configs.append(self.make_config({ConfigPlatform.GFE}, kernel_abi, caprevoke=True, mfsroot=True))
            configs.append(self.make_config({ConfigPlatform.AWS}, kernel_abi, fett=True, caprevoke=True))

        return configs


class AArch64KernelConfigFactory(KernelConfigFactory):
    kernconf_components: "typing.OrderedDict[str, Optional[str]]" = OrderedDict([(k, None) for k in (
        "platform_name", "kabi_name", "caprevoke", "flags")])
    separator: str = "-"
    platform_name_map: "dict[ConfigPlatform, Optional[str]]" = {
        ConfigPlatform.QEMU: "GENERIC",
        ConfigPlatform.FVP: "GENERIC",
    }

    def get_kabi_name(self, kernel_abi) -> Optional[str]:
        if kernel_abi == KernelABI.NOCHERI:
            return None
        elif kernel_abi == KernelABI.HYBRID:
            return "MORELLO"
        elif kernel_abi == KernelABI.PURECAP:
            return f"MORELLO{self.separator}PURECAP"

    def make_all(self) -> "list[CheriBSDConfig]":
        configs = []
        # Generate QEMU/FVP kernels
        for kernel_abi in KernelABI:
            configs.append(self.make_config({ConfigPlatform.QEMU, ConfigPlatform.FVP}, kernel_abi, default=True))
            configs.append(self.make_config({ConfigPlatform.QEMU, ConfigPlatform.FVP}, kernel_abi, default=True,
                                            benchmark=True))
            configs.append(self.make_config({ConfigPlatform.QEMU, ConfigPlatform.FVP}, kernel_abi, default=True,
                                            mfsroot=True))
        # Caprevoke kernels
        for kernel_abi in KernelABI:
            configs.append(self.make_config({ConfigPlatform.QEMU, ConfigPlatform.FVP}, kernel_abi, default=True,
                                            caprevoke=True))
            configs.append(self.make_config({ConfigPlatform.QEMU, ConfigPlatform.FVP}, kernel_abi, default=True,
                                            caprevoke=True, benchmark=True))
            configs.append(self.make_config({ConfigPlatform.QEMU, ConfigPlatform.FVP}, kernel_abi, default=True,
                                            caprevoke=True, mfsroot=True))

        return configs


def filter_kernel_configs(configs: "list[CheriBSDConfig]", *, platform: "Optional[ConfigPlatform]",
                          kernel_abi: Optional[KernelABI], **filter_kwargs) -> "typing.Sequence[CheriBSDConfig]":
    """
    Helper function to filter kernel configuration lists.
    Keyword filter arguments are mapped to CheriBSDConfig properties.
    Filter arguments may be "*" or "any", to override defaults and match
    all possible values of the property.
    """
    if platform is not None:
        configs = [c for c in configs if platform in c.platforms]
    if kernel_abi is not None:
        configs = [c for c in configs if c.kernel_abi == kernel_abi]
    for key, val in filter_kwargs.items():
        if val == "*" or val == "any":
            # Match any attribute value, skip
            continue
        else:
            configs = [c for c in configs if getattr(c, key) == val]
    return configs


class CheriBSDConfigTable:
    """
    Maintain lists of kernel configurations for each target we support.
    The following requirements need to be enforced in order to avoid missing
    default configurations:
    - For each supported platform there should be at least a configuration marked as default.
      There may be multiple defaults with debug/mfsroot/benchmark flags.
    - The platforms are used to select configurations available to run jobs.
    - Default configurations must select an unique (platform, kernel, flags) set, non-default
      configurations may select multiple kernels.
    """

    X86_CONFIGS: "list[CheriBSDConfig]" = [
        CheriBSDConfig("GENERIC", {ConfigPlatform.QEMU}, default=True),
    ]
    MIPS_CONFIGS: "list[CheriBSDConfig]" = [
        CheriBSDConfig("MALTA64", {ConfigPlatform.QEMU}, default=True),
    ]

    @classmethod
    def get_target_configs(cls, xtarget: CrossCompileTarget) -> "list[CheriBSDConfig]":
        if xtarget.is_any_x86():
            return cls.X86_CONFIGS
        elif xtarget.is_mips(include_purecap=False):
            return cls.MIPS_CONFIGS
        elif xtarget.is_riscv(include_purecap=True):
            return RISCVKernelConfigFactory().make_all()
        elif xtarget.is_aarch64(include_purecap=True):
            return AArch64KernelConfigFactory().make_all()
        else:
            raise ValueError("Invalid target architecture")

    @classmethod
    def get_entry(cls, xtarget, name: str) -> Optional[CheriBSDConfig]:
        for c in cls.get_target_configs(xtarget):
            if c.kernconf == name:
                return c
        return None

    @classmethod
    def get_default(cls, xtarget, platform: ConfigPlatform, kernel_abi: KernelABI, **filter_kwargs) -> CheriBSDConfig:
        """
        Return an unique default configuration for the given platform/kernelABI
        with optional extra filters.
        It is a fatal failure if 0 or more than one configurations exist.
        """
        configs = cls.get_configs(xtarget, platform=platform, kernel_abi=kernel_abi, default=True, **filter_kwargs)
        assert len(configs) != 0, "No matching default kernel configuration"
        assert len(configs) == 1, f"Too many default kernel configurations {configs}"
        return configs[0]

    @classmethod
    def get_configs(cls, xtarget, *, platform: "Optional[ConfigPlatform]", kernel_abi: "Optional[KernelABI]",
                    **filter_kwargs):
        """
        Return all configurations for a combination of target, platform and kernel ABI.
        This filters out all the specialized configuration flags defaulting all of them
        to False.
        """
        filter_kwargs.setdefault("caprevoke", False)
        filter_kwargs.setdefault("debug", False)
        filter_kwargs.setdefault("benchmark", False)
        filter_kwargs.setdefault("fett", False)
        filter_kwargs.setdefault("fuzzing", False)
        filter_kwargs.setdefault("mfsroot", False)
        return cls.get_all_configs(xtarget, platform=platform, kernel_abi=kernel_abi, **filter_kwargs)

    @classmethod
    def get_all_configs(cls, xtarget, *, platform: "Optional[ConfigPlatform]", kernel_abi: "Optional[KernelABI]",
                        **filter_kwargs):
        """
        Return all available configurations for a combination of
        target, group and kernel ABI filtered using kwargs.
        """
        return filter_kernel_configs(cls.get_target_configs(xtarget), platform=platform, kernel_abi=kernel_abi,
                                     **filter_kwargs)


class BuildFreeBSDBase(Project):
    do_not_add_to_targets: bool = True  # base class only
    repository: GitRepository = GitRepository("https://github.com/freebsd/freebsd-src.git", default_branch="main")
    make_kind: MakeCommandKind = MakeCommandKind.BsdMake
    skip_world: bool = False
    is_large_source_repository: bool = True
    include_os_in_target_suffix: bool = False  # Avoid adding target_info.os_prefix to the target name.
    has_installsysroot_target: bool = False
    default_extra_make_options: "list[str]" = [
        # "-DWITHOUT_HTML",  # should not be needed
        # "-DWITHOUT_SENDMAIL", "-DWITHOUT_MAIL",  # no need for sendmail
        # "-DWITHOUT_SVNLITE",  # no need for SVN
        # "-DWITHOUT_GAMES",  # not needed
        # "-DWITHOUT_MAN",  # seems to be a majority of the install time
        # "-DWITH_FAST_DEPEND",  # no separate make depend step, do it while compiling
        # "-DWITH_INSTALL_AS_USER", should be enforced by -DNO_ROOT
        # "-DWITH_DIRDEPS_BUILD", "-DWITH_DIRDEPS_CACHE",  # experimental fast build options
        # "-DWITH_LIBCHERI_JEMALLOC"  # use jemalloc instead of -lmalloc_simple
    ]
    has_optional_tests: bool = True
    default_build_tests: bool = True
    default_build_type: BuildType = BuildType.RELWITHDEBINFO
    build_toolchain: "FreeBSDToolchainKind"  # Set in subclass
    # Define the command line arguments here to make type checkers happy.
    minimal: "ClassVar[bool]"
    build_tests: "ClassVar[bool]"
    extra_make_args: "ClassVar[list[str]]"

    @property
    def use_bootstrapped_toolchain(self) -> bool:
        return self.build_toolchain == FreeBSDToolchainKind.BOOTSTRAPPED

    @classmethod
    def can_build_with_ccache(cls) -> bool:
        return True

    @property
    def crossbuild(self) -> bool:
        return not OSInfo.IS_FREEBSD

    @classmethod
    def setup_config_options(cls, kernel_only_target=False, **kwargs) -> None:
        super().setup_config_options(**kwargs)
        cls.extra_make_args = cls.add_list_option(
            "build-options", default=cls.default_extra_make_options, metavar="OPTIONS",
            help="Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf` "
                 "for more info.", show_help=True)
        cls.debug_kernel = cls.add_bool_option("debug-kernel", help="Build the kernel with -O0 and verbose boot output",
                                               show_help=False)
        if kernel_only_target:
            cls.minimal = False
            cls.build_tests = False
            return  # The remaining options only affect the userspace build

        if "minimal" not in cls.__dict__:
            cls.minimal = cls.add_bool_option("minimal", show_help=False,
                                              help="Don't build all of FreeBSD, just what is needed for running most "
                                                   "CHERI tests/benchmarks")

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        # The bootstrap tools need libarchive which is not always installed on Linux. macOS ships a libarchive.dylib
        # (without headers) so we use that with the contrib/ headers and don't need an additional package.
        if not OSInfo.IS_FREEBSD:
            self.check_required_pkg_config("libarchive", apt="libarchive-dev", zypper="libarchive-devel")

    def setup(self) -> None:
        super().setup()
        self.make_args.env_vars = {"MAKEOBJDIRPREFIX": str(self.build_dir)}
        # TODO? Avoid lots of nested child directories by using MAKEOBJDIR instead of MAKEOBJDIRPREFIX
        # self.make_args.env_vars = {"MAKEOBJDIR": str(self.build_dir)}

        if self.crossbuild:
            # Use the script that I added for building on Linux/MacOS:
            self.make_args.set_command(self.source_dir / "tools/build/make.py",
                                       early_args=["--bootstrap-toolchain"] if self.use_bootstrapped_toolchain else [])

        self.make_args.set(
            DB_FROM_SRC=True,  # don't use the system passwd file
            I_REALLY_MEAN_NO_CLEAN=True,  # Also skip the useless delete-old step
            NO_ROOT=True,  # use this even if current user is root, as without it the METALOG file is not created
            BUILD_WITH_STRICT_TMPPATH=True,  # This can catch lots of depdency errors
        )
        # FreeBSD has renamed NO_CLEAN to WITHOUT_CLEAN
        self.make_args.set_with_options(CLEAN=False)

        if self.minimal:
            self.make_args.set_with_options(MAN=False, KERBEROS=False, SVN=False, SVNLITE=False, MAIL=False, ZFS=False,
                                            SENDMAIL=False, EXAMPLES=False, LOCALES=False, NLS=False, CDDL=False)

        # tests off by default because they take a long time and often seems to break
        # the creation of disk-image (METALOG is invalid)
        self.make_args.set_with_options(TESTS=self.build_tests)

        if self.use_ccache:
            self.make_args.set_with_options(CCACHE_BUILD=True)

        # By default we only want to print the status updates -> use make -s so we have to do less filtering
        # However, jenkins builds default to --verbose and this amount of output is only useful when building
        # with -j1 so also disable it by default for jenkins builds
        if not self.config.verbose or (is_jenkins_build() and self.config.make_jobs != 1):
            self.make_args.add_flags("-s")

        # print detailed information about the failed target (including the command that was executed)
        self.make_args.add_flags("-de")

        for option in self.extra_make_args:
            if not self.crosscompile_target.is_hybrid_or_purecap_cheri() and "CHERI_" in option:
                self.warning("Should not be adding CHERI specific make option", option, "for", self.target,
                             " -- consider setting separate", self.get_config_option_name("extra_make_args"),
                             "in the config file.")
            if "=" in option:
                key, value = option.split("=", 1)
                args = {key: value}
                self.make_args.set(**args)
            else:
                self.make_args.add_flags(option)

    def run_make(self, make_target="", *, options: "Optional[MakeOptions]" = None, parallel=True, **kwargs):
        # make behaves differently with -j1 and not j flags -> remove the j flag if j1 is requested
        if parallel and self.config.make_jobs == 1:
            parallel = False
        if options is None:
            options = self.make_args
        if "METALOG" in options.env_vars:
            assert "DESTDIR" in options.env_vars, "METALOG set, but DESTDIR not set"
            assert options.env_vars["METALOG"].startswith(options.env_vars["DESTDIR"]), "METALOG not below DESTDIR"
        assert options.get_var("METALOG", None) is None, "METALOG should only be set in the environment"
        assert options.get_var("DESTDIR", None) is None, "DESTDIR should only be set in the environment"
        super().run_make(make_target, options=options, cwd=self.source_dir, parallel=parallel, **kwargs)

    @property
    def jflag(self) -> list:
        return [self.config.make_j_flag] if self.config.make_jobs > 1 else []

    def set_lto_binutils(self, ar, ranlib, nm, ld) -> None:
        self.fatal("Building FreeBSD/CheriBSD with LTO is not supported (yet).")


class BuildFreeBSD(BuildFreeBSDBase):
    target: str = "freebsd"
    repository: GitRepository = GitRepository("https://github.com/freebsd/freebsd.git")
    needs_sysroot: bool = False  # We are building the full OS so we don't need a sysroot
    # We still allow building FreeBSD for MIPS64. While the main branch no longer has support, this allows building
    # the stable/13 branch using cheribuild. However, MIPS is no longer included in ALL_SUPPORTED_FREEBSD_TARGETS.
    supported_architectures: "typing.ClassVar[tuple[CrossCompileTarget, ...]]" = (
        *CompilationTargets.ALL_SUPPORTED_FREEBSD_TARGETS,
        CompilationTargets.FREEBSD_MIPS64,
    )

    _default_install_dir_fn: ComputedDefaultValue[Path] = _arch_suffixed_custom_install_dir("freebsd")
    add_custom_make_options: bool = True
    use_llvm_binutils: bool = False

    # The compiler to use for building freebsd (bundled/upstream-llvm/cheri-llvm/custom)
    build_toolchain: FreeBSDToolchainKind = FreeBSDToolchainKind.DEFAULT_COMPILER
    can_build_with_system_clang: bool = True  # Not true for CheriBSD

    # cheribsd-mfs-root-kernel doesn't have a default kernel-config, instead
    # building a set, but kernel-config should still override that.
    has_default_buildkernel_kernel_config: bool = True

    @classmethod
    def get_rootfs_dir(cls, caller, cross_target: "Optional[CrossCompileTarget]" = None) -> Path:
        return cls.get_install_dir(caller, cross_target)

    @classmethod
    def setup_config_options(cls, bootstrap_toolchain=False, use_upstream_llvm: Optional[bool] = None,
                             debug_info_by_default=True, kernel_only_target=False, **kwargs) -> None:
        super().setup_config_options(kernel_only_target=kernel_only_target, **kwargs)
        if cls._xtarget:
            # KERNCONF always depends on the target, so we don't inherit this config option. The only exception is
            # the global --kernel-config option that is provided for convenience and backwards compat.
            cls.kernel_config = cls.add_config_option(
                "kernel-config", metavar="CONFIG", show_help=True, extra_fallback_config_names=["kernel-config"],
                default=ComputedDefaultValue(
                    function=lambda _, p:
                        p.default_kernel_config() if p.has_default_buildkernel_kernel_config else None,
                    as_string="target-dependent, usually GENERIC"),
                use_default_fallback_config_names=False,  #
                help="The kernel configuration to use for `make buildkernel`")

        if cls._xtarget is not None and cls._xtarget.is_hybrid_or_purecap_cheri():
            # When targeting CHERI we have to use CHERI LLVM
            assert not use_upstream_llvm
            assert not bootstrap_toolchain
            cls.build_toolchain = FreeBSDToolchainKind.DEFAULT_COMPILER
            cls.linker_for_world = "lld"
            cls.linker_for_kernel = "lld"
        elif bootstrap_toolchain:
            assert not use_upstream_llvm
            cls.build_toolchain = FreeBSDToolchainKind.BOOTSTRAPPED
            cls._cross_toolchain_root = None
            cls.linker_for_kernel = "should-not-be-used"
            cls.linker_for_world = "should-not-be-used"
        else:
            # Prefer using system clang for FreeBSD builds rather than a self-built snapshot of LLVM since that might
            # have new warnings that break the -Werror build.
            cls.build_toolchain = typing.cast(FreeBSDToolchainKind, cls.add_config_option(
                "toolchain", kind=FreeBSDToolchainKind, default=FreeBSDToolchainKind.DEFAULT_COMPILER,
                enum_choice_strings=[t.value for t in FreeBSDToolchainKind],
                help="The toolchain to use for building FreeBSD. When set to 'custom', the 'toolchain-path' option "
                     "must also be set"))
            cls._cross_toolchain_root = cls.add_optional_path_option(
                "toolchain-path", help="Path to the cross toolchain tools")
            # override in CheriBSD
            cls.linker_for_world = cls.add_config_option("linker-for-world", default="lld", choices=["bfd", "lld"],
                                                         help="The linker to use for world")
            cls.linker_for_kernel = cls.add_config_option("linker-for-kernel", default="lld", choices=["bfd", "lld"],
                                                          help="The linker to use for the kernel")

        cls.with_debug_info = cls.add_bool_option("debug-info", default=debug_info_by_default, show_help=True,
                                                  help="pass make flags for building with debug info")
        cls.with_debug_files = cls.add_bool_option("debug-files", default=True,
                                                   help="Use split DWARF debug files if building with debug info")
        cls.fast_rebuild = cls.add_bool_option(
            "fast", help="Skip some (usually) unnecessary build steps to speed up rebuilds")

        if kernel_only_target:
            cls.build_lib32 = False
            cls.build_drm_kmod = False
            cls.with_manpages = False
            return  # The remaining options only affect the userspace build

        subdir_default = ComputedDefaultValue(function=lambda config, proj: config.freebsd_subdir,
                                              as_string="the value of the global --freebsd-subdir options")

        cls.explicit_subdirs_only = cls.add_list_option(
            "subdir", metavar="SUBDIRS", show_help=True, default=subdir_default,
            help="Only build subdirs SUBDIRS instead of the full tree. Useful for quickly rebuilding individual"
                 " programs/libraries. If more than one dir is passed, they will be processed in order. Note: This"
                 " will break if not all dependencies have been built.")

        cls.keep_old_rootfs = cls.add_bool_option(
            "keep-old-rootfs", help="Don't remove the whole old rootfs directory.  This can speed up installing but"
                                    " may cause strange errors so is off by default.")
        cls.with_manpages = cls.add_bool_option("with-manpages", help="Also install manpages. This is off by default"
                                                                      " since they can just be read from the host.")
        cls.build_drm_kmod = cls.add_bool_option("build-drm-kmod", help="Also build drm-kmod during buildkernel",
                                                 show_help=False)
        if cls._xtarget is None or not cls._xtarget.cpu_architecture.is_32bit():
            cls.build_lib32 = cls.add_bool_option(
                "build-lib32", default=False,
                help="Build the 32-bit compatibility userspace libraries (if supported for the current architecture)")
        else:
            # XXX: this is not correct if we were to support a CHERI-64 userspace
            assert not cls._xtarget.is_hybrid_or_purecap_cheri()
            cls.build_lib32 = False

    def get_default_kernel_platform(self) -> ConfigPlatform:
        if self.crosscompile_target.is_aarch64(include_purecap=True):
            return ConfigPlatform.FVP
        else:
            return ConfigPlatform.QEMU

    def default_kernel_config(self, platform: "Optional[ConfigPlatform]" = None, **filter_kwargs) -> str:
        xtarget = self.crosscompile_target
        # Only handle FreeBSD native configs here
        assert not xtarget.is_hybrid_or_purecap_cheri(), "Unexpected FreeBSD target"
        if platform is None:
            platform = self.get_default_kernel_platform()
        config = CheriBSDConfigTable.get_default(xtarget, platform, KernelABI.NOCHERI, **filter_kwargs)
        return config.kernconf

    def _stdout_filter(self, line: bytes) -> None:
        if line.startswith(b">>> "):  # major status update
            if self._last_stdout_line_can_be_overwritten:
                sys.stdout.buffer.write(_clear_line_sequence)
            sys.stdout.buffer.write(line)
            flush_stdio(sys.stdout)
            self._last_stdout_line_can_be_overwritten = False
        elif line.startswith(b"===> "):  # new subdirectory
            self._line_not_important_stdout_filter(line)
        elif line == b"--------------------------------------------------------------\n":
            return  # ignore separator around status updates
        elif line == b"\n":
            return  # ignore empty lines when filtering
        elif line.endswith(b"' is up to date.\n"):
            return  # ignore these messages caused by (unnecessary?) recursive make invocations|
        elif line.endswith(b"missing (created)\n"):
            return  # ignore these from installworld
        elif line.startswith(b"[Creating objdir") or line.startswith(b"[Creating nested objdir"):
            return  # ignore the WITH_AUTO_OBJ messages
        else:
            self._show_line_stdout_filter(line)

    @property
    def arch_build_flags(self) -> "dict[str, Union[str, bool]]":
        assert isinstance(self.target_info, FreeBSDTargetInfo)
        result = {
            "TARGET": self.target_info.freebsd_target,
            "TARGET_ARCH": self.target_info.freebsd_target_arch,
        }
        if self.crosscompile_target.is_hybrid_or_purecap_cheri():
            if self.crosscompile_target.is_aarch64(include_purecap=True):
                result["TARGET_CPUTYPE"] = "morello"
                # FIXME: still needed?
                result["WITH_CHERI"] = True
            else:
                result["TARGET_CPUTYPE"] = "cheri"
                if self.compiling_for_mips(include_purecap=True):
                    result["CHERI"] = self.config.mips_cheri_bits_str
        return result

    def _setup_make_args(self) -> None:
        # Same as setup() but can be called multiple times.
        if self._setup_make_args_called:
            return
        # Must be called after __init__() to ensure that CHERI LLVM/upstream LLVM have been built
        # before querying the compiler.
        if self.crossbuild:
            assert not OSInfo.IS_FREEBSD
            self.add_cross_build_options()

        # external toolchain options:
        self._setup_cross_toolchain_config()

        if self.with_debug_info and not self.with_debug_files:
            self.make_args.set(DEBUG_FLAGS="-g")

        if not self.with_debug_info or not self.with_debug_files:
            # Don't split the debug info from the binary, just keep it as part
            # of the binary. This means we can just scp the file over to a
            # cheribsd instace, run gdb and get symbols and sources. This also
            # turns off giving DEBUG_FLAGS a separate value so if we don't
            # provide one then debug info will be omitted.
            self.make_args.set_with_options(DEBUG_FILES=False)

        if self.add_custom_make_options:
            self.make_args.set_with_options(PROFILE=False)  # PROFILE is useless and just slows down the build
            # The OFED code is unlikely to be of any use to us and is also full of annoying warnings that flood the
            # build log. Moreover, these warnings indicat that it's very unlikely to work as purecap.
            self.make_args.set_with_options(OFED=False)
            # Don't build manpages by default
            self.make_args.set_with_options(MAN=self.with_manpages)
            # we want to build makefs for the disk image (makefs depends on libnetbsd which will not be
            # bootstrapped on FreeBSD, and the same goes for libsbuf in recent versions since config(8) no longer
            # depends on it)
            # TODO: upstream a patch to bootstrap them by default
            self.make_args.set(LOCAL_XTOOL_DIRS="lib/libnetbsd lib/libsbuf usr.sbin/makefs usr.bin/mkimg")
            # Enable MALLOC_PRODUCTION by default unless --<tgt>/build-type=Debug is passed.
            self.make_args.set_with_options(MALLOC_PRODUCTION=self.build_type.is_release)

        self._setup_make_args_called = True

    def _try_find_compatible_system_clang(self) -> "tuple[Optional[Path], Optional[str], Optional[str]]":
        min_version = (10, 0)
        if OSInfo.IS_MAC:
            # Don't use apple_clang from /usr/bin
            prefix = self.get_homebrew_prefix()
            path = [str(prefix / "opt/llvm/bin"), str(prefix / "bin"), "/usr/bin"]
            compiler_path = shutil.which("clang", path=":".join(path))
        else:
            # Try using the latest installed clang
            compiler_path = latest_system_clang_tool(self.config, "clang", None)
        if not compiler_path:
            # No system clang found, fall back to trying the compiler specified as the host path
            cc_info = self.get_compiler_info(self.host_CC)
            # Use the compiler configured in the cheribuild config if possible
            if cc_info.is_clang and not cc_info.is_apple_clang and cc_info.version >= min_version:
                compiler_path = cc_info.path
            else:
                return (None, "Could not find a system installation of clang.",
                        "Please install a recent upstream clang or use the 'custom' or 'upstream-llvm' toolchain "
                        "option.")
        self.info("Checking if", compiler_path, "can be used to build FreeBSD...")
        cc_info = self.get_compiler_info(compiler_path)
        if cc_info.is_apple_clang:
            return (None, "Cannot build FreeBSD with Apple clang.",
                    "Please install a recent upstream clang (e.g. with homebrew) or use the 'custom' "
                    "or 'upstream-llvm' toolchain option.")
        if cc_info.version < min_version:
            return (None, "Cannot build FreeBSD with Clang older than " + ".".join(map(str, min_version)) +
                    ". Found clang = " + str(compiler_path),
                    "Please install a recent upstream clang (e.g. with homebrew) or use the 'custom' "
                    "or 'upstream-llvm' toolchain option.")
        # Note: FreeBSD installs shell script wrappers for clang, so we can't just use
        # Path(compiler_path).resolve().parent.parent since that will try to use /usr/local/bin/clang. Instead
        # we print the resource dir (<clang-root>/lib/clang/<version>) and go up three levels from there.
        clang_root = cc_info.get_resource_dir().parent.parent.parent
        assert not cc_info.is_apple_clang
        self.info(cc_info.path, " (", cc_info.version_str, ") can be used to build FreeBSD.", sep="")
        return clang_root, None, None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._setup_make_args_called = False
        self.kernel_toolchain_exists: bool = False
        self.cross_toolchain_config = MakeOptions(MakeCommandKind.BsdMake, self)
        if self.has_default_buildkernel_kernel_config:
            assert self.kernel_config is not None
        self.make_args.set(**self.arch_build_flags)
        self.extra_kernels: "list[str]" = []

    def setup(self) -> None:
        super().setup()
        self.destdir = self.install_dir
        self._install_prefix = Path("/")
        assert self.real_install_root_dir == self.destdir

    @cached_property
    def build_toolchain_root_dir(self) -> "Optional[Path]":
        if self.build_toolchain == FreeBSDToolchainKind.BOOTSTRAPPED:
            return self.objdir / "tmp/usr"
        elif self.build_toolchain in (FreeBSDToolchainKind.UPSTREAM_LLVM, FreeBSDToolchainKind.CHERI_LLVM,
                                      FreeBSDToolchainKind.MORELLO_LLVM):
            return BuildLLVMMonoRepoBase.get_install_dir_for_type(self, self.build_toolchain)
        elif self.build_toolchain == FreeBSDToolchainKind.SYSTEM_LLVM:
            system_clang_root, errmsg, fixit = self._try_find_compatible_system_clang()
            if system_clang_root is None:
                self.fatal(errmsg, fixit)
            return system_clang_root
        elif self.build_toolchain == FreeBSDToolchainKind.CUSTOM:
            if self._cross_toolchain_root is None:
                self.fatal("Requested custom toolchain but", self.get_config_option_name("_cross_toolchain_root"),
                           "is not set.")
            return self._cross_toolchain_root
        else:
            assert self.build_toolchain == FreeBSDToolchainKind.DEFAULT_COMPILER
            if self.can_build_with_system_clang:
                # Try to find system clang and if not we fall back to the default self-built clang
                system_clang_root, errmsg, _ = self._try_find_compatible_system_clang()
                if system_clang_root is not None:
                    return system_clang_root
            # Otherwise, the default logic is used, and we select clang based on self.target_info
            return None

    def _setup_cross_toolchain_config(self) -> None:
        if self.use_bootstrapped_toolchain:
            return

        self.cross_toolchain_config.set_with_options(
            # TODO: should we have an option to include a compiler in the target system?
            GCC=False, CLANG=False, LLD=False,  # Take a long time and not needed in the target system
            LLDB=False,  # may be useful but means we need to build LLVM
            # Bootstrap compiler/ linker are not needed:
            GCC_BOOTSTRAP=False, CLANG_BOOTSTRAP=False, LLD_BOOTSTRAP=False,
        )
        if not self.build_lib32:
            # takes a long time and usually not needed.
            self.cross_toolchain_config.set_with_options(LIB32=False)

        if self.config.csetbounds_stats:
            self.cross_toolchain_config.set(CSETBOUNDS_LOGFILE=self.csetbounds_stats_file)
        if self.config.subobject_bounds:
            self.cross_toolchain_config.set(CHERI_SUBOBJECT_BOUNDS=self.config.subobject_bounds)
            self.cross_toolchain_config.set(CHERI_SUBOBJECT_BOUNDS_DEBUG="yes" if self.config.subobject_debug else "no")

        cross_bindir = self.target_info.sdk_root_dir / "bin"
        cross_prefix = str(cross_bindir) + "/"  # needs to end with / for concatenation
        target_flags = self._setup_arch_specific_options()

        # TODO: should I be setting this in the environment instead?
        xccinfo = self.get_compiler_info(self.CC)
        if not xccinfo.is_clang:
            self.ask_for_confirmation("Cross compiler is not clang, are you sure you want to continue?")
        self.cross_toolchain_config.set_env(
            XCC=self.CC, XCXX=self.CXX, XCPP=self.CPP,
            X_COMPILER_TYPE=xccinfo.compiler,  # This is needed otherwise the build assumes it should build with $CC
        )
        if not self.use_llvm_binutils:
            self.cross_toolchain_config.set_with_options(ELFTOOLCHAIN_BOOTSTRAP=True)
        else:
            self.cross_toolchain_config.set_with_options(ELFTOOLCHAIN_BOOTSTRAP=False)
            # Note: the STRIP variable contains the flag to be passed to install for stripping, whereas install reads
            # the XSTRIPBIN environment variable to determine the path to strip.

            # We currently still need elftoolchain strip for installworld
            self.cross_toolchain_config.set_with_options(ELFTOOLCHAIN_BOOTSTRAP=True)

            self.cross_toolchain_config.set(
                XAR=cross_bindir / "llvm-ar",
                XNM=cross_bindir / "llvm-nm",
                XSIZE=cross_bindir / "llvm-size",
                XSTRIPBIN=cross_bindir / "llvm-strip",
                XSTRINGS=cross_bindir / "llvm-strings",
                XOBJCOPY=cross_bindir / "llvm-objcopy",
                XRANLIB=cross_bindir / "llvm-ranlib",
            )
        if xccinfo.is_clang and xccinfo.version < (10, 0):
            # llvm-ranlib didn't support -D flag (see https://bugs.llvm.org/show_bug.cgi?id=41707)
            self.cross_toolchain_config.set(RANLIBFLAGS="")
        # However, we do want to install the host tools
        self.cross_toolchain_config.set_with_options(TOOLCHAIN=True)

        if self.crosscompile_target.is_i386() and xccinfo.is_clang and xccinfo.version < (11, 0):
            # The i686 default was added in commit 02cfa7530d9e7cfd8ea940dab4173afb7938b831 (LLVM 11.0). When
            # building with an older clang we have to explicitly set the flag otherwise we get build failures.
            self.cross_toolchain_config.set(TARGET_CPUTYPE="i686")

        if self.linker_for_world == "bfd":
            # If WITH_LD_IS_LLD is set (e.g. by reading src.conf) the symlink ld -> ld.bfd in $BUILD_DIR/tmp/ won't be
            # created and the build system will then fall back to using /usr/bin/ld which won't work!
            self.cross_toolchain_config.set_with_options(LLD_IS_LD=False)
            self.cross_toolchain_config.set_env(XLD=cross_prefix + "ld.bfd")
        else:
            assert self.linker_for_world == "lld"
            # Don't set XLD when using bfd since it will pick up ld.bfd from the build directory
            self.cross_toolchain_config.set_env(XLD=cross_prefix + "ld.lld")

        if target_flags:
            self.cross_toolchain_config.set_env(XCFLAGS=target_flags)

        if self.linker_for_kernel == "lld" and self.linker_for_world == "lld" and not self.compiling_for_host():
            # When building freebsd x86 we need to build the 'as' binary
            self.cross_toolchain_config.set_with_options(BINUTILS_BOOTSTRAP=False)

    def _setup_arch_specific_options(self) -> str:
        if self.crosscompile_target.is_any_x86() or self.crosscompile_target.is_aarch64(include_purecap=True):
            target_flags = ""
            self.linker_for_kernel = "lld"  # bfd won't work here
            self.linker_for_world = "lld"
        elif self.compiling_for_mips(include_purecap=True):
            target_flags = "-fcolor-diagnostics"
            # TODO: should probably set that inside CheriBSD makefiles instead
            if self.target_info.is_cheribsd():
                target_flags += " -mcpu=beri"
            self.cross_toolchain_config.set_with_options(RESCUE=False,  # Won't compile with CHERI clang yet
                                                         BOOT=False)  # bootloaders won't link with LLD yet
        elif self.compiling_for_riscv(include_purecap=True):
            target_flags = ""
        else:
            self.fatal("Invalid state, should have a cross env")
            sys.exit(1)
        return target_flags

    @property
    def buildworld_args(self) -> MakeOptions:
        self._setup_make_args()  # ensure make args are complete
        result = self.make_args.copy()
        if self.crosscompile_target.is_cheri_hybrid([CPUArchitecture.RISCV64]):
            # CheriBSD installworld currently gets very confused that libcheri CCDL is forced to false and attempts
            # to install the files during installworld.
            result.set_with_options(CDDL=False)
        result.update(self.cross_toolchain_config)
        return result

    def kernel_make_args_for_config(self, kernconfs: "list[str]", extra_make_args) -> MakeOptions:
        self._setup_make_args()  # ensure make args are complete
        kernel_options = self.make_args.copy()
        if self.compiling_for_mips(include_purecap=True):
            # Don't build kernel modules for MIPS
            kernel_options.set(NO_MODULES="yes")
        elif self.crosscompile_target.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            # Disable CTF for now to avoid the following errors:
            # ERROR: cam_periph.c: die 25130: unknown base type encoding 0xffffffffffffffa1
            kernel_options.set_with_options(CTF=False)
            # TODO: Turn this into additional checks in sys/modules/Makefile
            # rather than requiring cheribuild to fix things.
            broken_modules = []
            # TODO: Fix armv8crypto for the purecap kernel; overrides
            # -march=morello+c64 by passing -march=armv8-a+crypto, but should
            # *only* enable crypto, not override the baseline. Otherwise it
            # fails to build (currently hits a frontend assertion because
            # Morello Clang doesn't actually validate the arguments properly).
            broken_modules.append("armv8crypto")
            # Linux (and, similarly, CloudABI) module support is broken, even
            # for hybrid kernels (since various APIs take user pointers).
            broken_modules += ["cloudabi32", "cloudabi64", "linprocfs", "linux64", "linux_common"]
            # efirt is verboten (might work ok in hybrid kernels, but we
            # deliberately turn it off there out of fear of capabilities being
            # clobbered, and in a purecap kernel you need a purecap interface).
            broken_modules.append("efirt")
            # TODO: ena(4) has its own copy of ERR_PTR etc that need porting to
            # using intptr_t like LinuxKPI now does even upstream.
            broken_modules.append("ena")
            # TODO: mlx(4) uses LinuxKPI's scatterlist.h which is too Linux-y,
            # using long in place of intptr_t.
            broken_modules += ["mlx4", "mlx4en", "mlx5", "mlx5en"]
            kernel_options.set(WITHOUT_MODULES=" ".join(broken_modules))
        if not self.use_bootstrapped_toolchain:
            # We can't use LLD for the kernel yet but there is a flag to experiment with it
            kernel_options.update(self.cross_toolchain_config)
            linker = Path(self.target_info.sdk_root_dir, "bin", "ld." + self.linker_for_kernel)
            kernel_options.remove_var("LDFLAGS")
            kernel_options.set(LD=linker, XLD=linker)
            # The kernel build using ${BINUTIL} directly and not X${BINUTIL}:
            for binutil_name in ("AS", "AR", "NM", "OBJCOPY", "RANLIB", "SIZE", "STRINGS", "STRIPBIN"):
                xbinutil = kernel_options.get_var("X" + binutil_name)
                if xbinutil:
                    kernel_options.set(**{binutil_name: xbinutil})
                    kernel_options.remove_var("X" + binutil_name)
        if self.build_drm_kmod:
            drm_kmod = BuildDrmKMod.get_instance(self)
            kernel_options.set(LOCAL_MODULES=drm_kmod.source_dir.name, LOCAL_MODULES_DIR=drm_kmod.source_dir.parent)
        kernel_options.set(KERNCONF=" ".join(kernconfs))
        if self.with_debug_info:
            kernel_options.set(DEBUG="-g")
        if extra_make_args:
            kernel_options.set(**extra_make_args)
        return kernel_options

    def clean(self) -> ThreadJoiner:
        cleaning_kerneldir = False
        builddir = self.build_dir
        if self.config.skip_world:
            kernel_dir = self.kernel_objdir(self.kernel_config)
            print(kernel_dir)
            if kernel_dir and kernel_dir.parent.exists():
                builddir = kernel_dir
                cleaning_kerneldir = True
                if kernel_dir.exists() and self.build_dir.exists():
                    assert not os.path.relpath(str(kernel_dir.resolve()), str(self.build_dir.resolve())).startswith(
                        ".."), builddir
            else:
                self.warning("Do not know the full path to the kernel build directory, will clean the whole tree!")
        if self.crossbuild:
            # avoid rebuilding bmake when crossbuilding:
            return self.async_clean_directory(builddir, keep_root=not cleaning_kerneldir, keep_dirs=["bmake-install"])
        else:
            return self.async_clean_directory(builddir)

    def _list_kernel_configs(self) -> None:
        """Emit a list of valid kernel configurations that can be given as --kernel-config overrides"""
        conf_dir = self.source_dir / "sys" / self.target_info.freebsd_target / "conf"
        configs = conf_dir.glob("*")
        blacklist = ["NOTES", "LINT", "DEFAULTS"]
        self.info("Valid kernel configuration files for --" + self.target + "/kernel-config:")
        for conf in configs:
            if (conf.name in blacklist or conf.name.startswith("std.") or conf.name.endswith(".hints") or
                    conf.name.endswith("~")):
                continue
            self.info(conf.name)

    def _buildkernel(self, kernconfs: "list[str]", mfs_root_image: "Optional[Path]" = None, extra_make_args=None,
                     ignore_skip_kernel=False) -> None:
        # Check that --skip-kernel is respected. However, we ignore it for the cheribsd-mfs-root-kernel targets
        # since those targets only build a kernel.
        assert not self.config.skip_kernel or ignore_skip_kernel, "--skip-kernel set but building kernel"
        kernel_make_args = self.kernel_make_args_for_config(kernconfs, extra_make_args)
        if not self.use_bootstrapped_toolchain and not self.CC.exists():
            self.fatal("Requested build of kernel with external toolchain, but", self.CC,
                       "doesn't exist!")
        if self.debug_kernel:
            if any(x.endswith(("_BENCHMARK", "-NODEBUG")) for x in kernconfs):
                if not self.query_yes_no("Trying to build BENCHMARK kernel without optimization. Continue?"):
                    return
            kernel_make_args.set(COPTFLAGS="-O0 -DBOOTVERBOSE=2")
        if mfs_root_image:
            kernel_make_args.set(MFS_IMAGE=mfs_root_image)
        if not self.kernel_toolchain_exists and not self.fast_rebuild:
            kernel_toolchain_opts = kernel_make_args.copy()
            # The kernel seems to use LDFLAGS and ignore XLDFLAGS. Ensure we don't pass those flags when building host
            # binaries
            kernel_toolchain_opts.remove_var("LDFLAGS")
            kernel_toolchain_opts.remove_var("LD")
            kernel_toolchain_opts.set_env(LDFLAGS="")
            # Don't build a compiler if we are using and external toolchain (only build config, etc)
            if not self.use_bootstrapped_toolchain:
                kernel_toolchain_opts.set_with_options(LLD_BOOTSTRAP=False, CLANG=False, CLANG_BOOTSTRAP=False)
            self.run_make("kernel-toolchain", options=kernel_toolchain_opts)
            self.kernel_toolchain_exists = True
        self.info("Building kernels for configs:", " ".join(kernconfs))
        self.run_make("buildkernel", options=kernel_make_args,
                      compilation_db_name="compile_commands_" + " ".join(kernconfs).replace(" ", "_") + ".json")

    def _installkernel(self, kernconfs: "list[str]", *, install_dir: Path, extra_make_args=None,
                       ignore_skip_kernel=False) -> None:
        # Check that --skip-kernel is respected. However, we ignore it for the cheribsd-mfs-root-kernel targets
        # since those targets only build a kernel.
        assert not self.config.skip_kernel or ignore_skip_kernel, "--skip-kernel set but building kernel"
        # don't use multiple jobs here
        install_kernel_args = self.kernel_make_args_for_config(kernconfs, extra_make_args)
        install_kernel_args.env_vars.update(self.make_install_env)
        # Also install all other kernels that were potentially built
        install_kernel_args.set(NO_INSTALLEXTRAKERNELS="no")
        # also install the debug files
        if self.with_debug_info:
            install_kernel_args.set_with_options(KERNEL_SYMBOLS=True)
            install_kernel_args.set(INSTALL_KERNEL_DOT_FULL=True)
        install_kernel_args.set_env(DESTDIR=install_dir, METALOG=install_dir / "METALOG.kernel")
        self.info("Installing kernels for configs:", " ".join(kernconfs))
        self.delete_file(install_dir / "METALOG.kernel")  # Ensure that METALOG does not contain stale values.
        self.run_make("installkernel", options=install_kernel_args, parallel=False)

    def kernconf_list(self) -> "list[str]":
        assert self.kernel_config is not None
        return [self.kernel_config, *self.extra_kernels]

    def compile(self, mfs_root_image: "Optional[Path]" = None, sysroot_only=False, **kwargs) -> None:
        # The build seems to behave differently when -j1 is passed (it still complains about parallel make failures)
        # so just omit the flag here if the user passes -j1 on the command line
        if not self.use_bootstrapped_toolchain:
            if not self.CC.is_file():
                self.fatal("CC does not exist: ", self.CC)
            if not self.CXX.is_file():
                self.fatal("CXX does not exist: ", self.CXX)
        build_args = self.buildworld_args
        if self.config.verbose:
            self.run_make("showconfig", options=build_args)
        if self.config.freebsd_host_tools_only:
            self.run_make("kernel-toolchain", options=build_args)
            return
        if sysroot_only:
            self.run_make("buildsysroot", options=build_args)
            return  # We are done after building the sysroot

        if not self.config.skip_world:
            if self.fast_rebuild:
                if self.with_clean:
                    self.info("Ignoring --", self.target, "/fast option since --clean was passed", sep="")
                else:
                    build_args.set(WORLDFAST=True)
            self.run_make("buildworld", options=build_args)
            self.kernel_toolchain_exists = True  # includes the necessary tools for kernel-toolchain
        if not self.config.skip_kernel:
            self._buildkernel(kernconfs=self.kernconf_list(), mfs_root_image=mfs_root_image)

    def _remove_schg_flag(self, *paths: "str") -> None:
        if shutil.which("chflags"):
            for i in paths:
                file = self.install_dir / i
                if file.exists():
                    self.run_cmd("chflags", "noschg", str(file))

    def _remove_old_rootfs(self) -> None:
        assert self.with_clean or not self.keep_old_rootfs
        if self.config.skip_world:
            self.makedirs(self.install_dir)
        else:
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # We have to keep the rootfs directory in case it has been NFS mounted (but we can delete subdirs)
            if os.getuid() == 0:
                # if we installed as root remove the schg flag from files before cleaning (otherwise rm will fail)
                self._remove_schg_flag(
                    "lib/libc.so.7", "lib/libcrypt.so.5", "lib/libthr.so.3",
                    "libexec/ld-elf.so.1", "sbin/init", "usr/bin/chpass", "usr/bin/chsh", "usr/bin/ypchpass",
                    "usr/bin/ypchfn", "usr/bin/ypchsh", "usr/bin/login", "usr/bin/opieinfo", "usr/bin/opiepasswd",
                    "usr/bin/passwd", "usr/bin/yppasswd", "usr/bin/su", "usr/bin/crontab", "usr/lib/librt.so.1",
                    "var/empty",
                )
            # We keep 3rd-party programs (anything installed in /usr/local + /opt), but delete everything else prior
            # to installworld to avoid having stale files in the generated disk images
            if self.install_dir.exists():
                dirs_to_delete = [x for x in self.install_dir.iterdir() if x.name not in ("opt", "usr")]
                if (self.install_dir / "usr").exists():
                    dirs_to_delete.extend(x for x in (self.install_dir / "usr").iterdir() if x.name != "local")
                self._delete_directories(*dirs_to_delete)
            else:
                self.makedirs(self.install_dir)

    def find_real_bmake_binary(self) -> Path:
        """return the path the bmake binary used for building. On FreeBSD this will generally be /usr/bin/make,
        but when crossbuilding we will usually use bmake-install/bin/bmake"
        """
        if self.crossbuild:
            make_cmd = self.build_dir / "bmake-install/bin/bmake"
        else:
            make_cmd = Path(shutil.which(self.make_args.command) or self.make_args.command)
        if not make_cmd.exists():
            raise FileNotFoundError(make_cmd)
        # FIXME: ./bmake-install/bin/bmake -v MAKE_VERSION -f /dev/null -- bootstrap if older than the
        # contents of contrib/bmake/VERSION
        return make_cmd

    def _query_make_var(self, args, var) -> Optional[Path]:
        try:
            try:
                bmake_binary = self.find_real_bmake_binary()
            except FileNotFoundError:
                self.verbose_print("Cannot query buildenv path if bmake hasn't been bootstrapped")
                return None
            query_args = args.copy()
            query_args.set_command(bmake_binary)
            bw_flags = [*query_args.all_commandline_args(self.config),
                        "BUILD_WITH_STRICT_TMPPATH=0",
                        "-f", self.source_dir / "Makefile.inc1",
                        "-m", self.source_dir / "share/mk",
                        "showconfig",
                        "-D_NO_INCLUDE_COMPILERMK",  # avoid calling ${CC} --version
                        "-V", var]
            if not self.source_dir.exists():
                assert self.config.pretend, "This should only happen when running in a test environment"
                return None
            # https://github.com/freebsd/freebsd/commit/1edb3ba87657e28b017dffbdc3d0b3a32999d933
            cmd = self.run_cmd([bmake_binary, *bw_flags], env=args.env_vars, cwd=self.source_dir,
                               run_in_pretend_mode=True, capture_output=True, print_verbose_only=True)
            lines = cmd.stdout.strip().split(b"\n")
            last_line = lines[-1].decode("utf-8").strip()
            if last_line.startswith("/") and cmd.returncode == 0:
                self.verbose_print("BUILDENV var", var, "was", last_line)
                return Path(last_line)
            self.warning("Failed to query", var, "-- output was:", lines)
            return None
        except subprocess.CalledProcessError as e:
            self.warning("Could not query make variable", var, "for buildworld root objdir: ", e)
            return None

    @cached_property
    def objdir(self) -> Path:
        result = self._query_make_var(self.buildworld_args, ".OBJDIR")
        if result is None:
            result = Path()
        if self.realpath(result) == self.realpath(self.source_dir):
            self.warning("bmake claims the build dir for", self.target, "is the source dir, assuming",
                         self.build_dir, "instead.")
            return self.build_dir
        if not result or result == Path():
            # just clean the whole directory instead
            self.warning("Could not infer buildworld root objdir for", self.target)
            return self.build_dir
        return result

    def kernel_objdir(self, config) -> Optional[Path]:
        result = self.objdir / "sys"
        if result.exists():
            return Path(result) / config
        self.warning("Could not infer buildkernel objdir")
        return None

    @property
    def installworld_args(self) -> MakeOptions:
        result = self.buildworld_args
        result.env_vars.update(self.make_install_env)
        # Speed up installworld a bit after https://github.com/CTSRD-CHERI/cheribsd/pull/739
        result.set(NO_SAFE_LIBINSTALL=True)
        result.set_env(METALOG=self.install_dir / "METALOG.world")
        return result

    def install(self, kernconfs: "Optional[list[str]]" = None, sysroot_only=False, **kwargs) -> None:
        if self.config.freebsd_host_tools_only:
            self.info("Skipping install step because freebsd-host-tools was set")
            return
        # keeping the old rootfs directory prior to install can sometimes cause the build to fail so delete by default
        if self.with_clean or not self.keep_old_rootfs:
            self._remove_old_rootfs()

        if not self.config.skip_world or sysroot_only:
            install_world_args = self.installworld_args
            # https://github.com/CTSRD-CHERI/cheribsd/issues/220
            # installworld reads compiler metadata which was written by kernel-toolchain which means that
            # it will attempt to install libc++ because compiler for kernel is now clang and not GCC
            # as a workaround force writing the compiler metadata by invoking the _compiler-metadata target
            try:
                self.run_make("_build-metadata", options=install_world_args)
            except subprocess.CalledProcessError:
                try:
                    # support building old versions of cheribsd before _compiler-metadata was renamed to _build-metadata
                    self.run_make("_compiler-metadata", options=install_world_args)
                except subprocess.CalledProcessError:
                    self.warning("Failed to run either target _compiler-metadata or "
                                 "_build_metadata, build system has changed!")
            # By default also create a sysroot when installing world
            installsysroot_args = install_world_args.copy()
            if self.has_installsysroot_target:
                # No need for the files in /usr/share and the METALOG file
                installsysroot_args.set(NO_SHARE=True)
                installsysroot_args.set_env(DESTDIR=self.target_info.get_non_rootfs_sysroot_dir())
            if sysroot_only:
                if not self.has_installsysroot_target:
                    self.fatal("Can't use installsysroot here")
                if is_jenkins_build():
                    # Install to the install dir in jenkins, but the sysroot otherwise
                    installsysroot_args.set_env(DESTDIR=self.install_dir)
                self.run_make("installsysroot", options=installsysroot_args)
                # Don't try to install the kernel if we are only building a sysroot
                return
            else:
                # Ensure that METALOG does not contain stale values:
                self.delete_file(self.install_dir / "METALOG.world")
                self.run_make("installworld", options=install_world_args)
                self.run_make("distribution", options=install_world_args)
                if self.has_installsysroot_target:
                    if is_jenkins_build():
                        installsysroot_args.set_env(DESTDIR=self.target_info.sysroot_dir)
                    installsysroot_args.set_env(METALOG=installsysroot_args.env_vars["DESTDIR"] + "/METALOG")
                    self.run_make("installsysroot", options=installsysroot_args)

                # Enable toor user with a shell of sh for those who dislike root's csh
                def rewrite_passwd(old):
                    new = []
                    for line in old:
                        fields = line.split(':')
                        if len(fields) == 10 and fields[0] == "toor" and fields[1] == "*" and not fields[9]:
                            fields[1] = ""
                            fields[9] = "/bin/sh"
                            line = ':'.join(fields)
                        new.append(line)
                    return new

                pwd_mkdb_cmd = self.objdir / "tmp/legacy/usr/bin/pwd_mkdb"
                self.rewrite_file(self.destdir / "etc/master.passwd", rewrite_passwd)
                self.run_cmd([pwd_mkdb_cmd, "-p", "-d", self.install_dir / "etc",
                              self.install_dir / "etc/master.passwd"])

        assert not sysroot_only, "Should not end up here"
        if self.config.skip_kernel:
            return
        # Run installkernel after installworld since installworld deletes METALOG and therefore the files added by
        # the installkernel step will not be included if we run it first.
        if kernconfs is None:
            kernconfs = self.kernconf_list()
        self._installkernel(kernconfs=kernconfs, install_dir=self.install_dir)

    def add_cross_build_options(self) -> None:
        self.make_args.set_env(CC=self.host_CC, CXX=self.host_CXX, CPP=self.host_CPP,
                               STRIPBIN=shutil.which("strip") or shutil.which("llvm-strip") or "strip")
        if self.use_bootstrapped_toolchain:
            assert "XCC" not in self.make_args.env_vars
            # We have to provide the default X* values so that Makefile.inc1 does not disable MK_CLANG_BOOTSTRAP and
            # doesn't try to cross-compile using the host compilers
            self.make_args.set_env(XCC="cc", XCXX="c++", XCPP="cpp")
        # won't work on a case-insensitive file system and is also really slow (and missing tools on linux)
        self.make_args.set_with_options(MAN=False)
        # links from /usr/bin/mail to /usr/bin/Mail won't work on case-insensitve fs
        self.make_args.set_with_options(MAIL=False)

        if self.crosscompile_target.is_any_x86():
            # seems to be missing some include paths which appears to work on freebsd
            self.make_args.set_with_options(BHYVE=False)

    def libcompat_name(self) -> str:
        if self.crosscompile_target.is_cheri_purecap():
            return "lib64"
        elif self.crosscompile_target.is_cheri_hybrid():
            return "lib64c"
        self.warning("Unknown libcompat for target", self.target)
        self.info("Will use default buildenv target")
        return ""

    def process(self) -> None:
        if not OSInfo.IS_FREEBSD:
            assert self.crossbuild
        _clear_dangerous_make_env_vars()

        if self.config.list_kernels:
            self._list_kernel_configs()
            return

        if self.explicit_subdirs_only:
            # Allow building a single FreeBSD/CheriBSD directory using the BUILDENV_SHELL trick
            args = self.installworld_args
            for subdir in self.explicit_subdirs_only:
                self.build_and_install_subdir(args, subdir)

        elif self.config.buildenv or self.config.libcompat_buildenv:
            args = self.buildworld_args
            args.remove_flag("-s")  # buildenv should not be silent
            if "bash" in os.getenv("SHELL", ""):
                args.set(BUILDENV_SHELL="env -u PROMPT_COMMAND 'PS1=" + self.target + "-buildenv:\\w> ' " +
                                        shutil.which("bash") + " --norc --noprofile")
            else:
                args.set(BUILDENV_SHELL="/bin/sh")
            buildenv_target = "buildenv"
            if self.config.libcompat_buildenv and self.libcompat_name():
                buildenv_target = self.libcompat_name() + "buildenv"
            self.run_cmd([self.make_args.command, *args.all_commandline_args(self.config), buildenv_target],
                         env=args.env_vars, cwd=self.source_dir)
        else:
            super().process()

    def build_and_install_subdir(self, make_args, subdir, skip_build=False, skip_clean=None, skip_install=None,
                                 install_to_internal_sysroot=True, libcompat_only=False, noncheri_only=False) -> None:
        is_lib = subdir.startswith("lib/") or "/lib/" in subdir or subdir.endswith("/lib")
        make_in_subdir = "make -C \"" + subdir + "\" "
        if skip_clean is None:
            skip_clean = not self.with_clean
        if skip_install is None:
            skip_install = self.config.skip_install
        if self.config.pass_dash_k_to_make:
            make_in_subdir += "-k "
        install_to_sysroot_cmd = ""
        # We have to override INSTALL so that the sysroot installations don't end up in METALOG
        # This happens after https://github.com/freebsd/freebsd/commit/5496ab2ac950813edbd55d73c967184e033bea2f
        install_nometalog_cmd = "INSTALL=\"install -N " + str(self.source_dir / "etc") + " -U\" METALOG=/dev/null"
        if is_lib and install_to_internal_sysroot:
            # Due to all the bmake + shell escaping I need 4 dollars here to get it to expand SYSROOT
            sysroot_var = "\"$$$${SYSROOT}\""
            install_to_sysroot_cmd = (
                f"if [ -n {sysroot_var} ]; then"
                f"  {make_in_subdir} install {install_nometalog_cmd} MK_TESTS=no DESTDIR={sysroot_var}; "
                f"fi"
            )
        if skip_install:
            if install_to_sysroot_cmd:
                install_cmd = install_to_sysroot_cmd
            else:
                install_cmd = "echo \"  Skipping make install\""
        else:
            # if we are building a library also install to the sysroot so that other targets afterwards use the
            # updated static lib
            if install_to_sysroot_cmd:
                install_to_sysroot_cmd += " &&  "
            # Execute install and installconfig targets to install both object files and optional configuration
            # (e.g., in usr.sbin/pkg).
            # Note that installconfig doesn't fail if there is no configuration in a subdirectory.
            install_cmd = install_to_sysroot_cmd + make_in_subdir + "install installconfig " + install_nometalog_cmd
        colour_diags = "export CLANG_FORCE_COLOR_DIAGNOSTICS=always; " if self.config.clang_colour_diags else ""
        build_cmd = "{colour_diags} {clean} && {build} && {install} && echo \"  Done.\"".format(
            build=make_in_subdir + "all " + self.commandline_to_str(
                self.jflag) if not skip_build else "echo \"  Skipping make all\"",
            clean=make_in_subdir + "clean" if not skip_clean else "echo \"  Skipping make clean\"",
            install=install_cmd, colour_diags=colour_diags)
        make_args.set(BUILDENV_SHELL="sh -ex -c '" + build_cmd + "' || exit 1")
        # If --libcompat-buildenv was passed skip the MIPS lib
        has_libcompat = self.crosscompile_target.is_hybrid_or_purecap_cheri() and is_lib  # TODO: handle lib32
        if has_libcompat and (self.config.libcompat_buildenv or libcompat_only):
            self.info("Skipping default ABI build of", subdir, "since --libcompat-buildenv was passed.")
        else:
            self.info("Building", subdir, "using buildenv target")
            self.run_cmd([self.make_args.command, *make_args.all_commandline_args(self.config), "buildenv"],
                         env=make_args.env_vars, cwd=self.source_dir)
        # If we are building a library, we want to build both the CHERI and the mips version (unless the
        # user explicitly specified --libcompat-buildenv)
        if has_libcompat and not noncheri_only and self.libcompat_name():
            compat_target = self.libcompat_name() + "buildenv"
            self.info("Building", subdir, "using", compat_target, "target")
            extra_flags = ["MK_TESTS=no"]  # don't build tests since they will overwrite the non-compat ones
            self.run_cmd(
                [self.make_args.command, *make_args.all_commandline_args(self.config), *extra_flags, compat_target],
                env=make_args.env_vars, cwd=self.source_dir)

    def get_kernel_install_path(self, kernconf: "Optional[str]" = None) -> Path:
        """
        Get the installed kernel path for the given kernel configuration. If no kernel config
        is given, the default kernel configuration is selected.
        """
        if kernconf is None or kernconf == self.kernel_config:
            kerndir = "kernel"
        else:
            kerndir = "kernel." + kernconf
        return self.install_dir / "boot" / kerndir / "kernel"

    def get_kern_module_path(self, kernconf: "Optional[str]" = None) -> "Optional[str]":
        """
        Get the path to provide to kern.module_path for the given kernel
        configuration if needed (i.e. the kernel is not the default one).
        """
        if kernconf is None or kernconf == self.kernel_config:
            return None
        return "/boot/kernel." + kernconf

    def get_kern_module_path_arg(self, kernconf: "Optional[str]" = None) -> "Optional[str]":
        """
        Get the tunable env var to set kern.module_path for the given kernel
        configuration if needed (i.e. the kernel is not the default one).
        """
        kerndir = self.get_kern_module_path(kernconf)
        if kerndir:
            return f"kern.module_path={kerndir}"
        return None

    def get_kernel_configs(self, platform: "Optional[ConfigPlatform]") -> "list[str]":
        """
        Get all the kernel configurations to build. This can be used by external targets to
        fetch the set of kernel configurations that have been built and filter them to account
        for run job restrictions (e.g. debug/benchmark or group).
        The filter parameters in kwargs are mapped to CheriBSDConfig fields.
        """
        config = CheriBSDConfigTable.get_entry(self.crosscompile_target, self.kernel_config)
        assert config is not None, "Invalid configuration name"
        return [c.kernconf for c in filter_kernel_configs([config], platform=platform, kernel_abi=None)]

    def prepare_install_dir_for_archiving(self):
        assert is_jenkins_build(), "Should only be called for jenkins builds"
        for config in self.get_kernel_configs(None):
            kernel_elf = self.get_kernel_install_path(config)
            self.install_file(kernel_elf, self.config.output_root / f"kernel.{config}")
            kernel_elf_with_dbg = kernel_elf.with_suffix(".full")
            if kernel_elf_with_dbg.exists():
                self.install_file(kernel_elf_with_dbg, self.config.output_root / f"kernel.{config}.full")


# Build FreeBSD with the default options (build the bundled clang instead of using the SDK one)
# also don't add any of the default -DWITHOUT/DWITH_FOO options
class BuildFreeBSDWithDefaultOptions(BuildFreeBSD):
    target: str = "freebsd-with-default-options"
    repository: ReuseOtherProjectRepository = ReuseOtherProjectRepository(BuildFreeBSD, do_update=True)
    build_dir_suffix: str = "-default-options"
    add_custom_make_options: bool = False
    hide_options_from_help: bool = True  # hide this from --help for now

    def clean(self) -> ThreadJoiner:
        # Bootstrapping LLVM takes forever with FreeBSD makefiles
        if self.use_bootstrapped_toolchain and not self.query_yes_no(
                "You are about to do a clean FreeBSD build (without external toolchain). This will rebuild all of "
                "LLVM and take a long time. Are you sure?", default_result=True):
            return ThreadJoiner(None)
        return super().clean()

    @classmethod
    def setup_config_options(cls, install_directory_help=None, **kwargs) -> None:
        super().setup_config_options(bootstrap_toolchain=True)
        cls.include_llvm = cls.add_bool_option("build-target-llvm",
                                               help="Build LLVM for the target architecture. Note: this adds "
                                                    "significant time to the build")

    def add_cross_build_options(self) -> None:
        # Just try to build as much as possible (but using make.py)
        if not self.include_llvm:
            # Avoid extremely long builds by default
            self.make_args.set_with_options(CLANG=False, LLD=False, LLDB=False)


def jflag_in_subjobs(config: CheriConfig, _) -> int:
    return max(1, config.make_jobs // 2)


def jflag_for_universe(config: CheriConfig, _) -> int:
    return max(1, config.make_jobs // 4)


# Build all targets (to test my changes)
class BuildFreeBSDUniverse(BuildFreeBSDBase):
    # Note: this is a seperate repository checkout, should probably just reuse the same source dir?
    default_directory_basename: str = "freebsd-universe"
    target: str = "freebsd-universe"
    repository: GitRepository = GitRepository("https://github.com/freebsd/freebsd.git")
    default_install_dir: DefaultInstallDir = DefaultInstallDir.DO_NOT_INSTALL
    minimal: bool = False
    hide_options_from_help: bool = True  # hide this from --help for now
    build_toolchain = FreeBSDToolchainKind.BOOTSTRAPPED

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        super().setup_config_options(**kwargs)
        cls.tinderbox = cls.add_bool_option("tinderbox", help="Use `make tinderbox` instead of `make universe`")
        cls.worlds_only = cls.add_bool_option("worlds-only", help="Only build worlds (skip building kernels)")
        cls.kernels_only = cls.add_bool_option("kernels-only", help="Only build kernels (skip building worlds)",
                                               default=ComputedDefaultValue(
                                                   function=lambda conf, proj: conf.skip_world,
                                                   as_string="true if --skip-world is set"))

        cls.jflag_in_subjobs = cls.add_config_option("jflag-in-subjobs",
                                                     help="Number of jobs in each world/kernel build",
                                                     kind=int, default=ComputedDefaultValue(jflag_in_subjobs,
                                                                                            "default -j flag / 2"))

        cls.jflag_for_universe = cls.add_config_option("jflag-for-universe",
                                                       help="Number of parallel world/kernel builds",
                                                       kind=int, default=ComputedDefaultValue(jflag_for_universe,
                                                                                              "default -j flag / 4"))

    def compile(self, **kwargs) -> None:
        # The build seems to behave differently when -j1 is passed (it still complains about parallel make failures)
        # so just omit the flag here if the user passes -j1 on the command line
        build_args = self.make_args.copy()
        if self.config.verbose:
            self.run_make("showconfig", options=build_args)

        if self.worlds_only and self.kernels_only:
            self.fatal("Can't set both worlds-only and kernels-only!")

        build_args.set(__MAKE_CONF="/dev/null")
        # TODO: warn if both worlds-only and kernels-only is set?

        if self.jflag_in_subjobs > 1:
            build_args.set(JFLAG="-j" + str(self.jflag_in_subjobs))
        if self.jflag_for_universe > 1:
            build_args.add_flags("-j" + str(self.jflag_for_universe))

        if self.kernels_only:
            # We need to build kernel-toolchains first (see https://reviews.freebsd.org/D17779)
            self.run_make("kernel-toolchains", options=build_args, parallel=False)

        if self.worlds_only:
            build_args.set(MAKE_JUST_WORLDS=True)
        if self.kernels_only:
            build_args.set(MAKE_JUST_KERNELS=True)
        self.run_make("tinderbox" if self.tinderbox else "universe", options=build_args, parallel=False)

    def install(self, **kwargs) -> None:
        self.info("freebsd-universe is a compile-only target")

    def process(self) -> None:
        if not OSInfo.IS_FREEBSD and not self.crossbuild:
            self.info("Can't build FreeBSD on a non-FreeBSD host (yet)!")
            return
        _clear_dangerous_make_env_vars()
        super().process()


class BuildCHERIBSD(BuildFreeBSD):
    default_directory_basename: str = "cheribsd"
    target: str = "cheribsd"
    can_build_with_system_clang: bool = False  # We need CHERI LLVM for most architectures
    repository: GitRepository = GitRepository("https://github.com/CTSRD-CHERI/cheribsd.git",
                                              old_branches={"master": "main"})
    _default_install_dir_fn: ComputedDefaultValue[Path] = _arch_suffixed_custom_install_dir("rootfs")
    supported_architectures = CompilationTargets.ALL_CHERIBSD_TARGETS_WITH_HYBRID
    is_sdk_target: bool = True
    hide_options_from_help: bool = False  # FreeBSD options are hidden, but this one should be visible
    use_llvm_binutils: bool = True
    has_installsysroot_target: bool = True

    # NB: Full CHERI-MIPS purecap kernel support was never merged
    purecap_kernel_targets: "tuple[CrossCompileTarget, ...]" = (
        CompilationTargets.CHERIBSD_RISCV_HYBRID,
        CompilationTargets.CHERIBSD_RISCV_PURECAP,
        CompilationTargets.CHERIBSD_MORELLO_HYBRID,
        CompilationTargets.CHERIBSD_MORELLO_PURECAP,
    )

    @classmethod
    def setup_config_options(cls, kernel_only_target=False, install_directory_help=None, **kwargs) -> None:
        if install_directory_help is None:
            install_directory_help = "Install directory for CheriBSD root file system"
        super().setup_config_options(install_directory_help=install_directory_help, use_upstream_llvm=False,
                                     kernel_only_target=kernel_only_target)
        fpga_targets = CompilationTargets.ALL_CHERIBSD_RISCV_TARGETS
        cls.build_fpga_kernels = cls.add_bool_option("build-fpga-kernels", show_help=True, _allow_unknown_targets=True,
                                                     only_add_for_targets=fpga_targets,
                                                     help="Also build kernels for the FPGA.")
        cls.build_fett_kernels = cls.add_bool_option("build-fett-kernels", show_help=False, _allow_unknown_targets=True,
                                                     only_add_for_targets=fpga_targets,
                                                     help="Also build kernels for FETT.")
        cls.mfs_root_image = cls.add_optional_path_option(
            "mfs-root-image", help="Path to an MFS root image to be embedded in the kernel for booting")

        cls.default_kernel_abi = cls.add_config_option(
            "default-kernel-abi", show_help=True, _allow_unknown_targets=True,
            only_add_for_targets=cls.purecap_kernel_targets,
            kind=KernelABI, default=KernelABI.HYBRID,
            enum_choices=[KernelABI.HYBRID, KernelABI.PURECAP],
            help="Select default kernel to build")

        # We also want to add this config option to the fake "cheribsd" target (to keep the config file manageable)
        cls.build_alternate_abi_kernels = cls.add_bool_option(
            "build-alternate-abi-kernels", show_help=True,
            _allow_unknown_targets=True,
            only_add_for_targets=cls.purecap_kernel_targets,
            default=True,
            help="Also build kernels with non-default ABI (purecap or hybrid)")

        cls.build_bench_kernels = cls.add_bool_option("build-bench-kernels", show_help=True,
                                                      _allow_unknown_targets=True,
                                                      help="Also build benchmark kernels")

        cls.caprevoke_kernel = cls.add_bool_option(
            "caprevoke-kernel", show_help=True, _allow_unknown_targets=True,
            only_add_for_targets=CompilationTargets.ALL_CHERIBSD_CHERI_TARGETS_WITH_HYBRID,
            help="Build kernel with caprevoke support (experimental)")
        if kernel_only_target:
            return  # The remaining options only affect the userspace build
        cls.sysroot_only = cls.add_bool_option("sysroot-only", show_help=False,
                                               help="Only build a sysroot instead of the full system. This will only "
                                                    "build the libraries and skip all binaries")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.extra_kernels_with_mfs: "list[str]" = []
        configs = self.extra_kernel_configs()
        self.extra_kernels += [c.kernconf for c in configs if not c.mfsroot]
        self.extra_kernels_with_mfs += [c.kernconf for c in configs if c.mfsroot]

    def get_default_kernel_abi(self) -> KernelABI:
        # XXX: Because the config option has _allow_unknown_targets it exists
        # in the base class and thus still inherited by non-purecap-kernel
        # targets
        if self.crosscompile_target in self.purecap_kernel_targets:
            kernel_abi = self.default_kernel_abi
        elif self.crosscompile_target.is_hybrid_or_purecap_cheri():
            kernel_abi = KernelABI.HYBRID
        else:
            kernel_abi = KernelABI.NOCHERI
        return kernel_abi

    def _get_config_variants(self, platforms: "set[ConfigPlatform]", kernel_abis: "list[KernelABI]",
                             combine_flags: list, **filter_kwargs) -> "list[CheriBSDConfig]":
        flag_values = itertools.product([True, False], repeat=len(combine_flags))
        combine_tuples = list(itertools.product(platforms, kernel_abis, flag_values))
        configs = []
        for platform, kernel_abi, flag_tuple in combine_tuples:
            combined_filter = {flag: v for flag, v in zip(combine_flags, flag_tuple)}
            filter_kwargs.update(combined_filter)
            configs += CheriBSDConfigTable.get_configs(self.crosscompile_target, platform=platform,
                                                       kernel_abi=kernel_abi, **filter_kwargs)
        return configs

    def _get_kernel_abis_to_build(self) -> "list[KernelABI]":
        default_kernel_abi = self.get_default_kernel_abi()
        kernel_abis = [default_kernel_abi]
        # XXX: Because the config option has _allow_unknown_targets it exists
        # in the base class and thus still inherited by non-purecap-kernel
        # targets
        if self.crosscompile_target in self.purecap_kernel_targets and self.build_alternate_abi_kernels:
            other_abi = KernelABI.PURECAP if default_kernel_abi != KernelABI.PURECAP else KernelABI.HYBRID
            kernel_abis.append(other_abi)
        return kernel_abis

    def _get_all_kernel_configs(self) -> "list[CheriBSDConfig]":
        kernel_abis = self._get_kernel_abis_to_build()
        platform = self.get_default_kernel_platform()
        combinations = []
        if self.build_bench_kernels:
            combinations.append("benchmark")
        if self.caprevoke_kernel:
            combinations.append("caprevoke")
        if self.build_fett_kernels:
            if not self.compiling_for_riscv(include_purecap=True):
                self.warning("Unsupported architecture for FETT kernels")
            combinations.append("fett")
        configs = self._get_config_variants({platform}, kernel_abis, combinations)
        if self.build_fpga_kernels:
            configs += self._get_config_variants(ConfigPlatform.fpga_platforms(), kernel_abis,
                                                 [*combinations, "mfsroot"])
        return configs

    def default_kernel_config(self, platform: "Optional[ConfigPlatform]" = None, **filter_kwargs) -> str:
        xtarget = self.crosscompile_target
        if not xtarget.is_hybrid_or_purecap_cheri():
            return super().default_kernel_config(platform=platform, **filter_kwargs)
        # Handle CheriBSD hybrid and purecap configs
        if platform is None:
            platform = self.get_default_kernel_platform()
        kernel_abi = filter_kwargs.pop("kernel_abi", self.get_default_kernel_abi())
        if xtarget.is_riscv(include_purecap=True):
            filter_kwargs.setdefault("fett", self.build_fett_kernels)
        filter_kwargs.setdefault("caprevoke", self.caprevoke_kernel)
        config = CheriBSDConfigTable.get_default(xtarget, platform, kernel_abi, **filter_kwargs)
        return config.kernconf

    def extra_kernel_configs(self) -> "list[CheriBSDConfig]":
        # Everything that is not the default kernconf
        option = inspect.getattr_static(self, "kernel_config")
        assert isinstance(option, ConfigOptionBase)
        if self.has_default_buildkernel_kernel_config and not option.is_default_value:
            return []
        configs = self._get_all_kernel_configs()
        default_kernconf = self.default_kernel_config()
        return [c for c in configs if c.kernconf != default_kernconf]

    def get_kernel_configs(self, platform: "Optional[ConfigPlatform]") -> "list[str]":
        default = super().get_kernel_configs(platform)
        extra = filter_kernel_configs(self.extra_kernel_configs(), platform=platform, kernel_abi=None)
        return default + [c.kernconf for c in extra]

    def setup(self) -> None:
        super().setup()
        if self.crosscompile_target.is_hybrid_or_purecap_cheri():
            self.make_args.set_with_options(CHERI=True)
            if self.config.cheri_cap_table_abi:
                self.cross_toolchain_config.set(CHERI_USE_CAP_TABLE=self.config.cheri_cap_table_abi)

        # Support for automatic variable initialization:
        # See https://github.com/CTSRD-CHERI/cheribsd/commit/57e063b20ec04e543b8a4029871c63bf5cbe6897
        # Explicitly disable first (in case the defaults in the source tree change)
        self.make_args.set_with_options(INIT_ALL_ZERO=False, INIT_ALL_PATTERN=False)
        if self.auto_var_init is AutoVarInit.ZERO:
            self.make_args.set_with_options(INIT_ALL_ZERO=True)
        elif self.auto_var_init is AutoVarInit.PATTERN:
            self.make_args.set_with_options(INIT_ALL_PATTERN=True)

    def compile(self, **kwargs) -> None:
        # We could also just pass all values in KERNCONF to build all those kernels. However, if MFS_ROOT is set
        # that will apply to all those kernels and embed the rootfs even if not needed
        super().compile(mfs_root_image=None, sysroot_only=self.sysroot_only, **kwargs)
        if self.sysroot_only:
            # Don't attempt to build extra kernels if we are only building a sysroot
            return
        if not self.config.skip_kernel and self.extra_kernels_with_mfs and self.mfs_root_image:
            self._buildkernel(kernconfs=self.extra_kernels_with_mfs, mfs_root_image=self.mfs_root_image)

    def install(self, **kwargs) -> None:
        # When building we build MFS and
        super().install(kernconfs=self.kernconf_list() + self.extra_kernels_with_mfs,
                        sysroot_only=self.sysroot_only, **kwargs)


class BuildCheriBsdMfsKernel(BuildCHERIBSD):
    target: str = "cheribsd-mfs-root-kernel"
    dependencies: "tuple[str, ...]" = ("disk-image-mfs-root",)
    repository: ReuseOtherProjectRepository = ReuseOtherProjectRepository(source_project=BuildCHERIBSD, do_update=True)
    supported_architectures: "typing.ClassVar[tuple[CrossCompileTarget, ...]]" = (
        CompilationTargets.CHERIBSD_AARCH64,
        *CompilationTargets.ALL_CHERIBSD_MORELLO_TARGETS,
        *CompilationTargets.ALL_CHERIBSD_RISCV_TARGETS,
    )
    default_build_dir: ComputedDefaultValue[Path] = \
        ComputedDefaultValue(function=cheribsd_reuse_build_dir,
                             as_string=lambda cls: BuildCHERIBSD.project_build_dir_help())
    # This exists specifically for this target
    has_default_buildkernel_kernel_config: bool = False
    # We want the CheriBSD config options as well, so that defaults (e.g. build-alternate-abi-kernels) are inherited.
    _config_inherits_from: "type[BuildCHERIBSD]" = BuildCHERIBSD

    @classproperty
    def mfs_root_image_class(self) -> "type[SimpleProject]":
        from ..disk_image import BuildMfsRootCheriBSDDiskImage
        return BuildMfsRootCheriBSDDiskImage

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # No need to rebuild kernel-toolchain, the full toolchain must have
        # been present to build the image in the first place.
        self.kernel_toolchain_exists = True

    @cached_property
    def image(self) -> Path:
        return self.mfs_root_image_class.get_instance(self).disk_image_path

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        super().setup_config_options(kernel_only_target=True, **kwargs)

    def kernconf_list(self) -> "list[str]":
        return self.get_kernel_configs(None)

    def process(self) -> None:
        kernel_configs = self.kernconf_list()
        if len(kernel_configs) == 0:
            self.fatal("No matching kernel configuration to build for", self.crosscompile_target)

        if self.with_clean:
            for kernconf in kernel_configs:
                kernel_dir = self.kernel_objdir(kernconf)
                if kernel_dir:
                    with self.async_clean_directory(kernel_dir):
                        self.verbose_print("Cleaning ", kernel_dir)
        self._build_and_install_kernel_binaries(kernconfs=kernel_configs, image=self.image)

    def _build_and_install_kernel_binaries(self, kernconfs: "list[str]", image: Path):
        # Install to a temporary directory and then copy the kernel to OUTPUT_ROOT
        # Don't bother with modules for the MFS kernels:
        extra_make_args = dict(NO_MODULES="yes")
        self._buildkernel(kernconfs=kernconfs, mfs_root_image=image, extra_make_args=extra_make_args,
                          ignore_skip_kernel=True)
        with tempfile.TemporaryDirectory(prefix="cheribuild-" + self.target + "-") as td:
            self._installkernel(kernconfs=kernconfs, install_dir=Path(td), extra_make_args=extra_make_args,
                                ignore_skip_kernel=True)
            self.run_cmd("find", td)
            for conf in kernconfs:
                kernel_install_path = self.get_kernel_install_path(conf)
                self.delete_file(kernel_install_path)
                if conf == kernconfs[0]:
                    source_path = Path(td, "boot/kernel/kernel")
                else:
                    # All other kernels are installed with a suffixed name:
                    source_path = Path(td, "boot/kernel." + conf, "kernel")
                self.install_file(source_path, kernel_install_path, force=True, print_verbose_only=False)
                dbg_info_kernel = source_path.with_suffix(".full")
                if dbg_info_kernel.exists():
                    fullkernel_install_path = kernel_install_path.with_name(kernel_install_path.name + ".full")
                    self.install_file(dbg_info_kernel, fullkernel_install_path, force=True, print_verbose_only=False)

    def _get_all_kernel_configs(self) -> list:
        kernel_abis = self._get_kernel_abis_to_build()
        platform = self.get_default_kernel_platform()
        combinations = []
        if self.build_bench_kernels:
            combinations.append("benchmark")
        if self.caprevoke_kernel:
            combinations.append("caprevoke")
        configs = self._get_config_variants({platform}, kernel_abis, combinations, mfsroot=True)
        if self.build_fpga_kernels:
            configs += self._get_config_variants(ConfigPlatform.fpga_platforms(), kernel_abis,
                                                 combinations, mfsroot=True)
        return configs

    def default_kernel_config(self, platform: "Optional[ConfigPlatform]" = None, **filter_kwargs) -> str:
        if platform is None:
            platform = self.get_default_kernel_platform()
        kernel_abi = filter_kwargs.pop("kernel_abi", self.get_default_kernel_abi())
        filter_kwargs.setdefault("caprevoke", self.caprevoke_kernel)
        filter_kwargs["mfsroot"] = True
        config = CheriBSDConfigTable.get_default(self.crosscompile_target, platform, kernel_abi, **filter_kwargs)
        return config.kernconf

    def get_kernel_configs(self, platform: "Optional[ConfigPlatform]") -> "list[str]":
        if self.kernel_config is not None:
            return [self.kernel_config]
        configs = self._get_all_kernel_configs()
        return [c.kernconf for c in filter_kernel_configs(configs, platform=platform, kernel_abi=None)]

    def get_kernel_install_path(self, kernconf: "Optional[str]" = None) -> Path:
        """ Get the installed kernel path for an MFS kernel config that has been built. """
        path = self.config.cheribsd_image_root / (
            "kernel" + self.crosscompile_target.build_suffix(self.config, include_os=False) +
            "." + kernconf)
        return path


class BuildCheriBsdMfsImageAndKernels(TargetAliasWithDependencies):
    target: str = "cheribsd-mfs-kernels"
    dependencies: "tuple[str, ...]" = ("disk-image-mfs-root", "cheribsd-mfs-root-kernel")
    direct_dependencies_only: bool = True

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return BuildCheriBsdMfsKernel.supported_architectures


if typing.TYPE_CHECKING:
    ReleaseMixinBase = BuildFreeBSD
else:
    ReleaseMixinBase = object


class BuildFreeBSDReleaseMixin(ReleaseMixinBase):
    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("bsdtar", cheribuild_target="bsdtar", apt="libarchive-tools")

    @property
    def release_objdir(self) -> Optional[Path]:
        result = self.objdir / "release"
        if result.exists() or self.config.pretend:
            return result
        self.warning("Could not infer release objdir")
        return None

    def process(self) -> None:
        if self.with_clean:
            release_objdir = self.release_objdir
            if release_objdir:
                with self.async_clean_directory(release_objdir):
                    self.verbose_print("Cleaning ", release_objdir)

        # release/Makefile needs to install both world and kernel to create
        # images, so start with the arguments for the combination of the two.
        kernconfs = self.get_kernel_configs(None)
        release_args = self.installworld_args.copy()
        release_args.update(self.kernel_make_args_for_config(kernconfs, None).copy())

        # Don't build src.txz into our releases.  We prefer that users check
        # out src using revision control.
        release_args.set(NOSRC=True)

        # Don't build ports.txz into our releases.  Cross-built releases do
        # not yet support this.  And even if they did, we probably prefer that
        # users check out ports using revision control.
        release_args.set(NOPORTS=True)

        # DISTDIR contains OBJTOP already when doing the various recursive
        # makes, adding an extra level isn't needed and breaks things, and we
        # don't want installworld's. Ideally release/Makefile would just set
        # DESTDIR= for the various recursive makes that are sensitive to it and
        # need it to be empty. Note DESTDIR=/ breaks too, we don't want a
        # trailing slash as Makefile.inc1 uses DESTDIR=${DESTDIR}/... for
        # various things and we need DESTDIR to be a strict prefix of the
        # installed file paths so it's stripped in the METALOG. We also want
        # the default METALOG rather than our split name.
        del release_args.env_vars["DESTDIR"]
        del release_args.env_vars["METALOG"]

        # make.py forces changing directory to the source root before calling
        # the real make, so we need to bypass it. -C release won't work as the
        # argument parsing and reassembling will separate them, but -Crelease
        # makes it through unscathed and works.
        release_args.add_flags("-Crelease")

        # Make sure we use in-tree sys.mk, especially src.sys.obj.mk, so the
        # release Makefile ends up with the right OBJTOP that includes
        # TARGET.TARGET_ARCH. Normally the top-level Makefile does this for us,
        # but we bypass that for release/Makefile, and the recursive make calls
        # have .MAKE.LEVEL > 0 so skip some build coordination logic and we end
        # up with OBJTOP set to OBJROOT, missing TARGET.TARGET_ARCH, and it
        # thus fails to find the prior build.
        release_args.add_flags("-m", self.source_dir / "share/mk")

        # TODO: Fix build system to pick these up from PATH rather than override it.
        release_args.set_env(INSTALL="sh " + str(self.source_dir / "tools/install.sh"))
        release_args.set_env(XZ_CMD=str(self.objdir / "tmp/legacy/usr/bin/xz -T 0"))

        # Need bsdtar for @file support
        release_args.set_env(TAR_CMD="bsdtar")

        # Make our various bootstrap and cross tools available
        # TODO: Do this automatically in the build system?
        extra_path_entries = [
            self.objdir / "tmp/legacy/usr/sbin",
            self.objdir / "tmp/legacy/usr/bin",
            self.objdir / "tmp/legacy/bin",
            self.objdir / "tmp/obj-tools/usr.sbin/makefs",
        ]
        release_args.set_env(PATH=":".join(map(str, extra_path_entries)) + ":" +
                                  release_args.env_vars.get("PATH", os.getenv("PATH")))

        # DESTDIR for install target is where to install the media, as you'd
        # expect, unlike the release target where it leaks into installworld
        # etc recursive makes. Otherwise everything is the same, though many
        # options are likely unused.
        install_args = release_args.copy()
        install_args.set_env(DESTDIR=self.install_dir)

        self.run_make("release", options=release_args, parallel=False)
        self.run_make("install", options=install_args, parallel=False)


class BuildFreeBSDRelease(BuildFreeBSDReleaseMixin, BuildFreeBSD):
    target: str = "freebsd-release"
    dependencies: "tuple[str, ...]" = ("freebsd",)
    repository: ReuseOtherProjectRepository = ReuseOtherProjectRepository(source_project=BuildFreeBSD)
    _always_add_suffixed_targets: bool = True
    default_build_dir: ComputedDefaultValue[Path] = \
        ComputedDefaultValue(function=freebsd_reuse_build_dir,
                             as_string=lambda cls: BuildFreeBSD.project_build_dir_help())
    _default_install_dir_fn: ComputedDefaultValue[Path] = _arch_suffixed_custom_install_dir("freebsd-release")
    # We want the FreeBSD config options as well so the release installworld,
    # distributeworld etc. calls match what was built.
    _config_inherits_from: "type[BuildFreeBSD]" = BuildFreeBSD


class BuildCheriBSDRelease(BuildFreeBSDReleaseMixin, BuildCHERIBSD):
    target: str = "cheribsd-release"
    dependencies: "tuple[str, ...]" = ("cheribsd",)
    repository: ReuseOtherProjectRepository = ReuseOtherProjectRepository(source_project=BuildCHERIBSD)
    _always_add_suffixed_targets: bool = True
    default_build_dir: ComputedDefaultValue[Path] = \
        ComputedDefaultValue(function=cheribsd_reuse_build_dir,
                             as_string=lambda cls: BuildCHERIBSD.project_build_dir_help())
    _default_install_dir_fn: ComputedDefaultValue[Path] = _arch_suffixed_custom_install_dir("cheribsd-release")
    # We want the CheriBSD config options as well so the release installworld,
    # distributeworld etc. calls match what was built.
    _config_inherits_from: "type[BuildCHERIBSD]" = BuildCHERIBSD


class BuildCheriBsdSysrootArchive(SimpleProject):
    target: str = "cheribsd-sysroot"
    is_sdk_target: bool = True
    rootfs_source_class: "type[BuildCHERIBSD]" = BuildCHERIBSD
    copy_remote_sysroot: "ClassVar[bool]"
    remote_path: "ClassVar[Optional[str]]"
    install_dir_override: "ClassVar[Optional[Path]]"

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return self.rootfs_source_class.supported_architectures

    @classmethod
    def dependencies(cls, _: CheriConfig) -> "tuple[str, ...]":
        return (cls.rootfs_source_class.target,)

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # GNU tar doesn't accept --include (and doesn't handle METALOG). bsdtar appears to be available
        # on FreeBSD and macOS by default. On Linux it is not always installed by default.
        self.bsdtar_cmd = "bsdtar"
        self.install_dir = self.target_info.sdk_root_dir

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("bsdtar", cheribuild_target="bsdtar", apt="libarchive-tools")
        if not OSInfo.IS_FREEBSD and not self.remote_path and not self.rootfs_source_class.get_instance(
                self).crossbuild:
            config_option = "'--" + self.get_config_option_name("remote_path") + "'"
            self.fatal("Path to the remote SDK is not set, option", config_option,
                       "must be set to a path that scp understands (e.g. vica:~foo/cheri/output/sdk)")
            if not self.config.pretend:
                sys.exit("Cannot continue...")

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        super().setup_config_options(**kwargs)
        cls.copy_remote_sysroot = cls.add_bool_option("copy-remote-sysroot",
                                                      help="Copy sysroot from remote server instead of from local "
                                                           "machine")
        cls.remote_path = cls.add_config_option("remote-sdk-path", show_help=True, metavar="PATH",
                                                help="The path to the CHERI SDK on the remote FreeBSD machine (e.g. "
                                                     "vica:~foo/cheri/output/sdk)")
        cls.install_dir_override = cls.add_optional_path_option(
            "install-directory", help="Override for the sysroot install directory")

    @property
    def cross_sysroot_path(self) -> Path:
        if self.install_dir_override is not None:
            # Work around https://github.com/google/pytype/issues/1344 false positive
            return typing.cast(Path, self.install_dir_override)
        return super().cross_sysroot_path

    def copy_sysroot_from_remote_machine(self) -> None:
        self.info("Copying sysroot from remote system.")
        if not self.remote_path:
            self.fatal(
                "Missing remote SDK path: Please set --cheribsd-sysroot/remote-sdk-path (or --freebsd/crossbuild)")
            if self.config.pretend:
                self.remote_path = "someuser@somehose:this/path/does/not/exist"
        # noinspection PyAttributeOutsideInit
        self.remote_path = os.path.expandvars(self.remote_path)
        remote_sysroot_dir = self.remote_path + "/" + self.cross_sysroot_path.name
        self.info("Will copy the sysroot files from ", remote_sysroot_dir, sep="")
        if not self.query_yes_no("Continue?"):
            return

        # now copy the files
        self.copy_remote_file(remote_sysroot_dir + "/", self.cross_sysroot_path.parent)
        # TODO: could also extract the remote archive?
        # with self.async_clean_directory(self.cross_sysroot_path / "usr"):
        #    extract_sysroot_archive()

    @property
    def sysroot_archive(self) -> Path:
        return self.cross_sysroot_path.parent / ("sysroot" + self.build_configuration_suffix() + ".tar.xz")

    def create_sysroot(self) -> None:
        # we need to add include files and libraries to the sysroot directory
        self.makedirs(self.cross_sysroot_path / "usr")
        # use tar+untar to copy all necessary files listed in metalog to the sysroot dir
        # Since we are using the metalog argument we need to use BSD tar and not GNU tar!
        bsdtar_path = shutil.which(str(self.bsdtar_cmd))
        if not bsdtar_path:
            bsdtar_path = str(self.bsdtar_cmd)
        tar_cmd = [bsdtar_path, "cf", "-", "--include=./lib/", "--include=./usr/include/",
                   "--include=./usr/lib/", "--include=./usr/libdata/",
                   "--include=./usr/lib32", "--include=./usr/lib64",
                   "--include=./usr/lib64c", "--include=./usr/lib64cb",
                   # only pack those files that are mentioned in METALOG
                   "@METALOG.world"]
        rootfs_target = self.rootfs_source_class.get_instance(self)
        rootfs_dir = rootfs_target.real_install_root_dir
        if not (rootfs_dir / "lib/libc.so.7").is_file():
            self.fatal("Sysroot source directory", rootfs_dir, "does not contain libc.so.7",
                       fixit_hint="Run `cheribuild.py " + rootfs_target.target + "` first")
        print_command(tar_cmd, cwd=rootfs_dir)
        if not self.config.pretend:
            tar_cwd = str(rootfs_dir)
            with subprocess.Popen(tar_cmd, stdout=subprocess.PIPE, cwd=tar_cwd) as tar:
                self.run_cmd(["tar", "xf", "-"], stdin=tar.stdout, cwd=self.cross_sysroot_path)
        if not (self.cross_sysroot_path / "lib/libc.so.7").is_file():
            self.fatal(self.cross_sysroot_path, "is missing the libc library, install seems to have failed!")

        # create an archive to make it easier to copy the sysroot to another machine
        self.delete_file(self.sysroot_archive, print_verbose_only=True)
        self.run_cmd("tar", "-caf", self.sysroot_archive, self.cross_sysroot_path.name,
                     cwd=self.cross_sysroot_path.parent)
        self.info("Successfully populated sysroot")

    def process(self) -> None:
        if self.config.skip_world:
            self.info("Not building sysroot because --skip-world was passed")
            return

        if self.copy_remote_sysroot:
            self.copy_sysroot_from_remote_machine()
        else:
            self.create_sysroot()
        if (self.cross_sysroot_path / "usr/lib64c/").is_dir():
            # clang++ expects libgcc_eh to exist:
            libgcc_eh = self.cross_sysroot_path / "usr/lib64c/libgcc_eh.a"
            if not libgcc_eh.is_file():
                self.warning("CHERI libgcc_eh missing! You should probably update CheriBSD")
                self.run_cmd("ar", "rc", libgcc_eh)


class BuildDrmKMod(CrossCompileProject):
    target: str = "drm-kmod"
    repository: GitRepository = GitRepository("https://github.com/freebsd/drm-kmod",
                                              default_branch="master", force_branch=True)
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS
    build_in_source_dir: bool = False
    use_buildenv: bool = False  # doesn't quite work yet (MAKEOBJDIRPREFIX isn't set)
    freebsd_project: BuildFreeBSD
    kernel_make_args: MakeOptions

    def setup(self) -> None:
        super().setup()
        self.freebsd_project = self.target_info.get_rootfs_project(t=BuildFreeBSD, caller=self)
        if self.use_buildenv:
            extra_make_args = dict(SYSDIR=self.freebsd_project.source_dir / "sys")
        else:
            extra_make_args = dict(LOCAL_MODULES=self.source_dir.name,
                                   LOCAL_MODULES_DIR=self.source_dir.parent,
                                   MODULES_OVERRIDE="linuxkpi")
        self.kernel_make_args = self.freebsd_project.kernel_make_args_for_config(self.freebsd_project.kernel_config,
                                                                                 extra_make_args)
        assert self.kernel_make_args.kind == MakeCommandKind.BsdMake

    def clean(self, **kwargs) -> None:
        # TODO: use buildenv and only build the kernel modules...
        if self.use_buildenv:
            self.info("Cleaning drm-kmod modules for configs:", self.freebsd_project.kernel_config)
            self.freebsd_project.build_and_install_subdir(self.kernel_make_args, str(self.source_dir),
                                                          skip_build=True, skip_clean=False, skip_install=True)
        else:
            self.info("Clean not supported yet")

    def compile(self, **kwargs) -> None:
        # TODO: use buildenv and only build the kernel modules...
        self.info("Building drm-kmod modules for configs:", self.freebsd_project.kernel_config)
        if self.use_buildenv:
            self.freebsd_project.build_and_install_subdir(self.kernel_make_args, str(self.source_dir),
                                                          skip_build=False, skip_clean=True, skip_install=True)
        else:
            self.run_make("buildkernel", options=self.kernel_make_args,
                          cwd=self.freebsd_project.source_dir, parallel=True)

    def install(self, **kwargs) -> None:
        # TODO: use buildenv and only install the kernel modules...
        self.info("Installing drm-kmod modules for configs:", self.freebsd_project.kernel_config)
        make_args = self.kernel_make_args.copy()
        # FIXME: it appears that installkernel removes all .ko files, so we can no longer create a disk image
        # if we install with MODULES_OVERRIDE.
        make_args.remove_var("MODULES_OVERRIDE")
        make_args.set_env(METALOG=self.real_install_root_dir / "METALOG.drm-kmod")
        if self.use_buildenv:
            self.freebsd_project.build_and_install_subdir(make_args, str(self.source_dir),
                                                          skip_build=True, skip_clean=True, skip_install=False)
        else:
            self.run_make_install(target="installkernel", options=make_args, cwd=self.freebsd_project.source_dir,
                                  parallel=False)
