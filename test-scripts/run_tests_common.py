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
import os
import sys
import typing
from pathlib import Path

_cheribuild_root = Path(__file__).parent.parent
_junitparser_dir = Path(__file__).parent.parent / "3rdparty/junitparser"
assert (_junitparser_dir / "junitparser/__init__.py").exists()
sys.path.insert(1, str(_junitparser_dir))
sys.path.insert(1, str(_cheribuild_root))
import junitparser
import pexpect
from pycheribuild import boot_cheribsd

__all__ = ["run_tests_main", "boot_cheribsd", "junitparser", "pexpect"]


def run_tests_main(test_function: typing.Callable[[boot_cheribsd.CheriBSDInstance, argparse.Namespace], bool] = None,
                   need_ssh=False, test_setup_function: typing.Callable[
            [boot_cheribsd.CheriBSDInstance, argparse.Namespace], None] = None, should_mount_builddir=True,
                   should_mount_srcdir=False, should_mount_sysroot=False, should_mount_installdir=False,
                   argparse_setup_callback: typing.Callable[[argparse.ArgumentParser], None] = None,
                   argparse_adjust_args_callback: typing.Callable[[argparse.Namespace], None] = None,
                   build_dir_in_target="/build"):
    def default_add_cmdline_args(parser: argparse.ArgumentParser):
        parser.add_argument("--build-dir", required=should_mount_builddir)
        parser.add_argument("--source-dir", required=should_mount_srcdir)
        parser.add_argument("--sysroot-dir", required=should_mount_sysroot)
        parser.add_argument("--install-destdir", required=should_mount_installdir)
        parser.add_argument("--install-prefix", required=should_mount_installdir)
        if argparse_setup_callback:
            argparse_setup_callback(parser)
        if not need_ssh:
            parser.add_argument("--force-ssh-setup", action="store_true", dest="__foce_ssh_setup")

    def default_setup_args(args: argparse.Namespace):
        if need_ssh:
            args.use_smb_instead_of_ssh = False  # we need ssh running to execute the tests
        else:
            args.use_smb_instead_of_ssh = True  # skip the ssh setup
            args.skip_ssh_setup = not args.__foce_ssh_setup
        if should_mount_builddir or args.build_dir:
            args.build_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.build_dir)))
            args.smb_mount_directories.append(
                boot_cheribsd.SmbMount(args.build_dir, readonly=False, in_target=build_dir_in_target))
        if should_mount_srcdir or args.source_dir:
            args.source_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.source_dir)))
            args.smb_mount_directories.append(
                boot_cheribsd.SmbMount(args.source_dir, readonly=True, in_target="/source"))
        if should_mount_sysroot or args.sysroot_dir:
            args.source_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.sysroot_dir)))
            args.smb_mount_directories.append(
                boot_cheribsd.SmbMount(args.sysroot_dir, readonly=True, in_target="/sysroot"))
        if should_mount_installdir or args.install_destdir:
            args.install_destdir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.install_destdir)))
            assert args.install_prefix and args.install_prefix[0] == "/"
            args.smb_mount_directories.append(
                boot_cheribsd.SmbMount(args.install_destdir + args.install_prefix, readonly=True,
                                       in_target=args.install_prefix))
        if argparse_adjust_args_callback:
            argparse_adjust_args_callback(args)

    def default_setup_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
        # Also link the build directory in the target under the host path. This should allow more tests to pass,
        # i.e. the libc++ filesystem tests, etc.
        if should_mount_builddir:
            assert args.build_dir
            # the host path might be too long and trigger the shell to emit a continuation line which really confuses
            # the pexpect logic.
            boot_cheribsd.run_cheribsd_command(qemu, "mkdir -p '{}'".format(Path(args.build_dir).parent))
            boot_cheribsd.checked_run_cheribsd_command(qemu, "ln -sf /build '{}'".format(args.build_dir), timeout=60)
            boot_cheribsd.success("Mounted build directory using host path")
        if should_mount_srcdir:
            assert args.source_dir
            # the host path might be too long and trigger the shell to emit a continuation line which really confuses
            # the pexpect logic.
            boot_cheribsd.run_cheribsd_command(qemu, "mkdir -p '{}'".format(Path(args.source_dir).parent))
            boot_cheribsd.checked_run_cheribsd_command(qemu, "ln -sf /source '{}'".format(args.source_dir), timeout=60)
            boot_cheribsd.success("Mounted source directory using host path")
        # Finally call the custom test setup function
        if test_setup_function:
            test_setup_function(qemu, args)

    assert sys.path[0] == str(Path(__file__).parent.absolute()), sys.path
    assert sys.path[1] == str(Path(__file__).parent.parent.absolute()), sys.path
    boot_cheribsd.main(test_function=test_function, test_setup_function=default_setup_tests,
                       argparse_setup_callback=default_add_cmdline_args,
                       argparse_adjust_args_callback=default_setup_args)
