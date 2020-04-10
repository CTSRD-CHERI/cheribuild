#-
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

from .crosscompileproject import *
from ..project import *
from ...utils import setEnv


class BuildRtems(CrossCompileProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/rtems",
        per_target_branches={
            CompilationTargets.RTEMS_RISCV64_PURECAP: TargetBranchInfo("cheri_waf1", "rtems-riscv")
            })
    target = "rtems"
    project_name = "rtems"
    supported_architectures = [CompilationTargets.RTEMS_RISCV64_PURECAP]
    default_install_dir = DefaultInstallDir.SYSROOT

    def __init__(self, config: CheriConfig):
        super().__init__(config)

    def configure(self, **kwargs):
        waf_run = self.run_cmd(self.sourceDir / "waf",
            "bsp_defaults",
            "-t", self.sourceDir,
            "-o", self.buildDir,
            "--rtems-bsps=rv64*xcheri*",
            "--rtems-compiler=clang",
            captureOutput=True)

        # waf configure reads config.ini by default to read RTEMS flags from
        self.writeFile(self.sourceDir / "config.ini", str(waf_run.stdout, 'utf-8'), overwrite=True)

        self.run_cmd(self.sourceDir / "waf", "configure",
            "-t", self.sourceDir,
            "-o", self.buildDir,
            "--prefix", self.destdir)

    def compile(self, **kwargs):
        self.run_cmd(self.sourceDir / "waf", "-t", self.sourceDir, "-o", self.buildDir)

    def install(self, **kwargs):
        self.run_cmd(self.sourceDir / "waf", "-t", self.sourceDir, "-o", self.buildDir, "install")

    def process(self):
        with setEnv(PATH=str(self.sdk_bindir) + ":" + os.getenv("PATH", "")):
            super().process()
