#
# Copyright (c) 2018 James Clarke
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
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

from ...targets import targetManager
from ..project import *

import re

# Using GCC not Clang, so can't use CrossCompileAutotoolsProject
class BuildBBLFreeBSDWithDefaultOptionsRISCV(AutotoolsProject):
    projectName = "bbl-freebsd-with-default-options-riscv"
    target = "bbl-freebsd-with-default-options-riscv"
    dependencies = ["freebsd-with-default-options-riscv"]
    repository = GitRepository("https://github.com/jrtc27/riscv-pk.git")
    defaultInstallDir = AutotoolsProject._installToSDK
    make_kind = MakeCommandKind.GnuMake
    is_sdk_target = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # Extract tool prefix via toolchain's XCC
        cross_toolchain_mk = Path("/usr/local/share/toolchains/riscv64-gcc.mk")
        self.host = ""
        if cross_toolchain_mk.exists():
            with cross_toolchain_mk.open("r") as f:
                for l in f:
                    if l[:4] == "XCC=":
                        self.host = re.sub(r".*/(.*)-.*", r"\1", l[4:]).strip()
                        break
        if not self.host:
            self.fatal("Could not find riscv64-gcc XCC")

        freebsdTarget = targetManager.get_target_raw("freebsd-with-default-options-riscv")
        freebsdProject = freebsdTarget.get_or_create_project(None, config)
        kernelPath = freebsdProject.get_installed_kernel_path(self, config)
        self.configureArgs.extend([
            "--with-payload=" + str(kernelPath),
            "--host=" + self.host
        ])

    def get_installed_kernel_path(self, caller, config):
        return self.real_install_root_dir / self.host / "bin" / "bbl"
