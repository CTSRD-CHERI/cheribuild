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
import datetime
import os
import subprocess
import typing
from pathlib import Path

from .cross.cheribsd import BuildCHERIBSD
from .project import (CheriConfig, CMakeProject, DefaultInstallDir, GitRepository, SimpleProject,
                      TargetAliasWithDependencies)
from ..targets import target_manager
from ..utils import include_local_file, OSInfo, set_env, statusUpdate


class BuildCheriBSDSdk(TargetAliasWithDependencies):
    target = "cheribsd-sdk"
    dependencies = ["freestanding-sdk", "cheribsd-mips-hybrid"]
    is_sdk_target = True


class BuildSdk(TargetAliasWithDependencies):
    target = "sdk"
    dependencies = ["cheribsd-sdk"]
    is_sdk_target = True


class BuildCheriCompressedCaps(CMakeProject):
    target = "cheri-compressed-cap"
    project_name = "cheri-compressed-cap"
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheri-compressed-cap.git")
    native_install_dir = DefaultInstallDir.CHERI_SDK


class BuildFreestandingSdk(SimpleProject):
    target = "freestanding-sdk"
    dependencies = ["llvm-native", "qemu", "gdb-native"]
    dependenciesMustBeBuilt = True
    is_sdk_target = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if OSInfo.IS_FREEBSD:
            self.add_required_system_tool("ar")
        self.cheribsdBuildRoot = None

    def installCMakeConfig(self):
        date = datetime.datetime.now()
        micro_version = str(date.year) + str(date.month) + str(date.day)
        version_file = include_local_file("files/CheriSDKConfigVersion.cmake.in")
        version_file.replace("@SDK_BUILD_DATE@", micro_version)
        config_file = include_local_file("files/CheriSDKConfig.cmake")
        cmake_config_dir = self.config.cheri_sdk_dir / "share/cmake/CheriSDK"
        self.makedirs(cmake_config_dir)
        self.write_file(cmake_config_dir / "CheriSDKConfig.cmake", config_file, overwrite=True)
        self.write_file(cmake_config_dir / "CheriSDKConfigVersion.cmake", version_file, overwrite=True)

    def buildCheridis(self):
        # Compile the cheridis helper (TODO: add it to the LLVM repo instead?)
        cheridis_src = include_local_file("files/cheridis.c")
        self.makedirs(self.config.cheri_sdk_bindir)
        self.run_cmd("cc", "-DLLVM_PATH=\"%s/\"" % str(self.config.cheri_sdk_bindir), "-x", "c", "-",
               "-o", self.config.cheri_sdk_bindir / "cheridis", input=cheridis_src)

    def process(self):
        self.installCMakeConfig()
        self.buildCheridis()

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
                self.install_file(CHERITOOLS_OBJ / tool, self.config.cheri_sdk_bindir / tool, force=True)
            elif (CHERIBOOTSTRAPTOOLS_OBJ / tool).is_file():
                self.install_file(CHERIBOOTSTRAPTOOLS_OBJ / tool, self.config.cheri_sdk_bindir / tool, force=True)
            else:
                self.fatal("Required tool", tool, "is missing!")

        # We should no longer need GCC:
        return
        # GCC wants the cc1 and cc1plus tools to be in the directory specified by -B.
        # We must make this the same directory that contains ld for linking and
        # compiling to both work...
        # for tool in ("cc1", "cc1plus"):
        #    self.install_file(CHERILIBEXEC_OBJ / tool, self.config.cheri_sdk_bindir / tool, force=True)


# Binutils now just builds LLVM since we don't need GNU binutils or Elftoolchain any more
target_manager.add_target_alias("binutils", "llvm-native")


class BuildBaremetalSdk(TargetAliasWithDependencies):
    target = "baremetal-sdk"  # FIXME: this should be a multi-arch target (or just build both probably)
    dependencies = ["freestanding-sdk", "newlib-baremetal-mips", "libcxx-baremetal-mips"]  # TODO: add libcxx-baremetal-cheri
    is_sdk_target = True


class StartCheriSDKShell(SimpleProject):
    target = "sdk-shell"

    def process(self):
        new_man_path = str(self.config.cheri_sdk_dir / "share/man") + ":" + os.getenv("MANPATH", "") + ":"
        new_path = str(self.config.cheri_sdk_bindir) + ":" + str(self.config.dollarPathWithOtherTools)
        shell = os.getenv("SHELL", "/bin/sh")
        with set_env(MANPATH=new_man_path, PATH=new_path):
            statusUpdate("Starting CHERI SDK shell... ", end="")
            try:
                self.run_cmd(shell)
            except subprocess.CalledProcessError as e:
                if e.returncode == 130:
                    return  # User pressed Ctrl+D to exit shell, don't print an error
                raise
