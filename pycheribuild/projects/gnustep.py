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
from ..project import Project, AutotoolsProject, CMakeProject
from ..utils import *

import shutil
from pathlib import Path

# http://wiki.gnustep.org/index.php/GNUstep_under_Ubuntu_Linux

class BuildLibObjC2(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir,
                         gitUrl="https://github.com/gnustep/libobjc2.git")
        # self.gitBranch = "1.8.1"  # track the stable release branch
        self.configureArgs.extend([
            "-DCMAKE_C_COMPILER=clang",
            "-DCMAKE_CXX_COMPILER=clang++",
            "-DCMAKE_ASM_COMPILER=clang",
            "-DCMAKE_ASM_COMPILER_ID=Clang",  # For some reason CMake doesn't detect the ASM compiler ID for clang
            "-DCMAKE_ASM_FLAGS=-c",  # required according to docs when using clang as ASM compiler
            # "-DLLVM_OPTS=OFF",  # For now don't build the LLVM plugin, it will break when clang is updated
            "-DTESTS=OFF",
            # Don't install in the location that gnustep-config says, it might be a directory that is not writable by
            # the current user:
            "-DGNUSTEP_INSTALL_TYPE=NONE",
        ])
        # TODO: require libdispatch?
        self._addRequiredSystemTool("clang")
        self._addRequiredSystemTool("clang++")


class BuildGnuStep_Make(AutotoolsProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir,
                         gitUrl="https://github.com/gnustep/make.git")
        self.configureArgs.extend([
            "--with-layout=fhs",  # more traditional file system layout
            "--with-library-combo=ng-gnu-gnu",  # use the new libobjc2 that supports ARC
            "--enable-objc-nonfragile-abi",  # not sure if required but given in install guide
            "CC=clang",  # TODO: find the most recent clang
            "CXX=clang++",  # TODO: find the most recent clang++
            "LDFLAGS=-Wl,-rpath," + str(self.installDir / "lib")  # add rpath, otherwise everything breaks
        ])


# FIXME: do we need to source Makefiles/GNUstep.sh before building?
class GnuStepModule(AutotoolsProject):
    doNotAddToTargets = True

    def __init__(self, config: CheriConfig, *args, moduleName: str, **kwargs):
        super().__init__(config, installDir=config.otherToolsDir,
                         gitUrl="https://github.com/gnustep/" + moduleName + " .git", *args, **kwargs)
        self.buildDir = self.sourceDir  # out of source builds don't seem to work!

    def configure(self):
        if not shutil.which("gnustep-config"):
            self.dependencyError("gnustep-config should have been installed in the last build step!")
        gnustepLibdir = runCmd("gnustep-config", "--variable=GNUSTEP_SYSTEM_LIBRARIES",
                               captureOutput=True, printVerboseOnly=True).stdout.strip().decode("utf-8")
        # Just to confirm that we have set up the -rpath flag correctly
        expectedLibdir = self.installDir / "lib"
        if not expectedLibdir.is_dir():
            fatalError("Expected gnustep libdir", expectedLibdir, "doesn't exist")
        if not Path(gnustepLibdir).is_dir():
            fatalError("GNUSTEP_SYSTEM_LIBRARIES directory", gnustepLibdir, "doesn't exist")
        if Path(gnustepLibdir).resolve() != expectedLibdir.resolve():
            fatalError("GNUSTEP_SYSTEM_LIBRARIES was", gnustepLibdir, "but expected ", expectedLibdir)

        # print(coloured(AnsiColour.green, "LDFLAGS=-L" + gnustepLibdir))
        # TODO: what about spaces??
        # self.configureArgs.append("LDFLAGS=-L" + gnustepLibdir + " -Wl,-rpath," + gnustepLibdir)
        super().configure()


class BuildGnuStep_Base(GnuStepModule):
    def __init__(self, config: CheriConfig):
        super().__init__(config, moduleName="base")
        self.configureArgs.extend([
            "--disable-mixedabi",
            # TODO: "--enable-libdispatch",
            # "--with-config-file=" + str(self.installDir / "etc/GNUStep/GNUStep.conf")
        ])


class BuildGnuStep_Gui(GnuStepModule):
    def __init__(self, config: CheriConfig):
        super().__init__(config, moduleName="gui")

    def checkSystemDependencies(self):
        # TODO check that libjpeg62-devel is not installed on opensuse, must use libjpeg8-devel
        # rpm -q libjpeg62-devel must not return 0
        super().checkSystemDependencies()


class BuildGnuStep_Back(GnuStepModule):
    def __init__(self, config: CheriConfig):
        super().__init__(config, moduleName="back")
        self.configureArgs.append("--enable-graphics=cairo")


# TODO: add MultiProject or something similar to project.py
class BuildGnuStep(Project):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir)
        self.subprojects = [
            BuildLibObjC2(config),
            BuildGnuStep_Make(config),
            BuildGnuStep_Base(config),
            BuildGnuStep_Gui(config),
            BuildGnuStep_Back(config),
        ]

    def checkSystemDependencies(self):
        for p in self.subprojects:
            p.checkSystemDependencies()

    def process(self):
        for p in self.subprojects:
            p.process()
