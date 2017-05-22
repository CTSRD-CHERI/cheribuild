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

from .crosscompileproject import *
from ...project import *
from ..cheribsd import BuildCHERIBSD
from ..llvm import BuildLLVM
from ...config.loader import ComputedDefaultValue
from ...utils import statusUpdate
from pathlib import Path

installToCXXDir = ComputedDefaultValue(
    function=lambda config, project: BuildCHERIBSD.rootfsDir(config) / "extra/c++",
    asString="$CHERIBSD_ROOTFS/extra/c++")


class LLVMTestSuiteBase(object):
    repository = "https://github.com/llvm-mirror/test-suite.git"
    defaultInstallDir = installToCXXDir
    dependencies = ["llvm"]
    defaultCMakeBuildType = "Debug"
    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: Path(config.sourceRoot / "llvm-test-suite"),
        asString="$SOURCE_ROOT/llvm-test-suite")

    def add_test_suite_cmake_options(self):
        llvmBinDir = self.config.sdkBinDir
        self.add_cmake_options(
            TEST_SUITE_LLVM_SIZE=llvmBinDir / "llvm-size",
            TEST_SUITE_LLVM_PROFDATA=llvmBinDir / "llvm-profdata",
            CMAKE_C_COMPILER=llvmBinDir / "clang",
            CMAKE_ASM_COMPILER=llvmBinDir / "clang",
            CMAKE_CXX_COMPILER=llvmBinDir / "clang++",
        )
        self.add_cmake_options(TEST_SUITE_LIT=BuildLLVM.buildDir / "bin/llvm-lit")

    def install(self):
        statusUpdate("No install step for llvm-test-suite")
        pass


class BuildLlvmTestSuiteNative(LLVMTestSuiteBase, CMakeProject):
    projectName = "llvm-test-suite-native"

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # TODO: dynamic vs static
        self.add_test_suite_cmake_options()


class BuildLlvmTestSuiteCross(LLVMTestSuiteBase, CrossCompileCMakeProject):
    projectName = "llvm-test-suite-cheri"

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.add_test_suite_cmake_options()
