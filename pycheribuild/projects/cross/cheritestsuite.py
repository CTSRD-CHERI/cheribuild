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

import os
import shutil
import typing

from .crosscompileproject import CrossCompileMakefileProject, DefaultInstallDir, GitRepository, MakeCommandKind
from ...config.compilation_targets import CompilationTargets, LinuxTargetInfoBase
from ...config.target_info import CPUArchitecture, RiscvCheriISA
from ...utils import classproperty


class BuildCheriAPITests(CrossCompileMakefileProject):
    _always_add_suffixed_targets = True
    _needs_sysroot = True
    _supported_architectures = (
        CompilationTargets.CHERI_LINUX_MORELLO_PURECAP,
        CompilationTargets.CHERI_LINUX_RISCV64_PURECAP_093,
        CompilationTargets.MORELLO_LINUX_MORELLO_PURECAP,
    )
    _default_architecture = CompilationTargets.CHERI_LINUX_MORELLO_PURECAP
    build_in_source_dir = False
    compiler_rt_dependency = None
    make_kind = MakeCommandKind.BsdMake
    repository = GitRepository("https://github.com/CTSRD-CHERI/portable-cheribsd-test-suite.git")

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    @classmethod
    def dependencies(cls, config) -> "tuple[str, ...]":
        ti = typing.cast(typing.Type[LinuxTargetInfoBase], cls.get_crosscompile_target().target_info_cls)
        return ti.compiler_rt_target, ti.musl_target, "libxo"

    def setup(self) -> None:
        if self.get_crosscompile_target().is_aarch64(include_purecap=True):
            self.set_env_make_args(machine_cpuarch="aarch64c", machine_abi="purecap", machine_arch="aarch64c")
        elif self.get_crosscompile_target().is_experimental_cheri093_std(self.config):
            self.set_env_make_args(
                machine_cpuarch="rv64imafdczcherihybrid_zcherilevels",
                machine_abi="purecap",
                machine_arch="rv64imafdczcherihybrid_zcherilevels",
            )
        else:
            target = self.target_info.target
            raise NotImplementedError(f"Unsupported architecture: {target.cpu_architecture} {target._cheri_isa}")

        return super().setup()

    def compile(self, **kwargs):
        self.install_arch_specific_machine_headers()

        # Musl libc's alltypes.h doesn't have a header guard by design and redefinition
        # errors caused by this are false positives.
        self.cross_warning_flags.append("-Wno-error=typedef-redefinition")

        # Musl libc's endian.h causes these warnings
        self.cross_warning_flags.append("-Wno-error=shift-op-parentheses")
        self.cross_warning_flags.append("-Wno-error=bitwise-op-parentheses")
        # Muls libc's ucontext.h causes this warning
        self.cross_warning_flags.append("-Wno-error=strict-prototypes")

        # The binaries will be put into /opt/cheri-api-tests
        # This ensures Pyrefly that destdir won't be None
        assert self.destdir is not None
        self.destdir = self.destdir / "rootfs" / "opt" / "cheri-api-tests"
        self.makedirs(self.destdir)

        self.make_args.set_env(
            C_INCLUDE_PATH="$C_INCLUDE_PATH:" + str(self.source_dir / "compat_headers"),
            # Ignore warning about the stack protector flag being unnecessary for purecap
            CFLAGS=" ".join(
                [
                    *self.default_compiler_flags(),
                    # Don't link with libgcc_s
                    "-rtlib=compiler-rt",
                    # It won't find libclang_rt.builtins-riscv64.a without this
                    f"-resource-dir={self.rootfs_dir}",
                ]
            ),
            CROSS_COMPILE="",
            # Put the binary into root's home directory
            DESTDIR=str(self.destdir),
            BINOWN=os.getuid(),
            BINGRP=os.getgid(),
            BINMODE=755,
            LD_FATAL_WARNINGS="no",
            LOCAL_LIBRARIES="bsd",
            # This is not supported by Morello LLVM
            MK_CHERI_CODEPTR_RELOCS="no",
            MAKESYSPATH=str(self.source_dir / "mk"),
            MAKEOBJDIRPREFIX=str(self.build_dir),
            # This property was added to _ClangBasedTargetInfo to support this specific use case.
            OBJCOPY=self.target_info.objcopy,
            **self.env_make_args,
        )

        self.run_make(cwd=self.source_dir / "cheribsdtest")

    def set_env_make_args(self, machine_cpuarch: str, machine_abi: str, machine_arch: str):
        self.env_make_args = {
            "MACHINE_CPUARCH": machine_cpuarch,
            "MACHINE_ABI": machine_abi,
            "MACHINE_ARCH": machine_arch,
        }

    def install(self, **kwargs):
        self.run_make_install(cwd=self.source_dir / "cheribsdtest")

    def process(self):
        self.check_required_system_tool("bmake", homebrew="bmake", cheribuild_target="bmake")
        super().process()

    def install_arch_specific_machine_headers(self):
        riscv_std093 = (
            CPUArchitecture.RISCV64,
            RiscvCheriISA.EXPERIMENTAL_STD093,
        )

        target = self.target_info.target
        if (target.cpu_architecture, target._cheri_isa) == riscv_std093:
            arch = "riscv-std093"
        elif target.cpu_architecture == CPUArchitecture.AARCH64:
            arch = "arm64"
        else:
            raise NotImplementedError(f"Unsupported architecture: {target.cpu_architecture} {target._cheri_isa}")

        machine_headers_dir = self.source_dir / "compat_headers" / "machine"
        arch_headers_dir = self.source_dir / "compat_headers" / arch
        if machine_headers_dir.exists():
            shutil.rmtree(machine_headers_dir)
        machine_headers_dir.mkdir(parents=True, exist_ok=True)
        if arch_headers_dir.exists():
            shutil.copytree(arch_headers_dir, machine_headers_dir, dirs_exist_ok=True)
