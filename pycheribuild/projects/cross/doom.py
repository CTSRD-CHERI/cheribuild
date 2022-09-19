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
from ..project import ExternallyManagedSourceRepository, GitRepository


class BuildChocolate_Doom(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/chocolate-doom/chocolate-doom.git",
                               old_urls=[b"https://github.com/jrtc27/chocolate-doom.git"],
                               default_branch="master", force_branch=True)
    dependencies = ["sdl", "sdl-mixer", "sdl-net", "libpng"]

    def configure(self, **kwargs):
        self.run_cmd("autoreconf", "-fi", cwd=self.source_dir)
        super().configure(**kwargs)


class BuildFreedoom(CrossCompileProject):
    repository = ExternallyManagedSourceRepository()
    dependencies = ["chocolate-doom"]

    version = "0.12.1"
    url_prefix: str = "https://github.com/freedoom/freedoom/releases/download/v{0}/".format(version)
    packages: "dict[str, list[str]]" = {
        'freedoom': ['freedoom1', 'freedoom2'],
        'freedm': ['freedm']
    }

    def compile(self, **kwargs):
        for pkgname, wads in self.packages.items():
            filename = "{0}-{1}.zip".format(pkgname, self.version)
            wadfiles = ['*/' + wad + ".wad" for wad in wads]
            if not (self.build_dir / filename).is_file():
                self.download_file(self.build_dir / filename, self.url_prefix + filename)
            self.run_cmd("unzip", "-jo", filename, *wadfiles, cwd=self.build_dir)

    def install(self, **kwargs):
        for wads in self.packages.values():
            for wad in wads:
                wadfile = wad + ".wad"
                wadpath = Path("share/doom") / wadfile
                self.install_file(self.build_dir / wadfile, self.install_dir / wadpath)
                self.write_file(self.install_dir / "bin" / wad, overwrite=True, mode=0o755,
                                contents="#!/bin/sh\nexec {0}/bin/chocolate-doom -iwad {0}/{1} \"$@\"\n".format(
                                    self.install_prefix, wadpath))
