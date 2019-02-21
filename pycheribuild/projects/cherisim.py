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
from ..utils import OSInfo, commandline_to_str


class BuildCheriSim(Project):
    target = "cheri-sim"
    projectName = "cheri-sim"
    repository = GitRepository("https://please/set/source/dir/to/ctsrd-svn/cheri/trunk")
    defaultInstallDir = Project._installToSDK
    build_in_source_dir = True      # Needs to build in the source dir
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # TODO: move this to project
        self._addRequiredSystemTool("dtc", apt="device-tree-compiler")
        self._addRequiredSystemHeader("mpfr.h", apt="libmpfr-dev")
        self.make_args.set(COP1="1" if self.build_fpu else "0")
        if self.build_cheri:
            if self.config.cheriBits == 128:
                self.make_args.set(CAP128="1")
            else:
                self.make_args.set(CAP="1")
        self.make_args.set(NOPRINTS="1") # This massively speeds up the simulator

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.build_fpu = cls.addBoolOption("fpu", default=True, help="include the FPU code")
        cls.build_cheri = cls.addBoolOption("cheri", default=True, help="include the CHERI code in the simulator. If false build BERI")

    def clean(self):
        self.runMake("clean", parallel=False, cwd=self.sourceDir)
        return None

    def update(self):
        pass

    def compile(self, **kwargs):
        self.runShellScript("source setup.sh && " + commandline_to_str(self.get_make_commandline("sim", parallel=False)),
                            cwd=self.sourceDir, shell="bash")
        pass

    def install(self, **kwargs):
        pass

    def process(self):
        if not (self.sourceDir / "setup.sh").exists():
            self.fatal("Could not find setup.sh, please set --cheri-sim/source-directory")
        if OSInfo.isUbuntu() and not Path("/usr/lib/x86_64-linux-gnu/libgmp.so.3").exists():
            # BSC needs libgmp.so.3
            self.fatal("libgmp.so.3 is needed to run BSC",
                       fixitHint="Creating a symlink to /usr/lib/x86_64-linux-gnu/libgmp.so.10 seems to work.\n"
                                 "\t\tTry running `sudo ln -s libgmp.so.10 /usr/lib/x86_64-linux-gnu/libgmp.so.3`")
        super().process()


