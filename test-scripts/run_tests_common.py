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
import sys
from pathlib import Path
from typing import Callable, Optional

_cheribuild_root = Path(__file__).parent.parent
_junitparser_dir = _cheribuild_root / "3rdparty/junitparser"
assert (_junitparser_dir / "junitparser/__init__.py").exists()
_pexpect_dir = _cheribuild_root / "3rdparty/pexpect"
assert (_pexpect_dir / "pexpect/__init__.py").exists()
sys.path.insert(1, str(_junitparser_dir))
sys.path.insert(1, str(_pexpect_dir))
# Pexpect also needs ptyprocess
_ptyprocess_dir = _cheribuild_root / "3rdparty/ptyprocess"
assert (_ptyprocess_dir / "ptyprocess/ptyprocess.py").exists(), _ptyprocess_dir / "ptyprocess/ptyprocess.py"
sys.path.insert(1, str(_ptyprocess_dir))
sys.path.insert(1, str(_cheribuild_root))
import junitparser  # noqa: E402
import pexpect  # noqa: E402

from pycheribuild import boot_cheribsd  # noqa: E402
from pycheribuild.boot_cheribsd import QemuCheriBSDInstance  # noqa: E402
from pycheribuild.config.target_info import CrossCompileTarget  # noqa: E402
from pycheribuild.processutils import commandline_to_str  # noqa: E402

__all__ = [
    "run_tests_main",
    "boot_cheribsd",
    "junitparser",
    "pexpect",
    "commandline_to_str",
    "CrossCompileTarget",
    "finish_and_write_junit_xml_report",
    "get_default_junit_xml_name",
]


def get_default_junit_xml_name(from_cmdline: "Optional[str]", default_output_dir: Path):
    if from_cmdline is None:
        time_suffix = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        result = Path(default_output_dir, ("test-results-" + time_suffix + ".xml"))
    else:
        result = Path(from_cmdline)
    if not result.is_absolute():
        result = Path(default_output_dir, result)
    return result


def finish_and_write_junit_xml_report(
    all_tests_starttime: datetime.datetime,
    xml: junitparser.JUnitXml,
    output_file: Path,
) -> bool:
    """
    :param output_file: the host path where to the JUnit XML should be written
    :param all_tests_starttime: the time when the test run started
    :param xml: the xml file
    :return: True if no tests failed, False otherwise
    """
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
    boot_cheribsd.info(
        "Ran " + str(num_testsuites),
        " test suites in ",
        (datetime.datetime.utcnow() - all_tests_starttime),
    )
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

        boot_cheribsd.failure(
            "The following ",
            len(failed_test_suites),
            " tests failed:\n\t",
            "\n\t".join(failed_test_info(x) for x in failed_test_suites),
            exit=False,
        )
    else:
        boot_cheribsd.success(
            "All ",
            xml.tests,
            " tests (",
            num_testsuites,
            " test suites) passed after ",
            (datetime.datetime.utcnow() - all_tests_starttime),
        )
    # Finally, write the Junit XML file:
    if not boot_cheribsd.PRETEND:
        xml.write(output_file, pretty=True)
    boot_cheribsd.info("Wrote Junit results to ", output_file)
    return not failed_test_suites


def run_tests_main(
    test_function: Optional[Callable[[QemuCheriBSDInstance, argparse.Namespace], bool]] = None,
    need_ssh=False,
    should_mount_builddir=True,
    should_mount_srcdir=False,
    should_mount_sysroot=False,
    should_mount_installdir=False,
    build_dir_in_target: "Optional[str]" = None,
    test_setup_function: Optional[Callable[[QemuCheriBSDInstance, argparse.Namespace], None]] = None,
    argparse_setup_callback: Optional[Callable[[argparse.ArgumentParser], None]] = None,
    argparse_adjust_args_callback: Optional[Callable[[argparse.Namespace], None]] = None,
):
    def default_add_cmdline_args(parser: argparse.ArgumentParser):
        parser.add_argument("--build-dir", required=should_mount_builddir)
        parser.add_argument("--source-dir", required=should_mount_srcdir)
        parser.add_argument("--sysroot-dir", required=should_mount_sysroot)
        parser.add_argument("--install-destdir", required=should_mount_installdir)
        parser.add_argument("--install-prefix", required=should_mount_installdir)
        if argparse_setup_callback:
            argparse_setup_callback(parser)
        if not need_ssh:
            parser.add_argument("--force-ssh-setup", action="store_true", dest="__foce_ssh_setup")

    def default_setup_args(args: argparse.Namespace):
        if need_ssh:
            args.use_smb_instead_of_ssh = False  # we need ssh running to execute the tests
        else:
            args.use_smb_instead_of_ssh = True  # skip the ssh setup
            args.skip_ssh_setup = not args.__foce_ssh_setup
        if should_mount_builddir or args.build_dir:
            args.build_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.build_dir)))
            # Default to mounting the build directory in the target under the host path.
            # This should allow more tests to pass, i.e. the libc++ filesystem tests or the libjpeg-turbo ones etc.
            # We previously mounted the build dir under /build and added a symlink but that breaks tests that try to
            # get at the source directory using a relative path (../../my-srcdir ends up resolving to /my-srcdir).
            path_in_target = build_dir_in_target if build_dir_in_target is not None else args.build_dir
            args.smb_mount_directories.append(
                boot_cheribsd.SmbMount(args.build_dir, readonly=False, in_target=path_in_target),
            )
        if should_mount_srcdir or args.source_dir:
            args.source_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.source_dir)))
            args.smb_mount_directories.append(
                boot_cheribsd.SmbMount(args.source_dir, readonly=True, in_target="/source"),
            )
        if should_mount_sysroot or args.sysroot_dir:
            args.sysroot_dir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.sysroot_dir)))
            args.smb_mount_directories.append(
                boot_cheribsd.SmbMount(args.sysroot_dir, readonly=True, in_target="/sysroot"),
            )
        if should_mount_installdir or args.install_destdir:
            args.install_destdir = os.path.abspath(os.path.expandvars(os.path.expanduser(args.install_destdir)))
            assert args.install_prefix and args.install_prefix[0] == "/"
            args.smb_mount_directories.append(
                boot_cheribsd.SmbMount(
                    args.install_destdir + args.install_prefix,
                    readonly=True,
                    in_target=args.install_prefix,
                ),
            )
        if argparse_adjust_args_callback:
            argparse_adjust_args_callback(args)

    def default_setup_tests(qemu: QemuCheriBSDInstance, args: argparse.Namespace):
        if should_mount_builddir or args.build_dir:
            qemu.checked_run(f"ln -sf '{args.build_dir}' /build", timeout=60)
        if should_mount_srcdir or args.source_dir:
            assert args.source_dir
            qemu.run(f"mkdir -p '{Path(args.source_dir).parent}'")
            qemu.checked_run(f"ln -sf /source '{args.source_dir}'", timeout=60)
            boot_cheribsd.success("Mounted source directory using host path")
        # Finally call the custom test setup function
        if test_setup_function:
            test_setup_function(qemu, args)

    assert sys.path[0] == str(Path(__file__).parent.absolute()), sys.path
    assert sys.path[1] == str(Path(__file__).parent.parent.absolute()), sys.path
    boot_cheribsd.main(
        test_function=test_function,
        test_setup_function=default_setup_tests,
        argparse_setup_callback=default_add_cmdline_args,
        argparse_adjust_args_callback=default_setup_args,
    )
