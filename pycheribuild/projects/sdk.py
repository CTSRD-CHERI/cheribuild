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
import os
import subprocess
import datetime
import shutil

from .cross.cheribsd import BuildCHERIBSD
from .project import *
from ..utils import *

from pathlib import Path


class BuildCheriBSDSdk(TargetAliasWithDependencies):
    target = "cheribsd-sdk"
    dependencies = ["freestanding-sdk", "cheribsd-sysroot-cheri"]
    is_sdk_target = True


class BuildSdk(TargetAliasWithDependencies):
    target = "sdk"
    dependencies = ["cheribsd-sdk"]
    is_sdk_target = True


class BuildCheriCompressedCaps(CMakeProject):
    target = "cheri-compressed-cap"
    projectName = "cheri-compressed-cap"
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheri-compressed-cap.git")
    defaultInstallDir = CMakeProject._installToSDK


class BuildFreestandingSdk(SimpleProject):
    target = "freestanding-sdk"
    dependencies = ["llvm", "qemu", "gdb-native"]  # TODO: should we add ld.bfd?
    dependenciesMustBeBuilt = True
    is_sdk_target = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if IS_FREEBSD:
            self.addRequiredSystemTool("ar")
        self.cheribsdBuildRoot = None

    def installCMakeConfig(self):
        date = datetime.datetime.now()
        microVersion = str(date.year) + str(date.month) + str(date.day)
        versionFile = includeLocalFile("files/CheriSDKConfigVersion.cmake.in")
        versionFile.replace("@SDK_BUILD_DATE@", microVersion)
        configFile = includeLocalFile("files/CheriSDKConfig.cmake")
        cmakeConfigDir = self.config.sdkDir / "share/cmake/CheriSDK"
        self.makedirs(cmakeConfigDir)
        self.writeFile(cmakeConfigDir / "CheriSDKConfig.cmake", configFile, overwrite=True)
        self.writeFile(cmakeConfigDir / "CheriSDKConfigVersion.cmake", versionFile, overwrite=True)

    def buildCheridis(self):
        # Compile the cheridis helper (TODO: add it to the LLVM repo instead?)
        cheridisSrc = includeLocalFile("files/cheridis.c")
        self.makedirs(self.config.sdkDir / "bin")
        runCmd("cc", "-DLLVM_PATH=\"%s/\"" % str(self.config.sdkDir / "bin"), "-x", "c", "-",
               "-o", self.config.sdkDir / "bin/cheridis", input=cheridisSrc)

    def process(self):
        self.installCMakeConfig()
        self.buildCheridis()
        if False and IS_FREEBSD:
            binutilsBinaries = "addr2line as brandelf nm objcopy objdump size strings strip".split()
            toolsToSymlink = binutilsBinaries
            sdkBinDir = self.config.sdkDir / "bin"
            # When building on FreeBSD we also copy the MIPS GCC and related tools
            self.copyCrossToolsFromCheriBSD(binutilsBinaries)
            for tool in set(toolsToSymlink):
                self.createBuildtoolTargetSymlinks(sdkBinDir / tool)
            # For some reason CheriBSD does not build a cross ar, let's symlink the system one to the SDK bindir
            runCmd("ln", "-fsn", shutil.which("ar"), sdkBinDir / "ar",
                   cwd=self.config.sdkDir / "bin", print_verbose_only=True)
            self.createBuildtoolTargetSymlinks(sdkBinDir / "ar")
            # install ld as ld.bfd and add a symlink
            self.installFile(self.cheribsdBuildRoot / "tmp/usr/bin/ld", sdkBinDir / "ld.bfd")
            self.createBuildtoolTargetSymlinks(sdkBinDir / "ld.bfd")
            # TODO: should we really be installing this as unprefixed ld?
            self.createSymlink(sdkBinDir / "ld.bfd", sdkBinDir / "ld")
            self.createBuildtoolTargetSymlinks(sdkBinDir / "ld")
            # we should no longer need GCC:
            return
            # Copy GCC and G++ for MIPS64:
            # for tool in ("gcc", "g++", "gcov"):
            #     self.installFile(self.cheribsdBuildRoot / "tmp/usr/bin" / tool,
            #                      sdkBinDir / ("mips64-unknown-freebsd-" + tool), force=True)
            #     # If we install these tools unprefixed we will break everything!
            #     if (sdkBinDir / tool).exists():
            #         (sdkBinDir / tool).unlink()

    def copyCrossToolsFromCheriBSD(self, binutilsBinaries: "typing.List[str]"):
        # if we pass a string starting with a slash to Path() it will reset to that absolute path
        # luckily we have to prepend mips.mips64, so it works out fine
        # expands to e.g. /home/alr48/cheri/output/cheribsd-obj/mips.mips64/home/alr48/cheri/cheribsd
        possibleBuildRoots = [Path(BuildCHERIBSD.buildDir, "mips.mips64" + path) for path in
                              (str(BuildCHERIBSD.sourceDir), os.path.realpath(str(BuildCHERIBSD.sourceDir)))]
        for directory in possibleBuildRoots:
            if directory.exists():
                self.cheribsdBuildRoot = directory
        if not self.cheribsdBuildRoot:
            self.fatal("CheriBSD build directory is missing! (Tried", possibleBuildRoots, ")")
        CHERITOOLS_OBJ = self.cheribsdBuildRoot / "tmp/usr/bin/"
        CHERIBOOTSTRAPTOOLS_OBJ = self.cheribsdBuildRoot / "tmp/legacy/usr/bin/"
        CHERILIBEXEC_OBJ = self.cheribsdBuildRoot / "tmp/usr/libexec/"
        for i in (CHERIBOOTSTRAPTOOLS_OBJ, CHERITOOLS_OBJ, CHERITOOLS_OBJ, BuildCHERIBSD.rootfsDir(self, self.config)):
            if not i.is_dir():
                self.fatal("Directory", i, "is missing!")

        # install tools:
        for tool in binutilsBinaries:
            if (CHERITOOLS_OBJ / tool).is_file():
                self.installFile(CHERITOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)
            elif (CHERIBOOTSTRAPTOOLS_OBJ / tool).is_file():
                self.installFile(CHERIBOOTSTRAPTOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)
            else:
                self.fatal("Required tool", tool, "is missing!")

        # We should no longer need GCC:
        return
        # GCC wants the cc1 and cc1plus tools to be in the directory specified by -B.
        # We must make this the same directory that contains ld for linking and
        # compiling to both work...
        # for tool in ("cc1", "cc1plus"):
        #    self.installFile(CHERILIBEXEC_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)


# Replace the old binutils target by on that builds the required tools from GNU binutils and elftoolchain
class BuildBinutils(TargetAlias):
    target = "binutils"
    # LLD should be usable for all cases now, now longer install the ancient gnu ld
    # After the next merge we can probably also remove elftoolchain (however, for consistency
    # with buildworld we should probably keep it?)
    dependencies = ["elftoolchain"]
    is_sdk_target = True


class BuildBaremetalSdk(TargetAliasWithDependencies):
    target = "baremetal-sdk"  # FIXME: this should be a multi-arch target (or just build both probably)
    dependencies = ["freestanding-sdk", "libcxx-baremetal-mips"]  # TODO: add libcxx-baremetal-cheri
    is_sdk_target = True


class StartCheriSDKShell(SimpleProject):
    target = "sdk-shell"

    def process(self):
        newManPath = str(self.config.sdkDir / "share/man") + ":" + os.getenv("MANPATH", "") + ":"
        newPath = str(self.config.sdkDir / "bin") + ":" + str(self.config.dollarPathWithOtherTools)
        shell = os.getenv("SHELL", "/bin/sh")
        with setEnv(MANPATH=newManPath, PATH=newPath):
            statusUpdate("Starting CHERI SDK shell... ", end="")
            try:
                runCmd(shell)
            except subprocess.CalledProcessError as e:
                if e.returncode == 130:
                    return  # User pressed Ctrl+D to exit shell, don't print an error
                raise
