#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Jessica Clarke
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

from pathlib import Path

from .crosscompileproject import CrossCompileAutotoolsProject, CrossCompileProject
from .sdl import BuildSDLMixer
from ..project import ExternallyManagedSourceRepository, GitRepository


class BuildChocolateDoom(CrossCompileAutotoolsProject):
    target = "chocolate-doom"
    repository = GitRepository(
        "https://github.com/chocolate-doom/chocolate-doom.git",
        old_urls=[b"https://github.com/jrtc27/chocolate-doom.git"],
        default_branch="master",
        force_branch=True,
    )
    dependencies = ("sdl", "sdl-mixer", "sdl-net", "libpng")
    _can_use_autogen_sh = False  # Can't use autogen.sh since it will run configure in the wrong dir

    def configure(self, **kwargs):
        self.run_cmd("autoreconf", "-fi", cwd=self.source_dir)
        super().configure(**kwargs)

    def setup(self):
        super().setup()
        # Rpath is usually handled automatically, but doesn't see to work here
        self.COMMON_LDFLAGS.append("-Wl,-rpath," + str(BuildSDLMixer.get_instance(self).install_prefix / "lib"))


class BuildFreedoom(CrossCompileProject):
    repository = ExternallyManagedSourceRepository()
    dependencies = ("chocolate-doom",)

    version = "0.12.1"
    url_prefix: str = f"https://github.com/freedoom/freedoom/releases/download/v{version}/"
    packages: "dict[str, list[str]]" = {
        "freedoom": ["freedoom1", "freedoom2"],
        "freedm": ["freedm"],
    }

    def compile(self, **kwargs):
        for pkgname, wads in self.packages.items():
            filename = f"{pkgname}-{self.version}.zip"
            wadfiles = ["*/" + wad + ".wad" for wad in wads]
            if not (self.build_dir / filename).is_file():
                self.download_file(self.build_dir / filename, self.url_prefix + filename)
            self.run_cmd("unzip", "-jo", filename, *wadfiles, cwd=self.build_dir)

    def install(self, **kwargs):
        for wads in self.packages.values():
            for wad in wads:
                wadfile = wad + ".wad"
                wadpath = Path("share/doom") / wadfile
                self.install_file(self.build_dir / wadfile, self.install_dir / wadpath)
                self.write_file(
                    self.install_dir / "bin" / wad,
                    overwrite=True,
                    mode=0o755,
                    contents=f"#!/bin/sh\n"
                    f'exec {self.install_prefix}/bin/chocolate-doom -iwad {self.install_prefix}/{wadpath} "$@"\n',
                )
