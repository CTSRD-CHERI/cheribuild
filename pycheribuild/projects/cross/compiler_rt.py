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
from pathlib import Path

from .crosscompileproject import CompilationTargets, CrossCompileCMakeProject, DefaultInstallDir
from .llvm import BuildCheriLLVM, BuildUpstreamLLVM
from ..project import ReuseOtherProjectDefaultTargetRepository, Linkage
from ...config.target_info import CPUArchitecture
from ...utils import classproperty, is_jenkins_build


class BuildCompilerRt(CrossCompileCMakeProject):
    # TODO: add an option to allow upstream llvm?
    llvm_project = BuildCheriLLVM
    repository = ReuseOtherProjectDefaultTargetRepository(llvm_project, subdirectory="compiler-rt")
    target = "compiler-rt"
    default_install_dir = DefaultInstallDir.COMPILER_RESOURCE_DIR
    _check_install_dir_conflict = False
    supported_architectures = \
        CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS + \
        CompilationTargets.ALL_SUPPORTED_BAREMETAL_TARGETS + \
        CompilationTargets.ALL_SUPPORTED_RTEMS_TARGETS

    def setup(self):
        super().setup()
        if self.target_info.is_rtems() or self.target_info.is_baremetal():
            self.add_cmake_options(CMAKE_TRY_COMPILE_TARGET_TYPE="STATIC_LIBRARY")  # RTEMS only needs static libs
        # When building in Jenkins, we use the installed path to LLVM tools, otherwise we use the tools from the
        # local build dir
        if is_jenkins_build():
            llvm_tools_bindir = self.llvm_project.get_native_install_path(self.config) / "bin"
        else:
            llvm_tools_bindir = self.llvm_project.get_build_dir(self, cross_target=CompilationTargets.NATIVE) / "bin"
        self.add_cmake_options(
            LLVM_CONFIG_PATH=llvm_tools_bindir / "llvm-config",
            LLVM_EXTERNAL_LIT=llvm_tools_bindir / "llvm-lit",
            COMPILER_RT_BUILD_BUILTINS=True,
            COMPILER_RT_BUILD_SANITIZERS=True,
            COMPILER_RT_BUILD_XRAY=False,
            COMPILER_RT_INCLUDE_TESTS=True,
            COMPILER_RT_BUILD_LIBFUZZER=True,
            COMPILER_RT_BUILD_PROFILE=False,
            COMPILER_RT_EXCLUDE_ATOMIC_BUILTIN=False,
            COMPILER_RT_BAREMETAL_BUILD=self.target_info.is_baremetal(),
            # Needed after https://reviews.llvm.org/D99621
            COMPILER_RT_DEFAULT_TARGET_ONLY=not self.compiling_for_host(),
            # Per-target runtime directories don't add the purecap suffix so can't be used right now.
            LLVM_ENABLE_PER_TARGET_RUNTIME_DIR=False,
            )
        if self.should_include_debug_info:
            self.add_cmake_options(COMPILER_RT_DEBUG=True)

        if self.compiling_for_mips(include_purecap=True):
            # self.add_cmake_options(COMPILER_RT_DEFAULT_TARGET_ARCH="mips")
            self.add_cmake_options(COMPILER_RT_DEFAULT_TARGET_ONLY=True)

    def install(self, **kwargs):
        super().install(**kwargs)
        if self.compiling_for_cheri([CPUArchitecture.MIPS64]):
            # HACK: we don't really need the ubsan runtime but the toolchain pulls it in automatically
            # TODO: is there an easier way to create an empty archive?
            ubsan_runtime_path = self.install_dir / (
                    "lib/freebsd/libclang_rt.ubsan_standalone-mips64c" + self.config.mips_cheri_bits_str + ".a")
            if not ubsan_runtime_path.exists():
                self.warning("Did not install ubsan runtime", ubsan_runtime_path)
        if self.target_info.is_rtems():
            rt_runtime_path = self.install_dir / "lib/generic/libclang_rt.builtins-riscv64.a"
            if not rt_runtime_path.exists():
                self.warning("Did not install compiler runtime", rt_runtime_path.exists)
            else:
                print(self.target_info.sysroot_dir)
                self.create_symlink(rt_runtime_path,
                                    self.target_info.sysroot_dir / "lib/libclang_rt.builtins-riscv64.a")

    def run_tests(self):
        self.run_make("check-compiler-rt")


class BuildUpstreamCompilerRt(BuildCompilerRt):
    llvm_project = BuildUpstreamLLVM
    repository = ReuseOtherProjectDefaultTargetRepository(llvm_project, subdirectory="compiler-rt")
    target = "upstream-compiler-rt"
    # TODO: default_install_dir = DefaultInstallDir.COMPILER_RESOURCE_DIR
    default_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    supported_architectures = [CompilationTargets.NATIVE]


class BuildCompilerRtBuiltins(CrossCompileCMakeProject):
    # TODO: add an option to allow upstream llvm?
    llvm_project = BuildCheriLLVM
    repository = ReuseOtherProjectDefaultTargetRepository(llvm_project, subdirectory="compiler-rt")
    target = "compiler-rt-builtins"
    _check_install_dir_conflict = False
    is_sdk_target = True
    root_cmakelists_subdirectory = Path("lib/builtins")
    needs_sysroot = False  # We don't need a complete sysroot
    supported_architectures = \
        CompilationTargets.ALL_SUPPORTED_BAREMETAL_TARGETS + CompilationTargets.ALL_SUPPORTED_RTEMS_TARGETS

    # Note: needs to be @classproperty since it is called before __init__
    @classproperty
    def default_install_dir(self):
        # Install compiler-rt to the sysroot to handle purecap and non-CHERI RTEMS
        if self._xtarget is CompilationTargets.RTEMS_RISCV64_PURECAP:
            return DefaultInstallDir.ROOTFS_LOCALBASE
        elif self._xtarget is not None and self._xtarget.target_info_cls.is_baremetal():
            # Conflicting file names for RISC-V non-CHERI,hybrid, and purecap -> install to prefixed directory
            # instead of the compiler resource directory
            return DefaultInstallDir.ROOTFS_LOCALBASE
        return DefaultInstallDir.COMPILER_RESOURCE_DIR

    def linkage(self):
        # The default value of STATIC (for baremetal targets) would add additional flags that are not be needed
        # since the CMake files already ensure that we link statically.
        # Forcing static linkage also depends on CMake 3.15 but we should be able to build this with the baseline
        # version of 3.13.4.
        return Linkage.DEFAULT

    def setup(self):
        super().setup()
        assert self.target_info.is_baremetal() or self.target_info.is_rtems(), "No other targets supported yet"
        # self.COMMON_FLAGS.append("-v")
        self.COMMON_FLAGS.append("-ffreestanding")
        if self.compiling_for_mips(include_purecap=False):
            self.add_cmake_options(COMPILER_RT_HAS_FPIC_FLAG=False)  # HACK: currently we build everything as -fno-pic

        if self.target_info.is_rtems() or self.target_info.is_baremetal():
            self.add_cmake_options(CMAKE_TRY_COMPILE_TARGET_TYPE="STATIC_LIBRARY")  # RTEMS only needs static libs

        self.add_cmake_options(
            LLVM_CONFIG_PATH=self.sdk_bindir / "llvm-config" if is_jenkins_build() and not self.compiling_for_host()
            else
            self.llvm_project.get_build_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-config",
            LLVM_EXTERNAL_LIT=self.sdk_bindir / "llvm-lit" if is_jenkins_build() and not self.compiling_for_host() else
            self.llvm_project.get_build_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-lit",
            COMPILER_RT_BUILD_BUILTINS=True,
            COMPILER_RT_BUILD_SANITIZERS=False,
            COMPILER_RT_BUILD_XRAY=False,
            COMPILER_RT_BUILD_LIBFUZZER=False,
            COMPILER_RT_BUILD_PROFILE=False,
            COMPILER_RT_EXCLUDE_ATOMIC_BUILTIN=False,
            COMPILER_RT_BAREMETAL_BUILD=self.target_info.is_baremetal(),
            COMPILER_RT_DEFAULT_TARGET_ONLY=True,
            # BUILTIN_SUPPORTED_ARCH="mips64",
            TARGET_TRIPLE=self.target_info.target_triple,
            )
        if self.target_info.is_baremetal():
            self.add_cmake_options(COMPILER_RT_OS_DIR="baremetal")
        if self.should_include_debug_info:
            self.add_cmake_options(COMPILER_RT_DEBUG=True)

    def install(self, **kwargs):
        super().install(**kwargs)

        libname = "libclang_rt.builtins-" + self.triple_arch + ".a"
        if self.target_info.is_rtems():
            self.move_file(self.install_dir / "lib/rtems5" / libname, self.install_dir / "lib" / libname)
        elif self.target_info.is_baremetal():
            self.move_file(self.install_dir / "lib/baremetal" / libname, self.real_install_root_dir / "lib" / libname)
            self.create_symlink(self.install_dir / "lib" / libname, self.install_dir / "lib/libgcc.a",
                                print_verbose_only=False)


class BuildUpstreamCompilerRtBuiltins(BuildCompilerRtBuiltins):
    target = "upstream-compiler-rt-builtins"
    llvm_project = BuildUpstreamLLVM
    repository = ReuseOtherProjectDefaultTargetRepository(llvm_project, subdirectory="compiler-rt")
