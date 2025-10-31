#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2025 Alfredo Mazzinghi
#
# This software was developed by SRI International, the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology), and Capabilities Limited under Defense Advanced Research
# Projects Agency (DARPA) Contract No. FA8750-24-C-B047 ("DEC").
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

from .crosscompileproject import BuildType, CrossCompileCMakeProject, GitRepository
from ...config.compilation_targets import CompilationTargets
from ...config.target_info import DefaultInstallDir


class BuildGrpc(CrossCompileCMakeProject):
    target = "grpc"
    repository = GitRepository("https://github.com/CTSRD-CHERI/grpc.git", default_branch="grpc-1.54.2-cheri")
    dependencies = ("abseil", "c-ares", "protobuf", "re2", "googlebenchmark")
    is_large_source_repository = True
    default_build_type = BuildType.RELWITHDEBINFO
    native_install_dir = DefaultInstallDir.CHERI_SDK
    _supported_architectures = (*CompilationTargets.ALL_SUPPORTED_CHERIBSD_TARGETS, *CompilationTargets.ALL_NATIVE)

    @property
    def cmake_prefix_paths(self):
        # Force CMAKE_PREFIX_PATH to be empty
        # This should be fine, because we are still setting the
        # CMAKE_SYSROOT correctly and setting
        # CMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER
        # So the find_package() calls should pick up sysroot packages
        # while find_program() will find thigs in $PATH or CMAKE_PROGRAM_PATH.
        if self.compiling_for_host():
            return super().cmake_prefix_paths
        else:
            return []

    def setup(self):
        super().setup()
        self.add_cmake_options(
            CMAKE_CXX_STANDARD=17,
            gRPC_ABSL_PROVIDER="package",
            gRPC_BENCHMARK_PROVIDER="package",
            gRPC_CARES_PROVIDER="package",
            gRPC_PROTOBUF_PROVIDER="package",
            gRPC_RE2_PROVIDER="package",
            gRPC_SSL_PROVIDER="package",
            gRPC_ZLIB_PROVIDER="package",
            gRPC_BACKWARDS_COMPATIBILITY_MODE="OFF",
            gRPC_BUILD_CODEGEN="ON",
            gRPC_BUILD_GRPC_CPP_PLUGIN="ON",
            BUILD_SHARED_LIBS="ON",
            CMAKE_PROGRAM_PATH=self.config.cheri_sdk_bindir,
        )

        if not self.compiling_for_host():
            self.add_cmake_options(gRPC_BUILD_TESTS="ON")
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")
        self.cross_warning_flags.append("-Wno-error=format")
        self.host_warning_flags.append("-Wno-error=missing-template-arg-list-after-template-kw")

    def install(self, **kwargs):
        super().install(**kwargs)

        if self.target_info.is_native():
            return

        scenario_dir = self.install_dir / "qps_scenarios"
        self.makedirs(scenario_dir)
        self.run_cmd(
            "python",
            f"{self.source_dir}/tools/run_tests/performance/scenario_config_exporter.py",
            "--export_scenarios",
            "-l",
            "c++",
            "--category=all",
            cwd=scenario_dir,
        )

        # Install the QPS benchmark components
        self.install_file(self.build_dir / "qps_worker", self.install_dir / "bin" / "grpc_qps_worker")
        self.install_file(self.build_dir / "qps_json_driver", self.install_dir / "bin" / "grpc_qps_json_driver")

        for sofile in self.build_dir.glob("libgrpc++_test_config.so*"):
            self.install_file(self.build_dir / sofile, self.install_dir / "lib" / sofile.name)
        for sofile in self.build_dir.glob("libgrpc++_test_util.so*"):
            self.install_file(self.build_dir / sofile, self.install_dir / "lib" / sofile.name)
        for sofile in self.build_dir.glob("libgrpc_test_util.so*"):
            self.install_file(self.build_dir / sofile, self.install_dir / "lib" / sofile.name)
