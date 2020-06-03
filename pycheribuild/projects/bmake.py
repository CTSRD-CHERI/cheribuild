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
from .project import AutotoolsProject, DefaultInstallDir, GitRepository
from ..utils import OSInfo


class BuildBmake(AutotoolsProject):
    repository = GitRepository("https://github.com/arichardson/bmake.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def configure(self, **kwargs):
        self.configureArgs.append("--with-default-sys-path=" + str(self.installDir / "share/mk"))
        self.configureArgs.append("--with-machine=amd64")
        # self.configureArgs.append("--with-force-machine=amd64")
        # self.configureArgs.append("--with-machine_arch=amd64")
        if not OSInfo.IS_FREEBSD:
            self.configureArgs.append("--without-meta")
            self.configureArgs.append("--without-filemon")
        super().configure()

    def compile(self, **kwargs):
        self.run_with_logfile(["sh", self.buildDir / "make-bootstrap.sh"], cwd=self.buildDir, logfile_name="build.log")
