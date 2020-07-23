#
# Copyright (c) 2016 Alex Richardson
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
import fcntl
import os
import shutil
import signal
import subprocess
import sys
# noinspection PyUnresolvedReferences
from pathlib import Path

from .config.defaultconfig import CheribuildAction, DefaultCheriConfig
# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from .configloader import ConfigLoader here because that will only update the local copy!
# https://stackoverflow.com/questions/3536620/how-to-change-a-module-variable-from-another-module
from .config.loader import JsonAndCommandLineConfigLoader, JsonAndCommandLineConfigOption
# make sure all projects are loaded so that target_manager gets populated
# noinspection PyUnresolvedReferences
from .projects import *  # noqa: F401,F403
# noinspection PyUnresolvedReferences
from .projects.cross import *  # noqa: F401,F403
from .projects.project import SimpleProject
from .targets import target_manager
from .utils import (AnsiColour, coloured, commandline_to_str, fatal_error, get_program_version,
                    have_working_internet_connection, init_global_config, print_command, run_command, status_update)

DIRS_TO_CHECK_FOR_UPDATES = [Path(__file__).parent.parent]


def update_check():
    for d in DIRS_TO_CHECK_FOR_UPDATES:
        _update_check(d)


def _update_check(d: Path):
    if not shutil.which("git"):
        return
    # Avoid update check if we don't have an internet connection
    if not have_working_internet_connection():
        return
    # check if new commits are available
    project_dir = str(d)
    run_command(["git", "fetch"], cwd=project_dir, timeout=5)
    output = subprocess.check_output(["git", "status", "-uno"], cwd=project_dir)
    behind_index = output.find(b"Your branch is behind ")
    if behind_index > 0:
        msg_end = output.find(b"\n  (use \"git pull\" to update your local branch)")
        if msg_end > 0:
            output = output[behind_index:msg_end]
        status_update("Current", d.name, "checkout can be updated: ", output.decode("utf-8"))
        if input("Would you like to update before continuing? y/[n] (Enter to skip) ").lower().startswith("y"):
            git_version = get_program_version(Path(shutil.which("git")))
            # Use the autostash flag for Git >= 2.14
            # https://stackoverflow.com/a/30209750/894271
            autostash_flag = ["--autostash"] if git_version >= (2, 14) else []
            subprocess.check_call(["git", "pull", "--rebase"] + autostash_flag, cwd=project_dir)
            os.execv(sys.argv[0], sys.argv)


def ensure_fd_is_blocking(fd):
    flag = fcntl.fcntl(fd, fcntl.F_GETFL)
    if flag & os.O_NONBLOCK:
        # Try to unset the flag (this appears to happen on macOS sometimes):
        fcntl.fcntl(fd, fcntl.F_SETFL, flag & ~os.O_NONBLOCK)
    flag = fcntl.fcntl(fd, fcntl.F_GETFL)
    if flag & os.O_NONBLOCK:
        fatal_error("fd", fd, "is set to nonblocking and could not unset flag")


def real_main():
    # avoid weird errors with macos terminal:
    ensure_fd_is_blocking(sys.stdin.fileno())
    ensure_fd_is_blocking(sys.stdout.fileno())
    ensure_fd_is_blocking(sys.stderr.fileno())

    all_target_names = list(sorted(target_manager.target_names))
    run_everything_target = "__run_everything__"
    config_loader = JsonAndCommandLineConfigLoader()
    # Register all command line options
    cheri_config = DefaultCheriConfig(config_loader, all_target_names + [run_everything_target])
    SimpleProject._config_loader = config_loader
    target_manager.register_command_line_options()
    # load them from JSON/cmd line
    cheri_config.load()
    init_global_config(test_mode=False, pretend_mode=cheri_config.pretend,
                       verbose_mode=cheri_config.verbose, quiet_mode=cheri_config.quiet)

    if cheri_config.docker or JsonAndCommandLineConfigLoader.get_config_prefix() == "docker-":
        # check that the docker build won't override native binaries
        cheri_config.docker = True
        # get the actual descriptor
        import inspect
        output_option = inspect.getattr_static(cheri_config, "output_root")  # type: JsonAndCommandLineConfigOption
        source_option = inspect.getattr_static(cheri_config, "source_root")  # type: JsonAndCommandLineConfigOption
        build_option = inspect.getattr_static(cheri_config, "build_root")  # type: JsonAndCommandLineConfigOption
        # noinspection PyProtectedMember
        if cheri_config.build_root == build_option._get_default_value(cheri_config) and \
                cheri_config.source_root == source_option._get_default_value(cheri_config) and \
                cheri_config.output_root == output_option._get_default_value(cheri_config):
            fatal_error(
                "Running cheribuild in docker with the default source/output/build directories is not supported")

    if CheribuildAction.LIST_TARGETS in cheri_config.action:
        print("Available targets are:\n ", "\n  ".join(all_target_names))
        sys.exit()
    elif CheribuildAction.DUMP_CONFIGURATION in cheri_config.action:
        print(cheri_config.get_options_json())
        sys.exit()
    elif cheri_config.get_config_option:
        if cheri_config.get_config_option not in config_loader.options:
            fatal_error("Unknown config key", cheri_config.get_config_option)
        option = config_loader.options[cheri_config.get_config_option]
        # noinspection PyProtectedMember
        print(option.__get__(cheri_config,
                             option._owning_class if option._owning_class else cheri_config))  # pytype:
        # disable=attribute-error
        sys.exit()

    assert any(x in cheri_config.action for x in (CheribuildAction.TEST, CheribuildAction.PRINT_CHOSEN_TARGETS,
                                                  CheribuildAction.BUILD, CheribuildAction.BENCHMARK))

    # create the required directories
    for d in (cheri_config.source_root, cheri_config.output_root, cheri_config.build_root):
        if d.exists():
            continue
        if not cheri_config.pretend:
            if cheri_config.verbose:
                print_command("mkdir", "-p", str(d))
            os.makedirs(str(d), exist_ok=True)

    if cheri_config.docker:
        cheribuild_dir = str(Path(__file__).absolute().parent.parent)
        # we can't pass all args
        filtered_cheribuild_args = ["--source-root", "/source", "--build-root", "/build", "--output-root", "/output"]
        skip_next = False
        excluded_args = ("--source-root", "--build-root", "--output-root", "--docker-container")
        for arg in sys.argv[1:]:
            if skip_next:
                skip_next = False
                continue
            if arg in excluded_args:
                skip_next = True
                continue
            if any(arg.startswith(s + "=") for s in excluded_args):
                continue
            if arg == "--docker" or arg == "--docker-reuse-container":
                continue
            filtered_cheribuild_args.append(arg)
        try:
            docker_dir_mappings = [
                # map cheribuild and the sources read-only into the container
                "-v", cheribuild_dir + ":/cheribuild:ro",
                "-v", str(cheri_config.source_root.absolute()) + ":/source:ro",
                # build and output are read-write:
                "-v", str(cheri_config.build_root.absolute()) + ":/build",
                "-v", str(cheri_config.output_root.absolute()) + ":/output",
                ]
            cheribuild_args = ["/cheribuild/cheribuild.py", "--skip-update"] + filtered_cheribuild_args
            if cheri_config.docker_reuse_container:
                # Use docker restart + docker exec instead of docker run
                # FIXME: docker restart doesn't work for some reason
                stop_cmd = ["docker", "stop", cheri_config.docker_container]
                print_command(stop_cmd)
                subprocess.check_call(stop_cmd)
                start_cmd = ["docker", "start", cheri_config.docker_container]
                print_command(start_cmd)
                subprocess.check_call(start_cmd)
                docker_run_cmd = ["docker", "exec", cheri_config.docker_container] + cheribuild_args
            else:
                docker_run_cmd = ["docker", "run", "--rm"] + docker_dir_mappings + [
                    cheri_config.docker_container] + cheribuild_args
            print_command(docker_run_cmd)
            subprocess.check_call(docker_run_cmd)
        except subprocess.CalledProcessError as e:
            # if the image is missing print a helpful error message:
            if e.returncode == 125:
                status_update("It seems like the docker image", cheri_config.docker_container, "was not found.")
                status_update("In order to build the default docker image for cheribuild (cheribuild-test) run:")
                print(
                    coloured(AnsiColour.blue, "cd", cheribuild_dir + "/docker && docker build --tag cheribuild-test ."))
                sys.exit(coloured(AnsiColour.red, "Failed to start docker!"))
            raise
        sys.exit()

    if run_everything_target in cheri_config.targets:
        cheri_config.targets = all_target_names
    if not cheri_config.targets:
        # Make --libcheri-buildenv and --buildenv without any targets imply cheribsd
        if cheri_config.libcompat_buildenv or cheri_config.buildenv:
            cheri_config.targets.append("cheribsd")
        else:
            fatal_error("At least one target name is required (see --list-targets).")

    if not cheri_config.quiet:
        print("Sources will be stored in", cheri_config.source_root)
        print("Build artifacts will be stored in", cheri_config.output_root)
    # Don't do the update check when tab-completing (otherwise it freezes)
    if "_ARGCOMPLETE" not in os.environ and not cheri_config.skip_update:  # no-combine
        try:  # no-combine
            update_check()  # no-combine
        except Exception as e:  # no-combine
            print("Failed to check for updates:", e)  # no-combine
    if CheribuildAction.PRINT_CHOSEN_TARGETS in cheri_config.action:
        for target in target_manager.get_all_chosen_targets(cheri_config):
            print("Would run", target)
    if CheribuildAction.BUILD in cheri_config.action:
        target_manager.run(cheri_config)
    if CheribuildAction.TEST in cheri_config.action:
        for target in target_manager.get_all_chosen_targets(cheri_config):
            target.run_tests(cheri_config)
    if CheribuildAction.BENCHMARK in cheri_config.action:
        for target in target_manager.get_all_chosen_targets(cheri_config):
            target.run_benchmarks(cheri_config)


def main():
    error = False
    try:
        if os.getpgrp() != os.getpid():
            os.setpgrp()  # create new process group, become its leader
        real_main()
    except KeyboardInterrupt:
        error = True
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError as err:
        error = True
        cwd = (". Working directory was ", err.cwd) if hasattr(err, "cwd") else ()
        fatal_error("Command ", "`" + commandline_to_str(err.cmd) + "` failed with non-zero exit code ",
                    err.returncode, *cwd, fatal_when_pretending=True, sep="", exit_code=err.returncode)
    finally:
        if error:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            os.killpg(0, signal.SIGTERM)  # Tell all child processes to exit


if __name__ == "__main__":
    main()
