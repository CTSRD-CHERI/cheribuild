import os
import pprint
from pathlib import Path

from ...config.loader import ComputedDefaultValue
from ...config.chericonfig import CrossCompileTarget
from ..cheribsd import BuildCHERIBSD
from ..llvm import BuildLLVM
from ...project import *
from ...utils import *

__all__ = ["CheriConfig", "installToCheriBSDRootfs", "CrossCompileCMakeProject", "CrossCompileAutotoolsProject",
           "CrossCompileTarget"]


installToCheriBSDRootfs = ComputedDefaultValue(
    function=lambda config, project: Path(BuildCHERIBSD.rootfsDir(config) / "extra" / project.projectName.lower()),
    asString=lambda cls: "$CHERIBSD_ROOTFS/extra/" + cls.projectName.lower())

defaultTarget = ComputedDefaultValue(
    function=lambda config, project: config.crossCompileTarget.value,
    asString="'cheri' unless -xmips/-xhost is set")

def _default_build_dir(config: CheriConfig, project):
    if project.crossCompileTarget == CrossCompileTarget.CHERI:
        build_dir_suffix = config.cheriBitsStr + "-build"
    else:
        build_dir_suffix = project.crossCompileTarget.value + "-build"
    return config.buildRoot / (project.projectName.lower() + "-" + build_dir_suffix)

class CrossCompileProject(Project):
    doNotAddToTargets = True
    defaultInstallDir = installToCheriBSDRootfs
    appendCheriBitsToBuildDir = True
    dependencies = ["cheribsd-sdk"]
    defaultLinker = "lld"
    _forceLibCXX = True
    crossCompileTarget = None  # type: CrossCompileTarget
    defaultOptimizationLevel = ["-O0"]
    warningFlags = ["-Wall", "-Werror=cheri-capability-misuse", "-Werror=implicit-function-declaration",
                    "-Werror=format", "-Werror=undefined-internal", "-Werror=incompatible-pointer-types",
                    "-Werror=mips-cheri-prototypes"]
    defaultBuildDir = ComputedDefaultValue(
        function=_default_build_dir,
        asString=lambda cls: "$BUILD_ROOT/" + cls.projectName.lower()  + "-$CROSS_TARGET-build")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.compiler_dir = self.config.sdkBinDir
        # Use the compiler from the build directory for native builds to get stddef.h (which will be deleted)
        if self.crossCompileTarget == CrossCompileTarget.NATIVE:
            if (BuildLLVM.buildDir / "bin/clang").exists():
                self.compiler_dir = BuildLLVM.buildDir / "bin"

        self.targetTriple = None
        self.sdkBinDir = self.config.sdkDir / "bin"
        self.sdkSysroot = self.config.sdkDir / "sysroot"
        # compiler flags:
        if self.crossCompileTarget == CrossCompileTarget.NATIVE:
            self.COMMON_FLAGS = []
            self.targetTriple = self.get_host_triple()
            self.installDir = self.buildDir / "test-install-prefix"
        else:
            self.installPrefix = Path("/", self.installDir.relative_to(BuildCHERIBSD.rootfsDir(config)))
            self.destdir = BuildCHERIBSD.rootfsDir(config)
            self.COMMON_FLAGS = ["-integrated-as", "-pipe", "-msoft-float", "-G0"]
            if self.crossCompileTarget == CrossCompileTarget.CHERI:
                self.targetTriple = "cheri-unknown-freebsd"
                self.COMMON_FLAGS.append("-mabi=purecap")
                if self.config.cheriBits == 128:
                    self.COMMON_FLAGS.append("-mcpu=cheri128")
            else:
                assert self.crossCompileTarget == CrossCompileTarget.MIPS
                self.targetTriple = "mips64-unknown-freebsd"
                self.COMMON_FLAGS.append("-mabi=n64")
            if not self.noUseMxgot:
                self.COMMON_FLAGS.append("-mxgot")
        if self.debugInfo:
            self.COMMON_FLAGS.append("-g")
        self.CFLAGS = []
        self.CXXFLAGS = []
        if self._forceLibCXX:
            self.CXXFLAGS = ["-stdlib=libc++"]
        self.ASMFLAGS = []
        self.LDFLAGS = []

    @staticmethod
    def get_host_triple():
        # TODO: get --build from `clang --version | grep Target:`
        if IS_FREEBSD:
            buildhost = "x86_64-unknown-freebsd"
            # noinspection PyUnresolvedReferences
            release = os.uname().release
            buildhost += release[:release.index(".")]
        else:
            buildhost = "x86_64-unknown-linux-gnu"
        return buildhost

    @property
    def sizeof_void_ptr(self):
        if self.crossCompileTarget in (CrossCompileTarget.MIPS, CrossCompileTarget.NATIVE):
            return 8
        elif self.config.cheriBits == 128:
            return 16
        else:
            assert self.config.cheriBits == 256
            return 32

    @property
    def default_ldflags(self):
        if self.crossCompileTarget == CrossCompileTarget.NATIVE:
            return ["-fuse-ld=" + self.linker]
        elif self.crossCompileTarget == CrossCompileTarget.CHERI:
            emulation = "elf64btsmip_cheri_fbsd"
            abi = "purecap"
        elif self.crossCompileTarget == CrossCompileTarget.MIPS:
            emulation = "elf64btsmip_fbsd"
            abi = "n64"
        else:
            fatalError("Logic error!")
            return []
        result = ["-mabi=" + abi,
                  "-Wl,-m" + emulation,
                  "-fuse-ld=" + self.linker,
                  "--sysroot=" + str(self.sdkSysroot),
                  "-B" + str(self.sdkBinDir)]
        if self.config.withLibstatcounters:
            if self.linkDynamic:
                result.append("-lstatcounters")
            else:
                result += ["-Wl,--whole-archive", "-lstatcounters", "-Wl,--no-whole-archive"]
        if not self.linkDynamic:
            result.append("-static")
        return result

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.noUseMxgot = cls.addBoolOption("no-use-mxgot", help="Compile without -mxgot flag (Unless the program is"
                                                                " small this will probably break everything!)")
        cls.linker = cls.addConfigOption("linker", default=cls.defaultLinker,
                                         help="The linker to use (`lld` or `bfd`) (lld is  better but may"
                                              " not work for some projects!)")
        cls.linkDynamic = cls.addBoolOption("link-dynamic", help="Try to link dynamically (probably broken)")
        cls.debugInfo = cls.addBoolOption("debug-info", help="build with debug info", default=True)
        cls.optimizationFlags = cls.addConfigOption("optimization-flags", kind=list, metavar="OPTIONS",
                                                    default=cls.defaultOptimizationLevel)
        if cls.crossCompileTarget is None:
            cls.crossCompileTarget = cls.addConfigOption("target", help="The target to build for (`cheri` or `mips` or `native`)",
                                                 default=defaultTarget, choices=["cheri", "mips", "native"],
                                                 kind=CrossCompileTarget)


class CrossCompileCMakeProject(CMakeProject, CrossCompileProject):
    doNotAddToTargets = True  # only used as base class
    defaultCMakeBuildType = "Debug"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # This must come first:
        if self.crossCompileTarget == CrossCompileTarget.NATIVE:
            self._cmakeTemplate = includeLocalFile("files/NativeToolchain.cmake.in")
            self.toolchainFile = self.buildDir / "NativeToolchain.cmake"
        else:
            self._cmakeTemplate = includeLocalFile("files/CheriBSDToolchain.cmake.in")
            self.toolchainFile = self.buildDir / "CheriBSDToolchain.cmake"
        self.add_cmake_options(CMAKE_TOOLCHAIN_FILE=self.toolchainFile)
        # The toolchain files need at least CMake 3.6
        self.set_minimum_cmake_version(3, 6)

    def _prepareToolchainFile(self, **kwargs):
        configuredTemplate = self._cmakeTemplate
        for key, value in kwargs.items():
            strval = " ".join(value) if isinstance(value, list) else str(value)
            assert "@" + key + "@" in configuredTemplate, key
            configuredTemplate = configuredTemplate.replace("@" + key + "@", strval)
        assert "@" not in configuredTemplate, configuredTemplate
        self.writeFile(contents=configuredTemplate, file=self.toolchainFile, overwrite=True, noCommandPrint=True)

    def configure(self, **kwargs):
        self.COMMON_FLAGS.append("-B" + str(self.sdkBinDir))
        self._prepareToolchainFile(
            TOOLCHAIN_SDK_BINDIR=self.sdkBinDir,
            TOOLCHAIN_SYSROOT=self.sdkSysroot,
            TOOLCHAIN_COMPILER_BINDIR=self.compiler_dir,
            TOOLCHAIN_TARGET_TRIPLE=self.targetTriple,
            TOOLCHAIN_COMMON_FLAGS=self.COMMON_FLAGS,
            TOOLCHAIN_C_FLAGS=self.CFLAGS,
            TOOLCHAIN_LINKER_FLAGS=self.LDFLAGS + self.default_ldflags,
            TOOLCHAIN_CXX_FLAGS=self.CXXFLAGS,
            TOOLCHAIN_ASM_FLAGS=self.ASMFLAGS,
        )
        super().configure()


class CrossCompileAutotoolsProject(AutotoolsProject, CrossCompileProject):
    doNotAddToTargets = True  # only used as base class

    add_host_target_build_config_options = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        buildhost = self.get_host_triple()
        if self.crossCompileTarget != CrossCompileTarget.NATIVE and self.add_host_target_build_config_options:
            self.configureArgs.extend(["--host=" + self.targetTriple, "--target=" + self.targetTriple,
                                       "--build=" + buildhost])

    @property
    def default_compiler_flags(self):
        result = self.COMMON_FLAGS + self.warningFlags + self.optimizationFlags + ["-target", self.targetTriple,
                                                                                   "-B" + str(self.sdkBinDir)]
        if self.crossCompileTarget != CrossCompileTarget.NATIVE:
            result += ["--sysroot=" + str(self.sdkSysroot)]
        return result

    def configure(self, **kwargs):
        CPPFLAGS = self.default_compiler_flags
        for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
            assert key not in self.configureEnvironment
        compiler_prefix = self.targetTriple + "-"
        if self.crossCompileTarget == CrossCompileTarget.NATIVE:
            compiler_prefix = ""

        self.configureEnvironment["CC"] = str(self.compiler_dir / (compiler_prefix + "clang"))
        self.configureEnvironment["CXX"] = str(self.compiler_dir / (compiler_prefix + "-clang++"))
        self.configureEnvironment["CPPFLAGS"] = " ".join(CPPFLAGS)
        self.configureEnvironment["CFLAGS"] = " ".join(CPPFLAGS + self.CFLAGS)
        self.configureEnvironment["CXXFLAGS"] = " ".join(CPPFLAGS + self.CXXFLAGS)
        self.configureEnvironment["LDFLAGS"] = " ".join(self.LDFLAGS + self.default_ldflags)
        print(coloured(AnsiColour.yellow, "Cross configure environment:",
                       pprint.pformat(self.configureEnvironment, width=160)))
        super().configure(**kwargs)

    def process(self):
        # We run all these commands with $PATH containing $CHERI_SDK/bin to ensure the right tools are used
        with setEnv(PATH=str(self.config.sdkDir / "bin") + ":" + os.getenv("PATH")):
            super().process()
