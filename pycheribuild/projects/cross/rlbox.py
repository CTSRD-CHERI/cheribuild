#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
from .crosscompileproject import CrossCompileCMakeProject
from ..project import DefaultInstallDir, GitRepository
from ...config.compilation_targets import CompilationTargets


class BuildRLBox(CrossCompileCMakeProject):
    target = "rlbox-api"
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    repository = GitRepository("https://github.com/PLSysSec/rlbox_sandboxing_api")
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS

    def setup(self):
        super().setup()
        # Documentation is built by default if Doxygen is installed. Skip it to reduce build time.
        self.add_cmake_options(CMAKE_DISABLE_FIND_PACKAGE_Doxygen=True)

    def run_tests(self):
        if self.compiling_for_host():
            self.run_make("test")
        else:
            args = ["--verbose"] if self.config.verbose else []
            self.target_info.run_cheribsd_test_script(
                "run_rlbox_tests.py", *args, mount_builddir=True, mount_sourcedir=True, mount_sysroot=True
            )


class BuildCatch2(CrossCompileCMakeProject):
    target = "catch2"
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    repository = GitRepository("https://github.com/catchorg/Catch2", default_branch="v2.x")
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS

    def run_tests(self):
        if self.compiling_for_host():
            self.run_make("test")
        else:
            super().run_tests()
