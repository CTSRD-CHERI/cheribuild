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
from .crosscompileproject import *
from ..llvm import BuildCheriLLVM
from ..project import ReuseOtherProjectDefaultTargetRepository
from ...utils import classproperty


class BuildCompilerRt(CrossCompileCMakeProject):
    # TODO: add an option to allow upstream llvm?
    repository = ReuseOtherProjectDefaultTargetRepository(BuildCheriLLVM, subdirectory="compiler-rt")
    project_name = "compiler-rt"
    default_install_dir = DefaultInstallDir.COMPILER_RESOURCE_DIR
    _check_install_dir_conflict = False
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS + \
                              CompilationTargets.ALL_SUPPORTED_BAREMETAL_TARGETS + \
                              [CompilationTargets.RTEMS_RISCV64_PURECAP]

    def __init__(self, config: CheriConfig):
        super().__init__(config)

        if self.target_info.is_rtems():
            self.add_cmake_options(CMAKE_TRY_COMPILE_TARGET_TYPE="STATIC_LIBRARY") # RTEMS only needs static libs
            # Get default target (arch) from the triple
            self.add_cmake_options(COMPILER_RT_DEFAULT_TARGET_ARCH=self.target_info.target_triple.split('-')[0])

        self.add_cmake_options(
            LLVM_CONFIG_PATH=BuildCheriLLVM.getBuildDir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-config",
            LLVM_EXTERNAL_LIT=BuildCheriLLVM.getBuildDir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-lit",
            COMPILER_RT_BUILD_BUILTINS=True,
            COMPILER_RT_BUILD_SANITIZERS=True,
            COMPILER_RT_BUILD_XRAY=False,
            COMPILER_RT_BUILD_LIBFUZZER=True,
            COMPILER_RT_BUILD_PROFILE=False,
            COMPILER_RT_BAREMETAL_BUILD=self.target_info.is_baremetal(),
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
        if self.target_info.is_rtems():
            rt_runtime_path = self.installDir / ("lib/generic/libclang_rt.builtins-riscv64.a")
            if not rt_runtime_path.exists():
                self.warning("Did not install compiler runtime", rt_runtime_path.exists)
            else:
                print(self.target_info.sysroot_dir)
                self.createSymlink(rt_runtime_path, self.target_info.sysroot_dir / "lib/libclang_rt.builtins-riscv64.a")


class BuildCompilerRtBuiltins(CrossCompileCMakeProject):
    # TODO: add an option to allow upstream llvm?
    repository = ReuseOtherProjectDefaultTargetRepository(BuildCheriLLVM, subdirectory="compiler-rt")
    project_name = "compiler-rt-builtins"
    _check_install_dir_conflict = False
    is_sdk_target = True
    dependencies = ["newlib"]
    needs_sysroot = False  # We don't need a complete sysroot
    supported_architectures = CompilationTargets.ALL_SUPPORTED_BAREMETAL_TARGETS + [
        CompilationTargets.RTEMS_RISCV64_PURECAP]
    _default_architecture = CompilationTargets.BAREMETAL_NEWLIB_MIPS64

    # Note: needs to be @classproperty since it is called before __init__
    @classproperty
    def default_install_dir(cls):
        # Install compiler-rt to the sysroot to handle purecap and non-CHERI RTEMS
        if cls._xtarget is CompilationTargets.RTEMS_RISCV64_PURECAP:
            return DefaultInstallDir.SYSROOT
        return DefaultInstallDir.COMPILER_RESOURCE_DIR

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        assert self.target_info.is_baremetal() or self.target_info.is_rtems(), "No other targets supported yet"
        assert self.target_info.is_newlib(), "No other targets supported yet"
        # self.COMMON_FLAGS.append("-v")
        self.COMMON_FLAGS.append("-ffreestanding")
        if self.compiling_for_mips(include_purecap=False):
            self.add_cmake_options(COMPILER_RT_HAS_FPIC_FLAG=False)  # HACK: currently we build everything as -fno-pic

        if self.target_info.is_rtems():
            self.add_cmake_options(CMAKE_TRY_COMPILE_TARGET_TYPE="STATIC_LIBRARY")  # RTEMS only needs static libs
        self.add_cmake_options(
            LLVM_CONFIG_PATH=BuildCheriLLVM.getBuildDir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-config",
            LLVM_EXTERNAL_LIT=BuildCheriLLVM.getBuildDir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-lit",
            COMPILER_RT_BUILD_BUILTINS=True,
            COMPILER_RT_BUILD_SANITIZERS=False,
            COMPILER_RT_BUILD_XRAY=False,
            COMPILER_RT_BUILD_LIBFUZZER=False,
            COMPILER_RT_BUILD_PROFILE=False,
            COMPILER_RT_BAREMETAL_BUILD=self.target_info.is_baremetal(),
            COMPILER_RT_DEFAULT_TARGET_ONLY=True,
            # BUILTIN_SUPPORTED_ARCH="mips64",
            TARGET_TRIPLE=self.target_info.target_triple,
        )
        if self.should_include_debug_info:
            self.add_cmake_options(COMPILER_RT_DEBUG=True)
        if self.compiling_for_mips(include_purecap=True):
            # self.add_cmake_options(COMPILER_RT_DEFAULT_TARGET_ARCH="mips")
            self.add_cmake_options(COMPILER_RT_DEFAULT_TARGET_ONLY=True)

    def configure(self, **kwargs):
        self.configureArgs[0] = str(self.sourceDir / "lib/builtins")
        super().configure()

    def install(self, **kwargs):
        super().install(**kwargs)

        libname = "libclang_rt.builtins-" + self.triple_arch + ".a"

        if self.target_info.is_rtems():
            self.moveFile(self.installDir / "lib/rtems5" / libname, self.installDir / "lib" / libname)
        else:
            self.moveFile(self.installDir / "lib/generic" / libname, self.real_install_root_dir / "lib" / libname)

            if self.compiling_for_cheri():
                # compatibility with older compilers
                self.createSymlink(self.real_install_root_dir / "lib" / libname,
                                   self.real_install_root_dir / "lib" / "libclang_rt.builtins-cheri.a", print_verbose_only=False)
                self.createSymlink(self.real_install_root_dir / "lib" / libname,
                                   self.real_install_root_dir / "lib" / "libclang_rt.builtins-mips64.a", print_verbose_only=False)
            # HACK: we don't really need libunwind but the toolchain pulls it in automatically
            # TODO: is there an easier way to create empty .a files?
            self.run_cmd("ar", "rcv", self.installDir / "lib/libunwind.a", "/dev/null")
            self.run_cmd("ar", "dv", self.installDir / "lib/libunwind.a", "null")
            self.run_cmd("ar", "t", self.installDir / "lib/libunwind.a")  # should be empty now
