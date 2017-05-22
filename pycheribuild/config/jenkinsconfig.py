#
# Copyright (c) 2017 Alex Richardson
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

import os
from pathlib import Path

from .loader import ConfigLoaderBase
from .chericonfig import CheriConfig
from ..utils import defaultNumberOfMakeJobs, fatalError


class JenkinsConfig(CheriConfig):
    def __init__(self, loader: ConfigLoaderBase, availableTargets: list):
        super().__init__(loader)

        self.cpu = loader.addCommandLineOnlyOption("cpu", default=os.getenv("CPU"),
                                                   help="The target to build the software for (defaults to $CPU).",
                                                   choices=["cheri128", "cheri256", "mips"])
        self.workspace = loader.addCommandLineOnlyOption("workspace", default=os.getenv("WORKSPACE"), type=Path,
                                                         help="The root directory for building (defaults to $WORKSPACE)")  # type: Path
        self.sdkArchiveName = loader.addCommandLineOnlyOption("sdk-archive", type=Path,
                                                              help="The name of the sdk archive")  # type: str
        self.keepInstallDir = loader.addCommandLineOnlyBoolOption("keep-install-dir",
                                                                  help="Don't delete the install dir prior to build")  # type: bool
        self.force_update = loader.addCommandLineOnlyBoolOption("force-update",
                                                                help="Do the updating (not recommended in jenkins!)")  # type: bool
        self.createCompilationDB = loader.addCommandLineOnlyBoolOption(
            "compilation-db", "-cdb", help="Create a compile_commands.json file in the build dir "
                                           "(requires Bear for non-CMake projects)")
        self.makeWithoutNice = True

        self.makeJobs = loader.addCommandLineOnlyOption("make-jobs", "j", type=int,
                                                        default=defaultNumberOfMakeJobs(),
                                                        help="Number of jobs to use for compiling")
        self.installationPrefix = loader.addCommandLineOnlyOption("install-prefix", type=Path, default="/usr/local",
                                                              help="The install prefix for cross compiled projects"
                                                                   " (the path where it will end up in the install"
                                                                   " image)")  # type: Path
        self.skipUpdate = True
        self.verbose = True
        self.quiet = False
        self.clean = True  # always clean build
        self.force = True  # no user input in jenkins
        self.noLogfile = True  # jenkins stores the output anyway
        self.skipConfigure = False
        self.forceConfigure = True
        # self.listTargets = False
        # self.dumpConfig = False
        # self.getConfigOption = None
        self.includeDependencies = False
        loader.finalizeOptions(availableTargets)

    @property
    def sdkDirectoryName(self):
        return "cherisdk"

    @property
    def sdkArchivePath(self):
        if self.sdkArchiveName is None:
            sdk_cpu = os.getenv("SDK_CPU")
            if not sdk_cpu:
                fatalError("SDK_CPU variable not set, cannot infer the name of the SDK archive")
            self.sdkArchiveName = "{}-{}-jemalloc-sdk.tar.xz".format(sdk_cpu, os.getenv("ISA", "vanilla"))
        assert isinstance(self.sdkArchiveName, str)
        return self.workspace / self.sdkArchiveName

    def load(self):
        super().load()

        if not self.workspace.is_dir():
            fatalError("WORKSPACE is not set to a valid directory:", self.workspace)
        self.sourceRoot = self.workspace
        self.buildRoot = self.workspace
        self.outputRoot = self.workspace / "tarball"
        self.otherToolsDir = self.workspace / "bootstrap"
        self.dollarPathWithOtherTools = str(self.otherToolsDir / "bin") + ":" + os.getenv("PATH")
        self.sdkDir = self.workspace / self.sdkDirectoryName
        self.sdkSysrootDir = self.sdkDir / "sysroot"
        self.sdkBinDir = self.sdkDir / "bin"

        self.crossCompileForMips = False
        if self.cpu == "cheri128":
            self.cheriBits = 128
        elif self.cpu == "cheri256":
            self.cheriBits = 256
        elif self.cpu == "mips":
            self.crossCompileForMips = True
            self.cheriBits = 0
        elif self.cpu in ("x86", "x86_64", "amd64"):
            self.cheriBits = 256  # just to make stuff work as expected
        else:
            fatalError("CPU is not set to a valid value:", self.cpu)

        if self.force_update:
            self.skipUpdate = False

        self._initializeDerivedPaths()

        assert self._ensureRequiredPropertiesSet()
        if os.getenv("DEBUG") is not None:
            import pprint
            for k, v in self.__dict__.items():
                if hasattr(v, "__get__"):
                    setattr(self, k, v.__get__(self, self.__class__))

            pprint.pprint(vars(self))
