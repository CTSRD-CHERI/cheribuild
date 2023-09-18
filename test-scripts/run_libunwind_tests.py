#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# Copyright (c) 2018 Alex Richardson
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
import argparse
import sys
import tempfile

import run_remote_lit_test
from run_tests_common import boot_cheribsd, run_tests_main


def setup_libunwind_env(qemu: boot_cheribsd.CheriBSDInstance, _: argparse.Namespace):
    # Ensure that the local libunwind.so is used instead of the system one
    qemu.checked_run("echo ln -sfv /build/lib/libunwind.so.1 /build/lib/libgcc_s.so.1")


def run_libunwind_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    common_args = dict(
        test_dirs=["libunwind/test"],
        llvm_lit_path=args.llvm_lit_path,
    )
    with tempfile.TemporaryDirectory(prefix="cheribuild-libunwind-tests-") as tempdir:
        # run the tests both for shared and static libunwind by setting -Denable_shared=
        # First static binaries
        static_everything_success = run_remote_lit_test.run_remote_lit_tests(
            "libunwind",
            qemu,
            args,
            tempdir,
            lit_extra_args=["-Dforce_static_executable=True", "-Denable_shared=False"],
            **common_args,
        )
        # dynamic binary with libunwind linked statically
        static_libunwind_success = run_remote_lit_test.run_remote_lit_tests(
            "libunwind",
            qemu,
            args,
            tempdir,
            lit_extra_args=["-Denable_shared=False"],
            **common_args,
        )
        # dynamic binary with libunwind linked shared
        shared_success = run_remote_lit_test.run_remote_lit_tests(
            "libunwind",
            qemu,
            args,
            tempdir,
            lit_extra_args=["-Denable_shared=True"],
            **common_args,
        )
        return static_libunwind_success and static_everything_success and shared_success


def add_cmdline_args(parser: argparse.ArgumentParser):
    # Only 10 tests, don't do the multiprocessing here
    run_remote_lit_test.add_common_cmdline_args(
        parser,
        default_xunit_output="qemu-libunwind-test-results.xml",
        allow_multiprocessing=False,
    )


def adjust_cmdline_args(args: argparse.Namespace):
    # We don't support parallel jobs but are reusing libcxx infrastructure -> set the expected vars
    args.internal_shard = None
    args.parallel_jobs = None
    run_remote_lit_test.adjust_common_cmdline_args(args)


if __name__ == "__main__":
    try:
        run_tests_main(
            test_function=run_libunwind_tests,
            need_ssh=True,  # we need ssh running to execute the tests
            argparse_setup_callback=add_cmdline_args,
            argparse_adjust_args_callback=adjust_cmdline_args,
            should_mount_sysroot=True,
            should_mount_builddir=True,
            test_setup_function=setup_libunwind_env,
        )
    finally:
        print("Finished running ", " ".join(sys.argv))
