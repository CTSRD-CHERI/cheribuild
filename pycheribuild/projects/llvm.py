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
import shutil

import sys

from .project import *
from ..utils import *
from ..config.loader import ComputedDefaultValue


class BuildLLVMBase(CMakeProject):
    githubBaseUrl = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(githubBaseUrl + "llvm.git")
    no_default_sysroot = None
    appendCheriBitsToBuildDir = True
    skip_cheri_symlinks = True
    doNotAddToTargets = True
    can_build_with_asan = True

    @classmethod
    def setupConfigOptions(cls, useDefaultSysroot=True):
        super().setupConfigOptions()
        if "included_projects" not in cls.__dict__:
            cls.included_projects = cls.addConfigOption("include-projects", default=["llvm", "clang", "lld"], kind=list,
                                                         help="List of LLVM subprojects that should be built")

        if useDefaultSysroot:
            cls.add_default_sysroot = cls.addBoolOption("add-default-sysroot", help="Set default sysroot and "
                                                        "target triple to include cheribsd paths", )
        else:
            cls.add_default_sysroot = False

        cls.enable_assertions = cls.addBoolOption("assertions", help="build with assertions enabled", default=True)
        cls.enable_lto = cls.addBoolOption("enable-lto", help="build with LTO enabled (experimental)")
        if "skip_static_analyzer" not in cls.__dict__:
            cls.skip_static_analyzer = cls.addBoolOption("skip-static-analyzer", default=True,
                                                         help="Don't build the clang static analyzer")
        if "skip_misc_llvm_tools" not in cls.__dict__:
            cls.skip_misc_llvm_tools = cls.addBoolOption("skip-unused-tools", default=True,
                help="Don't build some of the LLVM tools that should not be needed by default (e.g. llvm-mca, llvm-pdbutil)")
        cls.build_everything = cls.addBoolOption("build-everything", default=False,
                                                 help="Also build documentation,examples and bindings")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.cCompiler = config.clangPath
        self.cppCompiler = config.clangPlusPlusPath
        # this must be added after checkSystemDependencies
        link_jobs = 2 if self.enable_lto else 4
        # non-shared debug builds take lots of ram -> use only one parallel job
        if self.cmakeBuildType.lower() in ("debug", "relwithdebinfo") and "-DBUILD_SHARED_LIBS=ON" not in self.cmakeOptions:
            link_jobs = 1
        self.add_cmake_options(
            CMAKE_CXX_COMPILER=self.cppCompiler,
            CMAKE_C_COMPILER=self.cCompiler,
            LLVM_PARALLEL_LINK_JOBS=link_jobs,  # anything more causes too much I/O
        )
        if self.use_asan:
            # Use asan+ubsan
            self.add_cmake_options(LLVM_USE_SANITIZER="Address;Undefined")

        # Lit multiprocessing seems broken with python 2.7 on FreeBSD (and python 3 seems faster at least for libunwind/libcxx)
        self.add_cmake_options(PYTHON_EXECUTABLE=sys.executable)

        if not self.build_everything:
            self.add_cmake_options(
                LLVM_ENABLE_OCAMLDOC=False,
                LLVM_ENABLE_BINDINGS=False,
                # Skip CMake targets for examples and docs to save a tiny bit of time and shrink
                # the list of available targets in CLion
                LLVM_INCLUDE_EXAMPLES=False,
                LLVM_INCLUDE_DOCS=False,
                # This saves some CMake time since it is used as a sub-project
                LLVM_INCLUDE_BENCHMARKS=False,
            )
        if self.skip_static_analyzer:
            # save some build time by skipping the static analyzer
            self.add_cmake_options(CLANG_ENABLE_STATIC_ANALYZER=False,
                                   CLANG_ENABLE_ARCMT=False,   # also need to disable ARCMT to disable static analyzer
                                   CLANG_ANALYZER_ENABLE_Z3_SOLVER=False, # and this also needs to be set
                                   )
        if self.skip_misc_llvm_tools:
            self.add_cmake_options(LLVM_TOOL_LLVM_MCA_BUILD=False,
                                   LLVM_TOOL_LLVM_EXEGESIS_BUILD=False,
                                   LLVM_TOOL_LLVM_RC_BUILD=False,
                                   )
        if self.canUseLLd(self.cCompiler):
            self.add_cmake_options(LLVM_ENABLE_LLD=True)
            # Add GDB index to speed up debugging
            if self.cmakeBuildType.lower() == "debug" or self.cmakeBuildType.lower() == "relwithdebinfo":
                self.add_cmake_options(CMAKE_SHARED_LINKER_FLAGS="-fuse-ld=lld -Wl,--gdb-index",
                                       CMAKE_EXE_LINKER_FLAGS="-fuse-ld=lld -Wl,--gdb-index")
        if self.add_default_sysroot:
            self.add_cmake_options(DEFAULT_SYSROOT=self.crossSysrootPath,
                                   LLVM_DEFAULT_TARGET_TRIPLE="cheri-unknown-freebsd")
        # when making a debug or asserts build speed it up by building a release tablegen
        # Actually it seems like the time spent in CMake is longer than that spent running tablegen, disable for now
        self.add_cmake_options(LLVM_OPTIMIZED_TABLEGEN=False)
        # This should speed up building debug builds
        self.add_cmake_options(LLVM_USE_SPLIT_DWARF=True)
        # self.add_cmake_options(LLVM_APPEND_VC_REV=False)
        # don't set LLVM_ENABLE_ASSERTIONS if it is defined in cmake-options
        if "LLVM_ENABLE_ASSERTIONS" not in "".join(self.cmakeOptions):
            self.add_cmake_options(LLVM_ENABLE_ASSERTIONS=self.enable_assertions)
        if self.config.cheriBits == 128 and not self.config.unified_sdk:
            self.add_cmake_options(LLVM_CHERI_IS_128=True)
        self.add_cmake_options(LLVM_LIT_ARGS="--max-time 3600 --timeout 300 -s -vv")

        if self.enable_lto:
            version_suffix = ""
            if self.cCompiler.name.startswith("clang"):
                version_suffix = self.cCompiler.name[len("clang"):]
            self._addRequiredSystemTool("llvm-ar" + version_suffix)
            self._addRequiredSystemTool("llvm-ranlib" + version_suffix)
            llvm_ar = shutil.which("llvm-ar" + version_suffix)
            llvm_ranlib = shutil.which("llvm-ranlib" + version_suffix)
            self.add_cmake_options(LLVM_ENABLE_LTO="Thin", CMAKE_AR=llvm_ar, CMAKE_RANLIB=llvm_ranlib)
            if not self.canUseLLd(self.cCompiler):
                warningMessage("LLD not found for LTO build, it may fail.")

    @staticmethod
    def clang38InstallHint():
        if IS_FREEBSD:
            return "Try running `pkg install clang38`"
        if OSInfo.isUbuntu():
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
            return
        info = getCompilerInfo(self.cCompiler)
        # noinspection PyTypeChecker
        versionStr = ".".join(map(str, info.version))
        if info.compiler == "apple-clang":
            print("Compiler is apple clang", versionStr, " -- assuming it matches required version", "%d.%d" % (major, minor))
        elif info.compiler == "gcc":
            if info.version < (5, 0, 0):
                warningMessage("GCC older than 5.0.0 will probably not work for compiling clang!")
        elif info.compiler != "clang" or info.version < (major, minor, patch):
            self.dependencyError(self.cCompiler, "version", versionStr,
                                 "is not supported. Clang version %d.%d or newer is required." % (major, minor),
                                 installInstructions=self.clang38InstallHint())

    def install(self, **kwargs):
        super().install()
        if self.skip_cheri_symlinks:
            return
        # create a symlink for the target
        llvmBinaries = "llvm-mc llvm-objdump llvm-readobj llvm-size llc".split()
        if "clang" in self.included_projects:
            llvmBinaries += ["clang", "clang++", "clang-cpp"]
        for tool in llvmBinaries:
            self.createBuildtoolTargetSymlinks(self.installDir / "bin" / tool)

        if "clang" in self.included_projects:
            # create cc and c++ symlinks (expected by some build systems)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang", toolName="cc", createUnprefixedLink=False)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang++", toolName="c++", createUnprefixedLink=False)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang-cpp", toolName="cpp", createUnprefixedLink=False)

        # Use the LLVM versions of ranlib and ar and nm
        if "llvm" in self.included_projects:
            for tool in ("ar", "ranlib", "nm"):
                # TODO: also for objcopy soon so we don't need elftoolchain at all
                self.createBuildtoolTargetSymlinks(self.installDir / ("bin/llvm-" + tool), toolName=tool, createUnprefixedLink=True)

        if "lld" in self.included_projects:
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/ld.lld")
            if IS_MAC:
                self.deleteFile(self.installDir / "bin/ld", printVerboseOnly=True)
                # lld will call the mach-o linker when invoked as ld -> need to create a shell script instead
                script = "#!/bin/sh\nexec " + str(self.installDir / "bin/ld.lld") + " \"$@\"\n"
                self.writeFile(self.installDir / "bin/ld", script, overwrite=True, mode=0o755)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/ld.lld", toolName="ld",
                                               createUnprefixedLink=not IS_MAC)


class BuildLLVMMonoRepoBase(BuildLLVMBase):
    appendCheriBitsToBuildDir = False
    doNotAddToTargets = True

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(useDefaultSysroot=False)

    def configure(self, **kwargs):
        if (self.sourceDir / "tools/clang/.git").exists():
            self.fatal("Attempting to build LLVM Monorepo but the checkout is from the split repos!")
        if not self.included_projects:
            self.fatal("Need at least one project in --include-projects config option")
        self.add_cmake_options(LLVM_ENABLE_PROJECTS=";".join(self.included_projects))
        # CMake needs to run on the llvm subdir
        self.configureArgs[0] = self.configureArgs[0] + "/llvm"
        super().configure(**kwargs)


class BuildCheriLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/CTSRD-CHERI/llvm-project.git")
    projectName = "llvm-project"
    target = "llvm"
    skip_cheri_symlinks = False
    is_sdk_target = True
    defaultInstallDir = CMakeProject._installToSDK


# Add an alias target clang that builds llvm
class BuildClang(TargetAlias):
    target = "clang"
    dependencies = ["llvm"]

class BuildLLD(TargetAlias):
    target = "lld"
    dependencies = ["llvm"]


class BuildUpstreamLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/llvm/llvm-project.git")
    projectName = "upstream-llvm-project"
    target = "upstream-llvm"
    defaultInstallDir = ComputedDefaultValue(
        function=lambda config, project: config.outputRoot / "upstream-llvm",
        asString="$INSTALL_ROOT/upstream-llvm")
    skip_misc_llvm_tools = False # Cannot skip these tools in upstream LLVM


# Keep around the build infrastructure for building the split repos for now:
class BuildLLVMSplitRepoBase(BuildLLVMBase):
    doNotAddToTargets = True

    @classmethod
    def setupConfigOptions(cls, includeLldRevision=True, includeLldbRevision=False, useDefaultSysroot=True):
        super().setupConfigOptions(useDefaultSysroot=useDefaultSysroot)

        def addToolOptions(name):
            rev = cls.addConfigOption(name + "-git-revision", kind=str, metavar="REVISION",
                                      help="The git revision for tools/" + name)
            repo = cls.addConfigOption(name + "-repository", kind=str, metavar="REPOSITORY",
                                       default=cls.githubBaseUrl + name + ".git",
                                       help="The git repository for tools/" + name)
            return repo, rev

        cls.clangRepository, cls.clangRevision = addToolOptions("clang")
        if includeLldRevision:  # not built yet
            cls.lldRepository, cls.lldRevision = addToolOptions("lld")
        if includeLldbRevision:  # not built yet
            cls.lldbRepository, cls.lldbRevision = addToolOptions("lldb")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.add_cmake_options(LLVM_TOOL_CLANG_BUILD="clang" in self.included_projects,
                               LLVM_TOOL_LLDB_BUILD="lldb" in self.included_projects,
                               LLVM_TOOL_LLD_BUILD="lld" in self.included_projects)

    def update(self):
        super().update()
        if "clang" in self.included_projects:
            GitRepository(self.clangRepository).updateRepo(self, srcDir=self.sourceDir / "tools/clang", revision=self.clangRevision, initialBranch="master"),
        if "lld" in self.included_projects:
            GitRepository(self.lldRepository).updateRepo(self, srcDir=self.sourceDir / "tools/lld", revision=self.lldRevision, initialBranch="master"),
        if "lldb" in self.included_projects:  # Not yet usable
            GitRepository(self.lldbRepository).updateRepo(self, srcDir=self.sourceDir / "tools/lldb", revision=self.lldbRevision, initialBranch="master"),


class BuildUpstreamSplitRepoLLVM(BuildLLVMSplitRepoBase):
    githubBaseUrl = "https://github.com/llvm-mirror/"
    repository = GitRepository(githubBaseUrl + "llvm.git")
    projectName = "upstream-llvm-separate-repos"

    defaultInstallDir = ComputedDefaultValue(
        function=lambda config, project: config.outputRoot / "upstream-llvm-split",
        asString="$INSTALL_ROOT/upstream-llvm-split")
    skip_misc_llvm_tools = False # Cannot skip these tools in upstream LLVM

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(useDefaultSysroot=False)

    def configure(self, **kwargs):
        if not (self.sourceDir / "tools/clang/.git").exists():
            self.fatal("Attempting to build LLVM split repos but the checkout is from the monorepo!")
        super().configure(**kwargs)


class BuildCheriSplitRepoLLVM(BuildLLVMSplitRepoBase):
    githubBaseUrl = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(githubBaseUrl + "llvm.git")
    target = "llvm-separate-repos"
    projectName = "llvm"
    # install both split and merged CHERI LLVM to sdk for now
    defaultInstallDir = CMakeProject._installToSDK
    #defaultInstallDir = ComputedDefaultValue(
    #    function=lambda config, project: config.outputRoot / "cheri-llvm-old-layout",
    #    asString="$INSTALL_ROOT/cheri-llvm-old-layout")
    skip_cheri_symlinks = False

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(useDefaultSysroot=False)
