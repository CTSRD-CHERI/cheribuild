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
import boot_cheribsd
import os


def run_libcxxrt_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running libcxxrt tests")
    boot_cheribsd.run_cheribsd_command(qemu, "export LD_LIBRARY_PATH=/build/lib:/libunwind/lib:/sysroot/lib:/sysroot/usr/lib:/sysroot/usr/local/lib", timeout=2)
    boot_cheribsd.run_cheribsd_command(qemu, "export LD_CHERI_LIBRARY_PATH=/build/lib:/libunwind/lib:/sysroot/libcheri:/sysroot/usr/libcheri:/sysroot/usr/local/libcheri", timeout=2)
    boot_cheribsd.run_cheribsd_command(qemu, "export LIBUNWIND_PRINT_UNWINDING=1", timeout=2)
    boot_cheribsd.run_cheribsd_command(qemu, "export LIBUNWIND_PRINT_APIS=1", timeout=2)
    boot_cheribsd.run_cheribsd_command(qemu, "export LIBUNWIND_PRINT_DWARF=1", timeout=2)
    boot_cheribsd.checked_run_cheribsd_command(qemu, "'/build/bin/cxxrt-test-static' -v")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "'/build/bin/cxxrt-test-shared' -v")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "'/build/bin/cxxrt-test-foreign-exceptions' -v")
    return True


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--libunwind-build-dir", required=True)


def adjust_args(args: argparse.Namespace):
    args.build_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.build_dir)))
    args.smb_mount_directories.append(boot_cheribsd.SmbMount(args.libunwind_build_dir, readonly=True, in_target="/libunwind"))


if __name__ == '__main__':
    from run_tests_common import run_tests_main
    # we don't need ssh running to execute the tests
    run_tests_main(test_function=run_libcxxrt_tests, need_ssh=False, argparse_setup_callback=add_args,
                   argparse_adjust_args_callback=adjust_args, should_mount_builddir=True, should_mount_sysroot=True)
