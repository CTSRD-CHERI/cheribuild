from ..project import CMakeProject
from ..utils import *


class BuildCheriTrace(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/CTSRD-CHERI/CheriVis.git")
        self._addRequiredSystemTool("clang")
        self._addRequiredSystemTool("clang++")
        self.llvmConfigPath = self.config.sdkDir / "bin/llvm-config"
        self.configureArgs.extend([
            "-DLLVM_CONFIG=" + str(self.llvmConfigPath),
            "-DCMAKE_C_COMPILER=clang",
            "-DCMAKE_CXX_COMPILER=clang++",
        ])

    def configure(self):
        if not self.llvmConfigPath.is_file():
            self.dependencyError("Could not find llvm-config from CHERI LLVM.",
                                 installInstructions="Build target 'llvm' first.")
        super().configure()
