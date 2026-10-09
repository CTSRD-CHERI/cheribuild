#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2026 Capabilities Limited
#
# This software/hardware was developed in part by Capabilities Limited, lowRISC CIC,
# Sigil Logic Inc., and Cherified Systems LLC under Advanced Research and Invention
# Agency (ARIA) Contract MSAI-PR01-P060, "AI Verified Hardware for AI (AIVHAI)".
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
from pathlib import Path

from .crosscompileproject import CrossCompileAutotoolsProject
from ..project import (
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
)
from ..simple_project import OptionalStringConfigOption
from ...config.compilation_targets import BaremetalClangTargetInfo, CompilationTargets


class BuildAIVHAIFWCompartment(CrossCompileAutotoolsProject):
    target = "aivhai-fw-compartment"
    repository = GitRepository(
        "https://github.com/Capabilities-Limited/aivhai-fw-compartment.git",
        default_branch="main",
    )
    _supported_architectures = (CompilationTargets.FREESTANDING_RISCV64_Y_PURECAP,)
    default_install_dir = DefaultInstallDir.ROOTFS_LOCALBASE
    make_kind = MakeCommandKind.GnuMake
    _needs_sysroot = False
    _always_add_suffixed_targets = True
    target_info: BaremetalClangTargetInfo  # Specify the type of self.target_info to fix type checker warnings

    fw_compartment_path = OptionalStringConfigOption("fw-compartment-path", help="OpenSBI FW_COMPARTMENT_PATH")
    vulnerabilities = OptionalStringConfigOption(
        "vulnerabilities",
        show_help=True,
        help="Build a vulnerable firmware, specify comma-separted vulnerabilities to inject;"
        "choose from (ASR, BOUNDS, REGS_TEMP, DDC)",
    )

    def setup(self):
        super().setup()

        compflags = " " + self.commandline_to_str(self.essential_compiler_and_linker_flags)
        self.make_args.add_flags("-f", self.source_dir / "Makefile")
        self.make_args.set(
            SRC=self.source_dir / "fw_compartment.S",
            LDSCRIPT=self.source_dir / "fw_compartment.ldS",
        )

        if self.fw_compartment_path:
            assert Path(self.fw_compartment_path).exists(), (
                "Compartment path does not exist: {self.fw_compartment_path}"
            )
            self.make_args.set(
                CPPFLAGS=f"-DFW_COMPARTMENT_PATH={self.fw_compartment_path}",
            )

        if self.vulnerabilities:
            for vulnerablity in self.vulnerabilities.split(","):
                compflags += " -DAIVHAI_VULN_" + vulnerablity

        self.make_args.set(CC=str(self.CC) + compflags)

    def configure(self) -> None:
        pass
