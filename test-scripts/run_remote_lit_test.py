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
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Optional

from run_tests_common import boot_cheribsd, commandline_to_str, pexpect

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


CURRENT_STAGE: MultiprocessStages = MultiprocessStages.FINDING_SSH_PORT


def add_common_cmdline_args(parser: argparse.ArgumentParser, default_xunit_output: str, allow_multiprocessing: bool):
    parser.add_argument("--ssh-executor-script", help="Path to the ssh.py executor script", required=True)
    parser.add_argument("--use-shared-mount-for-tests", action="store_true", default=True)
    parser.add_argument("--no-use-shared-mount-for-tests", dest="use-shared-mount-for-tests", action="store_false")
    parser.add_argument("--llvm-lit-path")
    parser.add_argument("--xunit-output", default=default_xunit_output)
    parser.add_argument("--lit-debug-output", action="store_true")
    # For the parallel jobs
    if allow_multiprocessing:
        parser.add_argument("--multiprocessing-debug", action="store_true")
        parser.add_argument(
            "--parallel-jobs",
            metavar="N",
            type=int,
            help="Split up the testsuite into N parallel jobs",
        )
        parser.add_argument("--internal-num-shards", type=int, help=argparse.SUPPRESS)
        parser.add_argument("--internal-shard", type=int, help=argparse.SUPPRESS)


def adjust_common_cmdline_args(args: argparse.Namespace):
    if args.use_shared_mount_for_tests:
        # If we have a shared directory use that to massively speed up running tests
        tmpdir_name = "local-tmp" if not args.internal_shard else "local-tmp-shard-" + str(args.internal_shard)
        shared_tmpdir = Path(args.build_dir, tmpdir_name)
        shared_tmpdir.mkdir(parents=True, exist_ok=True)
        args.shared_tmpdir_local = shared_tmpdir
        args.smb_mount_directories.append(
            boot_cheribsd.SmbMount(str(shared_tmpdir), readonly=False, in_target="/shared-tmpdir"),
        )


def mp_debug(cmdline_args: argparse.Namespace, *args, **kwargs):
    if cmdline_args.multiprocessing_debug:
        boot_cheribsd.info(*args, **kwargs)


def notify_main_process(
    cmdline_args: argparse.Namespace,
    stage: MultiprocessStages,
    mp_q: multiprocessing.Queue,
    barrier: "Optional[multiprocessing.Barrier]" = None,
):
    if mp_q:
        global CURRENT_STAGE  # noqa: PLW0603
        mp_debug(cmdline_args, "Next stage: ", CURRENT_STAGE, "->", stage)
        mp_q.put((NEXT_STAGE, cmdline_args.internal_shard, stage))
        CURRENT_STAGE = stage
    if barrier:
        assert mp_q
        mp_debug(cmdline_args, "Waiting for main process to release barrier for stage ", stage)
        barrier.wait()
        mp_debug(cmdline_args, "Barrier released for stage ", stage)
        time.sleep(1)


def flush_thread(f, qemu: boot_cheribsd.QemuCheriBSDInstance, should_exit_event: threading.Event):
    while not should_exit_event.wait(timeout=0.1):
        if f:
            f.flush()
        if should_exit_event.is_set():
            break
        # keep reading line-by-line to output any QEMU trap messages:
        i = qemu.expect(
            [pexpect.TIMEOUT, "KDB: enter:", pexpect.EOF, qemu.crlf],
            timeout=qemu.flush_interval,
            log_patterns=False,
        )
        if boot_cheribsd.PRETEND:
            time.sleep(1)
        elif i == 1:
            boot_cheribsd.failure("GOT KERNEL PANIC!", exit=False)
            boot_cheribsd.debug_kernel_panic(qemu)
            global KERNEL_PANIC  # noqa: PLW0603
            KERNEL_PANIC = True  # TODO: tell lit to abort now....
        elif i == 2:
            boot_cheribsd.failure("GOT QEMU EOF!", exit=False)
            # QEMU exited?
            break
    # One final expect to flush the buffer:
    qemu.expect([pexpect.TIMEOUT, pexpect.EOF], timeout=1)
    boot_cheribsd.success("QEMU output flushing thread terminated.")


def run_remote_lit_tests(
    testsuite: str,
    qemu: boot_cheribsd.CheriBSDInstance,
    args: argparse.Namespace,
    tempdir: str,
    mp_q: Optional[multiprocessing.Queue] = None,
    barrier: Optional[multiprocessing.Barrier] = None,
    llvm_lit_path: "Optional[str]" = None,
    lit_extra_args: Optional[list] = None,
) -> bool:
    try:
        import psutil  # noqa: F401
    except ImportError:
        boot_cheribsd.failure("Cannot run lit without `psutil` python module installed", exit=True)
    try:
        if mp_q:
            assert barrier is not None
        result = run_remote_lit_tests_impl(
            testsuite=testsuite,
            qemu=qemu,
            args=args,
            tempdir=tempdir,
            barrier=barrier,
            mp_q=mp_q,
            llvm_lit_path=llvm_lit_path,
            lit_extra_args=lit_extra_args,
        )
        if mp_q:
            mp_q.put((COMPLETED, args.internal_shard))
        return result
    except Exception:
        if mp_q:
            boot_cheribsd.failure("GOT EXCEPTION in shard ", args.internal_shard, ": ", sys.exc_info(), exit=False)
            e = sys.exc_info()[1]
            mp_q.put((FAILURE, args.internal_shard, str(type(e)) + ": " + str(e)))
        raise


def run_remote_lit_tests_impl(
    testsuite: str,
    qemu: boot_cheribsd.CheriBSDInstance,
    args: argparse.Namespace,
    tempdir: str,
    mp_q: Optional[multiprocessing.Queue] = None,
    barrier: Optional[multiprocessing.Barrier] = None,
    llvm_lit_path: "Optional[str]" = None,
    lit_extra_args: Optional[list] = None,
) -> bool:
    qemu.EXIT_ON_KERNEL_PANIC = False  # since we run multiple threads we shouldn't use sys.exit()
    boot_cheribsd.info("PID of QEMU: ", qemu.pid)

    if args.pretend and os.getenv("FAIL_TIMEOUT_BOOT") and args.internal_shard == 2:
        time.sleep(10)
    if mp_q:
        assert barrier is not None
    notify_main_process(args, MultiprocessStages.TESTING_SSH_CONNECTION, mp_q, barrier=barrier)
    if args.pretend and os.getenv("FAIL_RAISE_EXCEPTION") and args.internal_shard == 1:
        raise RuntimeError("SOMETHING WENT WRONG!")
    qemu.checked_run("cat /root/.ssh/authorized_keys", timeout=20)
    port = args.ssh_port
    user = "root"  # TODO: run these tests as non-root!
    test_build_dir = Path(args.build_dir)
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
""".format(
        user=user,
        port=port,
        ssh_key=Path(args.ssh_key).with_suffix(""),
        home=Path.home(),
    )
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
        boot_cheribsd.run_host_command(
            [
                "ssh",
                "-F",
                str(Path(tempdir, "config")),
                "cheribsd-test-instance",
                "-p",
                str(port),
                "--",
                "echo",
                "connection successful",
            ],
            cwd=str(test_build_dir),
        )
        connection_time = (datetime.datetime.utcnow() - connection_test_start).total_seconds()
        boot_cheribsd.success(prefix, " successful after ", connection_time, " seconds")

    check_ssh_connection("First SSH connection")
    controlmaster_running = False
    try:
        # Check that controlmaster worked by running ssh -O check
        boot_cheribsd.info("Checking if SSH control master is working.")
        boot_cheribsd.run_host_command(
            ["ssh", "-F", str(Path(tempdir, "config")), "cheribsd-test-instance", "-p", str(port), "-O", "check"],
            cwd=str(test_build_dir),
        )
        check_ssh_connection("Second SSH connection (with controlmaster)")
        controlmaster_running = True
    except subprocess.CalledProcessError:
        boot_cheribsd.failure(
            "WARNING: Could not connect to ControlMaster SSH connection. Running tests will be slower",
            exit=False,
        )
        with Path(tempdir, "config").open("w") as c:
            c.write(config_contents.format(control_persist="no"))
        check_ssh_connection("Second SSH connection (without controlmaster)")

    if args.pretend:
        time.sleep(2.5)

    extra_ssh_args = commandline_to_str(("-n", "-4", "-F", f"{tempdir}/config"))
    extra_scp_args = commandline_to_str(("-F", f"{tempdir}/config"))
    ssh_executor_args = [
        args.ssh_executor_script,
        "--host",
        "cheribsd-test-instance",
        "--extra-ssh-args=" + extra_ssh_args,
    ]
    if args.use_shared_mount_for_tests:
        # If we have a shared directory use that to massively speed up running tests
        tmpdir_name = args.shared_tmpdir_local.name
        ssh_executor_args.append("--shared-mount-local-path=" + str(args.shared_tmpdir_local))
        ssh_executor_args.append("--shared-mount-remote-path=/build/" + tmpdir_name)
    else:
        # slow executor using scp:
        ssh_executor_args.append("--extra-scp-args=" + extra_scp_args)
    executor = commandline_to_str(ssh_executor_args)
    # TODO: I was previously passing -t -t to ssh. Is this actually needed?
    boot_cheribsd.success("Running", testsuite, "tests with executor", executor)
    notify_main_process(args, MultiprocessStages.RUNNING_TESTS, mp_q)
    # have to use -j1 since otherwise CheriBSD might wedge
    if llvm_lit_path is None:
        llvm_lit_path = str(test_build_dir / "bin/llvm-lit")
    # Note: we require python 3 since otherwise it seems to deadlock in Jenkins
    lit_cmd = [sys.executable, llvm_lit_path, "-j1", "-vv", "-Dexecutor=" + executor, "test"]
    if lit_extra_args:
        lit_cmd.extend(lit_extra_args)
    if args.lit_debug_output:
        lit_cmd.append("--debug")
    # This does not work since it doesn't handle running ssh commands....
    lit_cmd.append("--timeout=120")  # 2 minutes max per test (in case there is an infinite loop)
    xunit_file: "Optional[Path]" = None
    if args.xunit_output:
        lit_cmd.append("--xunit-xml-output")
        xunit_file = Path(args.xunit_output).absolute()
        if args.internal_shard:
            xunit_file = xunit_file.with_name("shard-" + str(args.internal_shard) + "-" + xunit_file.name)
        lit_cmd.append(str(xunit_file))
    qemu_logfile = qemu.logfile
    if args.internal_shard:
        assert args.internal_num_shards, "Invalid call!"
        lit_cmd.append("--num-shards=" + str(args.internal_num_shards))
        lit_cmd.append("--run-shard=" + str(args.internal_shard))
        if xunit_file:
            assert qemu_logfile is not None, "Should have a valid logfile when running multiple shards"
            boot_cheribsd.success("Writing QEMU output to ", qemu_logfile)
    # Fixme starting lit at the same time does not work!
    # TODO: add the polling to the main thread instead of having another thread?
    # start the qemu output flushing thread so that we can see the kernel panic
    qemu.flush_interval = 15  # flush the logfile every 15 seconds
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
        # lit_proc = None
        # while False:
        #     line = lit_proc.readline()
        #     if shard_prefix:
        #         line = shard_prefix + line
        #     print(line)
        #     global KERNEL_PANIC
        #     # Abort once we detect a kernel panic
        #     if KERNEL_PANIC:
        #         lit_proc.sendintr()
        #     print(shard_prefix + lit_proc.read())
        # print("Lit finished.")
        # if lit_proc and lit_proc.exitstatus == 1:
        #     boot_cheribsd.failure(shard_prefix + "SOME TESTS FAILED", exit=False)
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
                boot_cheribsd.run_host_command(
                    [
                        "ssh",
                        "-F",
                        str(Path(tempdir, "config")),
                        "cheribsd-test-instance",
                        "-p",
                        str(port),
                        "-O",
                        "exit",
                    ],
                    cwd=str(test_build_dir),
                )
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
