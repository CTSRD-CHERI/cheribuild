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
import typing

from .crosscompileproject import CrossCompileMakefileProject, DefaultInstallDir, GitRepository, MakeCommandKind
from ...config.compilation_targets import CompilationTargets, LinuxTargetInfoBase
from ...utils import classproperty


class BuildCheri_OS_Test(CrossCompileMakefileProject):
    _supported_architectures = CompilationTargets.ALL_LINUX_PURECAP_TARGETS
    make_kind = MakeCommandKind.BsdMake
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheri-os-test.git", default_branch = "preview")

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    @classmethod
    def dependencies(cls, config) -> "tuple[str, ...]":
        #ti = typing.cast(typing.Type[LinuxTargetInfoBase], cls.get_crosscompile_target().target_info_cls)
        return "libxo", "libbsd"

    def setup(self) -> None:
        # Don't depend on libgcc_s
        self.COMMON_LDFLAGS.append("--unwindlib=none")

        return super().setup()

    def compile(self, **kwargs):
        # The binaries will be put into /opt/cheri-api-tests
        # This ensures Pyrefly that destdir won't be None
        assert self.destdir is not None
        self.destdir = self.destdir / "rootfs" / "opt" / "cheri-os-test"
        self.makedirs(self.destdir / "lib")

        if self.get_crosscompile_target().is_aarch64(include_purecap=True):
            self.make_args.set_env(MACHINE_ARCH="aarch64c")
        elif self.get_crosscompile_target().is_experimental_cheri093_std(self.config):
            self.make_args.set_env(
                # The CheriBSD bmake makefiles are not RVY aware and so we need
                # to manually set MACHINE_ABI and the RISC-V arch string.
                MACHINE_ABI="purecap",
                MACHINE_ARCH=self.target_info.get_riscv_arch_string(self.crosscompile_target,
                                                                    self.config,
                                                                    softfloat=False)
            )
        else:
            target = self.target_info.target
            raise NotImplementedError(f"Unsupported architecture: {target.cpu_architecture} {target._cheri_isa}")

        self.make_args.set_env(
            DESTDIR=str(self.destdir),
            BINOWN=os.getuid(),
            BINGRP=os.getgid(),
            BINMODE=755,
            # Suppress a warning related to absent exception handlers.
            LD_FATAL_WARNINGS="no",
            # This is not supported by Morello LLVM,
            MAKESYSPATH=str(self.source_dir / "mk"),
            MAKEOBJDIRPREFIX=str(self.build_dir),
            # This property was added to _ClangBasedTargetInfo to support this specific use case.
            OBJCOPY=self.target_info.objcopy,
        )

        self.run_make(cwd=self.source_dir / "cheriostest")

    def install(self, **kwargs):
        self.run_make_install(cwd=self.source_dir / "cheriostest")

    def process(self):
        self.check_required_system_tool("bmake", homebrew="bmake", cheribuild_target="bmake")
        super().process()
