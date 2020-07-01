#
# SPDX-License-Identifier: BSD-2-Clause
#
# Author: Hesham Almatary <Hesham.Almatary@cl.cam.ac.uk>
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
import os

from .crosscompileproject import (CheriConfig, CompilationTargets, CrossCompileAutotoolsProject, DefaultInstallDir,
                                  GitRepository)
from ...utils import get_compiler_info, set_env


class BuildFreeRTOS(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/FreeRTOS-mirror",
                               force_branch=True, default_branch="cheri")
    target = "freertos"
    project_name = "freertos"
    dependencies = ["newlib", "compiler-rt-builtins"]
    is_sdk_target = True
    needs_sysroot = False  # We don't need a complete sysroot
    supported_architectures = [
        CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP,
        CompilationTargets.BAREMETAL_NEWLIB_RISCV64]
    default_install_dir = DefaultInstallDir.SYSROOT

    # FreeRTOS Demos to build
    freertos_demos = [
        # Generic/simple (CHERI-)RISC-V Demo that runs main_blinky on simulators
        # and simple SoCs
        "RISC-V-Generic"]

    # Map Demos and the FreeRTOS apps we support building/running for
    demo_apps = {"RISC-V-Generic": ["main_blinky"]}

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.compiler_resource = get_compiler_info(self.CC).get_resource_dir()

        # We only support building FreeRTOS with llvm from cheribuild
        self.make_args.set(TOOLCHAIN="llvm")

        # For backward compatibility. CheriFreeRTOS used to be built within a NIX env.
        # Override that with no and set the appopriate flags here.
        self.make_args.set(NIX_ENV="no")

        # Only build 64-bit FreeRTOS as cheribuild currently only supports building
        # for RV64
        self.make_args.set(RISCV_XLEN="64")

        # Set sysroot Makefile arg to pick up libc
        self.make_args.set(SYSROOT=str(self.sdk_sysroot))

        # Add compiler-rt location to the search path
        # self.make_args.set(LDFLAGS="-L"+str(self.compiler_resource / "lib"))

        if self.target_info.target.is_cheri_purecap():
            # CHERI-RISC-V sophisticated Demo with more advanced device drivers
            # and currently only runs on FPGA-GFE, purecap
            self.freertos_demos.append("RISC-V_Galois_P1")
            self.demo_apps["RISC-V_Galois_P1"] = ["main_blinky", "main_netboot"]

            self.make_args.set(EXTENSION="cheri")

    def compile(self, **kwargs):
        for demo in self.freertos_demos:
            if demo == "RISC-V-Generic":
                # Build parametrized FreeRTOS to run on QEMU's virt machine
                self.make_args.set(
                    BSP="qemu_virt-" + self.target_info.riscv_arch_string + "-" + self.target_info.riscv_softfloat_abi)

            for app in self.demo_apps[demo]:
                # Need to clean before/between building apps, otherwise
                # irrelevant objs will be picked up from incompatible apps/builds
                self.run_make("clean", cwd=self.source_dir / str("FreeRTOS/Demo/" + demo))
                self.make_args.set(PROG=app)
                self.run_make(cwd=self.source_dir / str("FreeRTOS/Demo/" + demo))
                self.move_file(self.source_dir / str("FreeRTOS/Demo/" + demo + "/" + app + ".elf"),
                               self.source_dir / str("FreeRTOS/Demo/" + demo + "/" + demo + app + ".elf"))

    def configure(self):
        pass

    def needs_configure(self):
        return False

    def install(self, **kwargs):
        for demo in self.freertos_demos:
            for app in self.demo_apps[demo]:
                self.install_file(self.source_dir / str("FreeRTOS/Demo/" + demo + "/" + demo + app + ".elf"),
                                  self.real_install_root_dir / str("FreeRTOS/Demo/" + demo + "_" + app + ".elf"))

    def process(self):
        with set_env(PATH=str(self.sdk_bindir) + ":" + os.getenv("PATH", ""),
                     # Add compiler-rt location to the search path
                     LDFLAGS="-L" + str(self.compiler_resource / "lib")):
            super().process()
