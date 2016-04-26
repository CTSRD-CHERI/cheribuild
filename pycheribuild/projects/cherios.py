import re
import shlex
import shutil

from ..project import Project
from ..utils import *


class BuildCheriOS(Project):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.outputRoot / ("cherios" + config.cheriBitsStr),
                         gitUrl="https://github.com/CTSRD-CHERI/cherios.git", appendCheriBitsToBuildDir=True)
        self.makeCommand = "ninja"
        # try to find cmake 3.4 or newer
        # make sure we have at least version 3.7
        versionPattern = re.compile(b"cmake version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # cmake prints this output to stdout
        versionString = runCmd("cmake", "--version", captureOutput=True, printVerboseOnly=True).stdout
        match = versionPattern.search(versionString)
        versionComponents = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if versionComponents < (3, 5):
            fatalError("CMake version is too old (need at least 3.7): got", str(versionComponents),
                       "You can run `cheribuild.py cmake` to install an up to date version")
        self.configureCommand = "cmake"
        self.configureArgs = [
            self.sourceDir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Debug",
            "-DCMAKE_INSTALL_PREFIX=" + str(self.installDir),
            "-DCHERI_SDK_DIR=" + str(self.config.sdkDir),
            "-DCMAKE_RANLIB=/usr/bin/true",
        ]

    def install(self):
        pass  # nothing to install yet
