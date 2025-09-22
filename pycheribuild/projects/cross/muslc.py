#
# Copyright (c) 2025 Hesham Almatary
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


from .crosscompileproject import CrossCompileAutotoolsProject
from ..project import (
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
)
from ...config.compilation_targets import CompilationTargets
from ...utils import classproperty


class BuildMuslc(CrossCompileAutotoolsProject):
    target = "muslc"
    repository = GitRepository("https://git.musl-libc.org/git/musl")
    needs_sysroot = False
    is_sdk_target = False
    supported_architectures = (
        CompilationTargets.LINUX_AARCH64,
        CompilationTargets.LINUX_RISCV64,
    )
    make_kind = MakeCommandKind.GnuMake
    _always_add_suffixed_targets = True

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    @property
    def muslc_target(self) -> str:
        return self.target_info.target_triple

    def setup(self) -> None:
        if self.config.verbose:
            self.make_args.set(V=True)

        self.make_args.set(
            CC="clang",
            HOSTCC="clang",
            # Force muslc's Makefile not to use the triple for finding the toolchain
            CROSS_COMPILE="",
            LD=self.target_info.linker,
            AR=self.sdk_bindir / "llvm-ar",
            NM=self.sdk_bindir / "llvm-nm",
            STRIP=self.sdk_bindir / "llvm-strip",
            OBJCOPY=self.sdk_bindir / "llvm-objcopy",
            OBJDUMP=self.sdk_bindir / "llvm-objdump",
        )

        self.configure_args.extend(
            [
                "--target=" + self.muslc_target,
            ]
        )
        super().setup()

    def clean(self) -> None:
        self.run_make("distclean", cwd=self.source_dir)
        self.run_make("clean", cwd=self.source_dir)
        super().clean()


class BuildMorelloLinuxMuslc(BuildMuslc):
    target = "morello-muslc"
    repository = GitRepository("https://git.morello-project.org/morello/musl-libc.git")
    supported_architectures = (CompilationTargets.LINUX_MORELLO_PURECAP,)

    def setup(self) -> None:
        self.configure_args.extend(["--enable-morello"])

        # FIXME: Morello muslc does not address warnings with implicit functions defined
        # and fails building. This should be fixed in Morello Busybox codebase manually
        # or when they update to recent revisions/releases
        self.cross_warning_flags.append("-Wno-error=implicit-function-declaration")
        super().setup()
