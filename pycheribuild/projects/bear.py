from ..project import CMakeProject
from ..utils import *


class BuildBear(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/rizsotto/Bear.git")
