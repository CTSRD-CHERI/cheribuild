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
import pexpect
import argparse
import os
import typing
import sys
from pathlib import Path

def run_tests_main(test_function: typing.Callable[[pexpect.spawn, argparse.Namespace], bool]=None, need_ssh=False,
                   should_mount_builddir=True, should_mount_srcdir=False,
                   argparse_setup_callback: typing.Callable[[argparse.ArgumentParser], None]=None,
                   argparse_adjust_args_callback: typing.Callable[[argparse.Namespace], None]=None):
    def default_add_cmdline_args(parser: argparse.ArgumentParser):
        if should_mount_builddir:
            parser.add_argument("--build-dir", required=True)
        if should_mount_srcdir:
            parser.add_argument("--source-dir", required=True)
        if argparse_setup_callback:
            argparse_setup_callback(parser)

    def default_setup_args(args: argparse.Namespace):
        if need_ssh:
            args.use_smb_instead_of_ssh = False  # we need ssh running to execute the tests
        else:
            args.use_smb_instead_of_ssh = True  # skip the ssh setup
        current_smb_index = 0
        if should_mount_builddir:
            args.build_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.build_dir)))
            args.smb_mount_directories.insert(current_smb_index,
                                              boot_cheribsd.SmbMount(args.build_dir, readonly=False, in_target="/build"))
            current_smb_index += 1
        if should_mount_srcdir:
            args.source_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.source_dir)))
            args.smb_mount_directories.insert(current_smb_index,
                                              boot_cheribsd.SmbMount(args.source_dir, readonly=True, in_target="/source"))
            current_smb_index += 1
        if argparse_adjust_args_callback:
            argparse_adjust_args_callback(args)

    import boot_cheribsd
    assert sys.path[0] == str(Path(__file__).parent.absolute()), sys.path
    boot_cheribsd.main(test_function=test_function, argparse_setup_callback=default_add_cmdline_args,
                       argparse_adjust_args_callback=default_setup_args)

