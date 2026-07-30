#
# Copyright (c) 2025-2026 Paul Metzger
# All rights reserved.
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

from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository, MakeCommandKind
from ...config.compilation_targets import CompilationTargets
from ...utils import OSInfo, classproperty


class BuildLibbsd(CrossCompileAutotoolsProject):
    _supported_architectures = CompilationTargets.ALL_CHERI_AND_MORELLO_LINUX_TARGETS
    make_kind = MakeCommandKind.GnuMake
    repository = GitRepository(
        "https://gitlab.freedesktop.org/libbsd/libbsd.git",
        temporary_url_override="https://gitlab.freedesktop.org/paul-metzger/libbsd-private-fork-pmetzger.git",
        default_branch="0_12_2_with_alignment_macro_fix",
        force_branch=True,
    )

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    @classmethod
    def dependencies(cls, config) -> "tuple[str, ...]":
        return ("libmd",)

    def configure(self, **kwargs):
        self.run_cmd(self.source_dir / "autogen", cwd=self.source_dir)
        super().configure(**kwargs)

    def setup(self) -> None:
        super().setup()
        # Remove dependency on libgcc_eh
        self.COMMON_LDFLAGS.append("--unwindlib=none")
        # Remove dependcy on libgcc_s
        self.COMMON_LDFLAGS.append("-Wc,--unwindlib=none")
        if OSInfo.IS_MAC:
            self.configure_environment["SED"] = "gsed"

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        if OSInfo.IS_MAC:
            self.check_required_system_tool("gsed", homebrew="gnu-sed")

    def install(self, **kwargs) -> None:
        self.run_make_install()

        # Copy the libraries from the cross-compile sysroot into rootfs/lib
        for sofile in self.install_dir.glob("lib/libbsd.so*"):
            self.install_file(sofile, self.install_dir / "rootfs/lib/" / sofile.name, create_dirs=True)

    def process(self):
        new_path = os.getenv("PATH", "")
        # Needed until https://gitlab.freedesktop.org/libbsd/libbsd/-/merge_requests/35 lands
        if OSInfo.IS_MAC:
            # /usr/bin/sed on macOS is not compatible, uses hardcoded 'sed' instead of AC_PROG_SED:
            # objdump -f format.ld.so | sed -n 's/.*file format \(.*\)/OUTPUT_FORMAT(\1)/;T;p' >format.ld
            new_path = str(self.get_homebrew_prefix("gnu-sed") / "libexec/gnubin") + ":" + new_path
        with self.set_env(PATH=new_path):
            super().process()
