#
# Copyright (c) 2020 Jessica Clarke
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

from pathlib import Path

from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository
from ...mtree import MtreeFile


class BuildBash(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/bash",
                               default_branch="cheri")
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS
    path_in_rootfs = "/usr/local"

    def setup(self):
        super().setup()
        # All CHERI architectures lack sbrk(2), required for Bash's malloc.
        if self.crosscompile_target.is_cheri_purecap():
            self.configureArgs.append("--without-bash-malloc")

        # Bash is horrible K&R C in many places and deliberately uses uses
        # declarations with no protoype. Hopefully it gets everything right.
        self.cross_warning_flags.append("-Wno-error=cheri-prototypes")

    def install(self, **kwargs):
        if self.destdir:
            self.make_args.set(DESTDIR=self.destdir)
        super().install(**kwargs)

        if not self.compiling_for_host():
            metalog = self.destdir / "METALOG"
            if not metalog.exists():
                self.fatal("METALOG", metalog, "does not exist")
                return
            mtree = MtreeFile(metalog)
            self.create_symlink(Path("/usr/local/bin/bash"), self.destdir / "bin/bash", relative=False)
            mtree.add_file(self.destdir / "bin/bash", "bin/bash")
            mtree.write(metalog)
