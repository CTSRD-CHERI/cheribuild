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

from ..utils import IS_MAC, IS_FREEBSD, IS_LINUX, getCompilerInfo

if typing.TYPE_CHECKING:
    from .chericonfig import CheriConfig
    from ..projects.project import SimpleProject


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
    @abstractmethod
    def sdk_root_dir(self) -> Path: ...

    @property
    @abstractmethod
    def sysroot_dir(self) -> Path: ...

    @property
    @abstractmethod
    def target_triple(self) -> str: ...

    @property
    @abstractmethod
    def c_compiler(self) -> Path: ...

    @property
    @abstractmethod
    def cxx_compiler(self) -> Path: ...

    @property
    @abstractmethod
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        """
        :return: flags such as -target + -mabi which are needed for both compiler and linker
        """
        ...

    @property
    @abstractmethod
    def c_preprocessor(self) -> Path: ...

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
    def config(self) -> "CheriConfig":
        return self.project.config

    @property
    def is_baremetal(self):
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
        if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            assert self.config.cheriBits in (128, 256), "No other cap size supported yet"
            return self.config.cheriBits / 8
        assert not self.target.is_cheri_purecap(), "RISC-V not handled yet"
        if self.target.is_i386():
            return 4
        # all other architectures we support currently use 64-bit pointers
        return 8


class _ClangBasedTargetInfo(TargetInfo, metaclass=ABCMeta):
    @property
    @abstractmethod
    def _compiler_dir(self) -> Path: ...

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
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        # However, when cross compiling we need at least -target=
        result = ["-target", self.target_triple]
        # And usually also --sysroot
        if self.project.needs_sysroot:
            result.append("--sysroot=" + str(self.sysroot_dir))
        result += ["-B" + str(self._compiler_dir)]

        if self.target.is_mips(include_purecap=True):
            # Floating point ABI:
            if self.is_baremetal:
                # The baremetal driver doesn't add -fPIC for CHERI
                if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
                    result.append("-fPIC")
                    # For now use soft-float to avoid compiler crashes
                    result.append(MipsFloatAbi.SOFT.clang_float_flag())
                else:
                    # We don't have a softfloat library baremetal so always compile hard-float
                    result.append(MipsFloatAbi.HARD.clang_float_flag())
            else:
                result.append(self.config.mips_float_abi.clang_float_flag())

            # CPU flags (currently always BERI):
            result.append("-mcpu=beri")
            if self.target.is_cheri_purecap():
                result.extend(["-mabi=purecap", "-mcpu=beri", "-cheri=" + self.config.cheriBitsStr])
                if self.config.subobject_bounds:
                    result.extend(["-Xclang", "-cheri-bounds=" + str(self.config.subobject_bounds)])
                    if self.config.subobject_debug:
                        result.extend(["-mllvm", "-cheri-subobject-bounds-clear-swperm=2"])
            else:
                assert self.target.is_mips(include_purecap=False)
                # TODO: should we use -mcpu=cheri128/256?
                result.extend(["-mabi=n64", "-mcpu=beri"])
                if self.project.mips_build_hybrid:
                    result.append("-cheri=" + self.config.cheriBitsStr)
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
            return ["llvm"]
        return []  # use host tools -> no target needed

    @property
    def target_triple(self):
        return getCompilerInfo(self.c_compiler).default_target

    @property
    def c_compiler(self) -> Path:
        if self.config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return self.config.cheri_sdk_bindir / "clang"
        return self.config.clangPath

    @property
    def cxx_compiler(self) -> Path:
        if self.config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return self.config.cheri_sdk_bindir / "clang++"
        return self.config.clangPlusPlusPath

    @property
    def c_preprocessor(self) -> Path:
        if self.config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return self.config.cheri_sdk_bindir / "clang-cpp"
        return self.config.clangCppPath

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

    @property
    def sdk_root_dir(self):
        # FIXME: different SDK root dir?
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self):
        return Path(self.sdk_root_dir, "sysroot-freebsd-" + str(self.target.cpu_architecture.value))

    @property
    def is_freebsd(self):
        return True

    @property
    def _compiler_dir(self) -> Path:
        # TODO: BuildLLVM.installDir?
        return self.sdk_root_dir / "bin"

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm"]  # TODO: upstream-llvm???

    @property
    def target_triple(self):
        common_suffix = "-unknown-freebsd" + str(self.FREEBSD_VERSION)
        # TODO: do we need any special cases here?
        return self.target.cpu_architecture.value + common_suffix

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["freebsd"]


class CheriBSDTargetInfo(FreeBSDTargetInfo):
    shortname = "CheriBSD"
    FREEBSD_VERSION = 13

    @property
    def sdk_root_dir(self):
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self):
        return self.config.get_cheribsd_sysroot_path(self.target, use_hybrid_sysroot=self.project.mips_build_hybrid)

    @property
    def is_cheribsd(self):
        return True

    @property
    def target_triple(self):
        if self.target.is_cheri_purecap():
            # anything over 10 should use libc++ by default
            assert self.target.is_mips(include_purecap=True), "Only MIPS purecap is supported"
            return "mips64c{}-unknown-freebsd{}-purecap".format(self.config.cheriBits, self.FREEBSD_VERSION)
        return super().target_triple

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm", "qemu", "gdb-native"]

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        if target.is_mips(include_purecap=False):
            if config.use_hybrid_sysroot_for_mips:
                return ["cheribsd-cheri", "cheribsd-sysroot-cheri"]
            return ["cheribsd-mips", "cheribsd-sysroot-mips"]
        return ["cheribsd", "cheribsd-sysroot"]


class NewlibBaremetalTargetInfo(_ClangBasedTargetInfo):
    shortname = "Newlib"
    @property
    def sdk_root_dir(self) -> Path:
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self) -> Path:
        # Install to mips/cheri128/cheri256 directory
        if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            suffix = "cheri" + self.config.cheriBitsStr
        else:
            suffix = self.target.generic_suffix
        return self.config.cheri_sdk_dir / "baremetal" / suffix / self.target_triple

    @property
    def _compiler_dir(self) -> Path:
        # TODO: BuildUpstreamLLVM.installDir?
        return self.config.cheri_sdk_bindir

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm", "qemu", "gdb-native"]  # upstream-llvm??

    @property
    def target_triple(self):
        if self.target.is_mips(include_purecap=True):
            if self.target.is_cheri_purecap():
                return "mips64c{}-qemu-elf-purecap".format(self.config.cheriBits)
            return "mips64-qemu-elf"
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
    def is_baremetal(self):
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
    def __init__(self, suffix: str, cpu_architecture: CPUArchitecture, is_cheri_purecap: bool,
                 target_info_cls: "typing.Type[TargetInfo]", check_conflict_with: "CrossCompileTarget" = None):
        if target_info_cls is None:
            self.name = suffix
        else:
            self.name = target_info_cls.shortname + suffix
        self.generic_suffix = suffix
        self.cpu_architecture = cpu_architecture
        # TODO: self.operating_system = ...
        self._is_cheri_purecap = is_cheri_purecap
        self.check_conflict_with = check_conflict_with  # Check that we don't reuse install-dir, etc for this target
        self.target_info_cls = target_info_cls

    def create_target_info(self, project: "SimpleProject") -> TargetInfo:
        return self.target_info_cls(self, project)

    def build_suffix(self, config: "CheriConfig", *, build_hybrid=False):
        assert self is not CrossCompileTarget.NONE
        if self is CrossCompileTarget.CHERIBSD_MIPS_PURECAP:
            result = ""  # only -128/-256 for legacy build dir compat
        elif self is CrossCompileTarget.CHERIBSD_MIPS:
            result = "-" + self.generic_suffix
            if build_hybrid:
                result += "-hybrid" + config.cheri_bits_and_abi_str
            if config.mips_float_abi == MipsFloatAbi.HARD:
                result += "-hardfloat"
        else:
            result = "-" + self.generic_suffix

        if self._is_cheri_purecap:
            result += "-" + config.cheri_bits_and_abi_str
        if config.cross_target_suffix:
            result += "-" + config.cross_target_suffix
        return result

    def is_native(self):
        """returns true if we building for the curent host"""
        return self is CrossCompileTarget.NATIVE

    # Querying the CPU architecture:
    def is_mips(self, include_purecap: bool = None):
        if include_purecap is None:
            # Check that cases that want to handle both pass an explicit argument
            assert self is not CrossCompileTarget.CHERIBSD_MIPS_PURECAP, "Should check purecap cases first"
        if not include_purecap and self._is_cheri_purecap:
            return False
        return self.cpu_architecture is CPUArchitecture.MIPS64

    def is_riscv(self, include_purecap: bool = None):
        return self.cpu_architecture is CPUArchitecture.RISCV64

    def is_aarch64(self):
        return self.cpu_architecture is CPUArchitecture.AARCH64

    def is_i386(self):
        return self.cpu_architecture is CPUArchitecture.I386

    def is_x86_64(self):
        return self.cpu_architecture is CPUArchitecture.X86_64

    def is_any_x86(self):
        return self.is_i386() or self.is_x86_64()

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

    # def __eq__(self, other):
    #     raise NotImplementedError("Should not compare to CrossCompileTarget, use the is_foo() methods.")


CrossCompileTarget.NONE = CrossCompileTarget("invalid", None, False, None)  # Placeholder
# XXX: should probably not harcode x86_64
CrossCompileTarget.NATIVE = CrossCompileTarget("native", CPUArchitecture.X86_64, False, NativeTargetInfo)
# CheriBSD targets
CrossCompileTarget.CHERIBSD_MIPS = CrossCompileTarget("mips", CPUArchitecture.MIPS64, False, CheriBSDTargetInfo)
CrossCompileTarget.CHERIBSD_MIPS_PURECAP = CrossCompileTarget("cheri", CPUArchitecture.MIPS64, True, CheriBSDTargetInfo,
                                                              CrossCompileTarget.CHERIBSD_MIPS)
CrossCompileTarget.CHERIBSD_RISCV = CrossCompileTarget("riscv", CPUArchitecture.RISCV64, False, CheriBSDTargetInfo)
CrossCompileTarget.CHERIBSD_X86_64 = CrossCompileTarget("native", CPUArchitecture.X86_64, False, CheriBSDTargetInfo)
# Baremetal targets
CrossCompileTarget.BAREMETAL_NEWLIB_MIPS64 = CrossCompileTarget("baremetal-mips", CPUArchitecture.MIPS64, False,
                                                                NewlibBaremetalTargetInfo)
CrossCompileTarget.BAREMETAL_NEWLIB_MIPS64_PURECAP = CrossCompileTarget("baremetal-purecap-mips",
                                                                        CPUArchitecture.MIPS64, True,
                                                                        NewlibBaremetalTargetInfo,
                                                                        CrossCompileTarget.BAREMETAL_NEWLIB_MIPS64)
# FreeBSD targets
CrossCompileTarget.FREEBSD_MIPS = CrossCompileTarget("mips", CPUArchitecture.MIPS64, False, FreeBSDTargetInfo)
CrossCompileTarget.FREEBSD_RISCV = CrossCompileTarget("riscv", CPUArchitecture.RISCV64, False, FreeBSDTargetInfo)
CrossCompileTarget.FREEBSD_I386 = CrossCompileTarget("i386", CPUArchitecture.I386, False, FreeBSDTargetInfo)
CrossCompileTarget.FREEBSD_AARCH64 = CrossCompileTarget("aarch64", CPUArchitecture.AARCH64, False, FreeBSDTargetInfo)
CrossCompileTarget.FREEBSD_X86_64 = CrossCompileTarget("x86_64", CPUArchitecture.X86_64, False, FreeBSDTargetInfo)
