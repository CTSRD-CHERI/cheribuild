#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
import argparse
import datetime
import json
import typing
from pathlib import Path
from typing import Optional

from run_tests_common import (
    boot_cheribsd,
    commandline_to_str,
    finish_and_write_junit_xml_report,
    get_default_junit_xml_name,
    junitparser,
    run_tests_main,
)


def do_setup(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    if args.test_setup_commands:
        # If the user supplied test setup steps, run them now.
        for command in args.test_setup_commands:
            qemu.checked_run(command)
    else:
        # Otherwise, we just set up the default LD_LIBRARY_PATH.
        boot_cheribsd.set_ld_library_path_with_sysroot(qemu)
    qemu.checked_run(f"cd {args.build_dir}")


def run_meson_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    xml = junitparser.JUnitXml()
    all_tests_starttime = datetime.datetime.utcnow()
    for ti in args.test_info:
        assert isinstance(ti, MesonTestInfo)
        suite = junitparser.TestSuite(name=ti.name)
        commandline = commandline_to_str(ti.command)
        suite.add_property("test_command", str(commandline))
        t = junitparser.TestCase(name=ti.name)
        starttime = datetime.datetime.utcnow()
        try:
            env_cmd = ""
            if ti.env_vars:
                env_cmd = " env " + commandline_to_str(k + "=" + str(v) for k, v in ti.env_vars.items())
            qemu.checked_run(
                "cd {cwd} &&{env} {cmd}".format(cwd=ti.cwd or "/build", cmd=commandline, env=env_cmd),
                timeout=ti.timeout or 10 * 60,
            )
            # TODO: TAP protocol parsing instead of using 0/1 return code.
        except boot_cheribsd.CheriBSDCommandFailed as e:
            boot_cheribsd.failure("Failed to run ", ti.name, ": ", str(e), exit=False)
            if isinstance(e, boot_cheribsd.CheriBSDCommandTimeout):
                t.result = junitparser.Failure(message="Command timed out")
                # Send CTRL+C if the process timed out.
                qemu.sendintr()
                qemu.sendintr()
                qemu.expect_prompt(timeout=5 * 60)
            else:
                t.result = junitparser.Failure(message="Command failed")
        t.time = (datetime.datetime.utcnow() - starttime).total_seconds()
        suite.add_testcase(t)
        xml.add_testsuite(suite)
    return finish_and_write_junit_xml_report(all_tests_starttime, xml, args.junit_xml)


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--junit-xml", required=False, help="Output file name for the JUnit XML results")
    parser.add_argument(
        "--test-setup-command",
        action="append",
        dest="test_setup_commands",
        metavar="COMMAND",
        help="Run COMMAND as an additional test setup step before running the tests",
    )


class MesonTestInfo(typing.NamedTuple):
    name: str
    command: "list[str]"
    cwd: Optional[str]
    env_vars: "dict[str, str]"
    timeout: Optional[int]


def adjust_args(args: argparse.Namespace):
    args.junit_xml = get_default_junit_xml_name(args.junit_xml, args.build_dir)

    # Parse the JSON file containing test information (see https://mesonbuild.com/IDE-integration.html)
    tests_json_path = Path(args.build_dir, "meson-info/intro-tests.json")
    if not tests_json_path.exists():
        boot_cheribsd.failure("Could not find test information (", tests_json_path, ")", exit=True)
    args.test_info = []
    with tests_json_path.open("r") as f:
        tests_json = json.load(f)
        for test in tests_json:
            protocol = test.get("protocol", None)
            name = test["name"]
            if protocol not in ("exitcode", "tap"):
                boot_cheribsd.failure(
                    "Unknown/unsupported testing protocol '",
                    protocol,
                    "' for test",
                    name,
                    ":",
                    test,
                    exit=True,
                )
            args.test_info.append(
                MesonTestInfo(
                    name=name,
                    command=test["cmd"],
                    cwd=test["workdir"],
                    env_vars=test["env"],
                    timeout=test["timeout"],
                ),
            )


if __name__ == "__main__":
    # we don't need ssh running to execute the tests
    run_tests_main(
        test_function=run_meson_tests,
        test_setup_function=do_setup,
        need_ssh=False,
        argparse_setup_callback=add_args,
        argparse_adjust_args_callback=adjust_args,
        should_mount_builddir=True,
        should_mount_srcdir=True,
        should_mount_sysroot=True,
    )
