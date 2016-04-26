from ..project import Project
from ..utils import *


class BuildAwk(Project):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, gitUrl="https://github.com/danfuzz/one-true-awk.git")
        self.buildDir = self.sourceDir

    def compile(self):
        self.runMake([self.makeCommand, "CC=cc", "CFLAGS=-O2 -Wall", "YACC=yacc -y -d"],
                     "a.out", cwd=self.sourceDir / "latest")

    def install(self):
        self.runMake([self.makeCommand], "names", cwd=self.sourceDir / "latest")
        self.copyFile(self.sourceDir / "latest/a.out", self.installDir / "bin/nawk")
        runCmd("ln", "-sfn", "nawk", "awk", cwd=self.installDir / "bin")

    def process(self):
        if not IS_LINUX:
            statusUpdate("Skipping awk as this is only needed on Linux hosts")
        else:
            super().process()
