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

from run_tests_common import boot_cheribsd, run_tests_main


def run_postgres_tests(qemu: boot_cheribsd.QemuCheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running PostgreSQL tests")
    if args.minimal_image:
        qemu.checked_run("ln -s /locale /usr/share/locale")
    # check that the locale files exist
    qemu.checked_run("ls /usr/share/locale/C.UTF-8")
    # TODO: copy over the logfile and enable coredumps?
    # Run tests with a two-hour timeout:
    qemu.checked_run(f"cd '{qemu.smb_dirs[0].in_target}' && sh -xe ./run-postgres-tests.sh", timeout=240 * 60)
    return True


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--locale-files-dir", required=True)


def adjust_args(args: argparse.Namespace):
    if args.minimal_image:
        args.smb_mount_directories.append(
            boot_cheribsd.SmbMount(args.locale_files_dir, readonly=True, in_target="/locale"),
        )


if __name__ == "__main__":
    # we don't need ssh running to execute the tests
    run_tests_main(
        test_function=run_postgres_tests,
        need_ssh=False,
        should_mount_builddir=False,
        argparse_setup_callback=add_args,
        argparse_adjust_args_callback=adjust_args,
    )
