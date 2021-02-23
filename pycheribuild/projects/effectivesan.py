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

from .cross.llvm import BuildLLVMMonoRepoBase
from .project import ComputedDefaultValue, GitRepository
from ..utils import OSInfo


# TODO: build from source
class BuildEffectiveSan(BuildLLVMMonoRepoBase):
    project_name = "EffectiveSan"
    repository = GitRepository("https://github.com/GJDuck/EffectiveSan")
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, project: config.output_root / "effectivesan",
        as_string="$INSTALL_ROOT/effectivesan")
    skip_cheri_symlinks = True
    llvm_subdir = "llvm-4.0.1.src"

    def compile(self, **kwargs):
        pass

    def install(self, **kwargs):
        pass

    def process(self):
        if not OSInfo.IS_LINUX:
            self.fatal("This target is currently only supported on Linux")

        with tempfile.TemporaryDirectory() as td:
            version = "0.1.1-alpha"
            filename = "effectivesan-{}.tar.xz".format(version)
            url = "https://github.com/GJDuck/EffectiveSan/releases/download/v{}/{}".format(version, filename)
            self.run_cmd("wget", url, cwd=td)
            self.clean_directory(self.install_dir, ensure_dir_exists=True)
            self.run_cmd("tar", "xvf", filename, "-C", self.install_dir, "--strip-components=1", cwd=td)
