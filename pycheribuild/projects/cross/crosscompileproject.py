import os
import pprint
from enum import Enum
from pathlib import Path


from ...config.loader import ComputedDefaultValue
from ...config.chericonfig import CrossCompileTarget
from ..cheribsd import BuildCHERIBSD
from ..llvm import BuildLLVM
from ...project import *
from ...utils import *

__all__ = ["CheriConfig", "CrossCompileCMakeProject", "CrossCompileAutotoolsProject", "CrossCompileTarget",
           "CrossCompileProject", "CrossInstallDir"]

class CrossInstallDir(Enum):
    NONE = 0
    CHERIBSD_ROOTFS = 1
    SDK = 2

defaultTarget = ComputedDefaultValue(
    function=lambda config, project: config.crossCompileTarget.value,
    asString="'cheri' unless -xmips/-xhost is set")

def _default_build_dir(config: CheriConfig, project):
    return project.buildDirForTarget(config, project.crossCompileTarget)

def _installDir(config: CheriConfig, project: "CrossCompileProject"):
    if project.crossCompileTarget == CrossCompileTarget.NATIVE:
        return config.sdkDir
    if project.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
        return Path(BuildCHERIBSD.rootfsDir(config) / "extra" / project.projectName.lower())
    elif project.crossInstallDir == CrossInstallDir.SDK:
        return config.sdkSysrootDir
    fatalError("Unknown install dir for", project.projectName)

def _installDirMessage(project: "CrossCompileProject"):
    if project.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
        return "$CHERIBSD_ROOTFS/extra/" + project.projectName.lower() + " or $CHERI_SDK for --xhost build"
    elif project.crossInstallDir == CrossInstallDir.SDK:
        return "$CHERI_SDK/sysroot for cross builds or $CHERI_SDK for --xhost build"
    return "UNKNOWN"


class CrossCompileProject(Project):
    doNotAddToTargets = True
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    defaultInstallDir = ComputedDefaultValue(function=_installDir, asString=_installDirMessage)
    appendCheriBitsToBuildDir = True
    dependencies = ["cheribsd-sdk"]
    defaultLinker = "lld"
    crossCompileTarget = None  # type: CrossCompileTarget
    defaultOptimizationLevel = ["-O2"]
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
            if self.crossInstallDir == CrossInstallDir.SDK:
                self.installPrefix = "/usr/local"
                self.destdir = config.sdkSysrootDir
            elif self.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
                self.installPrefix = Path("/", self.installDir.relative_to(BuildCHERIBSD.rootfsDir(config)))
                self.destdir = BuildCHERIBSD.rootfsDir(config)
            else:
                assert self.installPrefix and self.destdir, "Must be set!"
            self.COMMON_FLAGS = ["-integrated-as", "-pipe", "-msoft-float", "-G0"]
            # use *-*-freebsd12 to default to libc++
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
        self.ASMFLAGS = []
        self.LDFLAGS = []

    @property
    def targetTripleWithVersion(self):
        # we need to append the FreeBSD version to pick up the correct C++ standard library
        if self.compiling_for_host():
            return self.targetTriple
        else:
            # anything over 10 should use libc++ by default
            return self.targetTriple + "12"

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
            return []
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
                  "-Wl,-z,notext",  # needed so that LLD allows text relocations
                  "--sysroot=" + str(self.sdkSysroot),
                  "-B" + str(self.sdkBinDir)]
        if self.compiling_for_cheri() and self.newCapRelocs:
            # TODO: check that we are using LLD and not BFD
            result += ["-no-capsizefix", "-Wl,-process-cap-relocs", "-Wl,-verbose"]
        if self.config.withLibstatcounters:
            #if self.linkDynamic:
            #    result.append("-lstatcounters")
            #else:
            result += ["-Wl,--whole-archive", "-lstatcounters", "-Wl,--no-whole-archive"]
        return result

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.noUseMxgot = cls.addBoolOption("no-use-mxgot", help="Compile without -mxgot flag (Unless the program is"
                                                                " small this will probably break everything!)")
        cls.linker = cls.addConfigOption("linker", default=cls.defaultLinker,
                                         help="The linker to use (`lld` or `bfd`) (lld is  better but may"
                                              " not work for some projects!)")
        cls.debugInfo = cls.addBoolOption("debug-info", help="build with debug info", default=True)
        cls.optimizationFlags = cls.addConfigOption("optimization-flags", kind=list, metavar="OPTIONS",
                                                    default=cls.defaultOptimizationLevel)
        # TODO: check if LLD supports it and if yes default to true?
        cls.newCapRelocs = cls.addBoolOption("new-cap-relocs", help="Use the new __cap_relocs processing in LLD", default=False)
        if cls.crossCompileTarget is None:
            cls.crossCompileTarget = cls.addConfigOption("target", help="The target to build for (`cheri` or `mips` or `native`)",
                                                 default=defaultTarget, choices=["cheri", "mips", "native"],
                                                 kind=CrossCompileTarget)

    @classmethod
    def buildDirForTarget(cls, config: CheriConfig, target: CrossCompileTarget):
        if target == CrossCompileTarget.CHERI:
            build_dir_suffix = config.cheriBitsStr + "-build"
        else:
            build_dir_suffix = target.value + "-build"
        return config.buildRoot / (cls.projectName.lower() + "-" + build_dir_suffix)

    def compiling_for_mips(self):
        return self.crossCompileTarget == CrossCompileTarget.MIPS

    def compiling_for_cheri(self):
        return self.crossCompileTarget == CrossCompileTarget.CHERI

    def compiling_for_host(self):
        return self.crossCompileTarget == CrossCompileTarget.NATIVE

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
            TOOLCHAIN_TARGET_TRIPLE=self.targetTripleWithVersion,
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
        if not self.compiling_for_host() and self.add_host_target_build_config_options:
            self.configureArgs.extend(["--host=" + self.targetTriple, "--target=" + self.targetTriple,
                                       "--build=" + buildhost])

    @property
    def default_compiler_flags(self):
        result = self.COMMON_FLAGS + self.optimizationFlags + ["-target", self.targetTripleWithVersion]
        if self.crossCompileTarget != CrossCompileTarget.NATIVE:
            result += ["--sysroot=" + str(self.sdkSysroot), "-B" + str(self.sdkBinDir)] + self.warningFlags
        return result

    def configure(self, **kwargs):
        CPPFLAGS = self.default_compiler_flags
        for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
            assert key not in self.configureEnvironment
        # target triple contains a number suffix -> remove it when computing the compiler name
        compiler_prefix = self.targetTriple + "-"
        if self.crossCompileTarget == CrossCompileTarget.NATIVE:
            compiler_prefix = ""

        cc = self.config.clangPath if self.compiling_for_host() else self.compiler_dir / (compiler_prefix + "clang")
        self.configureEnvironment["CC"] = str(cc)
        cxx = self.config.clangPlusPlusPath if self.compiling_for_host() else self.compiler_dir / (compiler_prefix + "clang++")
        self.configureEnvironment["CXX"] = str(cxx)
        self.configureEnvironment["CPPFLAGS"] = " ".join(CPPFLAGS)
        self.configureEnvironment["CFLAGS"] = " ".join(CPPFLAGS + self.CFLAGS)
        self.configureEnvironment["CXXFLAGS"] = " ".join(CPPFLAGS + self.CXXFLAGS)
        self.configureEnvironment["LDFLAGS"] = " ".join(self.LDFLAGS + self.default_ldflags)
        # remove all empty items:
        env = {k: v for k, v in self.configureEnvironment.items() if v}
        self.configureEnvironment.clear()
        self.configureEnvironment.update(env)
        print(coloured(AnsiColour.yellow, "Cross configure environment:",
                       pprint.pformat(self.configureEnvironment, width=160)))
        super().configure(**kwargs)

    def process(self):
        # We run all these commands with $PATH containing $CHERI_SDK/bin to ensure the right tools are used
        with setEnv(PATH=str(self.config.sdkDir / "bin") + ":" + os.getenv("PATH")):
            super().process()
