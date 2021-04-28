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
import os
from pathlib import Path

from run_tests_common import boot_cheribsd, run_tests_main


def setup_qtbase_tests(qemu: boot_cheribsd.CheriBSDInstance, _: argparse.Namespace):
    boot_cheribsd.set_ld_library_path_with_sysroot(qemu)
    boot_cheribsd.prepend_ld_library_path(qemu, "/build/lib")
    qemu.run("export QT_PLUGIN_PATH=/build/plugins")


def run_qtbase_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    print("Running qtbase tests")
    # Start with some basic smoketests:
    qemu.checked_run("/build/tests/auto/corelib/tools/qarraydata/tst_qarraydata")
    qemu.checked_run("/build/tests/auto/corelib/global/qtendian/tst_qtendian")

    failed_tests = []
    successful_tests = []
    starttime = datetime.datetime.now()
    for root, dirs, files in os.walk(str(args.build_dir) + "/tests/auto/corelib", topdown=True):
        for name in files:
            if not name.startswith("tst_") or name.endswith(".core"):
                continue
            f = Path(root, name)
            try:
                qemu.checked_run(str(f), timeout=5 * 60)
                successful_tests.append(f)
            except boot_cheribsd.CheriBSDCommandFailed as e:
                boot_cheribsd.failure("Failed to run ", f.name, ": ", str(e), exit=False)
                failed_tests.append(f)
                # Kill the process that timed out:
                qemu.sendintr()
                qemu.expect_prompt(timeout=60)
        # Ignore .moc and .obj directories:
        dirs[:] = [d for d in dirs if not d.startswith(".")]
    endtime = datetime.datetime.now()
    # TODO: -o /path/to/file,xunitxml
    boot_cheribsd.info("Ran " + str(len(successful_tests) + len(failed_tests)), " tests in ", (endtime - starttime))
    if failed_tests:
        boot_cheribsd.failure("The following ", len(failed_tests), " tests failed:\n\t",
                              "\n\t".join(x.name for x in failed_tests), exit=False)
    return not failed_tests


if __name__ == '__main__':
    # we don't need ssh running to execute the tests, but we do need the sysroot for libexecinfo+libelf
    run_tests_main(test_function=run_qtbase_tests, test_setup_function=setup_qtbase_tests,
                   need_ssh=False, should_mount_sysroot=True, should_mount_srcdir=True)
