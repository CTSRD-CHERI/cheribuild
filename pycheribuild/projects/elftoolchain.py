from ..project import Project
from ..utils import *

import pwd
import grp
import os


class BuildCheriBinutils(Project):
    target = "cheri-binutils"

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
                                "size", "findtextrel", "as"]

    def compile(self):
        libTargets = ["common", "libelf", "libelftc", "libpe", "libdwarf"]
        # tools that we want to build:
        # build is not parallel-safe -> we can't make with all the all-foo targets and -jN
        # To speed it up run make for the individual library directories instead and then for all the binaries
        firstCall = True  # recreate logfile on first call, after that append
        for tgt in libTargets + self.programsToBuild:
            self.runMake(self.commonMakeArgs + [self.config.makeJFlag], makeTarget="all", cwd=self.sourceDir / tgt,
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
            "BINDIR=/bin", "MANDIR=/share/man", "LIBDIR=/lib", "INCSDIR=/include"
        ]
        if IS_LINUX:
            # $INSTALL is not set to create leading directories on Ubuntu
            ownerFlags.append("INSTALL=install -D")

        # some directories are not being created correctly:
        for i in ("share/man/man1", "share/man/man3", "share/man/man5"):
            self._makedirs(self.installDir / i)
        firstCall = True  # recreate logfile on first call, after that append
        for tgt in self.programsToBuild:
            self.runMake(self.commonMakeArgs + ownerFlags + ["DESTDIR=" + str(self.installDir)], makeTarget="install",
                         cwd=self.sourceDir / tgt, logfileName="install", appendToLogfile=not firstCall)
            firstCall = False

        # some make targets install more than one tool:
        # strip, objcopy and mcs are links to elfcopy and ranlib is a link to ar
        allInstalledTools = self.programsToBuild + ["strip", "ranlib", "objcopy", "mcs"]
        for prog in allInstalledTools:
            self.createBuildtoolTargetSymlinks(self.installDir / "bin" / prog)
        # we also create symlinks for objdump pointing to elfdump (for some reason this is not installed by elftc)
        # TODO: should we also create $SDK_DIR/bin/objdump pointing to elfdump? or only the prefixed ones
        self.createBuildtoolTargetSymlinks(self.installDir / "bin" / "elfdump", toolName="objdump")


class BuildElfToolchain(BuildCheriBinutils):
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


# TODO: remove this target and make it an alias for cheri-binutils
class BuildBrandelf(BuildCheriBinutils):
    def __init__(self, config: CheriConfig):
        super().__init__(config, gitUrl="https://github.com/RichardsonAlex/elftoolchain.git")
        self.programsToBuild = ["brandelf"]
