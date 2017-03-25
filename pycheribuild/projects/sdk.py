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
import sys
import shutil

from .cheribsd import BuildCHERIBSD
from ..project import Project, PseudoTarget, CMakeProject, SimpleProject
from ..utils import *

from pathlib import Path


class BuildCheriBSDSdk(PseudoTarget):
    target = "cheribsd-sdk"
    dependencies = ["freestanding-sdk", "cheribsd-sysroot", "cheri-buildsystem-wrappers"]
    if IS_LINUX:
        dependencies.append("awk")  # also add BSD compatible AWK to the SDK


class BuildSdk(PseudoTarget):
    target = "sdk"
    dependencies = ["cheribsd-sdk"] if IS_FREEBSD else ["freestanding-sdk"]


class BuildFreestandingSdk(SimpleProject):
    target = "freestanding-sdk"
    dependencies = ["llvm", "cheribsd", "qemu"] if IS_FREEBSD else ["binutils", "llvm", "qemu"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if IS_FREEBSD:
            self._addRequiredSystemTool("ar")
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
        if IS_FREEBSD:
            binutilsBinaries = "addr2line as brandelf nm objcopy objdump size strings strip".split()
            toolsToSymlink = binutilsBinaries
            sdkBinDir = self.config.sdkDir / "bin"
            # When building on FreeBSD we also copy the MIPS GCC and related tools
            self.copyCrossToolsFromCheriBSD(binutilsBinaries)
            for tool in set(toolsToSymlink):
                self.createBuildtoolTargetSymlinks(sdkBinDir / tool)
            # For some reason CheriBSD does not build a cross ar, let's symlink the system one to the SDK bindir
            runCmd("ln", "-fsn", shutil.which("ar"), sdkBinDir / "ar",
                   cwd=self.config.sdkDir / "bin", printVerboseOnly=True)
            self.createBuildtoolTargetSymlinks(sdkBinDir / "ar")
            # install ld as ld.bfd and add a symlink
            self.installFile(self.cheribsdBuildRoot / "tmp/usr/bin/ld", sdkBinDir / "ld.bfd")
            self.createBuildtoolTargetSymlinks(sdkBinDir / "ld.bfd")
            # TODO: should we really be installing this as unprefixed ld?
            self.createSymlink(sdkBinDir / "ld.bfd", sdkBinDir / "ld")
            self.createBuildtoolTargetSymlinks(sdkBinDir / "ld")
            # Copy GCC and G++ for MIPS64:
            for tool in ("gcc", "g++", "gcov"):
                self.installFile(self.cheribsdBuildRoot / "tmp/usr/bin" / tool,
                                 sdkBinDir / ("mips64-unknown-freebsd-" + tool), force=True)
                # If we install these tools unprefixed we will break everything!
                if (sdkBinDir / tool).exists():
                    (sdkBinDir / tool).unlink()

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
            fatalError("CheriBSD build directory is missing! (Tried", possibleBuildRoots, ")")
        CHERITOOLS_OBJ = self.cheribsdBuildRoot / "tmp/usr/bin/"
        CHERIBOOTSTRAPTOOLS_OBJ = self.cheribsdBuildRoot / "tmp/legacy/usr/bin/"
        CHERILIBEXEC_OBJ = self.cheribsdBuildRoot / "tmp/usr/libexec/"
        for i in (CHERIBOOTSTRAPTOOLS_OBJ, CHERITOOLS_OBJ, CHERITOOLS_OBJ, BuildCHERIBSD.rootfsDir(self.config)):
            if not i.is_dir():
                fatalError("Directory", i, "is missing!")
                # make sdk a link to the 256 bit sdk
        if (self.config.outputRoot / "sdk").is_dir():
            # remove the old sdk directory from previous versions of this script
            runCmd("rm", "-rf", self.config.outputRoot / "sdk", printVerboseOnly=True)
        if not self.config.pretend and not (self.config.outputRoot / "sdk").exists():
            runCmd("ln", "-sf", "sdk256", "sdk", cwd=self.config.outputRoot)

        # install tools:
        for tool in binutilsBinaries:
            if (CHERITOOLS_OBJ / tool).is_file():
                self.installFile(CHERITOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)
            elif (CHERIBOOTSTRAPTOOLS_OBJ / tool).is_file():
                self.installFile(CHERIBOOTSTRAPTOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)
            else:
                fatalError("Required tool", tool, "is missing!")

        # GCC wants the cc1 and cc1plus tools to be in the directory specified by -B.
        # We must make this the same directory that contains ld for linking and
        # compiling to both work...
        for tool in ("cc1", "cc1plus"):
            self.installFile(CHERILIBEXEC_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)


class BuildCheriBsdSysroot(SimpleProject):
    target = "cheribsd-sysroot"
    dependencies = ["cheribsd"] if IS_FREEBSD else []

    def fixSymlinks(self):
        # copied from the build_sdk.sh script
        # TODO: we could do this in python as well, but this method works
        fixlinksSrc = includeLocalFile("files/fixlinks.c")
        runCmd("cc", "-x", "c", "-", "-o", self.config.sdkDir / "bin/fixlinks", input=fixlinksSrc)
        runCmd(self.config.sdkDir / "bin/fixlinks", cwd=self.config.sdkSysrootDir / "usr/lib")

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        if not IS_FREEBSD and (not self.config.freeBsdBuilderOutputPath or not self.config.freeBsdBuildMachine):
            # TODO: improve this information
            fatalError("SDK files must be copied from a FreeBSD server but configurations is missing!"
                       " See --help for more info")
            sys.exit("Cannot continue...")

    def copySysrootFromRemoteMachine(self):
        remoteSysrootPath = os.path.join(self.config.freeBsdBuilderOutputPath, self.config.sdkDirectoryName,
                                         self.config.sysrootArchiveName)
        remoteSysrootPath = self.config.freeBsdBuildMachine + ":" + remoteSysrootPath
        statusUpdate("Will build SDK on", self.config.freeBsdBuildMachine, "and copy the sysroot files from",
                     remoteSysrootPath)
        if not self.queryYesNo("Continue?"):
            return

        if not self.config.freeBsdBuilderCopyOnly:
            # build the SDK on the remote machine:
            remoteRunScript = Path(__file__).parent.resolve() / "py3-run-remote.sh"
            if not remoteRunScript.is_file():
                remoteRunScript = Path(__file__).parent.parent.parent.resolve() / "py3-run-remote.sh"
            if not remoteRunScript.is_file():
                fatalError("Could not find py3-run-remote.sh script. Should be in this directory!")
            runCmd(remoteRunScript, self.config.freeBsdBuildMachine, __file__,
                   "--cheri-bits", self.config.cheriBits,  # make sure we build for the right number of cheri bits
                   "sdk")  # run target SDK with dependencies

        # now copy the files
        self.makedirs(self.config.sdkSysrootDir)
        self.deleteFile(self.config.sdkDir / self.config.sysrootArchiveName, printVerboseOnly=True)
        runCmd("scp", remoteSysrootPath, self.config.sdkDir)
        runCmd("rm", "-rf", self.config.sdkSysrootDir)
        runCmd("tar", "xzf", self.config.sdkDir / self.config.sysrootArchiveName, cwd=self.config.sdkDir)

    def createSysroot(self):
        # we need to add include files and libraries to the sysroot directory
        self.cleanDirectory(self.config.sdkSysrootDir)  # make sure the sysroot is cleaned
        self.makedirs(self.config.sdkSysrootDir / "usr")
        # use tar+untar to copy all necessary files listed in metalog to the sysroot dir
        archiveCmd = ["tar", "cf", "-", "--include=./lib/", "--include=./usr/include/",
                      "--include=./usr/lib/", "--include=./usr/libcheri", "--include=./usr/libdata/",
                      # only pack those files that are mentioned in METALOG
                      "@METALOG"]
        printCommand(archiveCmd, cwd=BuildCHERIBSD.rootfsDir(self.config))
        if not self.config.pretend:
            with subprocess.Popen(archiveCmd, stdout=subprocess.PIPE, cwd=str(BuildCHERIBSD.rootfsDir(self.config))) as tar:
                runCmd(["tar", "xf", "-"], stdin=tar.stdout, cwd=self.config.sdkSysrootDir)
        if not (self.config.sdkSysrootDir / "lib/libc.so.7").is_file():
            fatalError(self.config.sdkSysrootDir, "is missing the libc library, install seems to have failed!")

        # fix symbolic links in the sysroot:
        print("Fixing absolute paths in symbolic links inside lib directory...")
        self.fixSymlinks()
        # create an archive to make it easier to copy the sysroot to another machine
        self.deleteFile(self.config.sdkDir / self.config.sysrootArchiveName, printVerboseOnly=True)
        runCmd("tar", "-czf", self.config.sdkDir / self.config.sysrootArchiveName, "sysroot",
               cwd=self.config.sdkDir)
        print("Successfully populated sysroot")

    def process(self):
        if IS_FREEBSD:
            self.createSysroot()
        else:
            self.copySysrootFromRemoteMachine()
        # lld expects libgcc_s and libgcc_eh to exist:
        libgcc_s = self.config.sdkDir / "sysroot/usr/libcheri/libgcc_s.a"
        libgcc_eh = self.config.sdkDir / "sysroot/usr/libcheri/libgcc_eh.a"
        for lib in (libgcc_s, libgcc_eh):
            if not lib.is_file():
                runCmd("ar", "rc", lib)


class InstallCheriBuildsystemWrappers(CMakeProject):
    projectName = "cheri-buildsystem-wrappers"
    dependencies = ["freestanding-sdk", "cheribsd-sysroot"]
    repository = "https://github.com/RichardsonAlex/cheri-buildsystem-wrappers.git"
    defaultInstallDir = Project._installToSDK
    appendCheriBitsToBuildDir = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.add_cmake_options(CHERI_SDK_BINDIR=self.config.sdkDir / "bin",
                               CHERIBSD_SYSROOT=self.config.sdkDir / "sysroot")


# Replace the old binutils target by on that builds the required tools from GNU binutils and elftoolchain
class BuildBinutils(PseudoTarget):
    target = "binutils"
    dependencies = ["gnu-binutils", "elftoolchain-binutils"] if not IS_FREEBSD else []


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
