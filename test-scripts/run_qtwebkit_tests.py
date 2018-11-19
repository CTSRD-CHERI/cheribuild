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
import subprocess
from pathlib import Path
import boot_cheribsd


def run_qtwebkit_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running QtWebkit tests")
    boot_cheribsd.run_cheribsd_command(qemu, "export LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib:/sysroot/lib:/sysroot/usr/lib:/sysroot/usr/local/lib")
    boot_cheribsd.run_cheribsd_command(qemu, "export LD_CHERI_LIBRARY_PATH=/usr/libcheri:/usr/local/libcheri:/sysroot/libcheri:/sysroot/usr/libcheri:/sysroot/usr/local/libcheri")
    boot_cheribsd.run_cheribsd_command(qemu, "ldd /build/bin/jsc")
    boot_cheribsd.run_cheribsd_command(qemu, "/build/bin/jsc --help")
    return True


if __name__ == '__main__':
    from run_tests_common import run_tests_main
    # we don't need ssh running to execute the tests, but we need both host and source dir mounted
    run_tests_main(test_function=run_qtwebkit_tests, need_ssh=False, should_mount_builddir=True,
                   should_mount_srcdir=True, should_mount_sysroot=True)
