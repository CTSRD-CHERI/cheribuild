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
from ...utils import commandline_to_str, runCmd, IS_FREEBSD, IS_MAC, fatalError, IS_LINUX, getCompilerInfo
from pathlib import Path

# This class is used to build qtbase and all of qt5
class BuildQtWithConfigureScript(CrossCompileProject):
    crossInstallDir = CrossInstallDir.SDK
    doNotAddToTargets = True
    defaultOptimizationLevel = ["-O2"]
    add_host_target_build_config_options = False
    # Should not be needed, but it seems like some of the tests are broken otherwise
    make_kind = MakeCommandKind.GnuMake
    needs_mxcaptable_static = True  # Currently over the limit, maybe we need -ffunction-sections/-fdata-sections
    hide_options_from_help = True  # hide this for now

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configureCommand = self.sourceDir / "configure"
        if not self.compiling_for_host():
            self._linkage = Linkage.STATIC

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.build_tests = cls.addBoolOption("build-tests", showHelp=True, help="build the Qt unit tests")
        cls.build_examples = cls.addBoolOption("build-examples", showHelp=True, help="build the Qt examples")
        cls.minimal = cls.addBoolOption("minimal", showHelp=True, help="Don't build QtWidgets or QtGui, etc")
        cls.optimized_debug_build = cls.addBoolOption("optimized-debug-build",
                                                      help="Don't build with -Os instead of -O0 for debug info builds")
        cls.useMxgot = True  # appears to be needed for some tests

    def configure(self, **kwargs):
        if self.force_static_linkage:
            self.configureArgs.append("-static")

        if self.compiling_for_host():
            self.configureArgs.extend(["-prefix", str(self.installDir)])
            self.configureArgs.append("QMAKE_CC=" + str(self.config.clangPath))
            self.configureArgs.append("QMAKE_CXX=" + str(self.config.clangPlusPlusPath))
            if IS_LINUX and getCompilerInfo(self.config.clangPath).compiler == "clang":
                # otherwise the build assumes GCC
                self.configureArgs.append("-platform")
                self.configureArgs.append("linux-clang")
            # FreeBSD header files may use the register storage class but c++17 disallows this
            if IS_FREEBSD:
                self.configureArgs.extend(["-c++std", "c++14"])
        else:
            # make sure we use libc++ (only happens with mips64-unknown-freebsd10 and greater)
            compiler_flags = self.default_compiler_flags
            linker_flags = self.default_ldflags + ["-target", self.targetTriple + "12"]
            assert self.force_static_linkage, "Currently only static linking is supported!"

            if self.compiling_for_cheri():
                self.configureArgs.append("QMAKE_LIBDIR=" + str(self.crossSysrootPath / "usr/libcheri"))
            elif self.compiling_for_mips():
                # self.configureArgs.append("QMAKE_CXXFLAGS+=-stdlib=libc++")
                pass

            # The build system already passes these:
            linker_flags = filter(lambda s: not s.startswith("--sysroot"), linker_flags)
            compiler_flags = filter(lambda s: not s.startswith("--sysroot"), compiler_flags)
            if self.debugInfo:
                compiler_flags = list(compiler_flags) + ["-O0"]
            self.configureArgs.extend([
                "-device", "freebsd-generic-clang",
                "-device-option", "CROSS_COMPILE={}/{}-".format(self.config.sdkBinDir, self.targetTriple),
                "-device-option", "COMPILER_FLAGS=" + commandline_to_str(compiler_flags),
                "-device-option", "LINKER_FLAGS=" + commandline_to_str(linker_flags),
                "-sysroot", self.crossSysrootPath,
                "-prefix", "/usr/local/Qt-" + self._crossCompileTarget.value
            ])

        self.configureArgs.extend([
            # To ensure the host and cross-compiled version is the same also disable opengl and dbus there
            "-no-opengl", "-no-dbus",
            # Missing configure check for evdev means it will fail to compile for CHERI
            "-no-evdev",
            # Needed for webkit:
            # "-icu",
            "-no-Werror",
            "-no-use-gold-linker",
            "-no-iconv"
        ])
        if self.build_tests:
            self.configureArgs.append("-developer-build")
            if IS_MAC:
                # Otherwise we get "ERROR: debug-only framework builds are not supported. Configure with -no-framework
                # if you want a pure debug build."
                self.configureArgs.append("-no-framework")

        else:
            self.configureArgs.extend(["-nomake", "tests"])

        if not self.build_examples:
            # Seems to have changed
            self.configureArgs.extend(["-nomake", "examples", "-no-compile-examples"])
        # currently causes build failures:
        # Seems like I need to define PNG_READ_GAMMA_SUPPORTED
        self.configureArgs.append("-qt-libpng")

        if self.debugInfo:
            # TODO: once we update to qt 5.12 add this:
            # self.configureArgs.append("-gdb-index")
            # Build a release build with debug info for now
            if self.optimized_debug_build:
                self.configureArgs.append("-release")
                self.configureArgs.append("-optimize-size")  # Use -Os, otherwise it will use -O3
                self.configureArgs.append("-force-debug-info")
                self.configureArgs.append("-force-asserts")
            else:
                self.configureArgs.append("-debug")
                # optimize-debug needs GCC
                # self.configureArgs.append("-optimize-debug")
        else:
            self.configureArgs.append("-release")

        self.configureArgs.append("-no-pch")  # slows down build but gives useful crash testcases

        #  -reduce-exports ...... Reduce amount of exported symbols [auto]
        self.configureArgs.append("-reduce-exports")
        # -reduce-relocations .. Reduce amount of relocations [auto] (Unix only)
        # TODO: this needs PIE:
        # self.configureArgs.append("-reduce-relocations")

        if self.minimal:
            self.configureArgs.extend([
                "-no-widgets",
                "-no-glib",
                "-no-gtk",
                "-no-opengl",
                "-no-cups",
                "-no-syslog",
                "-no-gui",
                "-no-iconv"
            ])

        self.configureArgs.extend(["-opensource", "-confirm-license"])

        self.deleteFile(self.buildDir / "config.cache")
        self.deleteFile(self.buildDir / "config.opt")
        self.deleteFile(self.buildDir / "config.status")
        super().configure()

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()


class BuildQt5(BuildQtWithConfigureScript):
    repository = "https://github.com/CTSRD-CHERI/qt5"
    gitBranch = "5.10.0"
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
    repository = "https://github.com/CTSRD-CHERI/qtbase"
    gitBranch = "5.10.0"
    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.getSourceDir(project, config) / "qtbase",
        asString=lambda cls: "$SOURCE_ROOT/qt5" + cls.projectName.lower())

    def __init__(self, config):
        super().__init__(config)
        #self.COMMON_FLAGS.extend(self.extra_c_compat_flags)
        self.cross_warning_flags += ["-Wno-shadow", "-Wno-error=cheri-bitwise-operations"]  # FIXME: remove after update to 5.12


    def compile(self, **kwargs):
        if self.minimal:
            self.runMake("sub-src")
            if self.build_tests:
                # only build the tests for corelib:
                if not (self.buildDir / "tests/auto/corelib").exists():
                    # generate the makefiles
                    self.runMake("sub-tests-make_first")
                self.runMake("sub-corelib", cwd=self.buildDir / "tests/auto")
        else:
            self.runMake() # QtBase ignores -nomake if you run "gmake all"

    def run_tests(self):
        if self.compiling_for_host():
            runCmd("make", "check", cwd=self.buildDir)
        else:
            self.run_cheribsd_test_script("run_qtbase_tests.py", use_benchmark_kernel_by_default=True)


def icu_dependencies(cls: "typing.Type[CrossCompileProject]", config: CheriConfig):
    deps = crosscompile_dependencies(cls, config)
    target = cls.get_crosscompile_target(config)
    # ICU4C needs a native buid to cross-compile:
    if target != CrossCompileTarget.NATIVE:
        deps.append("icu4c-native")
    return deps


# Webkit needs ICU (and recommended for QtBase too:
class BuildICU4C(CrossCompileAutotoolsProject):
    repository = "https://github.com/CTSRD-CHERI/icu4c.git"
    crossInstallDir = CrossInstallDir.SDK
    make_kind = MakeCommandKind.GnuMake
    dependencies = icu_dependencies
    if IS_FREEBSD:
        forceDefaultCC = True  # for some reason crashes on FreeBSD 11 if using clang40/ clang39

    def __init__(self, config):
        super().__init__(config)
        self.configureCommand = self.sourceDir / "source/configure"
        self.configureArgs.extend(["--disable-plugins", "--disable-dyload",
                                   "--disable-tests",
                                   "--disable-samples"])
        self.nativeBuildDir = self.buildDirForTarget(self.config, CrossCompileTarget.NATIVE)
        # we can't create objects for a different endianess:
        self.COMMON_FLAGS.append("-DU_DISABLE_OBJ_CODE")
        self.cross_warning_flags += ["-Wno-error"]  # FIXME: build with capability -Werror

        if not self.compiling_for_host():
            self.configureArgs.append("--with-cross-build=" + str(self.nativeBuildDir))
            # can't build them yet
            # error: undefined symbol: uconvmsg_dat
            # self.configureArgs.append("--disable-tools")
            # but these seem to be needed
            # self.configureArgs.append("--disable-draft")
            # self.configureArgs.append("--disable-extras")  # can't add this to host build, it will fail otherwise
            # We have modified the ICU data Makefile so that ICU builds a big endian data archive
            self.configureArgs.append("--with-data-packaging=archive")

    def process(self):
        if not self.compiling_for_host() and not self.nativeBuildDir.exists():
            self.fatal("Missing host build directory", self.nativeBuildDir, " (needed for cross-compiling)",
                       fixitHint="Run `cheribuild.py " + self.target + " --xhost`")
        super().process()


# it also needs libxml2
class BuildLibXml2(CrossCompileAutotoolsProject):
    repository = "https://github.com/arichardson/libxml2"
    crossInstallDir = CrossInstallDir.SDK
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config):
        super().__init__(config)
        if (self.sourceDir / "configure").exists():
            self.configureCommand = self.sourceDir / "configure"
        else:
            self.configureCommand = self.sourceDir / "autogen.sh"
        self.configureArgs.extend([
            "--disable-shared", "--enable-static", "--without-python",
            "--without-modules", "--without-lzma",
        ])
        self.cross_warning_flags += ["-Wno-error", "-Wno-error=cheri-capability-misuse"]  # FIXME: build with capability -Werror


class BuildQtWebkit(CrossCompileCMakeProject):
    repository = "https://github.com/CTSRD-CHERI/qtwebkit"
    gitBranch = "dev"
    dependencies = ["qtbase", "icu4c", "libxml2", "sqlite"]
    # webkit is massive if we include debug info
    defaultCMakeBuildType = "MinSizeRel"
    crossInstallDir = CrossInstallDir.SDK
    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.getSourceDir(project, config) / "qtwebkit",
        asString=lambda cls: "$SOURCE_ROOT/qt5" + cls.projectName.lower())
    needs_mxcaptable_static = True  # Currently way over the limit
    needs_mxcaptable_dynamic = True  # Currently way over the limit

    def __init__(self, config: CheriConfig):
        # There is a bug in the cmake ninja generator that makes it use a response file for linking
        # WebCore but not actually generating it
        super().__init__(config,
                         # generator=BuildQtWebkit.Generator.Makefiles
                         generator=BuildQtWebkit.Generator.Ninja
                         )
        self.cross_warning_flags += ["-Wno-error", "-Wno-error=cheri-bitwise-operations", "-Wno-error=cheri-capability-misuse", "-Wno-error=format"]  # FIXME: build with capability -Werror
        self.add_cmake_options(PORT="Qt", ENABLE_X11_TARGET=False,
                               ENABLE_OPENGL=False,
                               USE_LIBHYPHEN=False,  # we don't have libhyphen
                               DEVELOPER_MODE=True, # needed to enable DumpRenderTree and ImageDiff
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
        # Use llvm-{ar,ranlib} because elftoolchain's versions truncate libWebCore.a
        self.add_cmake_options(CMAKE_AR=self.config.sdkBinDir / "llvm-ar")
        self.add_cmake_options(CMAKE_RANLIB=self.config.sdkBinDir / "llvm-ranlib")
        self.add_cmake_options(ENABLE_JIT=False,  # Not supported on MIPS
                               QT_STATIC_BUILD=True,  # we always build qt static for now
                               QT_BUNDLED_PNG=True,  # use libpng from Qt
                               # QT_BUNDLED_JPEG=True,  # use libjpeg from Qt
                               QTWEBKIT_LINK_STATIC_ONLY=self.force_static_linkage
                               )
        if not self.compiling_for_host():
            # we need to find the installed Qt
            self.add_cmake_options(Qt5_DIR=self.crossSysrootPath / ("usr/local/Qt-" + self._crossCompileTarget.value) / "lib/cmake/Qt5")
            self.add_cmake_options(PNG_LIBRARIES="libqtlibpng.a")
            self.add_cmake_options(PNG_INCLUDE_DIRS=BuildQtBase.getSourceDir(self, config) / "src/3rdparty/libpng")
            self.LDFLAGS.extend(["-lpthread"]) # Needed for DumpRenderTree

            # Pass CHERI capability size so we can pass this to the offlineasm ruby scripts
            if self._crossCompileTarget == CrossCompileTarget.CHERI:
                if self.config.cheriBits == 128:
                    self.add_cmake_options(CHERI_CAPABILITY_SIZE=128)
                elif self.config.cheriBits == 256:
                    self.add_cmake_options(CHERI_CAPABILITY_SIZE=256)
                self.add_cmake_options(CHERI_PURE_CAPABILITY=True)

            if not self.compiling_for_host():
                self.add_cmake_options(QTWEBKIT_LINK_STATIC_ONLY=self.force_static_linkage)

        self._addRequiredSystemTool("gperf")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.build_jsc_only = cls.addBoolOption("build-jsc-only", showHelp=True, help="only build the JavaScript interpreter executable")

    def compile(self, **kwargs):
        if self.build_jsc_only:
            self.runMake("jsc")
        else:
            self.runMake("all")

    def install(self, **kwargs):
        self.info("Not installing qtwebit since it uses too much space. If you really want this run `ninja install`")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("Running host tests not implemented")
        else:
            self.run_cheribsd_test_script("run_qtwebkit_tests.py", use_benchmark_kernel_by_default=True,
                                          mount_builddir=True, mount_sourcedir=True, mount_sysroot=True)
