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
from multiprocessing import Process, Semaphore
from pathlib import Path
import boot_cheribsd


def run_tests_impl(qemu: pexpect.spawn, args: argparse.Namespace, tempdir: str):
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
        ControlMaster no
""".format(user=user, port=port, ssh_key=Path(args.ssh_key).with_suffix(""), home=Path.home())
    # print("Writing ssh config: ", config_contents)
    with Path(tempdir, "config").open("w") as c:
        c.write(config_contents)
    Path(Path.home(), "controlmasters").mkdir(exist_ok=True)
    boot_cheribsd.run_host_command(["cat", str(Path(tempdir, "config"))])

    executor = 'SSHExecutorWithNFSMount("cheribsd-test-instance", username="{user}", port={port}, nfs_dir="{host_dir}", ' \
               'path_in_target="/mnt/tmp", extra_ssh_flags=["-F", "{tempdir}/config"], ' \
               'extra_scp_flags=["-F", "{tempdir}/config"])'.format(user=user, port=port, host_dir=str(libcxx_dir / "tmp"), tempdir=tempdir)
    print("Running libcxx_tests with executor", executor)
    # TODO: sharding + xunit output
    # have to use -j1 since otherwise CheriBSD might wedge
    lit_cmd = [str(libcxx_dir / "bin/llvm-lit"), "-j1", "-vv", "-Dexecutor=" + executor, "test"]
    if args.lit_debug_output:
        lit_cmd.append("--debug")
    #lit_cmd.append("--timeout=600")  # 10 minutes max per test (in case there is an infinite loop)
    lit_cmd.append("--timeout=5")
    if args.xunit_output:
        lit_cmd.append("--xunit-xml-output")
        xunit_file = Path(args.xunit_output).absolute()
        if args.internal_shard:
            xunit_file = xunit_file.with_name("shard-" + str(args.internal_shard) + "-" + xunit_file.name)
        lit_cmd.append(str(xunit_file))
    if args.internal_shard:
        assert args.internal_num_shards, "Invalid call!"
        lit_cmd.append("--num-shards=" + str(args.internal_num_shards))
        lit_cmd.append("--run-shard=" + str(args.internal_shard))
    # TODO: --num-shards = 16
    # --run-shard = N
    print("Will run ", " ".join(lit_cmd))
    # Fixme starting lit at the same time does not work!
    boot_cheribsd.run_host_command(lit_cmd, cwd=str(libcxx_dir))

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

def set_cmdline_args(args: argparse.Namespace):
    print(args)
    if args.interact and (args.internal_shard or args.internal_num_shards or args.parallel_jobs):
        boot_cheribsd.failure("Cannot use --interact with ")
        sys.exit()


def run_shard(sem: Semaphore, num, total):
    #sys.argv.append("--internal-num-shards=" + str(total))
    sys.argv.append("--internal-num-shards=800")
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
    run_tests_main(test_function=run_libcxx_tests, need_ssh=True, # we need ssh running to execute the tests
                   argparse_setup_callback=add_cmdline_args, argparse_adjust_args_callback=set_cmdline_args)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--parallel-jobs", metavar="N", type=int, help="Split up the testsuite into N parallel jobs")
    # Don't let this parser capture --help
    args, remainder = parser.parse_known_args(filter(lambda x: x != "-h" and x != "--help", sys.argv))
    # If parallel is set spawn N processes and use the lit --num-shards + --run-shard flags to split the work
    # Since a full run takes about 16 hours this should massively reduce the amount of time needed.
    if args.parallel_jobs:
        starttime = datetime.datetime.now()
        boot_cheribsd.success("Running ", args.parallel_jobs, " parallel jobs")
        sem = Semaphore(value=0) # ensure that we block immediately
        processes = []
        for i in range(args.parallel_jobs):
            shard_num = i + 1
            p = Process(target=run_shard, args=(sem, shard_num, args.parallel_jobs))
            p.daemon = True     # kill process on parent exit
            p.name = "<LIBCXX test shard " + str(shard_num) + ">"
            p.start()
            processes.append(p)
            atexit.register(p.terminate)
        print(processes)
        timed_out = False
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
            boot_cheribsd.failure("Timeout running the test jobs!")
        else:
            boot_cheribsd.success("All parallel jobs completed!")
        boot_cheribsd.success("Total execution time for parallel libcxx tests: ", datetime.datetime.now() - starttime)
        sys.exit()

    libcxx_main()
