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
import os
import shutil
import tempfile
from pathlib import Path

from .crosscompileproject import (
    BuildType,
    CheriConfig,
    CompilationTargets,
    CrossCompileAutotoolsProject,
    CrossCompileCMakeProject,
    CrossCompileMesonProject,
    CrossCompileProject,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
)
from .wayland import BuildWayland
from .x11 import BuildLibXCB
from ..project import default_source_dir_in_subdir
from ..simple_project import BoolConfigOption, SimpleProject
from ...utils import InstallInstructions


class InstallDejaVuFonts(SimpleProject):
    target = "dejavu-fonts"
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS

    @property
    def fonts_dir(self):
        return self.target_info.sysroot_dir / self.target_info.localbase / "share/fonts"

    def process(self):
        version = (2, 37)
        subdir = "version_{}_{}".format(*version)
        filename = "dejavu-fonts-ttf-{}.{}.tar.bz2".format(*version)
        base_url = "https://github.com/dejavu-fonts/dejavu-fonts/releases/download"
        self.download_file(
            self.config.build_root / filename,
            url=base_url + "/" + subdir + "/" + filename,
            sha256="fa9ca4d13871dd122f61258a80d01751d603b4d3ee14095d65453b4e846e17d7",
        )
        # Install the fonts to /usr/local/share/fonts, so that fontconfig picks it up automatically.
        fonts_dir = self.target_info.sysroot_dir / "usr/local/share/fonts"
        self.makedirs(fonts_dir)
        # self.clean_directory(fonts_dir)
        self.run_cmd(
            "tar",
            "xvf",
            self.config.build_root / filename,
            "--strip-components=2",
            "-C",
            fonts_dir,
            "dejavu-fonts-ttf-{}.{}/ttf".format(*version),
        )
        self.run_cmd("find", fonts_dir)


class BuildSharedMimeInfo(CrossCompileMesonProject):
    target = "shared-mime-info"
    repository = GitRepository(
        "https://gitlab.freedesktop.org/xdg/shared-mime-info.git",
        old_urls=[b"https://gitlab.freedesktop.org/arichardson/shared-mime-info.git"],
    )
    # We don't actually want to install the mime info, we just want the update-mime-info tool for native builds
    native_install_dir = DefaultInstallDir.KDE_PREFIX
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    path_in_rootfs = "/usr/local"  # Always install to /usr/local/share so that it's in the default search path
    needs_native_build_for_crosscompile = True
    builds_docbook_xml = True

    @classmethod
    def get_update_mime_database_path(cls, caller: SimpleProject) -> Path:
        native_instance = cls.get_instance(caller, cross_target=CompilationTargets.NATIVE)
        if native_instance._can_build_tools:
            result = native_instance.install_dir / "bin/update-mime-database"
            if not result.exists():
                native_instance.dependency_error(
                    "Cannot find native update-mime-database",
                    cheribuild_target=native_instance.target,
                    cheribuild_xtarget=CompilationTargets.NATIVE,
                )
            return result
        else:
            # We are building on CheriBSD (purecap)
            assert native_instance.compiling_for_cheri()
            result = shutil.which("update-mime-database")
            if not result:
                native_instance.dependency_error(
                    "Cannot find native update-mime-database and can't build it for purecap yet",
                    install_instructions=InstallInstructions("pkg64 install shared-mime-info"),
                )
            return Path(result)

    @property
    def _can_build_tools(self):
        return self.compiling_for_host() and not self.compiling_for_cheri()  # Missing purecap glib2

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("xmlto", homebrew="xmlto", apt="xmlto")
        self.check_required_system_tool(
            "xmllint",
            homebrew="libxml2",
            apt="libxml2-utils",
            cheribuild_target="libxml2-native",
        )
        self.check_required_system_tool("msgfmt", freebsd="gettext-tools")  # no way to disable translations

    def setup(self):
        super().setup()
        self.add_meson_options(
            **{
                "update-mimedb": True,
                "build-tools": self._can_build_tools,
            }
        )
        if not self._can_build_tools:
            # Ensure that we have update-mime-database available as it will be used in a post-install action.
            self.get_update_mime_database_path(self)
        # self.configure_args.append("--wrap-mode=default")

    def configure(self, **kwargs):
        if not self.compiling_for_host():
            native_bin = self.get_instance(self, cross_target=CompilationTargets.NATIVE).install_dir / "bin"
            self.configure_environment["PATH"] = str(native_bin) + ":" + os.getenv("PATH")
        super().configure()


# This class is used to build qtbase and all of qt5
class BuildQtWithConfigureScript(CrossCompileProject):
    native_install_dir = DefaultInstallDir.CHERI_SDK
    do_not_add_to_targets = True
    add_host_target_build_config_options = False
    # Should not be needed, but it seems like some of the tests are broken otherwise
    make_kind = MakeCommandKind.GnuMake
    default_build_type = BuildType.MINSIZERELWITHDEBINFO  # Default to -Os with debug info:
    use_x11: bool
    use_opengl: bool
    minimal: bool

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("perl", freebsd="perl5")

    @property
    def qt_host_tools_path(self):
        if self.compiling_for_host():
            return self.install_dir
        else:
            return self.build_dir / "qt-host-tools"

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        deps = list(super().dependencies(config))
        rootfs_target = cls.get_crosscompile_target().get_rootfs_target()
        deps.append(BuildSharedMimeInfo.get_class_for_target(rootfs_target).target)
        deps.append("sqlite")  # TODO: minimal should probably not include QtSql
        if cls.minimal:
            return tuple(deps)
        if cls.use_x11:
            # The system X11 libraries might be too old, so add the cheribuild-provided ones as a dependency
            deps.extend(
                [
                    "libx11",
                    "libxcb",
                    "libxkbcommon",
                    "libxcb-cursor",
                    "libxcb-util",
                    "libxcb-image",
                    "libice",
                    "libsm",
                    "libxext",
                    "libxtst",
                    "libxcb-render-util",
                    "libxcb-wm",
                    "libxcb-keysyms",
                ]
            )
        # Always use our patched image/sql libraries instead of the host ones:
        deps.extend(["libpng", "libjpeg-turbo"])
        if not cls.get_crosscompile_target().is_native():
            # We can only depend on fonts when installing to a rootfs, as those need to be installed to a directory
            # that is only writable by root.
            deps.extend([InstallDejaVuFonts.get_class_for_target(rootfs_target).target])
        # For non-macOS we need additional libraries for GUI and openGL parts.
        if not cls.get_crosscompile_target().target_info_cls.is_macos():
            deps.extend(["dbus", "fontconfig", "libinput"])
            if cls.use_opengl:
                deps.extend(["libglvnd", "libdrm"])
        return tuple(deps)

    @classmethod
    def can_build_with_ccache(cls):
        return True

    def setup(self):
        super().setup()
        self.configure_command = self.source_dir / "configure"
        if self.compiling_for_mips(include_purecap=False) and self.force_static_linkage:
            assert "-mxgot" in self.default_compiler_flags
        if self.config.verbose:
            self.configure_args.append("-verbose")

    has_optional_tests = True
    default_build_tests = False

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_examples = cls.add_bool_option("build-examples", show_help=True, help="build the Qt examples")
        # Enable assertions by default for now
        assertions_by_default = True
        cls.assertions = cls.add_bool_option(
            "assertions",
            default=assertions_by_default,
            show_help=True,
            help="Include assertions (even in release builds)",
        )
        cls.minimal = cls.add_bool_option("minimal", show_help=True, help="Don't build QtWidgets or QtGui, etc")
        # Link against X11 libs by default if we aren't compiling for macOS
        native_is_macos = cls._xtarget is not None and cls._xtarget.target_info_cls.is_macos()
        cls.use_x11 = cls.add_bool_option(
            "use-x11",
            default=not native_is_macos,
            show_help=False,
            help="Build Qt with the XCB backend.",
        )
        cls.use_opengl = cls.add_bool_option(
            "use-opengl",
            default=True,
            show_help=False,
            help="Build Qt with OpenGL support",
        )

    def configure(self, **kwargs):
        if self.force_static_linkage:
            self.configure_args.append("-static")

        if self.compiling_for_host():
            self.configure_args.extend(["-prefix", str(self.install_dir)])
            self.configure_args.append("QMAKE_CC=" + str(self.CC))
            self.configure_args.append("QMAKE_CXX=" + str(self.CXX))
            if self.target_info.is_linux() and self.get_compiler_info(self.CC).is_clang:
                # otherwise the build assumes GCC
                self.configure_args.append("-platform")
                self.configure_args.append("linux-clang")
            if self.target_info.is_macos():
                # Use my (rejected) patch to add additional data directories for macos
                # (https://codereview.qt-project.org/c/qt/qtbase/+/238640), so that we can find shared data and run
                # KDE unit tests/applications correctly
                self.configure_args.extend(["-additional-datadir", self.install_dir / "share"])
                # Ideally we would check the KCoreAddons install dir here, but that creates a cyclic dependency.
                # Instead, we use BuildSharedMimeInfo since we know that it installs to the KDE_PREFIX.
                smi_instance = BuildSharedMimeInfo.get_instance(self)
                kde_install_dir = smi_instance.install_dir
                assert smi_instance.get_default_install_dir_kind() == DefaultInstallDir.KDE_PREFIX
                if kde_install_dir != self.install_dir:
                    self.configure_args.extend(["-additional-datadir", kde_install_dir / "share"])
                # 5.15 hard-codes QMAKE_APPLE_DEVICE_ARCHS as x86_64
                apple_arch = self.target_info.target_triple.split("-")[0]
                self.configure_args.append("QMAKE_APPLE_DEVICE_ARCHS=" + apple_arch)
        else:
            # make sure we use libc++ (only happens with mips64-unknown-freebsd10 and greater)
            compiler_flags = self.default_compiler_flags
            linker_flags = [*self.default_ldflags, "-target", self.target_info.target_triple]

            # The build system already passes these:
            linker_flags = filter(lambda s: not s.startswith("--sysroot"), linker_flags)
            compiler_flags = filter(lambda s: not s.startswith("--sysroot"), compiler_flags)
            cross_tools_prefix = self.target_info.get_target_triple(include_version=False)
            self.configure_args.extend(
                [
                    "-device",
                    "freebsd-generic-clang",
                    "-device-option",
                    f"CROSS_COMPILE={self.sdk_bindir}/{cross_tools_prefix}-",
                    "-device-option",
                    "COMPILER_FLAGS=" + self.commandline_to_str(compiler_flags),
                    "-device-option",
                    "LINKER_FLAGS=" + self.commandline_to_str(linker_flags),
                    "-sysroot",
                    self.cross_sysroot_path,
                    "-prefix",
                    self.install_prefix,
                    # The prefix for host tools such as qmake
                    "-hostprefix",
                    str(self.qt_host_tools_path),
                ]
            )
            xcb = BuildLibXCB.get_instance(self)
            if xcb.install_prefix != self.install_prefix:
                self.configure_args.append(
                    "QMAKE_RPATHDIR=" + str(xcb.install_prefix / self.target_info.default_libdir),
                )
            # Use the libpng/libjpeg versions with CHERI fixes.
            self.configure_args.append("-system-libpng")
            self.configure_args.append("-system-libjpeg")
            # Same for SQLite (otherwise some of the tests end up crashing)
            self.configure_args.append("-system-sqlite")

        if self.use_asan:
            self.configure_args.extend(["-sanitize", "address", "-sanitize", "undefined"])

        self.configure_args.extend(
            [
                (
                    # To ensure the host and cross-compiled version is the same also disable opengl
                    "-opengl" if self.use_opengl else "-no-opengl"
                ),
                # Since the cross-compiled version doesn't have glib, also disable it for the native on
                "-no-glib",
                # Needed for webkit:
                # "-icu",
                # "-no-Werror",
                "-no-use-gold-linker",
                "-no-iconv",
                "-no-headersclean",
                # Don't embed the mimetype DB in libQt5Core.so. It's huge and results in lots XML parsing.
                # Instead, we just ensure that the binary cache exists in the disk image.
                "-no-mimetype-database",
            ]
        )
        if self.build_tests:
            self.configure_args.append("-developer-build")
            if self.target_info.is_macos():
                # Otherwise we get "ERROR: debug-only framework builds are not supported. Configure with -no-framework
                # if you want a pure debug build."
                self.configure_args.append("-no-framework")

        else:
            self.configure_args.extend(["-nomake", "tests"])

        if not self.compiling_for_host():
            self.configure_args.extend(["-compile-examples"])
        self.configure_args.extend(["-nomake", "examples"])

        # TODO: once we update to qt 5.12 add this:
        if self.build_type == BuildType.DEBUG:
            self.configure_args.append("-debug")
            # optimize-debug needs GCC
            # self.configure_args.append("-optimize-debug")
        else:
            assert self.build_type in (
                BuildType.RELWITHDEBINFO,
                BuildType.MINSIZERELWITHDEBINFO,
                BuildType.MINSIZEREL,
                BuildType.RELEASE,
            )
            self.configure_args.append("-release")
            if self.build_type in (BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO):
                self.configure_args.append("-force-debug-info")
            if self.build_type in (BuildType.MINSIZEREL, BuildType.MINSIZERELWITHDEBINFO):
                self.configure_args.append("-optimize-size")  # Use -Os, otherwise it will use -O3

        if self.assertions:
            self.configure_args.append("-force-asserts")
            # configure only accepts this for gcc: self.configure_args.append("-gdb-index")

        if self.build_type.should_include_debug_info and False:
            # separate debug info reduces the size of the shared libraries, but GDB doesn't seem to pick it up
            # automatically (probably not installed to the right directory?) so disable it for now.
            self.configure_args.append("-separate-debug-info")

        # PCH often results in build failures if some of the sysroot headers changed since it appears to be missing
        # some required depedencies. Also the build speedup is not that significant so just disable it.
        self.configure_args.append("-no-pch")  # slows down build but gives useful crash testcases

        #  -reduce-exports ...... Reduce amount of exported symbols [auto]
        self.configure_args.append("-reduce-exports")
        # -reduce-relocations .. Reduce amount of relocations [auto] (Unix only)
        # TODO: this needs PIE:
        # self.configure_args.append("-reduce-relocations")

        if self.minimal:
            self.configure_args.extend(
                [
                    "-no-widgets",
                    "-no-glib",
                    "-no-gtk",
                    "-no-opengl",
                    "-no-cups",
                    "-no-syslog",
                    "-no-gui",
                    "-no-iconv",
                ]
            )
        else:
            self.configure_args.append("-dbus")  # we want to build QtDBus
            if not self.target_info.is_macos():
                self.configure_args.append("-dbus-linked")  # link libdbus directly
            # Enable X11 support when cross-compiling by default
            if self.use_x11:
                self.configure_args.extend(["-xcb", "-xkbcommon", "-xcb-xlib"])
                # Note: all X11 libraries are installed into the same directory
                self.configure_args.append("-L" + str(BuildLibXCB.get_install_dir(self) / "lib"))
                self.configure_args.append("-I" + str(BuildLibXCB.get_install_dir(self) / "include"))
        if self.use_ccache:
            self.configure_args.append("-ccache")
        self.configure_args.extend(["-opensource", "-confirm-license"])

        self.delete_file(self.build_dir / "config.cache")
        self.delete_file(self.build_dir / "config.opt")
        self.delete_file(self.build_dir / "config.status")
        super().configure()

    def needs_configure(self):
        return not (self.build_dir / "Makefile").exists()


class BuildQtBaseDev(CrossCompileCMakeProject):
    default_directory_basename = "qtbase"
    target = "qtbase-dev"
    repository = GitRepository("https://github.com/CTSRD-CHERI/qtbase", default_branch="dev-cheri", force_branch=True)
    is_large_source_repository = True
    default_source_dir = default_source_dir_in_subdir(Path("qt5"))
    # native_install_dir = DefaultInstallDir.CHERI_SDK
    # default_build_type = BuildType.MINSIZERELWITHDEBINFO  # Default to -Os with debug info:
    default_build_type = BuildType.RELWITHDEBINFO
    needs_native_build_for_crosscompile = True
    build_tests = BoolConfigOption("build-tests", default=True, show_help=True, help="build the Qt unit tests")
    build_examples: bool = BoolConfigOption("build-examples", show_help=True, help="build the Qt examples")
    assertions: bool = BoolConfigOption("assertions", default=True, show_help=True, help="Include assertions")
    gui: bool = BoolConfigOption("gui", show_help=True, default=True, help="Include QtGui")
    use_opengl: bool = BoolConfigOption("opengl", show_help=True, default=False, help="Include QtOpenGl")
    minimal: bool = BoolConfigOption(
        "minimal",
        show_help=True,
        default=True,
        help="Don't build QtWidgets or QtSql, etc",
    )

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        deps = list(super().dependencies(config))
        rootfs_target = cls.get_crosscompile_target().get_rootfs_target()
        deps.append(BuildSharedMimeInfo.get_class_for_target(rootfs_target).target)
        if cls.gui:
            # The system X11 libraries might be too old, so add the cheribuild-provided ones as a dependency
            deps.extend(
                [
                    "libx11",
                    "libxkbcommon",
                    "libinput",
                    "libxcb",
                    "libxcb-cursor",
                    "libxcb-util",
                    "libxcb-image",
                    "libice",
                    "libsm",
                    "libxext",
                    "libxtst",
                    "libxcb-render-util",
                    "libxcb-wm",
                    "libxcb-keysyms",
                ]
            )
        # Always use our patched image/sql libraries instead of the host ones:
        deps.extend(["libpng", "libjpeg-turbo"])
        if not cls.get_crosscompile_target().is_native():
            # We can only depend on fonts when installing to a rootfs, as those need to be installed to a directory
            # that is only writable by root.
            deps.extend([InstallDejaVuFonts.get_class_for_target(rootfs_target).target])
        if cls.use_opengl:
            deps.extend(["libglvnd", "libdrm"])
        if cls.minimal:
            return tuple(deps)
        deps.append("sqlite")
        # For non-macOS we need additional libraries for GUI and openGL parts.
        if not cls.get_crosscompile_target().target_info_cls.is_macos():
            deps.extend(["dbus", "fontconfig"])
        return tuple(deps)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_minimum_cmake_version(3, 18)

    def process(self):
        if not self.compiling_for_host() and not (self.host_target.install_dir / "libexec/moc").exists():
            self.fatal(
                "Missing host build moc tool",
                self.host_target.install_dir / "libexec/moc",
                " (needed for cross-compiling)",
                fixit_hint="Run `cheribuild.py " + self.target + "-native`",
            )
        super().process()

    def setup(self):
        super().setup()
        # noinspection PyAttributeOutsideInit
        self.host_target = self.get_instance(self, cross_target=CompilationTargets.NATIVE)
        compiler_info = self.get_compiler_info(self.CC)
        if compiler_info.is_clang and not compiler_info.is_apple_clang and compiler_info.version > (10, 0):
            self.add_cmake_options(WARNINGS_ARE_ERRORS=False)  # -Werror,-Wunused-private-field

        if self.force_static_linkage:
            self.add_cmake_options(BUILD_SHARED_LIBS=False)

        if not self.compiling_for_host():
            assert self.target_info.is_freebsd(), "No other targets supported yet"
            self.add_cmake_options(QT_HOST_PATH=self.host_target.install_dir, QT_QMAKE_TARGET_MKSPEC="freebsd-clang")
        if self.compiling_for_cheri():
            # Not ported to CHERI purecap
            self.add_cmake_options(PCRE2_DISABLE_JIT="ON")
            # TODO: investigate: ms->window.base becomes invalid
            self.add_cmake_options(QT_FEATURE_zstd="OFF")

        # Debug info makes libraries massive and makes running tests from SMBFS really slow
        self.add_cmake_options(QT_FEATURE_separate_debug_info="ON")
        # Enable --gdb-index to make debugging less painfully slow
        if self.can_use_lld(self.CC):
            self.add_cmake_options(QT_FEATURE_enable_gdb_index="ON", INPUT_linker="lld")
        if self.gui:
            self.add_cmake_options(
                QT_FEATURE_gui="ON",
                INPUT_libpng="system",
                INPUT_freetype="system",
                INPUT_pcre="system",
                FEATURE_xcb="ON",
                FEATURE_xcb_xlib="ON",
            )
        else:
            self.add_cmake_options(QT_FEATURE_png="OFF", QT_FEATURE_freetype="OFF")

        # Disable most libraries for now (we only test qtcore and qtgui)
        if self.minimal:
            self.add_cmake_options(
                QT_FEATURE_sql="OFF",
                QT_FEATURE_network="OFF",
                # QT_FEATURE_xml="OFF",  disabling this breaks the build
                QT_FEATURE_dbus="OFF",
                QT_FEATURE_widgets="OFF",
            )
        if not self.use_opengl:
            # Disable OpenGL and Widgets libraries
            self.add_cmake_options(QT_FEATURE_opengl="OFF", INPUT_opengl="no")
        # if not self.compiling_for_host():
        # Seems to break the build
        #    self.add_cmake_options(FEATURE_use_lld_linker=True)
        # disable Werror? WARNINGS_ARE_ERRORS=False
        # TODO: Still needed? "-no-evdev"
        # TODO: require ICU "-icu",
        # TODO: "-no-iconv"
        if self.target_info.is_macos():
            self.add_cmake_options(QT_FEATURE_icu="OFF")  # Not linked correctly -> tests fail to run
            self.add_cmake_options(CMAKE_PREFIX_PATH="/usr/local")  # Find homebrew libraries
        if self.build_tests:
            self.add_cmake_options(FEATURE_developer_build="ON")
            if self.target_info.is_macos() and self.build_type.is_debug:
                # Otherwise we get "ERROR: debug-only framework builds are not supported. Configure with -no-framework
                # if you want a pure debug build."
                self.add_cmake_options(FEATURE_framework="OFF")
        else:
            self.add_cmake_options(BUILD_TESTING=False)
        self.add_cmake_options(BUILD_EXAMPLES=self.build_examples)

        if self.assertions:
            self.add_cmake_options(FEATURE_force_asserts="ON")
        self.add_cmake_options(BUILD_WITH_PCH=False)  # slows down build but gives useful crash testcases
        # QT_FEATURE_reduce_relocations

    def run_tests(self):
        if self.compiling_for_host():
            # TODO: ctest -V?
            self.run_make("test", cwd=self.build_dir)
        else:
            # TODO: run `ctest --show-only=json-v1` to get list of tests
            self.target_info.run_cheribsd_test_script(
                "run_qtbase_tests.py",
                use_benchmark_kernel_by_default=False,
                mount_sysroot=True,
                mount_sourcedir=True,
            )


class BuildQt5(BuildQtWithConfigureScript):
    repository = GitRepository("https://github.com/CTSRD-CHERI/qt5", default_branch="5.10", force_branch=True)
    skip_git_submodules = True  # init-repository does it for us

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.all_modules = cls.add_bool_option(
            "all-modules",
            show_help=True,
            help="Build all modules (even those that don't make sense for CHERI)",
        )

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
    repository = GitRepository("https://github.com/CTSRD-CHERI/qtbase", default_branch="5.15", force_branch=True)
    is_large_source_repository = True
    can_run_parallel_install = True
    default_source_dir = default_source_dir_in_subdir(Path("qt5"))
    _installed_examples = ("examples/corelib/mimetypes", "examples/widgets/widgets/tetrix")

    def compile(self, **kwargs):
        # Some directories in the build system are broken and pick headers installed to the sysroot.
        # To avoid this problem we remove the conflicting headers first
        # We use the syncqt modules list to clean all of them:
        modules_cmd = self.run_cmd(
            "perl",
            "-e",
            """
my $syncprofile = "./sync.profile";
unless ($result = do "$syncprofile") {
    die "couldn't parse $syncprofile: $@" if $@;
}
if (! %modules) {
    die "couldn't parse $syncprofile";
}
for my $module (keys %modules) {
    print "$module\n";
}""",
            capture_output=True,
            run_in_pretend_mode=self.source_dir.exists() and shutil.which("perl"),
            cwd=self.source_dir,
        )
        if not modules_cmd.stdout.strip():
            self.fatal("Coulnd't parse list of Qt Modules")
        for module in modules_cmd.stdout.decode("utf-8").strip().split():
            self.clean_directory(self.real_install_root_dir / "include" / module, ensure_dir_exists=False)
        # Now we can build without risking stale headers breaking the build
        self.run_make("sub-src-all")
        # Tests are build as part of --test
        # Build some examples (e.g. tetris demo and mimetype browser)
        for example in self._installed_examples:
            self.makedirs(self.build_dir / example)
            self.run_cmd(
                self.build_dir / "bin/qmake",
                "-o",
                "Makefile",
                self.source_dir / example / Path(example + ".pro").name,
                cwd=self.build_dir / example,
            )
            self.run_make(cwd=self.build_dir / example)

    def install(self, **kwargs):
        super().install()
        for example in self._installed_examples:
            self.run_make_install(cwd=self.build_dir / example)
        if self.target_info.is_freebsd() and not self.compiling_for_host():
            # When not using fontconfig, the Qt platformsupport/fontdatabases/freetype/qfreetypefontdatabase.cpp code
            # expects all .ttf files to be directly below QT_QPA_FONTDIR (by default <Qt install dir>/lib/fonts), so we
            # need to add a symlink here (and ensure that the fonts are directly inside that dir).
            qt_fonts_dir = self.install_dir / "lib/fonts"
            # remove old directories to replace them with a symlink
            self.clean_directory(qt_fonts_dir, ensure_dir_exists=False)
            self.makedirs(qt_fonts_dir.parent)
            dejavu_fonts = InstallDejaVuFonts.get_instance(
                self,
                cross_target=self.crosscompile_target.get_rootfs_target(),
            )
            self.create_symlink(dejavu_fonts.fonts_dir, qt_fonts_dir, print_verbose_only=False)

    def _compile_relevant_tests(self):
        # generate the makefiles
        self.run_make("sub-tests-qmake_all")
        self.run_make("sub-corelib", cwd=self.build_dir / "tests/auto")
        self.run_make("sub-testlib", cwd=self.build_dir / "tests/auto")

    def run_tests(self):
        # Download the input files for the QMimeDatabase tests
        mimedb_tests_dir = self.build_dir / "tests/auto/corelib/mimetypes/qmimedatabase"
        # Only download the file once if possible:
        self.download_file(
            self.config.build_root / "shared-mime-info-2.1.zip",
            "https://gitlab.freedesktop.org/xdg/shared-mime-info/-/archive/2.1/shared-mime-info-2.1.zip",
            sha256="ce16a44d70b683deb8a82b7203970b6f474f794c91fc4b103c6d8cf6c3c796fc",
        )
        self.makedirs(mimedb_tests_dir)
        if not (mimedb_tests_dir / "s-m-i/data").exists():
            # Delete any old files
            self.clean_directory(mimedb_tests_dir / "shared-mime-info-2.1")
            self.run_cmd(
                "unzip",
                self.config.build_root / "shared-mime-info-2.1.zip",
                cwd=mimedb_tests_dir,
                print_verbose_only=False,
            )
            self.create_symlink(
                mimedb_tests_dir / "shared-mime-info-2.1",
                mimedb_tests_dir / "s-m-i",
                print_verbose_only=False,
            )
        self._compile_relevant_tests()
        if self.compiling_for_host():
            # tst_QDate::startOfDay_endOfDay(epoch) is broken in BST (at least on macOS), use Europe/Oslo to match the
            # official CI.
            # Possibly similar to https://bugreports.qt.io/browse/QTBUG-87662
            with self.set_env(TZ="Europe/Oslo"):
                self.run_cmd("make", "check", cwd=self.build_dir)
        else:
            # We run tests using the full disk image since we want e.g. locales to be available.
            command = ["run_qtbase_tests.py"]
            if "--test-subset" not in " ".join(self.config.test_extra_args):
                command.append("--test-subset=corelib")
            self.target_info.run_cheribsd_test_script(
                *command,
                use_benchmark_kernel_by_default=True,
                mount_sysroot=False,
                mount_sourcedir=True,
                use_full_disk_image=True,
            )


# This class is used to build individual Qt Modules instead of using the qt5 project
class BuildQtModuleWithQMake(CrossCompileProject):
    native_install_dir = BuildQtBase.native_install_dir
    do_not_add_to_targets = True
    can_run_parallel_install = True
    dependencies = ("qtbase",)
    default_source_dir = default_source_dir_in_subdir(Path("qt5"))

    def setup(self):
        super().setup()
        # Avoid starting GUI windows with xcb/wayland while running tests. Without this many tests fail with:
        # qt.qpa.xcb: could not connect to display
        # qt.qpa.plugin: Could not load the Qt platform plugin "xcb" in "" even though it was found.
        self.make_args.set_env(QT_QPA_PLATFORM="offscreen")

    def configure(self, **kwargs):
        # Run the QtBase QMake to generate a makefile
        self.run_cmd(
            BuildQtBase.get_instance(self).qt_host_tools_path / "bin/qmake",
            self.source_dir,
            "--",
            *self.configure_args,
            cwd=self.build_dir,
            env=self.configure_environment,
        )

    def compile(self, **kwargs):
        self.run_make("sub-src")

    def run_tests(self):
        if self.compiling_for_host():
            self.run_make("check", cwd=self.build_dir)
        else:
            self.run_make("sub-tests-all")
            # We run tests using the full disk image since we want e.g. locales to be available.
            self.target_info.run_cheribsd_test_script(
                "run_qtbase_tests.py",
                use_benchmark_kernel_by_default=True,
                mount_sysroot=True,
                mount_sourcedir=True,
                use_full_disk_image=True,
            )


class BuildQtSVG(BuildQtModuleWithQMake):
    target = "qtsvg"
    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/qtsvg.git",
        old_urls=[b"https://code.qt.io/qt/qtsvg.git"],
        default_branch="5.15",
        force_branch=True,
    )


class BuildQtX11Extras(BuildQtModuleWithQMake):
    target = "qtx11extras"
    repository = GitRepository(
        "https://invent.kde.org/qt/qt/qtx11extras.git",
        old_urls=[b"https://code.qt.io/qt/qtx11extras.git"],
        default_branch="5.15",
        force_branch=True,
    )


class BuildQtMacExtras(BuildQtModuleWithQMake):
    target = "qtmacextras"
    repository = GitRepository(
        "https://invent.kde.org/qt/qt/qtmacextras.git",
        old_urls=[b"https://code.qt.io/qt/qtmacextras.git"],
        default_branch="5.15",
        force_branch=True,
    )


class BuildQtDeclarative(BuildQtModuleWithQMake):
    target = "qtdeclarative"
    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/qtdeclarative.git",
        default_branch="5.15",
        force_branch=True,
    )

    def setup(self):
        super().setup()
        self.configure_args.extend(
            [
                "-no-qml-debug",  # debugger not compatibale with CHERI purecap
                "-quick-designer",  # needed for quickcontrols2
            ]
        )


class BuildQtTools(BuildQtModuleWithQMake):
    target = "qttools"
    dependencies = ("qtbase",)
    repository = GitRepository(
        "https://invent.kde.org/qt/qt/qttools.git",
        old_urls=[b"https://code.qt.io/qt/qttools.git"],
        default_branch="kde/5.15",
        force_branch=True,
    )

    def setup(self):
        super().setup()
        # No need to build all the developer GUI tools, we only want programs that are
        # useful inside the disk image.
        self.configure_args.extend(
            [
                "-no-feature-assistant",
                # Some KDE programs/libraries install designer plugins, so for now we install it by default.
                # This avoids having to patch those projects to make the feature optional.
                # "-no-feature-designer",
                # kColorPicker (which is used by Gwenview needs linguist, so we build this too).
                # "-no-feature-linguist",
            ]
        )


class BuildQtWayland(BuildQtModuleWithQMake):
    target = "qtwayland"
    dependencies = ("qtbase", "wayland", "wayland-native", "libxcomposite")
    repository = GitRepository(
        "https://code.qt.io/qt/qtwayland.git",
        temporary_url_override="https://github.com/CTSRD-CHERI/qtwayland",
        url_override_reason="Needs a patch to build on FreeBSD",
        default_branch="5.15",
        force_branch=True,
    )

    def setup(self):
        super().setup()
        self.configure_args.extend(
            [
                "-feature-wayland-client",
                "-feature-wayland-server",
            ]
        )

    # def compile(self, **kwargs):
    #     self.run_make()

    def process(self):
        wayland_native = BuildWayland.get_instance(self, cross_target=CompilationTargets.NATIVE)
        # This build relies in wayland-scanner being found in $PATH
        with self.set_env(PATH=str(wayland_native.install_dir / "bin") + ":" + os.getenv("PATH")):
            super().process()


class BuildQtQuickControls2(BuildQtModuleWithQMake):
    target = "qtquickcontrols2"
    dependencies = ("qtdeclarative",)
    repository = GitRepository(
        "https://invent.kde.org/qt/qt/qtquickcontrols2.git",
        old_urls=[b"https://code.qt.io/qt/qtquickcontrols2.git"],
        default_branch="kde/5.15",
        force_branch=True,
    )

    def compile(self, **kwargs):
        self.run_make()


class BuildQtQuickControls(BuildQtModuleWithQMake):
    target = "qtquickcontrols"
    dependencies = ("qtdeclarative",)
    repository = GitRepository(
        "https://invent.kde.org/qt/qt/qtquickcontrols.git",
        old_urls=[b"https://code.qt.io/qt/qtquickcontrols.git"],
        default_branch="kde/5.15",
        force_branch=True,
    )

    def compile(self, **kwargs):
        self.run_make()


class BuildQtGraphicalEffects(BuildQtModuleWithQMake):
    target = "qtgraphicaleffects"
    dependencies = ("qtdeclarative",)
    # Depends on OpenGL to be useful, https://github.com/CTSRD-CHERI/qtgraphicaleffects allows compiling without OpenGL
    repository = GitRepository(
        "https://invent.kde.org/qt/qt/qtgraphicaleffects.git",
        old_urls=[
            b"https://github.com/CTSRD-CHERI/qtgraphicaleffects",
            b"https://code.qt.io/qt/qtgraphicaleffects.git",
        ],
        default_branch="kde/5.15",
        force_branch=True,
    )

    def compile(self, **kwargs):
        self.run_make()


class BuildQtMultimedia(BuildQtModuleWithQMake):
    target = "qtmultimedia"
    dependencies = ("qtbase", "qtdeclarative")
    repository = GitRepository(
        "https://invent.kde.org/qt/qt/qtmultimedia.git",
        default_branch="kde/5.15",
        force_branch=True,
    )

    def compile(self, **kwargs):
        self.run_make()


# Webkit needs ICU (and recommended for QtBase too):
class BuildICU4C(CrossCompileAutotoolsProject):
    # noinspection PyUnreachableCode
    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/icu.git",
        default_branch="maint/maint-70",
        force_branch=True,
        old_urls=[b"https://github.com/unicode-org/icu.git"],
    )
    default_directory_basename = "icu"
    target = "icu4c"
    build_dir_suffix = "4c"
    native_install_dir = DefaultInstallDir.CHERI_SDK
    make_kind = MakeCommandKind.GnuMake
    needs_native_build_for_crosscompile = True

    def setup(self):
        super().setup()
        self.configure_command = self.source_dir / "icu4c/source/configure"
        self.configure_args.extend(["--disable-plugins", "--disable-dyload", "--disable-tests", "--disable-samples"])
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

            if self.crosscompile_target.is_aarch64(include_purecap=True):
                # XXX: Morello hybrid gives relocation errors without this, add to purecap
                # as well for comparability
                self.COMMON_FLAGS.append("-fPIC")

    def process(self):
        if not self.compiling_for_host() and not (self.native_build_dir / "bin/icupkg").exists():
            self.fatal(
                "Missing host build directory",
                self.native_build_dir,
                " (needed for cross-compiling)",
                fixit_hint="Run `cheribuild.py " + self.target + "-native`",
            )
        super().process()


# it also needs libxml2
class BuildLibXml2(CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/libxml2")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def setup(self):
        super().setup()
        # TODO: could enable these for the host version
        self.add_cmake_options(LIBXML2_WITH_PYTHON=False, LIBXML2_WITH_LZMA=False)
        self.add_cmake_options(LIBXML2_WITH_MODULES=not self.force_static_linkage)
        self.add_cmake_options(BUILD_SHARED_LIBS=not self.force_static_linkage)


class BuildLibXslt(CrossCompileCMakeProject):
    repository = GitRepository("https://gitlab.gnome.org/GNOME/libxslt.git")
    dependencies = ("libxml2",)
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def setup(self):
        super().setup()
        self.add_cmake_options(BUILD_SHARED_LIBS=not self.force_static_linkage)
        self.add_cmake_options(LIBXSLT_WITH_PYTHON=False)  # libxml2 built without python


class BuildQtWebkit(CrossCompileCMakeProject):
    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/qtwebkit",
        default_branch="qtwebkit-5.212-cheri",
        force_branch=True,
    )
    is_large_source_repository = True
    dependencies = ("qtbase", "icu4c", "libxml2", "sqlite")
    # webkit is massive if we include debug info
    default_build_type = BuildType.RELWITHDEBINFO
    native_install_dir = DefaultInstallDir.CHERI_SDK
    default_source_dir = default_source_dir_in_subdir(Path("qt5"))

    @property
    def llvm_binutils_dir(self) -> Path:
        if self.compiling_for_host():
            return self.config.cheri_sdk_bindir  # Use the CHERI SDK for native
        return self.target_info.sdk_root_dir / "bin"

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("ruby", apt="ruby")
        self.check_required_system_tool("gperf", homebrew="gperf", apt="gperf")

    def setup(self):
        super().setup()
        self.cross_warning_flags += [
            "-Wno-error",
            "-Wno-error=cheri-bitwise-operations",
            "-Wno-error=cheri-capability-misuse",
            "-Wno-error=format",
        ]  # FIXME: build with capability -Werror
        # We are building an old version of webkit
        self.cross_warning_flags.append("-Wno-deprecated-copy")
        if self.should_include_debug_info:
            self.COMMON_FLAGS.append("-gline-tables-only")  # otherwise too much debug info
        self.add_cmake_options(
            PORT="Qt",
            ENABLE_X11_TARGET=False,
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
        self.add_cmake_options(
            ENABLE_GEOLOCATION=False,  # needs QtPositioning
            ENABLE_PRINT_SUPPORT=False,  # needs QtPrintSupport
            ENABLE_DEVICE_ORIENTATION=False,  # needs QtSensors
            ENABLE_WEBKIT2=False,  # needs QtQuick
        )
        # Use llvm-{ar,ranlib} because elftoolchain's versions truncate libWebCore.a
        self.add_cmake_options(CMAKE_AR=self.llvm_binutils_dir / "llvm-ar")
        self.add_cmake_options(CMAKE_RANLIB=self.llvm_binutils_dir / "llvm-ranlib")
        self.add_cmake_options(
            ENABLE_JIT=False,  # Not supported on MIPS
            QT_STATIC_BUILD=True,  # we always build qt static for now
            QT_BUNDLED_PNG=True,  # use libpng from Qt
            # QT_BUNDLED_JPEG=True,  # use libjpeg from Qt
            QTWEBKIT_LINK_STATIC_ONLY=self.force_static_linkage,
        )
        if not self.compiling_for_host():
            # we need to find the installed Qt
            self.add_cmake_options(
                Qt5_DIR=self.cross_sysroot_path / ("usr/local/" + self._xtarget.generic_arch_suffix) / "lib/cmake/Qt5",
            )
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

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_jsc_only = cls.add_bool_option(
            "build-jsc-only",
            show_help=True,
            help="only build the JavaScript interpreter executable",
        )

    def compile(self, **kwargs):
        # Generate the shared mime info cache to MASSIVELY speed up tests
        with tempfile.TemporaryDirectory(prefix="cheribuild-" + self.target + "-") as td:
            update_mime = BuildSharedMimeInfo.get_update_mime_database_path(self)
            mime_info_src = BuildQtBase.get_source_dir(self) / "src/corelib/mimetypes/mime/packages/freedesktop.org.xml"
            self.install_file(
                mime_info_src,
                Path(td, "mime/packages/freedesktop.org.xml"),
                force=True,
                print_verbose_only=False,
            )
            self.run_cmd(update_mime, "-V", Path(td, "mime"), cwd="/")

            if not Path(td, "mime/mime.cache").exists():
                self.fatal("Could not generated shared-mime-info cache!")
            # install mime.cache and freedesktop.org.xml into the build dir for tests
            self.install_file(
                mime_info_src,
                self.build_dir / "freedesktop.org.xml",
                force=True,
                print_verbose_only=False,
            )
            self.install_file(
                Path(td, "mime/mime.cache"),
                self.build_dir / "mime.cache",
                force=True,
                print_verbose_only=False,
            )
            # TODO: get https://github.com/annulen/webkit-test-fonts to run the full testsuite
        if self.build_jsc_only:
            self.run_make("jsc")
        else:
            self.run_make("all")

    def install(self, **kwargs):
        # create a stripped version of DumpRenderTree and jsc since the one with debug info is too big
        if not self.build_jsc_only:
            dump_render_tree = self.build_dir / "bin/DumpRenderTree"
            self.maybe_strip_elf_file(
                dump_render_tree,
                output_path=dump_render_tree.with_suffix(".stripped"),
                print_verbose_only=False,
            )
        jsc = self.build_dir / "bin/jsc"
        self.maybe_strip_elf_file(jsc, output_path=jsc.with_suffix(".stripped"), print_verbose_only=False)
        self.info("Not installing qtwebit since it uses too much space. If you really want this run `ninja install`")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("Running host tests not implemented")
        else:
            self.target_info.run_cheribsd_test_script(
                "run_qtwebkit_tests.py",
                use_benchmark_kernel_by_default=True,
                mount_builddir=True,
                mount_sourcedir=True,
                mount_sysroot=True,
            )
