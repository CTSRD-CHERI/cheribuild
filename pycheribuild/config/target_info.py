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
import functools
import platform
import re
import typing
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path
from typing import ClassVar, Optional

from .chericonfig import AArch64FloatSimdOptions, CheriConfig, MipsFloatAbi
from ..filesystemutils import FileSystemUtils
from ..processutils import CompilerInfo, get_compiler_info
from ..utils import OSInfo, cached_property, fatal_error, final, status_update, warning_message

__all__ = [
    "AArch64FloatSimdOptions",
    "AbstractProject",
    "AutoVarInit",
    "BasicCompilationTargets",
    "CPUArchitecture",
    "CompilerType",
    "CrossCompileTarget",
    "DefaultInstallDir",
    "MipsFloatAbi",
    "NativeTargetInfo",
    "TargetInfo",
    "sys_param_h_cheribsd_version",
    "cheribsd_morello_version_dependent_flags",
]


class CPUArchitecture(Enum):
    AARCH64 = "aarch64"
    ARM32 = "arm32"
    I386 = "i386"
    MIPS64 = "mips64"
    RISCV32 = "riscv32"
    RISCV64 = "riscv64"
    X86_64 = "x86_64"

    def word_bits(self) -> int:
        mapping = {
            CPUArchitecture.AARCH64: 64,
            CPUArchitecture.ARM32: 32,
            CPUArchitecture.I386: 32,
            CPUArchitecture.MIPS64: 64,
            CPUArchitecture.RISCV32: 32,
            CPUArchitecture.RISCV64: 64,
            CPUArchitecture.X86_64: 64,
        }
        return mapping[self]

    def is_32bit(self) -> bool:
        return self.word_bits() == 32

    def is_64bit(self) -> bool:
        return self.word_bits() == 64

    def as_meson_cpu_family(self) -> str:
        # https://mesonbuild.com/Reference-tables.html#cpu-families
        if self is CPUArchitecture.I386:
            return "x86"
        if self is CPUArchitecture.ARM32:
            return "arm"
        # All others match the Meson table
        return str(self.value)

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


# https://reviews.llvm.org/rG14daa20be1ad89639ec209d969232d19cf698845
class AutoVarInit(Enum):
    NONE = "none"
    ZERO = "zero"
    PATTERN = "pattern"

    def clang_flags(self) -> "list[str]":
        if self is None:
            return []  # Equivalent to -ftrivial-auto-var-init=uninitialized
        elif self is AutoVarInit.ZERO:
            return [
                "-ftrivial-auto-var-init=zero",
                "-enable-trivial-auto-var-init-zero-knowing-it-will-be-removed-from-clang",
            ]
        elif self is AutoVarInit.PATTERN:
            return ["-ftrivial-auto-var-init=pattern"]
        else:
            raise NotImplementedError()


class DefaultInstallDir(Enum):
    DO_NOT_INSTALL = "Should not be installed"
    IN_BUILD_DIRECTORY = "$BUILD_DIR/test-install-prefix"
    # Note: ROOTFS_LOCALBASE will be searched for libraries, ROOTFS_OPTBASE will not. The former should be used for
    # libraries that will be used by other programs, and the latter should be used for standalone programs (such as
    # PostgreSQL or WebKit).
    # Note: for ROOTFS_OPTBASE, the path_in_rootfs attribute can be used to override the default of /opt/...
    # This also works for ROOTFS_LOCALBASE
    ROOTFS_OPTBASE = "The rootfs for this target (<rootfs>/opt/<arch>/<program> by default)"
    ROOTFS_LOCALBASE = "The sysroot for this target (<rootfs>/usr/local/<arch> by default)"
    KDE_PREFIX = "The sysroot for this target (<rootfs>/opt/<arch>/kde by default)"
    CHERI_SDK = "The CHERI SDK directory"
    MORELLO_SDK = "The Morello SDK directory"
    BOOTSTRAP_TOOLS = "The bootstap tools directory"
    CUSTOM_INSTALL_DIR = "Custom install directory"
    SYSROOT_FOR_BAREMETAL_ROOTFS_OTHERWISE = "Sysroot for baremetal projects, rootfs otherwise"


_INVALID_INSTALL_DIR: Path = Path("/this/dir/should/be/overwritten/and/not/used/!!!!")
_DO_NOT_INSTALL_PATH: Path = Path("/this/project/should/not/be/installed!!!!")


class AbstractProject(FileSystemUtils):
    """A base class for (Simple)Project that exposes only the fields/methods needed in target_info."""

    _xtarget: "ClassVar[Optional[CrossCompileTarget]]" = None
    default_architecture: "ClassVar[Optional[CrossCompileTarget]]"
    needs_sysroot: "ClassVar[bool]"

    auto_var_init: AutoVarInit  # Needed for essential_compiler_flags
    config: CheriConfig
    crosscompile_target: "CrossCompileTarget"
    target: str

    # Allow overrides for libc++/llvm-test-suite
    custom_c_preprocessor: Optional[Path] = None
    custom_c_compiler: Optional[Path] = None
    custom_cxx_compiler: Optional[Path] = None

    def __init__(self, config):
        super().__init__(config)
        self._setup_called = False
        self._init_called = False

    def get_compiler_info(self, compiler: Path) -> CompilerInfo:
        return get_compiler_info(compiler, config=self.config)

    def info(self, *args, **kwargs) -> None:
        # TODO: move all those methods here
        if not self.config.quiet:
            status_update(*args, **kwargs)

    @staticmethod
    def warning(*args, **kwargs) -> None:
        warning_message(*args, **kwargs)

    def fatal(self, *args, sep=" ", fixit_hint=None, fatal_when_pretending=False) -> None:
        fatal_error(
            *args,
            sep=sep,
            fixit_hint=fixit_hint,
            fatal_when_pretending=fatal_when_pretending,
            pretend=self.config.pretend,
        )

    @classmethod
    def get_crosscompile_target(cls) -> "CrossCompileTarget":
        target = cls._xtarget
        assert target is not None
        return target

    @classmethod
    def get_instance(
        cls: "type[_AnyProject]",
        caller: "Optional[AbstractProject]",
        config: "Optional[CheriConfig]" = None,
        cross_target: "Optional[CrossCompileTarget]" = None,
    ) -> "_AnyProject":
        raise NotImplementedError()

    @classmethod
    def get_install_dir(cls, caller: "AbstractProject", cross_target: "Optional[CrossCompileTarget]" = None) -> Path:
        raise NotImplementedError()


_AnyProject = typing.TypeVar("_AnyProject", bound=AbstractProject)


class TargetInfo(ABC):
    shortname: str = "INVALID"
    # os_prefix defaults to shortname.lower() if not set
    os_prefix: Optional[str] = None

    def __init__(self, target: "CrossCompileTarget", project: AbstractProject) -> None:
        self.target = target
        self.project = project

    @property
    def cmake_processor_id(self) -> str:
        if self.target.is_mips(include_purecap=True):
            if self.target.is_cheri_purecap():
                return f"CHERI (MIPS IV compatible) with {self.config.mips_cheri_bits_str}-bit capabilities"
            else:
                return "BERI (MIPS IV compatible)"
        if self.target.is_aarch64(include_purecap=True):
            return "ARM64"
        return str(self.target.cpu_architecture.value)

    @property
    @abstractmethod
    def cmake_system_name(self) -> str: ...

    @property
    def toolchain_system_version(self) -> "Optional[str]":
        return None

    def cmake_prefix_paths(self, config: "CheriConfig") -> "list[Path]":
        """List of additional directories to be searched for packages (e.g. sysroot/usr/local/riscv64-purecap)"""
        return []

    def cmake_extra_toolchain_file_code(self) -> str:
        return ""

    @property
    @abstractmethod
    def sdk_root_dir(self) -> Path: ...

    @property
    @abstractmethod
    def sysroot_dir(self) -> Path: ...

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
    def get_target_triple(self, *, include_version: bool) -> str: ...

    @property
    @abstractmethod
    def c_compiler(self) -> Path: ...

    @property
    @abstractmethod
    def cxx_compiler(self) -> Path: ...

    @property
    @abstractmethod
    def linker(self) -> Path: ...

    @property
    @abstractmethod
    def ar(self) -> Path: ...

    @property
    @abstractmethod
    def ranlib(self) -> Path: ...

    @property
    @abstractmethod
    def nm(self) -> Path: ...

    @property
    @abstractmethod
    def strip_tool(self) -> Path: ...

    @classmethod
    @abstractmethod
    def essential_compiler_and_linker_flags_impl(
        cls,
        instance: "TargetInfo",
        *,
        xtarget: "CrossCompileTarget",
        perform_sanity_checks=True,
        default_flags_only=False,
        softfloat: Optional[bool] = None,
    ) -> "list[str]":
        """
        :return: flags such as -target + -mabi which are needed for both compiler and linker
        """
        ...

    def get_essential_compiler_and_linker_flags(
        self,
        xtarget: "Optional[CrossCompileTarget]" = None,
        perform_sanity_checks=True,
        default_flags_only=False,
        softfloat: Optional[bool] = None,
    ) -> "list[str]":
        return self.essential_compiler_and_linker_flags_impl(
            self,
            perform_sanity_checks=perform_sanity_checks,
            xtarget=xtarget if xtarget is not None else self.target,
            default_flags_only=default_flags_only,
            softfloat=softfloat,
        )

    @property
    def additional_executable_link_flags(self) -> "list[str]":
        """Additional linker flags that need to be passed when building an executable (e.g. custom linker script)"""
        return []

    @property
    def additional_shared_library_link_flags(self) -> "list[str]":
        """Additional linker flags that need to be passed when building an shared library (e.g. custom linker script)"""
        return []

    @property
    def default_libdir(self) -> str:
        return "lib"

    @property
    def localbase(self) -> Path:
        """Relative path from the root to LOCALBASE (usr/local on FreeBSD)"""
        raise RuntimeError("Should only be called for FreeBSD targets")

    def default_install_dir(self, install_dir: DefaultInstallDir) -> Path:
        if install_dir == DefaultInstallDir.DO_NOT_INSTALL:
            return _DO_NOT_INSTALL_PATH
        elif install_dir == DefaultInstallDir.IN_BUILD_DIRECTORY:
            # noinspection PyUnresolvedReferences
            return self.project.build_dir / "test-install-prefix"  # pytype: disable=attribute-error
        elif install_dir == DefaultInstallDir.CUSTOM_INSTALL_DIR:
            return _INVALID_INSTALL_DIR
        raise NotImplementedError(f"Unsupported {install_dir} for {self}")

    @property
    @abstractmethod
    def c_preprocessor(self) -> Path: ...

    @classmethod
    @abstractmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: CheriConfig) -> "list[str]":
        """returns e.g. [llvm]/[upstream-llvm], or an empty list"""
        ...

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: CheriConfig) -> "list[str]":
        """returns a list of targets that need to be built for a minimal sysroot"""
        return []

    def default_initial_compile_flags(self) -> "list[str]":
        """Flags that need to be passed to cc/c++/cpp in all cases"""
        return []

    # noinspection PyMethodMayBeStatic
    def required_link_flags(self) -> "list[str]":
        """Flags that need to be passed to cc/c++ for linking"""
        return []

    @property
    def pkgconfig_dirs(self) -> "list[str]":
        return []  # whatever the default is

    @property
    def pkg_config_libdir_override(self) -> "Optional[str]":
        return None

    @property
    def install_prefix_dirname(self) -> str:
        """The name of the root directory to install to: i.e. for CheriBSD /usr/local/mips64-purecap or
        /usr/local/riscv64-hybrid"""
        result = self.target.generic_arch_suffix
        if self.config.cross_target_suffix:
            result += "-" + self.config.cross_target_suffix
        return result

    @property
    def config(self) -> CheriConfig:
        return self.project.config

    @property
    def must_link_statically(self) -> bool:
        """E.g. for baremetal target infos we have to link statically (and add the -static linker flag)"""
        return False

    @final
    def get_rootfs_project(
        self, *, t: "type[_AnyProject]", caller: AbstractProject, xtarget: "Optional[CrossCompileTarget]" = None
    ) -> _AnyProject:
        if xtarget is None:
            xtarget = self.target
        xtarget = xtarget.get_rootfs_target()
        result = self._get_rootfs_class(xtarget)
        assert issubclass(result, t)
        return result.get_instance(caller=caller, cross_target=xtarget, config=self.config)

    def _get_rootfs_class(self, xtarget: "CrossCompileTarget") -> "type[AbstractProject]":
        raise LookupError("Should not be called for " + self.project.target)

    @classmethod
    def is_native(cls) -> bool:
        return False

    @classmethod
    def is_baremetal(cls) -> bool:
        return False

    @classmethod
    def is_rtems(cls) -> bool:
        return False

    @classmethod
    def is_newlib(cls) -> bool:
        return False

    @classmethod
    def is_freebsd(cls) -> bool:
        return False

    @classmethod
    def is_cheribsd(cls) -> bool:
        return False

    def run_cheribsd_test_script(
        self,
        script_name,
        *script_args,
        kernel_path=None,
        disk_image_path=None,
        mount_builddir=True,
        mount_sourcedir=False,
        mount_sysroot=False,
        use_full_disk_image=False,
        mount_installdir=False,
        use_benchmark_kernel_by_default=False,
        rootfs_alternate_kernel_dir=None,
    ) -> None:
        raise ValueError("run_cheribsd_test_script only supports CheriBSD targets")

    @classmethod
    def is_macos(cls) -> bool:
        return False

    @classmethod
    def is_linux(cls) -> bool:
        return False

    @property
    def pointer_size(self) -> int:
        if self.target.is_cheri_purecap():
            return self.capability_size
        if self.target.is_i386():
            return 4
        # all other architectures we support currently use 64-bit pointers
        return 8

    @property
    def capability_size(self) -> int:
        if self.target.is_hybrid_or_purecap_cheri([CPUArchitecture.MIPS64]):
            assert self.config.mips_cheri_bits in (128, 256), "No other cap size supported yet"
            return self.config.mips_cheri_bits // 8
        elif self.target.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV32]):
            return 8
        elif self.target.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            return 16
        elif self.target.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            return 16
        raise ValueError("Capabilities not supported for " + repr(self))

    @property
    def capability_size_in_bits(self) -> int:
        return self.capability_size * 8

    @staticmethod
    def host_c_compiler(config: CheriConfig) -> Path:
        if config.use_sdk_clang_for_native_xbuild and not OSInfo.IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang"
        return config.clang_path

    @staticmethod
    def host_cxx_compiler(config: CheriConfig) -> Path:
        if config.use_sdk_clang_for_native_xbuild and not OSInfo.IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang++"
        return config.clang_plusplus_path

    @staticmethod
    def host_c_preprocessor(config: CheriConfig) -> Path:
        if config.use_sdk_clang_for_native_xbuild and not OSInfo.IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang-cpp"
        return config.clang_cpp_path

    def pkgconfig_candidates(self, prefix: Path) -> "list[str]":
        """:return: a list of potential candidates for pkgconfig .pc files inside prefix"""
        return [
            str(prefix / self.default_libdir / "pkgconfig"),
            str(prefix / "share/pkgconfig"),
            str(prefix / "libdata/pkgconfig"),
        ]


class NativeTargetInfo(TargetInfo):
    shortname: str = "native"
    os_prefix: str = ""  # Don't add an extra -native to target names

    @property
    def sdk_root_dir(self) -> Path:
        raise ValueError("Should not be called for native")

    @property
    def sysroot_dir(self) -> Path:
        raise ValueError("Should not be called for native")

    @property
    def cmake_system_name(self) -> str:
        raise ValueError("Should not be called for native")

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: CheriConfig) -> "list[str]":
        raise ValueError("Should not be called for native")

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: CheriConfig) -> "list[str]":
        if config.use_sdk_clang_for_native_xbuild:
            return ["llvm-native"]
        return []  # use host tools -> no target needed

    def get_target_triple(self, *, include_version: bool) -> str:
        return self.project.get_compiler_info(self.c_compiler).default_target

    def default_install_dir(self, install_dir: DefaultInstallDir) -> Path:
        config = self.config
        if install_dir == DefaultInstallDir.ROOTFS_OPTBASE:
            raise ValueError("Should not use DefaultInstallDir.ROOTFS_OPTBASE for native builds!")
        elif install_dir == DefaultInstallDir.KDE_PREFIX:
            if self._is_libcompat_target:
                return config.output_root / ("kde-compat" + self._compat_abi_suffix)
            return self.config.output_root / "kde"
        elif install_dir == DefaultInstallDir.ROOTFS_LOCALBASE:
            if self._is_libcompat_target:
                return config.output_root / ("local" + self._compat_abi_suffix)
            return config.output_root / "local"
        elif install_dir == DefaultInstallDir.CHERI_SDK:
            return config.cheri_sdk_dir
        elif install_dir == DefaultInstallDir.MORELLO_SDK:
            return config.morello_sdk_dir
        elif install_dir == DefaultInstallDir.BOOTSTRAP_TOOLS:
            return config.other_tools_dir
        return super().default_install_dir(install_dir)

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
    def is_freebsd(cls) -> bool:
        return OSInfo.IS_FREEBSD

    @classmethod
    def is_cheribsd(cls) -> bool:
        return OSInfo.is_cheribsd()

    @classmethod
    def is_macos(cls) -> bool:
        return OSInfo.IS_MAC

    @classmethod
    def is_linux(cls) -> bool:
        return OSInfo.IS_LINUX

    @classmethod
    def is_native(cls) -> bool:
        return True

    @property
    def default_libdir(self) -> str:
        if OSInfo.is_ubuntu() or OSInfo.is_debian():
            # Ubuntu and Debian default to installing to lib/<triple> directories
            if self.target.is_x86_64():
                return "lib/x86_64-linux-gnu"
            else:
                self.project.warning("Don't know default libdir for", self.target.cpu_architecture)
        if OSInfo.is_suse() and self.pointer_size > 4:
            return "lib64"
        return "lib"

    @cached_property
    def _is_libcompat_target(self) -> bool:
        return _is_native_purecap() and not self.target.is_cheri_purecap()

    @cached_property
    def _compat_abi_suffix(self) -> str:
        assert self.is_freebsd()
        # Directory suffix for compat ABI (currently only "64"/"" should be valid)
        if _is_native_purecap() and not self.target.is_cheri_purecap():
            return "64"
        assert (
            _is_native_purecap() == self.target.is_cheri_purecap()
        ), "Building purecap natively is only supported on purecap installations"
        return ""

    @property
    def localbase(self) -> Path:
        if self.is_freebsd():
            # Use /usr/local64 for hybrid/non-CHERI targets on purecap CheriBSD.
            return Path(f"/usr/local{self._compat_abi_suffix}")
        raise NotImplementedError("Should only be called for FreeBSD targets")

    @cached_property
    def pkg_config_libdir_override(self) -> "Optional[str]":
        if OSInfo.is_cheribsd():
            # When building natively on CheriBSD with pkg-config installed using pkg64, the default pkg-config
            # search path will use the non-CHERI libraries in /usr/local64. We could avoid this override in cases
            # where the pkg-config localbase matches the current localbase, but always overriding it is simpler (and
            # also handles cases such as a self-built pkg-config).
            if self._compat_abi_suffix:
                return f"{self.localbase}/libdata/pkgconfig:/usr/lib{self._compat_abi_suffix}/pkgconfig"
            return "/usr/local/libdata/pkgconfig:/usr/libdata/pkgconfig"
        return None  # use the default value for non-CheriBSD

    @property
    def pkgconfig_dirs(self) -> "list[str]":
        # We need to add the bootstrap tools pkgconfig dirs to PKG_CONFIG_PATH to find e.g. libxml2, etc.
        # Note: some packages also install to libdata/pkgconfig or share/pkgconfig
        # NB: We don't want to look in this directory when building forced hybrid targets such as GDB:
        if _is_native_purecap() and not self.target.is_cheri_purecap():
            return []
        return self.pkgconfig_candidates(self.config.other_tools_dir)

    def cmake_prefix_paths(self, config: "CheriConfig") -> "list[Path]":
        return [config.other_tools_dir]

    def pkgconfig_candidates(self, prefix: Path) -> "list[str]":
        result = super().pkgconfig_candidates(prefix)
        if self.default_libdir != "lib":
            # Also add "lib/pkgconfig" for projects that don't use the default install dirs
            result.append(str(prefix / "lib/pkgconfig"))
        return result

    @classmethod
    def essential_compiler_and_linker_flags_impl(
        cls,
        instance: "TargetInfo",
        *,
        xtarget: "CrossCompileTarget",
        perform_sanity_checks=True,
        default_flags_only=False,
        softfloat: Optional[bool] = None,
    ) -> "list[str]":
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
                instance.project.fatal(
                    "Requested automatic variable initialization, but don't know how to for", compiler
                )
        if cls.is_cheribsd():
            if xtarget.is_aarch64(include_purecap=True):
                cheribsd_version = sys_param_h_cheribsd_version(Path("/"))
                result.extend(cheribsd_morello_version_dependent_flags(cheribsd_version, xtarget.is_cheri_purecap()))
                if xtarget.is_cheri_purecap():
                    result.append("-mabi=purecap")
                else:
                    assert xtarget.is_cheri_hybrid(), "non-cheri not supported"
                    result.append("-mabi=aapcs")  # in case cc defaults to -mabi=purecap
                result.append("-mcpu=rainier")
            else:
                instance.project.fatal("Native CheriBSD compilation currently only supported for Morello targets")
        return result  # default host compiler should not need any extra flags


class CrossCompileTarget:
    # Currently the same for all targets
    DEFAULT_SUBOBJECT_BOUNDS: str = "conservative"

    def __init__(
        self,
        arch_suffix: str,
        cpu_architecture: CPUArchitecture,
        target_info_cls: "type[TargetInfo]",
        *,
        is_cheri_purecap=False,
        is_cheri_hybrid=False,
        extra_target_suffix: str = "",
        check_conflict_with: "Optional[CrossCompileTarget]" = None,
        rootfs_target: "Optional[CrossCompileTarget]" = None,
        non_cheri_target: "Optional[CrossCompileTarget]" = None,
        hybrid_target: "Optional[CrossCompileTarget]" = None,
        purecap_target: "Optional[CrossCompileTarget]" = None,
        non_cheri_for_hybrid_rootfs_target: "Optional[CrossCompileTarget]" = None,
        non_cheri_for_purecap_rootfs_target: "Optional[CrossCompileTarget]" = None,
        hybrid_for_purecap_rootfs_target: "Optional[CrossCompileTarget]" = None,
        purecap_for_hybrid_rootfs_target: "Optional[CrossCompileTarget]" = None,
    ) -> None:
        assert not arch_suffix.startswith("-"), arch_suffix
        assert not extra_target_suffix or extra_target_suffix.startswith("-"), extra_target_suffix
        name_prefix = target_info_cls.shortname
        if target_info_cls.os_prefix is not None:
            self.os_prefix = target_info_cls.os_prefix
        else:
            self.os_prefix = name_prefix.lower() + "-"
        self.name = name_prefix + "-" + arch_suffix + extra_target_suffix

        self.base_arch_suffix = arch_suffix  # Excluding the OS names
        self.generic_arch_suffix = self.os_prefix + self.base_arch_suffix
        self.base_target_suffix = self.base_arch_suffix + extra_target_suffix
        self.generic_target_suffix = self.generic_arch_suffix + extra_target_suffix

        self.cpu_architecture = cpu_architecture
        # TODO: self.operating_system = ...
        self._is_cheri_purecap = is_cheri_purecap
        self._is_cheri_hybrid = is_cheri_hybrid
        assert not (is_cheri_purecap and is_cheri_hybrid), "Can't be both hybrid and purecap"
        self.check_conflict_with = check_conflict_with  # Check that we don't reuse install-dir, etc for this target
        self._rootfs_target = rootfs_target
        self.target_info_cls = target_info_cls
        # FIXME: there must be a better way of doing this, but this works for now
        self._non_cheri_target = non_cheri_target
        self._hybrid_target = hybrid_target
        self._purecap_target = purecap_target
        self._non_cheri_for_hybrid_rootfs_target = non_cheri_for_hybrid_rootfs_target
        self._non_cheri_for_purecap_rootfs_target = non_cheri_for_purecap_rootfs_target
        self._hybrid_for_purecap_rootfs_target = hybrid_for_purecap_rootfs_target
        self._purecap_for_hybrid_rootfs_target = purecap_for_hybrid_rootfs_target
        if typing.TYPE_CHECKING:
            # Inferring what these function calls do takes a very long time in pytype, and since they don't add any
            # instance variables (or change the type of them) we can skip over these calls.
            return
        self._set_for(non_cheri_target)
        self._set_for(hybrid_target)
        self._set_for(purecap_target)
        self._set_for(non_cheri_for_hybrid_rootfs_target)
        self._set_for(non_cheri_for_purecap_rootfs_target)
        self._set_for(hybrid_for_purecap_rootfs_target)
        self._set_for(purecap_for_hybrid_rootfs_target)

    def _set_from(self, other_target: "CrossCompileTarget") -> None:
        if self is other_target:
            return
        for attr in (
            "_hybrid_target",
            "_non_cheri_target",
            "_purecap_target",
            "_non_cheri_for_hybrid_rootfs_target",
            "_non_cheri_for_purecap_rootfs_target",
            "_hybrid_for_purecap_rootfs_target",
            "_purecap_for_hybrid_rootfs_target",
        ):
            if getattr(self, attr) is None and getattr(other_target, attr) is not None:
                setattr(self, attr, getattr(other_target, attr))
                # noinspection PyProtectedMember
                getattr(other_target, attr)._set_from(self)

    # Set the related targets:
    def _set_for(self, other_target: "Optional[CrossCompileTarget]", also_set_other=True) -> None:
        if other_target is not None and self is not other_target:
            if self._is_cheri_hybrid:
                if self._rootfs_target is not None:
                    assert (
                        self._rootfs_target._is_cheri_purecap
                    ), "Only support purecap separate rootfs for hybrid targets"
                    assert (
                        other_target._hybrid_for_purecap_rootfs_target is None
                        or other_target._hybrid_for_purecap_rootfs_target is self
                    ), "Already set?"
                    other_target._hybrid_for_purecap_rootfs_target = self
                    self._hybrid_for_purecap_rootfs_target = self
                else:
                    assert other_target._hybrid_target is None or other_target._hybrid_target is self, "Already set?"
                    other_target._hybrid_target = self
                    self._hybrid_target = self
            elif self._is_cheri_purecap:
                if self._rootfs_target is not None:
                    assert (
                        self._rootfs_target._is_cheri_hybrid
                    ), "Only support hybrid separate rootfs for purecap targets"
                    assert (
                        other_target._purecap_for_hybrid_rootfs_target is None
                        or other_target._purecap_for_hybrid_rootfs_target is self
                    ), "Already set?"
                    other_target._purecap_for_hybrid_rootfs_target = self
                    self._purecap_for_hybrid_rootfs_target = self
                else:
                    assert other_target._purecap_target is None or other_target._purecap_target is self, "Already set?"
                    other_target._purecap_target = self
                    self._purecap_target = self
            else:
                if self._rootfs_target is not None:
                    if self._rootfs_target._is_cheri_hybrid:
                        assert (
                            other_target._non_cheri_for_hybrid_rootfs_target is None
                            or other_target._non_cheri_for_hybrid_rootfs_target is self
                        ), "Already set?"
                        other_target._non_cheri_for_hybrid_rootfs_target = self
                        self._non_cheri_for_hybrid_rootfs_target = self
                    else:
                        assert self._rootfs_target._is_cheri_purecap, "Separate non-CHERI rootfs for non-CHERI target?"
                        assert (
                            other_target._non_cheri_for_purecap_rootfs_target is None
                            or other_target._non_cheri_for_purecap_rootfs_target is self
                        ), "Already set?"
                        other_target._non_cheri_for_purecap_rootfs_target = self
                        self._non_cheri_for_purecap_rootfs_target = self
                else:
                    assert self._rootfs_target is None, "Separate rootfs targets only supported for CHERI targets"
                    assert (
                        other_target._non_cheri_target is None or other_target._non_cheri_target is self
                    ), "Already set?"
                    other_target._non_cheri_target = self
                    self._non_cheri_target = self
            if also_set_other:
                other_target._set_for(self, also_set_other=False)
            other_target._set_from(self)

    def create_target_info(self, project: AbstractProject) -> TargetInfo:
        return self.target_info_cls(self, project)

    def build_suffix(self, config: CheriConfig, *, include_os: bool) -> str:
        assert self.target_info_cls is not None
        target_suffix = self.generic_target_suffix if include_os else self.base_target_suffix
        result = "-" + target_suffix + self.cheri_config_suffix(config)
        return result

    def cheri_config_suffix(self, config: CheriConfig) -> str:
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
        if self.is_aarch64(include_purecap=True):
            if config.aarch64_fp_and_simd_options != AArch64FloatSimdOptions.DEFAULT:
                result += config.aarch64_fp_and_simd_options.config_suffix()
        if config.cross_target_suffix:
            result += "-" + config.cross_target_suffix
        return result

    def is_native(self) -> bool:
        """returns true if we are building for the curent host"""
        assert self.target_info_cls is not None
        return self.target_info_cls.is_native()

    def is_native_hybrid(self) -> bool:
        return self.is_native() and self._is_cheri_hybrid

    def _check_arch(self, arch: CPUArchitecture, include_purecap: "Optional[bool]") -> bool:
        if self.cpu_architecture is not arch:
            return False
        if include_purecap is None:
            # Check that cases that want to handle both pass an explicit argument
            assert not self._is_cheri_purecap, "Should check purecap cases first"
        if not include_purecap and self._is_cheri_purecap:
            return False
        return True

    # Querying the CPU architecture:
    def is_mips(self, include_purecap: Optional[bool] = None) -> bool:
        return self._check_arch(CPUArchitecture.MIPS64, include_purecap)

    def is_riscv32(self, include_purecap: Optional[bool] = None) -> bool:
        return self._check_arch(CPUArchitecture.RISCV32, include_purecap)

    def is_riscv64(self, include_purecap: Optional[bool] = None) -> bool:
        return self._check_arch(CPUArchitecture.RISCV64, include_purecap)

    def is_riscv(self, include_purecap: Optional[bool] = None) -> bool:
        return self.is_riscv32(include_purecap) or self.is_riscv64(include_purecap)

    def is_aarch64(self, include_purecap: Optional[bool] = None) -> bool:
        return self._check_arch(CPUArchitecture.AARCH64, include_purecap)

    def is_i386(self, include_purecap: Optional[bool] = None) -> bool:
        return self._check_arch(CPUArchitecture.I386, include_purecap)

    def is_x86_64(self, include_purecap: Optional[bool] = None) -> bool:
        return self._check_arch(CPUArchitecture.X86_64, include_purecap)

    def is_any_x86(self, include_purecap: Optional[bool] = None) -> bool:
        return self.is_i386(include_purecap) or self.is_x86_64(include_purecap)

    def is_cheri_purecap(self, valid_cpu_archs: "Optional[list[CPUArchitecture]]" = None) -> bool:
        if valid_cpu_archs is None:
            return self._is_cheri_purecap
        if not self._is_cheri_purecap:
            return False
        # Purecap target, but must first check if one of the accepted architectures matches
        for a in valid_cpu_archs:
            if a is self.cpu_architecture:
                return True
        return False

    def is_cheri_hybrid(self, valid_cpu_archs: "Optional[list[CPUArchitecture]]" = None) -> bool:
        if valid_cpu_archs is None:
            return self._is_cheri_hybrid
        if not self._is_cheri_hybrid:
            return False
        # Purecap target, but must first check if one of the accepted architectures matches
        for a in valid_cpu_archs:
            if a is self.cpu_architecture:
                return True
        return False

    def is_hybrid_or_purecap_cheri(self, valid_cpu_archs: "Optional[list[CPUArchitecture]]" = None) -> bool:
        return self.is_cheri_purecap(valid_cpu_archs) or self.is_cheri_hybrid(valid_cpu_archs)

    def get_rootfs_target(self) -> "CrossCompileTarget":
        if self._rootfs_target is not None:
            return self._rootfs_target
        return self

    def is_libcompat_target(self) -> bool:
        return self._rootfs_target is not None

    def get_cheri_hybrid_target(self) -> "CrossCompileTarget":
        if self._is_cheri_hybrid and self._rootfs_target is None:
            return self
        elif self._hybrid_target is not None:
            return self._hybrid_target
        raise ValueError("Don't know CHERI hybrid version of " + repr(self))

    def get_cheri_purecap_target(self) -> "CrossCompileTarget":
        if self._is_cheri_purecap and self._rootfs_target is None:
            return self
        elif self._purecap_target is not None:
            return self._purecap_target
        raise ValueError("Don't know CHERI purecap version of " + repr(self))

    def get_non_cheri_target(self) -> "CrossCompileTarget":
        if not self._is_cheri_purecap and not self._is_cheri_hybrid and self._rootfs_target is None:
            return self
        elif self._non_cheri_target is not None:
            return self._non_cheri_target
        raise ValueError("Don't know non-CHERI version of " + repr(self))

    def get_non_cheri_for_hybrid_rootfs_target(self) -> "CrossCompileTarget":
        if (
            not self._is_cheri_purecap
            and not self._is_cheri_hybrid
            and self._rootfs_target is not None
            and self._rootfs_target._is_cheri_hybrid
        ):
            return self
        elif self._non_cheri_for_hybrid_rootfs_target is not None:
            return self._non_cheri_for_hybrid_rootfs_target
        raise ValueError("Don't know non-CHERI for hybrid rootfs version of " + repr(self))

    def get_non_cheri_for_purecap_rootfs_target(self) -> "CrossCompileTarget":
        if (
            not self._is_cheri_purecap
            and not self._is_cheri_hybrid
            and self._rootfs_target is not None
            and self._rootfs_target._is_cheri_purecap
        ):
            return self
        elif self._non_cheri_for_purecap_rootfs_target is not None:
            return self._non_cheri_for_purecap_rootfs_target
        raise ValueError("Don't know non-CHERI for purecap rootfs version of " + repr(self))

    def get_cheri_hybrid_for_purecap_rootfs_target(self) -> "CrossCompileTarget":
        if self._is_cheri_hybrid and self._rootfs_target is not None and self._rootfs_target._is_cheri_purecap:
            return self
        elif self._hybrid_for_purecap_rootfs_target is not None:
            return self._hybrid_for_purecap_rootfs_target
        raise ValueError("Don't know CHERI hybrid for purecap rootfs version of " + repr(self))

    def get_cheri_purecap_for_hybrid_rootfs_target(self) -> "CrossCompileTarget":
        if self._is_cheri_purecap and self._rootfs_target is not None and self._rootfs_target._is_cheri_hybrid:
            return self
        elif self._purecap_for_hybrid_rootfs_target is not None:
            return self._purecap_for_hybrid_rootfs_target
        raise ValueError("Don't know CHERI purecap for hybrid rootfs version of " + repr(self))

    def __repr__(self) -> str:
        result = self.target_info_cls.__name__ + "(" + self.cpu_architecture.name
        if self._is_cheri_purecap:
            result += " purecap"
        if self._is_cheri_hybrid:
            result += " hybrid"
        result += ")"
        if self._rootfs_target is not None:
            result += " for "
            result += repr(self._rootfs_target)
            result += " rootfs"
        return result

    def _dump_target_relations(self) -> None:
        self_repr = repr(self)
        for n in (
            "non_cheri",
            "hybrid",
            "purecap",
            "non_cheri_for_hybrid_rootfs",
            "non_cheri_for_purecap_rootfs",
            "hybrid_for_purecap_rootfs",
            "purecap_for_hybrid_rootfs",
        ):
            k = "_" + n + "_target"
            v = self.__dict__[k]
            print(self_repr + "." + n + ": " + repr(v))

    # def __eq__(self, other):
    #     raise NotImplementedError("Should not compare to CrossCompileTarget, use the is_foo() methods.")


@functools.lru_cache(maxsize=1)
def _native_cpu_arch() -> CPUArchitecture:
    machine = platform.machine()
    if machine in ("amd64", "x86_64"):
        return CPUArchitecture.X86_64
    elif machine in ("arm64", "aarch64"):
        return CPUArchitecture.AARCH64
    else:
        warning_message("Could not infer native CPU architecture from platform.machine()==", machine)
        # Just pretend we are targeting x86, to avoid a fatal error
        return CPUArchitecture.X86_64


@functools.lru_cache(maxsize=1)
def _is_native_purecap():
    # TODO: should we check if `cc -E -dM -xc /dev/null` contains __CHERI_PURE_CAPABILITY__ instead?
    return OSInfo.is_cheribsd() and platform.processor() in ("aarch64c", "riscv64c")


@functools.lru_cache(maxsize=3)
def sys_param_h_cheribsd_version(sysroot: Path) -> "Optional[int]":
    pattern = re.compile(r"#define\s+__CheriBSD_version\s+([0-9]+)")
    try:
        with open(sysroot / "usr/include/sys/param.h", encoding="utf-8") as f:
            for line in f:
                match = pattern.match(line)
                if match:
                    return int(match.groups()[0])
    except FileNotFoundError:
        return None
    return 0


def cheribsd_morello_version_dependent_flags(cheribsd_version: "Optional[int]", is_purecap) -> "list[str]":
    result = []
    # NB: If version is None, no CheriBSD tree exists, so we assume the new
    # ABI will be used when CheriBSD is eventually built. This ensures the
    # LLVM config files for the SDK utilities get the right flags in the
    # common case as otherwise there is a circular dependency.
    if cheribsd_version is None or cheribsd_version >= 20220511:
        # Use new var-args ABI
        result.extend(["-Xclang", "-morello-vararg=new"])
    if cheribsd_version is None or cheribsd_version >= 20230804:
        # Use new function call ABI
        result.extend(["-Xclang", "-morello-bounded-memargs=caller-only"])
    if is_purecap and cheribsd_version is not None and cheribsd_version < 20220511:
        # Use emulated TLS on older purecap
        result.append("-femulated-tls")
    return result


# This is a separate class to avoid cyclic dependencies.
# The real list is in CompilationTargets in compilation_targets.py
class BasicCompilationTargets:
    # Some projects (LLVM, QEMU, GDB, etc.) don't build as purecap binaries, so we have to build them hybrid instead.
    if _is_native_purecap():
        NATIVE = CrossCompileTarget("native", _native_cpu_arch(), NativeTargetInfo, is_cheri_purecap=True)
        NATIVE_HYBRID = CrossCompileTarget(
            "native-hybrid",
            _native_cpu_arch(),
            NativeTargetInfo,
            is_cheri_hybrid=True,
            purecap_target=NATIVE,
            check_conflict_with=NATIVE,
        )
        NATIVE_NON_PURECAP = NATIVE_HYBRID
        ALL_NATIVE = (NATIVE, NATIVE_HYBRID)
    else:
        NATIVE = CrossCompileTarget("native", _native_cpu_arch(), NativeTargetInfo)
        NATIVE_NON_PURECAP = NATIVE
        ALL_NATIVE = (NATIVE,)
    NATIVE_IF_FREEBSD = ALL_NATIVE if OSInfo.IS_FREEBSD else tuple()
    NATIVE_IF_LINUX = ALL_NATIVE if OSInfo.IS_LINUX else tuple()
    NATIVE_IF_MACOS = ALL_NATIVE if OSInfo.IS_MAC else tuple()
