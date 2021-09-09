#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2020 Alex Richardson
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

from .crosscompileproject import CrossCompileMesonProject, GitRepository
from ..project import DefaultInstallDir


# Prefer the CMake build over autotools since autotools does not work out-of-the-box
class BuildFreeType2(CrossCompileMesonProject):
    target = "freetype2"
    repository = GitRepository("https://gitlab.freedesktop.org/freetype/freetype",
                               old_urls=[b"https://github.com/freetype/freetype2.git"])
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    dependencies = ["libpng"]

    def setup(self):
        super().setup()
        self.add_meson_options(tests="enabled")

    def run_tests(self):
        self.run_cmd(self.source_dir / "tests/scripts/download-test-fonts.py", cwd=self.source_dir / "tests")
        super().run_tests()


class BuildFontConfig(CrossCompileMesonProject):
    target = "fontconfig"
    dependencies = ["freetype2", "libexpat"]
    repository = GitRepository(
        "https://gitlab.freedesktop.org/fontconfig/fontconfig",
        temporary_url_override="https://gitlab.freedesktop.org/arichardson/fontconfig",
        url_override_reason="Needs pointer provenance fixes (no PR posted yet)")

    @property
    def pkgconfig_dirs(self) -> "list[str]":
        return BuildFreeType2.get_instance(self).installed_pkgconfig_dirs() + self.target_info.pkgconfig_dirs

    def setup(self):
        super().setup()
        self.add_meson_options(doc="disabled")
        self.common_warning_flags.append("-Werror=int-conversion")
