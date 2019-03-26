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
import shutil

from .crosscompileproject import *
from ...utils import getCompilerInfo, runCmd, IS_FREEBSD

class BuildBODiagSuite(CrossCompileCMakeProject):
    projectName = "bodiagsuite"
    repository = GitRepository("https://github.com/CTSRD-CHERI/bodiagsuite")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    appendCheriBitsToBuildDir = True
    supported_architectures = [CrossCompileTarget.CHERI, CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]
    defaultOptimizationLevel = ["-O0"]
    default_build_type = BuildType.DEBUG
    default_use_asan = True

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if getCompilerInfo(self.CC).is_clang:
            self.common_warning_flags.append("-Wno-unused-command-line-argument")
            self.common_warning_flags.append("-Wno-array-bounds") # lots of statically out of bounds cases

    def process(self):
        # FIXME: add option to disable FORTIFY_SOURCE
        if self.cross_build_type != BuildType.DEBUG:
            self.warning("BODiagsuite contains undefined behaviour that might be optimized away unless you compile"
                         " at -O0.")
            if not self.queryYesNo("Are you sure you want to continue?"):
                self.fatal("Cannot continue.")
        super().process()

    def compile(self, **kwargs):
        super().compile(**kwargs)
        # TODO: add this copy to the CMakeLists.txt
        self.installFile(self.sourceDir / "Makefile.bsd-run", self.buildDir / "Makefile.bsd-run", force=True)

    def install(*args, **kwargs):
        pass

    def run_tests(self):
        bmake = shutil.which("bmake")
        if bmake is None and IS_FREEBSD:
            # on FreeBSD bmake is
            bmake = shutil.which("make")
        if bmake is None:
            self.fatal("Could not find bmake")
        # Ensure the run directory exists
        self.makedirs(self.buildDir / "run")
        if self.config.clean:
            self.cleanDirectory(self.buildDir / "run", keepRoot=False)
        testsuite_prefix = self.buildDirSuffix(self.config, self.get_crosscompile_target(self.config), self.use_asan)[1:]
        testsuite_prefix = testsuite_prefix.replace("-build", "")
        extra_args = ["--bmake-path", bmake] if self.compiling_for_host() else []
        self.run_cheribsd_test_script("run_bodiagsuite.py", "--junit-testsuite-name", testsuite_prefix, *extra_args,
                                      mount_sourcedir=False, mount_builddir=True)
