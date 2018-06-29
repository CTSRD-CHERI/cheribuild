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
import pexpect
boot_cheribsd = __import__("boot_cheribsd")

def run_qtbase_tests(qemu: pexpect.spawn, **kwargs):
    print("Running qtbase tests")
    boot_cheribsd.run_cheribsd_command(qemu, "mount_smbfs -I 10.0.2.4 -N //10.0.2.4/qemu /mnt",
                                       error_output="mount_smbfs: unable to open connection:")
    boot_cheribsd.run_cheribsd_command(qemu, "/mnt/tests/auto/corelib/global/qtendian/tst_qtendian")
    return True


def add_cmdline_args(parser: argparse.ArgumentParser):
    pass

def setup_args(args: argparse.Namespace):
    args.use_smb_instead_of_ssh = True

if __name__ == '__main__':
    boot_cheribsd.main(test_function=run_qtbase_tests, argparse_setup_callback=add_cmdline_args,
         argparse_adjust_args_callback=setup_args)