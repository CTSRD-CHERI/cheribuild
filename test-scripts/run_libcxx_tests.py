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
import atexit
import datetime
import os
import signal
import sys
import tempfile
import time
import traceback
from multiprocessing import Barrier, Process, Queue
from pathlib import Path
from queue import Empty
from typing import Optional

import run_remote_lit_test
from run_remote_lit_test import mp_debug

# To combine the test result xmls
from run_tests_common import boot_cheribsd, junitparser, run_tests_main


def add_cmdline_args(parser: argparse.ArgumentParser):
    run_remote_lit_test.add_common_cmdline_args(
        parser,
        default_xunit_output="qemu-libcxx-test-results.xml",
        allow_multiprocessing=True,
    )


def run_shard(q: Queue, barrier: Barrier, num, total, ssh_port_queue, kernel, disk_image, build_dir):
    sys.argv.append("--internal-num-shards=" + str(total))
    sys.argv.append("--internal-shard=" + str(num))
    if kernel is not None:
        sys.argv.append("--internal-kernel-override=" + str(kernel))
    if disk_image is not None:
        sys.argv.append("--internal-disk-image-override=" + str(disk_image))

    # sys.argv.append("--pretend")
    print("Starting shard", num, sys.argv)
    boot_cheribsd.MESSAGE_PREFIX = "\033[0;34m" + "shard" + str(num) + ": \033[0m"
    boot_cheribsd.QEMU_LOGFILE = Path(build_dir, "shard-" + str(num) + ".log")
    boot_cheribsd.info("writing CheriBSD output to ", boot_cheribsd.QEMU_LOGFILE)
    try:
        libcxx_main(barrier=barrier, mp_queue=q, ssh_port_queue=ssh_port_queue, shard_num=num)
        boot_cheribsd.success("====> Job ", num, " completed")
    except Exception as e:
        boot_cheribsd.failure("Job ", num, " failed: ", e, exit=False)
        raise


def libcxx_main(
    barrier: "Optional[Barrier]" = None,
    mp_queue: "Optional[Queue]" = None,
    ssh_port_queue: "Optional[Queue]" = None,
    shard_num: "Optional[int]" = None,
):
    def set_cmdline_args(args: argparse.Namespace):
        boot_cheribsd.info("Setting args:", args)
        if mp_queue:
            # check that we don't get a conflict
            mp_debug(args, "Syncing shard ", shard_num, " with main process. Stage: assign SSH port")

            ssh_port_queue.put((args.ssh_port, shard_num))  # check that we don't get a conflict
            run_remote_lit_test.notify_main_process(
                args,
                run_remote_lit_test.MultiprocessStages.BOOTING_CHERIBSD,
                mp_queue,
                barrier,
            )
        if args.interact and (shard_num is not None or args.internal_num_shards or args.parallel_jobs):
            boot_cheribsd.failure("Cannot use --interact with multiple shards", exit=True)
            sys.exit()
        run_remote_lit_test.adjust_common_cmdline_args(args)

    def run_libcxx_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
        with tempfile.TemporaryDirectory(prefix="cheribuild-libcxx-tests-") as tempdir:
            # TODO: do we need lit_extra_args=["-Denable_filesystem=False"]?
            # Some of the tests might fail on a SMBFS directory.
            return run_remote_lit_test.run_remote_lit_tests(
                "libcxx",
                qemu,
                args,
                tempdir,
                mp_q=mp_queue,
                barrier=barrier,
            )

    try:
        run_tests_main(
            test_function=run_libcxx_tests,
            need_ssh=True,  # we need ssh running to execute the tests
            should_mount_builddir=True,
            argparse_setup_callback=add_cmdline_args,
            argparse_adjust_args_callback=set_cmdline_args,
        )
    except Exception as e:
        if mp_queue:
            boot_cheribsd.failure("GOT EXCEPTION in shard ", shard_num, ": ", sys.exc_info(), exit=False)
            # print(sys.exc_info()[2])
            boot_cheribsd.info("".join(traceback.format_tb(sys.exc_info()[2])))
            mp_queue.put((run_remote_lit_test.FAILURE, shard_num, str(type(e)) + ": " + str(e)))
        raise
    finally:
        boot_cheribsd.info("Finished running ", " ".join(sys.argv))


class LitShardProcess(Process):
    stage: Optional[run_remote_lit_test.MultiprocessStages] = None
    ssh_port = -1
    error_message = ""


def run_parallel(args: argparse.Namespace):
    if args.pretend:
        boot_cheribsd.PRETEND = True
    boot_cheribsd.MESSAGE_PREFIX = "\033[0;35m" + "main process: \033[0m"
    if args.parallel_jobs < 1:
        boot_cheribsd.failure("Invalid number of parallel jobs: ", args.parallel_jobs, exit=True)
    boot_cheribsd.success("Running ", args.parallel_jobs, " parallel jobs")
    # to ensure that all threads have started lit
    mp_barrier = Barrier(parties=args.parallel_jobs + 1, timeout=4 * 60 * 60)
    mp_q = Queue()
    ssh_port_queue = Queue()
    processes: "list[LitShardProcess]" = []
    # Extract the kernel + disk image in the main process to avoid race condition:
    kernel_path = (
        boot_cheribsd.maybe_decompress(Path(args.kernel), True, True, args, what="kernel") if args.kernel else None
    )
    disk_image_path = (
        boot_cheribsd.maybe_decompress(Path(args.disk_image), True, True, args, what="disk image")
        if args.disk_image
        else None
    )
    for i in range(args.parallel_jobs):
        shard_num = i + 1
        boot_cheribsd.info(args)
        p = LitShardProcess(
            target=run_shard,
            args=(
                mp_q,
                mp_barrier,
                shard_num,
                args.parallel_jobs,
                ssh_port_queue,
                kernel_path,
                disk_image_path,
                args.build_dir,
            ),
        )
        p.stage = run_remote_lit_test.MultiprocessStages.FINDING_SSH_PORT
        p.daemon = True  # kill process on parent exit
        p.name = "<LIBCXX test shard " + str(shard_num) + ">"
        p.start()
        processes.append(p)
        atexit.register(p.terminate)
    dump_processes(processes)
    try:
        return run_parallel_impl(args, processes, mp_q, mp_barrier, ssh_port_queue)
    except BaseException as e:
        boot_cheribsd.info("Got error while running run_parallel_impl (", type(e), "): ", e)
        raise
    finally:
        wait_or_terminate_all_shards(processes, max_time=5, timed_out=False)
        # merge junit xml files
        if args.xunit_output:
            boot_cheribsd.success("Merging JUnit XML outputs")
            result = junitparser.JUnitXml()
            xunit_file = Path(args.xunit_output).absolute()
            dump_processes(processes)
            for i in range(args.parallel_jobs):
                shard_num = i + 1
                shard_file = xunit_file.with_name("shard-" + str(shard_num) + "-" + xunit_file.name)
                mp_debug(args, processes[i], processes[i].stage)
                if shard_file.exists():
                    result += junitparser.JUnitXml.fromfile(str(shard_file))
                else:
                    error_msg = "ERROR: could not find JUnit XML " + str(shard_file) + " for shard " + str(shard_num)
                    boot_cheribsd.failure(error_msg, exit=False)
                    error_suite = junitparser.TestSuite(name="failed-shard-" + str(shard_num))
                    error_case = junitparser.TestCase(name="cannot-find-file")
                    error_case.classname = "failed-shard-" + str(shard_num)
                    error_case.result = junitparser.Error(message=error_msg)
                    error_suite.add_testcase(error_case)
                    result.add_testsuite(error_suite)
                if processes[i].stage != run_remote_lit_test.MultiprocessStages.EXITED:
                    error_msg = (
                        "ERROR: shard "
                        + str(shard_num)
                        + " did not exit cleanly! Was in stage: "
                        + processes[i].stage.value
                    )
                    if hasattr(processes[i], "error_message"):
                        error_msg += "\nError message:\n" + processes[i].error_message
                    error_suite = junitparser.TestSuite(name="bad-exit-shard-" + str(shard_num))
                    error_case = junitparser.TestCase(name="bad-exit-status")
                    error_case.result = junitparser.Error(message=error_msg)
                    error_suite.add_testcase(error_case)
                    result.add_testsuite(error_suite)

            result.update_statistics()
            result.write(str(xunit_file))
            if args.pretend:
                print(xunit_file.read_text())
            boot_cheribsd.success("Done merging JUnit XML outputs into ", xunit_file)
            print("Duration: ", result.time)
            print("Tests: ", result.tests)
            print("Failures: ", result.failures)
            print("Errors: ", result.errors)
            print("Skipped: ", result.skipped)


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
            except Exception as e:
                boot_cheribsd.failure(
                    "Could not join job ",
                    p.name,
                    " in ",
                    remaining_time.total_seconds(),
                    " seconds: ",
                    e,
                    exit=False,
                )
                timed_out = True
        if p.is_alive():
            boot_cheribsd.failure("Parallel job ", p.name, " did not exit cleanly!", exit=False)
            p.terminate()
            time.sleep(1)
            os.kill(p.pid, signal.SIGKILL)
            time.sleep(1)
        if p.is_alive():
            boot_cheribsd.failure("ERROR: Could not kill child process ", p.name, ", pid=", p.pid, exit=False)


def dump_processes(processes: "list[LitShardProcess]"):
    for i, p in enumerate(processes):
        boot_cheribsd.info("Subprocess ", i + 1, " ", p, " -- current stage: ", p.stage.value)


def run_parallel_impl(
    args: argparse.Namespace,
    processes: "list[LitShardProcess]",
    mp_q: Queue,
    mp_barrier: Barrier,
    ssh_port_queue: Queue,
):
    timed_out = False
    starttime = datetime.datetime.now()
    ssh_ports = []  # check that we don't have multiple parallel jobs trying to use the same port
    assert not mp_barrier.broken, mp_barrier
    # FIXME: without this sleep it fails in jenkins (is the python version there broken?)
    # Works just fine everywhere else where I test it...
    boot_cheribsd.info("Waiting 5 seconds before releasing barrier")
    time.sleep(5)
    mp_debug(args, "Waiting for SSH port barrier")
    mp_barrier.wait(timeout=10)  # wait for ssh ports to be assigned
    for i in range(len(processes)):
        try:
            ssh_port, index = ssh_port_queue.get(timeout=1)
            assert index <= len(processes)
            print("SSH port for ", processes[index - 1].name, "is", ssh_port)
            processes[index - 1].ssh_port = ssh_port
            if ssh_port in ssh_ports:
                timed_out = True  # kill all child processes
                boot_cheribsd.failure("ERROR: reusing the same SSH port in multiple jobs: ", ssh_port, exit=False)
        except Empty:
            # This seems to be happening in jenkins? Barrier should ensure that we can read without blocking!
            timed_out = True  # kill all child processes
            boot_cheribsd.failure("ERROR: Could not determine SSH port for one of the processes!", exit=False)

    # wait for the success/failure message from the process:
    # if the shard takes longer than 4 hours to run something went wrong
    start_time = datetime.datetime.utcnow()
    max_test_duration = datetime.timedelta(seconds=4 * 60 * 60)
    test_end_time = start_time + max_test_duration
    # If any shard has not yet booted CheriBSD after 10 minutes something went horribly wrong
    max_boot_time = datetime.timedelta(seconds=10 * 60) if not args.pretend else datetime.timedelta(seconds=5)
    boot_cheribsd.info("Waiting for all shards to boot...")
    boot_end_time = start_time + max_boot_time
    remaining_processes = processes.copy()
    not_booted_processes = processes.copy()
    retrying_queue_read = False
    while len(remaining_processes) > 0:
        if timed_out:
            for p in remaining_processes:
                p.stage = run_remote_lit_test.MultiprocessStages.TIMED_OUT
            break
        loop_start_time = datetime.datetime.utcnow()
        num_shards_not_booted = len(not_booted_processes)
        if num_shards_not_booted > 0:
            mp_debug(args, "Still waiting for ", num_shards_not_booted, " shards to boot")
            if loop_start_time > boot_end_time:
                timed_out = True
                boot_cheribsd.failure(
                    "ERROR: ",
                    num_shards_not_booted,
                    " shards did not boot within ",
                    max_boot_time,
                    ". Shards remaining: ",
                    remaining_processes,
                    exit=False,
                )
                dump_processes(processes)
                continue

        mp_debug(args, "Still waiting for ", remaining_processes, " to finish")
        if boot_end_time > test_end_time:
            timed_out = True
            boot_cheribsd.failure(
                "Reached test timeout of",
                max_test_duration,
                " with ",
                len(remaining_processes),
                "shards remaining: ",
                remaining_processes,
                exit=False,
            )
            dump_processes(processes)
            continue
        remaining_test_time = test_end_time - loop_start_time
        max_timeout = 120.0 if not args.pretend else 1.0
        try:
            shard_result = mp_q.get(timeout=min(max(1.0, remaining_test_time.total_seconds()), max_timeout))
            retrying_queue_read = False
            mp_debug(args, "Got message:", shard_result)
            target_process = processes[shard_result[1] - 1]
            if shard_result[0] == run_remote_lit_test.COMPLETED:
                boot_cheribsd.success("===> Shard ", shard_result[1], " completed successfully.")
                mp_debug(args, "Shard ", target_process, "exited!")
                if target_process in remaining_processes:
                    remaining_processes.remove(target_process)
                target_process.stage = run_remote_lit_test.MultiprocessStages.EXITED
            elif shard_result[0] == run_remote_lit_test.NEXT_STAGE:
                mp_debug(args, "===> Shard ", shard_result[1], " reached next stage: ", shard_result[2])
                if target_process.stage == run_remote_lit_test.MultiprocessStages.BOOTING_CHERIBSD:
                    not_booted_processes.remove(target_process)
                    boot_cheribsd.success(
                        "Shard ",
                        shard_result[1],
                        " has booted successfully afer ",
                        loop_start_time - start_time,
                    )
                    if len(not_booted_processes) == 0:
                        boot_cheribsd.success(
                            "All shards have booted succesfully. Releasing barrier (num_waiting = ",
                            mp_barrier.n_waiting,
                            ")",
                        )
                        assert mp_barrier.n_waiting == len(processes), f"{mp_barrier.n_waiting} != {len(processes)}"
                        mp_barrier.wait(timeout=10)
                        boot_cheribsd.success("Barrier has been released, tests should run now.")
                # assert target_process.stage < shard_result[2], "STAGE WENT BACKWARDS?"
                target_process.stage = shard_result[2]
            elif shard_result[0] == run_remote_lit_test.FAILURE:
                previous_stage = target_process.stage
                target_process.stage = run_remote_lit_test.MultiprocessStages.FAILED
                target_process.error_message = shard_result[2]
                if target_process in remaining_processes:
                    remaining_processes.remove(target_process)
                if previous_stage != run_remote_lit_test.MultiprocessStages.RUNNING_TESTS:
                    boot_cheribsd.failure(
                        "===> FATAL: Shard ",
                        target_process,
                        " failed before running tests stage: ",
                        previous_stage,
                        " -> Aborting all other shards",
                        exit=False,
                    )
                    timed_out = True
                    break
                else:
                    boot_cheribsd.failure(
                        "===> ERROR: Shard ",
                        shard_result[1],
                        " failed while running tests: ",
                        shard_result[2],
                        exit=True,
                    )
            else:
                boot_cheribsd.failure("===> FATAL: Received invalid shard result message: ", shard_result, exit=True)
        except Empty:
            mp_debug(args, "Got Empty read from QUEUE. Checking ", remaining_processes)
            for p in list(remaining_processes):
                if not p.is_alive():
                    mp_debug(args, "Found dead process", p)
                    if retrying_queue_read:
                        mp_debug(args, "Already retried read after finding dead process", p)
                        boot_cheribsd.failure("===> ERROR: shard ", p, " died without sending a message!", exit=False)
                        remaining_processes.remove(p)
                    else:
                        # Try to read from the queue one more time to see if we missed a message
                        retrying_queue_read = True
                        mp_debug(args, "Retrying read after finding dead process", p)
                        break
            continue
        except KeyboardInterrupt:
            dump_processes(processes)
            boot_cheribsd.failure("GOT KEYBOARD INTERRUPT! EXITING!", exit=False)
            return

    if not timed_out:
        if not_booted_processes:
            boot_cheribsd.failure(
                "FATAL: all processes exited but some still not booted? ",
                not_booted_processes,
                exit=True,
            )
        boot_cheribsd.success("All shards have terminated")
    # If we got an error we should not end up here -> all processes should be in stage exited
    dump_processes(processes)

    # All shards should have completed -> give them 60 seconds to shut down cleanly
    wait_or_terminate_all_shards(processes, max_time=60, timed_out=timed_out)
    if timed_out:
        time.sleep(0.2)
        boot_cheribsd.failure("Error running the test jobs!", exit=True)
    else:
        boot_cheribsd.success("All parallel jobs completed!")
    boot_cheribsd.success("Total execution time for parallel libcxx tests: ", datetime.datetime.now() - starttime)


def main():
    parser = boot_cheribsd.get_argument_parser()
    parser.add_argument("--build-dir")  # needed later
    add_cmdline_args(parser)
    # Don't let this parser capture --help
    args, remainder = parser.parse_known_args(list(filter(lambda x: x != "-h" and x != "--help", sys.argv)))
    # If parallel is set spawn N processes and use the lit --num-shards + --run-shard flags to split the work
    # Since a full run takes about 16 hours this should massively reduce the amount of time needed.
    if args.parallel_jobs and args.parallel_jobs != 1:
        run_parallel(args)
    else:
        libcxx_main()


if __name__ == "__main__":
    main()
