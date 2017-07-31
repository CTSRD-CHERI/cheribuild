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
from .crosscompileproject import *
from ...utils import fatalError
import re


class BuildPostgres(CrossCompileAutotoolsProject):
    repository = "https://github.com/CTSRD-CHERI/postgres.git"
    gitBranch = "96-cheri"
    # we have to build in the source directory, out-of-source is broken
    defaultBuildDir = CrossCompileAutotoolsProject.defaultSourceDir
    requiresGNUMake = True
    defaultOptimizationLevel = ["-O2"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # self.COMMON_FLAGS.append("-DUSE_ASSERT_CHECKING=1")
        # self.COMMON_FLAGS.append("-DLOCK_DEBUG=1")
        self.COMMON_FLAGS.extend(["-pedantic",
                                  "-Wno-gnu-statement-expression",
                                  "-Wno-flexible-array-extensions",  # TODO: could this cause errors?
                                  "-Wno-extended-offsetof",
                                  "-Wno-format-pedantic",
                                  ])
        self.LDFLAGS.append("-pthread")
        if self.crossCompileTarget != CrossCompileTarget.NATIVE:
            self.COMMON_FLAGS.append("-I/usr/include/edit")
            self.configureEnvironment["AR"] = str(self.sdkBinDir / "cheri-unknown-freebsd-ar")
            # tell postgres configure that %zu works in printf()
            self.configureEnvironment["PRINTF_SIZE_T_SUPPORT"] = "yes"
            # currently we can only build static:
            self.LDFLAGS.append("-static")
            self.COMMON_FLAGS.append("-static")  # adding it to LDFLAGS only doesn't seem to be enough

        if self.debugInfo:
            self.configureArgs.append("--enable-debug")
        self.configureArgs.extend(["--without-libxml", "--without-readline", "--without-gssapi"])

    def install(self, **kwargs):
        super().install()
        self.runMakeInstall(args=self.commonMakeArgs + ["-C", "src/test/regress"], target="install-tests")
        # install the benchmark script
        benchmark = self.readFile(self.sourceDir / "postgres-benchmark.sh")
        benchmark = re.sub(r'POSTGRES_ROOT=".*"', "POSTGRES_ROOT=\"" + str(self.installPrefix) + "\"", benchmark)
        self.writeFile(self.real_install_root_dir / "postgres-benchmark.sh", benchmark, overwrite=True, mode=0o755)
        run_tests = self.readFile(self.sourceDir / "run-postgres-tests.sh")
        run_tests = re.sub(r'POSTGRES_ROOT=".*"', "POSTGRES_ROOT=\"" + str(self.installPrefix) + "\"", run_tests)
        self.writeFile(self.real_install_root_dir / "run-postgres-tests.sh", run_tests, overwrite=True, mode=0o755)

    def needsConfigure(self):
        return not (self.buildDir / "GNUmakefile").exists()

    def process(self):
        # Postgres needs to build in the source directory and mixing 128/256/mips causes issues
        # save the last target in a file and make it an error if it doesn't match
        last_target_file = self.buildDir / "LAST_TARGET"
        current_target_arch = self.crossCompileTarget.value
        if self.compiling_for_cheri():
            current_target_arch += self.config.cheriBitsStr
        if not self.config.clean and (self.buildDir / "config.log").exists():
            last_target_arch = "unknown" if not last_target_file.exists() else self.readFile(last_target_file)
            # print("Last target =", last_target_arch, "current target =", current_target_arch)
            if last_target_arch != current_target_arch:
                fatalError("Last postgres compile targeted", last_target_arch, " but current target is",
                           current_target_arch, "-- this will cause runtime errors! Rerun cheribuild with --clean.")
        if not self.config.pretend:
            last_target_file.write_text(current_target_arch)
        super().process()
