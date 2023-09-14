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
from pathlib import Path

from run_tests_common import boot_cheribsd, get_default_junit_xml_name, run_tests_main

from pycheribuild.utils import get_global_config


def test_setup(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    if not args.extra_library_paths:
        # If the used passed extra library paths assume that those are correct.
        # Otherwise, set up the default LD_LIBRARY_PATH to include the sysroot
        # and the libraries from the build directory.
        boot_cheribsd.set_ld_library_path_with_sysroot(qemu)
        # Prefer the files from the build directory over the sysroot.
        boot_cheribsd.prepend_ld_library_path(qemu, "/build/lib:/build/bin")
    # If the user supplied test setup steps, run them now.
    if args.test_setup_commands:
        for command in args.test_setup_commands:
            qemu.checked_run(command)

    # Update all references to CMAKE_COMMAND in the CTest file. Otherwise tests that use something like
    # `${CMAKE} -E copy_if_different ...` will fail.
    cmake_cache = Path(args.build_dir, "CMakeCache.txt")
    host_cmake_path = None
    if cmake_cache.is_file():
        with cmake_cache.open("rb") as f:
            for line in f.readlines():
                if line.startswith(b"CMAKE_COMMAND:INTERNAL="):
                    host_cmake_path = line[len(b"CMAKE_COMMAND:INTERNAL=") :].strip()
                    boot_cheribsd.info("Host CMake path is ", host_cmake_path)
                    break
    for ctest_file in Path(args.build_dir).rglob("CTestTestfile.cmake"):
        boot_cheribsd.info("Updating references to ${CMAKE_COMMAND} in ", ctest_file)
        ctest_contents = ctest_file.read_bytes()
        num_host_paths = ctest_contents.count(host_cmake_path)
        if num_host_paths > 0:
            if not host_cmake_path:
                boot_cheribsd.failure("Cannot update host CMake path in ", ctest_file, exit=True)
                continue
            new_contents = ctest_contents.replace(host_cmake_path, b"/cmake/bin/cmake")
            if not get_global_config().pretend:
                ctest_file.write_bytes(new_contents)
            boot_cheribsd.info("Updated ", num_host_paths, " references to ${CMAKE_COMMAND} in ", ctest_file)
    # Add CMake/CTest to $PATH
    qemu.checked_run("export PATH=$PATH:/cmake/bin")


def run_ctest_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running tests with ctest")
    ctest_args = ". --output-on-failure --test-timeout " + str(args.test_timeout)
    # Also write a junit XML result
    ctest_args += " --output-junit " + str(args.junit_xml)
    if args.verbose:
        ctest_args = "-VV " + ctest_args
    # First list all tests and then try running them.
    qemu.checked_run(f"cd {args.build_dir} && /cmake/bin/ctest --show-only -V", timeout=5 * 60)
    try:
        qemu.checked_run(
            f"cd {args.build_dir} && /cmake/bin/ctest {ctest_args}",
            timeout=int(args.test_timeout * 1.05),
            pretend_result=0,
            ignore_cheri_trap=args.ignore_cheri_trap,
        )
    except boot_cheribsd.CheriBSDCommandFailed as e:
        boot_cheribsd.failure("Failed to run some tests: " + str(e), exit=False)
        return False
    return True


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--cmake-install-dir", help="Installation root for the CMake/CTest commands", required=True)
    parser.add_argument("--junit-xml", required=False, help="Output file name for the JUnit XML results")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose ctest output")
    parser.add_argument(
        "--ignore-cheri-trap",
        action="store_true",
        required=False,
        default=True,
        help="Don't fail the tests when a CHERI trap happens",
    )
    parser.add_argument(
        "--test-setup-command",
        action="append",
        dest="test_setup_commands",
        metavar="COMMAND",
        help="Run COMMAND as an additional test setup step before running the tests",
    )


def adjust_args(args: argparse.Namespace):
    args.smb_mount_directories.append(boot_cheribsd.SmbMount(args.cmake_install_dir, readonly=True, in_target="/cmake"))
    args.junit_xml = get_default_junit_xml_name(args.junit_xml, args.build_dir)


if __name__ == "__main__":
    # we don't need ssh running to execute the tests
    run_tests_main(
        test_function=run_ctest_tests,
        test_setup_function=test_setup,
        need_ssh=False,
        argparse_setup_callback=add_args,
        argparse_adjust_args_callback=adjust_args,
        should_mount_builddir=True,
        should_mount_srcdir=True,
        should_mount_sysroot=True,
    )
