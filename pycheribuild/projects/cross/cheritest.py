#
# Copyright (c) 2020 Alex Richardson
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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
from ..project import DefaultInstallDir, GitRepository, MakeCommandKind, Project
from ..sail import BuildSailCheriMips


class _BuildCheriMipsTestBase(Project):
    do_not_add_to_targets = True
    target = "cheritest"
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheritest.git")
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.single_test = cls.add_config_option("single-test", help="Run a single test instead of all of them")
        cls.run_tests_with_build = cls.add_bool_option(
            "run-tests-with-build",
            help="Run tests as part of the --build step (option is useful " "for jenkins)",
            show_help=False,
            default=True,
        )

    def setup(self):
        super().setup()
        # CHERI_SDK is also used to find QEMU
        self.make_args.set(CHERI_SDK=self.config.cheri_sdk_dir)
        self.make_args.set(TEST_FPU=1)
        if self.single_test:
            self.make_args.set(CAP_SIZE=self.config.mips_cheri_bits)
            self.make_args.set_env(PYTEST_ADDOPTS="--color=yes")

    # Should run tests both for --test and --build
    def compile(self, **kwargs):
        if self.run_tests_with_build:
            self.do_cheritest()
        else:
            self.run_make("elfs128")
            self.run_make("elfs_mips")

    def run_tests(self):
        self.do_cheritest()

    def do_cheritest(self):
        raise NotImplementedError()


class BuildCheriMipsTestQEMU(_BuildCheriMipsTestBase):
    target = "cheritest-qemu"
    default_directory_basename = "cheritest"  # reuse source and build dirs
    dependencies = ("qemu",)

    def setup(self):
        super().setup()
        self.make_args.set(QEMU_CHERI128=self.config.qemu_bindir / "qemu-system-mips64cheri128")
        self.make_args.set(QEMU_MIPS64=self.config.qemu_bindir / "qemu-system-mips64")

    def do_cheritest(self):
        if self.single_test:
            self.run_make("pytest/qemu/tests/" + str(self.single_test), parallel=False)
        else:
            self.run_make("qemu_logs128")
            self.run_make("pytest_qemu128")
            self.run_make("qemu_logs_mips")
            self.run_make("pytest_qemu_mips")


class BuildCheriMipsTestSail(_BuildCheriMipsTestBase):
    target = "cheritest-sail"
    default_directory_basename = "cheritest"  # reuse source and build dirs
    dependencies = ("sail-cheri-mips",)

    def setup(self):
        super().setup()
        self.make_args.set(SAIL_CHERI_MIPS_DIR=BuildSailCheriMips.get_build_dir(self))

    def do_cheritest(self):
        if self.single_test:
            self.run_make("pytest/sail_cheri128_c/tests/" + str(self.single_test), parallel=False)
            self.run_make("pytest/sail_mips_c/tests/" + str(self.single_test), parallel=False)
        else:
            # Ignore ocaml version: nosetests_sail nosetests_sail_cheri nosetests_sail_cheri128
            self.run_make("nosetests_sail_cheri128_c")  # CHERI128
            self.run_make("nosetests_sail_mips_c")  # MIPS
