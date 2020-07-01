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
import functools
import operator
import os
import shlex
import shutil
import sys
import time
from pathlib import Path

from kyua_db_to_junit_xml import convert_kyua_db_to_junit_xml, fixup_kyua_generated_junit_xml
from run_tests_common import boot_cheribsd, run_tests_main, pexpect, CrossCompileTarget


def run_cheritest(qemu: boot_cheribsd.CheriBSDInstance, binary_name, args: argparse.Namespace) -> bool:
    try:
        qemu.checked_run("rm -f /tmp/{}.xml".format(binary_name))
        # Run it once with textual output (for debugging)
        # qemu.run("/bin/{} -a".format(binary_name, binary_name),
        #     ignore_cheri_trap=True, cheri_trap_fatal=False, timeout=5 * 60)
        # Generate JUnit XML:
        qemu.run("/bin/{} -a -x > /tmp/{}.xml".format(binary_name, binary_name),
                 ignore_cheri_trap=True, cheri_trap_fatal=False, timeout=5 * 60)
        qemu.sendline("echo EXITCODE=$?")
        qemu.expect(["EXITCODE=(\\d+)\r"], timeout=5, pretend_result=0)
        if boot_cheribsd.PRETEND:
            exit_code = 0
        else:
            print(qemu.match.groups())
            exit_code = int(qemu.match.group(1))
            qemu.expect_prompt()
        if qemu.smb_failed:
            boot_cheribsd.info("SMB mount has failed, performing normal scp")
            host_path = Path(args.test_output_dir, binary_name + ".xml")
            qemu.scp_from_guest("/tmp/{}.xml".format(binary_name), host_path)
        else:
            qemu.checked_run("mv -f /tmp/{}.xml /test-results/{}.xml".format(binary_name, binary_name))
            qemu.run("fsync /test-results/{}.xml".format(binary_name))
        return exit_code == 0
    except boot_cheribsd.CheriBSDCommandTimeout as e:
        boot_cheribsd.failure("Timeout running cheritest: " + str(e), exit=False)
        qemu.sendintr()
        qemu.sendintr()
        # Try to cancel the running command and get back to having a sensible prompt
        qemu.checked_run("pwd")
        time.sleep(10)
        return False
    except boot_cheribsd.CheriBSDCommandFailed as e:
        if "command not found" in e.args:
            boot_cheribsd.failure("Cannot find cheritest binary ", binary_name, ": " + str(e), exit=False)
        else:
            boot_cheribsd.failure("Failed to run: " + str(e), exit=False)
        return False


def run_cheribsd_test(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    boot_cheribsd.success("Booted successfully")
    qemu.checked_run("kenv")
    # unchecked since mount_smbfs returns non-zero for --help:
    qemu.run("mount_smbfs --help", cheri_trap_fatal=True)
    # same for ld-cheri-elf.so (but do check for CHERI traps):
    if qemu.xtarget.is_cheri_hybrid():
        qemu.run("/libexec/ld-cheri-elf.so.1 -h", cheri_trap_fatal=True)
    qemu.run("/libexec/ld-elf.so.1 -h", cheri_trap_fatal=True)

    tests_successful = True

    # Check that we can connect to QEMU using SSH. This catches regressions that break SSHD.
    if not qemu.check_ssh_connection():
        tests_successful = False

    host_has_kyua = shutil.which("kyua") is not None

    # Run the various cheritest binaries
    if args.run_cheritest:
        # Disable trap dumps while running cheritest (handle both old and new sysctl names until dev is merged):
        qemu.run("sysctl machdep.log_user_cheri_exceptions=0 || sysctl machdep.log_cheri_exceptions=0")
        # The minimal disk image only has the statically linked variants:
        test_binaries = ["cheritest", "cheriabitest"]
        if not args.minimal_image:
            test_binaries.extend(["cheriabitest-dynamic", "cheriabitest-dynamic-mt", "cheriabitest-mt",
                                  "cheritest-dynamic", "cheritest-dynamic-mt", "cheritest-mt"])
        for test in test_binaries:
            if not run_cheritest(qemu, test, args):
                tests_successful = False
                boot_cheribsd.failure("At least one test failure in", test, exit=False)
        qemu.run("sysctl machdep.log_user_cheri_exceptions=1 || sysctl machdep.log_cheri_exceptions=1")

    # Run kyua tests
    try:
        if args.kyua_tests_files:
            qemu.checked_run("kyua help", timeout=60)

        for i, tests_file in enumerate(args.kyua_tests_files):
            # TODO: is the results file too big for tmpfs? No should be fine, only a few megabytes
            qemu.checked_run("rm -f /tmp/results.db")
            # Allow up to 24 hours to run the full testsuite
            # Not a checked run since it might return false if some tests fail
            test_start = datetime.datetime.now()
            qemu.run("kyua test --results-file=/tmp/results.db -k {}".format(shlex.quote(tests_file)),
                     ignore_cheri_trap=True, cheri_trap_fatal=False, timeout=24 * 60 * 60)
            if i == 0:
                result_name = "test-results.db"
            else:
                result_name = "test-results-{}.db".format(i)
            results_db = Path("/test-results/{}".format(result_name))
            results_xml = results_db.with_suffix(".xml")
            assert shlex.quote(str(results_db)) == str(results_db), "Should not contain any special chars"
            if qemu.smb_failed:
                boot_cheribsd.info("SMB mount has failed, performing normal scp")
                qemu.scp_from_guest("/tmp/results.db", Path(args.test_output_dir, results_db.name))
            else:
                qemu.checked_run("cp -v /tmp/results.db {}".format(results_db))
                qemu.checked_run("fsync " + str(results_db))
            boot_cheribsd.success("Running tests for ", tests_file, " took: ", datetime.datetime.now() - test_start)

            # run: kyua report-junit --results-file=test-results.db | vis -os > ${CPU}-${TEST_NAME}-test-results.xml
            # Not sure how much we gain by running it on the host instead.
            # Converting the full test suite to xml can take over an hour (probably a lot faster without the vis -os
            # pipe)
            # TODO: should escape the XML file but that's probably faster on the host
            if host_has_kyua:
                boot_cheribsd.info("KYUA installed on the host, no need to do slow conversion in QEMU")
            else:
                xml_conversion_start = datetime.datetime.now()
                qemu.checked_run("kyua report-junit --results-file=/tmp/results.db > /tmp/results.xml",
                                 timeout=200 * 60)
                if qemu.smb_failed:
                    boot_cheribsd.info("SMB mount has failed, performing normal scp")
                    qemu.scp_from_guest("/tmp/results.xml", Path(args.test_output_dir, results_xml.name))
                else:
                    qemu.checked_run("cp -v /tmp/results.xml {}".format(results_xml))
                    qemu.checked_run("fsync " + str(results_xml))
                boot_cheribsd.success("Creating JUnit XML ", results_xml, " took: ",
                                      datetime.datetime.now() - xml_conversion_start)
    except boot_cheribsd.CheriBSDCommandTimeout as e:
        boot_cheribsd.failure("Timeout running tests: " + str(e), exit=False)
        qemu.sendintr()
        qemu.sendintr()
        # Try to cancel the running command and get back to having a sensible prompt
        qemu.checked_run("pwd")
        time.sleep(10)
        tests_successful = False
    except boot_cheribsd.CheriBSDCommandFailed as e:
        boot_cheribsd.failure("Failed to run: " + str(e), exit=False)
        boot_cheribsd.info("Trying to shut down cleanly")
        tests_successful = False

    # Update the JUnit stats in the XML files (both kyua and cheritest):
    if args.kyua_tests_files or args.run_cheritest:
        if not boot_cheribsd.PRETEND:
            time.sleep(2)  # sleep two seconds to ensure the files exist
        junit_dir = Path(args.test_output_dir)
        if host_has_kyua:
            try:
                boot_cheribsd.info("Converting kyua databases to JUNitXML in output directory ", junit_dir)
                for host_kyua_db_path in junit_dir.glob("*.db"):
                    convert_kyua_db_to_junit_xml(host_kyua_db_path, host_kyua_db_path.with_suffix(".xml"))
            except Exception as e:
                boot_cheribsd.failure("Could not convert kyua database in ", junit_dir, ": ", e, exit=False)
                tests_successful = False
        boot_cheribsd.info("Updating statistics in JUnit output directory ", junit_dir)
        for host_xml_path in junit_dir.glob("*.xml"):
            try:
                fixup_kyua_generated_junit_xml(host_xml_path)  # Despite the name also works for cheritest
            except Exception as e:
                boot_cheribsd.failure("Could not update stats in ", junit_dir, ": ", e, exit=False)
                tests_successful = False

    if args.interact or args.skip_poweroff:
        boot_cheribsd.info("Skipping poweroff step since --interact/--skip-poweroff was passed.")
        return tests_successful

    poweroff_start = datetime.datetime.now()
    qemu.sendline("poweroff")
    i = qemu.expect(["Uptime: ", pexpect.TIMEOUT, pexpect.EOF], timeout=360)
    if i != 0:
        boot_cheribsd.failure("Poweroff " + ("timed out" if i == 1 else "failed"))
        return False
    # 240 secs since it takes a lot longer on a full image (it took 44 seconds after installing kyua, so on a really
    # busy jenkins slave it might be a lot slower)
    i = qemu.expect([pexpect.TIMEOUT, "Please press any key to reboot.", pexpect.EOF], timeout=240)
    if i == 0:
        boot_cheribsd.failure("QEMU didn't exit after shutdown!")
        return False
    boot_cheribsd.success("Poweroff took: ", datetime.datetime.now() - poweroff_start)
    if tests_successful and qemu.smb_failed:
        boot_cheribsd.info("Tests succeeded, but SMB mount failed -> marking tests as failed.")
        tests_successful = False
    return tests_successful


def cheribsd_setup_args(args: argparse.Namespace):
    if args.run_cheritest is None:
        # Only hybrid and purecap images have cheritest
        assert isinstance(args.xtarget, CrossCompileTarget)
        args.run_cheritest = args.xtarget.is_hybrid_or_purecap_cheri()
    if args.kyua_tests_files:
        # flatten the list (https://stackoverflow.com/a/45323085/894271):
        args.kyua_tests_files = functools.reduce(operator.iconcat, args.kyua_tests_files, [])
        print(args.kyua_tests_files)
        for file in args.kyua_tests_files:
            if not Path(file).name == "Kyuafile":
                boot_cheribsd.failure("Expected a path to a Kyuafile but got: ", file)
    # Make sure we mount the output directory if we are running kyua and/or cheritest
    if args.kyua_tests_files or args.run_cheritest:
        test_output_dir = Path(os.path.expandvars(os.path.expanduser(args.test_output_dir)))
        if not test_output_dir.is_dir():
            boot_cheribsd.failure("Output directory does not exist: ", test_output_dir)
        # Create a timestamped directory:
        if args.no_timestamped_test_subdir:
            real_output_dir = test_output_dir.absolute()
        else:
            args.timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            real_output_dir = (test_output_dir / args.timestamp).absolute()
            args.test_output_dir = str(real_output_dir)
        boot_cheribsd.run_host_command(["mkdir", "-p", str(real_output_dir)])
        if not boot_cheribsd.PRETEND:
            (real_output_dir / "cmdline").write_text(str(sys.argv))
        args.smb_mount_directories.append(
            boot_cheribsd.SmbMount(real_output_dir, readonly=False, in_target="/test-results"))


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--skip-poweroff", action="store_true",
                        help="Don't run poweroff after tests (implicit with --interact). Without --interact this will"
                             "almost certainly corrupt the disk image, so only pass this if you no longer need the "
                             "image!")
    parser.add_argument("--kyua-tests-files", action="append", nargs=argparse.ZERO_OR_MORE, default=[],
                        help="Run tests for the given following Kyuafile(s)")
    default_test_output = str(Path(".").resolve() / "cheribsd-test-results")
    parser.add_argument("--test-output-dir", "--kyua-tests-output", dest="test_output_dir", default=default_test_output,
                        help="Directory for the test outputs (it will be mounted with SMB)")
    parser.add_argument("--no-timestamped-test-subdir", action="store_true",
                        help="Don't create a timestamped subdirectory in the test output dir ")
    parser.add_argument("--run-cheritest", dest="run_cheritest", action="store_true", default=None,
                        help="Run cheritest and cheriabitest")
    parser.add_argument("--minimal-image", action="store_true",
                        help="Set this if tests are being run on the minimal disk image rather than the full one")
    parser.add_argument("--no-run-cheritest", dest="run_cheritest", action="store_false",
                        help="Do not run cheritest and cheriabitest")


if __name__ == '__main__':
    # we set need_ssh to True here to test that SSH connections work.
    run_tests_main(test_function=run_cheribsd_test, argparse_setup_callback=add_args, should_mount_builddir=False,
                   argparse_adjust_args_callback=cheribsd_setup_args, need_ssh=True)
