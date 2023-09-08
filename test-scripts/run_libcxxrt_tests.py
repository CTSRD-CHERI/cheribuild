#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# Copyright (c) 2019 Alex Richardson
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
import os

from run_tests_common import boot_cheribsd, run_tests_main


def run_libcxxrt_tests(qemu: boot_cheribsd.CheriBSDInstance, _: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running libcxxrt tests")
    boot_cheribsd.set_ld_library_path_with_sysroot(qemu)
    qemu.run("export LIBUNWIND_PRINT_UNWINDING=1", timeout=2)
    qemu.run("export LIBUNWIND_PRINT_APIS=1", timeout=2)
    qemu.run("export LIBUNWIND_PRINT_DWARF=1", timeout=2)
    # Add the libunwind library dirs so that the local one is picked up
    boot_cheribsd.prepend_ld_library_path(qemu, "/libunwind/lib")

    qemu.checked_run("'/build/bin/cxxrt-test-static' -v")
    qemu.checked_run("'/build/bin/cxxrt-test-foreign-exceptions' -v")
    qemu.checked_run("'/build/bin/cxxrt-test-shared' -v")

    # Check the test binaries linked against libunwind
    qemu.checked_run("'/build/bin/cxxrt-test-libunwind-static' -v")
    qemu.checked_run("'/build/bin/cxxrt-test-libunwind-allstatic' -v")
    qemu.checked_run("'/build/bin/cxxrt-test-libunwind-shared' -v")
    return True


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--libunwind-build-dir", required=True)


def adjust_args(args: argparse.Namespace):
    args.build_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.build_dir)))
    args.smb_mount_directories.append(
        boot_cheribsd.SmbMount(args.libunwind_build_dir, readonly=True, in_target="/libunwind"),
    )


if __name__ == "__main__":
    # we don't need ssh running to execute the tests
    run_tests_main(
        test_function=run_libcxxrt_tests,
        need_ssh=False,
        argparse_setup_callback=add_args,
        argparse_adjust_args_callback=adjust_args,
        should_mount_builddir=True,
        should_mount_sysroot=True,
    )
