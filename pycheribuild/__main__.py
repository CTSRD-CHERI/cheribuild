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
import json
import os
import shlex
import subprocess
import sys

from .utils import *
from .targets import targetManager
from .configloader import ConfigLoader
from .projects import *  # make sure all projects are loaded so that targetManager gets populated
from .projects.cross import *  # make sure all projects are loaded so that targetManager gets populated


def updateCheck():
    from pathlib import Path
    # check if new commits are available
    projectDir = str(Path(__file__).parent)
    subprocess.call(["git", "fetch"], cwd=projectDir)
    output = subprocess.check_output(["git", "status", "-uno"], cwd=projectDir)
    behindIndex = output.find(b"Your branch is behind ")
    if behindIndex > 0:
        msgEnd = output.find(b"\n  (use \"git pull\" to update your local branch)")
        if msgEnd > 0:
            output = output[behindIndex:msgEnd]
        statusUpdate("Current CheriBuild checkout can be updated: ", output.decode("utf-8"))
        if input("Would you like to update before continuing? y/[n] (Enter to skip) ").lower().startswith("y"):
            subprocess.check_call(["git", "pull", "--rebase"], cwd=projectDir)
            os.execv(sys.argv[0], sys.argv)


def real_main():
    allTargetNames = list(sorted(targetManager.targetNames))
    targetManager.registerCommandLineOptions()
    runEverythingTarget = "__run_everything__"
    cheriConfig = CheriConfig(allTargetNames + [runEverythingTarget])
    setCheriConfig(cheriConfig)
    # create the required directories
    for d in (cheriConfig.sourceRoot, cheriConfig.outputRoot, cheriConfig.buildRoot):
        if d.exists():
            continue
        if not cheriConfig.pretend:
            if cheriConfig.verbose:
                printCommand("mkdir", "-p", str(d))
            os.makedirs(str(d), exist_ok=True)

    if cheriConfig.listTargets:
        print("Available targets are:\n ", "\n  ".join(allTargetNames))
    elif cheriConfig.dumpConfig:
        cheriConfig.dumpOptionsJSON()
    elif cheriConfig.getConfigOption:
        if cheriConfig.getConfigOption not in ConfigLoader.options:
            fatalError("Unknown config key", cheriConfig.getConfigOption)
        option = ConfigLoader.options[cheriConfig.getConfigOption]
        # noinspection PyProtectedMember
        print(option.__get__(cheriConfig, option._owningClass if option._owningClass else cheriConfig))
    else:
        if runEverythingTarget in cheriConfig.targets:
            cheriConfig.targets = allTargetNames
        if not cheriConfig.targets:
            fatalError("At least one target name is required (see --list-targets).")
        if not cheriConfig.quiet:
            print("Sources will be stored in", cheriConfig.sourceRoot)
            print("Build artifacts will be stored in", cheriConfig.outputRoot)
        # Don't do the update check when tab-completing (otherwise it freezes)
        if "_ARGCOMPLETE" not in os.environ:
            updateCheck()
        targetManager.run(cheriConfig)


def main():
    try:
        real_main()
    except KeyboardInterrupt:
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError as err:
        fatalError("Command ", "`" + " ".join(map(shlex.quote, err.cmd)) + "` failed with non-zero exit code",
                   err.returncode)


if __name__ == "__main__":
    main()

