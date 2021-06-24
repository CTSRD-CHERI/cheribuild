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

from .crosscompileproject import CrossCompileCMakeProject
from .qt5 import BuildQtBase
from ..project import DefaultInstallDir, GitRepository
from ...config.compilation_targets import CompilationTargets


class KDECMakeProject(CrossCompileCMakeProject):
    do_not_add_to_targets = True
    default_install_dir = DefaultInstallDir.KDE_PREFIX
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS
    ctest_needs_full_disk_image = False  # default to running with the full disk image
    # Prefer the libraries in the build directory over the installed ones. This is needed when RPATH is not set
    # correctly, i.e. when built with CMake+Ninja on macOS with a version where
    # https://gitlab.kitware.com/cmake/cmake/-/merge_requests/6240 is not included.
    ctest_script_extra_args = ("--extra-library-path", "/build/bin", "--extra-library-path", "/build/lib")
    dependencies = ["qtbase"]

    def setup(self):
        super().setup()
        if self.target_info.is_macos():
            self.add_cmake_options(APPLE_SUPPRESS_X11_WARNING=True)

    @property
    def cmake_prefix_paths(self):
        return [self.install_dir, BuildQtBase.get_install_dir(self)] + super().cmake_prefix_paths


class BuildExtraCMakeModules(KDECMakeProject):
    target = "extra-cmake-modules"
    repository = GitRepository("https://invent.kde.org/frameworks/extra-cmake-modules.git")


class BuildKCoreAddons(KDECMakeProject):
    target = "kcoreaddons"
    repository = GitRepository("https://invent.kde.org/frameworks/kcoreaddons.git")


class BuildDoplhin(KDECMakeProject):
    target = "dolphin"
    repository = GitRepository("https://invent.kde.org/system/dolphin.git")


# Lots of deps (including QtSVG)
# class BuildGwenview(KDECMakeProject):
#     target = "gwenview"
#     repository = GitRepository("https://invent.kde.org/graphics/gwenview.git")
