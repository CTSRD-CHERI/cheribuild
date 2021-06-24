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

from run_tests_common import boot_cheribsd, junitparser, run_tests_main


def setup_qtbase_tests(qemu: boot_cheribsd.QemuCheriBSDInstance, args: argparse.Namespace):
    if args.junit_xml is None:
        args.junit_xml = Path(args.build_dir,
                              ("junit-results-" + datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S") + ".xml"))
    else:
        args.junit_xml = Path(args.junit_xml)
    assert args.junit_xml.parent.exists(), args.junit_xml
    qemu.run("export QT_PLUGIN_PATH=/build/plugins")
    # Running GDB to get stack traces sometimes causes freezes when reading the debug info from smbfs (could also be
    # extremely long wait times, I killed the test after about 10 minutes).
    # Disable stack traces for now since we can always run the crashing tests under gdb manually.
    qemu.run("export QTEST_DISABLE_STACK_DUMP=1")

    # tst_QDate::startOfDay_endOfDay(epoch) is broken in BST, use Europe/Oslo to match the official CI
    # Possibly similar to https://bugreports.qt.io/browse/QTBUG-87662
    qemu.run("export TZ=Europe/London")
    qemu.checked_run("cd /tmp")
    if not Path(args.build_dir, "tests/auto/corelib").is_dir():
        # Not running qtbase tests, set LD_LIBRARY_PATH to include QtBase libraries
        boot_cheribsd.set_ld_library_path_with_sysroot(qemu)
    if args.copy_libraries_to_tmpfs:
        try:
            copy_qt_libs_to_tmpfs_and_set_libpath(qemu, args)
        except boot_cheribsd.CheriBSDCommandTimeout as e:
            boot_cheribsd.failure("Timeout copying Qt libraries, will try to use smbfs instead: ", e, exit=False)
            # Send CTRL+C in case the process timed out.
            qemu.sendintr()
            qemu.sendintr()
            qemu.expect_prompt(timeout=5*60)
            boot_cheribsd.prepend_ld_library_path(qemu, "/build/lib")
    else:
        # otherwise load the libraries from smbfs
        boot_cheribsd.prepend_ld_library_path(qemu, "/build/lib")


def copy_qt_libs_to_tmpfs_and_set_libpath(qemu: boot_cheribsd.QemuCheriBSDInstance, args):
    # Copy the libraries to tmpfs to avoid long loading times over smbfs
    qemu.checked_run("mkdir /tmp/qt-libs")
    num_libs = 0
    for lib in sorted(Path(args.build_dir, "lib").glob("*.so*")):
        if lib.name.endswith(".debug"):
            continue  # don't copy the debug info files, they are huge
        if lib.is_symlink():
            # don't use cp to copy a symlink from smbfs, this takes many seconds
            linkpath = os.readlink(str(lib))
            if os.path.pathsep in linkpath:
                boot_cheribsd.failure("Unexpected link path for ", lib.absolute(), ": ", linkpath, exit=False)
                continue
            qemu.checked_run("ln -sfn {} /tmp/qt-libs/{}".format(linkpath, lib.name))
        else:
            if args.copy_libraries_to_tmpfs_using_scp:
                qemu.scp_to_guest(lib, "/tmp/qt-libs/" + lib.name)
            else:
                qemu.checked_run("cp -fav /build/lib/{} /tmp/qt-libs/".format(lib.name))
            num_libs += 1
    boot_cheribsd.success("Copied ", num_libs, " files to tmpfs")
    boot_cheribsd.prepend_ld_library_path(qemu, "/tmp/qt-libs")


def run_subdir(qemu: boot_cheribsd.CheriBSDInstance, subdir: Path, xml: junitparser.JUnitXml, build_dir: Path):
    tests = []
    for root, dirs, files in os.walk(str(subdir), topdown=True):
        for name in files:
            if not name.startswith("tst_") or "." in name:  # should not have a file extension
                continue
            tests.append(Path(root, name))
        # Ignore .moc and .obj directories:
        dirs[:] = [d for d in dirs if not d.startswith(".")]
    # Ensure that we run the tests in a reproducible order
    for f in sorted(tests):
        test_xml = f.parent / (f.name + ".xml")
        starttime = datetime.datetime.utcnow()
        try:
            # Output textual results to stdout and write JUnit XML to /build/test.xml
            # Many of the test cases expect that the CWD == test binary dir
            qemu.checked_run("cd {test_dir} && rm -f {xml_name} && {test} -o {xml_name},junitxml -o -,txt -v1 && "
                             "fsync {xml_name}".format(xml_name=test_xml.name, test_dir=f.parent, test=f),
                             timeout=10 * 60)
        except boot_cheribsd.CheriBSDCommandFailed as e:
            boot_cheribsd.failure("Failed to run ", f.name, ": ", str(e), exit=False)
            # Send CTRL+C in case the process timed out.
            qemu.sendintr()
            qemu.sendintr()
            qemu.expect_prompt(timeout=5*60)
        try:
            endtime = datetime.datetime.utcnow()
            qt_test = junitparser.JUnitXml.fromfile(str(test_xml))
            boot_cheribsd.info("Results for ", f.name, ": ", qt_test)
            if not isinstance(qt_test, junitparser.TestSuite):
                raise ValueError("Got unexpected parse result loading JUnit Xml: " + qt_test.tostring())
            if qt_test.tests < 1:
                raise ValueError("No test found in: " + qt_test.tostring())
            if not qt_test.time:
                qt_test.time = (endtime - starttime).total_seconds()
            qt_test.add_property("test_executable", str(f))
            xml.add_testsuite(qt_test)
        except Exception as e:
            boot_cheribsd.failure("Error loading JUnit result for ", f.name, ": ", str(e), exit=False)
            add_junit_failure(xml, f, str(e), starttime, test_xml)


def add_junit_failure(xml: junitparser.JUnitXml, test: Path, message: str, starttime: datetime.datetime,
                      input_xml: Path):
    t = junitparser.TestCase(name=test.name)
    t.result = junitparser.Failure(message=str(message))
    t.time = (datetime.datetime.utcnow() - starttime).total_seconds()
    if input_xml.exists():
        t.system_err = input_xml.read_text("utf-8")
    suite = junitparser.TestSuite(name=test.name)
    suite.add_property("test_executable", str(test))
    suite.add_testcase(t)
    suite.update_statistics()
    xml.add_testsuite(suite)


def run_qtbase_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    # TODO: also run the non-corelib tests
    xml = junitparser.JUnitXml()
    build_dir = Path(args.build_dir)
    all_tests_starttime = datetime.datetime.utcnow()
    test_subset = Path(args.test_subset)
    tests_root = Path(build_dir, "tests/auto")
    relpath = os.path.relpath(str(Path(tests_root, test_subset)), str(tests_root))
    assert not relpath.startswith(os.path.pardir), "Invalid path " + str(tests_root / test_subset)
    boot_cheribsd.info("Running qtbase tests for ", test_subset)

    # Start with a basic smoketests:
    if (tests_root / "corelib").is_dir():
        # For QtBase:
        qemu.checked_run("ldd /build/tests/auto/corelib/tools/qarraydata/tst_qarraydata")
        qemu.checked_run("/build/tests/auto/corelib/tools/qarraydata/tst_qarraydata")
    else:
        # Run ldd on the first test binary
        for i in tests_root.rglob("tst_*"):
            if i.suffix:
                continue  # don't try running .core/.xml files
            qemu.checked_run("ldd " + str(i))
            qemu.checked_run(str(i) + " --help")
            break

    run_subdir(qemu, Path(tests_root, test_subset), xml, build_dir=build_dir)
    xml.time = (datetime.datetime.utcnow() - all_tests_starttime).total_seconds()
    xml.update_statistics()
    failed_test_suites = []
    num_testsuites = 0
    for suite in xml:
        assert isinstance(suite, junitparser.TestSuite)
        num_testsuites += 1
        if suite.errors > 0 or suite.failures > 0:
            failed_test_suites.append(suite)
    boot_cheribsd.info("JUnit results:", xml)
    boot_cheribsd.info("Ran " + str(num_testsuites), " test suites in ",
                       (datetime.datetime.utcnow() - all_tests_starttime))
    if failed_test_suites:
        def failed_test_info(ts: junitparser.TestSuite):
            result = ts.name

            if ts.failures:
                result += " " + str(ts.failures) + " failures"
            if ts.errors:
                result += " " + str(ts.errors) + " errors"
            if ts.tests:
                result += " in " + str(ts.tests) + " tests"
            for p in ts.properties():
                if p.name == "test_executable":
                    result += ", executable=" + p.value
                    break
            return result

        boot_cheribsd.failure("The following ", len(failed_test_suites), " tests failed:\n\t",
                              "\n\t".join(failed_test_info(x) for x in failed_test_suites), exit=False)
    else:
        boot_cheribsd.success("All ", xml.tests, " tests (", num_testsuites, " test suites) passed after ",
                              (datetime.datetime.utcnow() - all_tests_starttime))
    # Finally, write the Junit XML file:
    if not boot_cheribsd.PRETEND:
        xml.write(args.junit_xml, pretty=True)
    boot_cheribsd.info("Wrote Junit results to ", args.junit_xml)
    return not failed_test_suites


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--test-subset", required=False, default=".",
                        help="Subset of tests to run (set to '.' to run all tests)")
    parser.add_argument("--junit-xml", required=False, help="Output file name for the JUnit XML results")
    # Note: Copying to tmpfs is not enabled by default since it currently hangs/is very slow on purecap RISC-V.
    parser.add_argument("--copy-libraries-to-tmpfs", action="store_true", dest="copy_libraries_to_tmpfs", default=True,
                        help="Copy the Qt libraries to tmpfs first instead of loading them from smbfs")
    # For now use `scp` to copy the libraries instead of a `cp` from smbfs since the cp appears to hang.
    parser.add_argument("--copy-libraries-to-tmpfs-using-scp", action="store_true",
                        dest="copy_libraries_to_tmpfs_using_scp", default=True,
                        help="Copy the Qt libraries to tmpfs using scp instead of smbfs")
    parser.add_argument("--copy-libraries-to-tmpfs-from-smbfs", action="store_false",
                        dest="copy_libraries_to_tmpfs_using_scp",
                        help="Copy the Qt libraries to tmpfs using scp instead of smbfs")
    parser.add_argument("--no-copy-libraries-to-tmpfs", action="store_false", dest="copy_libraries_to_tmpfs",
                        help="Copy the Qt libraries to tmpfs first instead of loading them from smbfs")


if __name__ == '__main__':
    # we don't need ssh running to execute the tests, but we do need the sysroot for libexecinfo+libelf
    run_tests_main(test_function=run_qtbase_tests, test_setup_function=setup_qtbase_tests,
                   argparse_setup_callback=add_args, need_ssh=True,
                   should_mount_sysroot=False, should_mount_srcdir=True)
