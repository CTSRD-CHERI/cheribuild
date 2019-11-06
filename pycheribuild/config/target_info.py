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
from pathlib import Path
from enum import Enum

from ..utils import IS_MAC, IS_FREEBSD, IS_LINUX, getCompilerInfo

if typing.TYPE_CHECKING:
    from .chericonfig import CheriConfig, CrossCompileTarget
    from ..projects.project import SimpleProject


class CPUArchitecture(Enum):
    X86_64 = "x86_64"
    MIPS64 = "mips64"
    RISCV64 = "riscv64"
    I386 = "i386"
    AARCH64 = "aarch64"


class TargetInfo(ABC):
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


class _ClangBasedTargetInfo(TargetInfo, metaclass=ABCMeta):
    @property
    @abstractmethod
    def compiler_dir(self) -> Path: ...

    @property
    def c_compiler(self) -> Path:
        return self.compiler_dir / "clang"

    @property
    def cxx_compiler(self) -> Path:
        return self.compiler_dir / "clang++"

    @property
    def c_preprocessor(self) -> Path:
        return self.compiler_dir / "clang-cpp"


class NativeTargetInfo(TargetInfo):
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
        return getCompilerInfo(self.c_compiler()).default_target

    @property
    def c_compiler(self) -> Path:
        if self.project.config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return self.project.config.sdkBinDir / "clang"
        return self.project.config.clangPath

    @property
    def cxx_compiler(self) -> Path:
        if self.project.config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return self.project.config.sdkBinDir / "clang++"
        return self.project.config.clangPlusPlusPath

    @property
    def c_preprocessor(self) -> Path:
        if self.project.config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return self.project.config.sdkBinDir / "clang-cpp"
        return self.project.config.clangCppPath

    @property
    def is_freebsd(self):
        return IS_FREEBSD

    @property
    def is_macos(self):
        return IS_MAC

    @property
    def is_linux(self):
        return IS_LINUX


class FreeBSDTargetInfo(_ClangBasedTargetInfo):
    FREEBSD_VERSION = 13

    @property
    def sdk_root_dir(self):
        # FIXME: different SDK root dir?
        return self.project.config.sdkDir

    @property
    def sysroot_dir(self):
        return Path(self.sdk_root_dir, "sysroot-freebsd-" + self.target.cpu_architecture.value)

    @property
    def is_freebsd(self):
        return True

    @property
    def compiler_dir(self) -> Path:
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
    FREEBSD_VERSION = 13

    @property
    def sdk_root_dir(self):
        return self.project.config.sdkDir

    @property
    def sysroot_dir(self):
        return self.project.config.get_sysroot_path(self.target, use_hybrid_sysroot=self.project.mips_build_hybrid)

    @property
    def is_cheribsd(self):
        return True

    @property
    def target_triple(self):
        if self.target.is_cheri_purecap():
            # anything over 10 should use libc++ by default
            assert self.target.is_mips(include_purecap=True), "Only MIPS purecap is supported"
            return "mips64c{}-unknown-freebsd{}-purecap".format(config.cheriBits, self.FREEBSD_VERSION)
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
    @property
    def sdk_root_dir(self) -> Path:
        return self.project.config.sdkDir

    @property
    def sysroot_dir(self) -> Path:
        # Install to mips/cheri128/cheri256 directory
        if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            suffix = "cheri" + self.project.config.cheriBitsStr
        else:
            suffix = self.target.generic_suffix
        return self.project.config.sdkDir / "baremetal" / suffix / self.target_triple

    @property
    def compiler_dir(self) -> Path:
        # TODO: BuildUpstreamLLVM.installDir?
        return self.project.config.sdkBinDir

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm", "qemu", "gdb-native"]  # upstream-llvm??

    @property
    def target_triple(self):
        if self.target.is_mips(include_purecap=True):
            if self.target.is_cheri_purecap():
                return "mips64c{}-qemu-elf-purecap".format(self.project.config.cheriBits)
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
