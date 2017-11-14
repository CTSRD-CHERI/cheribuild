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
from ..project import *
from ..utils import *
from pathlib import Path

import pwd
import grp
import os


class BuildElftoolchain(Project):
    target = "elftoolchain"
    projectName = "elftoolchain"
    gitBranch = "master"
    repository = "https://github.com/emaste/elftoolchain.git"
    defaultInstallDir = Project._installToSDK
    defaultBuildDir = Project.defaultSourceDir  # we have to build in the source directory

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # TODO: move this to project
        if not IS_FREEBSD:
            self._addRequiredSystemTool("bmake")
            self.makeCommand = "bmake"
        else:
            self.makeCommand = "make"
        self.make_args.kind = self.make_args.Kind.BsdMake

        self.gitBranch = "master"
        # self.makeArgs = ["WITH_TESTS=no", "-DNO_ROOT"]
        # TODO: build static?
        if self.build_static:
            self.make_args.set(LDSTATIC="-static")
        self.make_args.set_with_options(TESTS=False, PE=False, DOCUMENTATION=False)
        # HACK: we don't want the binaries to depend on libelftc.so because the build system doesn't handle rpath
        # setting SHLIB_FULLVERSION to empty is a hack to prevent building of shared libraries
        # as we want the build tools to be statically linked but e.g. libarchive might not be available
        # as a static library (e.g. on openSUSE)
        self.make_args.set(SHLIB_MAJOR="", SHLIB_FULLVERSION="",  # don't build shared libraries
                           CC=str(self.config.clangPath))

        self.make_args.set(DESTDIR=self.installDir)
        if not self.config.verbose:
            self.make_args.add_flags("-s")
        self.programsToBuild = ["brandelf", "elfcopy", "elfdump", "strings", "nm", "readelf", "addr2line",
                                "size", "findtextrel"]
        # some make targets install more than one tool:
        # strip, objcopy and mcs are links to elfcopy and ranlib is a link to ar
        self.extraPrograms = ["strip", "objcopy", "mcs"]
        self.libTargets = ["common", "libelf", "libelftc", "libpe", "libdwarf"]
        if self.build_ar:
            self.programsToBuild.append("ar")
            self.extraPrograms.append("ranlib")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.build_ar = cls.addBoolOption("build-ar", default=True, help="build the ar/ranlib programs")
        cls.build_static = cls.addBoolOption("build-static", help="Try to link elftoolchain statically "
                                                                  "(needs patches on Linux)")
    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        if IS_MAC and not Path("/usr/local/opt/libarchive/lib").exists():
            self.dependencyError("libarchive is missing", installInstructions="Run `brew install libarchive`")

    def compile(self, **kwargs):
        # tools that we want to build:
        # build is not parallel-safe -> we can't make with all the all-foo targets and -jN
        # To speed it up run make for the individual library directories instead and then for all the binaries
        firstCall = True  # recreate logfile on first call, after that append
        for tgt in self.libTargets + self.programsToBuild:
            self.runMake("all", cwd=self.sourceDir / tgt, logfileName="build", appendToLogfile=not firstCall)
            firstCall = False

    def install(self, **kwargs):
        self.makedirs(self.installDir / "bin")
        # We don't actually want to install all the files, just copy the binaries that we want
        group = grp.getgrgid(os.getgid()).gr_name
        user = pwd.getpwuid(os.getuid()).pw_name
        self.make_args.set(
            # elftoolchain tries to install as root -> override *GRP and *OWN flags
            BINGRP=group, BINOWN=user,
            MANGRP=group, MANOWN=user,
            INFOGRP=group, INFOOWN=user,
            LIBGRP=group, LIBOWN=user,
            FILESGRP=group, FILESOWN=user,
        )

        self.make_args.set(
            BINDIR="/bin",
            LIBDIR="/lib",
            INCSDIR="/include",
            SHAREDIR="/share",
            )

        if IS_LINUX:
            # $INSTALL is not set to create leading directories on Ubuntu
            self.make_args.set(MANDIR="/share/man", INSTALL="install -D")

        mandirs = ("share/man/man1", "share/man/man3", "share/man/man5", "share/man1", "share/man3", "share/man5")
        # The build system assumes all install directories already exist;
        for i in ("bin", "lib", "include", "share") + mandirs:
            self.makedirs(self.installDir / i)
        firstCall = True  # recreate logfile on first call, after that append
        for tgt in self.programsToBuild:
            self.runMakeInstall(cwd=self.sourceDir / tgt, logfileName="install", appendToLogfile=not firstCall,
                                parallel=False)
            firstCall = False

        allInstalledTools = self.programsToBuild + self.extraPrograms
        for prog in allInstalledTools:
            if prog == "strip":
                self.deleteFile(self.installDir / "bin" / ("cheri-unknown-freebsd-" + prog))
                self.deleteFile(self.installDir / "bin" / ("mips64-unknown-freebsd-" + prog))
                self.deleteFile(self.installDir / "bin" / ("mips4-unknown-freebsd-" + prog))
            else:
                self.createBuildtoolTargetSymlinks(self.installDir / "bin" / prog)
        # if we didn't build ar/ranlib add symlinks to the versions in /usr/bin
        if not self.build_ar:
            self.createSymlink(Path("/usr/bin/ar"), self.installDir / "bin/ar", relative=False)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/ar")
            self.createSymlink(Path("/usr/bin/ranlib"), self.installDir / "bin/ranlib", relative=False)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/ranlib")

    def process(self):
        # work around bug in latest bmake that assumes metamode support
        with setEnv(META_NOECHO="echo"):
            super().process()
