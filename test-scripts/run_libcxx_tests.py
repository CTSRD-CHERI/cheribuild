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
import queue
import threading
from multiprocessing import Process, Semaphore, Queue, Barrier
from pathlib import Path
import boot_cheribsd
import run_remote_lit_test


def add_cmdline_args(parser: argparse.ArgumentParser):
    parser.add_argument("--lit-debug-output", action="store_true")
    parser.add_argument("--multiprocessing-debug", action="store_true")
    parser.add_argument("--xunit-output", default="qemu-libcxx-test-results.xml")
    parser.add_argument("--parallel-jobs", metavar="N", type=int, help="Split up the testsuite into N parallel jobs")
    # For the parallel jobs
    parser.add_argument("--internal-num-shards", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--internal-shard", type=int, help=argparse.SUPPRESS)

def run_shard(q: Queue, barrier: Barrier, num, total):
    sys.argv.append("--internal-num-shards=" + str(total))
    sys.argv.append("--internal-shard=" + str(num))
    # sys.argv.append("--pretend")
    print("shard", num, sys.argv)
    try:
        libcxx_main(barrier=barrier, queue=q)
        print("Job", num, "completed")
    except Exception as e:
        boot_cheribsd.failure("Job ", num, " failed!!", e, exit=False)


def libcxx_main(barrier: Barrier = None, queue: Queue = None):
    def set_cmdline_args(args: argparse.Namespace):
        print("Setting args:", args)
        if queue:
            # check that we don't get a conflict
            queue.put((run_remote_lit_test.COMPLETED, args.internal_shard, "setup an SSH port"))
            if args.multiprocessing_debug:
                boot_cheribsd.success("Syncing shard ", args.internal_shard, " with main process. Stage: assign SSH port")
            barrier.wait()
            queue.put((args.ssh_port, args.internal_shard))  # check that we don't get a conflict
        if args.interact and (args.internal_shard or args.internal_num_shards or args.parallel_jobs):
            boot_cheribsd.failure("Cannot use --interact with multiple shards")
            sys.exit()

    def run_libcxx_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
        with tempfile.TemporaryDirectory() as tempdir:
            return run_remote_lit_test.run_remote_lit_tests("libcxx", qemu, args, tempdir, mp_q=queue, mp_barrier=barrier)

    from run_tests_common import run_tests_main
    try:
        run_tests_main(test_function=run_libcxx_tests, need_ssh=True, # we need ssh running to execute the tests
                       argparse_setup_callback=add_cmdline_args, argparse_adjust_args_callback=set_cmdline_args)
    finally:
        print("Finished running ", " ".join(sys.argv))


def run_parallel(args: argparse.Namespace):
    if args.parallel_jobs < 1:
        boot_cheribsd.failure("Invalid number of parallel jobs: ", args.parallel_jobs, exit=True)
    boot_cheribsd.success("Running ", args.parallel_jobs, " parallel jobs")
    # to ensure that all threads have started lit
    mp_barrier = Barrier(parties=args.parallel_jobs + 1, timeout=4 * 60 * 60)
    mp_q = Queue()
    processes = []
    for i in range(args.parallel_jobs):
        shard_num = i + 1
        p = Process(target=run_shard, args=(mp_q, mp_barrier, shard_num, args.parallel_jobs))
        p.daemon = True  # kill process on parent exit
        p.name = "<LIBCXX test shard " + str(shard_num) + ">"
        p.start()
        processes.append(p)
        atexit.register(p.terminate)
    print(processes)
    try:
        return run_parallel_impl(args, processes, mp_q, mp_barrier)
    finally:
        wait_or_terminate_all_shards(processes, max_time=5, timed_out=False)

def wait_or_terminate_all_shards(processes, max_time, timed_out):
    assert max_time > 0 or timed_out
    max_end_time = datetime.datetime.utcnow() + datetime.timedelta(seconds=max_time)
    for i, p in enumerate(processes):
        # don't wait for completion if we've already timed out
        if not timed_out:
            remaining_time = max_end_time - datetime.datetime.utcnow()
            # wait for completion
            try:
                p.join(timeout=remaining_time.total_seconds())
            except:
                boot_cheribsd.failure("Could not join job ", p.name, " in ", remaining_time.total_seconds(),
                                      " seconds", exit=False)
                timed_out = True
        if p.is_alive():
            boot_cheribsd.failure("Parallel job ", p.name, " did not exit cleanly!", exit=False)
            p.terminate()
            time.sleep(1)
            os.kill(p.pid, signal.SIGKILL)
            time.sleep(1)
        if p.is_alive():
            boot_cheribsd.failure("ERROR: Could not kill child process ", p.name, ", pid=", p.pid, exit=False)

def run_parallel_impl(args: argparse.Namespace, processes: "typing.List[Process]", mp_q: Queue, mp_barrier: Barrier):
    # ensure we import junitparser only when running parallel jobs since it is not needed otherwise
    import junitparser

    def sync_stage(success_msg, failure_msg, timeout):
        nonlocal timed_out
        if args.pretend:
            timeout = 10
        assert not mp_barrier.broken, mp_barrier
        try:
            if args.multiprocessing_debug:
                boot_cheribsd.info("Syncing main process with shards. Stage:", success_msg)
                boot_cheribsd.info("NOTE: ", mp_barrier.n_waiting, "/", mp_barrier.parties, " shards are waiting.")
            mp_barrier.wait(timeout=timeout)  # wait for QEMU to start
            for _ in range(len(processes)):
                subprocess_msg = mp_q.get_nowait()
                if args.multiprocessing_debug:
                    boot_cheribsd.info("GOT MESSAGE FROM CHILD:", subprocess_msg)
                if subprocess_msg[0] != run_remote_lit_test.COMPLETED:
                    raise RuntimeError("Failed to sync with child process: " + subprocess_msg + "\nStage was '" +
                                       success_msg + "'")
                if subprocess_msg[2] != success_msg:
                    raise RuntimeError("Got wrong stage from child process: " + repr(subprocess_msg) +
                                       "\nExpected stage was '" + success_msg + "'")
            boot_cheribsd.success("===> All shards have ", success_msg)
            time.sleep(0.5)
        except Exception as e:
            timed_out = True  # kill all child processes
            boot_cheribsd.failure("ERROR: At least one process ", failure_msg, ": ", type(e), ":", e, exit=False)
            boot_cheribsd.failure("NOTE: ", mp_barrier.n_waiting, "/", mp_barrier.parties, " shards were successful.",
                                  exit=False)
            wait_or_terminate_all_shards(processes, max_time=0, timed_out=timed_out)
            boot_cheribsd.failure("===> ABORTING TEST RUN!", exit=True)

    timed_out = False
    starttime = datetime.datetime.now()
    ssh_ports = []  # check that we don't have multiple parallel jobs trying to use the same port
    sync_stage(success_msg="setup an SSH port", failure_msg="failed to get SSH port", timeout=10)
    for i in range(len(processes)):
        ssh_port, index = mp_q.get_nowait()
        assert index <= len(processes)
        print("SSH port for ", processes[index - 1].name, "is", ssh_port)
        if ssh_port in ssh_ports:
            timed_out = True  # kill all child processes
            boot_cheribsd.failure("ERROR: reusing the same SSH port in multiple jobs: ", ssh_port, exit=False)

    sync_stage(success_msg="booted CheriBSD", failure_msg="failed to boot CheriBSD", timeout=10 * 60)
    sync_stage(success_msg="checked SSH connection", failure_msg="failed to check SSH connection", timeout=10 * 60)


    # wait for the success/failure message from the process:
    # if the shard takes longer than 4 hours to run something went wrong
    max_test_duration = datetime.timedelta(seconds=4 * 60 * 60)
    test_end_time = datetime.datetime.utcnow() + max_test_duration
    completed_shards = 0
    remaining_processes = processes.copy()
    while len(remaining_processes) > 0:
        if args.multiprocessing_debug:
            boot_cheribsd.info("Still waiting for ", remaining_processes)
        if datetime.datetime.utcnow() > test_end_time:
            timed_out = True
            boot_cheribsd.failure("Reached test timeout of", max_test_duration, " with ", len(remaining_processes),
                                  "shards remaining: ", remaining_processes, exit=False)
        if timed_out:
            break
        remaining_test_time = test_end_time - datetime.datetime.utcnow()
        max_timeout = 120.0 if not args.pretend else 2.0
        try:
            shard_result = mp_q.get(timeout=min(max(1.0, remaining_test_time.total_seconds()), max_timeout))
            completed_shards += 1
            if shard_result[0] == run_remote_lit_test.COMPLETED:
                boot_cheribsd.success("===> Shard ", shard_result[1], " completed successfully.")
            elif shard_result[0] == run_remote_lit_test.FAILURE:
                boot_cheribsd.failure("===> ERROR: shard ", shard_result[1], " failed: ", shard_result[2])
            else:
                boot_cheribsd.failure("Received invalid shard result message: ", shard_result)
            exited_process = processes[shard_result[1] - 1]
            if args.multiprocessing_debug:
                boot_cheribsd.info("Shard ", exited_process, "exited!")
            if exited_process in remaining_processes:
                remaining_processes.remove(exited_process)
        except queue.Empty as e:
            if args.multiprocessing_debug:
                boot_cheribsd.info("Got Empty read from QUEUE. Checking ", remaining_processes)
            for p in list(remaining_processes):
                if not p.is_alive():
                    if args.multiprocessing_debug:
                        boot_cheribsd.info("Found dead process", p)
                    remaining_processes.remove(p)
            continue
        except Exception as e:
            boot_cheribsd.failure("Unknown error waiting for next shard to exit: ", e, exit=True)
            timed_out = True

    boot_cheribsd.success("All shards have terminated")

    # All shards should have completed -> give them 60 seconds to shut down cleanly
    wait_or_terminate_all_shards(processes, max_time=60, timed_out=timed_out)
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
                error_msg = "ERROR: could not find JUnit XML " + str(shard_file) + " for shard " + str(shard_num)
                boot_cheribsd.failure(error_msg, exit=False)
                error_suite = junitparser.TestSuite(name="failed-shard-" + str(shard_num))
                error_case = junitparser.TestCase(name="cannot-find-file")
                error_case.result = junitparser.Error(message=error_msg)
                error_suite.add_testcase(error_case)
                result.add_testsuite(error_suite)
                continue
            result += junitparser.JUnitXml.fromfile(str(shard_file))

        result.update_statistics()
        result.write(str(xunit_file))
        if args.pretend:
            print(xunit_file.read_text())
        boot_cheribsd.success("Done merging JUnit XML outputs into ", xunit_file)
        print("Duration: ", result.time)
        print("Tests: ", result.tests)
        print("Failures: ", result.failures)
        print("Errors: ", result.errors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parallel-jobs", metavar="N", type=int, help="Split up the testsuite into N parallel jobs")
    parser.add_argument("--xunit-output", default="qemu-libcxx-test-results.xml")
    parser.add_argument("--pretend", "-p", action="store_true")
    parser.add_argument("--multiprocessing-debug", action="store_true")
    # Don't let this parser capture --help
    args, remainder = parser.parse_known_args(filter(lambda x: x != "-h" and x != "--help", sys.argv))
    # If parallel is set spawn N processes and use the lit --num-shards + --run-shard flags to split the work
    # Since a full run takes about 16 hours this should massively reduce the amount of time needed.
    if args.parallel_jobs and args.parallel_jobs != 1:
        run_parallel(args)
    else:
        libcxx_main()

if __name__ == '__main__':
    main()
