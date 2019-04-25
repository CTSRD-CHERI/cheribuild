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
from ..project import ReuseOtherProjectRepository
from ...utils import getCompilerInfo, runCmd, IS_FREEBSD

class BuildJulietTestSuite(CrossCompileCMakeProject):
    projectName = "juliet-test-suite"
    # TODO: move repo to CTSRD-CHERI
    repository = GitRepository("https://github.com/arichardson/juliet-test-suite-c.git")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    appendCheriBitsToBuildDir = True
    supported_architectures = [CrossCompileTarget.CHERI, CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]
    defaultOptimizationLevel = ["-O0"]
    default_build_type = BuildType.DEBUG
    # default_use_asan = True

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.use_valgrind = cls.addBoolOption("use-valgrind", help="Run tests using valgrind (native only)",
                                             only_add_for_targets=[CrossCompileTarget.NATIVE])
        cls.use_stack_protector = cls.addBoolOption("use-stack-protector", help="Compile tests with stack-protector (non-CHERI only)")
        cls.use_fortify_source = cls.addBoolOption("use-fortify-source", help="Compile tests with _DFORTIFY_SOURCE=2 (no effect on FreeBSD)")


    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        #if getCompilerInfo(self.CC).is_clang:
        #    self.common_warning_flags.append("-Wno-unused-command-line-argument")
        if self.use_stack_protector:
            self.add_cmake_options(WITH_STACK_PROTECTOR=True)
        if self.use_fortify_source:
            self.add_cmake_options(WITH_FORTIFY_SOURCE=True)

    def process(self):
        if self.use_asan and self.use_valgrind:
            # ASAN is incompatible with valgrind
            self.fatal("Cannot use ASAN and valgrind at the same time!")
        # FIXME: add option to disable FORTIFY_SOURCE
        if self.cross_build_type != BuildType.DEBUG:
            self.warning("BODiagsuite contains undefined behaviour that might be optimized away unless you compile"
                         " at -O0.")
            if not self.queryYesNo("Are you sure you want to continue?"):
                self.fatal("Cannot continue.")
        super().process()

    def configure(self, **kwargs):
        self.fatal("Can't build all tests")

    def install(*args, **kwargs):
        pass

    def run_tests(self):
        pass
        # bmake = shutil.which("bmake")
        # if bmake is None and IS_FREEBSD:
        #     # on FreeBSD bmake is
        #     bmake = shutil.which("make")
        # if bmake is None:
        #     self.fatal("Could not find bmake")
        # # Ensure the run directory exists
        # self.makedirs(self.buildDir / "run")
        # if self.config.clean:
        #     self.cleanDirectory(self.buildDir / "run", keepRoot=False)
        # testsuite_prefix = self.buildDirSuffix(self.config, self.get_crosscompile_target(self.config), self.use_asan)[1:]
        # testsuite_prefix = testsuite_prefix.replace("-build", "")
        # extra_args = ["--bmake-path", bmake, "--jobs", str(self.config.makeJobs)] if self.compiling_for_host() else []
        # if self.use_valgrind:
        #     assert self.compiling_for_host()
        #     extra_args.append("--use-valgrind")
        # self.run_cheribsd_test_script("run_bodiagsuite.py", "--junit-testsuite-name", testsuite_prefix, *extra_args,
        #                               mount_sourcedir=False, mount_builddir=True)


class BuildJulietCWESubdir(CrossCompileCMakeProject):
    doNotAddToTargets = True
    cwe_number = None

    def configure(self, **kwargs):
        self.add_cmake_options(PLACE_OUTPUT_IN_TOPLEVEL_DIR=False)
        self.createSymlink(self.sourceDir / "../../CMakeLists.txt", self.sourceDir / "CMakeLists.txt")
        super().configure(**kwargs)

    def install(self, **kwargs):
        self.info("No need to install!")
        pass

    def run_tests(self):
        if self.compiling_for_host():
            # TODO: use the python script for native too
            runCmd(self.sourceDir / "../../bin/juliet-run.sh", str(self.cwe_number), cwd=self.sourceDir / "../../bin/")
        else:
            self.run_cheribsd_test_script("run_juliet_tests.py",
                                          mount_sourcedir=True, mount_sysroot=True, mount_builddir=True)

class BuildJulietCWE121(BuildJulietCWESubdir):
    projectName = "juliet-cwe-121"
    cwe_number = 121
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite, subdirectory="testcases/CWE121_Stack_Based_Buffer_Overflow")

class BuildJulietCWE126(BuildJulietCWESubdir):
    projectName = "juliet-cwe-126"
    cwe_number = 126
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite, subdirectory="testcases/CWE126_Buffer_Overread")