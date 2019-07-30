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
import pexpect
import sys
from pathlib import Path
import boot_cheribsd


def run_noop_test(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    boot_cheribsd.success("Booted successfully")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "kenv")
    # unchecked since mount_smbfs returns non-zero for --help:
    boot_cheribsd.run_cheribsd_command(qemu, "mount_smbfs --help", cheri_trap_fatal=True)
    # same for ld-cheri-elf.so (but do check for CHERI traps):
    boot_cheribsd.run_cheribsd_command(qemu, "/libexec/ld-cheri-elf.so.1 -h", cheri_trap_fatal=True)

    # potentially bootstrap kyua for later testing
    if args.bootstrap_kyua:
        boot_cheribsd.checked_run_cheribsd_command(qemu, "/sbin/prepare-testsuite.sh", timeout=20 * 60)
        boot_cheribsd.checked_run_cheribsd_command(qemu, "kyua help", timeout=60)

    poweroff_start = datetime.datetime.now()
    qemu.sendline("poweroff")
    i = qemu.expect(["Uptime:", pexpect.TIMEOUT, pexpect.EOF] + boot_cheribsd.FATAL_ERROR_MESSAGES, timeout=120)
    if i != 0:
        boot_cheribsd.failure("Poweroff " + ("timed out" if i == 1 else "failed"))
        return False
    i = qemu.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=120)  # 120 secs since it takes a lot longer on a full image
    if i == 0:
        boot_cheribsd.failure("QEMU didn't exit after shutdown!")
        return False
    boot_cheribsd.success("Poweroff took: ", datetime.datetime.now() - poweroff_start)
    return True


def test_boot_setup_args(args: argparse.Namespace):
    args.use_smb_instead_of_ssh = True  # skip the ssh setup
    args.skip_ssh_setup = True

def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--bootstrap-kyua", action="store_true",
                        help="Install kyua using the /sbin/prepare-testsuite.sh script")


if __name__ == '__main__':
    import boot_cheribsd
    assert Path(sys.path[0]).resolve() == Path(__file__).parent.resolve(), sys.path
    # we don't need to setup ssh config/authorized_keys to test the boot
    boot_cheribsd.main(test_function=run_noop_test, argparse_setup_callback=add_args,
                       argparse_adjust_args_callback=test_boot_setup_args)
