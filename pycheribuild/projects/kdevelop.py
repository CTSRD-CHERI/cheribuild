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
from .cross.llvm import BuildCheriLLVM
from .project import BuildType, DefaultInstallDir, GitRepository
from .simple_project import SimpleProject


# doesn't seem to be part of distro packages
class BuildLibKompareDiff2(CMakeProject):
    default_build_type = BuildType.DEBUG
    repository = GitRepository("git://anongit.kde.org/libkomparediff2.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS


class BuildKDevplatform(CMakeProject):
    dependencies = ("libkomparediff2",)
    default_build_type = BuildType.DEBUG
    repository = GitRepository("https://github.com/arichardson/kdevplatform.git", default_branch="cheri")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def setup(self):
        super().setup()
        self.add_cmake_options(BUILD_git=False)


class BuildKDevelop(CMakeProject):
    dependencies = ("kdevplatform", "llvm")
    default_build_type = BuildType.DEBUG
    repository = GitRepository("https://github.com/arichardson/kdevelop.git", default_branch="cheri")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    supported_architectures = (BuildCheriLLVM.default_architecture,)

    def setup(self):
        super().setup()
        # Tell kdevelop to use the CHERI clang and install the wrapper script that sets the right environment variables
        self.add_cmake_options(LLVM_ROOT=self.config.cheri_sdk_dir, INSTALL_KDEVELOP_LAUNCH_WRAPPER=True)


class StartKDevelop(SimpleProject):
    target = "run-kdevelop"
    dependencies = ("kdevelop",)

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool(
            "cmake", default="cmake", homebrew="cmake", zypper="cmake", apt="cmake", freebsd="cmake"
        )
        self.check_required_system_tool("qtpaths")

    def process(self):
        kdevelop_binary = BuildKDevelop.get_install_dir(self) / "bin/start-kdevelop.py"
        if not kdevelop_binary.exists():
            self.dependency_error("KDevelop is missing:", kdevelop_binary, cheribuild_target="kdevelop")
        self.run_cmd(kdevelop_binary, "--ps")
