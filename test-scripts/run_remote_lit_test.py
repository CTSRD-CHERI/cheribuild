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
import datetime
import os
import time
import threading
import multiprocessing
import subprocess
import sys
from pathlib import Path
from enum import Enum
import boot_cheribsd

KERNEL_PANIC = False
COMPLETED = "COMPLETED"
NEXT_STAGE = "NEXT_STAGE"
FAILURE = "FAILURE"

class MultiprocessStages(Enum):
    FINDING_SSH_PORT = "find free port for SSH"
    BOOTING_CHERIBSD = "booting CheriBSD"
    TESTING_SSH_CONNECTION = "testing SSH connection to CheriBSD"
    RUNNING_TESTS = "running lit tests"
    EXITED = "exited"
    FAILED = "failed"
    TIMED_OUT = "timed out"


def flush_thread(f, qemu: pexpect.spawn, should_exit_event: threading.Event):
    while not should_exit_event.wait(timeout=0.1):
        if f:
            f.flush()
        if should_exit_event.is_set():
            break
        # keep reading line-by-line to output any QEMU trap messages:
        i = qemu.expect([pexpect.TIMEOUT, "KDB: enter:", pexpect.EOF, qemu.crlf], timeout=qemu.flush_interval)
        if boot_cheribsd.PRETEND:
            time.sleep(1)
        elif i == 1:
            boot_cheribsd.failure("GOT KERNEL PANIC!", exit=False)
            boot_cheribsd.debug_kernel_panic(qemu)
            global KERNEL_PANIC
            KERNEL_PANIC = True
            # TODO: tell lit to abort now....
        elif i == 2:
            boot_cheribsd.failure("GOT QEMU EOF!", exit=False)
            # QEMU exited?
            break
    # One final expect to flush the buffer:
    qemu.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=1)
    boot_cheribsd.success("QEMU output flushing thread terminated.")


def run_remote_lit_tests(testsuite: str, qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace, tempdir: str,
                         mp_q: multiprocessing.Queue = None, llvm_lit_path: str = None) -> bool:
    try:
        result = run_remote_lit_tests_impl(testsuite=testsuite, qemu=qemu, args=args, tempdir=tempdir,
                                           mp_q=mp_q, llvm_lit_path=llvm_lit_path)
        if mp_q:
            mp_q.put((COMPLETED, args.internal_shard))
        return result
    except:
        if mp_q:
            boot_cheribsd.failure("GOT EXCEPTION in shard ", args.internal_shard, ": ", sys.exc_info(), exit=False)
            e = sys.exc_info()[1]
            mp_q.put((FAILURE, args.internal_shard, str(type(e)) + ": " +str(e)))
        raise


def run_remote_lit_tests_impl(testsuite: str, qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace, tempdir: str,
                              mp_q: multiprocessing.Queue = None, llvm_lit_path: str = None) -> bool:
    qemu.EXIT_ON_KERNEL_PANIC = False # since we run multiple threads we shouldn't use sys.exit()
    boot_cheribsd.info("PID of QEMU: ", qemu.pid)

    def notify_main_process(stage):
        if mp_q:
            if args.multiprocessing_debug:
                boot_cheribsd.success("Shard ", args.internal_shard, " stage complete: ", stage)
            mp_q.put((NEXT_STAGE, args.internal_shard, stage))

    if args.pretend and os.getenv("FAIL_TIMEOUT_BOOT") and args.internal_shard == 2:
        time.sleep(10)
    notify_main_process(MultiprocessStages.TESTING_SSH_CONNECTION)
    if args.pretend and os.getenv("FAIL_RAISE_EXCEPTION") and args.internal_shard == 1:
        raise RuntimeError("SOMETHING WENT WRONG!")
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
        NoHostAuthenticationForLocalhost yes
        # faster connection by reusing the existing one:
        ControlPath {home}/.ssh/controlmasters/%r@%h:%p
        # ConnectTimeout 20
        # ConnectionAttempts 2
        ControlMaster auto
""".format(user=user, port=port, ssh_key=Path(args.ssh_key).with_suffix(""), home=Path.home())
    config_contents += "        ControlPersist {control_persist}\n"
    # print("Writing ssh config: ", config_contents)
    with Path(tempdir, "config").open("w") as c:
        # Keep socket open for 10 min (600) or indefinitely (yes)
        c.write(config_contents.format(control_persist="yes"))
    Path(Path.home(), ".ssh/controlmasters").mkdir(exist_ok=True)
    boot_cheribsd.run_host_command(["cat", str(Path(tempdir, "config"))])
    # Check that the config file works:

    def check_ssh_connection(prefix):
        connection_test_start = datetime.datetime.utcnow()
        boot_cheribsd.run_host_command(["ssh", "-F", str(Path(tempdir, "config")), "cheribsd-test-instance", "-p", str(port), "--", "echo", "connection successful"], cwd=str(test_build_dir))
        connection_time = (datetime.datetime.utcnow() - connection_test_start).total_seconds()
        boot_cheribsd.success(prefix, " successful after ", connection_time, " seconds")

    check_ssh_connection("First SSH connection")
    controlmaster_running = False
    try:
        # Check that controlmaster worked by running ssh -O check
        boot_cheribsd.info("Checking if SSH control master is working.")
        boot_cheribsd.run_host_command(["ssh", "-F", str(Path(tempdir, "config")), "cheribsd-test-instance",
                                        "-p", str(port), "-O", "check"], cwd=str(test_build_dir))
        check_ssh_connection("Second SSH connection (with controlmaster)")
        controlmaster_running = True
    except subprocess.CalledProcessError:
        boot_cheribsd.failure("WARNING: Could not connect to ControlMaster SSH connection. Running tests will be slower",
                               first_connection_time, " seconds", exit=False)
        with Path(tempdir, "config").open("w") as c:
            c.write(config_contents.format(control_persist="no"))
        check_ssh_connection("Second SSH connection (without controlmaster)")

    if args.pretend:
        time.sleep(2.5)

    if False:
        # slow executor using scp:
        executor = 'SSHExecutor("localhost", username="{user}", port={port})'.format(user=user, port=port)
    executor = 'SSHExecutorWithNFSMount("cheribsd-test-instance", username="{user}", port={port}, nfs_dir="{host_dir}", ' \
               'path_in_target="/build/tmp", extra_ssh_flags=["-F", "{tempdir}/config", "-n", "-4", "-t", "-t"], ' \
               'extra_scp_flags=["-F", "{tempdir}/config"])'.format(user=user, port=port, host_dir=str(test_build_dir / "tmp"), tempdir=tempdir)

    print("Running", testsuite, "tests with executor", executor)
    notify_main_process(MultiprocessStages.RUNNING_TESTS)
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
            qemu_log_path = xunit_file.with_suffix(".output.log").absolute()
            boot_cheribsd.success("Writing QEMU output to ", qemu_log_path)
            qemu_logfile = qemu_log_path.open("w")
            qemu.logfile_read = qemu_logfile
            boot_cheribsd.run_cheribsd_command(qemu, "echo HELLO LOGFILE")
    # Fixme starting lit at the same time does not work!
    # TODO: add the polling to the main thread instead of having another thread?
    # start the qemu output flushing thread so that we can see the kernel panic
    qemu.flush_interval = 15 # flush the logfile every 15 seconds
    should_exit_event = threading.Event()
    t = threading.Thread(target=flush_thread, args=(qemu_logfile, qemu, should_exit_event))
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
        if lit_proc and lit_proc.exitstatus == 1:
            boot_cheribsd.failure(shard_prefix + "SOME TESTS FAILED", exit=False)
    except subprocess.CalledProcessError as e:
        boot_cheribsd.failure(shard_prefix + "SOME TESTS FAILED: ", e, exit=False)
        # Should only ever return 1 (otherwise something else went wrong!)
        if e.returncode == 1:
            return False
        else:
            raise
    finally:
        if qemu_logfile:
            qemu_logfile.flush()
        if controlmaster_running:
            boot_cheribsd.info("Terminating SSH controlmaster")
            try:
                boot_cheribsd.run_host_command(["ssh", "-F", str(Path(tempdir, "config")), "cheribsd-test-instance",
                                                "-p", str(port), "-O", "exit"], cwd=str(test_build_dir))
            except subprocess.CalledProcessError:
                boot_cheribsd.failure("Could not close SSH controlmaster connection.", exit=False)
        qemu.flush_interval = 0.1
        should_exit_event.set()
        t.join(timeout=30)
        if t.is_alive():
            boot_cheribsd.failure("Failed to kill flush thread. Interacting with CheriBSD will not work!", exit=True)
            return False
        if not qemu.isalive():
            boot_cheribsd.failure("QEMU died while running tests! ", qemu, exit=True)
    return True
