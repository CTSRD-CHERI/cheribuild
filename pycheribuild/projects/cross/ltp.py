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


class BuildLTP(CrossCompileAutotoolsProject):
    target = "upstream-ltp"
    repository = GitRepository("https://github.com/linux-test-project/ltp.git")
    _needs_sysroot = True
    is_sdk_target = False
    _supported_architectures = (
        CompilationTargets.UPSTREAM_LINUX_RISCV64,
        CompilationTargets.UPSTREAM_LINUX_AARCH64,
    )
    make_kind = MakeCommandKind.GnuMake

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    def configure(self) -> None:
        pass

    def needs_configure(self):
        return False

    def compile(self) -> None:
        pass

    def install(self) -> None:
        compflags = [*self.essential_compiler_and_linker_flags]
        compflags += ["--sysroot", self.install_dir]
        compflags += ["-isystem", self.install_dir / "usr/include"]
        # Avoid dependency on libgcc_eh
        compflags += ["--unwindlib=none"]
        self.COMMON_LDFLAGS.append("--unwindlib=none")

        with self.set_env(
            CFLAGS=self.commandline_to_str(compflags) + " -Doff64_t=off_t -Wno-error=int-conversion",
            LDFLAGS=self.commandline_to_str(compflags) + " -fuse-ld=lld -static",
            HOST_CFLAGS="-O2 -Wall",
            CONFIGURE_OPT_EXTRA="--prefix=/ --host=aarch64-linux-gnu --disable-metadata --without-numa",
            BUILD_DIR=self.build_dir,
            LTP_INSTALL=self.install_dir,
            TRIPLE=self.target,
            MAKE_OPTS="TST_NEWER_64_SYSCALL=no TST_COMPAT_16_SYSCALL=no",
            TARGETS="pan tools/apicmds testcases/kernel/syscalls",
            EXCLUDE_TARGETS="testcases/kernel/kvm",
            CC=self.sdk_bindir / "clang",
            LD=self.target_info.linker,
            AR=self.sdk_bindir / "llvm-ar",
            NM=self.sdk_bindir / "llvm-nm",
            STRIP=self.sdk_bindir / "llvm-strip",
            OBJCOPY=self.sdk_bindir / "llvm-objcopy",
            OBJDUMP=self.sdk_bindir / "llvm-objdump",
        ):
            # Build and install LTP in the target's rootfs' /opt/ltp directory
            self.run_cmd(
                ["./build.sh", "-t", "cross", "-o", "out", "-ip", self.install_dir / "rootfs/opt/ltp"],
                cwd=self.source_dir,
            )


class BuildMorelloLTP(BuildLTP):
    target = "morello-ltp"
    repository = GitRepository("https://git.morello-project.org/morello/morello-linux-ltp.git")
    _supported_architectures = CompilationTargets.ALL_MORELLO_LINUX_TARGETS


class BuildAllianceLTP(BuildLTP):
    target = "ltp"
    repository = GitRepository(
        "https://github.com/CHERI-Alliance/ltp.git", default_branch="codasip-cheri-riscv-20250930"
    )
    _supported_architectures = CompilationTargets.ALL_CHERI_LINUX_TARGETS
