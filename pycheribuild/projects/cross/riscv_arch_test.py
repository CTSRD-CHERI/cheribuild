#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
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
import shutil
from pathlib import Path

from ..project import DefaultInstallDir, GitRepository, MakeCommandKind, MakefileProject
from ..sail import BuildSailRISCV
from ...config.compilation_targets import CompilationTargets


class BuildRiscvArchTestsBase(MakefileProject):
    do_not_add_to_targets = True
    repository = GitRepository("https://github.com/riscv/riscv-arch-test")
    cross_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    make_kind = MakeCommandKind.GnuMake
    needs_sysroot = False
    supported_architectures = [CompilationTargets.NATIVE]  # Not really but works for now

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        # FIXME: build with clang instead of requiring RISC-V GCC
        self.check_required_system_tool("riscv64-unknown-elf-gcc", homebrew="riscv/riscv/riscv-gnu-toolchain")


class BuildRiscvArchTestsSail(BuildRiscvArchTestsBase):
    target = "riscv-arch-test-sail"
    dependencies = ["sail-riscv"]
    build_in_source_dir = True

    def setup(self):
        super().setup()
        # TODO: also test the ocaml sim?
        self.make_args.set(RISCV_TARGET="sail-riscv-c")

    def run_for_xlen(self, xlen: int, sim: Path):
        args = self.make_args.copy()
        args.set(XLEN=xlen, TARGET_SIM=str(sim) + " --no-trace")
        for ext in ("C", "I", "M", "Zifencei", "privilege"):
            args.set(RISCV_DEVICE=ext)
            self.run_make("verify", options=args)

    def compile(self, **kwargs):
        sail_dir = BuildSailRISCV.get_instance(self).build_dir / "c_emulator"
        self.run_for_xlen(64, sail_dir / "riscv_sim_RV64")
        if shutil.which("riscv32-unknown-elf-gcc"):
            self.run_for_xlen(32, sail_dir / "riscv_sim_RV32")

    def run_tests(self):
        self.compile()
