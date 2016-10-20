from ..project import Project
from ..utils import *


class BuildQtCreator(Project):
    dependencies = ["llvm"]

    def __init__(self, config: CheriConfig):
        super().__init__(config, gitUrl="https://code.qt.io/qt-creator/qt-creator.git", installDir=config.sdkDir,
                         appendCheriBitsToBuildDir=True)
        self._addRequiredSystemTool("qmake")
        self.configureCommand = "qmake"
        self.configureArgs.extend(["-r", self.sourceDir / "qtcreator.pro"])
        self.configureEnvironment["LLVM_INSTALL_DIR"] = self.config.sdkDir
        self.makeCommand = "make"

    def install(self):
        self.runMake(["install", "INSTALL_ROOT=" + str(self.installDir)])
