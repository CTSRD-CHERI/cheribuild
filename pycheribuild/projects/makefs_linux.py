from ..project import Project
from ..utils import *

from pathlib import Path


class BuildMakefsOnLinux(Project):
    target = "makefs-linux"

    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir, gitUrl="https://github.com/Engil/makefs.git")
        self._addRequiredSystemTool("bmake")
        self.buildDir = self.sourceDir

    def checkSystemDependencies(self):
        if not IS_LINUX:
            return  # not need on FreeBSD
        super().checkSystemDependencies()
        if not Path("/usr/include/bsd/bsd.h").is_file():
            self.dependencyError("libbsd must be installed to compile makefs on linux")

    def compile(self):
        self.runMake(self.commonMakeArgs)

    def install(self):
        self._makedirs(self.installDir / "bin")
        self.copyFile(self.sourceDir / "builddir/usr.sbin/makefs/makefs", self.installDir / "bin/makefs")

    def process(self):
        if not IS_LINUX:
            statusUpdate("Skipping makefs as this is only needed on Linux hosts")
        else:
            super().process()
