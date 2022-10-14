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
import shutil
import typing
from pathlib import Path

from .benchmark_mixin import BenchmarkMixin
from .crosscompileproject import (BuildType, CompilationTargets, CrossCompileCMakeProject, DefaultInstallDir,
                                  GitRepository)
from .llvm import BuildCheriLLVM, BuildUpstreamLLVM, BuildLLVMBase
from ..project import ReuseOtherProjectRepository
from ...utils import cached_property, is_jenkins_build, classproperty
from ...config.compilation_targets import FreeBSDTargetInfo


class BuildLLVMTestSuiteBase(BenchmarkMixin, CrossCompileCMakeProject):
    do_not_add_to_targets = True
    default_build_type = BuildType.RELEASE
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        return [cls.llvm_project.get_class_for_target(CompilationTargets.NATIVE).target]

    # noinspection PyMethodParameters
    @classproperty
    def llvm_project(cls) -> typing.Type[BuildLLVMBase]:
        target_info = cls.get_crosscompile_target().target_info_cls
        if issubclass(target_info, FreeBSDTargetInfo):
            # noinspection PyProtectedMember
            return target_info._get_compiler_project()
        else:
            return BuildCheriLLVM

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.collect_stats = cls.add_bool_option("collect-stats", default=False,
                                                help="Collect statistics from the compiler")

    def __find_in_sdk_or_llvm_build_dir(self, name) -> Path:
        llvm_project = self.llvm_project.get_instance(self, cross_target=CompilationTargets.NATIVE)
        if (llvm_project.build_dir / "bin" / name).exists():
            return llvm_project.build_dir / "bin" / name
        if is_jenkins_build() and not self.compiling_for_host():
            return self.sdk_bindir / name
        return llvm_project.install_dir / "bin" / name

    def find_in_sdk_or_llvm_build_dir(self, name):
        result = self.__find_in_sdk_or_llvm_build_dir(name)
        # Warn if the tool could not be found. Depending on other CMake flags this may not be an error, but printing
        # this message is useful to debug build failures.
        if not result.exists():
            self.dependency_warning("Could not find LLVM tool", name, "at", result)
        return result

    @cached_property
    def llvm_lit(self):
        return self.find_in_sdk_or_llvm_build_dir("llvm-lit")

    def setup(self):
        super().setup()
        self.add_cmake_options(
            TEST_SUITE_LLVM_SIZE=self.find_in_sdk_or_llvm_build_dir("llvm-size"),
            TEST_SUITE_LLVM_PROFDATA=self.find_in_sdk_or_llvm_build_dir("llvm-profdata"),
            TEST_SUITE_LIT=self.llvm_lit,
            TEST_SUITE_COLLECT_CODE_SIZE=self.collect_stats,
            TEST_SUITE_COLLECT_COMPILE_TIME=self.collect_stats,
            TEST_SUITE_COLLECT_STATS=self.collect_stats)
        if self.compiling_for_host() and self.target_info.is_linux() and shutil.which("perf") is not None:
            self.add_cmake_options(TEST_SUITE_USE_PERF=True)

        if not self.compiling_for_host():
            self.add_cmake_options(TEST_SUITE_HOST_CC=self.host_CC)
            # we want to link against libc++ not libstdc++ (and for some reason we need to specify libgcc_eh too
            self.add_cmake_options(TEST_SUITE_CXX_LIBRARY="-lc++;-lgcc_eh")
            self.add_cmake_options(BENCHMARK_USE_LIBCXX=True)
            if self.can_run_binaries_on_remote_morello_board():
                self.add_cmake_options(TEST_SUITE_RUN_BENCHMARKS=True,
                                       TEST_SUITE_REMOTE_HOST=self.config.remote_morello_board)
            else:
                self.add_cmake_options(TEST_SUITE_RUN_BENCHMARKS=False)  # Would need to set up custom executor
            if self.crosscompile_target.is_any_x86():
                # Have to set the X86CPU_ARCH otherwise the build fails
                self.add_cmake_options(X86CPU_ARCH="unknown")
        if self.compiling_for_cheri():
            # LLVM IR testcases do not work for purecap.
            self.add_cmake_options(TEST_SUITE_ENABLE_BITCODE_TESTS=False)

    def run_tests(self):
        output_file = self.build_dir / "results.json"
        if self.can_run_binaries_on_remote_morello_board():
            self.run_make("rsync")  # Copy benchmark binaries over
            self.run_cmd(self.llvm_lit, "-vv", "-j1", "-o", output_file, ".", cwd=self.build_dir)
            return
        if self.collect_stats or self.compiling_for_host():
            self.delete_file(output_file)
            self.run_cmd(self.llvm_lit, "-sv", "-o", output_file, ".", cwd=self.build_dir)
            return
        super().run_tests()


class BuildLLVMTestSuite(BuildLLVMTestSuiteBase):
    target = "llvm-test-suite"
    repository = GitRepository("https://github.com/CTSRD-CHERI/llvm-test-suite.git")
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL

    def setup(self):
        super().setup()
        # TODO: fix these issues
        self.cross_warning_flags += ["-Wno-error=format", "-Werror=cheri-prototypes"]


class BuildLLVMTestSuiteCheriBSDUpstreamLLVM(BuildLLVMTestSuite):
    target = "llvm-test-suite-cheribsd-upstream-llvm"
    repository = ReuseOtherProjectRepository(BuildLLVMTestSuite, do_update=True)
    llvm_project = BuildUpstreamLLVM
    supported_architectures = CompilationTargets.ALL_CHERIBSD_NON_CHERI_TARGETS + [CompilationTargets.NATIVE]

    @property
    def custom_c_preprocessor(self):
        return self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/clang-cpp"

    @property
    def custom_c_compiler(self):
        return self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/clang"

    @property
    def custom_cxx_compiler(self):
        return self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/clang++"
