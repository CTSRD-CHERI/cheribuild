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

    def setup(self):
        super().setup()
        if self.target_info.is_macos():
            self.add_cmake_options(APPLE_SUPPRESS_X11_WARNING=True)

    @property
    def cmake_prefix_paths(self):
        return [self.install_dir, BuildQtBase.get_install_dir(self)] + super().cmake_prefix_paths


# TODO: should generate the dependency graph from
#  https://invent.kde.org/sysadmin/repo-metadata/-/blob/master/dependencies/dependency-data-kf5-qt5
class BuildExtraCMakeModules(KDECMakeProject):
    target = "extra-cmake-modules"
    dependencies = []
    repository = GitRepository("https://invent.kde.org/frameworks/extra-cmake-modules.git")


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


# Frameworks, tier1
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

    def setup(self):
        super().setup()  # work around broken qx11extras
        self.COMMON_FLAGS.append("-I" + str(BuildLibXCB.get_install_dir(self) / "include"))


class BuildKGuiAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kguiaddons.git")
    dependencies = KDECMakeProject.dependencies + ["qtx11extras"]

    def setup(self):
        super().setup()
        # TODO: wayland support
        self.add_cmake_options(WITH_WAYLAND=False)


class BuildKIconThemes(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kiconthemes.git")


class BuildKItemViews(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kitemviews.git")

    def setup(self):
        super().setup()
        self.add_cmake_options(BUILD_DESIGNERPLUGIN=False)


class BuildKI18N(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/ki18n.git")
    dependencies = KDECMakeProject.dependencies + ["gettext"]


class BuildKWidgetsAddons(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kwidgetsaddons.git")

    def setup(self):
        super().setup()
        self.add_cmake_options(BUILD_DESIGNERPLUGIN=False)


class BuildKWindowSystem(KDECMakeProject):
    repository = GitRepository("https://invent.kde.org/frameworks/kwindowsystem.git")
    dependencies = KDECMakeProject.dependencies + ["qtx11extras", "libxfixes"]


class BuildDoplhin(KDECMakeProject):
    target = "dolphin"
    repository = GitRepository("https://invent.kde.org/system/dolphin.git")


# Lots of deps (including QtSVG)
# class BuildGwenview(KDECMakeProject):
#     target = "gwenview"
#     repository = GitRepository("https://invent.kde.org/graphics/gwenview.git")
