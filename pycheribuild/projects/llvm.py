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
from pathlib import Path
from ..project import *
from ..utils import *


class BuildLLVM(CMakeProject):
    defaultInstallDir = CMakeProject._installToSDK
    appendCheriBitsToBuildDir = True
    githubBaseUrl = "https://github.com/CTSRD-CHERI/"
    repository = githubBaseUrl + "llvm.git"
    no_default_sysroot = None

    @classmethod
    def setupConfigOptions(cls, includeClangRevision=True, includeLldbRevision=False, includeLldRevision=True,
                           useDefaultSysroot=True):
        super().setupConfigOptions()

        def addToolOptions(name):
            rev = cls.addConfigOption(name + "-git-revision", kind=str, metavar="REVISION",
                                      help="The git revision for tools/" + name)
            repo = cls.addConfigOption(name + "-repository", kind=str, metavar="REPOSITORY",
                                       default=cls.githubBaseUrl + name + ".git",
                                       help="The git repository for tools/" + name)
            return repo, rev

        if useDefaultSysroot:
            cls.no_default_sysroot = cls.addBoolOption("no-default-sysroot", help="Don't set default sysroot and "
                                                       "target triple. Needed e.g. for the test suite")
        else:
            cls.no_default_sysroot = True

        cls.skip_lld = cls.addBoolOption("skip-lld", help="Don't build lld as part of the llvm target")
        if includeClangRevision:
            cls.clangRepository, cls.clangRevision = addToolOptions("clang")
        if includeLldRevision:
            cls.lldRepository, cls.lldRevision = addToolOptions("lld")
        if includeLldbRevision:  # not built yet
            cls.lldbRepository, cls.lldbRevision = addToolOptions("lldb")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.cCompiler = config.clangPath
        self.cppCompiler = config.clangPlusPlusPath
        # this must be added after checkSystemDependencies
        self.add_cmake_options(
            CMAKE_CXX_COMPILER=self.cppCompiler,
            CMAKE_C_COMPILER=self.cCompiler,
            LLVM_TOOL_LLDB_BUILD=False,
            LLVM_TOOL_LLD_BUILD=not self.skip_lld,
            # saves a bit of time and but might be slightly broken in current clang:
            # CLANG_ENABLE_STATIC_ANALYZER=False,  # save some build time by skipping the static analyzer
            # CLANG_ENABLE_ARCMT=False",  # need to disable ARCMT to disable static analyzer
        )
        if self.canUseLLd(self.cCompiler):
            self.add_cmake_options(LLVM_ENABLE_LLD=True)
        if not self.no_default_sysroot:
            self.configureArgs.append("-DDEFAULT_SYSROOT=" + str(self.config.sdkSysrootDir))
            self.configureArgs.append("-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd")
        if self.config.cheriBits == 128:
            self.add_cmake_options(LLVM_CHERI_IS_128=True)

    def clang38InstallHint(self):
        if IS_FREEBSD:
            return "Try running `pkg install clang38`"
        osRelease = self.readFile(Path("/etc/os-release")) if Path("/etc/os-release").is_file() else ""
        if "Ubuntu" in osRelease:
            return """Try following the instructions on http://askubuntu.com/questions/735201/installing-clang-3-8-on-ubuntu-14-04-3:
            wget -O - http://llvm.org/apt/llvm-snapshot.gpg.key|sudo apt-key add -
            sudo apt-add-repository "deb http://llvm.org/apt/trusty/ llvm-toolchain-trusty-3.8 main"
            sudo apt-get update
            sudo apt-get install clang-3.8"""
        return "Try installing clang 3.8 or newer using your system package manager"

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        # make sure we have at least version 3.8
        self.checkClangVersion(3, 8, installInstructions=self.clang38InstallHint())

    def checkClangVersion(self, major: int, minor: int, patch=0, installInstructions=None):
        if not self.cCompiler or not self.cppCompiler:
            self.dependencyError("Could not find clang", installInstructions=installInstructions)
        info = getCompilerInfo(self.cCompiler)
        # noinspection PyTypeChecker
        if info.compiler != "clang" or info.version < (major, minor, patch):
            versionStr = ".".join(map(str, info.version))
            self.dependencyError(self.cCompiler, "version", versionStr,
                                 "is not supported. Clang version %d.%d or newer is required." % (major, minor),
                                 installInstructions=self.clang38InstallHint())

    def update(self):
        self._updateGitRepo(self.sourceDir, self.repository, revision=self.gitRevision)
        self._updateGitRepo(self.sourceDir / "tools/clang", self.clangRepository, revision=self.clangRevision)
        if not self.skip_lld:
            self._updateGitRepo(self.sourceDir / "tools/lld", self.lldRepository, revision=self.lldRevision,
                                initialBranch="master")
        if False:  # Not yet usable
            self._updateGitRepo(self.sourceDir / "tools/lldb", self.lldbRepository, revision=self.lldbRevision,
                                initialBranch="master")

    def install(self, **kwargs):
        super().install()
        # delete the files incompatible with cheribsd
        incompatibleFiles = list(self.installDir.glob("lib/clang/*/include/std*"))
        incompatibleFiles += self.installDir.glob("lib/clang/*/include/limits.h")
        if len(incompatibleFiles) == 0:
            fatalError("Could not find incompatible builtin includes. Build system changed?")
        print("Removing incompatible builtin includes...")
        for i in incompatibleFiles:
            self.deleteFile(i, printVerboseOnly=True)
        # create a symlink for the target
        llvmBinaries = "clang clang++ clang-cpp llvm-mc llvm-objdump llvm-readobj llvm-size llc".split()
        for tool in llvmBinaries:
            self.createBuildtoolTargetSymlinks(self.installDir / "bin" / tool)

        if not self.skip_lld:
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/ld.lld")
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/ld.lld", toolName="ld", createUnprefixedLink=True)


# Add an alias target clang that builds llvm
class BuildClang(TargetAlias):
    target = "clang"
    dependencies = ["llvm"]


class BuildLLD(TargetAlias):
    target = "lld"
    dependencies = ["llvm"]


class BuildUpstreamLLVM(BuildLLVM):
    githubBaseUrl = "https://github.com/llvm-mirror/"
    repository = githubBaseUrl + "llvm.git"
    projectName = "upstream-llvm"
    defaultInstallDir = CMakeProject._installToBootstrapTools
    appendCheriBitsToBuildDir = False

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(includeClangRevision=True, includeLldRevision=True, useDefaultSysroot=False)

    def install(self, **kwargs):
        CMakeProject.install(self)
