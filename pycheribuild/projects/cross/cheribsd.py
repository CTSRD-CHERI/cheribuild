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
import os
import shutil
import subprocess
import sys
import tempfile

from ..llvm import BuildUpstreamLLVM
from ..project import *
from ...config.chericonfig import CrossCompileTarget, MipsFloatAbi
from ...config.loader import ComputedDefaultValue
from ...utils import *


def default_kernel_config(config: CheriConfig, project: SimpleProject) -> str:
    assert isinstance(project, BuildFreeBSD)
    xtarget = project.crosscompile_target
    if xtarget.is_x86_64() or xtarget.is_i386():
        return "GENERIC"
    elif xtarget.is_cheri_purecap([CPUArchitecture.MIPS64]):
        # make sure we use a kernel with 128 bit CPU features selected
        # or a purecap kernel is selected
        assert isinstance(project, BuildCHERIBSD)
        kernconf_name = "CHERI{bits}{pure}_MALTA64"
        cheri_bits = "128" if config.cheriBits == 128 else ""
        cheri_pure = "_PURECAP" if project.purecapKernel else ""
        return kernconf_name.format(bits=cheri_bits, pure=cheri_pure)
    elif xtarget.is_mips(include_purecap=False):
        return "MALTA64"
    elif xtarget.is_riscv():
        return "GENERIC"  # TODO: what is the correct config
    elif xtarget.is_aarch64():
        return "GENERIC-UP"
    else:
        assert False, "should be unreachable"


def freebsd_install_dir(config: CheriConfig, project: SimpleProject):
    assert isinstance(project, BuildFreeBSD)
    target = project.get_crosscompile_target(config)
    if target.is_mips(include_purecap=True):
        assert not target.is_cheri_purecap(), "Should not reach this code!"
        if config.mips_float_abi == MipsFloatAbi.HARD:
            return config.outputRoot / "freebsd-mipshf"
        return config.outputRoot / "freebsd-mips"
    elif target.is_x86_64():
        return config.outputRoot / "freebsd-x86"
    elif target.is_riscv():
        return config.outputRoot / "freebsd-riscv"
    elif target.is_i386():
        return config.outputRoot / "freebsd-i386"
    else:
        assert False, "should not be reached"


# noinspection PyProtectedMember
def cheribsd_install_dir(config: CheriConfig, project: "BuildCHERIBSD"):
    assert isinstance(project, BuildCHERIBSD)
    if project.compiling_for_cheri():
        return config.outputRoot / ("rootfs" + config.cheri_bits_and_abi_str)
    elif project.compiling_for_mips(include_purecap=False):
        if config.mips_float_abi == MipsFloatAbi.HARD:
            return config.outputRoot / "rootfs-mipshf"
        return config.outputRoot / "rootfs-mips"
    elif project.compiling_for_riscv():
        return config.outputRoot / "rootfs-riscv"
    else:
        assert project.crosscompile_target.is_x86_64()
        return config.outputRoot / "rootfs-x86"


def cheribsd_purecap_install_dir(config: CheriConfig, project: SimpleProject):
    assert project.compiling_for_cheri()
    assert isinstance(project, BuildCHERIBSD)
    return config.outputRoot / ("rootfs-purecap" + config.cheri_bits_and_abi_str)


def cheribsd_minimal_install_dir(config: CheriConfig, project: SimpleProject):
    assert isinstance(project, BuildCHERIBSD)
    if project.compiling_for_cheri():
        return config.outputRoot / ("rootfs-minimal" + config.cheri_bits_and_abi_str)
    elif project.compiling_for_mips(include_purecap=False):
        if config.mips_float_abi == MipsFloatAbi.HARD:
            return config.outputRoot / "rootfs-minimal-mipshf"
        return config.outputRoot / "rootfs-minimal-mips"
    elif project.compiling_for_riscv():
        return config.outputRoot / "rootfs-minimal-riscv"
    else:
        assert project.crosscompile_target.is_x86_64()
        return config.outputRoot / "rootfs-minimal-x86"


def default_cross_toolchain_path(config: CheriConfig, proj: SimpleProject):
    assert isinstance(proj, BuildFreeBSD)
    if proj.build_with_upstream_llvm:
        return BuildUpstreamLLVM.getInstallDir(proj)
    return proj.target_info.sdk_root_dir


class BuildFreeBSDBase(Project):
    doNotAddToTargets = True  # base class only
    repository = GitRepository("https://github.com/freebsd/freebsd.git")
    make_kind = MakeCommandKind.BsdMake
    crossbuild = None
    skipBuildworld = False
    use_external_toolchain = False
    is_large_source_repository = True

    defaultExtraMakeOptions = [
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

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.makeOptions = cls.add_config_option("build-options", default=cls.defaultExtraMakeOptions, kind=list,
                                              metavar="OPTIONS",
                                              help="Additional make options to be passed to make when building "
                                                   "FreeBSD/CheriBSD. See `man src.conf` for more info.",
                                              show_help=True)

        if "minimal" not in cls.__dict__:
            cls.minimal = cls.add_bool_option("minimal", show_help=True,
                                            help="Don't build all of FreeBSD, just what is needed for running most "
                                                 "CHERI tests/benchmarks")
        if "build_tests" not in cls.__dict__:
            cls.build_tests = cls.add_bool_option("build-tests", help="Build the tests too (-DWITH_TESTS)", show_help=True)

        cls.debug_kernel = cls.add_bool_option("debug-kernel", help="Build the kernel with -O0 and verbose boot output",
                                             show_help=False)
        if IS_FREEBSD:
            cls.crossbuild = False
        elif is_jenkins_build():
            cls.crossbuild = True
        else:
            cross = inspect.getattr_static(cls, "crossbuild")
            if cross is not True:
                cls.crossbuild = cls.add_bool_option("crossbuild", help="Try to compile FreeBSD on non-FreeBSD machines")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if self.crossbuild:
            # Use the script that I added for building on Linux/MacOS:
            self.make_args.set_command(self.sourceDir / "tools/build/make.py")

        # if not IS_FREEBSD:
        #     self._addRequiredSystemHeader("archive.h", apt="libarchive-dev", homebrew="libarchive")

        self.make_args.env_vars = {"MAKEOBJDIRPREFIX": str(self.buildDir)}
        # TODO: once we have merged the latest upstream changes use MAKEOBJDIR instead to get a more sane hierarchy
        # self.common_options.env_vars = {"MAKEOBJDIR": str(self.buildDir)}
        self.make_args.set(
            DB_FROM_SRC=True,  # don't use the system passwd file
            # NO_WERROR=True,  # make sure we don't fail if clang introduces a new warning
            NO_CLEAN=True,  # don't clean, we have the --clean flag for that
            I_REALLY_MEAN_NO_CLEAN=True,  # Also skip the useless delete-old step
            NO_ROOT=True,  # use this even if current user is root, as without it the METALOG file is not created
            BUILD_WITH_STRICT_TMPPATH=True,  # This can catch lots of depdency errors
            )

        if self.minimal:
            self.make_args.set_with_options(MAN=False, KERBEROS=False, SVN=False, SVNLITE=False, MAIL=False, ZFS=False,
                                            SENDMAIL=False, EXAMPLES=False, LOCALES=False, NLS=False, CDDL=False)

        # tests off by default because they take a long time and often seems to break
        # the creation of disk-image (METALOG is invalid)
        self.make_args.set_with_options(TESTS=self.build_tests)

        if not self.config.verbose and not self.config.quiet:
            # By default we only want to print the status updates -> use make -s so we have to do less filtering
            self.make_args.add_flags("-s")

        # print detailed information about the failed target (including the command that was executed)
        self.make_args.add_flags("-de")

    def runMake(self, make_target="", *, options: MakeOptions = None, parallel=True, **kwargs):
        # make behaves differently with -j1 and not j flags -> remove the j flag if j1 is requested
        if parallel and self.config.makeJobs == 1:
            parallel = False
        super().runMake(make_target, options=options, cwd=self.sourceDir, parallel=parallel, **kwargs)

    @property
    def jflag(self) -> list:
        return [self.config.makeJFlag] if self.config.makeJobs > 1 else []

    # Return the path the a potetial sysroot created from installing this project
    # Currently we only create sysroots for CheriBSD but we might change that in the future
    # noinspection PyMethodMayBeStatic
    def get_corresponding_sysroot(self) -> "typing.Optional[Path]":
        return None


class BuildFreeBSD(BuildFreeBSDBase):
    target = "freebsd"
    repository = GitRepository("https://github.com/freebsd/freebsd.git")
    crossbuild = False
    needs_sysroot = False  # We are building the full OS so we don't need a sysroot
    # Only CheriBSD can target CHERI, upstream FreeBSD won't work
    # TODO: test more architectures (e.g. RISCV)
    supported_architectures = [CompilationTargets.FREEBSD_X86_64, CompilationTargets.FREEBSD_MIPS]

    defaultInstallDir = ComputedDefaultValue(function=freebsd_install_dir,
                                             as_string="$INSTALL_ROOT/freebsd-{mips/x86}")
    hide_options_from_help = True  # hide this for now (only show cheribsd)
    add_custom_make_options = True
    use_llvm_binutils = False

    @classmethod
    def rootfsDir(cls, caller, config=None, cross_target: CrossCompileTarget = CompilationTargets.NONE):
        return cls.getInstallDir(caller, config, cross_target)

    @classmethod
    def get_installed_kernel_path(cls, caller, config: CheriConfig = None,
                                  cross_target: CrossCompileTarget = CompilationTargets.NONE):
        return cls.rootfsDir(caller, config, cross_target) / "boot/kernel/kernel"

    @classmethod
    def setup_config_options(cls, buildKernelWithClang: bool = True, bootstrap_toolchain=False,
                           debug_info_by_default=True, **kwargs):
        super().setup_config_options(add_common_cross_options=False, **kwargs)
        if "subdirOverride" not in cls.__dict__:
            cls.subdirOverride = cls.add_config_option("subdir-with-deps", kind=str, metavar="DIR", show_help=False,
                                                     help="Only build subdir DIR instead of the full tree. This uses "
                                                          "the SUBDIR_OVERRIDE mechanism so "
                                                          "will build much more than just that directory")

        subdir_default = ComputedDefaultValue(function=lambda config, proj: config.freebsd_subdir,
                                              as_string="the value of the global --freebsd-subdir options")

        cls.explicit_subdirs_only = cls.add_config_option("subdir", kind=list, metavar="SUBDIRS", show_help=True,
                                                        default=subdir_default,
                                                        help="Only build subdirs SUBDIRS instead of the full tree. "
                                                             "Useful for quickly rebuilding an individual"
                                                             " programs/libraries. If more than one dir is passed "
                                                             "they will be processed in order."
                                                             " Note: This will break if not all dependencies have "
                                                             "been built.")

        cls.keepOldRootfs = cls.add_bool_option("keep-old-rootfs",
                                              help="Don't remove the whole old rootfs directory.  This can speed up "
                                                   "installing but may cause strange"
                                                   " errors so is off by default.")

        cls.kernelConfig = cls.add_config_option(
            "kernel-config", metavar="CONFIG", show_help=True, fallback_config_name="kernel-config",
            default=ComputedDefaultValue(function=default_kernel_config, as_string="target-dependent default"),
            help="The kernel configuration to use for `make buildkernel` (default: CHERI_MALTA64 or CHERI128_MALTA64"
                 " depending on --cheri-bits)")  # type: str

        if bootstrap_toolchain:
            cls.use_external_toolchain = False
            cls.build_with_upstream_llvm = False
            cls.crossToolchainRoot = None
            cls.useExternalToolchainForKernel = False
            cls.useExternalToolchainForWorld = False
            cls.linker_for_kernel = "should-not-be-used"
            cls.linker_for_world = "should-not-be-used"
        else:
            cls.use_external_toolchain = True
            cls.build_with_upstream_llvm = cls.add_bool_option("compile-with-cheribuild-upstream-llvm", show_help=True,
                                                             help="Compile with the Clang version built by the "
                                                                  "`cheribuild.py upstream-llvm` target")
            defaultExternalToolchain = ComputedDefaultValue(function=default_cross_toolchain_path,
                                                            as_string="$CHERI_SDK_DIR")
            cls.crossToolchainRoot = cls.add_path_option("cross-toolchain",
                                                       help="Path to the mips64-unknown-freebsd-* tools",
                                                       default=defaultExternalToolchain)
            # override in CheriBSD
            cls.useExternalToolchainForKernel = cls.add_bool_option("use-external-toolchain-for-kernel", show_help=True,
                                                                  help="build the kernel with the external toolchain",
                                                                  default=buildKernelWithClang)
            cls.useExternalToolchainForWorld = cls.add_bool_option("use-external-toolchain-for-world", show_help=True,
                                                                 help="build world with the external toolchain",
                                                                 default=True)
            cls.linker_for_world = cls.add_config_option("linker-for-world", default="lld", choices=["bfd", "lld"],
                                                       help="The linker to use for world")
            cls.linker_for_kernel = cls.add_config_option("linker-for-kernel", default="lld", choices=["bfd", "lld"],
                                                        help="The linker to use for the kernel")

        cls.addDebugInfoFlag = cls.add_bool_option("debug-info", default=debug_info_by_default, show_help=True,
                                                 help="pass make flags for building with debug info")
        cls.auto_obj = cls.add_bool_option("auto-obj", help="Use -DWITH_AUTO_OBJ (experimental)", show_help=True,
                                         default=True)
        cls.with_manpages = cls.add_bool_option("with-manpages", help="Also install manpages. This is off by default"
                                                                    " since they can just be read from the host.")
        cls.fastRebuild = cls.add_bool_option("fast",
                                            help="Skip some (usually) unnecessary build steps to speed up rebuilds")

    def _stdout_filter(self, line: bytes):
        if line.startswith(b">>> "):  # major status update
            if self._lastStdoutLineCanBeOverwritten:
                sys.stdout.buffer.write(Project._clearLineSequence)
            sys.stdout.buffer.write(line)
            flushStdio(sys.stdout)
            self._lastStdoutLineCanBeOverwritten = False
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
    def arch_build_flags(self):
        if self.compiling_for_mips(include_purecap=True):
            # The following is broken: (https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            # "CPUTYPE=mips64",  # mipsfpu for hardware float
            return {"TARGET": "mips", "TARGET_ARCH": self.config.mips_float_abi.freebsd_target_arch()}
        elif self.crosscompile_target.is_x86_64():
            return {"TARGET": "amd64", "TARGET_ARCH": "amd64"}
        elif self.crosscompile_target.is_riscv():
            return {"TARGET": "riscv", "TARGET_ARCH": "riscv64"}
        elif self.crosscompile_target.is_i386():
            return {"TARGET": "i386", "TARGET_ARCH": "i386"}
        elif self.crosscompile_target.is_aarch64():
            return {"TARGET": "arm64", "TARGET_ARCH": "aarch64"}
        else:
            assert False, "This should not be reached!"

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        assert self.kernelConfig is not None
        self.cross_toolchain_config = MakeOptions(MakeCommandKind.BsdMake, self)
        self.make_args.set(**self.arch_build_flags)

        if self.crossbuild:
            self.addCrossBuildOptions()
            if self.use_external_toolchain:
                self.useExternalToolchainForWorld = True
                self.useExternalToolchainForKernel = True

        # external toolchain options:
        self.externalToolchainCompiler = None
        self._setup_cross_toolchain_config()

        if self.compiling_for_host() and not self.build_with_upstream_llvm:
            self.warning("DISABLING openmp to work around clang crash")
            self.make_args.set_with_options(OPENMP=False)  # causes clang crash

        if self.addDebugInfoFlag:
            self.make_args.set(DEBUG_FLAGS="-g")

        if self.add_custom_make_options:
            # Don't split the debug info from the binary, just keep it as part of the binary
            # This means we can just scp the file over to a cheribsd instace, run gdb and get symbols and sources.
            self.make_args.set_with_options(DEBUG_FILES=False)
            # Don't build manpages by default
            self.make_args.set_with_options(MAN=self.with_manpages)
            # we want to build makefs for the disk image (makefs depends on libnetbsd which will not be
            # bootstrapped on FreeBSD)
            self.make_args.set(LOCAL_XTOOL_DIRS="lib/libnetbsd usr.sbin/makefs usr.bin/mkimg")

        # doesn't appear to work for buildkernel
        # if self.auto_obj:
        #     # seems like it should speed up the build significantly
        #     self.common_options.add(AUTO_OBJ=True)

        # build only part of the tree
        if self.subdirOverride:
            self.make_args.set(SUBDIR_OVERRIDE=self.subdirOverride)

        self.destdir = self.installDir
        self._installPrefix = Path("/")
        self.kernelToolchainAlreadyBuilt = False
        for option in self.makeOptions:
            if not self._crossCompileTarget.is_cheri_purecap() and "CHERI_" in option:
                warningMessage("Not adding CHERI specific make option", option, "for", self.target,
                               " -- consider setting separate", self.target + "/make-options in the config file.")
                continue
            if "=" in option:
                key, value = option.split("=")
                args = {key: value}
                self.make_args.set(**args)
            else:
                self.make_args.add_flags(option)

    def _setup_cross_toolchain_config(self):
        if not self.use_external_toolchain:
            # Building FreeBSD for RISC-V requires an external GCC:
            if self.compiling_for_riscv():
                self.make_args.set(CROSS_TOOLCHAIN="riscv64-gcc")
            return
        self.cross_toolchain_config.set_with_options(
            # TODO: should we have an option to include a compiler in the target system?
            GCC=False, CLANG=False, LLD=False,  # Take a long time and not needed in the target system
            # Bootstrap compiler/ linker are not needed:
            GCC_BOOTSTRAP=False, CLANG_BOOTSTRAP=False, LLD_BOOTSTRAP=False,
            LIB32=False,  # takes a long time and not needed
            )
        if self.config.csetbounds_stats:
            self.cross_toolchain_config.set(CSETBOUNDS_LOGFILE=self.csetbounds_stats_file)
        if self.config.subobject_bounds:
            self.cross_toolchain_config.set(CHERI_SUBOBJECT_BOUNDS=self.config.subobject_bounds)
            self.cross_toolchain_config.set(CHERI_SUBOBJECT_BOUNDS_DEBUG="yes" if self.config.subobject_debug else "no")

        cross_bindir = self.crossToolchainRoot / "bin"
        cross_prefix = str(self.crossToolchainRoot / "bin") + "/"  # needs to end with / for concatenation
        target_flags = self._setup_arch_specific_options()

        self.externalToolchainCompiler = Path(cross_prefix + "clang")
        # TODO: should I be setting this in the environment instead?
        xcc = self.CC
        xccinfo = getCompilerInfo(xcc)
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
            # the stripbin environment variable to determine the path to strip
            # TODO: self.cross_toolchain_config.set_env(STRIPBIN=cross_bindir / "llvm-strip")
            # We currently still need elftoolchain strip
            self.cross_toolchain_config.set_with_options(ELFTOOLCHAIN_BOOTSTRAP=True)

            self.cross_toolchain_config.set(
                XAS="/xas/should/not/be/used",
                XAR=cross_bindir / "llvm-ar",
                # XLD
                XNM=cross_bindir / "llvm-nm",
                XSIZE=cross_bindir / "llvm-size",
                XSTRIPBIN=cross_bindir / "llvm-strip",
                XSTRINGS=cross_bindir / "llvm-strings",
                XOBJCOPY=cross_bindir / "llvm-objcopy",
                XRANLIB=cross_bindir / "llvm-ranlib",
                # See https://bugs.llvm.org/show_bug.cgi?id=41707
                RANLIBFLAGS="",  # llvm-ranlib doesn't support -D flag
                )
        # However, we do want to install the host tools
        self.cross_toolchain_config.set_with_options(TOOLCHAIN=True)

        if self.linker_for_world == "bfd":
            # self.cross_toolchain_config.set_env(XLDFLAGS="-fuse-ld=bfd")
            target_flags += " -fuse-ld=bfd -Qunused-arguments"
            # If WITH_LD_IS_LLD is set (e.g. by reading src.conf) the symlink ld -> ld.bfd in $BUILD_DIR/tmp/ won't be
            # created and the build system will then fall back to using /usr/bin/ld which won't work!
            self.cross_toolchain_config.set_with_options(LLD_IS_LD=False)
            self.cross_toolchain_config.set_env(XLD=cross_prefix + "ld.bfd"),
        else:
            assert self.linker_for_world == "lld"
            # TODO: we should have a better way of passing linker flags than adding them to XCFLAGS
            linker_flags = "-fuse-ld=lld -Qunused-arguments"
            # self.cross_toolchain_config.set_env(XLDFLAGS=linker_flags)
            target_flags += " " + linker_flags
            # Don't set XLD when using bfd since it will pick up ld.bfd from the build directory
            self.cross_toolchain_config.set_env(XLD=cross_prefix + "ld.lld"),

        if target_flags:
            self.cross_toolchain_config.set_env(XCFLAGS=target_flags)

        if self.linker_for_kernel == "lld" and self.linker_for_world == "lld" and not self.compiling_for_host():
            # When building freebsd x86 we need to build the 'as' binary
            self.cross_toolchain_config.set_with_options(BINUTILS_BOOTSTRAP=False)

    def _setup_arch_specific_options(self):
        if self.crosscompile_target.is_any_x86():
            target_flags = ""
            self.useExternalToolchainForWorld = True
            self.useExternalToolchainForKernel = True
            self.linker_for_kernel = "lld"  # bfd won't work here
            self.linker_for_world = "lld"
            # DONT SET XAS!!! It prevents bfd from being built
            # self.cross_toolchain_config.set(XAS="/usr/bin/as")
        elif self.compiling_for_mips(include_purecap=True):
            target_flags = " -integrated-as -fcolor-diagnostics -mcpu=beri"
            if self.compiling_for_cheri() and self.config.cheri_cap_table_abi == "legacy":
                target_flags += " -DCHERIABI_LEGACY_SUPPORT"
            # for some reason this is not inferred....
            # if self.crossbuild:
            #     # For some reason STRINGS is not set
            #     self.cross_toolchain_config.set(STRINGS="strings")
            self.cross_toolchain_config.set_with_options(RESCUE=False,  # Won't compile with CHERI clang yet
                                                         BOOT=False)  # bootloaders won't link with LLD yet
            # DONT SET XAS!!! It prevents bfd from being built
            # self.cross_toolchain_config.set(XAS=cross_prefix + "clang " + target_flags)
        elif self.compiling_for_riscv():
            target_flags = ""
            self.useExternalToolchainForWorld = True
            self.useExternalToolchainForKernel = True
            # TODO: determine flags
        else:
            self.fatal("Invalid state, should have a cross env")
            sys.exit(1)
        return target_flags

    @property
    def buildworldArgs(self) -> MakeOptions:
        result = self.make_args.copy()
        # FIXME: once it works for buildkernel remove here
        if self.auto_obj:
            result.set_with_options(AUTO_OBJ=True)
        if self.useExternalToolchainForWorld:
            if not self.externalToolchainCompiler.exists():
                self.fatal("Requested build of world with external toolchain, but", self.externalToolchainCompiler,
                           "doesn't exist!")
            result.update(self.cross_toolchain_config)
        return result

    def kernelMakeArgsForConfig(self, kernconf: str) -> MakeOptions:
        kernel_options = self.make_args.copy()
        if self.compiling_for_mips(include_purecap=True):
            # Don't build kernel modules for MIPS
            kernel_options.set(NO_MODULES="yes")
        if self.useExternalToolchainForKernel:
            if not self.externalToolchainCompiler.exists():
                self.fatal("Requested build of kernel with external toolchain, but", self.externalToolchainCompiler,
                           "doesn't exist!")
            # We can't use LLD for the kernel yet but there is a flag to experiment with it
            if self.compiling_for_mips(include_purecap=True):
                cross_prefix = str(self.crossToolchainRoot / "bin/mips64-unknown-freebsd-")
            else:
                cross_prefix = str(self.crossToolchainRoot / "bin") + "/"

            kernel_options.update(self.cross_toolchain_config)
            linker = cross_prefix + "ld." + self.linker_for_kernel
            fuse_ld_flag = "-fuse-ld=" + linker
            kernel_options.remove_var("LDFLAGS")
            kernel_options.set(LD=linker, XLD=linker, HACK_EXTRA_FLAGS="-shared " + fuse_ld_flag,
                               TRAMP_LDFLAGS=fuse_ld_flag)
            kernel_options.set_env(LDFLAGS=fuse_ld_flag, XLDFLAGS=fuse_ld_flag)
        if self.crossbuild:
            kernel_options.set_with_options(KERNEL_TRAMPOLINE=False)
            kernel_options.remove_var("DEBUG")
            kernel_options.remove_all(lambda s: s.startswith("DEBUG"))
            kernel_options.set(INSTALL_NODEBUG=True)
        kernel_options.set(KERNCONF=kernconf)
        return kernel_options

    def clean(self) -> ThreadJoiner:
        cleaning_kerneldir = False
        if self.config.skipBuildworld:
            root_builddir = self.objdir
            kernel_dir = self.kernel_objdir(self.kernelConfig)
            print(kernel_dir)
            if kernel_dir and kernel_dir.parent.exists():
                builddir = kernel_dir
                cleaning_kerneldir = True
            else:
                warningMessage("Do not know the full path to the kernel build directory, will clean the whole tree!")
                builddir = root_builddir
        else:
            # builddir = root_builddir
            # Clean up pre-MAKEOBJDIR change directories for now
            # The only advantage of only deleting .OBJDIR is that it doesn't confuse a shell in that
            # directory but I doubt that is a compelling usecase
            builddir = self.buildDir
        if builddir.exists() and self.buildDir.exists():
            assert not os.path.relpath(str(builddir.resolve()), str(self.buildDir.resolve())).startswith(".."), builddir
        if self.crossbuild:
            # avoid rebuilding bmake and libbsd when crossbuilding:
            return self.async_clean_directory(builddir, keep_root=not cleaning_kerneldir,
                                            keep_dirs=["libbsd-install", "bmake-install"])
        else:
            return self.async_clean_directory(builddir)

    def _buildkernel(self, kernconf: str, mfs_root_image: Path = None):
        kernelMakeArgs = self.kernelMakeArgsForConfig(kernconf)
        if self.debug_kernel:
            if "_BENCHMARK" in kernconf:
                if not self.query_yes_no("Trying to build BENCHMARK kernel without optimization. Continue?"):
                    return
            kernelMakeArgs.set(COPTFLAGS="-O0 -DBOOTVERBOSE=2")
        if mfs_root_image:
            kernelMakeArgs.set(MFS_IMAGE=mfs_root_image)
            if "MFS_ROOT" not in kernconf:
                warningMessage("Attempting to build an MFS_ROOT kernel but kernel config name sounds wrong")
        # needKernelToolchain = not self.useExternalToolchainForKernel
        dontNeedKernelToolchain = self.useExternalToolchainForKernel and self.linker_for_kernel == "lld"
        if not dontNeedKernelToolchain and not self.kernelToolchainAlreadyBuilt:
            # we might need to build GCC to build the kernel:
            kernel_toolchain_opts = self.make_args.copy()
            # Don't build a compiler if we are using and external toolchain (only build config, etc)
            if self.use_external_toolchain:
                kernel_toolchain_opts.set_with_options(LLD_BOOTSTRAP=False, CLANG=False, CLANG_BOOTSTRAP=False)
                kernel_toolchain_opts.set_with_options(GCC_BOOTSTRAP=self.useExternalToolchainForKernel)
            if self.auto_obj:
                kernel_toolchain_opts.set_with_options(AUTO_OBJ=True)
            self.runMake("kernel-toolchain", options=kernel_toolchain_opts)
            self.kernelToolchainAlreadyBuilt = True
        self.runMake("buildkernel", options=kernelMakeArgs,
                     compilationDbName="compile_commands_" + self.kernelConfig + ".json")

    def _installkernel(self, kernconf, destdir: str = None):
        # don't use multiple jobs here
        install_kernel_args = self.kernelMakeArgsForConfig(kernconf)
        install_kernel_args.env_vars.update(self.makeInstallEnv)
        # Also install all other kernels that were potentially built
        install_kernel_args.set(NO_INSTALLEXTRAKERNELS="no")
        # also install the debug files
        if self.addDebugInfoFlag:
            install_kernel_args.set_with_options(KERNEL_SYMBOLS=True)
            install_kernel_args.set(INSTALL_KERNEL_DOT_FULL=True)
        if destdir:
            install_kernel_args.set_env(DESTDIR=destdir)
        self.runMake("installkernel", options=install_kernel_args, parallel=False)

    def compile(self, mfs_root_image: Path = None, sysroot_only=False, **kwargs):
        # The build seems to behave differently when -j1 is passed (it still complains about parallel make failures)
        # so just omit the flag here if the user passes -j1 on the command line
        build_args = self.buildworldArgs
        if self.config.verbose:
            self.runMake("showconfig", options=build_args)
        if self.config.freebsd_host_tools_only:
            self.runMake("kernel-toolchain", options=build_args)
            return
        if sysroot_only:
            self.runMake("buildsysroot", options=build_args)
            return  # We are done after building the sysroot

        if not self.config.skipBuildworld:
            if self.fastRebuild:
                build_args.set(WORLDFAST=True)
            self.runMake("buildworld", options=build_args)
            self.kernelToolchainAlreadyBuilt = True  # includes the necessary tools for kernel-toolchain
        if not self.subdirOverride:
            for i in ("USBROOT", "NFSROOT", "MDROOT"):
                if ("_" + i) in self.kernelConfig:
                    self.info("Not embedding MFS_ROOT image in non-MFS root kernel config:", self.kernelConfig)
                    mfs_root_image = None
                    break
            self._buildkernel(kernconf=self.kernelConfig, mfs_root_image=mfs_root_image)

    def _removeOldRootfs(self):
        assert self.config.clean or not self.keepOldRootfs
        if self.config.skipBuildworld:
            self.makedirs(self.installDir)
        else:
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # We have to keep the rootfs directory in case it has been NFS mounted
            self.clean_directory(self.installDir, keep_root=True)

    def find_real_bmake_binary(self) -> Path:
        """return the path the bmake binary used for building. On FreeBSD this will generally be /usr/bin/make,
        but when crossbuilding we will usually use bmake-install/bin/bmake"
        """
        if self.crossbuild:
            make_cmd = self.buildDir / "bmake-install/bin/bmake"
        else:
            make_cmd = Path(shutil.which(self.make_args.command) or self.make_args.command)
        if not make_cmd.exists():
            raise FileNotFoundError(make_cmd)
        return make_cmd

    def _query_buildenv_path(self, args, var):
        try:
            try:
                bmake_binary = self.find_real_bmake_binary()
            except FileNotFoundError:
                self.verbose_print("Cannot query buildenv path if bmake hasn't been bootstrapped")
                return None
            buildenv_cmd = str(bmake_binary) + " -V " + var
            bw_flags = args.all_commandline_args + ["BUILD_WITH_STRICT_TMPPATH=0", "buildenv",
                                                    "BUILDENV_SHELL=" + buildenv_cmd]
            if self.crossbuild:
                bw_flags.append("PATH=" + os.getenv("PATH"))
            if not self.sourceDir.exists():
                assert self.config.pretend, "This should only happen when running in a test environment"
                return None
            # https://github.com/freebsd/freebsd/commit/1edb3ba87657e28b017dffbdc3d0b3a32999d933
            cmd = runCmd([bmake_binary] + bw_flags, env=args.env_vars, cwd=self.sourceDir,
                         runInPretendMode=True, captureOutput=True, print_verbose_only=True)
            lines = cmd.stdout.strip().split(b"\n")
            last_line = lines[-1].decode("utf-8").strip()
            if last_line.startswith("/") and cmd.returncode == 0:
                self.verbose_print("BUILDENV var", var, "was", last_line)
                return Path(last_line)
            warningMessage("Failed to query", var, "-- output was:", lines)
            return None
        except subprocess.CalledProcessError as e:
            warningMessage("Could not query make variable", var, "for buildworld root objdir: ", e)
            return None

    @property
    def objdir(self):
        # TODO use https://github.com/pydanny/cached-property ?
        objdir = self._query_buildenv_path(self.buildworldArgs, ".OBJDIR")
        if not objdir or objdir == Path():
            # just clean the whole directory instead
            warningMessage("Could not infer buildworld root objdir")
            return self.buildDir
        return objdir

    def kernel_objdir(self, config):
        result = self.objdir / "sys"
        if result.exists():
            return Path(result) / config
        warningMessage("Could not infer buildkernel objdir")
        return None

    @property
    def installworld_args(self):
        result = self.buildworldArgs
        result.env_vars.update(self.makeInstallEnv)
        return result

    def install(self, all_kernel_configs: str = None, sysroot_only=False, install_with_subdir_override=False,
                skip_kernel=False, **kwargs):
        if self.subdirOverride and not install_with_subdir_override:
            statusUpdate("Skipping install step because SUBDIR_OVERRIDE was set")
            return
        if self.config.freebsd_host_tools_only:
            statusUpdate("Skipping install step because freebsd-host-tools was set")
            return
        # keeping the old rootfs directory prior to install can sometimes cause the build to fail so delete by default
        if self.config.clean or not self.keepOldRootfs:
            self._removeOldRootfs()

        if not self.config.skipBuildworld or sysroot_only:
            install_world_args = self.installworld_args
            # https://github.com/CTSRD-CHERI/cheribsd/issues/220
            # installworld reads compiler metadata which was written by kernel-toolchain which means that
            # it will attempt to install libc++ because compiler for kernel is now clang and not GCC
            # as a workaround force writing the compiler metadata by invoking the _compiler-metadata target
            try:
                self.runMake("_build-metadata", options=install_world_args)
            except subprocess.CalledProcessError:
                try:
                    # support building old versions of cheribsd before _compiler-metadata was renamed to _build-metadata
                    self.runMake("_compiler-metadata", options=install_world_args)
                except subprocess.CalledProcessError:
                    warningMessage("Failed to run either target _compiler-metadata or "
                                   "_build_metadata, build system has changed!")
            if sysroot_only:
                self.runMake("installsysroot", options=install_world_args)
                # Don't try to install the kernel if we are only building a sysroot
                return
            else:
                self.runMake("installworld", options=install_world_args)
                self.runMake("distribution", options=install_world_args)

        if skip_kernel:
            return
        assert not sysroot_only, "Should not end up here"
        # Run installkernel after installworld since installworld deletes METALOG and therefore the files added by
        # the installkernel step will not be included if we run it first.
        if not all_kernel_configs:
            all_kernel_configs = self.kernelConfig
        self._installkernel(kernconf=all_kernel_configs)

    def addCrossBuildOptions(self):
        # we also need to ensure that our SDK build tools are being picked up first
        # build_path = str(self.sdk_bindir) + ":" + str(self.crossBinDir)
        # self.make_args.env_vars["PATH"] = build_path

        # Tell glibc functions to be POSIX compatible
        # Would be ideal, but it seems like there is too much that depends on non-posix flags
        # self.common_options.env_vars["POSIXLY_CORRECT"] = "1"
        # self.make_args.set(PATH=build_path)

        self.make_args.set_env(CC=self.host_CC, CXX=self.host_CXX, CPP=self.host_CPP)

        # we don't build elftoolchain during buildworld so for the kernel we need to set these variables
        self.make_args.set_env(XOBJDUMP=self.sdk_bindir / "llvm-objdump")
        self.make_args.set_env(OBJCOPY=self.sdk_bindir / "llvm-objcopy")
        # This is not actually the path to the strip binary but rather a flag to install
        # self.make_args.env_vars["STRIP"] = self.sdk_bindir / "strip"

        # don't build all the bootstrap tools (just pretend we are running freebsd 42):
        # self.make_args.env_vars["OSRELDATE"] = "4204345"

        # These all seem to work now
        # self.make_args.set_with_options(SYSCONS=False, USB=False, GPL_DTC=False)
        # self.make_args.set_with_options(CDDL=False)  # lots of bootstrap tools issues

        self.make_args.set_with_options(BINUTILS=True, CLANG=False, GCC=False, GDB=False, LLD=False, LLDB=False)

        # TODO: build these for zoneinfo setup
        # "zic", "tzsetup"
        # self.make_args.set_with_options(ZONEINFO=False)

        # self.make_args.set_with_options(KERBEROS=False)  # needs some more work with bootstrap tools

        # won't work with CHERI
        # self.common_options.add(DIALOG=False)

        # won't work on a case-insensitive file system and is also really slow (and missing tools on linux)
        self.make_args.set_with_options(MAN=False)
        # links from /usr/bin/mail to /usr/bin/Mail won't work on case-insensitve fs
        self.make_args.set_with_options(MAIL=False)
        self.make_args.set_with_options(SENDMAIL=False)  # libexec somehow won't compile

        self.make_args.set_with_options(VT=False)

        # We don't want separate .debug for now
        self.make_args.set_with_options(DEBUG_FILES=False)

        if self.compiling_for_host():
            cross_binutils_prefix = str(self.sdk_bindir) + "/"
            self.make_args.set_with_options(BHYVE=False,
                                            # seems to be missing some include paths which appears to work on freebsd
                                            CTF=False)  # can't crossbuild ctfconvert yet
            self.make_args.set_with_options(BOOT=True)
        else:
            cross_binutils_prefix = str(self.sdk_bindir) + "/mips64-unknown-freebsd-"
            self.make_args.set_with_options(BOOT=False)
        # This should no longer be necessary since we can bootstrap elftoolchain
        # self.make_args.set_env(CROSS_BINUTILS_PREFIX=cross_binutils_prefix)

    def process(self):
        if not IS_FREEBSD:
            if not self.crossbuild:
                statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need"
                             " to scp the required files from another server (see --frebsd-build-server options)")
                return
        # remove any environment variables that could interfere with bmake running
        for k, v in os.environ.copy().items():
            if k in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKE_TERMERR", "MAKE_TERMOUT", "MAKE"):
                os.unsetenv(k)
                del os.environ[k]

        if self.explicit_subdirs_only:
            # Allow building a single FreeBSD/CheriBSD directory using the BUILDENV_SHELL trick
            args = self.installworld_args
            for subdir in self.explicit_subdirs_only:
                self.build_and_install_subdir(args, subdir)

        elif self.config.buildenv or self.config.libcheri_buildenv:
            args = self.buildworldArgs
            args.remove_flag("-s")  # buildenv should not be silent
            if "bash" in os.getenv("SHELL", ""):
                args.set(BUILDENV_SHELL="env -u PROMPT_COMMAND 'PS1=" + self.target + "-buildenv:\\w> ' " +
                                        shutil.which("bash") + " --norc --noprofile")
            else:
                args.set(BUILDENV_SHELL="/bin/sh")
            buildenv_target = "buildenv"
            if self.compiling_for_cheri() and self.config.libcheri_buildenv:
                buildenv_target = "libcheribuildenv"
            runCmd([self.make_args.command] + args.all_commandline_args + [buildenv_target], env=args.env_vars,
                   cwd=self.sourceDir)
        else:
            super().process()

    def build_and_install_subdir(self, make_args, subdir, skip_build=False, skip_clean=None, skip_install=None,
                                 install_to_internal_sysroot=True, libcheri_only=False, noncheri_only=False):
        is_lib = subdir.startswith("lib/") or "/lib/" in subdir or subdir.endswith("/lib")
        make_in_subdir = "make -C \"" + subdir + "\" "
        if skip_clean is None:
            skip_clean = not self.config.clean
        if skip_install is None:
            skip_install = self.config.skipInstall
        if self.config.passDashKToMake:
            make_in_subdir += "-k "
        install_to_sysroot_cmd = ""
        if is_lib:
            if install_to_internal_sysroot:
                # Due to all the bmake + shell escaping I need 4 dollars here to get it to expand SYSROOT
                sysroot_var = "\"$$$${SYSROOT}\""
                install_to_sysroot_cmd = "if [ -n {sysroot} ]; then {make} install MK_TESTS=no DESTDIR={sysroot}; " \
                                         "fi".format(
                    make=make_in_subdir, sysroot=sysroot_var)
            if self.config.install_subdir_to_sysroot and self.get_corresponding_sysroot() is not None:
                if install_to_sysroot_cmd:
                    install_to_sysroot_cmd += " && "
                install_to_sysroot_cmd += "{make} install MK_TESTS=no DESTDIR={sysroot}".format(
                    make=make_in_subdir, sysroot=self.get_corresponding_sysroot())

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
            install_cmd = install_to_sysroot_cmd + make_in_subdir + "install"
        if self.compiling_for_cheri() and not is_lib:
            # for non-library targets we need to set WANT_CHERI=pure in the environment to get the binary
            # to build as a CHERI binary
            if any("WITH_CHERI_PURE" in x for x in make_args.all_commandline_args):
                statusUpdate("WITH_CHERI_PURE found in build args -> set WANT_CHERI?=pure for non-library", subdir)
                make_args.set_env(WANT_CHERI="pure")
        colour_diags = "export CLANG_FORCE_COLOR_DIAGNOSTICS=always; " if self.config.clang_colour_diags else ""
        build_cmd = "{colour_diags} {clean} && {build} && {install} && echo \"  Done.\"".format(
            build=make_in_subdir + "all " + commandline_to_str(
                self.jflag) if not skip_build else "echo \"  Skipping make all\"",
            clean=make_in_subdir + "clean" if not skip_clean else "echo \"  Skipping make clean\"",
            install=install_cmd, colour_diags=colour_diags)
        make_args.set(BUILDENV_SHELL="sh -ex -c '" + build_cmd + "' || exit 1")
        # If --libcheri-buildenv was passed skip the MIPS lib
        is_cheri_lib = self.compiling_for_cheri() and is_lib
        if is_cheri_lib and (self.config.libcheri_buildenv or libcheri_only):
            statusUpdate("Skipping MIPS build of", subdir, "since --libcheri-buildenv was passed.")
        else:
            statusUpdate("Building", subdir, "using buildenv target")
            runCmd([self.make_args.command] + make_args.all_commandline_args + ["buildenv"], env=make_args.env_vars,
                   cwd=self.sourceDir)
        # If we are building a library we want to build both the CHERI and the mips version (unless the
        # user explicitly specified --libcheri-buildenv)
        if is_cheri_lib and not noncheri_only:
            statusUpdate("Building", subdir, "using libcheribuildenv target")
            runCmd([self.make_args.command] + make_args.all_commandline_args + ["libcheribuildenv"],
                   env=make_args.env_vars,
                   cwd=self.sourceDir)


# Build FreeBSD with the default options (build the bundled clang instead of using the SDK one)
# also don't add any of the default -DWITHOUT/DWITH_FOO options
class BuildFreeBSDWithDefaultOptions(BuildFreeBSD):
    project_name = "freebsd"
    target = "freebsd-with-default-options"
    repository = GitRepository("https://github.com/freebsd/freebsd.git")
    build_dir_suffix = "-default-options"
    add_custom_make_options = False

    # also try to support building for RISCV
    supported_architectures = BuildFreeBSD.supported_architectures + [CompilationTargets.FREEBSD_RISCV,
                                                                      CompilationTargets.FREEBSD_I386]

    @classmethod
    def setup_config_options(cls, installDirectoryHelp=None, **kwargs):
        super().setup_config_options(buildKernelWithClang=True, bootstrap_toolchain=True, debug_info_by_default=False)

    def addCrossBuildOptions(self):
        # Just try to build as much as possible (but using make.py)
        pass


def jflag_in_subjobs(config: CheriConfig, proj):
    return max(1, config.makeJobs // 2)


def jflag_for_universe(config: CheriConfig, proj):
    return max(1, config.makeJobs // 4)


# Build all targets (to test my changes)
class BuildFreeBSDUniverse(BuildFreeBSDBase):
    project_name = "freebsd-universe"
    target = "freebsd-universe"
    repository = GitRepository("https://github.com/freebsd/freebsd.git")
    # already in the project name:    build_dir_suffix = "universe"
    defaultInstallDir = Path("/this/target/should/not/be/installed!")

    @classmethod
    def setup_config_options(cls, buildKernelWithClang: bool = True, bootstrap_toolchain=False,
                           debug_info_by_default=True, **kwargs):
        super().setup_config_options(add_common_cross_options=False, **kwargs)
        cls.tinderbox = cls.add_bool_option("tinderbox", help="Use `make tinderbox` instead of `make universe`")
        cls.worlds_only = cls.add_bool_option("worlds-only", help="Only build worlds (skip building kernels)")
        cls.kernels_only = cls.add_bool_option("kernels-only", help="Only build kernels (skip building worlds)",
                                             default=ComputedDefaultValue(
                                                 function=lambda conf, proj: conf.skipBuildworld,
                                                 as_string="true if --skip-buildworld is set"))

        cls.jflag_in_subjobs = cls.add_config_option("jflag-in-subjobs", help="Number of jobs in each world/kernel build",
                                                   kind=int, default=ComputedDefaultValue(jflag_in_subjobs,
                                                                                          "default -j flag / 2"))

        cls.jflag_for_universe = cls.add_config_option("jflag-for-universe",
                                                     help="Number of parallel world/kernel builds",
                                                     kind=int, default=ComputedDefaultValue(jflag_for_universe,
                                                                                            "default -j flag / 4"))

    def compile(self, cwd: Path = None):
        # The build seems to behave differently when -j1 is passed (it still complains about parallel make failures)
        # so just omit the flag here if the user passes -j1 on the command line
        build_args = self.make_args.copy()
        if self.config.verbose:
            self.runMake("showconfig", options=build_args)

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
            self.runMake("kernel-toolchains", options=build_args, parallel=False)

        if self.worlds_only:
            build_args.set(MAKE_JUST_WORLDS=True)
        if self.kernels_only:
            build_args.set(MAKE_JUST_KERNELS=True)
        self.runMake("tinderbox" if self.tinderbox else "universe", options=build_args, parallel=False)

    def install(self, **kwargs):
        self.info("freebsd-universe is a compile-only target")

    # Don't filter lines here
    _stdout_filter = Project._show_line_stdout_filter

    def process(self):
        if not IS_FREEBSD:
            if not self.crossbuild:
                statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need"
                             " to scp the required files from another server (see --frebsd-build-server options)")
                return
        # remove any environment variables that could interfere with bmake running
        for k, v in os.environ.copy().items():
            if k in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKE_TERMERR", "MAKE_TERMOUT", "MAKE"):
                os.unsetenv(k)
                del os.environ[k]

        super().process()


class BuildCHERIBSD(BuildFreeBSD):
    project_name = "cheribsd"
    target = "cheribsd"
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheribsd.git", per_target_branches={
        CompilationTargets.CHERIBSD_RISCV: TargetBranchInfo("riscv_cheri_clang", directory_name="cheribsd-riscv",
                                                            # url="https://github.com/bsdjhb/cheribsd"
                                                            )
        })
    defaultInstallDir = cheribsd_install_dir
    appendCheriBitsToBuildDir = True
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_PURECAP, CompilationTargets.CHERIBSD_X86_64,
                               CompilationTargets.CHERIBSD_MIPS, CompilationTargets.CHERIBSD_RISCV]
    is_sdk_target = True
    hide_options_from_help = False  # FreeBSD options are hidden, but this one should be visible
    crossbuild = True  # changes have been merged into master
    use_llvm_binutils = True


    @classmethod
    def setup_config_options(cls, installDirectoryHelp=None, **kwargs):
        if installDirectoryHelp is None:
            installDirectoryHelp = "Install directory for CheriBSD root file system (default: " \
                                   "<OUTPUT>/rootfs256 or <OUTPUT>/rootfs128 depending on --cheri-bits)"
        super().setup_config_options(buildKernelWithClang=True, installDirectoryHelp=installDirectoryHelp)
        cls.sysroot_only = cls.add_bool_option("sysroot-only", show_help=True,
                                             help="Only build a sysroot instead of the full system. This will only "
                                                  "build the libraries and skip all binaries")

        mips_and_purecap_mips = [CompilationTargets.CHERIBSD_MIPS, CompilationTargets.CHERIBSD_MIPS_PURECAP]
        if issubclass(cls, BuildCHERIBSDPurecap):
            mips_and_purecap_mips = [CompilationTargets.CHERIBSD_MIPS_PURECAP]
        cls.buildFpgaKernels = cls.add_bool_option("build-fpga-kernels", show_help=True, _allow_unknown_targets=True,
                                                 only_add_for_targets=mips_and_purecap_mips,
                                                 help="Also build kernels for the FPGA.")
        cls.mfs_root_image = cls.add_path_option("mfs-root-image", help="Path to an MFS root image to be embedded in the"
                                                                      " kernel for booting")

        # We also want to add this config option to the fake "cheribsd" target (to keep the config file manageable)
        cls.purecapKernel = cls.add_bool_option("pure-cap-kernel", show_help=True, _allow_unknown_targets=True,
                                              only_add_for_targets=[CompilationTargets.CHERIBSD_MIPS_PURECAP],
                                              help="Build kernel with pure capability ABI (probably won't work!)")

    def get_corresponding_sysroot(self):
        if not self.is_exact_instance(BuildCHERIBSD):
            return None
        return self.config.get_cheribsd_sysroot_path(self.crosscompile_target, False)

    @property
    def arch_build_flags(self):
        result = super().arch_build_flags
        if self.crosscompile_target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            result["CHERI"] = self.config.cheriBitsStr
        return result

    def __init__(self, config: CheriConfig):
        self.installAsRoot = os.getuid() == 0
        arch_build_flags = None

        # TODO: should we build a cheri kernel even with a mips userspace?
        # self.kernelConfig = "MALTA64"
        super().__init__(config)
        if self.compiling_for_cheri():
            if self.config.cheri_cap_table_abi:
                self.cross_toolchain_config.set(CHERI_USE_CAP_TABLE=self.config.cheri_cap_table_abi)

        if self.compiling_for_riscv():
            self.make_args.set(CROSS_BINUTILS_PREFIX=str(self.sdk_bindir / "llvm-"))
            self.use_llvm_binutils = True

        self.extra_kernels = []
        self.extra_kernels_with_mfs = []
        if self.buildFpgaKernels:
            if self.compiling_for_mips(include_purecap=True):
                if self._crossCompileTarget.is_cheri_purecap([CPUArchitecture.MIPS64]):
                    if self.config.cheriBits == 128:
                        prefix = "CHERI128_DE4_"
                    elif self.config.cheriBits == 256:
                        prefix = "CHERI_DE4_"
                    else:
                        assert False, "unreachable"
                else:
                    prefix = "BERI_DE4_"
            else:
                prefix = "INVALID_KERNCONF_"
                self.fatal("Invalid CHERI BITS")
            # TODO: build the benchmark kernels?
            for conf in ("USBROOT", "USBROOT_BENCHMARK"):
                self.extra_kernels.append(prefix + conf)
            if self.mfs_root_image:
                self.extra_kernels_with_mfs.append(prefix + "MFS_ROOT")
                self.extra_kernels_with_mfs.append(prefix + "MFS_ROOT_BENCHMARK")

    def _removeSchgFlag(self, *paths: "typing.Iterable[str]"):
        for i in paths:
            file = self.installDir / i
            if file.exists():
                runCmd("chflags", "noschg", str(file))

    def _removeOldRootfs(self):
        if not self.config.skipBuildworld:
            if self.installAsRoot:
                # if we installed as root remove the schg flag from files before cleaning (otherwise rm will fail)
                self._removeSchgFlag(
                    "lib/libc.so.7", "lib/libcrypt.so.5", "lib/libthr.so.3", "libexec/ld-cheri-elf.so.1",
                    "libexec/ld-elf.so.1", "sbin/init", "usr/bin/chpass", "usr/bin/chsh", "usr/bin/ypchpass",
                    "usr/bin/ypchfn", "usr/bin/ypchsh", "usr/bin/login", "usr/bin/opieinfo", "usr/bin/opiepasswd",
                    "usr/bin/passwd", "usr/bin/yppasswd", "usr/bin/su", "usr/bin/crontab", "usr/lib/librt.so.1",
                    "var/empty"
                    )
        super()._removeOldRootfs()

    def compile(self, **kwargs):
        if not self.CC.is_file():
            self.fatal("CHERI CC does not exist: ", self.CC)
        if not self.CXX.is_file():
            self.fatal("CHERI CXX does not exist: ", self.CXX)
        if self.crossToolchainRoot:
            mipsCC = self.crossToolchainRoot / "bin/clang"
            if not mipsCC.is_file():
                self.fatal("MIPS toolchain specified but", mipsCC, "is missing.")
        super().compile(mfs_root_image=self.mfs_root_image, sysroot_only=self.sysroot_only, **kwargs)
        if self.sysroot_only:
            return  # Don't attempt to build extra kernels if we are only building a sysroot

        # We could also just pass multiple values in KERNCONF to build all those kernels. However, if MFS_ROOT is set
        # that will apply to all those kernels and embed the rootfs even if not needed
        for i in self.extra_kernels:
            self._buildkernel(kernconf=i)
        for i in self.extra_kernels_with_mfs:
            self._buildkernel(kernconf=i, mfs_root_image=self.mfs_root_image)

    def install(self, **kwargs):
        all_kernel_configs = self.kernelConfig
        # If we build the FPGA kernels also install them into boot:
        for i in self.extra_kernels + self.extra_kernels_with_mfs:
            all_kernel_configs += " " + i
        super().install(all_kernel_configs=all_kernel_configs, sysroot_only=self.sysroot_only, **kwargs)

    def update(self):
        super().update()
        if not (self.sourceDir / "contrib/cheri-libc++/src").exists():
            runCmd("git", "submodule", "init", cwd=self.sourceDir)
            runCmd("git", "submodule", "update", cwd=self.sourceDir)

    def process(self):
        # Compatibility with older versions of cheribuild (and scripts that hardcode the path):
        # Create a symlink from the new build directory name to the old build directory name.
        if self.compiling_for_cheri() and self.config.cheri_cap_table_abi == self.config.DEFAULT_CAP_TABLE_ABI:
            old_build_dir = Path(self.config.buildRoot, "cheribsd-obj-" + self.config.cheriBitsStr)
            if not old_build_dir.is_symlink():
                self.info("Updating old build directory name:")
                if not self.buildDir.exists() and old_build_dir.exists() and not self.config.clean:
                    self.run_cmd("mv", old_build_dir, self.buildDir)
                else:
                    self.clean_directory(old_build_dir, ensure_dir_exists=False)
                self.createSymlink(self.buildDir, old_build_dir, cwd=old_build_dir.parent, print_verbose_only=False)
        super().process()


class BuildCheriBsdMfsKernel(SimpleProject):
    project_name = "cheribsd-mfs-root-kernel"
    dependencies = ["disk-image-minimal"]
    # TODO: also support building a non-CHERI kernel... But that needs a plain MIPS disk-image-minimal first...
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_PURECAP]
    _always_add_suffixed_targets = True

    def process(self):
        from ..disk_image import BuildMinimalCheriBSDDiskImage
        minimal_image_instance = BuildMinimalCheriBSDDiskImage.get_instance(self)
        image = minimal_image_instance.disk_image_path
        # Re-use the same build directory as the CheriBSD target that was used for the disk image
        # This ensure that the kernel build tools can be found in the build directory
        build_cheribsd_instance = minimal_image_instance.cheribsd_class.get_instance(self)
        kernconf = self._get_kernconf_to_build(build_cheribsd_instance)
        if self.config.clean:
            kernel_dir = build_cheribsd_instance.kernel_objdir(kernconf)
            if kernel_dir:
                with self.async_clean_directory(kernel_dir):
                    self.verbose_print("Cleaning ", kernel_dir)
        self._build_and_install_kernel_binary(build_cheribsd_instance, kernconf=kernconf, image=image)
        # also build the benchmark kernel:
        self._build_and_install_kernel_binary(build_cheribsd_instance, kernconf=kernconf + "_BENCHMARK", image=image)

        if build_cheribsd_instance.buildFpgaKernels:
            prefix = self.fpga_kernconf
            self._build_and_install_kernel_binary(build_cheribsd_instance, kernconf=prefix, image=image)
            self._build_and_install_kernel_binary(build_cheribsd_instance, kernconf=prefix + "_BENCHMARK", image=image)

    @property
    def fpga_kernconf(self):
        if self.compiling_for_mips(include_purecap=False):
            return "BERI_DE4_MFS_ROOT"
        elif self.compiling_for_cheri():
            return "CHERI128_DE4_MFS_ROOT" if self.config.cheriBits == 128 else "CHERI_DE4_MFS_ROOT"
        else:
            self.fatal("Invalid ARCH")
            return "INVALID_KERNCONF"

    def _build_and_install_kernel_binary(self, build_cheribsd: BuildCHERIBSD, kernconf: str, image: Path):
        # Install to a temporary directory and then copy the kernel to OUTPUT_ROOT
        # noinspection PyProtectedMember
        build_cheribsd._buildkernel(kernconf=kernconf, mfs_root_image=image)
        with tempfile.TemporaryDirectory(prefix="cheribuild-" + self.target + "-") as td:
            # noinspection PyProtectedMember
            build_cheribsd._installkernel(kernconf=kernconf, destdir=td)
            # runCmd("find", td)
            kernel_install_path = self.installed_kernel_for_config(self.config, kernconf)
            self.deleteFile(kernel_install_path)
            self.installFile(Path(td, "boot/kernel/kernel"), kernel_install_path, force=True, print_verbose_only=False)
            if Path(td, "boot/kernel/kernel.full").exists():
                fullkernel_install_path = kernel_install_path.with_name(kernel_install_path.name + ".full")
                self.installFile(Path(td, "boot/kernel/kernel.full"), fullkernel_install_path, force=True,
                                 print_verbose_only=False)

    @property
    def crossbuild(self):
        return BuildCHERIBSD.get_instance(self).crossbuild

    def update(self):
        if not self.config.skipUpdate:
            statusUpdate("Not updating cheribsd repo when building mfs-root-kernel to avoid unwanted changes")
        pass

    @classmethod
    def get_kernel_config(cls, caller: SimpleProject) -> str:
        config = caller.config
        if caller.get_crosscompile_target(config).is_mips(
                include_purecap=False) and config.run_mips_tests_with_cheri_image:
            build_cheribsd = BuildCHERIBSD.get_instance_for_cross_target(CompilationTargets.CHERIBSD_MIPS_PURECAP,
                                                                         config, caller=caller)
        else:
            build_cheribsd = BuildCHERIBSD.get_instance(caller, config)
        return cls._get_kernconf_to_build(build_cheribsd)

    @classmethod
    def _get_kernconf_to_build(cls, build_cheribsd: BuildCHERIBSD):
        return build_cheribsd.kernelConfig + "_MFS_ROOT"

    @classmethod
    def get_installed_kernel_path(cls, caller) -> Path:
        return cls.installed_kernel_for_config(caller.config, cls.get_kernel_config(caller))

    @classmethod
    def get_installed_benchmark_kernel_path(cls, caller) -> Path:
        return cls.installed_kernel_for_config(caller.config, cls.get_kernel_config(caller) + "_BENCHMARK")

    @staticmethod
    def installed_kernel_for_config(config: CheriConfig, kernconf: str) -> Path:
        return config.cheribsd_image_root / ("kernel" + config.cheri_bits_and_abi_str + "." + kernconf)


class BuildCHERIBSDPurecap(BuildCHERIBSD):
    project_name = "cheribsd"  # reuse the same source dir
    target = "cheribsd-purecap"
    _config_inherits_from = "cheribsd"  # we want the CheriBSD config options as well

    # Set these variables to override the multi target magic and only support CHERI
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_PURECAP]  # Only Cheri is supported
    build_dir_suffix = "-purecap"

    defaultInstallDir = ComputedDefaultValue(function=cheribsd_purecap_install_dir,
                                             as_string="$INSTALL_ROOT/rootfs-purecap{128/256}")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

    def __init__(self, config):
        super().__init__(config)
        self.make_args.set_with_options(CHERI_PURE=True)


class BuildCHERIBSDMinimal(BuildCHERIBSD):
    project_name = "cheribsd"  # reuse the same source dir
    target = "cheribsd-minimal"
    _config_inherits_from = "cheribsd"  # we want the CheriBSD config options as well

    # Set these variables to override the multi target magic and only support CHERI
    _should_not_be_instantiated = False
    build_dir_suffix = "-minimal"
    defaultInstallDir = ComputedDefaultValue(function=cheribsd_minimal_install_dir,
                                             as_string="$INSTALL_ROOT/rootfs-minmal{128,256,-mips,-x86}")

    @classmethod
    def setup_config_options(cls, **kwargs):
        cls.subdirOverride = None  # "tools/cheribsdbox"
        cls.minimal = True
        cls.build_tests = False
        super().setup_config_options(**kwargs)

    def __init__(self, config):
        super().__init__(config)
        if self.compiling_for_cheri():
            self.make_args.set_with_options(CHERI_PURE=True)
        self.make_args.set_with_options(INCLUDES=False, PROFILE=False, MAN=False, KERBEROS=False)
        # Avoid building as many libraries as possible
        self.make_args.set_with_options(PMC=False, RADIUS_SUPPORT=False, SENDMAIL=False, TELNET=False, TESTS=False,
                                        TESTS_SUPPORT=False, UNBOUND=False, USB=False, OFED=False, ZFS=False,
                                        NIS=False, NAND=False, CUSE=False, DIALOG=False, FILE=False, GPIO=False,
                                        GSSAPI=False, KERBEROS_SUPPORT=False, LDNS=False, TOOLCHAIN=False,
                                        BLUETOOTH=False, BSNMP=False, AMD=False, AT=False)
        self.make_args.set(NO_SHARE=True)
        # TODO: ICONV=False?
        self.needed_shlibs = ("lib/libc", "lib/libthr", "lib/libutil", "lib/libz", "lib/libutil",
                              "lib/libstatcounters", "lib/libxo", "lib/libedit", "lib/ncurses")
        self.sysroot_only = True

    def compile(self, **kwargs):
        args_without_subdir_override = self.buildworldArgs
        # subdir-override seems to break if we don't build toolchain first
        args_without_subdir_override.remove_var("SUBDIR_OVERRIDE")
        self.runMake("kernel-toolchain", options=args_without_subdir_override)
        super().compile(**kwargs)
        self.build_and_install_subdir(args_without_subdir_override, "tools/cheribsdbox",
                                      skip_build=False, skip_install=True, install_to_internal_sysroot=True)
        for i in self.needed_shlibs:
            self.build_and_install_subdir(args_without_subdir_override, i, skip_build=False,
                                          skip_install=True, install_to_internal_sysroot=True)

    def install(self, **kwargs):
        self.makedirs(self.installDir)
        for i in ("bin", "sbin", "usr/sbin", "usr/bin", "lib", "usr/lib", "usr/libcheri"):
            self.makedirs(self.installDir / i)
        # install all the needed libs
        args = self.installworld_args
        args.remove_var("SUBDIR_OVERRIDE")
        for i in self.needed_shlibs:
            self.build_and_install_subdir(args, i, skip_build=True, skip_clean=True, skip_install=False)
        for i in ["lib/libpam"]:
            # only needed as non-CheriABI libs:
            self.build_and_install_subdir(args, i, skip_build=True, skip_clean=True, skip_install=False,
                                          noncheri_only=True)

        self.build_and_install_subdir(args, "tools/cheribsdbox", skip_build=True, skip_clean=True,
                                      skip_install=False, install_to_internal_sysroot=False)
        # TODO: install bin/sh? bin/csh?


class BuildCheriBsdSysroot(SimpleProject):
    # TODO: could use this to build only cheribsd sysroot by extending build-cheribsd
    project_name = "cheribsd-sysroot"
    is_sdk_target = True
    rootfs_source_class = BuildCHERIBSD  # type: typing.Type[BuildCHERIBSD]

    @classproperty
    def supported_architectures(cls):
        return cls.rootfs_source_class.supported_architectures

    @classmethod
    def dependencies(cls, config: CheriConfig):
        target = cls.get_crosscompile_target(config)  # type: CrossCompileTarget
        if target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            # TODO: can't access this member here...
            # if cls.use_cheribsd_purecap_rootfs:
            #    return ["cheribsd-purecap"]
            pass
        return [cls.rootfs_source_class.target]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # GNU tar doesn't accept --include (and doesn't handle METALOG). bsdtar appears to be available
        # on FreeBSD and macOS by default. On Linux it is not always installed by default.
        self.bsdtar_cmd = "bsdtar"
        self.addRequiredSystemTool("bsdtar", cheribuild_target="bsdtar", apt="bsdtar")
        if self.compiling_for_cheri() and self.use_cheribsd_purecap_rootfs:
            self.rootfs_source_class = BuildCHERIBSDPurecap
        self.install_dir = self.target_info.sdk_root_dir

    def fixSymlinks(self):
        # copied from the build_sdk.sh script
        # TODO: we could do this in python as well, but this method works
        # FIXME: should no longer be needed
        fixlinks_src = includeLocalFile("files/fixlinks.c")
        runCmd("cc", "-x", "c", "-", "-o", self.install_dir / "bin/fixlinks", input=fixlinks_src)
        runCmd(self.install_dir / "bin/fixlinks", cwd=self.crossSysrootPath / "usr/lib")

    def check_system_dependencies(self):
        super().check_system_dependencies()
        if not IS_FREEBSD and not self.remotePath and not self.rootfs_source_class.get_instance(self).crossbuild:
            config_option = "'--" + self.target + "/" + "remote-sdk-path'"
            self.fatal("Path to the remote SDK is not set, option", config_option,
                       "must be set to a path that scp understands (e.g. vica:~foo/cheri/output/sdk)")
            if not self.config.pretend:
                sys.exit("Cannot continue...")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.copy_remote_sysroot = cls.add_bool_option("copy-remote-sysroot",
            help="Copy sysroot from remote server instead of from local machine")
        cls.remotePath = cls.add_config_option("remote-sdk-path", show_help=True, metavar="PATH",
            help="The path to the CHERI SDK on the remote FreeBSD machine (e.g. vica:~foo/cheri/output/sdk)")
        cls.use_cheri_sysroot_for_mips = cls.add_bool_option("use-cheri-sysroot-for-mips",
            help="Create the MIPS sysroot using the files from hybrid CHERI libraries (note: binaries build from this "
                 "sysroot will only work on the matching CHERI 128/256 architecture)")
        cls.use_cheribsd_purecap_rootfs = cls.add_bool_option("use-cheribsd-purecap-rootfs",
            help="Use the rootfs built by cheribsd-purecap instead")
        cls.install_dir_override = cls.add_path_option("install-directory",
            help="Override for the sysroot install directory")

    @property
    def crossSysrootPath(self) -> Path:
        if self.install_dir_override:
            return self.install_dir_override
        return super().crossSysrootPath

    def copySysrootFromRemoteMachine(self):
        statusUpdate("Copying sysroot from remote system.")
        if not self.remotePath:
            self.fatal(
                "Missing remote SDK path: Please set --cheribsd-sysroot/remote-sdk-path (or --freebsd/crossbuild)")
            if self.config.pretend:
                self.remotePath = "someuser@somehose:this/path/does/not/exist"
        # noinspection PyAttributeOutsideInit
        self.remotePath = os.path.expandvars(self.remotePath)
        remoteSysrootArchive = self.remotePath + "/" + self.sysrootArchiveName
        statusUpdate("Will copy the sysroot files from ", remoteSysrootArchive, sep="")
        if not self.query_yes_no("Continue?"):
            return

        # now copy the files
        self.makedirs(self.crossSysrootPath)
        self.copyRemoteFile(remoteSysrootArchive, self.sysroot_archive)
        runCmd("tar", "xzf", self.sysroot_archive, cwd=self.crossSysrootPath.parent)

    @property
    def sysrootArchiveName(self):
        if self.compiling_for_cheri():
            return "cheri-sysroot" + self.config.cheri_bits_and_abi_str + ".tar.gz"
        else:
            return "cheribsd-" + self._crossCompileTarget.generic_suffix + "-sysroot.tar.gz"

    @property
    def sysroot_archive(self):
        return self.crossSysrootPath.parent / self.sysrootArchiveName

    def createSysroot(self):
        # we need to add include files and libraries to the sysroot directory
        self.makedirs(self.crossSysrootPath / "usr")
        # use tar+untar to copy all necessary files listed in metalog to the sysroot dir
        # Since we are using the metalog argument we need to use BSD tar and not GNU tar!
        bsdtar_path = shutil.which(str(self.bsdtar_cmd))
        if not bsdtar_path:
            bsdtar_path = str(self.bsdtar_cmd)
        tar_cmd = [bsdtar_path, "cf", "-", "--include=./lib/", "--include=./usr/include/",
                   "--include=./usr/lib/", "--include=./usr/libdata/", "--include=./usr/libcheri"]
        # only pack those files that are mentioned in METALOG
        tar_cmd.append("@METALOG")
        if self.compiling_for_mips(include_purecap=False) and self.use_cheri_sysroot_for_mips:
            rootfs_target = self.rootfs_source_class.get_instance_for_cross_target(
                CompilationTargets.CHERIBSD_MIPS_PURECAP, self.config)
        else:
            rootfs_target = self.rootfs_source_class.get_instance(self)
        rootfs_dir = rootfs_target.real_install_root_dir
        if not (rootfs_dir / "lib/libc.so.7").is_file():
            if self.compiling_for_mips(include_purecap=False) and not self.use_cheri_sysroot_for_mips:
                fixit = "Either build a plain-mips CheriBSD rootfs first by running `cheribuild.py " + \
                        rootfs_target.target + "` or set --cheribsd-sysroot-mips/use-cheri-sysroot-for-mips" \
                                               " to copy from the CheriBSD sysroot instead"
            else:
                fixit = "Run `cheribuild.py " + rootfs_target.target + "` first"
            self.fatal("Sysroot source directory", rootfs_dir, "does not contain libc.so.7", fixitHint=fixit)
        printCommand(tar_cmd, cwd=rootfs_dir)
        if not self.config.pretend:
            tar_cwd = str(rootfs_dir)
            with subprocess.Popen(tar_cmd, stdout=subprocess.PIPE, cwd=tar_cwd) as tar:
                runCmd(["tar", "xf", "-"], stdin=tar.stdout, cwd=self.crossSysrootPath)
        if not (self.crossSysrootPath / "lib/libc.so.7").is_file():
            self.fatal(self.crossSysrootPath, "is missing the libc library, install seems to have failed!")

        # fix symbolic links in the sysroot:
        print("Fixing absolute paths in symbolic links inside lib directory...")
        self.fixSymlinks()
        # create an archive to make it easier to copy the sysroot to another machine
        self.deleteFile(self.sysroot_archive, print_verbose_only=True)
        runCmd("tar", "-czf", self.sysroot_archive, self.crossSysrootPath.name, cwd=self.crossSysrootPath.parent)
        print("Successfully populated sysroot")

    def process(self):
        if self.config.skipBuildworld:
            statusUpdate("Not building sysroot because --skip-buildworld was passed")
            return

        with self.async_clean_directory(self.crossSysrootPath):
            building_on_host = IS_FREEBSD or self.rootfs_source_class.get_instance(self).crossbuild
            if self.copy_remote_sysroot or not building_on_host:
                self.copySysrootFromRemoteMachine()
            else:
                self.createSysroot()
            if (self.crossSysrootPath / "usr/libcheri/").is_dir():
                # clang++ expects libgcc_eh to exist:
                libgcc_eh = self.crossSysrootPath / "usr/libcheri/libgcc_eh.a"
                if not libgcc_eh.is_file():
                    warningMessage("CHERI libgcc_eh missing! You should probably update CheriBSD")
                    runCmd("ar", "rc", libgcc_eh)


class BuildCheriBsdAndSysroot(TargetAlias):
    target = "cheribsd-with-sysroot"
    dependencies = ["cheribsd-cheri", "cheribsd-sysroot-cheri"]
