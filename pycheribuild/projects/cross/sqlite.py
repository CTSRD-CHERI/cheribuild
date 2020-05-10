#
# Copyright (c) 2017 Alex Richardson
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
from .crosscompileproject import *
from .qt5 import BuildQtWebkit
from ...utils import runCmd, IS_FREEBSD


class BuildSQLite(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/sqlite.git",
                               default_branch="branch-3.19", force_branch=True)
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.SYSROOT

    def linkage(self):
        if not self.compiling_for_host() and BuildQtWebkit.get_instance(self, self.config).force_static_linkage:
            return Linkage.STATIC  # make sure it works with webkit
        return super().linkage()

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if not self.compiling_for_host():
            self.configureEnvironment["BUILD_CC"] = self.host_CC
            # self.configureEnvironment["BUILD_CFLAGS"] = "-integrated-as"
            self.configureArgs.extend([
                "--disable-amalgamation",  # don't concatenate sources
                "--disable-tcl",
                "--disable-load-extension",
            ])
        self.cross_warning_flags += ["-Wno-error=cheri-capability-misuse"]

        if not self.compiling_for_host() or IS_FREEBSD:
            self.configureArgs.append("--disable-editline")
            # not sure if needed:
            self.configureArgs.append("--disable-readline")

    def compile(self, **kwargs):
        # create the required metadata
        runCmd(self.sourceDir / "create-fossil-manifest", cwd=self.sourceDir)
        super().compile()

    def install(self, **kwargs):
        super().install()

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()


class BuildFettSQLite(BuildSQLite):
    target = "fett-sqlite"
    project_name = "fett-sqlite"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sqlite.git",
                               default_branch="fett")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configureArgs.extend(["--enable-fts3"])
