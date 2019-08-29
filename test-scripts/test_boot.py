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
import datetime
import functools
import os
import operator
import shlex

import pexpect
import sys
from pathlib import Path
from run_tests_common import *

def run_noop_test(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    boot_cheribsd.success("Booted successfully")
    qemu.checked_run("kenv")
    # unchecked since mount_smbfs returns non-zero for --help:
    qemu.run("mount_smbfs --help", cheri_trap_fatal=True)
    # same for ld-cheri-elf.so (but do check for CHERI traps):
    qemu.run("/libexec/ld-cheri-elf.so.1 -h", cheri_trap_fatal=True)


    try:
        # potentially bootstrap kyua for later testing
        if args.bootstrap_kyua or args.kyua_tests_files:
            qemu.checked_run("/sbin/prepare-testsuite.sh", timeout=20 * 60)
            qemu.checked_run("kyua help", timeout=60)

        for i, tests_file in enumerate(args.kyua_tests_files):
            # TODO: is the results file too big for tmpfs?
            qemu.checked_run("rm -f /tmp/results.db")
            # Allow up to 24 hours to run the full testsuite
            # Not a checked run since it might return false if some tests fail
            test_start = datetime.datetime.now()
            qemu.run("kyua test --results-file=/tmp/results.db -k {}".format(shlex.quote(tests_file)),
                     ignore_cheri_trap=True, cheri_trap_fatal=False, timeout=24 * 60 * 60)
            boot_cheribsd.success("Running tests for ", tests_file, " took: ", datetime.datetime.now() - test_start)

            if i == 0:
                results_file = "/kyua-results/test-results.db"
            else:
                results_file = "/kyua-results/test-results-{}.db".format(i)
            qemu.checked_run("cp -v /tmp/results.db {}".format(results_file))
            qemu.checked_run("fsync " + results_file)
    except boot_cheribsd.CheriBSDCommandFailed as e:
        boot_cheribsd.failure("Failed to run: " + str(e), exit=False)
        boot_cheribsd.info("Trying to shut down cleanly")


    if args.interact:
        boot_cheribsd.info("Skipping poweroff step since --interact was passed.")
        return True

    poweroff_start = datetime.datetime.now()
    qemu.sendline("poweroff")
    i = qemu.expect(["Uptime:", pexpect.TIMEOUT, pexpect.EOF] + boot_cheribsd.FATAL_ERROR_MESSAGES, timeout=120)
    if i != 0:
        boot_cheribsd.failure("Poweroff " + ("timed out" if i == 1 else "failed"))
        return False
    # 240 secs since it takes a lot longer on a full image (it took 44 seconds after installing kyua, so on a really
    # busy jenkins slave it might be a lot slower)
    i = qemu.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=240)
    if i == 0:
        boot_cheribsd.failure("QEMU didn't exit after shutdown!")
        return False
    boot_cheribsd.success("Poweroff took: ", datetime.datetime.now() - poweroff_start)
    return True


def test_boot_setup_args(args: argparse.Namespace):
    args.use_smb_instead_of_ssh = True  # skip the ssh setup
    args.skip_ssh_setup = True
    print(args)
    if args.kyua_tests_files:
        # flatten the list (https://stackoverflow.com/a/45323085/894271):
        args.kyua_tests_files = functools.reduce(operator.iconcat, args.kyua_tests_files, [])
        print(args.kyua_tests_files)
        for file in args.kyua_tests_files:
            if not Path(file).name == "Kyuafile":
                boot_cheribsd.failure("Expected a path to a Kyuafile but got: ", file)
        test_output_dir = Path(os.path.expandvars(os.path.expanduser(args.kyua_tests_output)))
        if not test_output_dir.is_dir():
            boot_cheribsd.failure("Output directory does not exist: ", test_output_dir)
        args.kyua_tests_output = str(test_output_dir)
        args.smb_mount_directories.append(boot_cheribsd.SmbMount(test_output_dir, readonly=False, in_target="/kyua-results"))

def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--bootstrap-kyua", action="store_true",
                        help="Install kyua using the /sbin/prepare-testsuite.sh script")
    parser.add_argument("--kyua-tests-files", action="append", nargs=argparse.ZERO_OR_MORE, default=[],
                        help="Run tests for the given following Kyuafile(s)")
    parser.add_argument("--kyua-tests-output", default=str(Path(".").resolve() / "kyua-results"),
                        help="Copy the kyua results.db to the following directory (it will be mounted with SMB)")

if __name__ == '__main__':
    # we don't need to setup ssh config/authorized_keys to test the boot
    run_tests_main(test_function=run_noop_test, argparse_setup_callback=add_args, should_mount_builddir=False,
                   argparse_adjust_args_callback=test_boot_setup_args)
