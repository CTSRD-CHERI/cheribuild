from ...project import CMakeProject
from ...configloader import ConfigLoader
from ...chericonfig import CheriConfig
from ..cheribsd import BuildCHERIBSD
from pathlib import Path

__all__ = ["CheriConfig", "installToCheriBSDRootfs", "CrossCompileCMakeProject"]

installToCheriBSDRootfs = ConfigLoader.ComputedDefaultValue(
    function=lambda config, project: Path(BuildCHERIBSD.rootfsDir(config) / "extra" / project.projectName.lower()),
    asString=lambda cls: "$CHERIBSD_ROOTFS/extra/" + cls.projectName.lower())


class CrossCompileCMakeProject(CMakeProject):
    doNotAddToTargets = True  # only used as base class

    defaultInstallDir = installToCheriBSDRootfs
    appendCheriBitsToBuildDir = True
    defaultCMakeBuildType = "Debug"
    dependencies = ["cheri-buildsystem-wrappers"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.toolchain_file = config.sdkDir / "share/cmake/cheri-toolchains/CheriBSDToolchainCheriABIDynamic.cmake"
        # This must come first:
        self.add_cmake_option("CMAKE_TOOLCHAIN_FILE", self.toolchain_file)

    def configure(self):
        if not self.toolchain_file.exists():
            self.dependencyError("Could not find CheriABI crooscompile cmake toolchain",
                                 installInstructions="Run `cheribuild cheri-buildsystem-wrappers`")
        super().configure()
