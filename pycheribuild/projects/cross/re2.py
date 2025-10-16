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


class BuildRe2(CrossCompileCMakeProject):
    target = "re2"
    repository = GitRepository("https://github.com/google/re2", default_branch="2023-03-01")
    is_large_source_repository = True
    default_build_type = BuildType.DEBUG
    native_install_dir = DefaultInstallDir.CHERI_SDK
    _supported_architectures = (*CompilationTargets.ALL_SUPPORTED_CHERIBSD_TARGETS, *CompilationTargets.ALL_NATIVE)

    def setup(self):
        super().setup()
        self.add_cmake_options(BUILD_SHARED_LIBS="ON")
