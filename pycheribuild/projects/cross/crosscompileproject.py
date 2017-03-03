from ...project import CMakeProject, AutotoolsProject, Project
from ...configloader import ConfigLoader
from ...chericonfig import CheriConfig
from ..cheribsd import BuildCHERIBSD
from pathlib import Path

__all__ = ["CheriConfig", "installToCheriBSDRootfs", "CrossCompileCMakeProject"]

installToCheriBSDRootfs = ConfigLoader.ComputedDefaultValue(
    function=lambda config, project: Path(BuildCHERIBSD.rootfsDir(config) / "extra" / project.projectName.lower()),
    asString=lambda cls: "$CHERIBSD_ROOTFS/extra/" + cls.projectName.lower())


def _setupCrossCompileConfigOptions(cls: Project):
    cls.useLld = cls.addBoolOption("use-lld", default=True, help="Whether to use lld for linking (probably better!)")


class CrossCompileCMakeProject(CMakeProject):
    doNotAddToTargets = True  # only used as base class
    defaultInstallDir = installToCheriBSDRootfs
    appendCheriBitsToBuildDir = True
    defaultCMakeBuildType = "Debug"
    dependencies = ["cheribsd-sdk", "cheri-buildsystem-wrappers"]  # TODO: generate the toolchain file dynamically?

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        _setupCrossCompileConfigOptions(cls)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        toolchainName = "CheriBSDToolchainCheriABIDynamic.cmake"
        if self.useLld:
            toolchainName = "CheriBSDToolchainCheriABIDynamicWithLLD.cmake"
        self.toolchain_file = config.sdkDir / "share/cmake/cheri-toolchains" / toolchainName
        # This must come first:
        self.add_cmake_option("CMAKE_TOOLCHAIN_FILE", self.toolchain_file)

    def configure(self):
        if not self.toolchain_file.exists():
            self.dependencyError("Could not find CheriABI crooscompile cmake toolchain",
                                 installInstructions="Run `cheribuild cheri-buildsystem-wrappers`")
        super().configure()
