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
import atexit
import pexpect
import argparse
import os
import subprocess
import tempfile
import time
import datetime
import signal
import sys
import threading
from multiprocessing import Process, Semaphore, Queue
from pathlib import Path
import boot_cheribsd

KERNEL_PANIC = False


def flush_thread(f, qemu: pexpect.spawn):
    while True:
        if f:
            f.flush()
        i = qemu.expect([pexpect.TIMEOUT, "KDB: enter:"], timeout=30)
        if boot_cheribsd.PRETEND:
            time.sleep(1)
        elif i == 1:
            boot_cheribsd.failure("GOT KERNEL PANIC!", exit=False)
            boot_cheribsd.debug_kernel_panic(qemu)
            global KERNEL_PANIC
            KERNEL_PANIC = True
            # TODO: tell lit to abort now....

def run_tests_impl(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace, tempdir: str):
    qemu.EXIT_ON_KERNEL_PANIC = False # since we run multiple threads we shouldn't use sys.exit()
    print("PID of QEMU:", qemu.pid)
    port = args.ssh_port
    user = "root"  # TODO: run these tests as non-root!
    libcxx_dir = Path(args.build_dir)
    (libcxx_dir / "tmp").mkdir(exist_ok=True)
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
    Path(Path.home(), "controlmasters").mkdir(exist_ok=True)
    boot_cheribsd.run_host_command(["cat", str(Path(tempdir, "config"))])
    # Check that the config file works:
    boot_cheribsd.run_host_command(["ssh", "-F", str(Path(tempdir, "config")), "cheribsd-test-instance", "-p", str(port), "--", "echo", "connection successful"], cwd=str(libcxx_dir))

    executor = 'SSHExecutorWithNFSMount("cheribsd-test-instance", username="{user}", port={port}, nfs_dir="{host_dir}", ' \
               'path_in_target="/mnt/tmp", extra_ssh_flags=["-F", "{tempdir}/config", "-n", "-4", "-t", "-t"], ' \
               'extra_scp_flags=["-F", "{tempdir}/config"])'.format(user=user, port=port, host_dir=str(libcxx_dir / "tmp"), tempdir=tempdir)

    print("Running libcxx_tests with executor", executor)
    # have to use -j1 + --single-process since otherwise CheriBSD might wedge
    lit_cmd = [str(libcxx_dir / "bin/llvm-lit"), "-j1", "-vv", "--single-process", "-Dexecutor=" + executor, "test"]
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
    t = threading.Thread(target=flush_thread, args=(qemu_logfile, qemu))
    t.daemon = True
    t.start()
    shard_prefix = "SHARD" + str(args.internal_shard) + ": " if args.internal_shard else ""
    try:
        boot_cheribsd.success("Starting llvm-lit: cd ", libcxx_dir, " && ", " ".join(lit_cmd))
        boot_cheribsd.run_host_command(lit_cmd, cwd=str(libcxx_dir))
        # lit_proc = pexpect.spawnu(lit_cmd[0], lit_cmd[1:], echo=True, timeout=60, cwd=str(libcxx_dir))
        # TODO: get stderr!!
        # while lit_proc.isalive():
        lit_proc = None
        while False:
            line = lit_proc.readline()
            if shard_prefix:
                line =  shard_prefix + line
            print(line)
            global KERNEL_PANIC
            # Abort once we detect a kernel panic
            if KERNEL_PANIC:
                lit_proc.sendintr()
            print(shard_prefix + lit_proc.read())
        print("Lit finished.")
        if lit_proc and lit_proc.exitstatus == 1:
            boot_cheribsd.failure(shard_prefix + "SOME TESTS FAILED", exit=False)
    except:
        raise
    finally:
        if qemu_logfile:
            qemu_logfile.flush()

    if False:
        # slow executor using scp:
        executor = 'SSHExecutor("localhost", username="{user}", port={port})'.format(user=user, port=port)

def run_libcxx_tests(qemu: pexpect.spawn, args: argparse.Namespace):
    with tempfile.TemporaryDirectory() as tempdir:
        run_tests_impl(qemu, args, tempdir)


def add_cmdline_args(parser: argparse.ArgumentParser):
    parser.add_argument("--lit-debug-output", action="store_true")
    parser.add_argument("--xunit-output", default="libcxx-tests.xml")
    parser.add_argument("--parallel-jobs", metavar="N", type=int, help="Split up the testsuite into N parallel jobs")
    # For the parallel jobs
    parser.add_argument("--internal-num-shards", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--internal-shard", type=int, help=argparse.SUPPRESS)


_MP_QUEUE = None  # type: Queue


def set_cmdline_args(args: argparse.Namespace):
    print(args)
    global _MP_QUEUE
    if _MP_QUEUE:
        _MP_QUEUE.put(args.ssh_port)  # check that we don't get a conflict
    if args.interact and (args.internal_shard or args.internal_num_shards or args.parallel_jobs):
        boot_cheribsd.failure("Cannot use --interact with ")
        sys.exit()


def run_shard(q: Queue, sem: Semaphore, num, total):
    global _MP_QUEUE
    _MP_QUEUE = q
    sys.argv.append("--internal-num-shards=" + str(total))
    sys.argv.append("--internal-shard=" + str(num))
    # sys.argv.append("--pretend")
    print("shard", num, sys.argv)
    try:
        libcxx_main()
        print("Job", num, "completed")
    except Exception as e:
        print("Job", num, "failed!!", e)
    sem.release()


def libcxx_main():
    from run_tests_common import run_tests_main
    try:
        run_tests_main(test_function=run_libcxx_tests, need_ssh=True, # we need ssh running to execute the tests
                       argparse_setup_callback=add_cmdline_args, argparse_adjust_args_callback=set_cmdline_args)
    finally:
        print("Finished running ", " ".join(sys.argv))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--parallel-jobs", metavar="N", type=int, help="Split up the testsuite into N parallel jobs")
    # Don't let this parser capture --help
    args, remainder = parser.parse_known_args(filter(lambda x: x != "-h" and x != "--help", sys.argv))
    # If parallel is set spawn N processes and use the lit --num-shards + --run-shard flags to split the work
    # Since a full run takes about 16 hours this should massively reduce the amount of time needed.

    if args.parallel_jobs and args.parallel_jobs != 1:
        if args.parallel_jobs < 1:
            boot_cheribsd.failure("Invalid number of parallel jobs: ", args.parallel_jobs, exit=True)
        starttime = datetime.datetime.now()
        boot_cheribsd.success("Running ", args.parallel_jobs, " parallel jobs")
        sem = Semaphore(value=0) # ensure that we block immediately
        mp_q = Queue()
        processes = []
        for i in range(args.parallel_jobs):
            shard_num = i + 1
            p = Process(target=run_shard, args=(mp_q, sem, shard_num, args.parallel_jobs))
            p.daemon = True     # kill process on parent exit
            p.name = "<LIBCXX test shard " + str(shard_num) + ">"
            p.start()
            processes.append(p)
            atexit.register(p.terminate)
        print(processes)
        timed_out = False

        ssh_ports = []  # check that we don't have multiple parallel jobs trying to use the same port
        for i, p in enumerate(processes):
            ssh_port = mp_q.get()
            print("SSH port for ", p.name, "is", ssh_port)
            if ssh_port in ssh_ports:
                timed_out = True  # kill all child processes
                boot_cheribsd.failure("ERROR: reusing the same SSH port in multiple jobs: ", ssh_port, exit=False)

        for i, p in enumerate(processes):
            # wait for completion
            max_time = 60 * 60 * 4
            # if the shard takes longer than 4 hours to run something went wrong
            if False:
                if timed_out or not sem.acquire(timeout=max_time):
                    boot_cheribsd.failure("Failed to acquire semaphore ", i, " for worker ", p.name, exit=False)
                    timed_out = True
                else:
                    boot_cheribsd.success("Acquired semaphore ", i)
                # Since the semaphores were released this should complete immediately
                p.join(timeout=1)
            # don't wait for completion if we've already timed out
            if not timed_out:
                p.join(timeout=60 * 60 * 4)
            if p.is_alive():
                boot_cheribsd.failure("Parallel job ", p.name, " did not exit cleanly!", exit=False)
                p.terminate()
                time.sleep(1)
                os.kill(p.pid, signal.SIGKILL)
                time.sleep(1)
            if p.is_alive():
                boot_cheribsd.failure("ERROR: Could not kill child process ", p.name, ", pid=", p.pid, exit=False)
        if timed_out:
            time.sleep(0.2)
            boot_cheribsd.failure("Error running the test jobs!", exit=True)
        else:
            boot_cheribsd.success("All parallel jobs completed!")
        boot_cheribsd.success("Total execution time for parallel libcxx tests: ", datetime.datetime.now() - starttime)
        sys.exit()

    libcxx_main()
