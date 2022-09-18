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
from .cmake_project import CMakeProject
from .project import CheriConfig, DefaultInstallDir, GitRepository


class BuildCheriTrace(CMakeProject):
    dependencies = ["llvm"]
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheritrace.git")
    native_install_dir = DefaultInstallDir.CHERI_SDK

    @classmethod
    def setup_config_options(cls):
        super().setup_config_options()
        cls.include_python_bindings = cls.add_bool_option("python-bindings")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.llvm_config_path = self.config.cheri_sdk_bindir / "llvm-config"

    def configure(self):
        if not self.llvm_config_path.is_file():
            self.dependency_error("Could not find llvm-config from CHERI LLVM.", cheribuild_target="llvm")
        self.add_cmake_options(
            LLVM_CONFIG=self.llvm_config_path,
            PYTHON_BINDINGS=self.include_python_bindings
            )
        super().configure()
