#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# Copyright (c) 2022 Alex Richardson
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
import os
import subprocess
from pathlib import Path

from run_tests_common import boot_cheribsd, run_tests_main


def run_libffi_tests(qemu: boot_cheribsd.QemuCheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running libffi tests")
    print(args)
    # copy the shared libraries to the host and link to /usr/lib so that the tests can run:
    for i in Path(args.build_dir, ".libs").glob("libffi.so*"):
        qemu.scp_to_guest(i, str(Path("/tmp", i.name)))
    qemu.checked_run("ln -sf /tmp/libffi.so* /usr/lib")
    Path(args.build_dir, "site.exp").write_text(
        f"""
if ![info exists boards_dir] {{
    set boards_dir {{}}
}}
lappend boards_dir "{args.build_dir}"
verbose "Global Config File: target_triplet is $target_triplet" 2
global target_list
set target_list "remote-cheribsd"
""",
    )
    ssh_options = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o NoHostAuthenticationForLocalhost=yes"
    Path(args.build_dir, "remote-cheribsd.exp").write_text(
        f"""
load_generic_config "unix"
set_board_info connect ssh
#set_board_info rsh_prog "ssh -i {qemu.ssh_private_key} {ssh_options} -p {qemu.ssh_port}"
# set_board_info rcp_prog "scp -i {qemu.ssh_private_key} {ssh_options} -P {qemu.ssh_port}"
set_board_info hostname localhost
set_board_info username {qemu.ssh_user}
set_board_info port {qemu.ssh_port}
# Work around typo in ssh.exp, it checks for ssh_useropts, but then appends the value of ssh_opts
set_board_info ssh_useropts "-i {qemu.ssh_private_key} {ssh_options}"
set_board_info ssh_opts "-i {qemu.ssh_private_key} {ssh_options}"
# set_board_info exec_shell "gdb-run-noninteractive.sh"
""",
    )
    boot_cheribsd.run_host_command(["runtest", "--version"])
    tests_okay = False
    try:
        # Note: we have to use dict(os.environ, **dict(...)) to update env, since env=... overrides it.
        boot_cheribsd.run_host_command(
            ["make", "check", "RUNTESTFLAGS=-a --target-board remote-cheribsd --xml"],
            env=dict(os.environ, **dict(BOARDSDIR=str(args.build_dir), DEJAGNU=str(Path(args.build_dir, "site.exp")))),
            cwd=str(args.build_dir),
        )
        tests_okay = True
    except subprocess.CalledProcessError:
        boot_cheribsd.failure("Some tests failed", exit=False)

    # TODO: parse the XML output/.sum file to generate a JUnit XML file
    test_summary = Path(args.build_dir, "testsuite/libffi.sum")
    if test_summary.exists():
        boot_cheribsd.info("Test summary:\n", test_summary.read_text())
    return tests_okay


if __name__ == "__main__":
    # we don't need ssh running to execute the tests
    run_tests_main(
        test_function=run_libffi_tests,
        need_ssh=True,
        # We don't actually need to mount these directories, but by setting the arguments to true, the
        # test script will give an error if --source-dir/--build-dir is not passed. required
        should_mount_builddir=True,
        should_mount_srcdir=False,
    )
