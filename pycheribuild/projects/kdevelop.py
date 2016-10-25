from ..project import CMakeProject, Project
from ..utils import *
from pathlib import Path
import tempfile

import os


def kdevInstallDir(config: CheriConfig):
    return config.sdkDir


class BuildLibKompareDiff2(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=kdevInstallDir(config), buildType="Debug",
                         gitUrl="git://anongit.kde.org/libkomparediff2.git", appendCheriBitsToBuildDir=True)


class BuildKDevplatform(CMakeProject):
    dependencies = ["libkomparediff2"]

    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=kdevInstallDir(config), buildType="Debug",
                         gitUrl="https://github.com/RichardsonAlex/kdevplatform.git", appendCheriBitsToBuildDir=True)
        self.gitBranch = "cheri"


class BuildKDevelop(CMakeProject):
    dependencies = ["kdevplatform", "llvm"]

    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=kdevInstallDir(config), buildType="Debug",
                         gitUrl="https://github.com/RichardsonAlex/kdevelop.git", appendCheriBitsToBuildDir=True)
        # Tell kdevelop to use the CHERI clang
        self.configureArgs.append("-DLLVM_ROOT=" + str(self.config.sdkDir))
        # install the wrapper script that sets the right environment variables
        self.configureArgs.append("-DINSTALL_KDEVELOP_LAUNCH_WRAPPER=ON")
        self.gitBranch = "cheri"


class StartKDevelop(Project):
    target = "run-kdevelop"
    dependencies = ["kdevelop"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._addRequiredSystemTool("cmake")
        self._addRequiredSystemTool("qtpaths")

    def process(self):
        kdevelopBinary = self.config.sdkDir / "bin/start-kdevelop.py"
        if not kdevelopBinary.exists():
            self.dependencyError("KDevelop is missing:", kdevelopBinary,
                                 installInstructions="Run `cheribuild.py kdevelop` or `cheribuild.py " +
                                                     self.target + " -d`.")
        runCmd(kdevelopBinary, "--ps")
