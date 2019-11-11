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
from .project import *
from ..utils import *

from pathlib import Path


class BuildMakefsOnLinux(Project):
    project_name = "makefs-linux"
    repository = GitRepository("https://github.com/Engil/makefs.git")
    defaultInstallDir = Project._installToBootstrapTools
    build_in_source_dir = True  # out of source builds don't work
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if IS_LINUX:
            self._addRequiredSystemHeader("bsd/bsd.h")
        if not IS_FREEBSD:
            self.addRequiredSystemTool("bmake", homebrew="bmake", cheribuild_target="bmake")

    def check_system_dependencies(self):
        if IS_FREEBSD:
            return  # not need on FreeBSD
        super().check_system_dependencies()
        if not Path("/usr/include/bsd/bsd.h").is_file():
            self.dependencyError("libbsd must be installed to compile makefs on linux")

    def compile(self, **kwargs):
        # Doesn't have an all target
        self.runMake(make_target="", parallel=False)

    def clean(self):
        self.deleteFile(self.sourceDir / "builddir/.build_stamp")
        self.clean_directory(self.sourceDir / "builddir")
        return super().clean()

    def install(self, **kwargs):
        self.installFile(self.sourceDir / "builddir/usr.sbin/makefs/makefs", self.installDir / "bin/makefs")

    def process(self):
        if IS_FREEBSD:
            statusUpdate("Skipping makefs as this is only needed on Linux hosts")
        else:
            super().process()
