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

class BODiagTestsuite(object):
    def __init__(self, name: str, xml: "junitparser.JUnitXml"):
        self.test_prefix = name
        self.min_suite = junitparser.TestSuite(name=name + "-min-overflow")
        xml.add_testsuite(self.min_suite)
        self.med_suite = junitparser.TestSuite(name=name + "-med-overflow")
        xml.add_testsuite(self.med_suite)
        self.large_suite = junitparser.TestSuite(name=name + "-large-overflow")
        xml.add_testsuite(self.large_suite)
        self.ok_suite = junitparser.TestSuite(name=name + "-in-bounds")
        xml.add_testsuite(self.ok_suite)
        self.error_suite = junitparser.TestSuite(name=name + "-test-broken")
        xml.add_testsuite(self.error_suite)

        # There are 291 tests, we want to check that all of them were run
        self.expected_test_names = []
        assert name in ("basic", "basic-heap")
        for i in range(291, 0, -1):
            prefix = "{}-{:0>5}".format(name, i)
            self.expected_test_names.append(prefix + "-min")
            self.expected_test_names.append(prefix + "-med")
            self.expected_test_names.append(prefix + "-large")
            self.expected_test_names.append(prefix + "-ok")

    def check_all_cases_parsed(self):
        for missing_test in self.expected_test_names:
            self.error("Could not find output file for test: ", missing_test)
            testcase = junitparser.TestCase(name=missing_test)
            testcase.result = junitparser.Error(message="Could not find output for test " + missing_test)
            self.error_suite.add_testcase(testcase)

    def error(self, *args):
        print(self.test_prefix, "ERROR:", *args, file=sys.stderr)

    def handle_testcase(self, o: Path):
        stem = o.stem
        assert stem.startswith(self.test_prefix), stem
        exit_code_str = o.read_text().rstrip()
        testcase = junitparser.TestCase(name=stem)
        try:
            index = self.expected_test_names.index(stem)
        except ValueError:
            self.error("Found output for unknown test: ", o)
            testcase.result = junitparser.Error(message="UNEXPECTED TEST NAME: " + o.name)
            testcase.system_out = exit_code_str
            self.error_suite.add_testcase(testcase)
            return
        # test has been handled -> remove from expected list
        del self.expected_test_names[index]
        if o.with_suffix(".stderr").exists():
            testcase.system_err = o.with_suffix(".stderr").read_text().rstrip()
        try:
            exit_code = int(exit_code_str)
        except ValueError:
            if exit_code_str == "skip" and stem.endswith("00183-large"):
                testcase.result = junitparser.Skipped(message="Skipped since the test needs too big cwd")
                self.large_suite.add_testcase(testcase)
            else:
                self.error("Malformed output for test: ", o)
                testcase.result = junitparser.Error(message="INVALID OUTPUT FILE CONTENTS: " + o.name)
                testcase.system_out = exit_code_str
                self.error_suite.add_testcase(testcase)
            return

        signaled = os.WIFSIGNALED(exit_code)
        exited = os.WIFEXITED(exit_code)
        testcase.system_out = "WIFSIGNALED={} WIFEXITED={}, WTERMSIG={}, WEXITSTATUS={} WCOREDUMP={}".format(
            signaled, exited, os.WTERMSIG(exit_code), os.WEXITSTATUS(exit_code), os.WCOREDUMP(exit_code))
        # -ok testcases are expected to run succesfully -> exit code zero
        if stem.endswith("-ok"):
            if not exited or os.WEXITSTATUS(exit_code) != 0:
                # This is not just a failure, it means something is seriously wrong if the good case fails
                self.error("One of the good test cases failed: ", o)
                testcase.result = junitparser.Error(message="Expected exit code 0 but got " + exit_code_str)
            self.ok_suite.add_testcase(testcase)
        else:
            # all others should crash
            if stem.endswith("-min"):
                suite = self.min_suite
            elif stem.endswith("-med"):
                suite = self.med_suite
            elif stem.endswith("-large"):
                suite = self.large_suite
            else:
                self.error("Malformed output for test: ", o)
                testcase.result = junitparser.Error(message="INVALID OUTPUT FILE FOUND: " + o.name)
                self.error_suite.add_testcase(testcase)
                return
            if not signaled:
                # test should fail with a signal: (162 for CHERI)
                # TODO: for CHERI check that it was signal 34?
                testcase.result = junitparser.Failure(message="Expected test to be killed by a SIGNAL but got exit code " + exit_code_str)
            suite.add_testcase(testcase)


def _create_junit_xml(builddir: Path, name):
    xml = junitparser.JUnitXml(name)

    # TODO: check that all cases exist (otherwise add an error)
    output_files = builddir.glob("run/*.out")
    sorted_files = []
    for o in output_files:
        sorted_files.append(str(o))
    sorted_files.sort()

    expected_test_names = []
    testsuite_basic = BODiagTestsuite("basic", xml)
    testsuite_heap = BODiagTestsuite("basic-heap", xml)
    # There are 291 tests, we want to check that all of them were run
    for base_prefix in ("basic", "basic-heap"):
        for i in range(291, 0, -1):
            prefix = "{}-{:0>5}".format(base_prefix, i)
            expected_test_names.append(prefix + "-min")
            expected_test_names.append(prefix + "-med")
            expected_test_names.append(prefix + "-large")
            expected_test_names.append(prefix + "-ok")

    for fullpath in sorted_files:
        o = Path(fullpath)
        if "-heap-" in o.stem:
            testsuite_heap.handle_testcase(o)
        else:
            testsuite_basic.handle_testcase(o)

    testsuite_basic.check_all_cases_parsed()
    testsuite_heap.check_all_cases_parsed()

    xml.update_statistics()
    # Older version of python only support str and not Path
    xml.write(str(builddir / "test-results.xml"), pretty=True)



def create_junit_xml(builddir, name):
    _create_junit_xml(builddir, name)
    test_output = Path(builddir, "test-results.xml")
    if not test_output.exists():
        boot_cheribsd.failure("Failed to create the JUnit XML file")
        return False
    # boot_cheribsd.run_host_command(["head", "-n2", str(test_output)])
    boot_cheribsd.run_host_command(["grep", "<testsuite", str(test_output)])
    return True


def run_bodiagsuite(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running BODiagSuite")

    if not args.junit_xml_only:
        boot_cheribsd.checked_run_cheribsd_command(qemu, "rm -rf /build/run")
        boot_cheribsd.checked_run_cheribsd_command(qemu, "cd /build && mkdir -p run")
        # Don't log all the CHERI traps while running (should speed up the tests a bit and produce shorter logfiles)
        boot_cheribsd.run_cheribsd_command(qemu, "sysctl machdep.log_cheri_exceptions=0 || true")
        boot_cheribsd.checked_run_cheribsd_command(qemu, "{} -r -f /build/Makefile.bsd-run all".format(args.bmake_path),
                                                   timeout=120*60, ignore_cheri_trap=True)
        # restore old behaviour
        boot_cheribsd.run_cheribsd_command(qemu, "sysctl machdep.log_cheri_exceptions=1 || true")

    if not create_junit_xml(Path(args.build_dir), args.junit_testsuite_name):
        return False
    return True


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--junit-xml-only", action="store_true")
    parser.add_argument("--bmake-path", default="make")
    parser.add_argument("--junit-testsuite-name", default="tests")
    parser.add_argument("--jobs", "-j", help="make jobs", type=int, default=1)


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
                cmd += ["-j", str(args.jobs)]
            boot_cheribsd.run_host_command(cmd, cwd=args.build_dir)
        if not create_junit_xml(Path(args.build_dir), args.junit_testsuite_name):
            sys.exit("Failed to create JUnit xml")
        sys.exit()

    from run_tests_common import run_tests_main
    # we don't need ssh running to execute the tests
    run_tests_main(test_function=run_bodiagsuite, need_ssh=False, should_mount_builddir=True,
                   argparse_setup_callback=add_args)