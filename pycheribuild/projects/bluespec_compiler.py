#
# Copyright (c) 2016 Alex Richardson
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
from .project import DefaultInstallDir, GitRepository, MakeCommandKind, Project
from .simple_project import TargetAliasWithDependencies
from ..utils import OSInfo


class BuildBluespecCompiler(Project):
    target = "bluespec-compiler"
    default_directory_basename = "bsc"
    repository = GitRepository("https://github.com/B-Lang-org/bsc.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    build_in_source_dir = True
    make_kind = MakeCommandKind.GnuMake
    needs_full_history = True

    def check_system_dependencies(self):
        super().check_system_dependencies()
        self.check_required_system_tool("ghc", apt="ghc", homebrew="ghc")
        self.check_required_system_tool("cabal", apt="cabal-install", homebrew="cabal-install")
        self.check_required_system_tool("gperf", homebrew="gperf", apt="gperf")
        self.check_required_pkg_config("tcl", apt="tcl")
        for i in ("autoconf", "bison", "flex"):
            self.check_required_system_tool(i, homebrew=i)

    def clean(self):
        if self.source_dir.exists():
            self.run_make("clean", cwd=self.source_dir)

    def setup(self):
        super().setup()
        # The build tends to fail if we set GHCJOBS to a value larger than 8
        self.make_args.set(PREFIX=self.install_dir, GHCJOBS=min(self.config.make_jobs, 8))

    def compile(self, **kwargs):
        self.info("Compilation happens as part of install")

    def install(self, **kwargs):
        try:
            self.run_cmd("cabal", "v1-update")
            self.run_cmd("cabal", "v1-install", "regex-compat", "syb", "old-time", "split", cwd=self.source_dir)
            self.run_make("install-src", cwd=self.source_dir)
        except Exception:
            self.info(
                "Compilation failed. If it complains about missing packages try running:\n"
                "\tcabal install regex-compat syb old-time split\n"
                "If this doesn't fix the issue `v1-install` instead of `install` (e.g. macOS).",
            )
            if OSInfo.IS_MAC:
                self.info(
                    "Alternatively, try running:",
                    self.source_dir / ".github/workflows/install_dependencies_macos.sh",
                )
            elif OSInfo.is_ubuntu():
                self.info(
                    "Alternatively, try running:",
                    self.source_dir / ".github/workflows/install_dependencies_ubuntu.sh",
                )
            raise

    def run_tests(self) -> None:
        self.run_cmd("check-smoke")


class BuildBluespecLibraries(Project):
    target = "bluespec-libraries"
    dependencies = ("bluespec-compiler",)
    default_directory_basename = "bsc-contrib"
    repository = GitRepository("https://github.com/B-Lang-org/bsc-contrib.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    build_in_source_dir = True
    make_kind = MakeCommandKind.GnuMake

    def compile(self, **kwargs):
        self.make_args.set(PREFIX=self.install_dir)
        self.run_make("all")

    def clean(self):
        self.make_args.set(PREFIX=self.install_dir)
        self.run_make("full_clean", cwd=self.source_dir)


class BuildBluespec(TargetAliasWithDependencies):
    target = "bluespec"
    dependencies = ("bluespec-libraries",)
