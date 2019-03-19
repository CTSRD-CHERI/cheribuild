#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
# -
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
# runtests.py - run FreeBSD tests and export them to a tarfile via a disk
# device.
#
import argparse
import boot_cheribsd
import junitparser
import os
import sys
from pathlib import Path


def _create_junit_xml(builddir: Path, name):
    xml = junitparser.JUnitXml(name)
    min_suite = junitparser.TestSuite(name=name + "-min-overflow")
    xml.add_testsuite(min_suite)
    med_suite = junitparser.TestSuite(name=name + "-med-overflow")
    xml.add_testsuite(med_suite)
    large_suite = junitparser.TestSuite(name=name + "-large-overflow")
    xml.add_testsuite(large_suite)
    ok_suite = junitparser.TestSuite(name=name + "-in-bounds")
    xml.add_testsuite(ok_suite)
    error_suite = junitparser.TestSuite(name=name + "-test-broken")
    xml.add_testsuite(error_suite)

    # TODO: check that all cases exist (otherwise add an error)
    output_files = builddir.glob("run/*.out")
    sorted_files = []
    for o in output_files:
        sorted_files.append(str(o))
    sorted_files.sort()

    expected_test_names = []
    # There are 291 tests, we want to check that all of them were run
    for i in range(291, 0, -1):
        prefix = "basic-{:0>5}".format(i)
        expected_test_names.append(prefix + "-min")
        expected_test_names.append(prefix + "-med")
        expected_test_names.append(prefix + "-large")
        expected_test_names.append(prefix + "-ok")

    for fullpath in sorted_files:
        o = Path(fullpath)
        exit_code_str = o.read_text()
        stem = o.stem
        testcase = junitparser.TestCase(name=stem)
        try:
            index = expected_test_names.index(stem)
        except ValueError:
            print("ERROR: Found output for unknown test: ", o, file=sys.stderr)
            testcase.result = junitparser.Error(message="UNEXPECTED TEST NAME: " + o.name)
            testcase.system_err = exit_code_str
            error_suite.add_testcase(testcase)
            continue
        # test has been handled -> remove from expected list
        del expected_test_names[index]

        try:
            exit_code = int(exit_code_str)
        except ValueError:
            print("ERROR: Malformed output for test: ", o, file=sys.stderr)
            testcase.result = junitparser.Error(message="INVALID OUTPUT FILE CONTENTS: " + o.name)
            testcase.system_err = exit_code_str
            error_suite.add_testcase(testcase)
            continue

        signaled = os.WIFSIGNALED(exit_code)
        exited = os.WIFEXITED(exit_code)
        testcase.system_out = "WIFSIGNALED={} WIFEXITED={}, WTERMSIG={}, WEXITSTATUS={} WCOREDUMP={}".format(
            signaled, exited, os.WTERMSIG(exit_code), os.WEXITSTATUS(exit_code), os.WCOREDUMP(exit_code))
        # -ok testcases are expected to run succesfully -> exit code zero
        if stem.endswith("-ok"):
            if not exited or os.WEXITSTATUS(exit_code) != 0:
                testcase.result = junitparser.Failure(message="Expected exit code 0 but got " + exit_code_str)
                testcase.system_err = exit_code_str
            ok_suite.add_testcase(testcase)
        else:
            # all others should crash
            suite = None
            if stem.endswith("-min"):
                suite = min_suite
            elif stem.endswith("-med"):
                suite = med_suite
            elif stem.endswith("-large"):
                suite = large_suite
            else:
                print("ERROR: Found invalid test output: ", o, file=sys.stderr)
                testcase.result = junitparser.Error(message="INVALID OUTPUT FILE FOUND: " + o.name)
                error_suite.add_testcase(testcase)
                continue
            if not signaled:
                # TODO: for CHERI check that it was signal 34?
                testcase.result = junitparser.Failure(message="Expected test to be killed by a SIGNAL but got exit code" + exit_code_str)
                testcase.system_err = exit_code_str
            suite.add_testcase(testcase)
            # test should fail with a signal: (162 for CHERI)

    for missing_test in expected_test_names:
        print("ERROR: Could not find output file for test: ", missing_test, file=sys.stderr)
        testcase = junitparser.TestCase(name=missing_test)
        testcase.result = junitparser.Error(message="Could not find output for test " + missing_test)
        error_suite.add_testcase(testcase)

    xml.update_statistics()
    xml.write(builddir / "test-results.xml", pretty=True)



def create_junit_xml(builddir, name):
    _create_junit_xml(builddir, name)
    test_output = Path(builddir, "test-results.xml")
    if not test_output.exists():
        boot_cheribsd.failure("Failed to create the JUnit XML file")
        return False
    boot_cheribsd.run_host_command(["head", "-n2", str(test_output)])
    return True


def run_bodiagsuite(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running BODiagSuite")

    if not args.junit_xml_only:
        boot_cheribsd.checked_run_cheribsd_command(qemu, "{} -r -f /build/Makefile.bsd-run all".format(args.bmake_path),
                                                   timeout=60*60, ignore_cheri_trap=True)

    return True


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--junit-xml-only", action="store_true")
    parser.add_argument("--bmake-path", default="make")
    parser.add_argument("--junit-testsuite-name", default="tests")
    parser.add_argument("--jobs", "-j", help="make jobs", type=int, default=1)


# TODO: allow running native as well


if __name__ == '__main__':
    if "--junit-xml-only" in sys.argv or "--test-native" in sys.argv:
        parser = argparse.ArgumentParser()
        add_args(parser)
        parser.add_argument("--test-native", action="store_true")
        parser.add_argument("--build-dir")
        args, remaining = parser.parse_known_args()
        if args.test_native and not args.junit_xml_only:
            cmd = [args.bmake_path, "-r", "-f", args.build_dir + "/Makefile.bsd-run", "all"]
            if args.jobs > 1:
                cmd += ["-j", args.jobs]
            boot_cheribsd.run_host_command(cmd, cwd=args.build_dir)
        if not create_junit_xml(Path(args.build_dir), args.junit_testsuite_name):
            sys.exit("Failed to create JUnit xml")
        sys.exit()

    from run_tests_common import run_tests_main
    # we don't need ssh running to execute the tests
    run_tests_main(test_function=run_bodiagsuite, need_ssh=False, should_mount_builddir=True,
                   argparse_setup_callback=add_args)