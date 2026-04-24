#
# Copyright (c) 2025 Paul Metzger
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
    _always_add_suffixed_targets = True
    _can_use_autogen_sh = True
    _supported_architectures = (
        *CompilationTargets.ALL_CHERI_LINUX_TARGETS,
        *CompilationTargets.ALL_MORELLO_LINUX_TARGETS,
    )
    _default_architecture = CompilationTargets.CHERI_LINUX_MORELLO_PURECAP
    make_kind = MakeCommandKind.GnuMake
    is_sdk_target = False
    repository = GitRepository("https://git.hadrons.org/git/libmd.git")

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    @classmethod
    def dependencies(cls, config) -> "tuple[str, ...]":
        ti = typing.cast(typing.Type[LinuxTargetInfoBase], cls.get_crosscompile_target().target_info_cls)
        return ti.compiler_rt_target, ti.musl_target

    @property
    def muslc_target(self) -> str:
        return self.target_info.target_triple

    def setup(self) -> None:
        super().setup()
        # Remove dependency on libgcc_eh
        self.COMMON_LDFLAGS.append("--unwindlib=none")
        # Remove dependcy on libgcc_s
        self.COMMON_LDFLAGS.append("-Wc,--unwindlib=none")

    def configure(self, **kwargs):
        if not self.configure_command.exists():
            self.run_cmd(self.source_dir / "autogen", cwd=self.source_dir)
        super().configure(**kwargs)

    def compile(self, **kwargs):
        self.run_make()
        super().compile(**kwargs)

    def install(self, **kwargs):
        self.run_make_install()
        super().install(**kwargs)
