#
# Copyright (c) 2017 Alex Richardson
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
from ...utils import getCompilerInfo

class BuildBODiagSuite(CrossCompileCMakeProject):
    projectName = "bodiagsuite"
    repository = GitRepository("https://github.com/nwf/bodiagsuite")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    appendCheriBitsToBuildDir = True
    supported_architectures = [CrossCompileTarget.CHERI, CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]
    defaultOptimizationLevel = ["-O0"]
    default_build_type = BuildType.DEBUG

    def __init__(self, config: CheriConfig, *args, **kwargs):
        if self.compiling_for_host():
            self.use_asan = True  # must set this before calling the superclass constructor
        super().__init__(config, *args, **kwargs)
        if getCompilerInfo(self.CC).is_clang:
            self.common_warning_flags.append("-Wno-unused-command-line-argument")
            self.common_warning_flags.append("-Wno-array-bounds") # lots of statically out of bounds cases

    def process(self):
        if self.cross_build_type != BuildType.DEBUG:
            self.warning("BODiagsuite contains undefined behaviour that might be optimized away unless you compile"
                         " at -O0.")
            if not self.queryYesNo("Are you sure you want to continue?"):
                self.fatal("Cannot continue.")
        super().process()

    def install(*args, **kwargs):
        pass

    def run_tests(self):
        # Ensure the run directory exists
        self.cleanDirectory(self.buildDir / "run", keepRoot=False)
        # TODO: add this copy to the CMakeLists.txt
        self.installFile(self.sourceDir / "Makefile.bsd-run", self.buildDir / "Makefile.bsd-run", force=True)
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", "make -r -f /build/Makefile.bsd-run all",
                                      "--test-timeout", str(120 * 60), "--ignore-cheri-trap",
                                      mount_builddir=True, mount_sourcedir=False)
