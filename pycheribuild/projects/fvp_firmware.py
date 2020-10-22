#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2020 Alex Richardson
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

from .cross.crosscompileproject import CrossCompileMakefileProject
from .project import DefaultInstallDir, GitRepository
from ..config.compilation_targets import CompilationTargets
from ..utils import get_compiler_info


class MorelloFirmwareBase(CrossCompileMakefileProject):
    do_not_add_to_targets = True
    supported_architectures = [CompilationTargets.MORELLO_BAREMETAL_HYBRID]
    cross_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY  # TODO: install it
    needs_sysroot = False  # We don't need a complete sysroot


class BuildMorelloScpFirmware(MorelloFirmwareBase):
    repository = GitRepository("git@git.morello-project.org:morello/scp-firmware.git")
    project_name = "morello-scp-firmware"
    supported_architectures = [CompilationTargets.ARM_NONE_EABI]
    cross_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR

    def setup(self):
        super().setup()
        self.make_args.set(PRODUCT="morello", MODE="debug", LOG_LEVEL="INFO", V="y")  # TODO: change it to warn
        ccinfo = get_compiler_info(self.CC)
        # Build system tries to use macos tool which won't work
        self.make_args.set(
            AR=self.target_info.ar,
            OBJCOPY=self.CC.with_name(self.CC.name.replace("gcc", "objcopy")),
            SIZE=self.CC.with_name(self.CC.name.replace("gcc", "size")),
            )

    def install(self, **kwargs):
        pass  # TODO: implement

    def run_tests(self):
        self.run_make(make_target="test")  # XXX: doesn't work yet, needs a read/write/isatty()
