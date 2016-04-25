from ..project import Project
from ..utils import *


class BuildCMake(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("cmake", config, installDir=config.sdkDir,
                         # gitUrl="https://cmake.org/cmake.git")
                         gitUrl="https://github.com/Kitware/CMake")  # a lot faster than the official repo
        self.gitBranch = "release"  # track the stable release branch
        self.buildDir = self.sourceDir
        self.configureCommand = self.sourceDir / "bootstrap"
        self.configureArgs = ["--prefix=" + str(self.installDir)]

    def process(self):
        # TODO: check whether installed CMake version is new enough and if it is don't build it
        super().process()
