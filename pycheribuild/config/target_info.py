#
# Copyright (c) 2019 Alex Richardson
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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
import typing
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path

from ..utils import cached_property, final, OSInfo

if typing.TYPE_CHECKING:  # no-combine
    from .chericonfig import CheriConfig  # no-combine    # pytype: disable=pyi-error
    from ..projects.project import SimpleProject, Project  # no-combine

__all__ = ["AutoVarInit", "BasicCompilationTargets", "CPUArchitecture", "CrossCompileTarget",  # no-combine
           "Linkage", "CompilerType", "MipsFloatAbi", "TargetInfo"]  # no-combine


class CPUArchitecture(Enum):
    AARCH64 = "aarch64"
    ARM32 = "arm32"
    I386 = "i386"
    MIPS64 = "mips64"
    RISCV64 = "riscv64"
    X86_64 = "x86_64"

    def is_32bit(self) -> bool:
        return self in (CPUArchitecture.ARM32, CPUArchitecture.I386)

    def is_64bit(self) -> bool:
        return not self.is_32bit()

    def as_meson_cpu_family(self) -> str:
        # https://mesonbuild.com/Reference-tables.html#cpu-families
        if self is CPUArchitecture.I386:
            return "x86"
        if self is CPUArchitecture.ARM32:
            return "arm"
        # All others match the Meson table
        return self.value

    def endianess(self) -> str:
        # Meson expects us to pass this manually... Why not query the compiler???
        if self is CPUArchitecture.MIPS64:
            return "big"
        return "little"


class CompilerType(Enum):
    """
    Used by the jenkins script to detect which compiler directory should be used
    """
    DEFAULT_COMPILER = "default-compiler"  # Default system compiler (i.e. the argument passed to cheribuild)
    CHERI_LLVM = "cheri-llvm"  # Compile with CHERI LLVM built by cheribuild
    MORELLO_LLVM = "morello-llvm"  # Compile with Morello LLVM built by cheribuild
    UPSTREAM_LLVM = "upstream-llvm"  # Compile with upstream LLVM built by cheribuild
    SYSTEM_LLVM = "system-llvm"  # Compile with system installation of LLVM/Clang
    BOOTSTRAPPED = "bootstrap"  # Compiler is included with the project
    CUSTOM = "custom"  # Custom compiler specific in config file/command line


class TargetInfo(ABC):
    shortname = "INVALID"  # type: str
    # os_prefix defaults to shortname.lower() if not set
    os_prefix = None  # type: typing.Optional[str]

    def __init__(self, target: "CrossCompileTarget", project: "SimpleProject"):
        self.target = target
        self.project = project

    @property
    def cmake_processor_id(self):
        if self.target.is_mips(include_purecap=True):
            if self.target.is_cheri_purecap():
                return "CHERI (MIPS IV compatible) with {}-bit capabilities".format(self.config.mips_cheri_bits_str)
            else:
                return "BERI (MIPS IV compatible)"
        if self.target.is_aarch64(include_purecap=True):
            return "ARM64"
        return self.target.cpu_architecture.value

    @property
    @abstractmethod
    def cmake_system_name(self) -> str:
        ...

    @property
    def toolchain_system_version(self) -> "typing.Optional[str]":
        return None

    @property
    def cmake_prefix_paths(self) -> "list[Path]":
        """List of additional directories to be searched for packages (e.g. sysroot/usr/local/riscv64-purecap)"""
        return []

    @property
    @abstractmethod
    def sdk_root_dir(self) -> Path:
        ...

    @property
    @abstractmethod
    def sysroot_dir(self) -> Path:
        ...

    @property
    def sysroot_install_prefix_absolute(self) -> Path:
        return self.sysroot_dir / self.sysroot_install_prefix_relative

    @property
    def sysroot_install_prefix_relative(self) -> Path:
        """
        :return: The install dir inside the sysroot for non-system targets:
        By default everything is installed directly to the sysroot (i.e. libraries in sysroot/<lib>)
        For FreeBSD sysroots, we install third-party software to <sysroot>/usr/local and for CheriBSD, we use
        <sysroot>/usr/local/<target> to allow installing hybrid/non-cheri/cheri to the same sysroot.
        """
        return Path()

    @property
    def additional_rpath_directories(self) -> "list[str]":
        return []

    @cached_property
    def target_triple(self) -> str:
        return self.get_target_triple(include_version=True)

    @abstractmethod
    def get_target_triple(self, *, include_version: bool) -> str:
        ...

    @property
    @abstractmethod
    def c_compiler(self) -> Path:
        ...

    @property
    @abstractmethod
    def cxx_compiler(self) -> Path:
        ...

    @property
    @abstractmethod
    def linker(self) -> Path:
        ...

    @property
    @abstractmethod
    def ar(self) -> Path:
        ...

    @property
    @abstractmethod
    def ranlib(self) -> Path:
        ...

    @property
    @abstractmethod
    def nm(self) -> Path:
        ...

    @property
    @abstractmethod
    def strip_tool(self) -> Path:
        ...

    @classmethod
    @abstractmethod
    def essential_compiler_and_linker_flags_impl(cls, instance: "TargetInfo", *, xtarget: "CrossCompileTarget",
                                                 perform_sanity_checks=True, default_flags_only=False):
        """
        :return: flags such as -target + -mabi which are needed for both compiler and linker
        """
        ...

    def get_essential_compiler_and_linker_flags(self, xtarget: "CrossCompileTarget" = None,
                                                perform_sanity_checks=True, default_flags_only=False):
        return self.essential_compiler_and_linker_flags_impl(self, perform_sanity_checks=perform_sanity_checks,
                                                             xtarget=xtarget if xtarget is not None else self.target,
                                                             default_flags_only=default_flags_only)

    @property
    def additional_executable_link_flags(self):
        """Additional linker flags that need to be passed when building an executable (e.g. custom linker script)"""
        return []

    @property
    def additional_shared_library_link_flags(self):
        """Additional linker flags that need to be passed when building an shared library (e.g. custom linker script)"""
        return []

    @property
    def default_libdir(self):
        return "lib"

    @property
    @abstractmethod
    def c_preprocessor(self) -> Path:
        ...

    @classmethod
    @abstractmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        """returns e.g. [llvm]/[upstream-llvm], or an empty list"""
        ...

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        """returns a list of targets that need to be built for a minimal sysroot"""
        return []

    def default_initial_compile_flags(self) -> typing.List[str]:
        """Flags that need to be passed to cc/c++/cpp in all cases"""
        return []

    # noinspection PyMethodMayBeStatic
    def required_link_flags(self) -> typing.List[str]:
        """Flags that need to be passed to cc/c++ for linking"""
        return []

    @property
    def pkgconfig_dirs(self) -> "typing.List[str]":
        return []  # whatever the default is

    @property
    def install_prefix_dirname(self):
        """The name of the root directory to install to: i.e. for CheriBSD /usr/local/mips64-purecap or
        /usr/local/riscv64-hybrid"""
        result = self.target.generic_suffix
        if self.config.cross_target_suffix:
            result += "-" + self.config.cross_target_suffix
        return result

    @property
    def config(self) -> "CheriConfig":
        return self.project.config

    @property
    def must_link_statically(self):
        """E.g. for baremetal target infos we have to link statically (and add the -static linker flag)"""
        return False

    @final
    def get_rootfs_project(self, xtarget: "CrossCompileTarget" = None) -> "Project":
        return self._get_rootfs_project(xtarget if xtarget is not None else self.target)

    def _get_rootfs_project(self, xtarget: "CrossCompileTarget") -> "Project":
        raise RuntimeError("Should not be called for " + self.project.target)

    @classmethod
    def is_native(cls):
        return False

    @classmethod
    def is_baremetal(cls):
        return False

    @classmethod
    def is_rtems(cls):
        return False

    @classmethod
    def is_newlib(cls):
        return False

    @classmethod
    def is_freebsd(cls):
        return False

    @classmethod
    def is_cheribsd(cls):
        return False

    def run_cheribsd_test_script(self, script_name, *script_args, kernel_path=None, disk_image_path=None,
                                 mount_builddir=True, mount_sourcedir=False, mount_sysroot=False,
                                 use_full_disk_image=False, mount_installdir=False,
                                 use_benchmark_kernel_by_default=False):
        raise ValueError("run_cheribsd_test_script only supports CheriBSD targets")

    def run_fpga_benchmark(self, benchmarks_dir: Path, *, output_file: str = None, benchmark_script: str = None,
                           benchmark_script_args: list = None, extra_runbench_args: list = None):
        raise ValueError("run_fpga_benchmark only supports CheriBSD targets")

    @classmethod
    def is_macos(cls):
        return False

    @classmethod
    def is_linux(cls):
        return False

    @property
    def pointer_size(self):
        if self.target.is_cheri_purecap():
            return self.capability_size
        if self.target.is_i386():
            return 4
        # all other architectures we support currently use 64-bit pointers
        return 8

    @property
    def capability_size(self):
        if self.target.is_hybrid_or_purecap_cheri([CPUArchitecture.MIPS64]):
            assert self.config.mips_cheri_bits in (128, 256), "No other cap size supported yet"
            return self.config.mips_cheri_bits // 8
        elif self.target.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            return 16  # RISCV64 uses 128-bit capabilities
        elif self.target.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            return 16
        raise ValueError("Capabilities not supported for " + repr(self))

    @property
    def capability_size_in_bits(self):
        return self.capability_size * 8

    @staticmethod
    def host_c_compiler(config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not OSInfo.IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang"
        return config.clang_path

    @staticmethod
    def host_cxx_compiler(config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not OSInfo.IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang++"
        return config.clang_plusplus_path

    @staticmethod
    def host_c_preprocessor(config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not OSInfo.IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang-cpp"
        return config.clang_cpp_path

    def pkgconfig_candidates(self, prefix: Path) -> "list[str]":
        """:return: a list of potential candidates for pkgconfig .pc files inside prefix"""
        return [str(prefix / self.default_libdir / "pkgconfig"), str(prefix / "share/pkgconfig"),
                str(prefix / "libdata/pkgconfig")]


# https://reviews.llvm.org/rG14daa20be1ad89639ec209d969232d19cf698845
class AutoVarInit(Enum):
    NONE = "none"
    ZERO = "zero"
    PATTERN = "pattern"

    def clang_flags(self) -> "typing.List[str]":
        if self is None:
            return []  # Equivalent to -ftrivial-auto-var-init=uninitialized
        elif self is AutoVarInit.ZERO:
            return ["-ftrivial-auto-var-init=zero",
                    "-enable-trivial-auto-var-init-zero-knowing-it-will-be-removed-from-clang"]
        elif self is AutoVarInit.PATTERN:
            return ["-ftrivial-auto-var-init=pattern"]
        else:
            raise NotImplementedError()


class NativeTargetInfo(TargetInfo):
    shortname = "native"
    os_prefix = ""  # Don't add an extra -native to target names

    @property
    def sdk_root_dir(self):
        raise ValueError("Should not be called for native")

    @property
    def sysroot_dir(self):
        raise ValueError("Should not be called for native")

    @property
    def cmake_system_name(self) -> str:
        raise ValueError("Should not be called for native")

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        raise ValueError("Should not be called for native")

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        if config.use_sdk_clang_for_native_xbuild:
            return ["llvm-native"]
        return []  # use host tools -> no target needed

    def get_target_triple(self, *, include_version: bool) -> str:
        return self.project.get_compiler_info(self.c_compiler).default_target

    @property
    def c_compiler(self) -> Path:
        if self.project.custom_c_compiler is not None:
            return self.project.custom_c_compiler
        return self.host_c_compiler(self.config)

    @property
    def cxx_compiler(self) -> Path:
        if self.project.custom_cxx_compiler is not None:
            return self.project.custom_cxx_compiler
        return self.host_cxx_compiler(self.config)

    @property
    def c_preprocessor(self) -> Path:
        if self.project.custom_c_preprocessor is not None:
            return self.project.custom_c_preprocessor
        return self.host_c_preprocessor(self.config)

    @property
    def linker(self) -> Path:
        # Should rarely be needed
        return self.c_compiler.parent / "ld"

    @property
    def ar(self) -> Path:
        return self.c_compiler.parent / "ar"

    @property
    def ranlib(self) -> Path:
        return self.c_compiler.parent / "ranlib"

    @property
    def nm(self) -> Path:
        return self.c_compiler.parent / "nm"

    @property
    def strip_tool(self) -> Path:
        return self.c_compiler.parent / "strip"

    @classmethod
    def is_freebsd(cls):
        return OSInfo.IS_FREEBSD

    @classmethod
    def is_macos(cls):
        return OSInfo.IS_MAC

    @classmethod
    def is_linux(cls):
        return OSInfo.IS_LINUX

    @classmethod
    def is_native(cls):
        return True

    @property
    def default_libdir(self):
        if OSInfo.is_ubuntu() or OSInfo.is_debian():
            # Ubuntu and Debian default to installing to lib/<triple> directories
            if self.target.is_x86_64():
                return "lib/x86_64-linux-gnu"
            else:
                self.project.warning("Don't know default libdir for", self.target.cpu_architecture)
        if OSInfo.is_suse() and self.pointer_size > 4:
            return "lib64"
        return "lib"

    @property
    def pkgconfig_dirs(self) -> "typing.List[str]":
        # We need to add the bootstrap tools pkgconfig dirs to PKG_CONFIG_PATH to find e.g. libxml2, etc.
        # Note: some packages also install to libdata/pkgconfig or share/pkgconfig
        return self.pkgconfig_candidates(self.config.other_tools_dir)

    def pkgconfig_candidates(self, prefix: Path) -> "list[str]":
        result = super().pkgconfig_candidates(prefix)
        if self.default_libdir != "lib":
            # Also add "lib/pkgconfig" for projects that don't use the default install dirs
            result.append(str(prefix / "lib/pkgconfig"))
        return result

    @classmethod
    def essential_compiler_and_linker_flags_impl(cls, instance: "TargetInfo", *, xtarget: "CrossCompileTarget",
                                                 perform_sanity_checks=True, default_flags_only=False):
        result = []
        if instance.project.auto_var_init != AutoVarInit.NONE:
            compiler = instance.project.get_compiler_info(instance.c_compiler)
            if compiler.is_apple_clang:
                # Not sure which apple clang version is the first to support it but 11.0.3 on my system does
                valid_clang_version = compiler.version >= (11, 0)
            else:
                # Clang 8.0.0 is the first to support auto-var-init
                valid_clang_version = compiler.is_clang and compiler.version >= (8, 0)
            if valid_clang_version:
                result += instance.project.auto_var_init.clang_flags()
            else:
                instance.project.fatal("Requested automatic variable initialization, but don't know how to for",
                                       compiler)
        return result  # default host compiler should not need any extra flags


class Linkage(Enum):
    DEFAULT = "default"
    STATIC = "static"
    DYNAMIC = "dynamic"


class MipsFloatAbi(Enum):
    SOFT = ("mips64", "-msoft-float")
    HARD = ("mips64hf", "-mhard-float")

    def freebsd_target_arch(self):
        return self.value[0]

    def clang_float_flag(self):
        return self.value[1]


class CrossCompileTarget(object):
    # Currently the same for all targets
    DEFAULT_SUBOBJECT_BOUNDS = "conservative"

    def __init__(self, suffix: str, cpu_architecture: CPUArchitecture, target_info_cls: "typing.Type[TargetInfo]",
                 *, is_cheri_purecap=False, is_cheri_hybrid=False, check_conflict_with: "CrossCompileTarget" = None,
                 non_cheri_target: "CrossCompileTarget" = None, hybrid_target: "CrossCompileTarget" = None,
                 purecap_target: "CrossCompileTarget" = None):
        assert not suffix.startswith("-"), suffix
        name_prefix = target_info_cls.shortname
        if target_info_cls.os_prefix is not None:
            self.os_prefix = target_info_cls.os_prefix
        else:
            self.os_prefix = name_prefix.lower() + "-"
        self.name = name_prefix + "-" + suffix
        self.base_suffix = suffix  # Excluding the OS names
        self.generic_suffix = self.os_prefix + suffix
        self.cpu_architecture = cpu_architecture
        # TODO: self.operating_system = ...
        self._is_cheri_purecap = is_cheri_purecap
        self._is_cheri_hybrid = is_cheri_hybrid
        assert not (is_cheri_purecap and is_cheri_hybrid), "Can't be both hybrid and purecap"
        self.check_conflict_with = check_conflict_with  # Check that we don't reuse install-dir, etc for this target
        self.target_info_cls = target_info_cls
        # FIXME: there must be a better way of doing this, but this works for now
        self._hybrid_target = non_cheri_target
        self._purecap_target = hybrid_target
        self._non_cheri_target = purecap_target
        self._set_for(non_cheri_target)
        self._set_for(hybrid_target)
        self._set_for(purecap_target)

    # noinspection PyProtectedMember
    def _set_from(self, other_target: "CrossCompileTarget"):
        if self is other_target:
            return
        if self._hybrid_target is None and other_target._hybrid_target is not None:
            self._hybrid_target = other_target._hybrid_target
            other_target._hybrid_target._set_from(self)
        if self._non_cheri_target is None and other_target._non_cheri_target is not None:
            self._non_cheri_target = other_target._non_cheri_target
            other_target._non_cheri_target._set_from(self)
        if self._purecap_target is None and other_target._purecap_target is not None:
            self._purecap_target = other_target._purecap_target
            other_target._purecap_target._set_from(self)

    # Set the related targets:
    def _set_for(self, other_target: "typing.Optional[CrossCompileTarget]", also_set_other=True):
        if other_target is not None and self is not other_target:
            if self._is_cheri_hybrid:
                assert other_target._hybrid_target is None or other_target._hybrid_target is self, "Already set?"
                other_target._hybrid_target = self
                self._hybrid_target = self
            elif self._is_cheri_purecap:
                assert other_target._purecap_target is None or other_target._purecap_target is self, "Already set?"
                other_target._purecap_target = self
                self._purecap_target = self
            else:
                assert other_target._non_cheri_target is None or other_target._non_cheri_target is self, "Already set?"
                other_target._non_cheri_target = self
                self._non_cheri_target = self
            if also_set_other:
                other_target._set_for(self, also_set_other=False)
            other_target._set_from(self)

    def create_target_info(self, project: "SimpleProject") -> TargetInfo:
        return self.target_info_cls(self, project)

    def build_suffix(self, config: "CheriConfig", *, include_os: bool):
        assert self.target_info_cls is not None
        target_suffix = self.generic_suffix if include_os else self.base_suffix
        result = "-" + target_suffix + self.cheri_config_suffix(config)
        return result

    def cheri_config_suffix(self, config: "CheriConfig"):
        """
        :return: a string such as "-subobject-safe"/"128"/"128-plt" to ensure different build/install dirs for config
        options
        """
        result = ""
        if self.is_hybrid_or_purecap_cheri():
            if config.cheri_cap_table_abi:
                result += "-" + str(config.cheri_cap_table_abi)
            if config.subobject_bounds is not None and config.subobject_bounds != self.DEFAULT_SUBOBJECT_BOUNDS:
                result += "-" + str(config.subobject_bounds)
                # TODO: this suffix should not be added. However, it's useful for me right now...
                if not config.subobject_debug:
                    result += "-subobject-nodebug"
        if self.is_mips(include_purecap=True) and config.mips_float_abi == MipsFloatAbi.HARD:
            result += "-hardfloat"
        if config.cross_target_suffix:
            result += "-" + config.cross_target_suffix
        return result

    def is_native(self):
        """returns true if we building for the curent host"""
        assert self.target_info_cls is not None
        return self.target_info_cls.is_native()

    def _check_arch(self, arch: CPUArchitecture, include_purecap: "typing.Optional[bool]"):
        if self.cpu_architecture is not arch:
            return False
        if include_purecap is None:
            # Check that cases that want to handle both pass an explicit argument
            assert not self._is_cheri_purecap, "Should check purecap cases first"
        if not include_purecap and self._is_cheri_purecap:
            return False
        return True

    # Querying the CPU architecture:
    def is_mips(self, include_purecap: bool = None):
        return self._check_arch(CPUArchitecture.MIPS64, include_purecap)

    def is_riscv(self, include_purecap: bool = None):
        return self._check_arch(CPUArchitecture.RISCV64, include_purecap)

    def is_aarch64(self, include_purecap: bool = None):
        return self._check_arch(CPUArchitecture.AARCH64, include_purecap)

    def is_i386(self, include_purecap: bool = None):
        return self._check_arch(CPUArchitecture.I386, include_purecap)

    def is_x86_64(self, include_purecap: bool = None):
        return self._check_arch(CPUArchitecture.X86_64, include_purecap)

    def is_any_x86(self, include_purecap: bool = None):
        return self.is_i386(include_purecap) or self.is_x86_64(include_purecap)

    def is_cheri_purecap(self, valid_cpu_archs: "typing.List[CPUArchitecture]" = None):
        if valid_cpu_archs is None:
            return self._is_cheri_purecap
        if not self._is_cheri_purecap:
            return False
        # Purecap target, but must first check if one of the accepted architectures matches
        for a in valid_cpu_archs:
            if a is self.cpu_architecture:
                return True
        return False

    def is_cheri_hybrid(self, valid_cpu_archs: "typing.List[CPUArchitecture]" = None):
        if valid_cpu_archs is None:
            return self._is_cheri_hybrid
        if not self._is_cheri_hybrid:
            return False
        # Purecap target, but must first check if one of the accepted architectures matches
        for a in valid_cpu_archs:
            if a is self.cpu_architecture:
                return True
        return False

    def is_hybrid_or_purecap_cheri(self, valid_cpu_archs: "typing.List[CPUArchitecture]" = None):
        return self.is_cheri_purecap(valid_cpu_archs) or self.is_cheri_hybrid(valid_cpu_archs)

    def get_cheri_hybrid_target(self) -> "CrossCompileTarget":
        if self._is_cheri_hybrid:
            return self
        elif self._hybrid_target is not None:
            return self._hybrid_target
        raise ValueError("Don't know CHERI hybrid version of " + repr(self))

    def get_cheri_purecap_target(self) -> "CrossCompileTarget":
        if self._is_cheri_purecap:
            return self
        elif self._purecap_target is not None:
            return self._purecap_target
        raise ValueError("Don't know CHERI purecap version of " + repr(self))

    def get_non_cheri_target(self) -> "CrossCompileTarget":
        if not self._is_cheri_purecap and not self._is_cheri_hybrid:
            return self
        elif self._non_cheri_target is not None:
            return self._non_cheri_target
        raise ValueError("Don't know non-CHERI version of " + repr(self))

    def __repr__(self):
        result = self.target_info_cls.__name__ + "(" + self.cpu_architecture.name
        if self._is_cheri_purecap:
            result += " purecap"
        if self._is_cheri_hybrid:
            result += " hybrid"
        return result + ")"

    # def __eq__(self, other):
    #     raise NotImplementedError("Should not compare to CrossCompileTarget, use the is_foo() methods.")


# This is a separate class to avoid cyclic dependencies.
# The real list is in CompilationTargets in compilation_targets.py
class BasicCompilationTargets:
    # XXX: should probably not harcode x86_64 for native
    NATIVE = CrossCompileTarget("native", CPUArchitecture.X86_64, NativeTargetInfo)
    NATIVE_IF_FREEBSD = [NATIVE] if OSInfo.IS_FREEBSD else []
    NATIVE_IF_LINUX = [NATIVE] if OSInfo.IS_LINUX else []
    NATIVE_IF_MACOS = [NATIVE] if OSInfo.IS_MAC else []
