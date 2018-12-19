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
import time
import threading
from pathlib import Path
import boot_cheribsd

KERNEL_PANIC = False

def flush_thread(f, qemu: pexpect.spawn):
    while not qemu.tests_completed:
        if f:
            f.flush()
        i = qemu.expect([pexpect.TIMEOUT, "KDB: enter:"], timeout=qemu.flush_interval)
        if boot_cheribsd.PRETEND:
            time.sleep(1)
        elif i == 1:
            boot_cheribsd.failure("GOT KERNEL PANIC!", exit=False)
            boot_cheribsd.debug_kernel_panic(qemu)
            global KERNEL_PANIC
            KERNEL_PANIC = True
            # TODO: tell lit to abort now....
    boot_cheribsd.success("EXIT FLUSH THREAD.")


def run_remote_lit_tests(testsuite: str, qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace, tempdir: str,
                         llvm_lit_path: str=None) -> bool:
    qemu.EXIT_ON_KERNEL_PANIC = False # since we run multiple threads we shouldn't use sys.exit()
    print("PID of QEMU:", qemu.pid)
    port = args.ssh_port
    user = "root"  # TODO: run these tests as non-root!
    test_build_dir = Path(args.build_dir)
    (test_build_dir / "tmp").mkdir(exist_ok=True)
    # TODO: move this to boot_cheribsd.py
    config_contents = """
Host cheribsd-test-instance
        User {user}
        HostName localhost
        Port {port}
        IdentityFile {ssh_key}
        # avoid errors due to changed host key:
        UserKnownHostsFile /dev/null
        StrictHostKeyChecking no
        # faster connection by reusing the existing one:
        ControlPath {home}/.ssh/controlmasters/%r@%h:%p
        # ConnectTimeout 30
        # ConnectionAttempts 3
        ControlMaster auto
""".format(user=user, port=port, ssh_key=Path(args.ssh_key).with_suffix(""), home=Path.home())
    # print("Writing ssh config: ", config_contents)
    with Path(tempdir, "config").open("w") as c:
        c.write(config_contents)
    Path(Path.home(), ".ssh/controlmasters").mkdir(exist_ok=True)
    boot_cheribsd.run_host_command(["cat", str(Path(tempdir, "config"))])
    # Check that the config file works:
    boot_cheribsd.run_host_command(["ssh", "-F", str(Path(tempdir, "config")), "cheribsd-test-instance", "-p", str(port), "--", "echo", "connection successful"], cwd=str(test_build_dir))

    if False:
        # slow executor using scp:
        executor = 'SSHExecutor("localhost", username="{user}", port={port})'.format(user=user, port=port)
    executor = 'SSHExecutorWithNFSMount("cheribsd-test-instance", username="{user}", port={port}, nfs_dir="{host_dir}", ' \
               'path_in_target="/build/tmp", extra_ssh_flags=["-F", "{tempdir}/config", "-n", "-4", "-t", "-t"], ' \
               'extra_scp_flags=["-F", "{tempdir}/config"])'.format(user=user, port=port, host_dir=str(test_build_dir / "tmp"), tempdir=tempdir)

    print("Running", testsuite, "tests with executor", executor)
    # have to use -j1 + --single-process since otherwise CheriBSD might wedge
    if llvm_lit_path is None:
        llvm_lit_path = str(test_build_dir / "bin/llvm-lit")
    lit_cmd = [llvm_lit_path, "-j1", "-vv", "--single-process", "-Dexecutor=" + executor, "test"]
    if args.lit_debug_output:
        lit_cmd.append("--debug")
    # This does not work since it doesn't handle running ssh commands....
    lit_cmd.append("--timeout=120")  # 2 minutes max per test (in case there is an infinite loop)
    xunit_file = None  # type: Path
    if args.xunit_output:
        lit_cmd.append("--xunit-xml-output")
        xunit_file = Path(args.xunit_output).absolute()
        if args.internal_shard:
            xunit_file = xunit_file.with_name("shard-" + str(args.internal_shard) + "-" + xunit_file.name)
        lit_cmd.append(str(xunit_file))
    qemu_logfile = None
    if args.internal_shard:
        assert args.internal_num_shards, "Invalid call!"
        lit_cmd.append("--num-shards=" + str(args.internal_num_shards))
        lit_cmd.append("--run-shard=" + str(args.internal_shard))
        if xunit_file:
            qemu_log_path = xunit_file.with_suffix(".output").absolute()
            boot_cheribsd.success("Writing QEMU output to ", qemu_log_path)
            qemu_logfile = qemu_log_path.open("w")
            qemu.logfile_read = qemu_logfile
            boot_cheribsd.run_cheribsd_command(qemu, "echo HELLO LOGFILE")
    # Fixme starting lit at the same time does not work!
    # TODO: add the polling to the main thread instead of having another thread?
    # start the qemu output flushing thread so that we can see the kernel panic
    qemu.tests_completed = False
    qemu.flush_interval = 15 # flush the logfile every 15 seconds
    t = threading.Thread(target=flush_thread, args=(qemu_logfile, qemu))
    t.daemon = True
    t.start()
    shard_prefix = "SHARD" + str(args.internal_shard) + ": " if args.internal_shard else ""
    try:
        boot_cheribsd.success("Starting llvm-lit: cd ", test_build_dir, " && ", " ".join(lit_cmd))
        boot_cheribsd.run_host_command(lit_cmd, cwd=str(test_build_dir))
        # lit_proc = pexpect.spawnu(lit_cmd[0], lit_cmd[1:], echo=True, timeout=60, cwd=str(test_build_dir))
        # TODO: get stderr!!
        # while lit_proc.isalive():
        lit_proc = None
        while False:
            line = lit_proc.readline()
            if shard_prefix:
                line = shard_prefix + line
            print(line)
            global KERNEL_PANIC
            # Abort once we detect a kernel panic
            if KERNEL_PANIC:
                lit_proc.sendintr()
            print(shard_prefix + lit_proc.read())
        print("Lit finished.")
        qemu.flush_interval = 1
        qemu.tests_completed = True
        if lit_proc and lit_proc.exitstatus == 1:
            boot_cheribsd.failure(shard_prefix + "SOME TESTS FAILED", exit=False)
    except:
        raise
    finally:
        if qemu_logfile:
            qemu_logfile.flush()
        t.join()
    return True
