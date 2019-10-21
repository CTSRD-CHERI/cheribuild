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
from run_tests_common import boot_cheribsd, run_tests_main, pexpect


def run_cheribsd_test(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    boot_cheribsd.success("Booted successfully")
    qemu.checked_run("kenv")
    # unchecked since mount_smbfs returns non-zero for --help:
    qemu.run("mount_smbfs --help", cheri_trap_fatal=True)
    # same for ld-cheri-elf.so (but do check for CHERI traps):
    qemu.run("/libexec/ld-cheri-elf.so.1 -h", cheri_trap_fatal=True)

    tests_successful = True
    host_has_kyua = shutil.which("kyua") is not None

    try:
        # potentially bootstrap kyua for later testing
        if args.bootstrap_kyua or args.kyua_tests_files:
            qemu.checked_run("/sbin/prepare-testsuite.sh", timeout=30 * 60)
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
                results_db = Path("/kyua-results/test-results.db")
            else:
                results_db = Path("/kyua-results/test-results-{}.db".format(i))
            results_xml = results_db.with_suffix(".xml")
            assert shlex.quote(str(results_db)) == str(results_db), "Should not contain any special chars"
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

    # Update the JUnit stats in the XML file
    if args.kyua_tests_files:
        if not boot_cheribsd.PRETEND:
            time.sleep(2)  # sleep two seconds to ensure the files exist
        junit_dir = Path(args.kyua_tests_output)
        try:
            if host_has_kyua:
                boot_cheribsd.info("Converting kyua databases to JUNitXML in output directory ", junit_dir)
                for host_kyua_db_path in junit_dir.glob("*.db"):
                    convert_kyua_db_to_junit_xml(host_kyua_db_path, host_kyua_db_path.with_suffix(".xml"))
            else:
                boot_cheribsd.info("Updating statistics in JUnit output directory ", junit_dir)
                for host_xml_path in junit_dir.glob("*.xml"):
                    fixup_kyua_generated_junit_xml(host_xml_path)
        except Exception as e:
            boot_cheribsd.failure("Could not update stats in ", junit_dir, ": ", e, exit=False)
            tests_successful = False

    if args.interact or args.skip_poweroff:
        boot_cheribsd.info("Skipping poweroff step since --interact/--skip-poweroff was passed.")
        return tests_successful

    poweroff_start = datetime.datetime.now()
    qemu.sendline("poweroff")
    i = qemu.expect(["Uptime:", pexpect.TIMEOUT, pexpect.EOF] + boot_cheribsd.FATAL_ERROR_MESSAGES, timeout=240)
    if i != 0:
        boot_cheribsd.failure("Poweroff " + ("timed out" if i == 1 else "failed"))
        return False
    # 240 secs since it takes a lot longer on a full image (it took 44 seconds after installing kyua, so on a really
    # busy jenkins slave it might be a lot slower)
    i = qemu.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=240)
    if i == 0:
        boot_cheribsd.failure("QEMU didn't exit after shutdown!")
        return False
    boot_cheribsd.success("Poweroff took: ", datetime.datetime.now() - poweroff_start)
    return tests_successful


def cheribsd_setup_args(args: argparse.Namespace):
    args.use_smb_instead_of_ssh = True  # skip the ssh setup
    args.skip_ssh_setup = True
    if args.kyua_tests_files:
        # flatten the list (https://stackoverflow.com/a/45323085/894271):
        args.kyua_tests_files = functools.reduce(operator.iconcat, args.kyua_tests_files, [])
        print(args.kyua_tests_files)
        for file in args.kyua_tests_files:
            if not Path(file).name == "Kyuafile":
                boot_cheribsd.failure("Expected a path to a Kyuafile but got: ", file)
        test_output_dir = Path(os.path.expandvars(os.path.expanduser(args.kyua_tests_output)))
        if not test_output_dir.is_dir():
            boot_cheribsd.failure("Output directory does not exist: ", test_output_dir)
        # Create a timestamped directory:
        if args.kyua_tests_output_no_timestamped_subdir:
            real_output_dir = test_output_dir.absolute()
        else:
            args.timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            real_output_dir = (test_output_dir / args.timestamp).absolute()
            args.kyua_tests_output = str(real_output_dir)
        boot_cheribsd.run_host_command(["mkdir", "-p", str(real_output_dir)])
        if not boot_cheribsd.PRETEND:
            (real_output_dir / "cmdline").write_text(str(sys.argv))
        args.smb_mount_directories.append(
            boot_cheribsd.SmbMount(real_output_dir, readonly=False, in_target="/kyua-results"))


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--bootstrap-kyua", action="store_true",
                        help="Install kyua using the /sbin/prepare-testsuite.sh script")
    parser.add_argument("--skip-poweroff", action="store_true",
                        help="Don't run poweroff after tests (implicit with --interact). Without --interact this will"
                             "almost certainly corrupt the disk image, so only pass this if you no longer need the "
                             "image!")
    parser.add_argument("--kyua-tests-files", action="append", nargs=argparse.ZERO_OR_MORE, default=[],
                        help="Run tests for the given following Kyuafile(s)")
    parser.add_argument("--kyua-tests-output", default=str(Path(".").resolve() / "kyua-results"),
                        help="Copy the kyua results.db to the following directory (it will be mounted with SMB)")
    parser.add_argument("--kyua-tests-output-no-timestamped-subdir", action="store_true",
                        help="Don't create a timestamped subdirectory in the test output dir ")


if __name__ == '__main__':
    # we don't need to setup ssh config/authorized_keys to test the boot
    run_tests_main(test_function=run_cheribsd_test, argparse_setup_callback=add_args, should_mount_builddir=False,
                   argparse_adjust_args_callback=cheribsd_setup_args)
