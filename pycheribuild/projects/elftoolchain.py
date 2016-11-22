from ..project import Project
from ..utils import *

import pwd
import grp
import os


class BuildElfToolchain(Project):
    def __init__(self, config: CheriConfig, gitUrl="https://github.com/emaste/elftoolchain.git", **kwargs):
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
        self.programsToBuild = ["brandelf", "ar"]
        if not self.config.verbose:
            self.commonMakeArgs.append("-s")

    def compile(self):
        libTargets = ["common", "libelf", "libelftc", "libpe", "libdwarf"]
        # tools that we want to build:
        # build is not parallel-safe -> we can't make with all the all-foo targets and -jN
        # To speed it up run make for the individual library directories instead and then for all the binaries
        for tgt in libTargets:
            self.runMake(self.commonMakeArgs + [self.config.makeJFlag], makeTarget="all-" + tgt,
                         logfileName="build." + tgt)
        progTargets = list(map(lambda p: "all-" + p, self.programsToBuild))
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag] + progTargets, logfileName="build.programs")

    def install(self):
        if IS_FREEBSD:
            statusUpdate("Not installing elftoolchain binaries as they conflict witht he ones from CheriBSD")
            return
        # self.runMake([self.makeCommand, self.config.makeJFlag, "DESTDIR=" + str(self.installDir)] + self.makeArgs,
        #              "install", cwd=self.sourceDir)
        # make install requires root, just build binaries statically and copy them
        self.copyFile(self.sourceDir / "brandelf/brandelf", self.installDir / "bin/brandelf", force=True)


class BuildBrandelf(BuildElfToolchain):
    def __init__(self, config: CheriConfig):
        super().__init__(config, gitUrl="https://github.com/RichardsonAlex/elftoolchain.git")
