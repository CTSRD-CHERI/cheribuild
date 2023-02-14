#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright 2022 Alex Richardson
# Copyright 2022 Google LLC
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
from .crosscompileproject import CompilationTargets, CrossCompileCMakeProject, DefaultInstallDir, GitRepository


class BuildFlatbuffers(CrossCompileCMakeProject):
    target = "flatbuffers"
    repository = GitRepository("https://github.com/google/flatbuffers.git")
    needs_native_build_for_crosscompile = True
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def setup(self):
        super().setup()
        self.add_cmake_options(FLATBUFFERS_BUILD_CPP17=True)
        # We need a native flatc executable when cross-compiling
        if not self.compiling_for_host():
            native_instance = self.get_instance(self, cross_target=CompilationTargets.NATIVE)
            self.add_cmake_options(FLATBUFFERS_FLATC_EXECUTABLE=native_instance.install_dir / "bin/flatc")
        # FIXME: std::stringstream needs an intcap overload
        # self.COMMON_FLAGS.append("-DFLATBUFFERS_PREFER_PRINTF=1")
