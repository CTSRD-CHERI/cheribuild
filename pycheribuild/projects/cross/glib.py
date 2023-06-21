#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright 2022 Alex Richardson
# Copyright 2022 Google LLC
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


class BuildGlib(CrossCompileMesonProject):
    target = "glib"
    dependencies = ("pcre2", "libffi", "dbus")
    repository = GitRepository("https://gitlab.gnome.org/GNOME/glib.git",
                               temporary_url_override="https://gitlab.gnome.org/arichardson/glib.git",
                               old_urls=[b"https://github.com/CTSRD-CHERI/glib.git"],
                               url_override_reason="Lots of CHERI incompatibilities",
                               default_branch="main-with-cheri-fixes", force_branch=True)

    def setup(self) -> None:
        super().setup()
        self.add_meson_options(xattr=False, tests=True)
        self.configure_args.append("--localstatedir=/var")  # This is needed for GDBus
        self.common_warning_flags.append("-Werror=int-conversion")
        self.common_warning_flags.append("-Werror=incompatible-pointer-types")
        self.COMMON_FLAGS.append("-DG_ENABLE_EXPERIMENTAL_ABI_COMPILATION")
        if self.compiling_for_cheri():
            self.common_warning_flags.append("-Wshorten-cap-to-int")
        if self.target_info.is_freebsd():
            self.add_meson_options(b_lundef=False)  # undefined reference to environ
        self.add_meson_options(gtk_doc=False)
        self.configure_args.append("--wrap-mode=nodownload")
