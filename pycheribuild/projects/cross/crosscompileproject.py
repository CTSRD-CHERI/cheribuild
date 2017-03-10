from ...project import CMakeProject, AutotoolsProject, Project
from ...configloader import ConfigLoader
from ...chericonfig import CheriConfig
from ...utils import *
from ...colour import *
from ..cheribsd import BuildCHERIBSD
from pathlib import Path
import os
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
    defaultLinker = "lld"
    targetArch = None  # build for mips64-unknown-freebsd instead of cheri-unknown-freebsd

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.installPrefix = Path("/", self.installDir.relative_to(BuildCHERIBSD.rootfsDir(config)))
        self.destdir = BuildCHERIBSD.rootfsDir(config)
        self.targetTriple = self.targetArch + "-unknown-freebsd"

    @classmethod
    def setupConfigOptions(cls: Project, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.noUseMxgot = cls.addBoolOption("no-use-mxgot", help="Compile without -mxgot flag (Unless the program is"
                                                                " small this will probably break everything!)")
        cls.linker = cls.addConfigOption("linker", default=cls.defaultLinker,
                                         help="The linker to use (`lld` or `bfd`) (lld is  better but may"
                                              " not work for some projects!)")
        cls.linkDynamic = cls.addBoolOption("link-dynamic", help="Try to link dynamically (probably broken)")
        if cls.targetArch is None:
            cls.targetArch = cls.addConfigOption("target", help="The target to build for (`cheri` or `mips64`)",
                                                 default="cheri", choices=["cheri", "mips64"])


class CrossCompileCMakeProject(CMakeProject, CrossCompileProject):
    doNotAddToTargets = True  # only used as base class
    defaultCMakeBuildType = "Debug"
    dependencies = ["cheri-buildsystem-wrappers"]  # TODO: generate the toolchain file dynamically?

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if self.targetArch == "mips64":
            self.toolchainName = "CheriBSDToolchainMIPS"
        elif self.targetArch == "cheri":
            self.toolchainName = "CheriBSDToolchainCheriABI"
        else:
            raise RuntimeError("Invalid target arch: " + self.targetArch)
        self.toolchainName += "Dynamic" if self.linkDynamic else "Static"
        self.toolchainName += "WithLLD" if self.linker == "lld" else ""
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
            "-target", self.targetTriple,
            "-msoft-float",
            "-integrated-as", "-G0", "-g"
        ]
        if self.targetArch == "cheri":
            self.compileFlags.append("-mabi=sandbox")

        self.cOnlyFlags = []
        self.cPlusPlusFlags = []
        self.linkerFlags = ["-Wl,-melf64btsmip_cheri_fbsd"]
        self.linkerFlags.append("-fuse-ld=" + self.linker)
        if not self.linkDynamic:
            self.linkerFlags.append("-static")

        # TODO: get --build from `clang --version | grep Target:`
        if IS_FREEBSD:
            buildhost = "x86_64-unknown-freebsd"
            release = os.uname().release
            buildhost += release[:release.index(".")]
        else:
            buildhost = "x86_64-unknown-linux-gnu"

        self.configureArgs.extend(["--host=" + self.targetTriple, "--target=" + self.targetTriple,
                                   "--build=" + buildhost])

    def configure(self):
        cflags = self.compileFlags + self.warningFlags + self.optimizationFlags.split()
        if not self.noUseMxgot:
            cflags.append("-mxgot")
        for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
            assert key not in self.configureEnvironment
        self.configureEnvironment["CC"] = str(self.config.sdkDir / ("bin/" + self.targetTriple + "-clang"))
        self.configureEnvironment["CXX"] = str(self.config.sdkDir / ("bin/" + self.targetTriple + "-clang++"))
        self.configureEnvironment["CPPFLAGS"] = " ".join(cflags)
        self.configureEnvironment["CFLAGS"] = " ".join(cflags + self.cOnlyFlags)
        self.configureEnvironment["CXXFLAGS"] = " ".join(cflags + self.cPlusPlusFlags)
        self.configureEnvironment["LDFLAGS"] = " ".join(self.linkerFlags)
        print(coloured(AnsiColour.yellow, "Cross configure environment:", pprint.pformat(self.configureEnvironment)))
        super().configure()

    def process(self):
        # We run all these commands with $PATH containing $CHERI_SDK/bin to ensure the right tools are used
        with setEnv(PATH=str(self.config.sdkDir / "bin") + ":" + os.getenv("PATH")):
            super().process()
