#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

import os

from .crosscompileproject import CrossCompileAutotoolsProject, CrossCompileCMakeProject
from .qt5 import BuildQtBase
from .x11 import BuildLibXCB
from ..project import DefaultInstallDir, GitRepository, MakeCommandKind
from ...config.chericonfig import BuildType
from ...config.compilation_targets import CompilationTargets
from ...config.loader import ComputedDefaultValue
from ...processutils import set_env
from ...utils import OSInfo


class KDECMakeProject(CrossCompileCMakeProject):
    do_not_add_to_targets = True
    default_install_dir = DefaultInstallDir.KDE_PREFIX
    default_build_type = BuildType.RELWITHDEBINFO
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS
    # Group all the frameworks source directories together
    default_source_dir = ComputedDefaultValue(
        function=lambda config, project: config.source_root / "kde-frameworks" / project.default_directory_basename,
        as_string=lambda cls: "$SOURCE_ROOT/kde-frameworks" + cls.default_directory_basename)

    ctest_needs_full_disk_image = False  # default to running with the full disk image
    # Prefer the libraries in the build directory over the installed ones. This is needed when RPATH is not set
    # correctly, i.e. when built with CMake+Ninja on macOS with a version where
    # https://gitlab.kitware.com/cmake/cmake/-/merge_requests/6240 is not included.
    ctest_script_extra_args = ("--extra-library-path", "/build/bin", "--extra-library-path", "/build/lib")
    dependencies = ["qtbase", "extra-cmake-modules"]
    _has_qt_designer_plugin = False

    def setup(self):
        super().setup()
        if self.target_info.is_macos():
            self.add_cmake_options(APPLE_SUPPRESS_X11_WARNING=True)
        # Skip the QtDesigner plugin for now, it won't be particularly useful
        if self._has_qt_designer_plugin:
            self.add_cmake_options(BUILD_DESIGNERPLUGIN=False)
        if not self.compiling_for_host():
            # We need native tools (e.g. desktoptojson/kconfig_compiler) for some projects
            native_install_root = BuildKConfig.get_install_dir(self, cross_target=CompilationTargets.NATIVE)
            self.add_cmake_options(KF5_HOST_TOOLING=native_install_root / "lib/cmake")
            if "qtx11extras" in self.dependencies:
                self.warning("Adding include path as workaround for broken QtX11Extras")
                self.COMMON_FLAGS.append("-I" + str(BuildLibXCB.get_install_dir(self) / "include"))

    @property
    def cmake_prefix_paths(self):
        return [self.install_dir, BuildQtBase.get_install_dir(self)] + super().cmake_prefix_paths


# TODO: should generate the dependency graph from
#  https://invent.kde.org/sysadmin/repo-metadata/-/blob/master/dependencies/dependency-data-kf5-qt5
class BuildExtraCMakeModules(KDECMakeProject):
    target = "extra-cmake-modules"
    dependencies = []
    repository = GitRepository("https://invent.kde.org/frameworks/extra-cmake-modules.git")


class BuildPhonon(KDECMakeProject):
    target = "phonon"
    repository = GitRepository("https://invent.kde.org/libraries/phonon.git")


class BuildGettext(CrossCompileAutotoolsProject):
    target = "gettext"
    repository = GitRepository("https://git.savannah.gnu.org/git/gettext.git")
    make_kind = MakeCommandKind.GnuMake

    def setup(self):
        super().setup()
        self.configure_args.extend([
            "--enable-relocatable",
            "--disable-csharp",
            "--disable-java",
            "--disable-libasprintf",
            "--disable-openmp",
            "--without-emacs",
            "--with-included-gettext",
            "ac_cv_lib_rt_sched_yield=no"
        ])

    def configure(self, **kwargs):
        # gettext-runtime/intl
        if not (self.source_dir / "configure").exists():
            self.run_cmd(self.source_dir / "autogen.sh", cwd=self.source_dir)
        super().configure()

    def clean(self):
        if not (self.source_dir / "Makefile").exists():
            return None
        self.run_make("distclean", cwd=self.source_dir)

    def compile(self, **kwargs):
        self.run_make("all", cwd=self.build_dir / "gettext-runtime/intl")

    def install(self, **kwargs):
        self.run_make_install(cwd=self.build_dir / "gettext-runtime/intl")

    def process(self):
        new_env = dict()
        if OSInfo.IS_MAC:
            # /usr/bin/bison and /usr/bin/sed on macOS are not compatible with this build system
            new_env["PATH"] = ":".join([str(self.get_homebrew_prefix("gnu-sed") / "libexec/gnubin"),
                                        str(self.get_homebrew_prefix("bison") / "bin"),
                                        os.getenv("PATH")])
        with set_env(**new_env):
            super().process()


#
# Frameworks, tier1
#
# frameworks/syntax-highlighting: third-party/taglib
# frameworks/kwayland: kdesupport/plasma-wayland-protocols
# class BuildBreezeIcons(KDECMakeProject):
#     target = "breeze-icons"
#     repository = GitRepository("https://invent.kde.org/frameworks/breeze-icons.git")
class BuildKArchive(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/karchive.git")


class BuildKCodecs(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kcodecs.git")


class BuildKCoreAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kcoreaddons.git")


class BuildKConfig(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kconfig.git")


class BuildKDBusAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kdbusaddons.git")
    dependencies = KDECMakeProject.dependencies + ["qtx11extras"]


class BuildKGuiAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kguiaddons.git")
    dependencies = KDECMakeProject.dependencies + ["qtx11extras"]

    def setup(self):
        super().setup()
        # TODO: wayland support
        self.add_cmake_options(WITH_WAYLAND=False)


class BuildKItemViews(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kitemviews.git")
    _has_qt_designer_plugin = True


class BuildKI18N(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/ki18n.git")
    dependencies = KDECMakeProject.dependencies + ["gettext"]


class BuildKWidgetsAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kwidgetsaddons.git")
    _has_qt_designer_plugin = True


class BuildKWindowSystem(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kwindowsystem.git")
    dependencies = KDECMakeProject.dependencies + ["qtx11extras", "libxfixes"]


class BuildSolid(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/solid.git")

    def setup(self):
        super().setup()
        if OSInfo.IS_MAC:
            # /usr/bin/bison on macOS is too old
            self.add_cmake_options(BISON_EXECUTABLE=self.get_homebrew_prefix("bison") / "bin/bison")


class BuildSonnet(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/sonnet.git")
    # TODO: should probably install a spell checker:
    # -- The following OPTIONAL packages have not been found:
    # * ASPELL, Spell checking support via Aspell, <http://aspell.net/>
    # * HSPELL, Spell checking support for Hebrew, <http://ivrix.org.il/projects/spell-checker/>
    # * HUNSPELL, Spell checking support via Hunspell, <http://hunspell.sourceforge.net/>
    # * VOIKKO, Spell checking support via Voikko, <http://voikko.puimula.org/>
    _has_qt_designer_plugin = True

#
# Frameworks, tier2
#


class BuildKAuth(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kauth.git")
    dependencies = ["kcoreaddons"]  # optional: "polkit-qt-1"


class BuildKCompletion(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kcompletion.git")
    dependencies = ["kconfig", "kconfig-native", "kwidgetsaddons"]
    _has_qt_designer_plugin = True


class BuildKCrash(KDECMakeProject):
    dependencies = ["kcoreaddons", "kcoreaddons-native", "qtx11extras", "kwindowsystem"]
    repository = GitRepository("https://invent.kde.org/frameworks/kcrash.git")


class BuildKJobWidgets(KDECMakeProject):
    dependencies = ["kcoreaddons", "kcoreaddons-native", "kwidgetsaddons", "qtx11extras"]
    repository = GitRepository("https://invent.kde.org/frameworks/kjobwidgets.git")


# class BuildKDocTools(KDECMakeProject):
#     dependencies = ["karchive", "ki18n"]
#     repository = GitRepository("https://invent.kde.org/frameworks/kdoctools.git")


class BuildKNotifications(KDECMakeProject):
    # frameworks/knotifications: third-party/libdbusmenu-qt
    dependencies = ["kwindowsystem", "kconfig", "kconfig-native", "kcoreaddons", "kcoreaddons-native", "qtx11extras",
                    "phonon"]
    repository = GitRepository("https://invent.kde.org/frameworks/knotifications.git")


class BuildKPackage(KDECMakeProject):
    dependencies = ["karchive", "ki18n", "kcoreaddons", "kcoreaddons-native"]
    repository = GitRepository("https://invent.kde.org/frameworks/kpackage.git")



#
# Frameworks, tier3
#
class BuildKBookmarks(KDECMakeProject):
    dependencies = ["kconfigwidgets", "kcodecs", "kiconthemes", "kxmlgui"]
    repository = GitRepository("https://invent.kde.org/frameworks/kbookmarks.git")



class BuildKConfigWidgets(KDECMakeProject):
    dependencies = ["kauth", "kcoreaddons", "kcodecs", "kconfig", "kguiaddons", "ki18n", "kwidgetsaddons",
                    "kconfig-native"]
    repository = GitRepository("https://invent.kde.org/frameworks/kconfigwidgets.git")
    _has_qt_designer_plugin = True


class BuildKService(KDECMakeProject):
    dependencies = ["kconfig", "kcoreaddons", "kcrash", "kdbusaddons", "ki18n",
                    "kcoreaddons-native",  # desktoptojson
                    "kconfig-native",  # kconfig_compiler
                    ]
    repository = GitRepository("https://invent.kde.org/frameworks/kservice.git")

    def __init__(self, config):
        super().__init__(config)
        self.add_required_system_tool("bison", apt="bison", homebrew="bison")
        self.add_required_system_tool("flex", apt="flex")

    def process(self):
        # TODO: add this as a generic helper function
        newpath = os.getenv("PATH")
        if OSInfo.IS_MAC:
            # FIXME: /Users/alex/cheri/output/rootfs-amd64/opt/amd64/kde/bin/desktoptojson
            # /usr/bin/bison on macOS is not compatible with this build system
            newpath = ":".join([str(self.get_homebrew_prefix("bison") / "bin"),
                                str(self.get_homebrew_prefix("flex") / "bin"),
                                newpath])
        with set_env(PATH=newpath):
            super().process()


class BuildKTextWidgets(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/ktextwidgets.git")
    dependencies = ["sonnet", "kcompletion", "kconfigwidgets", "kwidgetsaddons"]
    _has_qt_designer_plugin = True


class BuildKIconThemes(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kiconthemes.git")
    dependencies = ["kconfigwidgets", "kwidgetsaddons", "kitemviews", "karchive", "ki18n"]
    _has_qt_designer_plugin = True


class BuildKGlobalAccel(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kglobalaccel.git")
    dependencies = ["kconfig", "kconfig-native", "kcrash", "kdbusaddons", "kwindowsystem",
                    "qtx11extras", "libxcb"]


class BuildKXMLGUI(KDECMakeProject):
    # frameworks/kxmlgui: frameworks/kglobalaccel
    dependencies = ["kitemviews", "kconfig", "kconfig-native", "kglobalaccel",
                    "kconfigwidgets", "ki18n", "kiconthemes",
                    "ktextwidgets", "kwidgetsaddons", "kwindowsystem"]
    repository = GitRepository("https://invent.kde.org/frameworks/kxmlgui.git")
    _has_qt_designer_plugin = True



class BuildKIO(KDECMakeProject):
    dependencies = ["kauth", "kdbusaddons", "ki18n", "kguiaddons", "kconfigwidgets", "kitemviews", "kcoreaddons",
                    "kwidgetsaddons", "kservice", "karchive", "qtx11extras", "solid",
                    "kjobwidgets", "kiconthemes", "kwindowsystem", "kcrash", "kcompletion", "ktextwidgets",
                    "kxmlgui", "kbookmarks", "kconfig", "kconfig-native",
                    # optional: "kwallet", "knotifications", "kded"
                    ]
    repository = GitRepository("https://invent.kde.org/frameworks/kio.git")
    _has_qt_designer_plugin = True



class BuildDoplhin(KDECMakeProject):
    target = "dolphin"
    repository = GitRepository("https://invent.kde.org/system/dolphin.git")

# Lots of deps (including QtSVG)
# class BuildGwenview(KDECMakeProject):
#     target = "gwenview"
#     repository = GitRepository("https://invent.kde.org/graphics/gwenview.git")
