#
# Copyright (c) 2019 Alfredo Mazzinghi
# All rights reserved.
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

from .project import CheriConfig, CrossCompileTarget, DefaultInstallDir, GitRepository, Path, Project
from ..utils import ThreadJoiner


class BuildGo(Project):
    githubBaseUrl = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(githubBaseUrl + "freebsd-mips-go.git")
    no_default_sysroot = None
    skip_cheri_symlinks = True
    native_install_dir = DefaultInstallDir.CHERI_SDK

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.go_bootstrap = cls.add_path_option(
            "bootstrap-toolchain", show_help=True,
            help="Path to alternate go bootstrap toolchain.")

    def __init__(self, config: CheriConfig):
        super().__init__(config)

        # It does not seem possible to change this in the go build scripts (easily).
        self.makeDir = self.sourceDir / "src"
        self.binDir = self.sourceDir / "bin"
        self.pkgDir = self.sourceDir / "pkg"
        self.gorootDir = self.installDir / "go"
        self.goCache = Path("~").expanduser() / ".cache" / "go-build"

    def build_dir_for_target(self, target: CrossCompileTarget):
        return self.sourceDir / "pkg"

    def compile(self, **kwargs):
        env = {
            "GOROOT_FINAL": self.gorootDir,
        }
        if self.go_bootstrap:
            env["GOROOT_BOOTSTRAP"] = self.go_bootstrap

        cmd = "bash make.bash".split()
        if self.config.verbose:
            cmd += ["-v"]
        self.run_cmd(cmd, cwd=self.makeDir, env=env)

    def clean(self) -> ThreadJoiner:
        if (self.binDir / "go").exists():
            self.run_cmd("bash clean.bash".split(), cwd=self.makeDir)
        self.clean_directory(self.gorootDir)
        # Make sure we remove everything in the go cache, just in case
        if self.goCache.exists():
            self.clean_directory(self.goCache.resolve())
        joiner = super().clean()
        return joiner

    def install(self, **kwargs):
        # Move bin and pkg to goroot and link src dir
        self.clean_directory(self.gorootDir, ensure_dir_exists=True)

        self.copy_directory(self.binDir, self.gorootDir / "bin")
        self.copy_directory(self.pkgDir, self.gorootDir / "pkg")
        self.copy_directory(self.makeDir, self.gorootDir / "src")

        # Refresh the link in sdk/bin
        self.deleteFile(self.installDir / "bin" / "go")
        self.createSymlink(self.gorootDir / "bin" / "go", self.installDir / "bin" / "go")

    def run_tests(self):
        cmd = "bash run.bash --no-rebuild".split()
        self.run_cmd(cmd, cwd=self.makeDir)

