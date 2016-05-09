from ..project import AutotoolsProject
from ..utils import *


# Not really autotools but same sequence of commands (other than the script being call bootstrap instead of configure)
class BuildCMake(AutotoolsProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir, configureScript="bootstrap",
                         # gitUrl="https://cmake.org/cmake.git")
                         gitUrl="https://github.com/Kitware/CMake")  # a lot faster than the official repo
        self.gitBranch = "release"  # track the stable release branch
        # TODO: do we need to use gmake on FreeBSD?
