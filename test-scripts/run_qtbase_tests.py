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

from run_tests_common import boot_cheribsd, run_tests_main, junitparser


def setup_qtbase_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    if args.junit_xml is None:
        args.junit_xml = Path(args.build_dir,
                              ("junit-results-" + datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S") + ".xml"))
    else:
        args.junit_xml = Path(args.junit_xml)
    assert args.junit_xml.parent.exists(), args.junit_xml
    boot_cheribsd.set_ld_library_path_with_sysroot(qemu)
    qemu.run("export QT_PLUGIN_PATH=/build/plugins")
    # Running GDB to get stack traces sometimes causes freezes when reading the debug info from smbfs (could also be
    # extremely long wait times, I killed the test after about 10 minutes).
    # Disable stack traces for now since we can always run the crashing tests under gdb manually.
    qemu.run("export QTEST_DISABLE_STACK_DUMP=1")

    # tst_QDate::startOfDay_endOfDay(epoch) is broken in BST, use Europe/Oslo to match the official CI
    # Possibly similar to https://bugreports.qt.io/browse/QTBUG-87662
    qemu.run("export TZ=Europe/London")
    qemu.checked_run("cd /tmp")
    if args.copy_libraries_to_tmpfs:
        try:
            copy_qt_libs_to_tmpfs_and_set_libpath(qemu, args)
        except boot_cheribsd.CheriBSDCommandTimeout as e:
            boot_cheribsd.failure("Timeout copying Qt libraries, will try to use smbfs instead", exit=False)
            # Send CTRL+C in case the process timed out.
            qemu.sendintr()
            qemu.sendintr()
            qemu.expect_prompt(timeout=5*60)
            boot_cheribsd.prepend_ld_library_path(qemu, "/build/lib")
    else:
        # otherwise load the libraries from smbfs
        boot_cheribsd.prepend_ld_library_path(qemu, "/build/lib")


def copy_qt_libs_to_tmpfs_and_set_libpath(qemu, args):
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
            qemu.checked_run("cp -fav /build/lib/{} /tmp/qt-libs/".format(lib.name))
            num_libs += 1
    boot_cheribsd.success("Copied ", num_libs, " files to tmpfs")
    boot_cheribsd.prepend_ld_library_path(qemu, "/tmp/qt-libs")


def run_subdir(qemu: boot_cheribsd.CheriBSDInstance, subdir: Path, xml: junitparser.JUnitXml,
               successful_tests: list, failed_tests: list, build_dir: Path):
    tests = []
    for root, dirs, files in os.walk(str(subdir), topdown=True):
        for name in files:
            if not name.startswith("tst_") or name.endswith(".core"):
                continue
            tests.append(Path(root, name))
        # Ignore .moc and .obj directories:
        dirs[:] = [d for d in dirs if not d.startswith(".")]
    # Ensure that we run the tests in a reproducible order
    test_xml = build_dir / "test.xml"
    for f in sorted(tests):
        starttime = datetime.datetime.utcnow()
        try:
            # Output textual results to stdout and write JUnit XML to /build/test.xml
            qemu.checked_run("rm -f /build/test.xml && "
                             "{} -o /build/test.xml,junitxml -o -,txt -v1 && "
                             "fsync /build/test.xml".format(f),
                             timeout=10 * 60)
            successful_tests.append(f)
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
            xml.add_testsuite(qt_test)
        except Exception as e:
            boot_cheribsd.failure("Error loading JUnit result for", f.name, ": ", str(e), exit=False)
            failed_tests.append(f)
            add_junit_failure(xml, f, str(e), starttime, test_xml)
        finally:
            if test_xml.is_file():
                test_xml.unlink()


def add_junit_failure(xml: junitparser.JUnitXml, test: Path, message: str, starttime: datetime.datetime,
                      input_xml: Path):
    t = junitparser.TestCase(name=test.name)
    t.result = junitparser.Failure(message=str(message))
    t.time = (datetime.datetime.utcnow() - starttime).total_seconds()
    if input_xml.exists():
        t.system_err = input_xml.read_text("utf-8")
    suite = junitparser.TestSuite(name=test.name)
    suite.add_testcase(t)
    xml.add_testsuite(suite)


def run_qtbase_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    # TODO: also run the non-corelib tests
    xml = junitparser.JUnitXml()
    failed_tests = []
    successful_tests = []

    build_dir = Path(args.build_dir)
    all_tests_starttime = datetime.datetime.utcnow()
    test_subset = Path(args.test_subset)
    tests_root = Path(build_dir, "tests/auto")
    relpath = os.path.relpath(str(Path(tests_root, test_subset)), str(tests_root))
    assert not relpath.startswith(os.path.pardir), "Invalid path " + str(tests_root / test_subset)
    boot_cheribsd.info("Running qtbase tests for ", test_subset)

    # Start with some basic smoketests:
    qemu.checked_run("/build/tests/auto/corelib/tools/qarraydata/tst_qarraydata")
    qemu.checked_run("/build/tests/auto/corelib/global/qtendian/tst_qtendian")

    run_subdir(qemu, Path(tests_root, test_subset), xml, build_dir=build_dir,
               successful_tests=successful_tests, failed_tests=failed_tests)
    xml.time = (datetime.datetime.utcnow() - all_tests_starttime).total_seconds()
    xml.update_statistics()
    boot_cheribsd.info("JUnit results:", xml)
    boot_cheribsd.info("Ran " + str(len(successful_tests) + len(failed_tests)), " tests in ",
                       (datetime.datetime.utcnow() - all_tests_starttime))
    if failed_tests:
        boot_cheribsd.failure("The following ", len(failed_tests), " tests failed:\n\t",
                              "\n\t".join(x.name for x in failed_tests), exit=False)

    # Finally, write the Junit XML file:
    if not boot_cheribsd.PRETEND:
        xml.write(args.junit_xml, pretty=True)
    boot_cheribsd.info("Wrote Junit results to ", args.junit_xml)
    return not failed_tests


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--test-subset", required=False, default="corelib/tools",
                        help="Subset of tests to run (set to '.' to run all tests)")
    parser.add_argument("--junit-xml", required=False, help="Output file name for the JUnit XML results")
    # Note: Copying to tmpfs is not enabled by default since it currently hangs/is very slow on purecap RISC-V.
    parser.add_argument("--copy-libraries-to-tmpfs", action="store_true", dest="copy_libraries_to_tmpfs", default=False,
                        help="Copy the Qt libraries to tmpfs first instead of loading them from smbfs")
    parser.add_argument("--no-copy-libraries-to-tmpfs", action="store_false", dest="copy_libraries_to_tmpfs",
                        help="Copy the Qt libraries to tmpfs first instead of loading them from smbfs")


if __name__ == '__main__':
    # we don't need ssh running to execute the tests, but we do need the sysroot for libexecinfo+libelf
    run_tests_main(test_function=run_qtbase_tests, test_setup_function=setup_qtbase_tests,
                   argparse_setup_callback=add_args, need_ssh=False,
                   should_mount_sysroot=True, should_mount_srcdir=True)
