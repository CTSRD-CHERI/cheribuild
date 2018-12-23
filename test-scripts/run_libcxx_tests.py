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
import run_remote_lit_test

def run_libcxx_tests(qemu: pexpect.spawn, args: argparse.Namespace) -> bool:
    with tempfile.TemporaryDirectory() as tempdir:
        return run_remote_lit_test.run_remote_lit_tests("libcxx", qemu, args, tempdir)

def add_cmdline_args(parser: argparse.ArgumentParser):
    parser.add_argument("--lit-debug-output", action="store_true")
    parser.add_argument("--xunit-output", default="qemu-libcxx-test-results.xml")
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
    parser.add_argument("--xunit-output", default="qemu-libcxx-test-results.xml")
    # Don't let this parser capture --help
    args, remainder = parser.parse_known_args(filter(lambda x: x != "-h" and x != "--help", sys.argv))
    # If parallel is set spawn N processes and use the lit --num-shards + --run-shard flags to split the work
    # Since a full run takes about 16 hours this should massively reduce the amount of time needed.

    if args.parallel_jobs and args.parallel_jobs != 1:
        # ensure we import junitparser only when running parallel jobs since it is not needed otherwise
        import junitparser
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
        # merge junit xml files
        if args.xunit_output:
            boot_cheribsd.success("Merging JUnit XML outputs")
            result = junitparser.JUnitXml()
            xunit_file = Path(args.xunit_output).absolute()
            for i in range(args.parallel_jobs):
                shard_num = i + 1
                shard_file = xunit_file.with_name("shard-" + str(shard_num) + "-" + xunit_file.name)
                if not shard_file.exists():
                    boot_cheribsd.failure("Error could not find JUnit XML ", shard_file, " for shard", shard_num,
                                          exit=False)
                    continue
                result += junitparser.JUnitXml.fromfile(str(shard_file))

            result.update_statistics()
            result.write(str(xunit_file))
            boot_cheribsd.success("Done merging JUnit XML outputs into ", xunit_file)
            print("Duration: ", result.time)
            print("Tests: ", result.tests)
            print("Failures: ", result.failures)
            print("Errors: ", result.errors)
        sys.exit()

    libcxx_main()
