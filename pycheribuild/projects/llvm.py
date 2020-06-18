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
import typing

from .project import BuildType, CMakeProject, DefaultInstallDir, GitRepository
from ..config.compilation_targets import CheriBSDTargetInfo, CompilationTargets
from ..config.loader import ComputedDefaultValue
from ..utils import CompilerInfo, get_compiler_info, OSInfo, ThreadJoiner


class BuildLLVMBase(CMakeProject):
    githubBaseUrl = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(githubBaseUrl + "llvm.git")
    no_default_sysroot = None
    skip_cheri_symlinks = True
    doNotAddToTargets = True
    can_build_with_asan = True
    is_large_source_repository = True
    # Linking all the debug info takes forever
    default_build_type = BuildType.RELEASE

    @classmethod
    def is_toolchain_target(cls):
        return True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        if "included_projects" not in cls.__dict__:
            cls.included_projects = cls.add_config_option("include-projects", default=["llvm", "clang", "lld"],
                                                          kind=list,
                                                          help="List of LLVM subprojects that should be built")

        cls.add_default_sysroot = False

        cls.enable_assertions = cls.add_bool_option("assertions", help="build with assertions enabled", default=True)
        if "skip_static_analyzer" not in cls.__dict__:
            cls.skip_static_analyzer = cls.add_bool_option("skip-static-analyzer", default=True,
                                                           help="Don't build the clang static analyzer")
        if "skip_misc_llvm_tools" not in cls.__dict__:
            cls.skip_misc_llvm_tools = cls.add_bool_option("skip-unused-tools", default=True,
                                                           help="Don't build some of the LLVM tools that should not be "
                                                                "needed by default (e.g. llvm-mca, llvm-pdbutil)")
        cls.build_everything = cls.add_bool_option("build-everything", default=False,
                                                   help="Also build documentation,examples and bindings")
        cls.use_llvm_cxx = cls.add_bool_option("use-in-tree-cxx-libs", default=False,
                                               help="Use in-tree, not host, C++ runtime")
        cls.dylib = cls.add_bool_option("dylib", default=False, help="Build dynamic-link LLVM")

    def setup(self):
        super().setup()
        if self.compiling_for_host():
            cheri_cc = self.config.cheri_sdk_bindir / "clang"
            if self.CC.exists() and cheri_cc.exists() and self.CC.resolve() == cheri_cc.resolve():
                self.warning("It appears you are trying to compile CHERI-LLVM with CHERI-LLVM (", self.CC,
                             "). This is not recommended!", sep="")
                self.ask_for_confirmation("Are you sure you want to continue?")
        # this must be added after check_system_dependencies
        link_jobs = 2 if self.use_lto else 4
        if os.cpu_count() >= 24:
            link_jobs *= 2  # Increase number of link jobs for powerful servers
        # non-shared debug builds take lots of ram -> use fewer parallel jobs
        if self.should_include_debug_info and "-DBUILD_SHARED_LIBS=ON" not in self.cmakeOptions:
            link_jobs //= 4
        self.add_cmake_options(LLVM_PARALLEL_LINK_JOBS=link_jobs)  # anything more causes too much I/O + memory usage
        if self.use_asan:
            # Use asan+ubsan
            self.add_cmake_options(LLVM_USE_SANITIZER="Address;Undefined")

        # Lit multiprocessing seems broken with python 2.7 on FreeBSD (and python 3 seems faster at least for
        # libunwind/libcxx)
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
        if self.use_llvm_cxx:
            self.included_projects += ["libcxx", "libcxxabi", "compiler-rt", "libunwind"]
            self.add_cmake_options(
                LIBCXXABI_USE_LLVM_UNWINDER=True,
                CLANG_DEFAULT_CXX_STDLIB="libc++",
                CLANG_DEFAULT_RTLIB="compiler-rt",
                )
        if self.dylib:
            self.add_cmake_options(LLVM_LINK_LLVM_DYLIB=True)
        if self.skip_static_analyzer:
            # save some build time by skipping the static analyzer
            self.add_cmake_options(CLANG_ENABLE_STATIC_ANALYZER=False,
                                   CLANG_ENABLE_ARCMT=False,  # also need to disable ARCMT to disable static analyzer
                                   CLANG_ANALYZER_ENABLE_Z3_SOLVER=False,  # and this also needs to be set
                                   )
        if self.skip_misc_llvm_tools:
            self.add_cmake_options(LLVM_TOOL_LLVM_MCA_BUILD=False,
                                   LLVM_TOOL_LLVM_EXEGESIS_BUILD=False,
                                   LLVM_TOOL_LLVM_RC_BUILD=False,
                                   )
        if self.canUseLLd(self.CC):
            self.add_cmake_options(LLVM_ENABLE_LLD=True, _replace=False)  # Don't set to true if LTO set it to false
            # Add GDB index to speed up debugging
            if self.should_include_debug_info:
                self.add_cmake_options(CMAKE_SHARED_LINKER_FLAGS="-fuse-ld=lld -Wl,--gdb-index",
                                       CMAKE_EXE_LINKER_FLAGS="-fuse-ld=lld -Wl,--gdb-index")
                # This should also speed up link time:
                self.add_cmake_options(LLVM_USE_SPLIT_DWARF=True)
        if self.add_default_sysroot:
            self.add_cmake_options(DEFAULT_SYSROOT=self.crossSysrootPath,
                                   LLVM_DEFAULT_TARGET_TRIPLE="mips64-unknown-freebsd")
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

    def set_lto_binutils(self, ar, ranlib, nm, ld):
        super().set_lto_binutils(ar=ar, ranlib=ranlib, nm=nm, ld=ld)
        # we are passing an explicit linker path -> cannot use LLVM_ENABLE_LLD
        self.add_cmake_options(LLVM_USE_LINKER=ld)
        self.add_cmake_options(LLVM_ENABLE_LLD=False)

    def add_lto_build_options(self, ccinfo: CompilerInfo) -> bool:
        if not super().add_lto_build_options(ccinfo):
            return False  # can't use LTO
        if self.canUseLLd(self.CC) and not self.prefer_full_lto_over_thin_lto:
            self.add_cmake_options(LLVM_ENABLE_LTO="Thin")
        else:
            self.add_cmake_options(LLVM_ENABLE_LTO=True)

    def clean(self) -> ThreadJoiner:
        # TODO: probably fine if LLVM is the only target to be built
        # Warn before cleaning LLVM to avoid wasted CPU cycles
        if not self.query_yes_no("You are about to do a clean LLVM build. This may take a long time. Are you sure?",
                                 default_result=True):
            return ThreadJoiner(None)
        return super().clean()

    @staticmethod
    def clang_install_hint():
        if OSInfo.IS_FREEBSD:
            return "Try running `pkg install llvm`"
        if OSInfo.is_ubuntu() or OSInfo.is_debian():
            return """Try running:
sudo apt install software-properties-common
sudo bash -c "$(wget -O - https://apt.llvm.org/llvm.sh)"
"""
        return "Try installing clang 3.8 or newer using your system package manager"

    def check_system_dependencies(self):
        super().check_system_dependencies()
        # make sure we have at least version 3.8
        self.check_compiler_version(3, 8, install_instructions=self.clang_install_hint())

    def check_compiler_version(self, major: int, minor: int, patch=0, install_instructions=None):
        info = get_compiler_info(self.CC)
        # noinspection PyTypeChecker
        version_str = ".".join(map(str, info.version))
        if info.compiler == "apple-clang":
            self.info("Compiler is apple clang", version_str, " -- assuming it matches required version",
                      "%d.%d" % (major, minor))
        elif info.compiler == "gcc":
            if info.version < (5, 0, 0):
                self.warning("GCC older than 5.0.0 will probably not work for compiling clang!")
        elif info.compiler != "clang" or info.version < (major, minor, patch):
            self.dependency_error(self.CC, "version", version_str,
                                  "is not supported. Clang version %d.%d or newer is required." % (major, minor),
                                  install_instructions=install_instructions)

    def install(self, **kwargs):
        super().install()
        if self.skip_cheri_symlinks:
            return
        # create a symlink for the target
        llvm_binaries = "llvm-mc llvm-objdump llvm-readobj llvm-size llc".split()
        if "clang" in self.included_projects:
            llvm_binaries += ["clang", "clang++", "clang-cpp"]
        for tool in llvm_binaries:
            self.create_triple_prefixed_symlinks(self.installDir / "bin" / tool)

        if "clang" in self.included_projects:
            # create cc and c++ symlinks (expected by some build systems)
            self.create_triple_prefixed_symlinks(self.installDir / "bin/clang", tool_name="cc",
                                                 create_unprefixed_link=False)
            self.create_triple_prefixed_symlinks(self.installDir / "bin/clang++", tool_name="c++",
                                                 create_unprefixed_link=False)
            self.create_triple_prefixed_symlinks(self.installDir / "bin/clang-cpp", tool_name="cpp",
                                                 create_unprefixed_link=False)

        # Use the LLVM versions of ranlib and ar and nm
        if "llvm" in self.included_projects:
            for tool in ("ar", "ranlib", "nm", "objcopy", "readelf", "objdump", "strip"):
                self.create_triple_prefixed_symlinks(self.installDir / ("bin/llvm-" + tool), tool_name=tool,
                                                     create_unprefixed_link=True)
            self.create_triple_prefixed_symlinks(self.installDir / "bin/llvm-symbolizer", tool_name="addr2line",
                                                 create_unprefixed_link=True)
            self.create_triple_prefixed_symlinks(self.installDir / "bin/llvm-cxxfilt", tool_name="c++filt",
                                                 create_unprefixed_link=True)

        if "lld" in self.included_projects:
            self.create_triple_prefixed_symlinks(self.installDir / "bin/ld.lld")
            if OSInfo.IS_MAC:
                self.deleteFile(self.installDir / "bin/ld", print_verbose_only=True)
                # lld will call the mach-o linker when invoked as ld -> need to create a shell script instead
                # that forwards to /usr/bin/ld for macOS binaries and ld.lld for cross-compilation
                script = """#!/bin/sh
case "$@" in
  *-macosx_version_min*|*-platform_version*macos*)
    # Must be linking a native macOS executable
    exec /usr/bin/ld "$@"
    ;;
esac
exec {lld} "$@"
""".format(lld=self.installDir / "bin/ld.lld")
                self.write_file(self.installDir / "bin/ld", script, overwrite=True, mode=0o755)
            self.create_triple_prefixed_symlinks(self.installDir / "bin/ld.lld", tool_name="ld",
                                                 create_unprefixed_link=not OSInfo.IS_MAC)


class BuildLLVMMonoRepoBase(BuildLLVMBase):
    doNotAddToTargets = True
    llvm_subdir = "llvm"

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

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
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.ROOTFS
    supported_architectures = [CompilationTargets.NATIVE, CompilationTargets.CHERIBSD_MIPS_NO_CHERI,
                               CompilationTargets.CHERIBSD_MIPS_HYBRID, CompilationTargets.CHERIBSD_MIPS_PURECAP]

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_all_targets = cls.add_bool_option("build-all-targets",
                                                    help="Support code generation for all architectures instead of "
                                                         "only for CHERI+Host. This is off by "
                                                         "default to reduce compile time.")

    def setup(self):
        super().setup()
        if not self.build_all_targets:
            # Save some time by only building the targets that we need.
            self.add_cmake_options(LLVM_TARGETS_TO_BUILD="Mips;RISCV;host")

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
        for cheri_bits in [128]:
            for abi in ("purecap", "n64"):
                prefix = "cheribsd" + str(cheri_bits) + abi
                config_file_contents = config_file_template.format(cheri_bits=cheri_bits, abi=abi,
                                                                   sdk_dir=self.installDir)
                self.write_file(self.installDir / "bin" / (prefix + ".cfg"), config_file_contents, overwrite=True,
                                mode=0o644)
                self.create_symlink(self.installDir / "bin/clang", self.installDir / "bin" / (prefix + "-clang"))
                self.create_symlink(self.installDir / "bin/clang++", self.installDir / "bin" / (prefix + "-clang++"))
                self.create_symlink(self.installDir / "bin/clang-cpp",
                                    self.installDir / "bin" / (prefix + "-clang-cpp"))
        # llvm-objdump currently doesn't infer the available features
        # This depends on https://reviews.llvm.org/D74023
        self.write_file(self.installDir / "bin/riscv64cheri-objdump",
                        "#!/bin/sh\nexec '{}' --mattr=+m,+a,+f,+d,+c,+xcheri \"$@\"".format(
                            self.installDir / "bin/llvm-objdump"),
                        overwrite=True, mode=0o755)

    @property
    def triple_prefixes_for_binaries(self) -> typing.Iterable[str]:
        triples = [
            "cheri-unknown-freebsd",  # for compat
            CheriBSDTargetInfo.triple_for_target(CompilationTargets.CHERIBSD_MIPS_NO_CHERI, self.config,
                                                 include_version=True),
            CheriBSDTargetInfo.triple_for_target(CompilationTargets.CHERIBSD_MIPS_NO_CHERI, self.config,
                                                 include_version=False),
            # RISC-V triple is the same for NO_CHERI and PURECAP so only give once
            CheriBSDTargetInfo.triple_for_target(CompilationTargets.CHERIBSD_RISCV_NO_CHERI, self.config,
                                                 include_version=True),
            CheriBSDTargetInfo.triple_for_target(CompilationTargets.CHERIBSD_RISCV_NO_CHERI, self.config,
                                                 include_version=False),
            ]
        return [x + "-" for x in triples]


class BuildUpstreamLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/llvm/llvm-project.git")
    project_name = "upstream-llvm-project"
    target = "upstream-llvm"
    _default_install_dir_fn = ComputedDefaultValue(function=lambda config, project: config.outputRoot / "upstream-llvm",
                                                   as_string="$INSTALL_ROOT/upstream-llvm")
    skip_misc_llvm_tools = False  # Cannot skip these tools in upstream LLVM


class BuildCheriOSLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/CTSRD-CHERI/llvm-project.git", force_branch=True,
                               default_branch="temporal")
    project_name = "cherios-llvm-project"
    target = "cherios-llvm"
    _default_install_dir_fn = ComputedDefaultValue(function=lambda config, project: config.outputRoot / "cherios-sdk",
                                                   as_string="$INSTALL_ROOT/cherios-sdk")
    skip_misc_llvm_tools = False  # Cannot skip these tools in upstream LLVM

    def configure(self, **kwargs):
        self.add_cmake_options(LLVM_TARGETS_TO_BUILD="Mips;host")
        super().configure(**kwargs)


# Keep around the build infrastructure for building the split repos for now (needed for SOAAP):
class BuildLLVMSplitRepoBase(BuildLLVMBase):
    doNotAddToTargets = True

    @classmethod
    def setup_config_options(cls, include_lld_revision=True, include_lldb_revision=False, **kwargs):
        super().setup_config_options(**kwargs)

        def add_subproject_ptions(name):
            rev = cls.add_config_option(name + "-git-revision", kind=str, metavar="REVISION",
                                        help="The git revision for tools/" + name)
            repo = cls.add_config_option(name + "-repository", kind=str, metavar="REPOSITORY",
                                         default=cls.githubBaseUrl + name + ".git",
                                         help="The git repository for tools/" + name)
            return repo, rev

        cls.clangRepository, cls.clangRevision = add_subproject_ptions("clang")
        if include_lld_revision:  # not built yet
            cls.lldRepository, cls.lldRevision = add_subproject_ptions("lld")
        if include_lldb_revision:  # not built yet
            cls.lldbRepository, cls.lldbRevision = add_subproject_ptions("lldb")

    def setup(self):
        super().setup()
        self.add_cmake_options(LLVM_TOOL_CLANG_BUILD="clang" in self.included_projects,
                               LLVM_TOOL_LLDB_BUILD="lldb" in self.included_projects,
                               LLVM_TOOL_LLD_BUILD="lld" in self.included_projects)

    def update(self):
        super().update()
        if "clang" in self.included_projects:
            GitRepository(self.clangRepository).update(self, src_dir=self.sourceDir / "tools/clang",
                                                       revision=self.clangRevision),
        if "lld" in self.included_projects:
            GitRepository(self.lldRepository).update(self, src_dir=self.sourceDir / "tools/lld",
                                                     revision=self.lldRevision),
        if "lldb" in self.included_projects:  # Not yet usable
            GitRepository(self.lldbRepository).update(self, src_dir=self.sourceDir / "tools/lldb",
                                                      revision=self.lldbRevision),
