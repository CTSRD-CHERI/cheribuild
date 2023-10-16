#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2023 Alfredo Mazzinghi
#
# This software was developed by SRI International, the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology), and Capabilities Limited under Defense Advanced Research
# Projects Agency (DARPA) Contract No. HR001122S0003 ("MTSS").
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
from .crosscompileproject import CrossCompileCMakeProject, GitRepository


class BuildAbseil(CrossCompileCMakeProject):
    target = "abseil"
    repository = GitRepository("https://github.com/CTSRD-CHERI/abseil-cpp.git",
                               default_branch="cheri-20220623.0")

    def setup(self):
        super().setup()
        # Enable tests for debug builds but not for production builds.
        # In this way, the production version will have rtti.
        if self.build_type.is_debug:
            self.CXXFLAGS.append("-fno-rtti")
            self.add_cmake_options(ABSL_BUILD_TESTING=True,
                                   ABSL_USE_EXTERNAL_GOOGLETEST=False,
                                   ABSL_USE_GOOGLETEST_HEAD=True)
        # FIXME: The ElfMemImage in abseil is unported.
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")
