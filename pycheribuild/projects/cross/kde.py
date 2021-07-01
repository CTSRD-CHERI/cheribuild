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
    _needs_newer_bison = False

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
        if OSInfo.IS_MAC and self._needs_newer_bison:
            # /usr/bin/bison on macOS is too old
            self.add_cmake_options(BISON_EXECUTABLE=self.get_homebrew_prefix("bison") / "bin/bison")

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
class BuildBreezeIcons(KDECMakeProject):
    target = "breeze-icons"
    repository = GitRepository("https://invent.kde.org/frameworks/breeze-icons.git")


class BuildAttica(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/attica.git")


class BuildKArchive(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/karchive.git")


class BuildKCodecs(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kcodecs.git")


class BuildKCoreAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kcoreaddons.git")

    def setup(self):
        super().setup()
        # Install prefix.sh for KCoreAddons only (could do it for all projects but there is no point overwriting it)
        self.add_cmake_options(KDE_INSTALL_PREFIX_SCRIPT=True)


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


class BuildKItemModels(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kitemmodels.git")


class BuildKI18N(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/ki18n.git")
    dependencies = KDECMakeProject.dependencies + ["gettext"]

    def setup(self):
        super().setup()
        # Avoid QtQml dependency since we don't really care about translations right now
        self.add_cmake_options(BUILD_WITH_QML=False)


class BuildKWidgetsAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kwidgetsaddons.git")
    _has_qt_designer_plugin = True


class BuildKWindowSystem(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kwindowsystem.git")

    @classmethod
    def dependencies(cls, config):
        if cls.get_crosscompile_target(config).target_info_cls.is_macos():
            return super().dependencies
        return super().dependencies + ["qtx11extras", "libxfixes"]


class BuildSolid(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/solid.git")
    # XXX: https://foss.heptapod.net/bsdutils/bsdisks for the DBus API
    _needs_newer_bison = True


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
    repository = GitRepository("https://invent.kde.org/frameworks/knotifications.git")

    @classmethod
    def dependencies(cls, config):
        result = ["qtdeclarative", "kwindowsystem", "kconfig", "kconfig-native", "kcoreaddons", "kcoreaddons-native",
                  "phonon"]
        if cls.get_crosscompile_target(config).target_info_cls.is_macos():
            return result + ["qtmacextras"]
        return result + ["qtx11extras"]


class BuildKPackage(KDECMakeProject):
    dependencies = ["karchive", "ki18n", "kcoreaddons", "kcoreaddons-native"]
    repository = GitRepository("https://invent.kde.org/frameworks/kpackage.git")


class BuildKSyndication(KDECMakeProject):
    dependencies = ["kcodecs"]
    repository = GitRepository("https://invent.kde.org/frameworks/syndication.git")


# frameworks/kfilemetadata: frameworks/ki18n
# frameworks/kfilemetadata: frameworks/karchive
# frameworks/kfilemetadata: frameworks/kcoreaddons
# frameworks/kfilemetadata: third-party/taglib
# frameworks/kfilemetadata: third-party/poppler
# frameworks/kimageformats: frameworks/karchive
# frameworks/kpty: frameworks/kcoreaddons
# frameworks/kpty: frameworks/ki18n
# frameworks/kunitconversion: frameworks/ki18n
# frameworks/kunitconversion: frameworks/kconfig

# frameworks/qqc2-desktop-style: frameworks/kirigami
# frameworks/qqc2-desktop-style: frameworks/kiconthemes
# frameworks/qqc2-desktop-style: frameworks/kconfigwidgets


#
# Frameworks, tier3
#
class BuildKBookmarks(KDECMakeProject):
    dependencies = ["kconfigwidgets", "kcodecs", "kiconthemes", "kxmlgui"]
    repository = GitRepository("https://invent.kde.org/frameworks/kbookmarks.git")


class BuildKCMUtils(KDECMakeProject):
    dependencies = ["kitemviews", "kconfigwidgets", "kservice", "kxmlgui", "kdeclarative", "kauth"]
    repository = GitRepository("https://invent.kde.org/frameworks/kcmutils.git")


class BuildKConfigWidgets(KDECMakeProject):
    dependencies = ["kauth", "kcoreaddons", "kcodecs", "kconfig", "kguiaddons", "ki18n", "kwidgetsaddons",
                    "kconfig-native"]
    repository = GitRepository("https://invent.kde.org/frameworks/kconfigwidgets.git")
    _has_qt_designer_plugin = True


# frameworks/kdav: frameworks/kio
# frameworks/kdesignerplugin: frameworks/kcoreaddons
# frameworks/kdesignerplugin: frameworks/kconfig
# frameworks/kdesignerplugin: frameworks/kdoctools
# frameworks/kemoticons: frameworks/karchive
# frameworks/kemoticons: frameworks/kservice
# frameworks/kjs: frameworks/kdoctools
class BuildKNewStuff(KDECMakeProject):
    dependencies = ["attica", "kitemviews", "kiconthemes", "ktextwidgets", "kxmlgui",
                    "solid", "kio", "kbookmarks", "kpackage", "kpackage-native", "ksyndication"
                    ]  # TODO: kirigami
    repository = GitRepository("https://invent.kde.org/frameworks/knewstuff.git")
    _needs_newer_bison = True


class BuildKService(KDECMakeProject):
    dependencies = ["kconfig", "kcoreaddons", "kcrash", "kdbusaddons", "ki18n",
                    "kcoreaddons-native",  # desktoptojson
                    "kconfig-native",  # kconfig_compiler
                    ]
    repository = GitRepository("https://invent.kde.org/frameworks/kservice.git")
    _needs_newer_bison = True


class BuildKTextWidgets(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/ktextwidgets.git")
    dependencies = ["sonnet", "kcompletion", "kconfigwidgets", "kwidgetsaddons"]
    _has_qt_designer_plugin = True


class BuildKParts(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kparts.git")
    dependencies = ["kio", "kxmlgui", "ktextwidgets", "knotifications"]
    _has_qt_designer_plugin = True


class BuildKIconThemes(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kiconthemes.git")
    dependencies = ["kconfigwidgets", "kwidgetsaddons", "kitemviews", "karchive", "ki18n", "breeze-icons", "qtsvg"]
    _has_qt_designer_plugin = True


class BuildKGlobalAccel(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kglobalaccel.git")

    @classmethod
    def dependencies(cls, config):
        result = ["kconfig", "kconfig-native", "kcrash", "kdbusaddons", "kwindowsystem"]
        if not cls.get_crosscompile_target(config).target_info_cls.is_macos():
            result += ["qtx11extras", "libxcb"]
        return result


class BuildKXMLGUI(KDECMakeProject):
    dependencies = ["kitemviews", "kconfig", "kconfig-native", "kglobalaccel",
                    "kconfigwidgets", "ki18n", "kiconthemes",
                    "ktextwidgets", "kwidgetsaddons", "kwindowsystem"]
    repository = GitRepository("https://invent.kde.org/frameworks/kxmlgui.git")
    _has_qt_designer_plugin = True


class BuildKDeclarative(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kdeclarative.git")
    dependencies = ["kpackage", "kpackage-native", "kio", "kiconthemes", "knotifications", "qtdeclarative"]
    _has_qt_designer_plugin = True

    def setup(self):
        super().setup()
        # We build Qt wihtout OpenGL support, so we shouldn't build the OpenGL code.
        self.add_cmake_options(CMAKE_DISABLE_FIND_PACKAGE_epoxy=True)


# frameworks/kinit: frameworks/kservice
# frameworks/kinit: frameworks/kio
# frameworks/kinit: frameworks/ki18n
# frameworks/kinit: frameworks/kwindowsystem
# frameworks/kinit: frameworks/kcrash
# frameworks/kinit: frameworks/kdoctools
# frameworks/kjsembed: frameworks/kjs
# frameworks/kjsembed: frameworks/ki18n
# frameworks/kjsembed: frameworks/kdoctools
# frameworks/knotifyconfig: frameworks/kio
# frameworks/knotifyconfig: frameworks/ki18n
# frameworks/knotifyconfig: frameworks/knotifications #test dependency
# frameworks/kded: frameworks/ki18n
# frameworks/kded: frameworks/kcoreaddons
# frameworks/kded: frameworks/kdbusaddons
# frameworks/kded: frameworks/kservice
# frameworks/kded: frameworks/kwindowsystem
# frameworks/kded: frameworks/kcrash
# frameworks/kded: frameworks/kinit
# frameworks/kded: frameworks/kdoctools
# frameworks/kross: frameworks/ki18n
# frameworks/kross: frameworks/kiconthemes
# frameworks/kross: frameworks/kio
# frameworks/kross: frameworks/kparts
class BuildKIO(KDECMakeProject):
    dependencies = ["kauth", "kdbusaddons", "ki18n", "kguiaddons", "kconfigwidgets", "kitemviews", "kcoreaddons",
                    "kwidgetsaddons", "kservice", "karchive", "qtx11extras", "solid",
                    "kjobwidgets", "kiconthemes", "kwindowsystem", "kcrash", "kcompletion", "ktextwidgets",
                    "kxmlgui", "kbookmarks", "kconfig", "kconfig-native", "knotifications",
                    # optional: "kwallet", "kded"
                    ]
    repository = GitRepository("https://invent.kde.org/frameworks/kio.git")
    _has_qt_designer_plugin = True


# frameworks/kmediaplayer: frameworks/ki18n
# frameworks/kmediaplayer: frameworks/kparts
# frameworks/kmediaplayer: frameworks/kxmlgui
# frameworks/kdewebkit: frameworks/kcoreaddons
# frameworks/kdewebkit: frameworks/kwallet
# frameworks/kdewebkit: frameworks/kio
# frameworks/kdewebkit: frameworks/knotifications
# frameworks/kdewebkit: frameworks/kparts
# frameworks/kdesu: frameworks/kcoreaddons
# frameworks/kdesu: frameworks/kservice
# frameworks/kdesu: frameworks/kpty
# frameworks/ktexteditor: frameworks/karchive
# frameworks/ktexteditor: frameworks/kconfig
# frameworks/ktexteditor: frameworks/kguiaddons
# frameworks/ktexteditor: frameworks/ki18n
# frameworks/ktexteditor: frameworks/kjobwidgets
# frameworks/ktexteditor: frameworks/kio
# frameworks/ktexteditor: frameworks/kparts
# frameworks/ktexteditor: frameworks/sonnet
# frameworks/ktexteditor: frameworks/kxmlgui
# frameworks/ktexteditor: frameworks/syntax-highlighting
# frameworks/kwallet: frameworks/kconfig
# frameworks/kwallet: frameworks/kcoreaddons
# frameworks/kwallet: frameworks/kdbusaddons
# frameworks/kwallet: frameworks/kiconthemes
# frameworks/kwallet: frameworks/ki18n
# frameworks/kwallet: frameworks/knotifications
# frameworks/kwallet: frameworks/kservice
# frameworks/kwallet: frameworks/kwindowsystem
# frameworks/kwallet: frameworks/kwidgetsaddons
# frameworks/kwallet: third-party/gpgme
# frameworks/kactivities: frameworks/kconfig
# frameworks/kactivities: frameworks/kwindowsystem
# frameworks/kactivities: frameworks/kcoreaddons
# frameworks/kactivities: frameworks/kio
# frameworks/kactivities-stats: frameworks/kactivities
# frameworks/plasma-framework: frameworks/kactivities
# frameworks/plasma-framework: frameworks/karchive
# frameworks/plasma-framework: frameworks/kauth
# frameworks/plasma-framework: frameworks/kbookmarks
# frameworks/plasma-framework: frameworks/kcodecs
# frameworks/plasma-framework: frameworks/kcompletion
# frameworks/plasma-framework: frameworks/kconfig
# frameworks/plasma-framework: frameworks/kconfigwidgets
# frameworks/plasma-framework: frameworks/kcoreaddons
# frameworks/plasma-framework: frameworks/kcrash
# frameworks/plasma-framework: frameworks/kdbusaddons
# frameworks/plasma-framework: frameworks/kdeclarative
# frameworks/plasma-framework: frameworks/kdnssd
# frameworks/plasma-framework: frameworks/kglobalaccel
# frameworks/plasma-framework: frameworks/kguiaddons
# frameworks/plasma-framework: frameworks/ki18n
# frameworks/plasma-framework: frameworks/kiconthemes
# frameworks/plasma-framework: frameworks/kidletime
# frameworks/plasma-framework: frameworks/kitemmodels
# frameworks/plasma-framework: frameworks/kitemviews
# frameworks/plasma-framework: frameworks/kjobwidgets
# frameworks/plasma-framework: frameworks/kio
# frameworks/plasma-framework: frameworks/kross
# frameworks/plasma-framework: frameworks/knotifications
# frameworks/plasma-framework: frameworks/kparts
# frameworks/plasma-framework: frameworks/kpackage
# frameworks/plasma-framework: frameworks/kservice
# frameworks/plasma-framework: frameworks/solid
# frameworks/plasma-framework: frameworks/sonnet
# frameworks/plasma-framework: frameworks/ktextwidgets
# frameworks/plasma-framework: frameworks/threadweaver
# frameworks/plasma-framework: frameworks/kunitconversion
# frameworks/plasma-framework: frameworks/kwallet
# frameworks/plasma-framework: frameworks/kwayland
# frameworks/plasma-framework: frameworks/kwidgetsaddons
# frameworks/plasma-framework: frameworks/kwindowsystem
# frameworks/plasma-framework: frameworks/kxmlgui
# frameworks/plasma-framework: frameworks/ktexteditor
# frameworks/plasma-framework: frameworks/oxygen-icons5
# frameworks/plasma-framework: frameworks/kirigami
# frameworks/purpose: frameworks/kcoreaddons
# frameworks/purpose: frameworks/kconfig
# frameworks/purpose: frameworks/ki18n
# frameworks/purpose: frameworks/kio
# frameworks/purpose: frameworks/kirigami
# frameworks/kxmlrpcclient: frameworks/kio
# frameworks/kpeople: frameworks/kcoreaddons
# frameworks/kpeople: frameworks/kwidgetsaddons
# frameworks/kpeople: frameworks/ki18n
# frameworks/kpeople: frameworks/kitemviews
# frameworks/kcontacts: frameworks/kcoreaddons
# frameworks/kcontacts: frameworks/ki18n
# frameworks/kcontacts: frameworks/kconfig
# frameworks/kcontacts: frameworks/kcodecs
# frameworks/baloo: frameworks/kfilemetadata
# frameworks/baloo: frameworks/kcoreaddons
# frameworks/baloo: frameworks/kconfig
# frameworks/baloo: frameworks/kdbusaddons
# frameworks/baloo: frameworks/ki18n
# frameworks/baloo: frameworks/kidletime
# frameworks/baloo: frameworks/solid
# frameworks/baloo: frameworks/kcrash
# frameworks/baloo: frameworks/kio

class BuildDoplhin(KDECMakeProject):
    target = "dolphin"
    dependencies = ["kparts", "kxmlgui", "knewstuff", "kio", "kcmutils"]
    repository = GitRepository("https://invent.kde.org/system/dolphin.git")


class BuildGwenview(KDECMakeProject):
    target = "gwenview"
    dependencies = ["qtsvg", "kitemmodels", "kio", "kparts"]
    repository = GitRepository("https://invent.kde.org/graphics/gwenview.git")
