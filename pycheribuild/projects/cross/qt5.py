#
# Copyright (c) 2017 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
from .crosscompileproject import *
from ...config.loader import ComputedDefaultValue
from ...utils import commandline_to_str, runCmd, IS_FREEBSD

# This class is used to build qtbase and all of qt5
class BuildQtWithConfigureScript(CrossCompileProject):
    crossInstallDir = CrossInstallDir.SDK
    doNotAddToTargets = True
    defaultOptimizationLevel = ["-O2"]
    add_host_target_build_config_options = False

    def __init__(self, config: CheriConfig, target_arch: CrossCompileTarget):
        super().__init__(config, target_arch)
        self.configureCommand = self.sourceDir / "configure"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.build_tests = cls.addBoolOption("build-tests", showHelp=True, help="build the Qt unit tests")
        cls.build_examples = cls.addBoolOption("build-examples", showHelp=True, help="build the Qt examples")
        cls.useMxgot = True  # appears to be needed for some tests

    def configure(self, **kwargs):
        if not self.needsConfigure() and not self.config.forceConfigure:
            return
        if self.compiling_for_host():
            self.configureArgs.extend(["-prefix", str(self.installDir)])
            self.configureArgs.append("QMAKE_CC=" + str(self.config.clangPath))
            self.configureArgs.append("QMAKE_CXX=" + str(self.config.clangPlusPlusPath))
        else:
            # make sure we use libc++ (only happens with mips64-unknown-freebsd10 and greater)
            compiler_flags = self.COMMON_FLAGS + ["-target", self.targetTriple + "12"]
            linker_flags = self.default_ldflags + ["-target", self.targetTriple + "12"]

            if self.compiling_for_cheri():
                # force static linking for now (MIPS dynamic seems broken)
                linker_flags += ["-static"]  # dynamically linked C++ doesn't work yet
                self.configureArgs.append("QMAKE_LIBDIR=" + str(self.config.sdkSysrootDir / "usr/libcheri"))
            elif self.compiling_for_mips():
                linker_flags += ["-static"]  # also link statically to compare with static CHERI
            # self.configureArgs.append("QMAKE_CXXFLAGS+=-stdlib=libc++")

            # The build system already passes these:
            linker_flags = filter(lambda s: not s.startswith("--sysroot"), linker_flags)
            compiler_flags = filter(lambda s: not s.startswith("--sysroot"), compiler_flags)

            self.configureArgs.extend([
                "-device", "freebsd-generic-clang",
                "-device-option", "CROSS_COMPILE={}/{}-".format(self.config.sdkBinDir, self.targetTriple),
                "-device-option", "COMPILER_FLAGS=" + commandline_to_str(compiler_flags),
                "-device-option", "LINKER_FLAGS=" + commandline_to_str(linker_flags),
                "-sysroot", self.config.sdkSysrootDir,
                "-static",
                "-prefix", "/usr/local/Qt-" + self.crossCompileTarget.value,
            ])

        self.configureArgs.extend([
            # To ensure the host and cross-compiled version is the same also disable opengl and dbus there
            "-no-opengl", "-no-dbus",
            # Missing configure check for evdev means it will fail to compile for CHERI
            "-no-evdev",
            # Needed for webkit:
            # "-icu",

            "-no-Werror",
        ])
        if self.build_tests:
            self.configureArgs.append("-developer-build")
        else:
            self.configureArgs.extend(["-nomake", "tests"])

        if not self.build_examples:
            self.configureArgs.extend(["-nomake", "examples"])
        # currently causes build failures:
        # Seems like I need to define PNG_READ_GAMMA_SUPPORTED
        self.configureArgs.append("-qt-libpng")

        if self.debugInfo:
            # Build a release build with debug info for now
            self.configureArgs.append("-release")
            self.configureArgs.append("-optimize-size")  # Use -Os, otherwise it will use -O3
            self.configureArgs.append("-force-debug-info")
            # self.configureArgs.append("-debug")
        else:
            self.configureArgs.append("-release")

        self.configureArgs.extend(["-opensource", "-confirm-license"])

        self.deleteFile(self.buildDir / "config.cache")
        self.deleteFile(self.buildDir / "config.opt")
        self.deleteFile(self.buildDir / "config.status")
        super().configure()

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()


class BuildQt5(BuildQtWithConfigureScript):
    repository = "https://github.com/arichardson/qt5"
    gitBranch = "5.10-cheri"
    skipGitSubmodules = True  # init-repository does it for us

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.allModules = cls.addBoolOption("all-modules", showHelp=True,
                                          help="Build all modules (even those that don't make sense for CHERI)")

    def configure(self, **kwargs):
        if not self.allModules:
            modules_to_skip = "qtgamepad qtlocation".split()
            for i in modules_to_skip:
                self.configureArgs.extend(["-skip", i])
            # TODO: skip modules that just increase compile time and are useless
        super().configure(**kwargs)

    def update(self):
        super().update()
        # qtlocation breaks for some reason if qt5 is forked on github
        # TODO: qtwebkit, but that won't cross-compile with QMAKE
        runCmd("perl", "init-repository", "--module-subset=essential", "-f", "--branch", cwd=self.sourceDir)


class BuildQtBase(BuildQtWithConfigureScript):
    repository = "https://github.com/arichardson/qtbase"
    gitBranch = "5.10-cheri"
    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.getSourceDir(config) / "qtbase",
        asString=lambda cls: "$SOURCE_ROOT/qt5" + cls.projectName.lower())


# Webkit needs ICU (and recommended for QtBase too:
class BuildICU4C(CrossCompileAutotoolsProject):
    repository = "https://github.com/arichardson/icu4c.git"
    crossInstallDir = CrossInstallDir.SDK
    make_kind = MakeCommandKind.GnuMake
    warningFlags = []  # FIXME: build with capability -Werror
    if IS_FREEBSD:
        forceDefaultCC = True  # for some reason crashes on FreeBSD 11 if using clang40/ clang39

    def __init__(self, config, target_arch: CrossCompileTarget):
        super().__init__(config, target_arch)
        self.configureCommand = self.sourceDir / "source/configure"
        self.configureArgs.extend(["--enable-static", "--disable-shared", "--disable-plugins", "--disable-dyload",
                                   "--disable-tests",
                                   "--disable-samples"])
        self.nativeBuildDir = self.buildDirForTarget(self.config, CrossCompileTarget.NATIVE)
        print(self.nativeBuildDir)
        # we can't create objects for a different endianess:
        self.COMMON_FLAGS.append("-DU_DISABLE_OBJ_CODE")
        if not self.compiling_for_host():
            self.configureArgs.append("--with-cross-build=" + str(self.nativeBuildDir))
            # can't build them yet
            # error: undefined symbol: uconvmsg_dat
            # self.configureArgs.append("--disable-tools")
            # but these seem to be needed
            # self.configureArgs.append("--disable-draft")
            # self.configureArgs.append("--disable-extras")  # can't add this to host build, it will fail otherwise
            # TODO: not quite sure what to do with the data
            # ICU generates a little endian library otherwise....
            # for now packaging as an archive seems to work (maybe)
            # self.configureArgs.append("--with-data-packaging=archive")
            self.configureArgs.append("--with-data-packaging=static")

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        if not self.compiling_for_host() and not self.nativeBuildDir.exists():
            self.dependencyError("Missing host build directory", self.nativeBuildDir, " (needed for cross-compiling)",
                                 installInstructions="Run `cheribuild.py " + self.target + " --xhost`")


# it also needs libxml2
class BuildLibXml2(CrossCompileAutotoolsProject):
    repository = "https://github.com/arichardson/libxml2"
    crossInstallDir = CrossInstallDir.SDK
    warningFlags = []  # FIXME: build with capability -Werror
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config, target_arch: CrossCompileTarget):
        super().__init__(config, target_arch)
        if (self.sourceDir / "configure").exists():
            self.configureCommand = self.sourceDir / "configure"
        else:
            self.configureCommand = self.sourceDir / "autogen.sh"
        self.configureArgs.extend([
            "--disable-shared", "--enable-static", "--without-python",
            "--without-modules",
        ])


class BuildQtWebkit(CrossCompileCMakeProject):
    repository = "https://github.com/arichardson/qtwebkit"
    gitBranch = "dev"
    dependencies = ["qtbase", "icu4c", "libxml2", "sqlite"]
    # webkit is massive if we include debug info
    defaultCMakeBuildType = "MinSizeRel"
    crossInstallDir = CrossInstallDir.SDK
    warningFlags = []  # FIXME: build with capability -Werror
    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.getSourceDir(config) / "qtwebkit",
        asString=lambda cls: "$SOURCE_ROOT/qt5" + cls.projectName.lower())

    def __init__(self, config: CheriConfig, target_arch: CrossCompileTarget):
        # There is a bug in the cmake ninja generator that makes it use a response file for linking
        # WebCore but not actually generating it
        super().__init__(config, target_arch,
                         # generator=BuildQtWebkit.Generator.Makefiles
                         generator=BuildQtWebkit.Generator.Ninja
                         )
        self.add_cmake_options(PORT="Qt", ENABLE_X11_TARGET=False,
                               ENABLE_OPENGL=False,
                               USE_LIBHYPHEN=False,  # we don't have libhyphen
                               ENABLE_TEST_SUPPORT=False,
                               ENABLE_VIDEO=False,  # probably depends on lots of stuff
                               ENABLE_XSLT=False,  # 1 less library to build
                               USE_GSTREAMER=False,  # needs all the glib+gtk crap
                               USE_LD_GOLD=False,  # Webkit wants to use gold by default...
                               USE_SYSTEM_MALLOC=True,  # we want bounds
                               ENABLE_API_TESTS=False,
                               )
        # TODO: when we use the full build of Qt enable these:
        self.add_cmake_options(ENABLE_GEOLOCATION=False,  # needs QtPositioning
                               ENABLE_PRINT_SUPPORT=False,  # needs QtPrintSupport
                               ENABLE_DEVICE_ORIENTATION=False,  # needs QtSensors
                               ENABLE_WEBKIT2=False,  # needs QtQuick
                               )
        if not self.compiling_for_host():
            # we need to find the installed Qt
            self.add_cmake_options(ENABLE_JIT=False,  # Not supported on MIPS
                                   QT_STATIC_BUILD=True,  # we always build qt static for now
                                   QT_BUNDLED_PNG=True,  # use libpng from Qt
                                   # QT_BUNDLED_JPEG=True,  # use libjpeg from Qt
                                   )
            self.add_cmake_options(Qt5_DIR=self.config.sdkSysrootDir / ("usr/local/Qt-" + self.crossCompileTarget.value) / "lib/cmake/Qt5")
            self.add_cmake_options(PNG_LIBRARIES="libqtlibpng.a")
            self.add_cmake_options(PNG_INCLUDE_DIRS=BuildQtBase.getSourceDir(config) / "src/3rdparty/libpng")

        self._addRequiredSystemTool("gperf")

