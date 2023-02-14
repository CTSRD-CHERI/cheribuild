#
# Copyright (c) 2019 Alex Richardson
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
import shutil
from pathlib import Path

from .cross.llvm import BuildLLVMMonoRepoBase
from .project import DefaultInstallDir, GitRepository

# install_to_soaap_dir = ComputedDefaultValue(function=lambda config, project: config.output_root / "soaap",
#                                            as_string="$INSTALL_ROOT/soaap")
from ..config.chericonfig import CheriConfig
from ..config.compilation_targets import CompilationTargets


class BuildSoftBoundCETS(BuildLLVMMonoRepoBase):
    target = "softbound-cets"
    default_directory_basename = "SoftBoundCETS"
    repository = GitRepository("https://github.com/santoshn/SoftBoundCETS-3.9")
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    skip_cheri_symlinks = True
    root_cmakelists_subdirectory = Path("llvm-3.9")

    def compile(self, **kwargs):
        super().compile(**kwargs)
        make = shutil.which("gmake") or "make"
        # TODO: clean runtime
        self.run_cmd(make, cwd=self.source_dir / "runtime")

    def install(self, **kwargs):
        self.info("Not installing, to use SoftBoundCETS run from the source dir")

    @classmethod
    def get_native_install_path(cls, config: CheriConfig):
        return cls.get_instance(None, config, cross_target=CompilationTargets.NATIVE).install_dir
