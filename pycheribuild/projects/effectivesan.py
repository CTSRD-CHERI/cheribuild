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
import tempfile
from pathlib import Path

from .cross.llvm import BuildLLVMMonoRepoBase
from .project import ComputedDefaultValue, GitRepository
from ..config.chericonfig import CheriConfig
from ..utils import OSInfo


# TODO: build from source
class BuildEffectiveSan(BuildLLVMMonoRepoBase):
    @classmethod
    def get_native_install_path(cls, config: CheriConfig):
        return config.output_root / "effectivesan"

    target = "effectivesan"
    default_directory_basename = "EffectiveSan"
    repository = GitRepository("https://github.com/GJDuck/EffectiveSan")
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, project: project.get_native_install_path(config),
        as_string="$INSTALL_ROOT/effectivesan")
    skip_cheri_symlinks = True
    root_cmakelists_subdirectory = Path("llvm-4.0.1.src")

    def compile(self, **kwargs):
        pass

    def install(self, **kwargs):
        pass

    def process(self):
        if not OSInfo.IS_LINUX:
            self.fatal("This target is currently only supported on Linux")

        with tempfile.TemporaryDirectory() as td:
            version = "0.1.1-alpha"
            filename = f"effectivesan-{version}.tar.xz"
            url = f"https://github.com/GJDuck/EffectiveSan/releases/download/v{version}/{filename}"
            self.download_file(Path(td, filename), url)
            self.clean_directory(self.install_dir, ensure_dir_exists=True)
            self.run_cmd("tar", "xvf", filename, "-C", self.install_dir, "--strip-components=1", cwd=td)
