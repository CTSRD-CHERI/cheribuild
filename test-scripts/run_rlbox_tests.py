#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
import argparse
import stat
from pathlib import Path

from run_tests_common import boot_cheribsd, run_tests_main


def run_rlbox_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.set_ld_library_path_with_sysroot(qemu)
    # Note: CTest does not work with cross-compiled Catch2
    # Run all tests manually until https://github.com/catchorg/Catch2/issues/2223 is fixed
    failed_tests = []
    for f in Path(args.build_dir).iterdir():
        if not f.name.startswith("test_") or f.name.endswith(".core"):
            continue
        if (f.stat().st_mode & stat.S_IXUSR) == 0:
            continue
        try:
            qemu.checked_run(f"cd {args.build_dir} && ./{f.name}", timeout=5 * 60)
        except boot_cheribsd.CheriBSDCommandFailed as e:
            boot_cheribsd.failure("Failed to run ", f, ": ", str(e), exit=False)
            failed_tests.append(f)
    if failed_tests:
        boot_cheribsd.failure("The following tests failed:\n\t", "\n\t".join(x.name for x in failed_tests), exit=False)
    return not failed_tests


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--verbose", action="store_true", help="Enable verbose ctest output")
    parser.add_argument(
        "--ignore-cheri-trap",
        action="store_true",
        required=False,
        default=True,
        help="Don't fail the tests when a CHERI trap happens",
    )


if __name__ == "__main__":
    # we don't need ssh running to execute the tests
    run_tests_main(
        test_function=run_rlbox_tests,
        need_ssh=False,
        argparse_setup_callback=add_args,
        should_mount_builddir=True,
        should_mount_srcdir=True,
        should_mount_sysroot=True,
    )
