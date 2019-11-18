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
import sys

from .project import *
from ..config.loader import ComputedDefaultValue
from ..utils import *


class BuildLLVMBase(CMakeProject):
    githubBaseUrl = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(githubBaseUrl + "llvm.git")
    no_default_sysroot = None
    skip_cheri_symlinks = True
    doNotAddToTargets = True
    can_build_with_asan = True
    is_large_source_repository = True

    @classmethod
    def setup_config_options(cls, useDefaultSysroot=True):
        super().setup_config_options()
        if "included_projects" not in cls.__dict__:
            cls.included_projects = cls.add_config_option("include-projects", default=["llvm", "clang", "lld"], kind=list,
                                                        help="List of LLVM subprojects that should be built")

        if useDefaultSysroot:
            cls.add_default_sysroot = cls.add_bool_option("add-default-sysroot", help="Set default sysroot and "
                                                                                    "target triple to include "
                                                                                    "cheribsd paths", )
        else:
            cls.add_default_sysroot = False

        cls.enable_assertions = cls.add_bool_option("assertions", help="build with assertions enabled", default=True)
        cls.enable_lto = cls.add_bool_option("enable-lto", help="build with LTO enabled (experimental)")
        if "skip_static_analyzer" not in cls.__dict__:
            cls.skip_static_analyzer = cls.add_bool_option("skip-static-analyzer", default=True,
                                                         help="Don't build the clang static analyzer")
        if "skip_misc_llvm_tools" not in cls.__dict__:
            cls.skip_misc_llvm_tools = cls.add_bool_option("skip-unused-tools", default=True,
                                                         help="Don't build some of the LLVM tools that should not be "
                                                              "needed by default (e.g. llvm-mca, llvm-pdbutil)")
        cls.build_everything = cls.add_bool_option("build-everything", default=False,
                                                 help="Also build documentation,examples and bindings")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.cCompiler = self.CC
        self.cppCompiler = self.CXX
        # this must be added after check_system_dependencies
        link_jobs = 2 if self.enable_lto else 4
        if os.cpu_count() >= 24:
            link_jobs *= 2  # Increase number of link jobs for powerful servers
        # non-shared debug builds take lots of ram -> use only one parallel job
        if self.cmakeBuildType.lower() in (
                "debug", "relwithdebinfo") and "-DBUILD_SHARED_LIBS=ON" not in self.cmakeOptions:
            link_jobs = 1
        self.add_cmake_options(
            CMAKE_CXX_COMPILER=self.cppCompiler,
            CMAKE_C_COMPILER=self.cCompiler,
            LLVM_PARALLEL_LINK_JOBS=link_jobs,  # anything more causes too much I/O + memory usage
        )
        if self.use_asan:
            # Use asan+ubsan
            self.add_cmake_options(LLVM_USE_SANITIZER="Address;Undefined")

        # Lit multiprocessing seems broken with python 2.7 on FreeBSD (and python 3 seems faster at least for libunwind/libcxx)
        self.add_cmake_options(PYTHON_EXECUTABLE=sys.executable)

        # Install the llvm binutils symlinks since they now seem to work fine.
        self.add_cmake_options(LLVM_INSTALL_BINUTILS_SYMLINKS=True)

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
                                   LLVM_DEFAULT_TARGET_TRIPLE="mips64c" + self.config.cheriBitsStr +
                                                              "hybrid-unknown-freebsd")
        # when making a debug or asserts build speed it up by building a release tablegen
        # Actually it seems like the time spent in CMake is longer than that spent running tablegen, disable for now
        self.add_cmake_options(LLVM_OPTIMIZED_TABLEGEN=False)
        # This should speed up building debug builds
        self.add_cmake_options(LLVM_USE_SPLIT_DWARF=True)
        # self.add_cmake_options(LLVM_APPEND_VC_REV=False)
        # don't set LLVM_ENABLE_ASSERTIONS if it is defined in cmake-options
        if "LLVM_ENABLE_ASSERTIONS" not in "".join(self.cmakeOptions):
            self.add_cmake_options(LLVM_ENABLE_ASSERTIONS=self.enable_assertions)
        self.add_cmake_options(LLVM_LIT_ARGS="--max-time 3600 --timeout 300 -s -vv")

        if self.enable_lto:
            ccinfo = getCompilerInfo(self.cCompiler)
            llvm_ar = ccinfo.get_matching_binutil("llvm-ar")
            llvm_ranlib = ccinfo.get_matching_binutil("llvm-ranlib")
            lld = ccinfo.get_matching_binutil("ld.lld")
            if not llvm_ar or not llvm_ranlib or not lld:
                self.warning("Could not find all required binutils to enable LTO")
            elif ccinfo.is_clang and ccinfo.compiler != "apple-clang":
                self.add_cmake_options(CMAKE_AR=llvm_ar, CMAKE_RANLIB=llvm_ranlib, LLVM_USE_LINKER=lld)
                # we are passing an explicit linker path -> cannot use LLVM_ENABLE_LLD
                self.add_cmake_options(LLVM_ENABLE_LLD=False)
                if not self.canUseLLd(self.cCompiler):
                    warningMessage("LLD not found for LTO build, it may fail.")
                self.add_cmake_options(LLVM_ENABLE_LTO="Thin")

    @staticmethod
    def clang_install_hint():
        if IS_FREEBSD:
            return "Try running `pkg install llvm`"
        if OSInfo.isUbuntu() or OSInfo.isDebian():
            return """Try running:
sudo apt install software-properties-common
sudo bash -c "$(wget -O - https://apt.llvm.org/llvm.sh)"
"""
        return "Try installing clang 3.8 or newer using your system package manager"

    def check_system_dependencies(self):
        super().check_system_dependencies()
        # make sure we have at least version 3.8
        self.checkClangVersion(3, 8, installInstructions=self.clang_install_hint())

    def checkClangVersion(self, major: int, minor: int, patch=0, installInstructions=None):
        if not self.cCompiler or not self.cppCompiler:
            self.dependencyError("Could not find clang", installInstructions=installInstructions)
            return
        info = getCompilerInfo(self.cCompiler)
        # noinspection PyTypeChecker
        versionStr = ".".join(map(str, info.version))
        if info.compiler == "apple-clang":
            print("Compiler is apple clang", versionStr, " -- assuming it matches required version",
                  "%d.%d" % (major, minor))
        elif info.compiler == "gcc":
            if info.version < (5, 0, 0):
                warningMessage("GCC older than 5.0.0 will probably not work for compiling clang!")
        elif info.compiler != "clang" or info.version < (major, minor, patch):
            self.dependencyError(self.cCompiler, "version", versionStr,
                                 "is not supported. Clang version %d.%d or newer is required." % (major, minor),
                                 installInstructions=self.clang_install_hint())

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
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang++", toolName="c++",
                                               createUnprefixedLink=False)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang-cpp", toolName="cpp",
                                               createUnprefixedLink=False)

        # Use the LLVM versions of ranlib and ar and nm
        if "llvm" in self.included_projects:
            for tool in ("ar", "ranlib", "nm", "objcopy", "readelf", "objdump", "strip"):
                # TODO: also for objcopy soon so we don't need elftoolchain at all
                self.createBuildtoolTargetSymlinks(self.installDir / ("bin/llvm-" + tool), toolName=tool,
                                                   createUnprefixedLink=True)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/llvm-symbolizer", toolName="addr2line",
                                               createUnprefixedLink=True)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/llvm-cxxfilt", toolName="c++filt",
                                               createUnprefixedLink=True)

        if "lld" in self.included_projects:
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/ld.lld")
            if IS_MAC:
                self.deleteFile(self.installDir / "bin/ld", print_verbose_only=True)
                # lld will call the mach-o linker when invoked as ld -> need to create a shell script instead
                script = "#!/bin/sh\nexec " + str(self.installDir / "bin/ld.lld") + " \"$@\"\n"
                self.writeFile(self.installDir / "bin/ld", script, overwrite=True, mode=0o755)
            self.createBuildtoolTargetSymlinks(self.installDir / "bin/ld.lld", toolName="ld",
                                               createUnprefixedLink=not IS_MAC)


class BuildLLVMMonoRepoBase(BuildLLVMBase):
    doNotAddToTargets = True
    llvm_subdir = "llvm"

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(useDefaultSysroot=False)

    def configure(self, **kwargs):
        if (self.sourceDir / "tools/clang/.git").exists():
            self.fatal("Attempting to build LLVM Monorepo but the checkout is from the split repos!")
        if not self.included_projects:
            self.fatal("Need at least one project in --include-projects config option")
        self.add_cmake_options(LLVM_ENABLE_PROJECTS=";".join(self.included_projects))
        # CMake needs to run on the llvm subdir
        self.configureArgs[0] = self.configureArgs[0] + "/" + self.llvm_subdir
        super().configure(**kwargs)


class BuildCheriLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/CTSRD-CHERI/llvm-project.git")
    project_name = "llvm-project"
    target = "llvm"
    skip_cheri_symlinks = False
    is_sdk_target = True
    defaultInstallDir = CMakeProject._installToSDK

    def install(self, **kwargs):
        super().install(**kwargs)
        # Create symlinks that hardcode the sdk and the ABI to easily compile binaries
        config_file_template = """-target mips64-unknown-freebsd13
-integrated-as
-G0
-msoft-float
-cheri={cheri_bits}
-mcpu=cheri{cheri_bits}
--sysroot={sdk_dir}/sysroot{cheri_bits}
-B{sdk_dir}/bin
-mabi={abi}
"""
        for cheri_bits in (128, 256):
            for abi in ("purecap", "n64"):
                prefix = "cheribsd" + str(cheri_bits) + abi
                config_file_contents = config_file_template.format(cheri_bits=cheri_bits, abi=abi,
                                                                   sdk_dir=self.installDir)
                self.writeFile(self.installDir / "bin" / (prefix + ".cfg"), config_file_contents, overwrite=True,
                               mode=0o644)
                self.createSymlink(self.installDir / "bin/clang", self.installDir / "bin" / (prefix + "-clang"))
                self.createSymlink(self.installDir / "bin/clang++", self.installDir / "bin" / (prefix + "-clang++"))
                self.createSymlink(self.installDir / "bin/clang-cpp", self.installDir / "bin" / (prefix + "-clang-cpp"))


class BuildUpstreamLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/llvm/llvm-project.git")
    project_name = "upstream-llvm-project"
    target = "upstream-llvm"
    defaultInstallDir = ComputedDefaultValue(function=lambda config, project: config.outputRoot / "upstream-llvm",
                                             as_string="$INSTALL_ROOT/upstream-llvm")
    skip_misc_llvm_tools = False  # Cannot skip these tools in upstream LLVM


class BuildCheriOSLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/CTSRD-CHERI/llvm-project.git", force_branch=True, default_branch="temporal")
    project_name = "cherios-llvm-project"
    target = "cherios-llvm"
    defaultInstallDir = ComputedDefaultValue(function=lambda config, project: config.outputRoot / "cherios-sdk",
                                             as_string="$INSTALL_ROOT/cherios-sdk")
    skip_misc_llvm_tools = False  # Cannot skip these tools in upstream LLVM

    def configure(self, **kwargs):
        self.add_cmake_options(LLVM_TARGETS_TO_BUILD="Mips")
        super().configure(**kwargs)


# Keep around the build infrastructure for building the split repos for now (needed for SOAAP):
class BuildLLVMSplitRepoBase(BuildLLVMBase):
    doNotAddToTargets = True

    @classmethod
    def setup_config_options(cls, includeLldRevision=True, includeLldbRevision=False, useDefaultSysroot=True):
        super().setup_config_options(useDefaultSysroot=useDefaultSysroot)

        def addToolOptions(name):
            rev = cls.add_config_option(name + "-git-revision", kind=str, metavar="REVISION",
                                      help="The git revision for tools/" + name)
            repo = cls.add_config_option(name + "-repository", kind=str, metavar="REPOSITORY",
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
            GitRepository(self.clangRepository).update(self, src_dir=self.sourceDir / "tools/clang", revision=self.clangRevision),
        if "lld" in self.included_projects:
            GitRepository(self.lldRepository).update(self, src_dir=self.sourceDir / "tools/lld", revision=self.lldRevision),
        if "lldb" in self.included_projects:  # Not yet usable
            GitRepository(self.lldbRepository).update(self, src_dir=self.sourceDir / "tools/lldb", revision=self.lldbRevision),
