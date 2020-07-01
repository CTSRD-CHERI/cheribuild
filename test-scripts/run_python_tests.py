#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# Copyright (c) 2019 Alex Richardson
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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
from pathlib import Path

from run_tests_common import *


def run_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running Python tests")
    # Need the library path for libpython.so
    boot_cheribsd.prepend_ld_library_path(qemu, "/build")
    # When running the full test suite we want all python files in tmpfs:
    if args.full_test:
        # copy python libs from smb to tmpfs:
        install_prefix = Path(args.install_prefix)
        qemu.checked_run("time cp -a '{pfx}' '{pfx}.tmpfs'".format(pfx=install_prefix))
        qemu.checked_run("umount '{pfx}'".format(pfx=install_prefix))
        qemu.checked_run("rmdir '{pfx}' && mv '{pfx}.tmpfs' '{pfx}'".format(pfx=install_prefix))

    # run basic sanity check:
    build_python_exe = "python" + args.buildexe_suffix
    qemu.checked_run("/build/{} --version".format(build_python_exe))
    qemu.checked_run("/build/{} -E -c 'import sys; sys.exit(0)'".format(build_python_exe))

    if args.full_test:
        # Run the full test suite:
        qemu.checked_run("cd /build && ./{} -m test -v --junit-xml=python-tests.xml".format(build_python_exe))
    return True


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--buildexe-suffix", required=False, default="", help="Suffix for build executables")
    parser.add_argument("--full-test", action="store_true", required=False, dest="full_test",
                        help="Run the full python test suite")
    parser.add_argument("--smoketest", action="store_false", required=False, dest="full_test",
                        help="Don't run full python test suite, only check that a basic program works")


if __name__ == '__main__':
    # we don't need ssh running to execute the tests
    run_tests_main(test_function=run_tests, need_ssh=False, should_mount_builddir=True, should_mount_srcdir=True,
                   argparse_setup_callback=add_args)
