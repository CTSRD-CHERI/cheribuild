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
from abc import ABCMeta, abstractmethod, ABC
from enum import Enum
from pathlib import Path

from ..utils import IS_MAC, IS_FREEBSD, IS_LINUX, getCompilerInfo, classproperty, is_jenkins_build

if typing.TYPE_CHECKING:    # no-combine
    from .chericonfig import CheriConfig    # no-combine
    from ..projects.project import SimpleProject, Project    # no-combine


class CPUArchitecture(Enum):
    X86_64 = "x86_64"
    MIPS64 = "mips64"
    RISCV64 = "riscv64"
    I386 = "i386"
    AARCH64 = "aarch64"


class TargetInfo(ABC):
    shortname = None

    def __init__(self, target: "CrossCompileTarget", project: "SimpleProject"):
        self.target = target
        self.project = project

    @property
    def cmake_processor_id(self):
        # FIXME: move this to target_info!
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
    def sdk_root_dir(self) -> Path:
        ...

    @property
    @abstractmethod
    def sysroot_dir(self) -> Path:
        ...

    @property
    @abstractmethod
    def target_triple(self) -> str:
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
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        """
        :return: flags such as -target + -mabi which are needed for both compiler and linker
        """
        ...

    @property
    def additional_executable_link_flags(self):
        """Additional linker flags that need to be passed when building an executable (e.g. custom linker script)"""
        return []

    @property
    def additional_shared_library_link_flags(self):
        """Additional linker flags that need to be passed when building an shared library (e.g. custom linker script)"""
        return []

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

    def required_compile_flags(self) -> typing.List[str]:
        """Flags that need to be passed to cc/c++/cpp in all cases"""
        return []

    def required_link_flags(self) -> typing.List[str]:
        """Flags that need to be passed to cc/c++ for linking"""
        return []

    @property
    def pkgconfig_dirs(self) -> str:
        return ""  # whatever the default is

    @property
    def install_prefix_dirname(self):
        """The name of the root directory to install to: i.e. for CheriBSD /usr/local/cheri or /usr/local/mips"""
        if self.target.is_cheri_purecap():
            result = "cheri"
        else:
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

    def get_rootfs_target(self) -> "Project":
        raise RuntimeError("Should not be called for " + self.project.target)

    @classproperty
    def is_baremetal(self):
        return False

    @property
    def is_rtems(self):
        return False

    @property
    def is_newlib(self):
        return False

    @property
    def is_freebsd(self):
        return False

    @property
    def is_cheribsd(self):
        return False

    @property
    def is_macos(self):
        return False

    @property
    def is_linux(self):
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
            return self.config.mips_cheri_bits / 8
        elif self.target.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            return 16  # RISCV64 uses 128-bit capabilities
        raise ValueError("Capabilities not supported for " + repr(self))

    @staticmethod
    def host_c_compiler(config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang"
        return config.clangPath

    @staticmethod
    def host_cxx_compiler(config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang++"
        return config.clangPlusPlusPath

    @staticmethod
    def host_c_preprocessor(config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.cheri_sdk_bindir / "clang-cpp"
        return config.clangCppPath


class _ClangBasedTargetInfo(TargetInfo, metaclass=ABCMeta):
    def __init__(self, target: "CrossCompileTarget", project: "SimpleProject"):
        super().__init__(target, project)
        self._sdk_root_dir = None  # type: Path

    @property
    def _compiler_dir(self) -> Path:
        return self.sdk_root_dir / "bin"

    @property
    def sdk_root_dir(self) -> Path:
        if self._sdk_root_dir is not None:
            return self._sdk_root_dir
        self._sdk_root_dir = self._get_sdk_root_dir_lazy()
        return self._sdk_root_dir

    @abstractmethod
    def _get_sdk_root_dir_lazy(self) -> Path: ...

    @property
    def c_compiler(self) -> Path:
        return self._compiler_dir / "clang"

    @property
    def cxx_compiler(self) -> Path:
        return self._compiler_dir / "clang++"

    @property
    def c_preprocessor(self) -> Path:
        return self._compiler_dir / "clang-cpp"

    @property
    def linker(self) -> Path:
        return self._compiler_dir / "ld.lld"

    @property
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        # However, when cross compiling we need at least -target=
        result = ["-target", self.target_triple, "-pipe"]
        # And usually also --sysroot
        if self.project.needs_sysroot:
            result.append("--sysroot=" + str(self.sysroot_dir))
        result += ["-B" + str(self._compiler_dir)]

        if self.target.is_mips(include_purecap=True):
            result.append("-integrated-as")
            result.append("-G0")  # no small objects in GOT optimization
            # Floating point ABI:
            if self.is_baremetal or self.is_rtems:
                # The baremetal driver doesn't add -fPIC for CHERI
                if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
                    result.append("-fPIC")
                    # For now use soft-float to avoid compiler crashes
                    result.append(MipsFloatAbi.SOFT.clang_float_flag())
                else:
                    # We don't have a softfloat library baremetal so always compile hard-float
                    result.append(MipsFloatAbi.HARD.clang_float_flag())
                    result.append("-fno-pic")
                    result.append("-mno-abicalls")
            else:
                result.append(self.config.mips_float_abi.clang_float_flag())
                # always use libc++
                result.append("-stdlib=libc++")

            # CPU flags (currently always BERI):
            if self.is_cheribsd:
                result.append("-mcpu=beri")
            if self.target.is_cheri_purecap():
                result.extend(["-mabi=purecap", "-mcpu=beri", "-cheri=" + self.config.mips_cheri_bits_str])
                if self.config.subobject_bounds:
                    result.extend(["-Xclang", "-cheri-bounds=" + str(self.config.subobject_bounds)])
                    if self.config.subobject_debug:
                        result.extend(["-mllvm", "-cheri-subobject-bounds-clear-swperm=2"])
                if self.config.cheri_cap_table_abi:
                    result.append("-cheri-cap-table-abi=" + self.config.cheri_cap_table_abi)
            else:
                assert self.target.is_mips(include_purecap=False)
                # TODO: should we use -mcpu=cheri128/256?
                result.extend(["-mabi=n64", "-mcpu=beri"])
                if self.target.is_cheri_hybrid():
                    result.append("-cheri=" + self.config.mips_cheri_bits_str)
                # always use libc++
                result.append("-stdlib=libc++")
        elif self.target.is_riscv(include_purecap=True):
            assert self.target.cpu_architecture == CPUArchitecture.RISCV64
            # Use the insane RISC-V arch string to enable CHERI
            arch_string = "rv64imafdc"
            if self.target.is_hybrid_or_purecap_cheri():
                arch_string += "xcheri"
            result.append("-march=" + arch_string)  # XXX: any more xfoo extensions?
            if self.target.is_cheri_purecap():
                result.append("-mabi=l64pc128d")  # 64-bit double-precision hard-float + purecap
            else:
                result.append("-mabi=lp64d")  # 64-bit double-precision hard-float
            result.append("-mno-relax")  # Linker relaxations are not supported with clang+lld
        else:
            self.project.warning("Compiler flags might be wong, only native + MIPS checked so far")
        return result


class NativeTargetInfo(TargetInfo):
    shortname = "native"

    @property
    def sdk_root_dir(self):
        raise ValueError("Should not be called for native")

    @property
    def sysroot_dir(self):
        raise ValueError("Should not be called for native")

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        raise ValueError("Should not be called for native")

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        if config.use_sdk_clang_for_native_xbuild:
            return ["llvm-native"]
        return []  # use host tools -> no target needed

    @property
    def target_triple(self):
        return getCompilerInfo(self.c_compiler).default_target

    @property
    def c_compiler(self) -> Path:
        return self.host_c_compiler(self.config)

    @property
    def cxx_compiler(self) -> Path:
        return self.host_cxx_compiler(self.config)

    @property
    def linker(self) -> Path:
        # Should rarely be needed
        return self.c_compiler.parent / "ld"

    @property
    def c_preprocessor(self) -> Path:
        return self.host_c_preprocessor(self.config)

    @property
    def is_freebsd(self):
        return IS_FREEBSD

    @property
    def is_macos(self):
        return IS_MAC

    @property
    def is_linux(self):
        return IS_LINUX

    @property
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        return []  # default host compiler should not need any extra flags


class FreeBSDTargetInfo(_ClangBasedTargetInfo):
    shortname = "FreeBSD"
    FREEBSD_VERSION = 13

    def _get_sdk_root_dir_lazy(self):
        from ..projects.llvm import BuildUpstreamLLVM
        return BuildUpstreamLLVM.getInstallDir(self.project, cross_target=CompilationTargets.NATIVE)

    @property
    def sysroot_dir(self):
        return Path(self.sdk_root_dir, "sysroot-freebsd-" + str(self.target.cpu_architecture.value))

    @property
    def is_freebsd(self):
        return True

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["upstream-llvm"]

    @classmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config: "CheriConfig", include_version: bool):
        common_suffix = "-unknown-freebsd"
        if include_version:
            common_suffix += str(cls.FREEBSD_VERSION)
        # TODO: do we need any special cases here?
        return target.cpu_architecture.value + common_suffix

    @property
    def target_triple(self):
        return self.triple_for_target(self.target, self.config, include_version=True)

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["freebsd"]

    @property
    def pkgconfig_dirs(self) -> str:
        return str(self.sysroot_dir / "usr/local/lib/pkgconfig")

    def get_rootfs_target(self) -> "Project":
        from ..projects.cross.cheribsd import BuildFreeBSD
        return BuildFreeBSD.get_instance(self.project)


class CheriBSDTargetInfo(FreeBSDTargetInfo):
    shortname = "CheriBSD"
    FREEBSD_VERSION = 13

    def _get_sdk_root_dir_lazy(self):
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self):
        if is_jenkins_build():
            # TODO: currently we need this to be unprefixed since that is what the archives created by jenkins look like
            return self.config.cheri_sdk_dir / "sysroot"
        return self.get_cheribsd_sysroot_path()

    def get_cheribsd_sysroot_path(self, separate_cheri_sysroots=False) -> Path:
        """
        :param cross_compile_target: The target we want the sysroot dir for
        :param separate_cheri_sysroots: If true will use a separate sysroot dir for purecap and hybrid sysroots. The
        default behaviour is to use the hybrid sysroot for both purecap and hybrid applications.
        :return: The sysroot path
        """
        config = self.config
        if self.target.is_mips(include_purecap=True):
            return self._sysroot_path(config.cheri_sdk_dir, separate_cheri_sysroots,
                purecap_prefix="-purecap", hybrid_prefix="", nocheri_name="-mips")
        elif self.target.is_riscv(include_purecap=True):
            return self._sysroot_path(config.cheri_sdk_dir, separate_cheri_sysroots,
                purecap_prefix="-riscv64c", hybrid_prefix="-riscv64c-hybrid", nocheri_name="-riscv64")
        elif self.target.is_x86_64():
            return config.cheri_sdk_dir / "sysroot-x86_64"
        else:
            assert False, "Invalid cross_compile_target: " + str(self.target)

    def _sysroot_path(self, root_dir: Path, separate_cheri_sysroots: bool, *, purecap_prefix: str,
                      hybrid_prefix: str, nocheri_name: str):
        if self.target.is_cheri_hybrid() or (self.target.is_cheri_purecap() and not separate_cheri_sysroots):
            return root_dir / ("sysroot" + hybrid_prefix + self.target.cheri_config_suffix(self.config))
        elif self.target.is_cheri_purecap():
            assert separate_cheri_sysroots, "Logic error?"
            return root_dir / ("sysroot" + purecap_prefix + self.target.cheri_config_suffix(self.config))
        assert not self.target.is_hybrid_or_purecap_cheri()
        return root_dir / ("sysroot" + nocheri_name)

    @property
    def is_cheribsd(self):
        return True

    @classmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config: "CheriConfig", include_version):
        if target.is_cheri_purecap():
            # anything over 10 should use libc++ by default
            if target.is_mips(include_purecap=True):
                return "mips64c{}-unknown-freebsd{}-purecap".format(config.mips_cheri_bits,
                    cls.FREEBSD_VERSION if include_version else "")
            elif target.is_riscv(include_purecap=True):
                return "riscv64-unknown-freebsd{}".format(cls.FREEBSD_VERSION if include_version else "")
            else:
                assert False, "Unsuported purecap target" + str(cls)
        return super().triple_for_target(target, config, include_version)

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm-native", "qemu", "gdb-native"]

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        # Purecap (currently) builds against the hybrid sysroot:
        if target.is_cheri_purecap():
            if target.is_mips(include_purecap=True):
                return ["cheribsd-cheri"]
            elif target.is_riscv(include_purecap=True):
                return ["cheribsd-riscv64-hybrid"]
            else:
                assert False, "Logic error"
        # Otherwise pick the matching sysroot
        return ["cheribsd"]

    @property
    def local_install_root(self) -> Path:
        return self.sysroot_dir / "usr/local" / self.install_prefix_dirname

    @property
    def pkgconfig_dirs(self) -> str:
        if self.target.is_cheri_purecap():
            return str(self.sysroot_dir / "usr/libcheri/pkgconfig") + ":" + \
                   str(self.local_install_root / "lib/pkgconfig") + ":" + \
                   str(self.local_install_root / "libcheri/pkgconfig")
        return str(self.sysroot_dir / "usr/lib/pkgconfig") + ":" + str(self.local_install_root / "lib/pkgconfig")

    def get_rootfs_target(self) -> "Project":
        from ..projects.cross.cheribsd import BuildCHERIBSD
        xtarget = CompilationTargets.NONE
        # Install the purecap targets into the hybrid rootfs:
        if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            xtarget = CompilationTargets.CHERIBSD_MIPS_HYBRID
        elif self.target.is_cheri_purecap([CPUArchitecture.RISCV64]):
            xtarget = CompilationTargets.CHERIBSD_RISCV_HYBRID
        return BuildCHERIBSD.get_instance(self.project, cross_target=xtarget)


class NewlibBaremetalTargetInfo(_ClangBasedTargetInfo):
    shortname = "Newlib"

    def _get_sdk_root_dir_lazy(self) -> Path:
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self) -> Path:
        # Install to mips/cheri128/cheri256 directory
        if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            suffix = "cheri" + self.config.mips_cheri_bits_str
        else:
            suffix = self.target.generic_suffix
        return self.config.cheri_sdk_dir / "baremetal" / suffix / self.target_triple

    @property
    def must_link_statically(self):
        return True  # only static linking works

    @property
    def _compiler_dir(self) -> Path:
        # TODO: BuildUpstreamLLVM.installDir?
        return self.config.cheri_sdk_bindir

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm-native", "qemu", "gdb-native"]  # upstream-llvm??

    @property
    def target_triple(self):
        if self.target.is_mips(include_purecap=True):
            if self.target.is_cheri_purecap():
                return "mips64c{}-qemu-elf-purecap".format(self.config.mips_cheri_bits)
            return "mips64-qemu-elf"
        if self.target.is_riscv(include_purecap=True):
            if self.target.is_cheri_purecap():
                return "riscv64-none-none-purecap"
            return "riscv64-none-none"
        assert False, "Other baremetal cases have not been tested yet!"

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["newlib", "compiler-rt-builtins"]

    def required_compile_flags(self) -> typing.List[str]:
        # Currently we need these flags to build anything against newlib baremetal
        return [
            "-D_GNU_SOURCE=1",  # needed for the locale functions
            "-D_POSIX_TIMERS=1", "-D_POSIX_MONOTONIC_CLOCK=1",  # pretend that we have a monotonic clock
            ]

    @property
    def additional_executable_link_flags(self):
        """Additional linker flags that need to be passed when building an executable (e.g. custom linker script)"""
        return ["-Wl,-T,qemu-malta.ld"]

    @property
    def is_baremetal(self):
        return True

    @property
    def is_newlib(self):
        return True

    def get_rootfs_target(self) -> "Project":
        from ..projects.cross.newlib import BuildNewlib
        return BuildNewlib.get_instance(self.project)


class NewlibRtemsTargetInfo(_ClangBasedTargetInfo):
    shortname = "Newlib RTEMS"

    def _get_sdk_root_dir_lazy(self) -> Path:
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self) -> Path:
        # Install to target triple as RTEMS' LLVM/Clang Driver expects
        return self.config.cheri_sdk_dir

    @property
    def must_link_statically(self):
        return True  # only static linking works

    @property
    def _compiler_dir(self) -> Path:
        return self.config.cheri_sdk_bindir

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm-native"]

    @property
    def target_triple(self):
        return "riscv64-unknown-rtems5"

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["newlib", "compiler-rt-builtins"]

    def required_compile_flags(self) -> typing.List[str]:
        return [""]

    @property
    def local_install_root(self) -> Path:
        return self.config.cheri_sdk_dir

    @property
    def additional_executable_link_flags(self):
        """Additional linker flags that need to be passed when building an executable (e.g. custom linker script)"""
        return [""]

    @property
    def is_baremetal(self):
        return False

    @property
    def is_rtems(self):
        return True

    @property
    def is_newlib(self):
        return True


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
    DEFAULT_CAP_TABLE_ABI = "pcrel"
    DEFAULT_SUBOBJECT_BOUNDS = "conservative"

    def __init__(self, suffix: str, cpu_architecture: CPUArchitecture, target_info_cls: "typing.Type[TargetInfo]", *,
                 is_cheri_purecap=False, is_cheri_hybrid=False, check_conflict_with: "CrossCompileTarget" = None):
        if target_info_cls is None:
            self.name = suffix
        else:
            assert not suffix.startswith("-"), suffix
            self.name = target_info_cls.shortname + "-" + suffix
        self.generic_suffix = suffix
        self.cpu_architecture = cpu_architecture
        # TODO: self.operating_system = ...
        self._is_cheri_purecap = is_cheri_purecap
        self._is_cheri_hybrid = is_cheri_hybrid
        assert not (is_cheri_purecap and is_cheri_hybrid), "Can't be both hybrid and purecap"
        self.check_conflict_with = check_conflict_with  # Check that we don't reuse install-dir, etc for this target
        self.target_info_cls = target_info_cls

    def create_target_info(self, project: "SimpleProject") -> TargetInfo:
        return self.target_info_cls(self, project)

    def build_suffix(self, config: "CheriConfig"):
        assert self is not CompilationTargets.NONE
        if self is CompilationTargets.CHERIBSD_MIPS_PURECAP:
            result = ""  # only -128/-256 for legacy build dir compat
        else:
            result = "-" + self.generic_suffix
        result += self.cheri_config_suffix(config)
        return result

    def cheri_config_suffix(self, config: "CheriConfig"):
        """
        :return: a string such as "-subobject-safe"/"128"/"256-plt" to ensure different build/install dirs for config options
        """
        result = ""
        if self.is_hybrid_or_purecap_cheri([CPUArchitecture.MIPS64]):
            # MIPS supports 128/256 -> include that in the configuration
            result += config.mips_cheri_bits_str
        if self.is_cheri_purecap():
            if config.cheri_cap_table_abi != self.DEFAULT_CAP_TABLE_ABI:
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
        return self is CompilationTargets.NATIVE

    def _check_arch(self, arch: CPUArchitecture, include_purecap: bool):
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

    def __repr__(self):
        result = self.target_info_cls.__name__ + "(" + self.cpu_architecture.name
        if self._is_cheri_purecap:
            result += " purecap"
        if self._is_cheri_hybrid:
            result += " hybrid"
        return result + ")"

    # def __eq__(self, other):
    #     raise NotImplementedError("Should not compare to CrossCompileTarget, use the is_foo() methods.")


class CompilationTargets(object):
    NONE = CrossCompileTarget("invalid", None, None)  # Placeholder

    # XXX: should probably not harcode x86_64 for native
    NATIVE = CrossCompileTarget("native", CPUArchitecture.X86_64, NativeTargetInfo)

    CHERIBSD_MIPS_NO_CHERI = CrossCompileTarget("mips-nocheri", CPUArchitecture.MIPS64, CheriBSDTargetInfo)
    CHERIBSD_MIPS_HYBRID = CrossCompileTarget("mips-hybrid", CPUArchitecture.MIPS64, CheriBSDTargetInfo,
        is_cheri_hybrid=True, check_conflict_with=CHERIBSD_MIPS_NO_CHERI)
    CHERIBSD_MIPS_PURECAP = CrossCompileTarget("cheri", CPUArchitecture.MIPS64, CheriBSDTargetInfo,
        is_cheri_purecap=True, check_conflict_with=CHERIBSD_MIPS_NO_CHERI)

    CHERIBSD_RISCV_NO_CHERI = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, CheriBSDTargetInfo)
    CHERIBSD_RISCV_HYBRID = CrossCompileTarget("riscv64-hybrid", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
        is_cheri_hybrid=True, check_conflict_with=CHERIBSD_RISCV_NO_CHERI)
    CHERIBSD_RISCV_PURECAP = CrossCompileTarget("riscv64-purecap", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
        is_cheri_purecap=True, check_conflict_with=CHERIBSD_RISCV_HYBRID)
    CHERIBSD_X86_64 = CrossCompileTarget("native", CPUArchitecture.X86_64, CheriBSDTargetInfo)

    # Baremetal targets
    BAREMETAL_NEWLIB_MIPS64 = CrossCompileTarget("baremetal-mips", CPUArchitecture.MIPS64, NewlibBaremetalTargetInfo)
    BAREMETAL_NEWLIB_MIPS64_PURECAP = CrossCompileTarget("baremetal-mips-purecap", CPUArchitecture.MIPS64,
        NewlibBaremetalTargetInfo, is_cheri_purecap=True, check_conflict_with=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV64 = CrossCompileTarget("baremetal-riscv64", CPUArchitecture.RISCV64,
        NewlibBaremetalTargetInfo, check_conflict_with=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV64_PURECAP = CrossCompileTarget("baremetal-riscv64-purecap", CPUArchitecture.RISCV64,
        NewlibBaremetalTargetInfo, is_cheri_purecap=True, check_conflict_with=BAREMETAL_NEWLIB_RISCV64)
    # FreeBSD targets
    FREEBSD_MIPS = CrossCompileTarget("mips", CPUArchitecture.MIPS64, FreeBSDTargetInfo)
    FREEBSD_RISCV = CrossCompileTarget("riscv", CPUArchitecture.RISCV64, FreeBSDTargetInfo)
    FREEBSD_I386 = CrossCompileTarget("i386", CPUArchitecture.I386, FreeBSDTargetInfo)
    FREEBSD_AARCH64 = CrossCompileTarget("aarch64", CPUArchitecture.AARCH64, FreeBSDTargetInfo)
    FREEBSD_X86_64 = CrossCompileTarget("x86_64", CPUArchitecture.X86_64, FreeBSDTargetInfo)

    # RTEMS targets
    RTEMS_NEWLIB_RISCV64 = CrossCompileTarget("rtems-riscv64-purecap", CPUArchitecture.RISCV64,
        NewlibRtemsTargetInfo, is_cheri_purecap=True)

    # TODO: test RISCV
    ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS = [CHERIBSD_MIPS_PURECAP, CHERIBSD_MIPS_HYBRID, CHERIBSD_MIPS_NO_CHERI,
                                               CHERIBSD_RISCV_PURECAP, CHERIBSD_RISCV_HYBRID, CHERIBSD_RISCV_NO_CHERI,
                                               NATIVE]
    ALL_CHERIBSD_MIPS_AND_RISCV_TARGETS = [CHERIBSD_MIPS_HYBRID, CHERIBSD_MIPS_NO_CHERI, CHERIBSD_MIPS_PURECAP,
                                           CHERIBSD_RISCV_PURECAP, CHERIBSD_RISCV_HYBRID,CHERIBSD_RISCV_NO_CHERI]
    ALL_SUPPORTED_BAREMETAL_TARGETS = [BAREMETAL_NEWLIB_MIPS64, BAREMETAL_NEWLIB_MIPS64_PURECAP,
                                       BAREMETAL_NEWLIB_RISCV64]
    ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS = ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS + \
                                                            ALL_SUPPORTED_BAREMETAL_TARGETS
