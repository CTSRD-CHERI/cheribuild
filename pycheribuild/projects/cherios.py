import re

from ..project import CMakeProject
from ..utils import *


class BuildCheriOS(CMakeProject):
    dependencies = ["freestanding-sdk"]
    if IS_LINUX:
        dependencies.append("makefs-linux")

    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.outputRoot / ("cherios" + config.cheriBitsStr), buildType="Debug",
                         gitUrl="https://github.com/CTSRD-CHERI/cherios.git", appendCheriBitsToBuildDir=True)
        self.configureArgs.append("-DCHERI_SDK_DIR=" + str(self.config.sdkDir))

    # TODO: move to CMakeProject
    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        # try to find cmake 3.4 or newer
        versionPattern = re.compile(b"cmake version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # cmake prints this output to stdout
        versionString = runCmd("cmake", "--version", captureOutput=True, printVerboseOnly=True).stdout
        match = versionPattern.search(versionString)
        versionComponents = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if versionComponents < (3, 4):
            versionStr = ".".join(map(str, versionComponents))
            self.dependencyError("CMake version", versionStr, "is too old (need at least 3.4)",
                                 installInstructions=self.cmakeInstallInstructions)

    def install(self):
        pass  # nothing to install yet
