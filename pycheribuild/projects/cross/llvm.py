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
from pathlib import Path
from typing import ClassVar, Iterable

from ..cmake_project import CMakeProject
from ..project import BuildType, ComputedDefaultValue, DefaultInstallDir, GitRepository
from ..simple_project import SimpleProject
from ...config.chericonfig import CheriConfig
from ...config.compilation_targets import (
    CheriBSDMorelloTargetInfo,
    CheriBSDTargetInfo,
    CompilationTargets,
    FreeBSDTargetInfo,
)
from ...config.target_info import AbstractProject, CompilerType, CrossCompileTarget
from ...processutils import CompilerInfo
from ...utils import InstallInstructions, OSInfo, ThreadJoiner, is_jenkins_build, remove_tuple_duplicates

_true_unless_build_all_set = ComputedDefaultValue(function=lambda config, project: not project.build_everything,
                                                  as_string="True unless build-everything is set")
_false_unless_build_all_set = ComputedDefaultValue(function=lambda config, project: project.build_everything,
                                                   as_string="False unless build-everything is set")


class BuildLLVMBase(CMakeProject):
    github_base_url = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(github_base_url + "llvm.git")
    no_default_sysroot = None
    skip_cheri_symlinks = True
    do_not_add_to_targets = True
    can_build_with_asan = True
    is_large_source_repository = True
    # Linking all the debug info takes forever
    default_build_type = BuildType.RELEASE
    # LLVM does not yet compile for purecap.
    supported_architectures = (CompilationTargets.NATIVE_NON_PURECAP,)
    default_architecture = CompilationTargets.NATIVE_NON_PURECAP

    included_projects: "ClassVar[list[str]]"
    add_default_sysroot: "ClassVar[bool]"
    enable_assertions: "ClassVar[bool]"
    skip_static_analyzer: "ClassVar[bool]"
    skip_misc_llvm_tools: "ClassVar[bool]"
    build_everything: "ClassVar[bool]"
    use_llvm_cxx: "ClassVar[bool]"
    use_modules_build: "ClassVar[bool]"
    dylib: "ClassVar[bool]"
    install_toolchain_only: "ClassVar[bool]"
    build_minimal_toolchain: "ClassVar[bool]"

    @staticmethod
    def custom_target_name(base_target: str, xtarget: CrossCompileTarget) -> str:
        if xtarget is CompilationTargets.NATIVE_NON_PURECAP and xtarget != CompilationTargets.NATIVE:
            assert xtarget.generic_target_suffix == "native-hybrid", xtarget.generic_target_suffix
            return base_target + "-native"
        return base_target + "-" + xtarget.generic_target_suffix

    @classmethod
    def is_toolchain_target(cls):
        return True

    @classmethod
    def can_build_with_ccache(cls):
        return True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        if "included_projects" not in cls.__dict__:
            cls.included_projects = cls.add_list_option("include-projects", default=["llvm", "clang", "lld"],
                                                        help="List of LLVM subprojects that should be built")
        cls.add_default_sysroot = False
        cls.enable_assertions = cls.add_bool_option("assertions", help="build with assertions enabled", default=True)
        if "skip_static_analyzer" not in cls.__dict__:
            cls.skip_static_analyzer = cls.add_bool_option("skip-static-analyzer", default=_true_unless_build_all_set,
                                                           help="Don't build the clang static analyzer")
        if "skip_misc_llvm_tools" not in cls.__dict__:
            cls.skip_misc_llvm_tools = cls.add_bool_option("skip-unused-tools", default=_true_unless_build_all_set,
                                                           help="Don't build some of the LLVM tools that should not be "
                                                                "needed by default (e.g. llvm-mca, llvm-pdbutil)")
        cls.build_everything = cls.add_bool_option("build-everything", default=False,
                                                   help="Build everything for the projects that are enabled (e.g. "
                                                        "documentation,examples and bindings)")
        cls.use_llvm_cxx = cls.add_bool_option("use-in-tree-cxx-libs", default=False,
                                               help="Use in-tree, not host, C++ runtime")
        cls.use_modules_build = cls.add_bool_option(
            "use-llvm-modules-build", default=False,
            help="Use the LLVM modules build (may be faster in some cases but probably won't allow debugging)")
        cls.dylib = cls.add_bool_option("dylib", default=False, help="Build dynamic-link LLVM")
        cls.install_toolchain_only = cls.add_bool_option("install-toolchain-only", default=False,
                                                         help="Install only toolchain binaries (i.e. no test tools)")
        cls.build_minimal_toolchain = cls.add_bool_option("build-minimal-toolchain", default=False,
                                                          help="Only build the binaries required for a minimal "
                                                               "toolchain (this is useful to avoid excessive compile "
                                                               "times with LTO)")

    minimal_toolchain_targets = ["clang", "clang-format", "llc", "lld", "llvm-ar", "llvm-cxxfilt", "llvm-mc",
                                 "llvm-nm", "llvm-objcopy", "llvm-objdump", "llvm-ranlib", "llvm-readelf",
                                 "llvm-readobj", "llvm-size", "llvm-strings", "llvm-strip", "llvm-symbolizer",
                                 "opt"]

    def setup(self):
        super().setup()
        if self.compiling_for_host():
            cheri_cc = self.config.cheri_sdk_bindir / "clang"
            if self.CC.exists() and cheri_cc.exists() and self.CC.resolve() == cheri_cc.resolve():
                self.warning("It appears you are trying to compile CHERI-LLVM with CHERI-LLVM (", self.CC,
                             "). This is not recommended!", sep="")
                self.ask_for_confirmation("Are you sure you want to continue?")
        if self.compiling_for_cheri():
            # XXX: Lots of these from SmallVector/StringRef; silence the noise
            # until diagnosed and fixed appropriately.
            self.common_warning_flags.append("-Wno-cheri-inefficient")
        # this must be added after check_system_dependencies
        link_jobs = 2 if self.use_lto else 4
        if os.cpu_count() >= 24:
            link_jobs *= 4  # Increase number of link jobs for powerful servers
        # non-shared debug builds take lots of ram -> use fewer parallel jobs
        if self.should_include_debug_info and "-DBUILD_SHARED_LIBS=ON" not in self.cmake_options:
            link_jobs //= 4
        self.add_cmake_options(LLVM_PARALLEL_LINK_JOBS=link_jobs)  # anything more causes too much I/O + memory usage
        if self.use_asan:
            # Use asan+ubsan
            self.add_cmake_options(LLVM_USE_SANITIZER="Address;Undefined")

        if self.build_type is BuildType.DEBUG:
            # For debug builds we default to enabling expensive checks (override using --llvm/cmake-options)
            self.add_cmake_options(LLVM_ENABLE_EXPENSIVE_CHECKS=True)

        self.add_cmake_options(LLVM_CCACHE_BUILD=self.use_ccache)
        # Lit multiprocessing seems broken with python 2.7 on FreeBSD (and python 3 seems faster at least for
        # libunwind/libcxx)
        # Note: Newer CMake uses Python3_EXECUTABLE instead of PYTHON_EXECUTABLE.
        self.add_cmake_options(PYTHON_EXECUTABLE=sys.executable, Python3_EXECUTABLE=sys.executable)

        # Install the llvm binutils symlinks since they now seem to work fine.
        self.add_cmake_options(LLVM_INSTALL_BINUTILS_SYMLINKS=True)

        # No need for libxml2 (only used for c-index-test and WindowsManifestMerger. The build system doesn't set RPATH
        # correctly for libxml2, so linking against libxml2 in bootstrap tools breaks the build.
        self.add_cmake_options(LLVM_ENABLE_LIBXML2=False)

        # Ensure zlib compressed debug support is present (ON is really AUTO)
        self.add_cmake_options(LLVM_ENABLE_ZLIB="FORCE_ON")

        if self.use_modules_build:
            self.add_cmake_options(LLVM_ENABLE_MODULES=True,
                                   LLVM_ENABLE_MODULE_DEBUGGING=self.should_include_debug_info)

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
        if self.install_toolchain_only:
            self.add_cmake_options(LLVM_INSTALL_TOOLCHAIN_ONLY=True)
        else:
            self.add_cmake_options(LLVM_INSTALL_UTILS=True)
        if self.skip_static_analyzer:
            # save some build time by skipping the static analyzer
            self.add_cmake_options(CLANG_ENABLE_STATIC_ANALYZER=False,
                                   CLANG_ENABLE_ARCMT=False,  # also need to disable ARCMT to disable static analyzer
                                   LLVM_ENABLE_Z3_SOLVER=False,  # and this also needs to be set
                                   )
        if self.skip_misc_llvm_tools:
            self.add_cmake_options(LLVM_TOOL_LLVM_MCA_BUILD=False,
                                   LLVM_TOOL_LLVM_EXEGESIS_BUILD=False,
                                   LLVM_TOOL_LLVM_RC_BUILD=False,
                                   )
        if self.can_use_lld(self.CC):
            self.add_cmake_options(LLVM_ENABLE_LLD=True, _replace=False)  # Don't set to true if LTO set it to false
            # Add GDB index to speed up debugging
            if self.should_include_debug_info:
                self.add_cmake_options(CMAKE_SHARED_LINKER_FLAGS="-fuse-ld=lld -Wl,--gdb-index",
                                       CMAKE_EXE_LINKER_FLAGS="-fuse-ld=lld -Wl,--gdb-index")
                # This should also speed up link time:
                self.add_cmake_options(LLVM_USE_SPLIT_DWARF=True)
        if self.add_default_sysroot:
            self.add_cmake_options(DEFAULT_SYSROOT=self.cross_sysroot_path,
                                   LLVM_DEFAULT_TARGET_TRIPLE="mips64-unknown-freebsd")
        # when making a debug or asserts build speed it up by building a release tablegen
        # Actually it seems like the time spent in CMake is longer than that spent running tablegen, disable for now
        self.add_cmake_options(LLVM_OPTIMIZED_TABLEGEN=False)
        # This should speed up building debug builds
        self.add_cmake_options(LLVM_USE_SPLIT_DWARF=True)
        # self.add_cmake_options(LLVM_APPEND_VC_REV=False)
        # don't set LLVM_ENABLE_ASSERTIONS if it is defined in cmake-options
        if "LLVM_ENABLE_ASSERTIONS" not in "".join(self.cmake_options):
            self.add_cmake_options(LLVM_ENABLE_ASSERTIONS=self.enable_assertions)
        self.add_cmake_options(LLVM_LIT_ARGS="--max-time 3600 --timeout 300 -s -vv")
        if self.build_type == BuildType.DEBUG and "compiler-rt" in self.included_projects:
            self.add_cmake_options(COMPILER_RT_DEBUG=True)  # Enable ASAN, etc assertions
        if self.build_minimal_toolchain:
            if self.build_everything:
                self.fatal(self.target, "/build-everything is incompatible with ", self.target,
                           "/build-minimal-toolchain", sep="")
                if self.install_toolchain_only:
                    self.fatal(self.target, "/build-minimal-toolchain is incompatible with ", self.target,
                               "/install-toolchain-only", sep="")
            self.add_cmake_options(LLVM_BUILD_LLVM_DYLIB=False, LLVM_LINK_LLVM_DYLIB=False,
                                   LLVM_BUILD_LLVM_C_DYLIB=False, CLANG_LINK_CLANG_DYLIB=False,
                                   LLVM_INCLUDE_UTILS=False, LLVM_INCLUDE_TESTS=False, CLANG_INCLUDE_TESTS=False,
                                   CLANG_ENABLE_STATIC_ANALYZER=False, CLANG_ENABLE_ARCMT=False,
                                   LLVM_INSTALL_TOOLCHAIN_ONLY=False,  # This prevents some targets from being created
                                   )

        if not self.compiling_for_host():
            self.add_cmake_options(LLVM_DEFAULT_TARGET_TRIPLE=self.target_info.target_triple)

    def set_lto_binutils(self, ar, ranlib, nm, ld):
        super().set_lto_binutils(ar=ar, ranlib=ranlib, nm=nm, ld=ld)
        # we are passing an explicit linker path -> cannot use LLVM_ENABLE_LLD
        self.add_cmake_options(LLVM_USE_LINKER=ld)
        self.add_cmake_options(LLVM_ENABLE_LLD=False)

    def add_lto_build_options(self, ccinfo: CompilerInfo) -> bool:
        if not super().add_lto_build_options(ccinfo):
            return False  # can't use LTO
        # Use the LLVM build system support for LTO instead of trying to modify CFLAGS/LDFLAGS. The build system
        # includes logic to avoid building binaries such as unit tests with LTO to reduce build times and explicitly
        # adding the compilation flags means that those binaries will actually be built with LTO, massively increasing
        # the build times.
        self._lto_compiler_flags.clear()
        self._lto_linker_flags.clear()
        if self.can_use_thinlto(ccinfo) and not self.prefer_full_lto_over_thin_lto:
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
    def clang_install_hint() -> InstallInstructions:
        alternative = None
        if OSInfo.is_ubuntu() or OSInfo.is_debian():
            alternative = """if the repository version is too old, try running:
sudo apt install software-properties-common
sudo bash -c "$(wget -O - https://apt.llvm.org/llvm.sh)"
"""
        return OSInfo.install_instructions("clang", is_lib=False, freebsd="llvm", apt="clang", alternative=alternative)

    def check_system_dependencies(self):
        super().check_system_dependencies()
        # make sure we have at least version 3.8
        self.check_compiler_version(3, 8)
        # NB: macOS includes it in the SDK, FreeBSD includes it in base
        self.check_required_pkg_config("zlib", apt="zlib1g-dev", zypper="zlib-devel")

    def check_compiler_version(self, major: int, minor: int, patch=0):
        info = self.get_compiler_info(self.CC)
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
                                  install_instructions=self.clang_install_hint())

    def compile(self, **kwargs):
        if self.build_minimal_toolchain:
            # TODO: should allow multiple targets in self.run_make()
            make_args = self.make_args.copy()
            make_args.add_flags(*self.minimal_toolchain_targets)
            self.run_make(options=make_args)
        else:
            super().compile(**kwargs)

    def install(self, **kwargs):
        if self.build_minimal_toolchain:
            # TODO: should allow multiple targets in self.run_make()
            make_args = self.make_args.copy()
            make_args.add_flags("install-clang-resource-headers",
                                *["install-" + x for x in self.minimal_toolchain_targets])
            self.run_make(options=make_args)
        else:
            super().install(**kwargs)
        if self.skip_cheri_symlinks:
            return
        # create a symlinks for triple-prefixed tools
        if "clang" in self.included_projects:
            # create cc and c++ symlinks (expected by some build systems)
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/clang", tool_name="cc",
                                                 create_unprefixed_link=False)
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/clang++", tool_name="c++",
                                                 create_unprefixed_link=False)
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/clang-cpp", tool_name="cpp",
                                                 create_unprefixed_link=False)
            for tool in ("clang", "clang++", "clang-cpp"):
                self.create_triple_prefixed_symlinks(self.install_dir / "bin" / tool)

            # Ensure that the installed clang can find the C++ headers:
            if OSInfo.IS_MAC and Path("/Library/Developer/CommandLineTools/usr/include/c++/v1").is_dir():
                self.makedirs(self.install_dir / "include/c++")
                self.create_symlink(Path("/Library/Developer/CommandLineTools/usr/include/c++/v1"),
                                    self.install_dir / "include/c++/v1", relative=False)

        # Use the LLVM versions of all binutils by default
        if "llvm" in self.included_projects:
            for tool in ("ar", "ranlib", "nm", "objcopy", "readelf", "objdump", "strip"):
                if not (self.install_dir / ("bin/llvm-" + tool)).exists():
                    # Handle old versions of LLVM where readelf isn't installed
                    self.warning(self.install_dir / ("bin/llvm-" + tool), "is missing, please update LLVM")
                    continue
                self.create_triple_prefixed_symlinks(self.install_dir / ("bin/llvm-" + tool), tool_name=tool,
                                                     create_unprefixed_link=True)
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/llvm-symbolizer", tool_name="addr2line",
                                                 create_unprefixed_link=True)
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/llvm-cxxfilt", tool_name="c++filt",
                                                 create_unprefixed_link=True)

        if "lld" in self.included_projects:
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/ld.lld")
            if self.target_info.is_macos():
                self.delete_file(self.install_dir / "bin/ld", print_verbose_only=True)
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
""".format(lld=self.install_dir / "bin/ld.lld")
                self.write_file(self.install_dir / "bin/ld", script, overwrite=True, mode=0o755)
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/ld.lld", tool_name="ld",
                                                 create_unprefixed_link=not self.target_info.is_macos())

    def run_tests(self):
        if not self.compiling_for_host():
            self.fatal("Cannot run tests yet for", self.crosscompile_target)
            return
        # Without setting LC_ALL lit attempts to encode some things as ASCII and fails.
        # This only happens on FreeBSD, but we might as well set it everywhere
        with self.set_env(LC_ALL="en_US.UTF-8", FILECHECK_DUMP_INPUT_ON_FAILURE=1):
            self.run_cmd("cmake", "--build", self.build_dir, "--target", "check-all")

    def prepare_install_dir_for_archiving(self):
        assert is_jenkins_build(), "Should only be called for jenkins builds"
        """Perform cleanup to reduce the size of the tarball that jenkins creates"""
        self.info("Removing LLVM files that are not required for other Jenkins jobs. Size before:")
        self.run_cmd("du", "-sh", self.install_dir)
        # We don't use libclang.so or the other llvm libraries:
        # Note: this is a non-recursive search since we *do* need the files in lib/clang/<version>/
        if self.install_toolchain_only and (self.install_dir / "lib").is_dir():
            for f in (self.install_dir / "lib").iterdir():
                if f.is_dir():
                    continue
                if f.name.startswith(("libclang", "libRemarks", "libLTO", "libLLVM", "liblld")):
                    self.delete_file(f, warn_if_missing=True)
                    continue
                self.warning("Found an unexpected file in libdir. Was this installed by another project?", f)
        if self.install_toolchain_only:
            # We also don't need the C API headers if we deleted the libraries
            self.clean_directory(self.install_dir / "include/", ensure_dir_exists=False)
        # Each of these executables are 30-40MB and we don't use them anywhere:
        # 31685928	/local/scratch/alr48/jenkins-test/tarball/opt/llvm-native/bin/clang-scan-deps
        # 32103560	/local/scratch/alr48/jenkins-test/tarball/opt/llvm-native/bin/clang-rename
        # 33349288	/local/scratch/alr48/jenkins-test/tarball/opt/llvm-native/bin/clang-refactor
        # 41052504	/local/scratch/alr48/jenkins-test/tarball/opt/llvm-native/bin/clang-import-test
        for i in ("clang-scan-deps", "clang-rename", "clang-refactor", "clang-import-test", "clang-offload-bundler",
                  "clang-offload-wrapper", "clang-extdef-mapping", "clang-check"):
            self.delete_file(self.install_dir / "bin" / i, warn_if_missing=True)
        self.info("Size after cleanup")
        self.run_cmd("du", "-sh", self.install_dir)


class BuildLLVMMonoRepoBase(BuildLLVMBase):
    do_not_add_to_targets = True
    root_cmakelists_subdirectory = Path("llvm")

    def setup(self):
        super().setup()
        self.add_cmake_options(LLVM_ENABLE_PROJECTS=";".join(self.included_projects))

    def configure(self, **kwargs):
        if (self.source_dir / "tools/clang/.git").exists():
            self.fatal("Attempting to build LLVM Monorepo but the checkout is from the split repos!")
        if not self.included_projects:
            self.fatal("Need at least one project in --include-projects config option")
        super().configure(**kwargs)

    def add_compiler_with_config_file(self, prefix: str, target: CrossCompileTarget):
        # Create a fake project class that has the required properties needed for essential_compiler_and_linker_flags
        class MockProject(AbstractProject):
            def __init__(self, config, _target: CrossCompileTarget):
                super().__init__(config)
                self.crosscompile_target = _target
                self.needs_sysroot = True

        prefix += target.build_suffix(self.config, include_os=False)
        # Instantiate the target_info using the mock project:
        tgt_info = target.target_info_cls(target, MockProject(self.config, target))  # pytype: disable=not-instantiable
        assert isinstance(tgt_info, FreeBSDTargetInfo)
        # We only want the compiler flags, don't check whether required files exist
        flags = tgt_info.get_essential_compiler_and_linker_flags(perform_sanity_checks=False, default_flags_only=True)
        config_contents = "\n".join(flags) + "\n"
        self.makedirs(self.install_dir / "utils")
        # Note: the config file is loaded from the directory containing the real binary, not the symlink.
        self.write_file(self.install_dir / "bin" / (prefix + ".cfg"), config_contents, overwrite=True, mode=0o644)
        for i in ("clang", "clang++", "clang-cpp"):
            self.create_symlink(self.install_dir / "bin" / i, self.install_dir / "utils" / (prefix + "-" + i))

    def add_compilers_with_config_files(self, prefix: str, rootfs_target: CrossCompileTarget):
        targets = [rootfs_target]
        if rootfs_target.is_cheri_hybrid():
            targets.append(rootfs_target.get_non_cheri_for_hybrid_rootfs_target())
            targets.append(rootfs_target.get_cheri_purecap_for_hybrid_rootfs_target())
        elif rootfs_target.is_cheri_purecap():
            targets.append(rootfs_target.get_non_cheri_for_purecap_rootfs_target())
            targets.append(rootfs_target.get_cheri_hybrid_for_purecap_rootfs_target())

        for target in targets:
            self.add_compiler_with_config_file(prefix, target)

    @classmethod
    def get_install_dir_for_type(cls, caller: SimpleProject, compiler_type: CompilerType):
        if compiler_type == CompilerType.CHERI_LLVM:
            return BuildCheriLLVM.get_native_install_path(caller.config)
        if compiler_type == CompilerType.MORELLO_LLVM:
            return BuildMorelloLLVM.get_native_install_path(caller.config)
        if compiler_type == CompilerType.UPSTREAM_LLVM:
            return BuildUpstreamLLVM.get_native_install_path(caller.config)
        else:
            raise ValueError("Invalid compiler type: " + str(compiler_type))

    def process(self):
        if self.compiling_for_host() and not is_jenkins_build():
            if self.get_native_install_path(self.config) != self.install_dir:
                self.fatal("Overriding the install directory of ", self.target,
                           " is not supported, set the global path properties instead.", fatal_when_pretending=True)
        return super().process()

    @classmethod
    def get_native_install_path(cls, config: CheriConfig):
        # This returns the path where the installed compiler is expected to be
        # Note: When building LLVM in Jenkins this will not match the install_directory
        raise NotImplementedError()


class BuildCheriLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/CTSRD-CHERI/llvm-project.git")
    default_directory_basename = "llvm-project"
    target = "llvm"
    skip_cheri_symlinks = False
    is_sdk_target = True
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    # NB: remove_duplicates is needed for --enable-hybrid-for-purecap-rootfs targets.
    supported_architectures = remove_tuple_duplicates((
        *CompilationTargets.ALL_SUPPORTED_CHERIBSD_TARGETS,
        *CompilationTargets.ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS,
        CompilationTargets.NATIVE_NON_PURECAP,
    ))
    build_all_targets: "ClassVar[bool]"

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_all_targets = cls.add_bool_option("build-all-targets", default=_false_unless_build_all_set,
                                                    help="Support code generation for all architectures instead of "
                                                         "only for CHERI+Host. This is off by "
                                                         "default to reduce compile time.")

    def setup(self):
        super().setup()
        if not self.build_all_targets:
            # Save some time by only building the targets that we need.
            self.add_cmake_options(LLVM_TARGETS_TO_BUILD="AArch64;ARM;Mips;RISCV;X86;host")

        # CLANG_ROUND_TRIP_CC1_ARGS doesn't work for us yet. See e.g. https://reviews.llvm.org/D97462#2677130
        self.add_cmake_options(CLANG_ROUND_TRIP_CC1_ARGS=False)

    def install(self, **kwargs):
        super().install(**kwargs)
        # Create symlinks that hardcode the sdk and the ABI to easily compile binaries
        # Note: This works as long as the first component of the name is not a recognized LLVM triple architecture, so
        # we use {freebsd,cheribsd}-<arch>-<variant>-clang instead of <arch>-cheribsd-<variant>-clang
        if self.compiling_for_host():
            for tgt in CompilationTargets.ALL_CHERIBSD_NON_MORELLO_TARGETS:
                self.add_compilers_with_config_files("cheribsd", tgt)
            for tgt in CompilationTargets.ALL_SUPPORTED_FREEBSD_TARGETS:
                self.add_compilers_with_config_files("freebsd", tgt)

        # llvm-objdump currently doesn't infer the available features
        # This depends on https://reviews.llvm.org/D74023
        self.write_file(self.install_dir / "bin/riscv64cheri-objdump",
                        "#!/bin/sh\nexec '{}' --mattr=+m,+a,+f,+d,+c,+xcheri \"$@\"".format(
                            self.install_dir / "bin/llvm-objdump"),
                        overwrite=True, mode=0o755)

    @property
    def triple_prefixes_for_binaries(self) -> "Iterable[str]":
        triples = [
            CheriBSDTargetInfo.triple_for_target(CompilationTargets.CHERIBSD_RISCV_NO_CHERI, self.config,
                                                 include_version=False),
            CheriBSDTargetInfo.triple_for_target(CompilationTargets.CHERIBSD_AARCH64, self.config,
                                                 include_version=False),
            CheriBSDTargetInfo.triple_for_target(CompilationTargets.CHERIBSD_X86_64, self.config,
                                                 include_version=False),
            ]
        return [x + "-" for x in triples]

    @classmethod
    def get_native_install_path(cls, config: CheriConfig):
        return config.cheri_sdk_dir


class BuildMorelloLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://git.morello-project.org/morello/llvm-project.git")
    default_directory_basename = "morello-llvm-project"
    target = "morello-llvm"
    skip_cheri_symlinks = False  # add target-specific symlinks
    is_sdk_target = True
    native_install_dir = DefaultInstallDir.MORELLO_SDK
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE

    # NB: remove_duplicates is needed for --enable-hybrid-for-purecap-rootfs targets.
    supported_architectures = remove_tuple_duplicates((
        *CompilationTargets.ALL_SUPPORTED_CHERIBSD_TARGETS,
        *CompilationTargets.ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS,
        CompilationTargets.NATIVE_NON_PURECAP,
    ))

    @property
    def triple_prefixes_for_binaries(self) -> "Iterable[str]":
        triples = [
            CheriBSDMorelloTargetInfo.triple_for_target(CompilationTargets.CHERIBSD_MORELLO_PURECAP, self.config,
                                                        include_version=False),
            ]
        return [x + "-" for x in triples]

    def configure(self, **kwargs):
        # Unless we set the default target triple, CMake will not be able to determine the compiler ID.
        # The other alternative to fix this problem is to build the host backend.
        # To save build time we could do the former, but some projects except a working host compiler for
        # configure-time compiler checks.
        # self.add_cmake_options(LLVM_DEFAULT_TARGET_TRIPLE=CheriBSDMorelloTargetInfo.triple_for_target(
        #    CompilationTargets.CHERIBSD_MORELLO_PURECAP, self.config, include_version=True))
        # Note: ARM target is needed for firmware
        self.add_cmake_options(LLVM_TARGETS_TO_BUILD="ARM;AArch64;host")
        # The current master branch isn't ready yet to switch over to the new pass manager
        # TODO: remove this after the next dev->master merge
        self.add_cmake_options(ENABLE_EXPERIMENTAL_NEW_PASS_MANAGER=False)
        # CLANG_ROUND_TRIP_CC1_ARGS doesn't work for us yet. See e.g. https://reviews.llvm.org/D97462#2677130
        self.add_cmake_options(CLANG_ROUND_TRIP_CC1_ARGS=False)
        super().configure(**kwargs)

    def install(self, **kwargs):
        super().install(**kwargs)
        # FIXME: this appears to break the cheribsd build, so let's remove it for now...
        # Seems like this is fixed in CHERI LLVM so it might be caused by Morello LLVM being based on an older version
        if OSInfo.IS_MAC and (self.install_dir / "include/c++/v1").is_symlink():
            self.delete_file(self.install_dir / "include/c++/v1")
        if self.compiling_for_host():
            for tgt in CompilationTargets.ALL_CHERIBSD_MORELLO_TARGETS:
                self.add_compilers_with_config_files("cheribsd", tgt)

    @classmethod
    def get_native_install_path(cls, config: CheriConfig):
        return config.morello_sdk_dir


class BuildUpstreamLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/llvm/llvm-project.git")
    default_directory_basename = "upstream-llvm-project"
    target = "upstream-llvm"
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, project: config.output_root / "upstream-llvm",
        as_string="$INSTALL_ROOT/upstream-llvm")
    skip_misc_llvm_tools = False  # Cannot skip these tools in upstream LLVM

    @classmethod
    def get_native_install_path(cls, config: CheriConfig):
        return config.output_root / "upstream-llvm"


class BuildCheriOSLLVM(BuildLLVMMonoRepoBase):
    repository = GitRepository("https://github.com/CTSRD-CHERI/llvm-project.git", force_branch=True,
                               default_branch="temporal_merge_neat")
    default_directory_basename = "cherios-llvm-project"
    target = "cherios-llvm"
    _default_install_dir_fn = ComputedDefaultValue(function=lambda config, project: config.output_root / "cherios-sdk",
                                                   as_string="$INSTALL_ROOT/cherios-sdk")
    skip_misc_llvm_tools = False  # Cannot skip these tools in upstream LLVM
    hide_options_from_help = True

    def configure(self, **kwargs):
        self.add_cmake_options(LLVM_TARGETS_TO_BUILD="Mips;RISCV;host")
        super().configure(**kwargs)

    @classmethod
    def get_native_install_path(cls, config: CheriConfig):
        return config.output_root / "cherios-sdk"


# Keep around the build infrastructure for building the split repos for now (needed for SOAAP):
class BuildLLVMSplitRepoBase(BuildLLVMBase):
    do_not_add_to_targets = True

    @classmethod
    def setup_config_options(cls, include_lld_revision=True, include_lldb_revision=False, **kwargs):
        super().setup_config_options(**kwargs)

        def add_subproject_ptions(name):
            rev = cls.add_config_option(name + "-git-revision", kind=str, metavar="REVISION",
                                        help="The git revision for tools/" + name)
            repo = cls.add_config_option(name + "-repository", kind=str, metavar="REPOSITORY",
                                         default=cls.github_base_url + name + ".git",
                                         help="The git repository for tools/" + name)
            return repo, rev

        cls.clang_repository, cls.clang_revision = add_subproject_ptions("clang")
        if include_lld_revision:  # not built yet
            cls.lld_repository, cls.lld_revision = add_subproject_ptions("lld")
        if include_lldb_revision:  # not built yet
            cls.lldb_repository, cls.lldb_revision = add_subproject_ptions("lldb")

    def setup(self):
        super().setup()
        self.add_cmake_options(LLVM_TOOL_CLANG_BUILD="clang" in self.included_projects,
                               LLVM_TOOL_LLDB_BUILD="lldb" in self.included_projects,
                               LLVM_TOOL_LLD_BUILD="lld" in self.included_projects)

    def update(self):
        super().update()
        if "clang" in self.included_projects:
            GitRepository(self.clang_repository).update(self, src_dir=self.source_dir / "tools/clang",
                                                        revision=self.clang_revision)
        if "lld" in self.included_projects:
            GitRepository(self.lld_repository).update(self, src_dir=self.source_dir / "tools/lld",
                                                      revision=self.lld_revision)
        if "lldb" in self.included_projects:  # Not yet usable
            GitRepository(self.lldb_repository).update(self, src_dir=self.source_dir / "tools/lldb",
                                                       revision=self.lldb_revision)
