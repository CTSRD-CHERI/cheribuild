#
# Copyright (c) 2025 Paul Metzger
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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
from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository
from ..project import (
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind
)

from ...config.compilation_targets import CompilationTargets
from ...utils import classproperty

class BuildLibmd(CrossCompileAutotoolsProject):
    _always_add_suffixed_targets = True
    _supported_architectures = (CompilationTargets.LINUX_MORELLO_PURECAP,)
    _can_use_autogen_sh = True
    dependencies = ("morello-muslc",)
    make_kind = MakeCommandKind.GnuMake
    is_sdk_target = False
    repository = GitRepository("https://git.hadrons.org/git/libmd.git")
    target = 'libmd'

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE
    
    @property
    def muslc_target(self) -> str:
        return self.target_info.target_triple
    
    def setup(self) -> None:
        super().setup()
        self.COMMON_LDFLAGS.append("--unwindlib=none")
        self.make_args.set(ARCH="arm64", O=self.build_dir)
        self.make_args.set(
            CC=self.commandline_to_str([self.CC, *self.essential_compiler_and_linker_flags]),
            HOSTCC=self.host_CC,
            # Force busybox's Makefile not to use the triple for finding the toolchain
            CROSS_COMPILE="",
            LD=self.target_info.linker,
        )
        self.configure_args.extend(["--target=" + self.muslc_target])
    
    def configure(self, **kwargs):
        if not self.configure_command.exists():
            self.run_cmd(self.source_dir / "autogen", cwd=self.source_dir)
        super().configure(**kwargs)

    def compile(self, **kwargs):
        self.make_args.set(DESTDIR=self.install_dir / "rootfs")
        self.run_make()
        super().compile(**kwargs)

    def install(self, **kwargs):
        self.run_make_install()
        super().install(**kwargs)



