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
from pathlib import Path

from .crosscompileproject import (BuildType, CheriConfig, CompilationTargets, CrossCompileAutotoolsProject,
                                  CrossCompileCMakeProject, CrossCompileProject, DefaultInstallDir, GitRepository,
                                  Linkage, MakeCommandKind)
from ...config.loader import ComputedDefaultValue
from ...utils import commandline_to_str, get_compiler_info, OSInfo


# This class is used to build qtbase and all of qt5
class BuildQtWithConfigureScript(CrossCompileProject):
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.SYSROOT
    do_not_add_to_targets = True
    add_host_target_build_config_options = False
    # Should not be needed, but it seems like some of the tests are broken otherwise
    make_kind = MakeCommandKind.GnuMake
    needs_mxcaptable_static = True  # Currently over the limit, maybe we need -ffunction-sections/-fdata-sections
    hide_options_from_help = True  # hide this for now

    default_build_type = BuildType.MINSIZERELWITHDEBINFO  # Default to -Os with debug info:

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configure_command = self.source_dir / "configure"
        if not self.compiling_for_host():
            self._linkage = Linkage.STATIC

    def setup(self):
        super().setup()
        if self.compiling_for_mips(include_purecap=False) and self.force_static_linkage:
            assert "-mxgot" in self.default_compiler_flags

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_tests = cls.add_bool_option("build-tests", show_help=True, help="build the Qt unit tests")
        cls.build_examples = cls.add_bool_option("build-examples", show_help=True, help="build the Qt examples")
        cls.assertions = cls.add_bool_option("assertions", default=False, show_help=True, help="Include assertions")
        cls.minimal = cls.add_bool_option("minimal", show_help=True, help="Don't build QtWidgets or QtGui, etc")
        cls.optimized_debug_build = cls.add_bool_option("optimized-debug-build",
                                                        help="Don't build with -Os instead of -O0 for debug info "
                                                             "builds")

    def configure(self, **kwargs):
        if self.force_static_linkage:
            self.configure_args.append("-static")

        if self.compiling_for_host():
            self.configure_args.extend(["-prefix", str(self.install_dir)])
            self.configure_args.append("QMAKE_CC=" + str(self.CC))
            self.configure_args.append("QMAKE_CXX=" + str(self.CXX))
            if OSInfo.IS_LINUX and get_compiler_info(self.CC).is_clang:
                # otherwise the build assumes GCC
                self.configure_args.append("-platform")
                self.configure_args.append("linux-clang")
            # FreeBSD header files may use the register storage class but c++17 disallows this
            if OSInfo.IS_FREEBSD:
                self.configure_args.append("-platform")
                self.configure_args.append("offscreen")
                self.configure_args.extend(["-c++std", "c++14"])
        else:
            # make sure we use libc++ (only happens with mips64-unknown-freebsd10 and greater)
            compiler_flags = self.default_compiler_flags
            linker_flags = self.default_ldflags + ["-target", self.target_info.target_triple]
            assert self.force_static_linkage, "Currently only static linking is supported!"

            if self.crosscompile_target.is_cheri_purecap():
                # Note: we are using the hybrid sysroot, so base system libraries are in usr/libcheri:
                self.configure_args.append("QMAKE_LIBDIR=" + str(self.cross_sysroot_path / "usr/libcheri"))
            elif self.compiling_for_mips(include_purecap=False):
                # self.configure_args.append("QMAKE_CXXFLAGS+=-stdlib=libc++")
                pass

            # The build system already passes these:
            linker_flags = filter(lambda s: not s.startswith("--sysroot"), linker_flags)
            compiler_flags = filter(lambda s: not s.startswith("--sysroot"), compiler_flags)
            cross_compile_prefix = self.target_info.target_triple
            if self.compiling_for_mips(include_purecap=True):
                cross_compile_prefix = "mips64-unknown-freebsd"
            self.configure_args.extend([
                "-device", "freebsd-generic-clang",
                "-device-option", "CROSS_COMPILE={}/{}-".format(self.sdk_bindir, cross_compile_prefix),
                "-device-option", "COMPILER_FLAGS=" + commandline_to_str(compiler_flags),
                "-device-option", "LINKER_FLAGS=" + commandline_to_str(linker_flags),
                "-sysroot", self.cross_sysroot_path,
                "-prefix", "/usr/local/" + self._xtarget.generic_suffix
                ])

        self.configure_args.extend([
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
            self.configure_args.append("-developer-build")
            if OSInfo.IS_MAC:
                # Otherwise we get "ERROR: debug-only framework builds are not supported. Configure with -no-framework
                # if you want a pure debug build."
                self.configure_args.append("-no-framework")

        else:
            self.configure_args.extend(["-nomake", "tests"])

        if not self.build_examples:
            # Seems to have changed
            self.configure_args.extend(["-nomake", "examples", "-no-compile-examples"])
        # currently causes build failures:
        # Seems like I need to define PNG_READ_GAMMA_SUPPORTED
        self.configure_args.append("-qt-libpng")

        print("TYPE:", self.build_type)
        # TODO: once we update to qt 5.12 add this:
        # self.configure_args.append("-gdb-index")
        if self.build_type == BuildType.DEBUG:
            self.configure_args.append("-debug")
            # optimize-debug needs GCC
            # self.configure_args.append("-optimize-debug")
        else:
            assert self.build_type in (BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO,
                                       BuildType.MINSIZEREL, BuildType.RELEASE)
            self.configure_args.append("-release")
            if self.build_type in (BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO):
                self.configure_args.append("-force-debug-info")
            if self.build_type in (BuildType.MINSIZEREL, BuildType.MINSIZERELWITHDEBINFO):
                self.configure_args.append("-optimize-size")  # Use -Os, otherwise it will use -O3

        if self.assertions:
            self.configure_args.append("-force-asserts")

        self.configure_args.append("-no-pch")  # slows down build but gives useful crash testcases

        #  -reduce-exports ...... Reduce amount of exported symbols [auto]
        self.configure_args.append("-reduce-exports")
        # -reduce-relocations .. Reduce amount of relocations [auto] (Unix only)
        # TODO: this needs PIE:
        # self.configure_args.append("-reduce-relocations")

        if self.minimal:
            self.configure_args.extend([
                "-no-widgets",
                "-no-glib",
                "-no-gtk",
                "-no-opengl",
                "-no-cups",
                "-no-syslog",
                "-no-gui",
                "-no-iconv"
                ])

        self.configure_args.extend(["-opensource", "-confirm-license"])

        self.delete_file(self.build_dir / "config.cache")
        self.delete_file(self.build_dir / "config.opt")
        self.delete_file(self.build_dir / "config.status")
        super().configure()

    def needs_configure(self):
        return not (self.build_dir / "Makefile").exists()


class BuildQtBaseDev(CrossCompileCMakeProject):
    project_name = "qtbase"
    target = "qtbase-dev"
    repository = GitRepository("https://github.com/CTSRD-CHERI/qtbase", default_branch="dev", force_branch=True)
    is_large_source_repository = True
    default_source_dir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.get_source_dir(project, config) / "qtbase",
        as_string=lambda cls: "$SOURCE_ROOT/qt5" + cls.project_name.lower())
    # native_install_dir = DefaultInstallDir.CHERI_SDK
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.SYSROOT
    needs_mxcaptable_static = True  # Currently over the limit, maybe we need -ffunction-sections/-fdata-sections
    # default_build_type = BuildType.MINSIZERELWITHDEBINFO  # Default to -Os with debug info:
    default_build_type = BuildType.DEBUG

    @property
    def needs_mxcaptable_dynamic(self):
        # Debug build: 35927 entries to .captable but current maximum is 32768
        return self.build_type == BuildType.DEBUG

    @classmethod
    def dependencies(cls, config: CheriConfig):
        deps = super().dependencies(config)
        target = cls.get_crosscompile_target(config)
        # QtBase needs a native buid to cross-compile:
        if not target.is_native():
            deps.append("qtbase-dev-native")
        return deps

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_tests = cls.add_bool_option("build-tests", default=True, show_help=True, help="build the Qt unit tests")
        cls.build_examples = cls.add_bool_option("build-examples", show_help=True, help="build the Qt examples")
        cls.assertions = cls.add_bool_option("assertions", default=True, show_help=True, help="Include assertions")
        cls.minimal = cls.add_bool_option("minimal", show_help=True, default=True, help="Don't build QtWidgets or QtGui, etc")
        cls.optimized_debug_build = cls.add_bool_option("optimized-debug-build",
                                                        help="Don't build with -Os instead of -O0 for debug info "
                                                             "builds")

    def __init__(self, config):
        super().__init__(config)
        self.set_minimum_cmake_version(3, 18)

    def process(self):
        if not self.compiling_for_host() and not (self.host_target.install_dir / "bin/moc").exists():
            self.fatal("Missing host build moc tool", self.host_target.install_dir / "bin/moc",
                       " (needed for cross-compiling)", fixit_hint="Run `cheribuild.py " + self.target + "-native`")
        super().process()

    def setup(self):
        super().setup()
        # noinspection PyAttributeOutsideInit
        self.host_target = self.get_instance(self, cross_target=CompilationTargets.NATIVE)

        if self.compiling_for_mips(include_purecap=False) and self.force_static_linkage:
            assert "-mxgot" in self.default_compiler_flags
        if self.force_static_linkage:
            self.add_cmake_options(BUILD_SHARED_LIBS=False)

        if not self.compiling_for_host():
            assert self.target_info.is_freebsd(), "Not other targets supported yet"
            self.add_cmake_options(QT_HOST_PATH=self.host_target.install_dir,
                                   QT_QMAKE_TARGET_MKSPEC="freebsd-clang")
        if self.compiling_for_cheri():
            # Not ported to CHERI purecap
            self.add_cmake_options(PCRE2_DISABLE_JIT=True)

        # Disable most libraries for now (we only test qtcore)
        if self.minimal:
            self.add_cmake_options(
                QT_FEATURE_sql=False,
                QT_FEATURE_network=False,
                # QT_FEATURE_xml=False,  disabling this breaks the build
                QT_FEATURE_dbus=False,
                # Disable all GUI libs
                QT_FEATURE_gui=False, QT_FEATURE_opengl=False, QT_FEATURE_widgets=False)

            self.add_cmake_options(QT_FEATURE_png=False, QT_FEATURE_freetype=False)

        # if not self.compiling_for_host():
        # Seems to break the build
        #    self.add_cmake_options(FEATURE_use_lld_linker=True)
        # disable Werror? WARNINGS_ARE_ERRORS=False
        # TODO: Still needed? "-no-evdev"
        # TODO: require ICU "-icu",
        # TODO: "-no-iconv"
        if self.target_info.is_macos():
            self.add_cmake_options(QT_FEATURE_icu=False)  # Not linked correctly -> tests fail to run
            self.add_cmake_options(CMAKE_PREFIX_PATH="/usr/local")  # Find homebrew libraries
        if self.build_tests:
            self.add_cmake_options(FEATURE_developer_build=True)
            if OSInfo.IS_MAC:
                # Otherwise we get "ERROR: debug-only framework builds are not supported. Configure with -no-framework
                # if you want a pure debug build."
                self.add_cmake_options(FEATURE_framework=False)
        else:
            self.add_cmake_options(BUILD_TESTING=False)
        self.add_cmake_options(BUILD_EXAMPLES=self.build_examples)

        # currently causes build failures:
        self.add_cmake_options(QT_FEATURE_system_png=False)

        if self.assertions:
            self.add_cmake_options(FEATURE_force_asserts=True)
        self.add_cmake_options(BUILD_WITH_PCH=False)  # slows down build but gives useful crash testcases
        # QT_FEATURE_reduce_relocations


class BuildQt5(BuildQtWithConfigureScript):
    repository = GitRepository("https://github.com/CTSRD-CHERI/qt5", default_branch="5.10", force_branch=True)
    skip_git_submodules = True  # init-repository does it for us

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.all_modules = cls.add_bool_option("all-modules", show_help=True,
                                              help="Build all modules (even those that don't make sense for CHERI)")

    def configure(self, **kwargs):
        if not self.all_modules:
            modules_to_skip = "qtgamepad qtlocation".split()
            for i in modules_to_skip:
                self.configure_args.extend(["-skip", i])
            # TODO: skip modules that just increase compile time and are useless
        super().configure(**kwargs)

    def update(self):
        super().update()
        # qtlocation breaks for some reason if qt5 is forked on github
        # TODO: qtwebkit, but that won't cross-compile with QMAKE
        self.run_cmd("perl", "init-repository", "--module-subset=essential", "-f", "--branch", cwd=self.source_dir)

    def process(self):
        if not self.compiling_for_host():
            self.fatal("This target is currently broken, use qtbase instead.")
        super().process()


class BuildQtBase(BuildQtWithConfigureScript):
    do_not_add_to_targets = False  # Even though it ends in Base this is not a Base class
    repository = GitRepository("https://github.com/CTSRD-CHERI/qtbase", default_branch="5.10", force_branch=True)
    is_large_source_repository = True
    default_source_dir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.get_source_dir(project, config) / "qtbase",
        as_string=lambda cls: "$SOURCE_ROOT/qt5" + cls.project_name.lower())

    def setup(self):
        super().setup()
        self.cross_warning_flags += ["-Wno-shadow",
                                     "-Wno-error=cheri-bitwise-operations"]  # FIXME: remove after update to 5.12

    def compile(self, **kwargs):
        if self.minimal:
            self.run_make("sub-src")
            if self.build_tests:
                # only build the tests for corelib:
                if not (self.build_dir / "tests/auto/corelib").exists():
                    # generate the makefiles
                    self.run_make("sub-tests-make_first")
                self.run_make("sub-corelib", cwd=self.build_dir / "tests/auto")
        else:
            self.run_make()  # QtBase ignores -nomake if you run "gmake all"

    def run_tests(self):
        if self.compiling_for_host():
            self.run_cmd("make", "check", cwd=self.build_dir)
        else:
            self.target_info.run_cheribsd_test_script("run_qtbase_tests.py", use_benchmark_kernel_by_default=True)


# Webkit needs ICU (and recommended for QtBase too):
class BuildICU4C(CrossCompileAutotoolsProject):
    # noinspection PyUnreachableCode
    repository = GitRepository("https://github.com/CTSRD-CHERI/icu.git", default_branch="maint/maint-67",
                               force_branch=True, old_urls=[b"https://github.com/unicode-org/icu.git"])
    project_name = "icu"
    target = "icu4c"
    build_dir_suffix = "4c"
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.SYSROOT
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def dependencies(cls, config: CheriConfig):
        deps = super().dependencies(config)
        target = cls.get_crosscompile_target(config)
        # ICU4C needs a native buid to cross-compile:
        if not target.is_native():
            deps.append("icu4c-native")
        return deps

    def linkage(self):
        if not self.compiling_for_host() and BuildQtWebkit.get_instance(self, self.config).force_static_linkage:
            return Linkage.STATIC  # make sure it works with webkit
        return super().linkage()

    def __init__(self, config):
        super().__init__(config)
        self.configure_command = self.source_dir / "icu4c/source/configure"
        self.configure_args.extend(["--disable-plugins", "--disable-dyload",
                                    "--disable-tests",
                                    "--disable-samples"])
        self.native_build_dir = self.build_dir_for_target(CompilationTargets.NATIVE)
        # we can't create objects for a different endianess:
        self.COMMON_FLAGS.append("-DU_DISABLE_OBJ_CODE")
        self.cross_warning_flags += ["-Wno-error"]  # FIXME: build with capability -Werror

        if not self.compiling_for_host():
            self.configure_args.append("--with-cross-build=" + str(self.native_build_dir))
            # can't build them yet
            # error: undefined symbol: uconvmsg_dat
            # self.configure_args.append("--disable-tools")
            # but these seem to be needed
            # self.configure_args.append("--disable-draft")
            # self.configure_args.append("--disable-extras")  # can't add this to host build, it will fail otherwise
            # We have modified the ICU data Makefile so that ICU builds a big endian data archive
            self.configure_args.append("--with-data-packaging=archive")

    def process(self):
        if not self.compiling_for_host() and not (self.native_build_dir / "bin/icupkg").exists():
            self.fatal("Missing host build directory", self.native_build_dir, " (needed for cross-compiling)",
                       fixit_hint="Run `cheribuild.py " + self.target + "-native`")
        super().process()


# it also needs libxml2
class BuildLibXml2(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/libxml2")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.SYSROOT
    make_kind = MakeCommandKind.GnuMake

    def linkage(self):
        if not self.compiling_for_host() and BuildQtWebkit.get_instance(self, self.config).force_static_linkage:
            return Linkage.STATIC  # make sure it works with webkit
        return super().linkage()

    def __init__(self, config):
        super().__init__(config)
        if (self.source_dir / "configure").exists():
            self.configure_command = self.source_dir / "configure"
        else:
            self.configure_command = self.source_dir / "autogen.sh"
        self.configure_args.extend([
            "--without-python", "--without-modules", "--without-lzma",
            ])
        if OSInfo.IS_MAC:
            self.add_required_system_tool("glibtoolize", homebrew="libtool")
            self.configure_environment["LIBTOOLIZE"] = "glibtoolize"
        self.cross_warning_flags += ["-Wno-error",
                                     "-Wno-error=cheri-capability-misuse"]  # FIXME: build with capability -Werror


class BuildQtWebkit(CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/qtwebkit",
                               default_branch="qtwebkit-5.212-cheri", force_branch=True)
    is_large_source_repository = True
    dependencies = ["qtbase", "icu4c", "libxml2", "sqlite"]
    # webkit is massive if we include debug info
    default_build_type = BuildType.RELWITHDEBINFO

    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.SYSROOT
    default_source_dir = ComputedDefaultValue(
        function=lambda config, project: BuildQt5.get_source_dir(project, config) / "qtwebkit",
        as_string=lambda cls: "$SOURCE_ROOT/qt5" + cls.project_name.lower())
    needs_mxcaptable_static = True  # Currently way over the limit
    needs_mxcaptable_dynamic = True  # Currently way over the limit

    @property
    def llvm_binutils_dir(self) -> Path:
        if self.compiling_for_host():
            return self.config.cheri_sdk_bindir  # Use the CHERI SDK for native
        return self.target_info.sdk_root_dir / "bin"

    def linkage(self):
        if not self.compiling_for_host():
            return Linkage.STATIC  # currently dynamic doesn't work
        return super().linkage()

    def __init__(self, config: CheriConfig):
        # There is a bug in the cmake ninja generator that makes it use a response file for linking
        # WebCore but not actually generating it
        super().__init__(config,
                         # generator=BuildQtWebkit.Generator.Makefiles
                         generator=BuildQtWebkit.Generator.Ninja
                         )
        self.add_required_system_tool("update-mime-database", homebrew="shared-mime-info", apt="shared-mime-info")
        self.add_required_system_tool("ruby", apt="ruby")

        self.cross_warning_flags += ["-Wno-error", "-Wno-error=cheri-bitwise-operations",
                                     "-Wno-error=cheri-capability-misuse",
                                     "-Wno-error=format"]  # FIXME: build with capability -Werror
        # We are building an old version of webkit
        self.cross_warning_flags.append("-Wno-deprecated-copy")
        if self.should_include_debug_info:
            self.COMMON_FLAGS.append("-gline-tables-only")  # otherwise too much debug info
        self.add_cmake_options(PORT="Qt", ENABLE_X11_TARGET=False,
                               ENABLE_OPENGL=False,
                               USE_LIBHYPHEN=False,  # we don't have libhyphen
                               DEVELOPER_MODE=True,  # needed to enable DumpRenderTree and ImageDiff
                               ENABLE_VIDEO=False,  # probably depends on lots of stuff
                               ENABLE_XSLT=False,  # 1 less library to build
                               USE_GSTREAMER=False,  # needs all the glib+gtk crap
                               USE_LD_GOLD=False,  # Webkit wants to use gold by default...
                               USE_SYSTEM_MALLOC=True,
                               # we want bounds (instead of the fast bump-the-pointer bmalloc code)
                               ENABLE_API_TESTS=False,
                               )
        # TODO: when we use the full build of Qt enable these:
        self.add_cmake_options(ENABLE_GEOLOCATION=False,  # needs QtPositioning
                               ENABLE_PRINT_SUPPORT=False,  # needs QtPrintSupport
                               ENABLE_DEVICE_ORIENTATION=False,  # needs QtSensors
                               ENABLE_WEBKIT2=False,  # needs QtQuick
                               )
        # Use llvm-{ar,ranlib} because elftoolchain's versions truncate libWebCore.a
        self.add_cmake_options(CMAKE_AR=self.llvm_binutils_dir / "llvm-ar")
        self.add_cmake_options(CMAKE_RANLIB=self.llvm_binutils_dir / "llvm-ranlib")
        self.add_cmake_options(ENABLE_JIT=False,  # Not supported on MIPS
                               QT_STATIC_BUILD=True,  # we always build qt static for now
                               QT_BUNDLED_PNG=True,  # use libpng from Qt
                               # QT_BUNDLED_JPEG=True,  # use libjpeg from Qt
                               QTWEBKIT_LINK_STATIC_ONLY=self.force_static_linkage
                               )
        if not self.compiling_for_host():
            # we need to find the installed Qt
            self.add_cmake_options(
                Qt5_DIR=self.cross_sysroot_path / ("usr/local/" + self._xtarget.generic_suffix) / "lib/cmake/Qt5")
            self.add_cmake_options(PNG_LIBRARIES="libqtlibpng.a")
            self.add_cmake_options(PNG_INCLUDE_DIRS=BuildQtBase.get_source_dir(self) / "src/3rdparty/libpng")
            if self.force_static_linkage:
                self.LDFLAGS.append("-pthread")  # Needed for DumpRenderTree when linking statically

            # Pass CHERI capability size so we can pass this to the offlineasm ruby scripts
            if self.crosscompile_target.is_hybrid_or_purecap_cheri():
                self.add_cmake_options(CHERI_CAPABILITY_SIZE=self.target_info.capability_size_in_bits)
            if self.crosscompile_target.is_cheri_purecap():
                self.add_cmake_options(CHERI_PURE_CAPABILITY=True)
            if not self.compiling_for_host():
                self.add_cmake_options(QTWEBKIT_LINK_STATIC_ONLY=self.force_static_linkage)

        self.add_required_system_tool("gperf")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_jsc_only = cls.add_bool_option("build-jsc-only", show_help=True,
                                                 help="only build the JavaScript interpreter executable")

    def compile(self, **kwargs):
        # Generate the shared mime info cache to MASSIVELY speed up tests
        with tempfile.TemporaryDirectory(prefix="cheribuild-" + self.target + "-") as td:
            mime_info_src = BuildQtBase.get_source_dir(self) / "src/corelib/mimetypes/mime/packages/freedesktop.org.xml"
            self.install_file(mime_info_src, Path(td, "mime/packages/freedesktop.org.xml"), force=True,
                              print_verbose_only=False)
            self.run_cmd("update-mime-database", "-V", Path(td, "mime"), cwd="/")

            if not Path(td, "mime/mime.cache").exists():
                self.fatal("Could not generated shared-mime-info cache!")
            # install mime.cache and freedesktop.org.xml into the build dir for tests
            self.install_file(mime_info_src, self.build_dir / "freedesktop.org.xml", force=True,
                              print_verbose_only=False)
            self.install_file(Path(td, "mime/mime.cache"), self.build_dir / "mime.cache", force=True,
                              print_verbose_only=False)
            # TODO: get https://github.com/annulen/webkit-test-fonts to run the full testsuite
        if self.build_jsc_only:
            self.run_make("jsc")
        else:
            self.run_make("all")

    def install(self, **kwargs):
        # create a stripped version of DumpRenderTree and jsc since the one with debug info is too big
        if not self.build_jsc_only:
            dump_render_tree = self.build_dir / "bin/DumpRenderTree"  # type: Path
            if dump_render_tree.is_file():
                self.run_cmd(self.llvm_binutils_dir / "llvm-strip", "-o", dump_render_tree.with_suffix(".stripped"),
                             dump_render_tree)
        jsc = self.build_dir / "bin/jsc"  # type: Path
        if jsc.is_file():
            self.run_cmd(self.llvm_binutils_dir / "llvm-strip", "-o", jsc.with_suffix(".stripped"), jsc)
        self.info("Not installing qtwebit since it uses too much space. If you really want this run `ninja install`")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("Running host tests not implemented")
        else:
            self.target_info.run_cheribsd_test_script("run_qtwebkit_tests.py", use_benchmark_kernel_by_default=True,
                                                      mount_builddir=True, mount_sourcedir=True, mount_sysroot=True)
