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
from ..utils import IS_MAC, IS_FREEBSD, IS_LINUX, getCompilerInfo

if typing.TYPE_CHECKING:
    from .chericonfig import CheriConfig, CrossCompileTarget


class TargetOperatingSystemInfo(object):
    pass


class TargetInfo(ABC):
    def __init__(self, target: "CrossCompileTarget"):
        self.target = target

    @abstractmethod
    def compiler_target(self, config: "CheriConfig") -> typing.List[str]:
        """returns e.g. [llvm]/[upstream-llvm], or an empty list"""
        ...

    @abstractmethod
    def target_triple(self, config: "CheriConfig"): ...

    @abstractmethod
    def c_compiler(self, config: "CheriConfig") -> Path: ...

    @abstractmethod
    def cxx_compiler(self, config: "CheriConfig") -> Path: ...

    @abstractmethod
    def c_preprocessor(self, config: "CheriConfig") -> Path: ...


class _ClangBasedTargetInfo(TargetInfo, metaclass=ABCMeta):
    @abstractmethod
    def compiler_dir(self, config: "CheriConfig") -> Path: ...

    def c_compiler(self, config: "CheriConfig") -> Path:
        return self.compiler_dir(config) / "clang"

    def cxx_compiler(self, config: "CheriConfig") -> Path:
        return self.compiler_dir(config) / "clang++"

    def c_preprocessor(self, config: "CheriConfig") -> Path:
        return self.compiler_dir(config) / "clang-cpp"


class NativeTargetInfo(TargetInfo):
    def target_triple(self, config: "CheriConfig"):
        return getCompilerInfo(self.c_compiler(config)).default_target

    def compiler_target(self, config: "CheriConfig"):
        if config.use_sdk_clang_for_native_xbuild:
            return ["llvm"]
        return []  # use host tools -> no target needed

    def c_compiler(self, config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.sdkBinDir / "clang"
        return config.clangPath

    def cxx_compiler(self, config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.sdkBinDir / "clang++"
        return config.clangPlusPlusPath

    def c_preprocessor(self, config: "CheriConfig") -> Path:
        if config.use_sdk_clang_for_native_xbuild and not IS_MAC:
            # SDK clang doesn't work for native builds on macos
            return config.sdkBinDir / "clang-cpp"
        return config.clangCppPath


class FreeBSDTargetInfo(_ClangBasedTargetInfo):
    FREEBSD_VERSION = 13

    def compiler_dir(self, config: "CheriConfig") -> Path:
        # TODO: BuildUpstreamLLVM.installDir?
        return config.sdkBinDir

    def compiler_target(self, config: "CheriConfig"):
        return ["llvm"]  # TODO: upstream-llvm???

    def target_triple(self, config: "CheriConfig"):
        common_suffix = "-unknown-freebsd" + str(self.FREEBSD_VERSION)
        # TODO: do we need any special cases here?
        return self.target.cpu_architecture.value + common_suffix


class CheriBSDTargetInfo(FreeBSDTargetInfo):
    FREEBSD_VERSION = 13

    def compiler_dir(self, config: "CheriConfig") -> Path:
        # TODO: BuildLLVM.installDir?
        return config.sdkBinDir

    def compiler_target(self, config: "CheriConfig"):
        return ["llvm"]

    def target_triple(self, config: "CheriConfig"):
        if self.target.is_cheri_purecap():
            # anything over 10 should use libc++ by default
            assert self.target.is_mips(include_purecap=True), "Only MIPS purecap is supported"
            return "mips64c{}-unknown-freebsd{}-purecap".format(config.cheriBits, self.FREEBSD_VERSION)
        return super().target_triple(config)

