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

from run_tests_common import boot_cheribsd, run_tests_main


def test_setup(qemu, _):
    boot_cheribsd.set_ld_library_path_with_sysroot(qemu)


def run_ctest_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running tests with ctest")
    ctest_args = ". --output-on-failure --progress --test-timeout " + str(args.test_timeout)
    if args.verbose:
        ctest_args = "-VV " + ctest_args
    # First list all tests and then try running them.
    qemu.checked_run("cd {} && /cmake/bin/ctest --show-only -V".format(args.build_dir), timeout=5 * 60)
    try:
        qemu.checked_run("cd {} && /cmake/bin/ctest {}".format(args.build_dir, ctest_args),
                         timeout=int(args.test_timeout * 1.05), pretend_result=0,
                         ignore_cheri_trap=args.ignore_cheri_trap)
    except boot_cheribsd.CheriBSDCommandFailed as e:
        boot_cheribsd.failure("Failed to run some tests: " + str(e), exit=False)
        return False
    return True


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--cmake-install-dir", help="Installation root for the CMake/CTest commands", required=True)
    parser.add_argument("--verbose", action="store_true", help="Enable verbose ctest output")
    parser.add_argument("--ignore-cheri-trap", action="store_true", required=False, default=True,
                        help="Don't fail the tests when a CHERI trap happens")


def adjust_args(args: argparse.Namespace):
    args.smb_mount_directories.append(
        boot_cheribsd.SmbMount(args.cmake_install_dir, readonly=True, in_target="/cmake"))


if __name__ == '__main__':
    # we don't need ssh running to execute the tests
    run_tests_main(test_function=run_ctest_tests, test_setup_function=test_setup,
                   need_ssh=False, argparse_setup_callback=add_args, argparse_adjust_args_callback=adjust_args,
                   should_mount_builddir=True, should_mount_srcdir=True, should_mount_sysroot=True)
