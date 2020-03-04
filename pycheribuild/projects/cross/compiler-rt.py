#
# Copyright (c) 2020 Hesham Almatary
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
import os
import sys
from .crosscompileproject import *
from ..llvm import BuildCheriLLVM
from ..project import ReuseOtherProjectDefaultTargetRepository
#from ...utils import OSInfo, setEnv, runCmd, warningMessage, commandline_to_str, IS_MAC

class BuildCompilerRt(CrossCompileCMakeProject):
    # TODO: add an option to allow upstream llvm?
    repository = ReuseOtherProjectDefaultTargetRepository(BuildCheriLLVM, subdirectory="compiler-rt")
    project_name = "compiler-rt"
    default_install_dir = DefaultInstallDir.COMPILER_RESOURCE_DIR
    _check_install_dir_conflict = False
    _default_architecture = CompilationTargets.CHERIBSD_MIPS_PURECAP
    supported_architectures =CompilationTargets.ALL_SUPPORTED_BAREMETAL_TARGETS + CompilationTargets.ALL_SUPPORTED_RTEMS_TARGETS

    def __init__(self, config: CheriConfig):
        super().__init__(config)

        if self.target_info.is_rtems:
            self.add_cmake_options(CMAKE_TRY_COMPILE_TARGET_TYPE="STATIC_LIBRARY") # RTEMS only needs static libs
            # Get default target (arch) from the triple
            self.add_cmake_options(COMPILER_RT_DEFAULT_TARGET_ARCH=self.target_info.target_triple.split('-')[0])

        self.add_cmake_options(
            LLVM_CONFIG_PATH=BuildCheriLLVM.getInstallDir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-config",
            LLVM_EXTERNAL_LIT=BuildCheriLLVM.getBuildDir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-lit",
            COMPILER_RT_BUILD_BUILTINS=True,
            COMPILER_RT_BUILD_SANITIZERS=True,
            COMPILER_RT_BUILD_XRAY=False,
            COMPILER_RT_BUILD_LIBFUZZER=True,
            COMPILER_RT_BUILD_PROFILE=False,
            COMPILER_RT_BAREMETAL_BUILD=self.target_info.is_baremetal,
            # COMPILER_RT_DEFAULT_TARGET_ONLY=True,
            # BUILTIN_SUPPORTED_ARCH="mips64",
            TARGET_TRIPLE=self.target_info.target_triple,
            # LLVM_ENABLE_PER_TARGET_RUNTIME_DIR=True,
        )
        if self.should_include_debug_info:
            self.add_cmake_options(COMPILER_RT_DEBUG=True)

        if self.compiling_for_mips(include_purecap=True):
            # self.add_cmake_options(COMPILER_RT_DEFAULT_TARGET_ARCH="mips")
            self.add_cmake_options(COMPILER_RT_DEFAULT_TARGET_ONLY=True)

    def install(self, **kwargs):
        super().install(**kwargs)
        if self.compiling_for_cheri():
            # HACK: we don't really need the ubsan runtime but the toolchain pulls it in automatically
            # TODO: is there an easier way to create an empty archive?
            ubsan_runtime_path = self.installDir / ("lib/freebsd/libclang_rt.ubsan_standalone-mips64c" + self.config.mips_cheri_bits_str + ".a")
            if not ubsan_runtime_path.exists():
                self.warning("Did not install ubsan runtime", ubsan_runtime_path)
        if self.target_info.is_rtems:
            rt_runtime_path = self.installDir / ("lib/generic/libclang_rt.builtins-riscv64.a")
            if not rt_runtime_path.exists():
                self.warning("Did not install compiler runtime", rt_runtime_path.exists)
            else:
                print(self.target_info.sysroot_dir)
                self.createSymlink(rt_runtime_path, self.target_info.sysroot_dir / "lib/libclang_rt.builtins-riscv64.a")
