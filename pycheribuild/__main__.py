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
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from .configloader import ConfigLoader here because that will only update the local copy!
# https://stackoverflow.com/questions/3536620/how-to-change-a-module-variable-from-another-module
from .config.loader import JsonAndCommandLineConfigLoader, JsonAndCommandLineConfigOption
from .config.defaultconfig import DefaultCheriConfig, CheribuildAction
from .utils import *
from .utils import have_working_internet_connection
from .targets import targetManager
from .projects.project import SimpleProject
# noinspection PyUnresolvedReferences
from .projects import *  # make sure all projects are loaded so that targetManager gets populated
# noinspection PyUnresolvedReferences
from .projects.cross import *  # make sure all projects are loaded so that targetManager gets populated


def updateCheck():
    from pathlib import Path
    if not shutil.which("git"):
        return
    # Avoid update check if we don't have an internet connection
    if not have_working_internet_connection():
        return
    # check if new commits are available
    projectDir = str(Path(__file__).parent)
    subprocess.call(["git", "fetch"], cwd=projectDir, timeout=5)
    output = subprocess.check_output(["git", "status", "-uno"], cwd=projectDir)
    behindIndex = output.find(b"Your branch is behind ")
    if behindIndex > 0:
        msgEnd = output.find(b"\n  (use \"git pull\" to update your local branch)")
        if msgEnd > 0:
            output = output[behindIndex:msgEnd]
        statusUpdate("Current CheriBuild checkout can be updated: ", output.decode("utf-8"))
        if input("Would you like to update before continuing? y/[n] (Enter to skip) ").lower().startswith("y"):
            git_version = get_program_version(Path(shutil.which("git")))
            # Use the autostash flag for Git >= 2.14
            # https://stackoverflow.com/a/30209750/894271
            autostash_flag = ["--autostash"] if git_version >= (2, 14) else []
            subprocess.check_call(["git", "pull", "--rebase"] + autostash_flag, cwd=projectDir)
            os.execv(sys.argv[0], sys.argv)


def ensure_fd_is_blocking(fd):
    flag = fcntl.fcntl(fd, fcntl.F_GETFL)
    if flag & os.O_NONBLOCK:
        # Try to unset the flag (this appears to happen on macOS sometimes):
        fcntl.fcntl(fd, fcntl.F_SETFL, flag & ~os.O_NONBLOCK)
    flag = fcntl.fcntl(fd, fcntl.F_GETFL)
    if flag & os.O_NONBLOCK:
        fatalError("fd", fd, "is set to nonblocking and could not unset flag")


def real_main():
    # avoid weird errors with macos terminal:
    ensure_fd_is_blocking(sys.stdin.fileno())
    ensure_fd_is_blocking(sys.stdout.fileno())
    ensure_fd_is_blocking(sys.stderr.fileno())

    allTargetNames = list(sorted(targetManager.targetNames))
    runEverythingTarget = "__run_everything__"
    configLoader = JsonAndCommandLineConfigLoader()
    # Register all command line options
    cheriConfig = DefaultCheriConfig(configLoader, allTargetNames + [runEverythingTarget])
    SimpleProject._configLoader = configLoader
    targetManager.registerCommandLineOptions()
    # load them from JSON/cmd line
    cheriConfig.load()
    setCheriConfig(cheriConfig)

    if cheriConfig.docker or JsonAndCommandLineConfigLoader.get_config_prefix() == "docker-":
        # check that the docker build won't override native binaries
        cheriConfig.docker = True
        # get the actual descriptor
        import inspect
        outputOption = inspect.getattr_static(cheriConfig, "outputRoot")  # type: JsonAndCommandLineConfigOption
        sourceOption = inspect.getattr_static(cheriConfig, "sourceRoot")  # type: JsonAndCommandLineConfigOption
        buildOption = inspect.getattr_static(cheriConfig, "buildRoot")  # type: JsonAndCommandLineConfigOption
        # noinspection PyProtectedMember
        if cheriConfig.buildRoot == buildOption._getDefaultValue(cheriConfig) and \
                cheriConfig.sourceRoot == sourceOption._getDefaultValue(cheriConfig) and \
                cheriConfig.outputRoot == outputOption._getDefaultValue(cheriConfig):
            fatalError("Running cheribuild in docker with the default source/output/build directories is not supported")

    if CheribuildAction.LIST_TARGETS in cheriConfig.action:
        print("Available targets are:\n ", "\n  ".join(allTargetNames))
        sys.exit()
    elif CheribuildAction.DUMP_CONFIGURATION in cheriConfig.action:
        print(cheriConfig.getOptionsJSON())
        sys.exit()
    elif cheriConfig.getConfigOption:
        if cheriConfig.getConfigOption not in configLoader.options:
            fatalError("Unknown config key", cheriConfig.getConfigOption)
        option = configLoader.options[cheriConfig.getConfigOption]
        # noinspection PyProtectedMember
        print(option.__get__(cheriConfig, option._owningClass if option._owningClass else cheriConfig))
        sys.exit()

    assert any(x in cheriConfig.action for x in (CheribuildAction.TEST, CheribuildAction.PRINT_CHOSEN_TARGETS, CheribuildAction.BUILD))

    # create the required directories
    for d in (cheriConfig.sourceRoot, cheriConfig.outputRoot, cheriConfig.buildRoot):
        if d.exists():
            continue
        if not cheriConfig.pretend:
            if cheriConfig.verbose:
                printCommand("mkdir", "-p", str(d))
            os.makedirs(str(d), exist_ok=True)

    if cheriConfig.docker:
        cheribuild_dir = str(Path(__file__).absolute().parent.parent)
        # we can't pass all args
        filtered_cheribuild_args = ["--source-root", "/source", "--build-root", "/build", "--output-root", "/output"]
        skip_next = False
        blacklisted = ("--source-root", "--build-root", "--output-root", "--docker-container")
        for arg in sys.argv[1:]:
            if skip_next:
                skip_next = False
                continue
            if arg in blacklisted:
                skip_next = True
                continue
            if any(arg.startswith(s + "=") for s in blacklisted):
                continue
            if arg == "--docker" or arg == "--docker-reuse-container":
                continue
            filtered_cheribuild_args.append(arg)
        try:
            docker_dir_mappings = [
                # map cheribuild and the sources read-only into the container
                "-v", cheribuild_dir + ":/cheribuild:ro",
                "-v", str(cheriConfig.sourceRoot.absolute()) + ":/source:ro",
                # build and output are read-write:
                "-v", str(cheriConfig.buildRoot.absolute()) + ":/build",
                "-v", str(cheriConfig.outputRoot.absolute()) + ":/output",
            ]
            cheribuild_args = ["/cheribuild/cheribuild.py", "--skip-update"] + filtered_cheribuild_args
            if cheriConfig.docker_reuse_container:
                # Use docker restart + docker exec instead of docker run
                # FIXME: docker restart doesn't work for some reason
                stop_cmd = ["docker", "stop", cheriConfig.docker_container]
                printCommand(stop_cmd)
                subprocess.check_call(stop_cmd)
                start_cmd = ["docker", "start", cheriConfig.docker_container]
                printCommand(start_cmd)
                subprocess.check_call(start_cmd)
                docker_run_cmd = ["docker", "exec", cheriConfig.docker_container] + cheribuild_args
            else:
                docker_run_cmd = ["docker", "run", "--rm"] + docker_dir_mappings + [cheriConfig.docker_container] + cheribuild_args
            printCommand(docker_run_cmd)
            subprocess.check_call(docker_run_cmd)
        except subprocess.CalledProcessError as e:
            # if the image is missing print a helpful error message:
            if e.returncode == 125:
                statusUpdate("It seems like the docker image", cheriConfig.docker_container, "was not found.")
                statusUpdate("In order to build the default docker image for cheribuild (cheribuild-test) run:")
                print(coloured(AnsiColour.blue, "cd", cheribuild_dir + "/docker && docker build --tag cheribuild-test ."))
                sys.exit(coloured(AnsiColour.red, "Failed to start docker!"))
            raise
        sys.exit()

    if runEverythingTarget in cheriConfig.targets:
        cheriConfig.targets = allTargetNames
    if not cheriConfig.targets:
        # Make --libcheri-buildenv and --buildenv without any targets imply cheribsd
        if cheriConfig.libcheri_buildenv or cheriConfig.buildenv:
            cheriConfig.targets.append("cheribsd")
        else:
            fatalError("At least one target name is required (see --list-targets).")

    if not cheriConfig.quiet:
        print("Sources will be stored in", cheriConfig.sourceRoot)
        print("Build artifacts will be stored in", cheriConfig.outputRoot)
    # Don't do the update check when tab-completing (otherwise it freezes)
    if "_ARGCOMPLETE" not in os.environ and not cheriConfig.skipUpdate:  # no-combine
        try:                                          # no-combine
            updateCheck()                             # no-combine
        except Exception as e:                        # no-combine
            print("Failed to check for updates:", e)  # no-combine
    if CheribuildAction.PRINT_CHOSEN_TARGETS in cheriConfig.action:
        for target in targetManager.get_all_chosen_targets(cheriConfig):
            print("Would run", target)
    if CheribuildAction.BUILD in cheriConfig.action:
        targetManager.run(cheriConfig)
    if CheribuildAction.TEST in cheriConfig.action:
        for target in targetManager.get_all_chosen_targets(cheriConfig):
            target.run_tests(cheriConfig)


def main():
    try:
        real_main()
    except KeyboardInterrupt:
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError as err:
        cwd = (". Working directory was ", err.cwd) if hasattr(err, "cwd") else ()
        fatalError("Command ", "`" + commandline_to_str(err.cmd) + "` failed with non-zero exit code ",
                   err.returncode, *cwd, fatalWhenPretending=True, sep="")


if __name__ == "__main__":
    main()

