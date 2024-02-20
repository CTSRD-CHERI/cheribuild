#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# Copyright (c) 2019 Alex Richardson
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
import shutil
from pathlib import Path

from run_tests_common import boot_cheribsd, junitparser, run_tests_main


def output_to_junit_suite(xml, output_path, suite_name, good=True):
    suite = junitparser.TestSuite(suite_name)

    with open(output_path, encoding="utf-8") as output_file:
        next(output_file)  # skip first header
        for line in output_file:
            if line[0] == "=":  # stop on next header
                break

            split = line.split()
            case = junitparser.TestCase(split[0])
            exit_code = int(split[1])

            if exit_code == 124:
                # timeout
                case.result = junitparser.Error(split[1])  # TODO error on timeout?
            elif good and exit_code != 0:
                # good run had bad exit code
                case.result = junitparser.Failure(split[1])
            elif not good and exit_code == 0:  # TODO we usually? expect a cheri exception
                # bad run had good exit code
                case.result = junitparser.Failure(split[1])

            suite.add_testcase(case)

    xml.add_testsuite(suite)


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--testcase-timeout", required=False, default="1s")
    parser.add_argument("--ld-preload-path", required=False, default=None)
    parser.add_argument(
        "--test-setup-command",
        action="append",
        dest="test_setup_commands",
        metavar="COMMAND",
        help="Run COMMAND as an additional test setup step before running the tests",
    )


def setup_juliet_test_environment(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    boot_cheribsd.set_ld_library_path_with_sysroot(qemu)
    if args.test_setup_commands:
        for command in args.test_setup_commands:
            qemu.checked_run(command)


def run_juliet_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    # args.ld_preload_path should be a path on the host
    if args.ld_preload_path:
        # hack until libcaprevoke is always present in cheribsd and can be added to the disk image via METALOG:
        # copy it into the runtime linker's search path from the sysroot
        qemu.checked_run("cp /sysroot/usr/libcheri/libcheri_caprevoke* /usr/libcheri")

        try:
            shutil.copy2(args.ld_preload_path, args.build_dir)
        except Exception as e:
            boot_cheribsd.failure("could not copy shared library for preload: ", e, exit=True)
            return False
        preload_path = Path(args.ld_preload_path)
        run_command = "/build/juliet-run.sh {} {}".format(args.testcase_timeout, "/build/" + preload_path.name)

    else:
        run_command = f"/build/juliet-run.sh {args.testcase_timeout}"

    build_dir = Path(args.build_dir)
    qemu.checked_run(run_command, ignore_cheri_trap=True, timeout=60000)
    xml = junitparser.JUnitXml()
    output_to_junit_suite(xml, build_dir / "bin" / "good.run", "good", True)
    output_to_junit_suite(xml, build_dir / "bin" / "bad.run", "bad", False)
    xml.write(build_dir / "results.xml")

    return True


if __name__ == "__main__":
    # we don't need ssh running to execute the tests, but we need both host and source dir mounted
    run_tests_main(
        test_function=run_juliet_tests,
        test_setup_function=setup_juliet_test_environment,
        argparse_setup_callback=add_args,
        need_ssh=False,
        should_mount_builddir=True,
        should_mount_srcdir=True,
        should_mount_sysroot=True,
    )
