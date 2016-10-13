import re

from ..project import CMakeProject
from ..utils import *


class BuildCheriOS(CMakeProject):
    # If we are building on FreeBSD we get binutils from CheriBSD, on Linux we build binutils and elftoolchain
    # We could also depend on "sdk" on Linux but that requires configuration of the remote build server, so
    # just depend on the actually required targets
    # TODO: add a target sdk-freestanding that doesn't include the CheriBSD libs+includes
    dependencies = {"sdk"} if IS_FREEBSD else {"elftoolchain", "binutils", "llvm"}

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
