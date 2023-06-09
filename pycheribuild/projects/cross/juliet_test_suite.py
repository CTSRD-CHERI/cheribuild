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

from .crosscompileproject import (
    BuildType,
    CompilationTargets,
    CrossCompileCMakeProject,
    DefaultInstallDir,
    GitRepository,
)
from ..project import ReuseOtherProjectRepository


class BuildJulietTestSuite(CrossCompileCMakeProject):
    target = "juliet-test-suite"
    # TODO: move repo to CTSRD-CHERI
    repository = GitRepository("https://github.com/arichardson/juliet-test-suite-c.git")
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS
    default_build_type = BuildType.DEBUG

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

    def process(self):
        if self.build_type != BuildType.DEBUG:
            self.warning("The Juliet test suite contains undefined behaviour that might be optimized away unless "
                         "you compile at -O0.")
            self.ask_for_confirmation("Are you sure you want to continue?")
        super().process()

    def configure(self, **kwargs):
        pass

    def install(self, *args, **kwargs):
        self.fatal("Should not be called")

    def run_tests(self):
        pass


class BuildJulietCWESubdir(CrossCompileCMakeProject):
    do_not_add_to_targets = True
    cwe_number = None
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    cwe_warning_flags = []
    cwe_setup_commands = []

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.testcase_timeout = cls.add_config_option("testcase-timeout", kind=str)
        cls.ld_preload_path = cls.add_config_option("ld-preload-path", kind=str)

    def setup(self):
        super().setup()
        for flag in self.cwe_warning_flags:
            self.cross_warning_flags.append(flag)

    def configure(self, **kwargs):
        self.add_cmake_options(PLACE_OUTPUT_IN_TOPLEVEL_DIR=False)
        self.create_symlink(self.source_dir / "../../CMakeLists.txt", self.source_dir / "CMakeLists.txt")
        super().configure(**kwargs)

    def install(self, *args, **kwargs):
        self.fatal("Should not be called")

    def run_tests(self):
        if self.compiling_for_host():
            self.warning("Not implemented yet")
            return

        args = []
        if self.testcase_timeout:
            args.append("--testcase-timeout")
            args.append(self.testcase_timeout)
        if self.ld_preload_path:
            args.append("--ld-preload-path")
            args.append(self.ld_preload_path)

        for cmd in self.cwe_setup_commands:
            args.append("--test-setup-command=" + cmd)

        # For stdin redirection
        args.append("--test-setup-command=touch /tmp/in.txt")

        self.target_info.run_cheribsd_test_script("run_juliet_tests.py", *args, mount_sourcedir=True,
                                                  mount_sysroot=True, mount_builddir=True)


class BuildJulietCWE121(BuildJulietCWESubdir):
    target = "juliet-cwe-121"
    cwe_number = 121
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE121_Stack_Based_Buffer_Overflow")
    cwe_setup_commands = [
                "echo 500 > /tmp/in.txt",
            ]


class BuildJulietCWE122(BuildJulietCWESubdir):
    target = "juliet-cwe-122"
    cwe_number = 122
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE122_Heap_Based_Buffer_Overflow")
    cwe_setup_commands = [
                "echo 500 > /tmp/in.txt",
            ]


class BuildJulietCWE124(BuildJulietCWESubdir):
    target = "juliet-cwe-124"
    cwe_number = 124
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE124_Buffer_Underwrite")
    cwe_setup_commands = [
                "echo -500 > /tmp/in.txt",
            ]


class BuildJulietCWE126(BuildJulietCWESubdir):
    target = "juliet-cwe-126"
    cwe_number = 126
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE126_Buffer_Overread")
    cwe_setup_commands = [
                "echo 500 > /tmp/in.txt",
            ]


class BuildJulietCWE127(BuildJulietCWESubdir):
    target = "juliet-cwe-127"
    cwe_number = 127
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE127_Buffer_Underread")
    cwe_setup_commands = [
                "echo -500 > /tmp/in.txt",
            ]


class BuildJulietCWE134(BuildJulietCWESubdir):
    target = "juliet-cwe-134"
    cwe_number = 134
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE134_Uncontrolled_Format_String")
    cwe_warning_flags = ["-Wno-error=format-security"]
    cwe_setup_commands = [
                "export ADD=%s%d%s",
                "echo Format string: %s %d %s > /tmp/file.txt",
                "echo Format string: %s %d %s > /tmp/in.txt",
            ]


class BuildJulietCWE188(BuildJulietCWESubdir):
    target = "juliet-cwe-188"
    cwe_number = 188
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE188_Reliance_on_Data_Memory_Layout")

    def setup(self):
        super().setup()
        if self.compiling_for_cheri():
            self.CFLAGS.append("-cheri-bounds=subobject-safe")


class BuildJulietCWE415(BuildJulietCWESubdir):
    target = "juliet-cwe-415"
    cwe_number = 415
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite, subdirectory="testcases/CWE415_Double_Free")


class BuildJulietCWE416(BuildJulietCWESubdir):
    target = "juliet-cwe-416"
    cwe_number = 416
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite, subdirectory="testcases/CWE416_Use_After_Free")


class BuildJulietCWE587(BuildJulietCWESubdir):
    target = "juliet-cwe-587"
    cwe_number = 587
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE587_Assignment_of_Fixed_Address_to_Pointer")


class BuildJulietCWE588(BuildJulietCWESubdir):
    target = "juliet-cwe-588"
    cwe_number = 588
    repository = ReuseOtherProjectRepository(
            BuildJulietTestSuite,
            subdirectory="testcases/CWE588_Attempt_to_Access_Child_of_Non_Structure_Pointer")


class BuildJulietCWE680(BuildJulietCWESubdir):
    target = "juliet-cwe-680"
    cwe_number = 680
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite,
                                             subdirectory="testcases/CWE680_Integer_Overflow_to_Buffer_Overflow")


class BuildJulietCWE685(BuildJulietCWESubdir):
    target = "juliet-cwe-685"
    cwe_number = 685
    repository = ReuseOtherProjectRepository(
            BuildJulietTestSuite,
            subdirectory="testcases/CWE685_Function_Call_With_Incorrect_Number_of_Arguments")
    cwe_warning_flags = ["-Wno-error=format-insufficient-args"]


class BuildJulietCWE843(BuildJulietCWESubdir):
    target = "juliet-cwe-843"
    cwe_number = 843
    repository = ReuseOtherProjectRepository(BuildJulietTestSuite, subdirectory="testcases/CWE843_Type_Confusion")
