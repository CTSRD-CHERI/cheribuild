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
import atexit
import pexpect
import argparse
import os
import subprocess
import tempfile
import time
import datetime
import signal
import sys
import threading
from multiprocessing import Process, Semaphore, Queue
from pathlib import Path
import boot_cheribsd
import run_remote_lit_test

def run_libunwind_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    # TODO: do I really want the sysroot mounted? or should I just copy libcxxrt.so.1 to the bindir
    # boot_cheribsd.run_cheribsd_command(qemu, "export LD_LIBRARY_PATH=/build/lib:/sysroot/lib:/sysroot/usr/lib", timeout=2)
    # boot_cheribsd.run_cheribsd_command(qemu, "export LD_CHERI_LIBRARY_PATH=/build/lib:/sysroot/libcheri:/sysroot/usr/libcheri", timeout=2)

    # Copy the libunwind library to both MIPS and CHERI library dirs so that it is picked up
    boot_cheribsd.checked_run_cheribsd_command(qemu, "ln -sfv /build/lib/libunwind.so* /usr/lib/")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "ln -sfv /build/lib/libunwind.so* /usr/libcheri/")
    # Also link libcxxrt from the sysroot to one of the default search paths
    boot_cheribsd.checked_run_cheribsd_command(qemu, "ln -sfv /sysroot/usr/lib/libcxxrt.so* /usr/lib/")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "ln -sfv /sysroot/usr/libcheri/libcxxrt.so* /usr/libcheri/")
    # libcxxrt links against libgcc_s which is libunwind:
    boot_cheribsd.checked_run_cheribsd_command(qemu, "ln -sfv /usr/lib/libunwind.so.1 /usr/lib/libgcc_s.so.1")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "ln -sfv /usr/libcheri/libunwind.so.1 /usr/libcheri/libgcc_s.so.1")

    with tempfile.TemporaryDirectory(prefix="cheribuild-libunwind-tests-") as tempdir:
        # run the tests both for shared and static libunwind by setting -Denable_shared=
        # TODO: this needs -lcompiler_rt
        # static_everything_success = run_remote_lit_test.run_remote_lit_tests("libunwind", qemu, args, tempdir,
        #                                                          lit_extra_args=["-Dforce_static_executable=True", "-Denable_shared=False"],
        #                                                          llvm_lit_path=args.llvm_lit_path)
        static_everything_success = True # TODO: run this
        static_libunwind_success = run_remote_lit_test.run_remote_lit_tests("libunwind", qemu, args, tempdir,
                                                                  lit_extra_args=["-Denable_shared=False"],
                                                                  llvm_lit_path=args.llvm_lit_path)
        shared_success = run_remote_lit_test.run_remote_lit_tests("libunwind", qemu, args, tempdir,
                                                                  lit_extra_args=["-Denable_shared=True"],
                                                                  llvm_lit_path=args.llvm_lit_path)
        return static_libunwind_success and static_everything_success and shared_success


def add_cmdline_args(parser: argparse.ArgumentParser):
    parser.add_argument("--lit-debug-output", action="store_true")
    parser.add_argument("--llvm-lit-path")
    parser.add_argument("--xunit-output", default="qemu-libunwind-test-results.xml")


def set_cmdline_args(args: argparse.Namespace):
    # We don't support parallel jobs but are reusing libcxx infrastructure -> set the expected vars
    args.internal_shard = None
    args.parallel_jobs = None


if __name__ == '__main__':
    from run_tests_common import run_tests_main
    try:
        run_tests_main(test_function=run_libunwind_tests, need_ssh=True, # we need ssh running to execute the tests
                       argparse_setup_callback=add_cmdline_args, argparse_adjust_args_callback=set_cmdline_args,
                       should_mount_sysroot=True, should_mount_builddir=True)
    finally:
        print("Finished running ", " ".join(sys.argv))
