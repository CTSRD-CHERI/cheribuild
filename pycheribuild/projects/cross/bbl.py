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

import re

from .cheribsd import *
from ..project import *
from .crosscompileproject import CrossCompileAutotoolsProject


# Using GCC not Clang, so can't use CrossCompileAutotoolsProject
class BuildBBLBase(CrossCompileAutotoolsProject):
    doNotAddToTargets = True
    repository = GitRepository("https://github.com/jrtc27/riscv-pk.git")
    make_kind = MakeCommandKind.GnuMake
    _always_add_suffixed_targets = True
    is_sdk_target = False
    freebsd_class = None
    cross_install_dir = DefaultInstallDir.CHERI_SDK

    @classmethod
    def dependencies(cls, config: CheriConfig):
        xtarget = cls.get_crosscompile_target(config)
        result = [cls.freebsd_class.get_class_for_target(xtarget).target]
        return result

    def __init__(self, config: CheriConfig):
        super().__init__(config)

    def configure(self, **kwargs):
        kernel_path = self.freebsd_class.get_installed_kernel_path(self, cross_target=self.crosscompile_target)
        self.configureArgs.extend([
            "--with-payload=" + str(kernel_path),
            "--host=" + self.get_host_triple()
            ])
        super().configure(**kwargs)

    def get_installed_kernel_path(self):
        return self.real_install_root_dir / self.get_host_triple() / "bin" / "bbl"


class BuildBBLFreeBSDRISCV(BuildBBLBase):
    project_name = "bbl-freebsd"
    target = "bbl-freebsd"
    supported_architectures = [CompilationTargets.FREEBSD_RISCV]
    freebsd_class = BuildFreeBSD


class BuildBBLFreeBSDWithDefaultOptionsRISCV(BuildBBLBase):
    project_name = "bbl-freebsd-with-default-options"
    target = "bbl-freebsd-with-default-options"
    supported_architectures = [CompilationTargets.FREEBSD_RISCV]
    freebsd_class = BuildFreeBSDWithDefaultOptions


class BuildBBLCheriBSDRISCV(BuildBBLBase):
    project_name = "bbl-cheribsd"
    target = "bbl-cheribsd"
    supported_architectures = [CompilationTargets.CHERIBSD_RISCV]
    freebsd_class = BuildCHERIBSD

