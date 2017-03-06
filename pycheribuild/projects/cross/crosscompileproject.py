from ...project import CMakeProject, AutotoolsProject, Project
from ...configloader import ConfigLoader
from ...chericonfig import CheriConfig
from ...utils import IS_FREEBSD
from ...colour import *
from ..cheribsd import BuildCHERIBSD
from pathlib import Path
import pprint

__all__ = ["CheriConfig", "installToCheriBSDRootfs", "CrossCompileCMakeProject", "CrossCompileAutotoolsProject"]

installToCheriBSDRootfs = ConfigLoader.ComputedDefaultValue(
    function=lambda config, project: Path(BuildCHERIBSD.rootfsDir(config) / "extra" / project.projectName.lower()),
    asString=lambda cls: "$CHERIBSD_ROOTFS/extra/" + cls.projectName.lower())


class CrossCompileProject(Project):
    doNotAddToTargets = True
    defaultInstallDir = installToCheriBSDRootfs
    appendCheriBitsToBuildDir = True
    dependencies = ["cheribsd-sdk"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.installPrefix = "/extra/" + self.projectName.lower()
        self.destdir = BuildCHERIBSD.rootfsDir(config)

    @classmethod
    def setupConfigOptions(cls: Project, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.noUseMxgot = cls.addBoolOption("no-use-mxgot", help="Compile without -mxgot flag (Unless the program is"
                                                                " small this will probably break everything!)")
        cls.useLld = cls.addBoolOption("use-lld", default=True, help="Use lld for linking (probably better!)")
        cls.linkDynamic = cls.addBoolOption("link-dynamic", help="Try to link dynamically (probably broken)")


class CrossCompileCMakeProject(CMakeProject, CrossCompileProject):
    doNotAddToTargets = True  # only used as base class
    defaultCMakeBuildType = "Debug"
    dependencies = ["cheri-buildsystem-wrappers"]  # TODO: generate the toolchain file dynamically?

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.toolchainName = "CheriBSDToolchainCheriABI"
        self.toolchainName += "Dynamic" if self.linkDynamic else "Static"
        self.toolchainName += "WithLLD" if self.useLld else ""
        self.toolchainName += ".cmake"
        self.toolchain_file = config.sdkDir / "share/cmake/cheri-toolchains" / self.toolchainName
        # This must come first:
        self.add_cmake_option("CMAKE_TOOLCHAIN_FILE", self.toolchain_file)

    def configure(self):
        if not self.toolchain_file.exists():
            self.dependencyError("Could not find CheriABI crosscompile cmake toolchain",
                                 installInstructions="Run `cheribuild cheri-buildsystem-wrappers`")
        super().configure()


class CrossCompileAutotoolsProject(AutotoolsProject, CrossCompileProject):
    doNotAddToTargets = True  # only used as base class
    _customInstallPrefix = True
    defaultOptimizationLevel = "-O0"
    warningFlags = ["-Wall", "-Werror=cheri-capability-misuse", "-Werror=implicit-function-declaration",
                    "-Werror=format", "-Werror=undefined-internal", "-Werror=incompatible-pointer-types"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.optimizationFlags = cls.addConfigOption("optimization-flags", default=cls.defaultOptimizationLevel)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.compileFlags = [
            "-pipe", "--sysroot=" + str(config.sdkSysrootDir),
            "-B" + str(config.sdkDir / "bin"),
            "-target", "cheri-unknown-freebsd",
            "-mabi=sandbox", "-msoft-float",
            "-integrated-as", "-G0", "-g"
        ]
        self.cPlusPlusFlags = []
        self.linkerFlags = ["-Wl,-melf64btsmip_cheri_fbsd"]
        if self.useLld:
            self.linkerFlags.append("-fuse-ld=lld")
        if not self.linkDynamic:
            self.linkerFlags.append("-static")
        # TODO: get --build from `clang --version | grep Target:`
        self.configureArgs.extend([
            "--host=cheri-unknown-freebsd",
            "--target=cheri-unknown-freebsd",
            "--build=x86_64-unknown-freebsd" if IS_FREEBSD else "--build=x86_64-unknown-linux-gnu",
        ])

    def configure(self):
        cflags = self.compileFlags + self.warningFlags + self.optimizationFlags.split()
        if not self.noUseMxgot:
            cflags.append("-mxgot")
        for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
            assert key not in self.configureEnvironment
        self.configureEnvironment["CC"] = str(self.config.sdkDir / "bin/cheri-unknown-freebsd-clang")
        self.configureEnvironment["CXX"] = str(self.config.sdkDir / "bin/cheri-unknown-freebsd-clang++")
        self.configureEnvironment["CFLAGS"] = " ".join(cflags)
        self.configureEnvironment["CPPFLAGS"] = " ".join(cflags)
        self.configureEnvironment["CXXFLAGS"] = " ".join(cflags + self.cPlusPlusFlags)
        self.configureEnvironment["LDFLAGS"] = " ".join(self.linkerFlags)
        print(coloured(AnsiColour.yellow, "Cross configure environment:", pprint.pformat(self.configureEnvironment)))
        super().configure()
