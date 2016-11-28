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
from ..project import Project
from ..utils import *

import pwd
import grp
import os


class BuildElftoolchainBinutils(Project):
    target = "elftoolchain-binutils"

    def __init__(self, config: CheriConfig, gitUrl="https://github.com/RichardsonAlex/elftoolchain.git", **kwargs):
        super().__init__(config, installDir=config.sdkDir, gitUrl=gitUrl, **kwargs)
        self.buildDir = self.sourceDir
        if IS_LINUX:
            self._addRequiredSystemTool("bmake")
            self.makeCommand = "bmake"
        else:
            self.makeCommand = "make"

        self.gitBranch = "master"
        # self.makeArgs = ["WITH_TESTS=no", "-DNO_ROOT"]
        # TODO: build static?
        self.commonMakeArgs.append("WITH_TESTS=no")
        self.commonMakeArgs.append("WITH_DOCUMENTATION=no")
        if not self.config.verbose:
            self.commonMakeArgs.append("-s")
        self.programsToBuild = ["brandelf", "ar", "elfcopy", "elfdump", "strings", "nm", "readelf", "addr2line",
                                "size", "findtextrel"]
        # some make targets install more than one tool:
        # strip, objcopy and mcs are links to elfcopy and ranlib is a link to ar
        self.extraPrograms = ["strip", "ranlib", "objcopy", "mcs"]

    def compile(self):
        libTargets = ["common", "libelf", "libelftc", "libpe", "libdwarf"]
        # tools that we want to build:
        # build is not parallel-safe -> we can't make with all the all-foo targets and -jN
        # To speed it up run make for the individual library directories instead and then for all the binaries
        firstCall = True  # recreate logfile on first call, after that append
        for tgt in libTargets + self.programsToBuild:
            # setting SHLIB_FULLVERSION to empty is a hack to prevent building of shared libraries
            # as we want the build tools to be statically linked but e.g. libarchive might not be available
            # as a static library (e.g. on openSUSE)
            makeArgs = self.commonMakeArgs + ["SHLIB_FULLVERSION=", self.config.makeJFlag]
            self.runMake(makeArgs, makeTarget="all", cwd=self.sourceDir / tgt,
                         logfileName="build", appendToLogfile=not firstCall)
            firstCall = False

    def install(self):
        # We don't actually want to install all the files, just copy the binaries that we want
        group = grp.getgrgid(os.getegid()).gr_name
        user = pwd.getpwuid(os.geteuid()).pw_name
        ownerFlags = [
            # elftoolchain tries to install as root -> override *GRP and *OWN flags
            "BINGRP=" + group, "BINOWN=" + user,
            "MANGRP=" + group, "MANOWN=" + user,
            "INFOGRP=" + group, "INFOOWN=" + user,
            "LIBGRP=" + group, "LIBOWN=" + user,
            "FILESGRP=" + group, "FILESOWN=" + user,
            # override the install paths:
            "BINDIR=/bin", "LIBDIR=/lib", "INCSDIR=/include", "SHAREDIR=/share"
        ]
        if IS_LINUX:
            # $INSTALL is not set to create leading directories on Ubuntu
            ownerFlags.append("INSTALL=install -D")
            # on Linux MANDIR is not relative to SHAREDIR so we need to set it manually
            ownerFlags.append("MANDIR=/share/man")

        # some directories are not being created correctly:
        for i in ("share/man/man1", "share/man/man3", "share/man/man5"):
            self._makedirs(self.installDir / i)
        firstCall = True  # recreate logfile on first call, after that append
        for tgt in self.programsToBuild:
            self.runMake(self.commonMakeArgs + ownerFlags + ["DESTDIR=" + str(self.installDir)], makeTarget="install",
                         cwd=self.sourceDir / tgt, logfileName="install", appendToLogfile=not firstCall)
            firstCall = False

        allInstalledTools = self.programsToBuild + self.extraPrograms
        for prog in allInstalledTools:
            self.createBuildtoolTargetSymlinks(self.installDir / "bin" / prog)


class BuildElfToolchain(BuildElftoolchainBinutils):
    def __init__(self, config: CheriConfig):
        super().__init__(config, gitUrl="https://github.com/emaste/elftoolchain.git")
        self.programsToBuild = ["brandelf"]

    def process(self):
        warningMessage("Building target 'elftoolchain' is deprecated, you should build 'cheri-binutils' instead.")
        if not self.queryYesNo("Are you sure you want to build this target", forceResult=False):
            statusUpdate("Skipping deprecated target 'elftoolchain'")
            return
        super().process()

    def install(self):
        if IS_FREEBSD:
            statusUpdate("Not installing elftoolchain binaries as they conflict witht he ones from CheriBSD")
            return
        # self.runMake([self.makeCommand, self.config.makeJFlag, "DESTDIR=" + str(self.installDir)] + self.makeArgs,
        #              "install", cwd=self.sourceDir)
        # make install requires root, just build binaries statically and copy them
        self.installFile(self.sourceDir / "brandelf/brandelf", self.installDir / "bin/brandelf", force=True)


# TODO: remove this target and make it an alias for elftoolchain-binutils
class BuildBrandelf(BuildElftoolchainBinutils):
    def __init__(self, config: CheriConfig):
        super().__init__(config, gitUrl="https://github.com/emaste/elftoolchain.git")
        self.programsToBuild = ["brandelf"]
        self.extraPrograms = []
