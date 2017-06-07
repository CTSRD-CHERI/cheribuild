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
from ...utils import runCmd, IS_FREEBSD

class BuildSQLite(CrossCompileAutotoolsProject):
    repository = "https://github.com/CTSRD-CHERI/sqlite.git"
    gitBranch = "branch-3.19"
    crossInstallDir = CrossInstallDir.SDK
    defaultOptimizationLevel = ["-O2"]
    warningFlags = ["-Wno-error=cheri-capability-misuse"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if self.crossCompileTarget != CrossCompileTarget.NATIVE:
            self.configureEnvironment["BUILD_CC"] = self.config.clangPath
            self.configureEnvironment["BUILD_CFLAGS"] = "-integrated-as"
            self.LDFLAGS.append("-static")
            self.configureArgs.extend([
                "--enable-static", "--disable-shared",
                "--disable-amalgamation",  # don't concatenate sources
                "--disable-tcl",
                "--disable-load-extension",
            ])

        if not self.compiling_for_host() or IS_FREEBSD:
            self.configureArgs.append("--disable-editline")
            # not sure if needed:
            self.configureArgs.append("--disable-readline")

    def compile(self, **kwargs):
        # create the required metadata
        runCmd("create-fossil-manifest", cwd=self.sourceDir)
        super().compile()

    def install(self, **kwargs):
        super().install()
        # self.runMakeInstall(args=self.commonMakeArgs + ["-C", "src/test/regress"], target="install-tests")
        # # install the benchmark script
        # benchmark = self.readFile(self.sourceDir / "postgres-benchmark.sh")
        # benchmark = re.sub(r'POSTGRES_ROOT=".*"', "POSTGRES_ROOT=\"" + str(self.installPrefix) + "\"", benchmark)
        # self.writeFile(self.real_install_root_dir / "postgres-benchmark.sh", benchmark, overwrite=True, mode=0o755)
        # run_tests = self.readFile(self.sourceDir / "run-postgres-tests.sh")
        # run_tests = re.sub(r'POSTGRES_ROOT=".*"', "POSTGRES_ROOT=\"" + str(self.installPrefix) + "\"", run_tests)
        # self.writeFile(self.real_install_root_dir / "run-postgres-tests.sh", run_tests, overwrite=True, mode=0o755)

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()

