#
# Copyright (c) 2022 Microsoft Corporation
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

from pathlib import Path
from typing import Optional

from .crosscompileproject import CrossCompileMakefileProject, DefaultInstallDir
from ..project import ExternallyManagedSourceRepository


class BuildGKermit(CrossCompileMakefileProject):
    build_in_source_dir = False
    build_via_symlink_farm = True
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    repository = ExternallyManagedSourceRepository()
    set_commands_on_cmdline = True

    def setup(self):
        super().setup()
        self.common_warning_flags.append("-Wno-unused-value")
        self.common_warning_flags.append("-Wno-non-literal-null-conversion")
        self.make_args.set_env(KFLAGS=self.commandline_to_str(
            [*self.default_compiler_flags, "-include", "unistd.h"]))

    def update(self):
        filename = "gku201.tar.gz"
        sha256 = "19f9ac00d7b230d0a841928a25676269363c2925afc23e62704cde516fc1abbd"
        url_prefix = "https://www.kermitproject.org/ftp/kermit/archives/"

        self.makedirs(self.source_dir)
        self.download_file(self.source_dir / filename, url=url_prefix + filename, sha256=sha256)
        if not (self.source_dir / "makefile").is_file():
            self.run_cmd(["tar", "xzvf", self.source_dir / filename, "-C", self.source_dir])

    def compile(self, cwd: "Optional[Path]" = None, parallel: bool = True):
        if cwd is None:
            cwd = self.build_dir
        self.run_make("gkermit", cwd=cwd, parallel=parallel)

    def install(self, **kwargs):
        self.install_file(self.build_dir / "gkermit", self.install_dir / "bin" / "gkermit")
