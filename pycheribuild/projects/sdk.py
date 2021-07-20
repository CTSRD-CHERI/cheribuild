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

from .cross.cheribsd import BuildCHERIBSD
from .project import (CheriConfig, CMakeProject, CPUArchitecture, DefaultInstallDir, GitRepository, SimpleProject,
                      TargetAliasWithDependencies)
from ..targets import target_manager
from ..utils import classproperty, include_local_file


class BuildCheriBSDSdk(TargetAliasWithDependencies):
    target = "cheribsd-sdk"
    is_sdk_target = True

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "list[str]":
        if cls.get_crosscompile_target(config).is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            deps = ["freestanding-morello-sdk"]
        else:
            deps = ["freestanding-cheri-sdk"]
        return deps + ["cheribsd"]

    @classproperty
    def supported_architectures(self):
        return BuildCHERIBSD.supported_architectures


class BuildSdk(TargetAliasWithDependencies):
    target = "sdk"
    dependencies = ["cheribsd-sdk"]
    is_sdk_target = True

    @classproperty
    def supported_architectures(self):
        return BuildCheriBSDSdk.supported_architectures


class BuildCheriCompressedCaps(CMakeProject):
    target = "cheri-compressed-cap"
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheri-compressed-cap.git")
    native_install_dir = DefaultInstallDir.CHERI_SDK


class BuildFreestandingSdk(SimpleProject):
    target = "freestanding-cheri-sdk"
    dependencies = ["llvm-native", "qemu", "gdb-native"]
    dependencies_must_be_built = True
    is_sdk_target = True

    def install_cmake_config(self):
        date = datetime.datetime.now()
        micro_version = str(date.year) + str(date.month) + str(date.day)
        version_file = include_local_file("files/CheriSDKConfigVersion.cmake.in")
        version_file.replace("@SDK_BUILD_DATE@", micro_version)
        config_file = include_local_file("files/CheriSDKConfig.cmake")
        cmake_config_dir = self.config.cheri_sdk_dir / "share/cmake/CheriSDK"
        self.makedirs(cmake_config_dir)
        self.write_file(cmake_config_dir / "CheriSDKConfig.cmake", config_file, overwrite=True)
        self.write_file(cmake_config_dir / "CheriSDKConfigVersion.cmake", version_file, overwrite=True)

    def build_cheridis(self):
        # Compile the cheridis helper (TODO: add it to the LLVM repo instead?)
        cheridis_src = include_local_file("files/cheridis.c")
        self.makedirs(self.config.cheri_sdk_bindir)
        self.run_cmd("cc", "-DLLVM_PATH=\"%s/\"" % str(self.config.cheri_sdk_bindir), "-x", "c", "-",
                     "-o", self.config.cheri_sdk_bindir / "cheridis", input=cheridis_src)

    def process(self):
        self.install_cmake_config()
        self.build_cheridis()


class BuildFreestandingMorelloSdk(TargetAliasWithDependencies):
    target = "freestanding-morello-sdk"
    dependencies = ["morello-llvm-native", "morello-qemu"]  # "morello-gdb-native" does not exist
    dependencies_must_be_built = True
    is_sdk_target = True


target_manager.add_target_alias("freestanding-sdk", "freestanding-cheri-sdk", deprecated=True)

# Binutils now just builds LLVM since we don't need GNU binutils or Elftoolchain any more
target_manager.add_target_alias("binutils", "llvm-native")


class BuildBaremetalSdk(TargetAliasWithDependencies):
    target = "baremetal-sdk"  # FIXME: this should be a multi-arch target (or just build both probably)
    dependencies = ["freestanding-cheri-sdk", "newlib-baremetal-mips64",
                    "libcxx-baremetal-mips64"]  # TODO: add libcxx-baremetal-cheri
    is_sdk_target = True


class StartCheriSDKShell(SimpleProject):
    target = "sdk-shell"

    def process(self):
        new_man_path = str(self.config.cheri_sdk_dir / "share/man") + ":" + os.getenv("MANPATH", "") + ":"
        new_path = str(self.config.cheri_sdk_bindir) + ":" + str(self.config.dollar_path_with_other_tools)
        shell = os.getenv("SHELL", "/bin/sh")
        with self.set_env(MANPATH=new_man_path, PATH=new_path):
            self.info("Starting CHERI SDK shell... ", end="")
            try:
                self.run_cmd(shell)
            except subprocess.CalledProcessError as e:
                if e.returncode == 130:
                    return  # User pressed Ctrl+D to exit shell, don't print an error
                raise
