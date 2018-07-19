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
import pexpect
import argparse
import os
import subprocess
from pathlib import Path
boot_cheribsd = __import__("boot_cheribsd")


def run_libcxx_tests(qemu: pexpect.spawn, args: argparse.Namespace):
    boot_cheribsd.run_cheribsd_command(qemu, "mount_smbfs -I 10.0.2.4 -N //10.0.2.4/qemu /mnt",
                                       error_output="mount_smbfs: unable to open connection:")
    port = args.ssh_port
    user = "root"  # TODO: run these tests as non-root!
    libcxx_dir = Path(args.libcxx_build_dir)
    (libcxx_dir / "tmp").mkdir(exist_ok=True)

    if False:
        # slow executor using scp:
        executor = 'SSHExecutor("localhost", username="{user}", port={port})'.format(user=user, port=port)
    executor = 'SSHExecutorWithNFSMount("localhost", username="{user}", port={port}, nfs_dir="{host_dir}", path_in_target="/mnt/tmp")'.format(
        user=user, port=port, host_dir=str(libcxx_dir / "tmp"))
    print("Running libcxx_tests with executor", executor)
    # TODO: sharding + xunit output
    # have to use -j1 since otherwise CheriBSD might wedge
    lit_cmd = [str(libcxx_dir / "bin/llvm-lit"), "-j1", "-vv", "-Dexecutor=" + executor, "test"]
    if args.lit_debug_output:
        lit_cmd.append("--debug")
    if args.xunit_output:
        lit_cmd.append("--xunit-xml-output")
        lit_cmd.append(str(Path(args.xunit_output).absolute()))
    # TODO: --num-shards = 16
    # --run-shard = N
    print("Will run ", " ".join(lit_cmd))
    subprocess.check_call(lit_cmd, cwd=str(libcxx_dir))

def add_cmdline_args(parser: argparse.ArgumentParser):
    parser.add_argument("--libcxx-build-dir", required=True)
    parser.add_argument("--lit-debug-output", action="store_true")
    parser.add_argument("--xunit-output", default="libcxx-tests.xml")

def setup_args(args: argparse.Namespace):
    args.libcxx_build_dir = os.path.expandvars(os.path.expanduser(args.libcxx_build_dir))
    args.use_smb_instead_of_ssh = False  # we need ssh running to execute the tests
    args.smb_mount_directory = args.libcxx_build_dir

if __name__ == '__main__':
    boot_cheribsd.main(test_function=run_libcxx_tests, argparse_setup_callback=add_cmdline_args, argparse_adjust_args_callback=setup_args)