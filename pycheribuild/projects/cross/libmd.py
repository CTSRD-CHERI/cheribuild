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

import typing

from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository, MakeCommandKind
from ...config.compilation_targets import CompilationTargets, LinuxTargetInfoBase
from ...utils import classproperty


class BuildLibmd(CrossCompileAutotoolsProject):
    _can_use_autogen_sh = True
    _supported_architectures = CompilationTargets.ALL_CHERI_AND_MORELLO_LINUX_TARGETS
    make_kind = MakeCommandKind.GnuMake
    is_sdk_target = False
    repository = GitRepository("https://git.hadrons.org/git/libmd.git")

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    def configure(self, **kwargs):
        self.run_cmd(self.source_dir / "autogen", cwd=self.source_dir)
        super().configure(**kwargs)

    def setup(self) -> None:
        super().setup()
        # Remove dependency on libgcc_eh
        self.COMMON_LDFLAGS.append("--unwindlib=none")
        # Remove dependcy on libgcc_s
        self.COMMON_LDFLAGS.append("-Wc,--unwindlib=none")

    def install(self, **kwargs) -> None:
        super().install(**kwargs)

        # Copy the libraries from the cross-compile sysroot into rootfs/lib
        for sofile in self.install_dir.glob("lib/libmd.so*"):
            self.install_file(sofile, self.install_dir / f"rootfs/lib/" / sofile.name, create_dirs=True)
