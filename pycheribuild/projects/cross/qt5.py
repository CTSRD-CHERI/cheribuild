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
import tempfile

from .crosscompileproject import *
from ...config.loader import ComputedDefaultValue
from ...utils import commandline_to_str, runCmd, IS_FREEBSD, IS_MAC, fatalError, IS_LINUX, getCompilerInfo


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

    default_build_type = BuildType.MINSIZERELWITHDEBINFO # Default to -Os with debug info:

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configureCommand = self.sourceDir / "configure"
        if not self.compiling_for_host():
            self._linkage = Linkage.STATIC
        if self.compiling_for_mips(include_purecap=False) and self.force_static_linkage:
            assert "-mxgot" in self.default_compiler_flags

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_tests = cls.addBoolOption("build-tests", showHelp=True, help="build the Qt unit tests")
        cls.build_examples = cls.addBoolOption("build-examples", showHelp=True, help="build the Qt examples")
        cls.assertions = cls.addBoolOption("assertions", default=False, showHelp=True, help="Include assertions")
        cls.minimal = cls.addBoolOption("minimal", showHelp=True, help="Don't build QtWidgets or QtGui, etc")
        cls.optimized_debug_build = cls.addBoolOption("optimized-debug-build",
                                                      help="Don't build with -Os instead of -O0 for debug info builds")

    def configure(self, **kwargs):
        if self.force_static_linkage:
            self.configureArgs.append("-static")

        if self.compiling_for_host():
            self.configureArgs.extend(["-prefix", str(self.installDir)])
            self.configureArgs.append("QMAKE_CC=" + str(self.config.clangPath))
            self.configureArgs.append("QMAKE_CXX=" + str(self.config.clangPlusPlusPath))
            if IS_LINUX and getCompilerInfo(self.config.clangPath).is_clang:
                # otherwise the build assumes GCC
                self.configureArgs.append("-platform")
                self.configureArgs.append("linux-clang")
            # FreeBSD header files may use the register storage class but c++17 disallows this
            if IS_FREEBSD:
                self.configureArgs.append("-platform")
                self.configureArgs.append("offscreen")
                self.configureArgs.extend(["-c++std", "c++14"])
        else:
            # make sure we use libc++ (only happens with mips64-unknown-freebsd10 and greater)
            compiler_flags = self.default_compiler_flags
            linker_flags = self.default_ldflags + ["-target", self.target_info.target_triple]
            assert self.force_static_linkage, "Currently only static linking is supported!"

            if self.compiling_for_cheri():
                self.configureArgs.append("QMAKE_LIBDIR=" + str(self.crossSysrootPath / "usr/libcheri"))
            elif self.compiling_for_mips(include_purecap=False):
                # self.configureArgs.append("QMAKE_CXXFLAGS+=-stdlib=libc++")
                pass

            # The build system already passes these:
            linker_flags = filter(lambda s: not s.startswith("--sysroot"), linker_flags)
            compiler_flags = filter(lambda s: not s.startswith("--sysroot"), compiler_flags)
            cross_compile_prefix = self.target_info.target_triple
            if self.compiling_for_cheri() or self.compiling_for_mips(include_purecap=False):
                cross_compile_prefix = "mips64-unknown-freebsd"
            self.configureArgs.extend([
                "-device", "freebsd-generic-clang",
                "-device-option", "CROSS_COMPILE={}/{}-".format(self.sdk_bindir, cross_compile_prefix),
                "-device-option", "COMPILER_FLAGS=" + commandline_to_str(compiler_flags),
                "-device-option", "LINKER_FLAGS=" + commandline_to_str(linker_flags),
                "-sysroot", self.crossSysrootPath,
                "-prefix", "/usr/local/" + self._crossCompileTarget.generic_suffix
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

        print("TYPE:", self.cross_build_type)
        # TODO: once we update to qt 5.12 add this:
        # self.configureArgs.append("-gdb-index")
        if self.cross_build_type == BuildType.DEBUG:
            self.configureArgs.append("-debug")
            # optimize-debug needs GCC
            # self.configureArgs.append("-optimize-debug")
        else:
            assert self.cross_build_type in (BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO,
                                             BuildType.MINSIZEREL, BuildType.RELEASE)
            self.configureArgs.append("-release")
            if self.cross_build_type in (BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO):
                self.configureArgs.append("-force-debug-info")
            if self.cross_build_type in (BuildType.MINSIZEREL, BuildType.MINSIZERELWITHDEBINFO):
                self.configureArgs.append("-optimize-size")  # Use -Os, otherwise it will use -O3

        if self.assertions:
            self.configureArgs.append("-force-asserts")

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
    repository = GitRepository("https://github.com/CTSRD-CHERI/qt5", default_branch="5.10", force_branch=True)
    skipGitSubmodules = True  # init-repository does it for us

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
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

    def process(self):
        if not self.compiling_for_host():
            self.fatal("This target is currently broken, use qtbase instead.")
        super().process()


class BuildQtBase(BuildQtWithConfigureScript):
    doNotAddToTargets = False  # Even though it ends in Base this is not a Base class
    repository = GitRepository("https://github.com/CTSRD-CHERI/qtbase", default_branch="5.10", force_branch=True)
    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.getSourceDir(project, config) / "qtbase",
        as_string=lambda cls: "$SOURCE_ROOT/qt5" + cls.project_name.lower())

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


# Webkit needs ICU (and recommended for QtBase too:
class BuildICU4C(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/icu4c.git")
    crossInstallDir = CrossInstallDir.SDK
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def dependencies(cls, config: CheriConfig):
        deps = super().dependencies(config)
        target = cls.get_crosscompile_target(config)
        # ICU4C needs a native buid to cross-compile:
        if not target.is_native():
            deps.append("icu4c-native")
        return deps

    def __init__(self, config):
        super().__init__(config)
        if not self.compiling_for_host() and BuildQtWebkit.get_instance(self, config).force_static_linkage:
            self._linkage = Linkage.STATIC  # make sure it works with webkit
        self.configureCommand = self.sourceDir / "source/configure"
        self.configureArgs.extend(["--disable-plugins", "--disable-dyload",
                                   "--disable-tests",
                                   "--disable-samples"])
        self.nativeBuildDir = self.build_dir_for_target(CrossCompileTarget.NATIVE)
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
    repository = GitRepository("https://github.com/CTSRD-CHERI/libxml2")
    crossInstallDir = CrossInstallDir.SDK
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config):
        super().__init__(config)
        if not self.compiling_for_host() and BuildQtWebkit.get_instance(self, config).force_static_linkage:
            self._linkage = Linkage.STATIC  # make sure it works with webkit

        if (self.sourceDir / "configure").exists():
            self.configureCommand = self.sourceDir / "configure"
        else:
            self.configureCommand = self.sourceDir / "autogen.sh"
        self.configureArgs.extend([
            "--without-python", "--without-modules", "--without-lzma",
        ])
        if IS_MAC:
            self.addRequiredSystemTool("glibtoolize", homebrew="libtool")
            self.configureEnvironment["LIBTOOLIZE"] = "glibtoolize"
        self.cross_warning_flags += ["-Wno-error", "-Wno-error=cheri-capability-misuse"]  # FIXME: build with capability -Werror


class BuildQtWebkit(CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/qtwebkit",
                               default_branch="qtwebkit-5.212-cheri", force_branch=True)
    dependencies = ["qtbase", "icu4c", "libxml2", "sqlite"]
    # webkit is massive if we include debug info
    default_build_type = BuildType.RELWITHDEBINFO

    crossInstallDir = CrossInstallDir.SDK
    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.getSourceDir(project, config) / "qtwebkit",
        as_string=lambda cls: "$SOURCE_ROOT/qt5" + cls.project_name.lower())
    needs_mxcaptable_static = True  # Currently way over the limit
    needs_mxcaptable_dynamic = True  # Currently way over the limit

    def __init__(self, config: CheriConfig):
        # There is a bug in the cmake ninja generator that makes it use a response file for linking
        # WebCore but not actually generating it
        super().__init__(config,
                         # generator=BuildQtWebkit.Generator.Makefiles
                         generator=BuildQtWebkit.Generator.Ninja
                         )
        self.addRequiredSystemTool("update-mime-database", homebrew="shared-mime-info", apt="shared-mime-info")
        self.addRequiredSystemTool("ruby", apt="ruby")
        if not self.compiling_for_host():
            self._linkage = Linkage.STATIC  # currently dynamic doesn't work

        self.cross_warning_flags += ["-Wno-error", "-Wno-error=cheri-bitwise-operations", "-Wno-error=cheri-capability-misuse", "-Wno-error=format"]  # FIXME: build with capability -Werror
        if self.include_debug_info:
            self.COMMON_FLAGS.append("-gline-tables-only") # otherwise too much debug info
        self.add_cmake_options(PORT="Qt", ENABLE_X11_TARGET=False,
                               ENABLE_OPENGL=False,
                               USE_LIBHYPHEN=False,  # we don't have libhyphen
                               DEVELOPER_MODE=True, # needed to enable DumpRenderTree and ImageDiff
                               ENABLE_VIDEO=False,  # probably depends on lots of stuff
                               ENABLE_XSLT=False,  # 1 less library to build
                               USE_GSTREAMER=False,  # needs all the glib+gtk crap
                               USE_LD_GOLD=False,  # Webkit wants to use gold by default...
                               USE_SYSTEM_MALLOC=True,  # we want bounds (instead of the fast bump-the-pointer bmalloc code)
                               ENABLE_API_TESTS=False,
                               )
        # TODO: when we use the full build of Qt enable these:
        self.add_cmake_options(ENABLE_GEOLOCATION=False,  # needs QtPositioning
                               ENABLE_PRINT_SUPPORT=False,  # needs QtPrintSupport
                               ENABLE_DEVICE_ORIENTATION=False,  # needs QtSensors
                               ENABLE_WEBKIT2=False,  # needs QtQuick
                               )
        # Use llvm-{ar,ranlib} because elftoolchain's versions truncate libWebCore.a
        self.add_cmake_options(CMAKE_AR=self.config.cheri_sdk_bindir / "llvm-ar")
        self.add_cmake_options(CMAKE_RANLIB=self.config.cheri_sdk_bindir / "llvm-ranlib")
        self.add_cmake_options(ENABLE_JIT=False,  # Not supported on MIPS
                               QT_STATIC_BUILD=True,  # we always build qt static for now
                               QT_BUNDLED_PNG=True,  # use libpng from Qt
                               # QT_BUNDLED_JPEG=True,  # use libjpeg from Qt
                               QTWEBKIT_LINK_STATIC_ONLY=self.force_static_linkage
                               )
        if not self.compiling_for_host():
            # we need to find the installed Qt
            self.add_cmake_options(Qt5_DIR=self.crossSysrootPath / ("usr/local/" + self._crossCompileTarget.generic_suffix) / "lib/cmake/Qt5")
            self.add_cmake_options(PNG_LIBRARIES="libqtlibpng.a")
            self.add_cmake_options(PNG_INCLUDE_DIRS=BuildQtBase.getSourceDir(self) / "src/3rdparty/libpng")
            self.LDFLAGS.extend(["-lpthread"]) # Needed for DumpRenderTree

            # Pass CHERI capability size so we can pass this to the offlineasm ruby scripts
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.add_cmake_options(CHERI_CAPABILITY_SIZE=128)
                elif self.config.cheriBits == 256:
                    self.add_cmake_options(CHERI_CAPABILITY_SIZE=256)
                self.add_cmake_options(CHERI_PURE_CAPABILITY=True)

            if not self.compiling_for_host():
                self.add_cmake_options(QTWEBKIT_LINK_STATIC_ONLY=self.force_static_linkage)

        self.addRequiredSystemTool("gperf")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_jsc_only = cls.addBoolOption("build-jsc-only", showHelp=True, help="only build the JavaScript interpreter executable")

    def compile(self, **kwargs):
        # Generate the shared mime info cache to MASSIVELY speed up tests
        with tempfile.TemporaryDirectory(prefix="cheribuild-" + self.target + "-") as td:
            mime_info_src = BuildQtBase.getSourceDir(self) / "src/corelib/mimetypes/mime/packages/freedesktop.org.xml"
            self.installFile(mime_info_src, Path(td, "mime/packages/freedesktop.org.xml"), force=True, print_verbose_only=False)
            try:
                runCmd("update-mime-database", "-V", Path(td, "mime"), cwd="/")
            except:
                input("Failed in" + td + "/mime")
                raise

            if not Path(td, "mime/mime.cache").exists():
                fatalError("Could not generated shared-mime-info cache!")
            # install mime.cache and freedesktop.org.xml into the build dir for tests
            self.installFile(mime_info_src, self.buildDir / "freedesktop.org.xml", force=True, print_verbose_only=False)
            self.installFile(Path(td, "mime/mime.cache"), self.buildDir / "mime.cache", force=True, print_verbose_only=False)
            # TODO: get https://github.com/annulen/webkit-test-fonts to run the full testsuite
        if self.build_jsc_only:
            self.runMake("jsc")
        else:
            self.runMake("all")

    def install(self, **kwargs):
        # create a stripped version of DumpRenderTree and jsc since the one with debug info is too big
        if not self.build_jsc_only:
            dump_render_tree = self.buildDir / "bin/DumpRenderTree" # type: Path
            if dump_render_tree.is_file():
                runCmd(self.config.cheri_sdk_bindir / "llvm-strip", "-o", dump_render_tree.with_suffix(".stripped"), dump_render_tree)
        jsc = self.buildDir / "bin/jsc" # type: Path
        if jsc.is_file():
            runCmd(self.config.cheri_sdk_bindir / "llvm-strip", "-o", jsc.with_suffix(".stripped"), jsc)
        self.info("Not installing qtwebit since it uses too much space. If you really want this run `ninja install`")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("Running host tests not implemented")
        else:
            self.run_cheribsd_test_script("run_qtwebkit_tests.py", use_benchmark_kernel_by_default=True,
                                          mount_builddir=True, mount_sourcedir=True, mount_sysroot=True)
