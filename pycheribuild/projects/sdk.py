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

from .cmake_project import CMakeProject
from .cross.cheribsd import BuildCHERIBSD
from .project import CheriConfig, CPUArchitecture, DefaultInstallDir, GitRepository
from .simple_project import SimpleProject, TargetAliasWithDependencies
from ..config.target_info import CrossCompileTarget
from ..utils import classproperty, include_local_file


class BuildCheriBSDSdk(TargetAliasWithDependencies):
    target = "cheribsd-sdk"
    is_sdk_target = True

    @classmethod
    def dependencies(cls, _: CheriConfig) -> "tuple[str, ...]":
        if cls.get_crosscompile_target().is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            deps = ("freestanding-morello-sdk",)
        else:
            deps = ("freestanding-cheri-sdk",)
        return (*deps, "cheribsd")

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return BuildCHERIBSD.supported_architectures


class BuildSdk(TargetAliasWithDependencies):
    target = "sdk"
    dependencies = ("cheribsd-sdk",)
    is_sdk_target = True

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return BuildCheriBSDSdk.supported_architectures


class BuildCheriCompressedCaps(CMakeProject):
    target = "cheri-compressed-cap"
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheri-compressed-cap.git")
    native_install_dir = DefaultInstallDir.CHERI_SDK


class BuildFreestandingSdk(SimpleProject):
    target = "freestanding-cheri-sdk"
    dependencies = ("llvm-native", "qemu", "gdb-native")
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
    dependencies = ("morello-llvm-native", "qemu")  # "morello-gdb-native" does not exist
    dependencies_must_be_built = True
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
