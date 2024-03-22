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
import platform
import sys
import typing
from contextlib import suppress

from .crosscompileproject import (
    CheriConfig,
    CompilationTargets,
    CrossCompileCMakeProject,
    DefaultInstallDir,
    GitRepository,
)
from .llvm import BuildCheriLLVM, BuildLLVMMonoRepoBase, BuildUpstreamLLVM, extra_llvm_lit_opts
from ..build_qemu import BuildQEMU
from ..cmake_project import CMakeProject
from ..project import ReuseOtherProjectDefaultTargetRepository
from ..run_qemu import LaunchCheriBSD, LaunchFreeBSD
from ...colour import AnsiColour, coloured
from ...config.chericonfig import BuildType
from ...ssh_utils import generate_ssh_config_file_for_qemu, ssh_host_accessible_uncached
from ...utils import OSInfo, classproperty


# A base class to set the default installation directory
class _CxxRuntimeCMakeProject(CrossCompileCMakeProject):
    do_not_add_to_targets = True
    cross_install_dir = DefaultInstallDir.SYSROOT_FOR_BAREMETAL_ROOTFS_OTHERWISE
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY

    @property
    def _rootfs_install_dir_name(self):
        return "c++"


class BuildLibCXXRT(_CxxRuntimeCMakeProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/libcxxrt.git")
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        result = super().dependencies(config)
        return (*result, "libunwind")

    def setup(self):
        super().setup()
        if not self.target_info.is_baremetal():
            self.add_cmake_options(
                LIBUNWIND_PATH=BuildLibunwind.get_install_dir(self) / "lib",
                CMAKE_INSTALL_RPATH_USE_LINK_PATH=True,
            )
        if self.compiling_for_host():
            assert not self.target_info.is_baremetal()
            self.add_cmake_options(BUILD_TESTS=True, TEST_LIBUNWIND=True)
            if OSInfo.is_ubuntu():
                self.add_cmake_options(COMPARE_TEST_OUTPUT_TO_SYSTEM_OUTPUT=False)
                # Seems to be needed for at least jenkins (it says relink with -pie)
                self.add_cmake_options(CMAKE_POSITION_INDEPENDENT_CODE=True)
                # The static libc.a on Ubuntu is not compiled with -fPIC so we can't link to it..
                self.add_cmake_options(NO_STATIC_TEST=True)
            self.add_cmake_options(NO_UNWIND_LIBRARY=False)
        else:
            # TODO: __sync_fetch_and_add in exceptions code
            self.add_cmake_options(
                NO_SHARED=self.force_static_linkage,
                DISABLE_EXCEPTIONS_RTTI=False,
                NO_UNWIND_LIBRARY=False,
            )
            self.add_cmake_options(COMPARE_TEST_OUTPUT_TO_SYSTEM_OUTPUT=False)
            if not self.target_info.is_baremetal():
                self.add_cmake_options(BUILD_TESTS=True, TEST_LIBUNWIND=True)

    def install(self, **kwargs):
        self.install_file(self.build_dir / "lib/libcxxrt.a", self.install_dir / "lib" / "libcxxrt.a", force=True)
        self.install_file(self.build_dir / "lib/libcxxrt.so", self.install_dir / "lib" / "libcxxrt.soa", force=True)

    def run_tests(self):
        if self.target_info.is_baremetal():
            self.info("Baremetal tests not implemented")
            return
        # TODO: this won't work on macOS
        with self.set_env(LD_LIBRARY_PATH=self.build_dir / "lib"):
            if self.compiling_for_host():
                self.run_cmd("ctest", ".", "-VV", cwd=self.build_dir)
            else:
                self.target_info.run_cheribsd_test_script(
                    "run_libcxxrt_tests.py",
                    "--libunwind-build-dir",
                    BuildLibunwind.get_build_dir(self),
                    mount_builddir=True,
                    mount_sysroot=True,
                )


def _default_ssh_port(c, p: CMakeProject):
    xtarget = p.crosscompile_target
    if not xtarget.target_info_cls.is_cheribsd():
        return None
    return LaunchCheriBSD.get_instance(p, c, cross_target=xtarget.get_rootfs_target()).ssh_forwarding_port


class BuildLibCXX(_CxxRuntimeCMakeProject):
    # TODO: add an option to allow upstream llvm?
    repository = ReuseOtherProjectDefaultTargetRepository(BuildCheriLLVM, subdirectory="libcxx")
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS
    dependencies = ("libcxxrt",)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.only_compile_tests = cls.add_bool_option(
            "only-compile-tests",
            help="Don't attempt to run tests, only compile them",
        )
        cls.exceptions = cls.add_bool_option("exceptions", default=True, help="Build with support for C++ exceptions")
        cls.collect_test_binaries = cls.add_optional_path_option(
            "collect-test-binaries",
            metavar="TEST_PATH",
            help="Instead of running tests copy them to $TEST_PATH",
        )
        cls.nfs_mounted_path = cls.add_optional_path_option(
            "nfs-mounted-path",
            metavar="PATH",
            help=(
                "Use a PATH as a directorythat is NFS mounted inside QEMU instead of using scp to copy individual tests"
            ),
        )
        cls.nfs_path_in_qemu = cls.add_optional_path_option(
            "nfs-mounted-path-in-qemu",
            metavar="PATH",
            help="The path used inside QEMU to refer to nfs-mounted-path",
        )
        cls.qemu_host = cls.add_config_option(
            "ssh-host",
            help="The QEMU SSH hostname to connect to for running tests",
            default="localhost",
        )
        cls.qemu_port = cls.add_config_option(
            "ssh-port",
            help="The QEMU SSH port to connect to for running tests",
            _allow_unknown_targets=True,
            default=_default_ssh_port,
            only_add_for_targets=CompilationTargets.ALL_SUPPORTED_CHERIBSD_TARGETS,
        )
        cls.qemu_user = cls.add_config_option("ssh-user", default="root", help="The CheriBSD used for running tests")

        cls.test_jobs = cls.add_config_option(
            "parallel-test-jobs",
            help="Number of QEMU instances spawned to run tests (default: number of build jobs (-j flag) / 2)",
            default=lambda c, p: max(c.make_jobs / 2, 1),
            kind=int,
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.qemu_host:
            self.qemu_host = os.path.expandvars(self.qemu_host)
        self.libcxx_lit_jobs = ""

    def setup(self):
        super().setup()
        if self.compiling_for_host():
            self.add_cmake_options(LIBCXX_ENABLE_SHARED=True, LIBCXX_ENABLE_STATIC_ABI_LIBRARY=False)
            if OSInfo.is_ubuntu():
                # Ubuntu packagers think that static linking should not be possible....
                self.add_cmake_options(LIBCXX_ENABLE_STATIC=False)
        else:
            self.add_cross_flags()
        # add the common test options
        self.add_cmake_options(
            CMAKE_INSTALL_RPATH_USE_LINK_PATH=True,  # Fix finding libunwind.so
            LIBCXX_INCLUDE_TESTS=True,
            LIBCXXABI_USE_LLVM_UNWINDER=False,  # we have a fake libunwind in libcxxrt
            LLVM_LIT_ARGS="--xunit-xml-output "
            + os.getenv("WORKSPACE", ".")
            + "/libcxx-test-results.xml --max-time 3600 --timeout 120"
            + self.libcxx_lit_jobs,
        )
        # Lit multiprocessing seems broken with python 2.7 on FreeBSD (and python 3 seems faster at least for
        # libunwind/libcxx)
        self.add_cmake_options(PYTHON_EXECUTABLE=sys.executable)
        # select libcxxrt as the runtime library (except on macos where this doesn't seem to work very well)
        if not (self.compiling_for_host() and OSInfo.IS_MAC):
            self.add_cmake_options(
                LIBCXX_CXX_ABI="libcxxrt",
                LIBCXX_CXX_ABI_LIBNAME="libcxxrt",
                LIBCXX_CXX_ABI_INCLUDE_PATHS=BuildLibCXXRT.get_source_dir(self) / "src",
                LIBCXX_CXX_ABI_LIBRARY_PATH=BuildLibCXXRT.get_build_dir(self) / "lib",
            )
            if not self.target_info.is_baremetal():
                # use llvm libunwind when testing
                self.add_cmake_options(
                    LIBCXX_STATIC_CXX_ABI_LIBRARY_NEEDS_UNWIND_LIBRARY=True,
                    LIBCXX_CXX_ABI_UNWIND_LIBRARY="unwind",
                    LIBCXX_CXX_ABI_UNWIND_LIBRARY_PATH=BuildLibunwind.get_build_dir(self) / "lib",
                )

        if not self.exceptions or self.target_info.is_baremetal():
            self.add_cmake_options(LIBCXX_ENABLE_EXCEPTIONS=False, LIBCXX_ENABLE_RTTI=False)
        else:
            self.add_cmake_options(LIBCXX_ENABLE_EXCEPTIONS=True, LIBCXX_ENABLE_RTTI=True)
        # TODO: remove this once stuff has been fixed:
        self.common_warning_flags.append("-Wno-ignored-attributes")

    def add_cross_flags(self):
        # TODO: do I even need the toolchain file to cross compile?

        self.add_cmake_options(LIBCXX_TARGET_TRIPLE=self.target_info.target_triple, LIBCXX_SYSROOT=self.sdk_sysroot)

        if self.compiling_for_cheri():
            # Ensure that we don't have failing tests due to cheri bugs
            self.common_warning_flags.append("-Werror=cheri")

        # We need to build with -G0 otherwise we get R_MIPS_GPREL16 out of range linker errors
        test_compile_flags = self.commandline_to_str(self.default_compiler_flags)
        test_linker_flags = self.commandline_to_str(self.default_ldflags)

        if self.target_info.is_baremetal():
            if self.compiling_for_mips(include_purecap=False):
                test_compile_flags += " -fno-pic -mno-abicalls"
            self.add_cmake_options(
                LIBCXX_ENABLE_FILESYSTEM=False,
                LIBCXX_USE_COMPILER_RT=True,
                LIBCXX_ENABLE_STDIN=False,  # currently not support on baremetal QEMU
                LIBCXX_ENABLE_GLOBAL_FILESYSTEM_NAMESPACE=False,  # no filesystem on baremetal QEMU
                # TODO: we should be able to implement this in QEMU
                LIBCXX_ENABLE_MONOTONIC_CLOCK=False,  # no monotonic clock for now
            )
            test_linker_flags += " -Wl,-T,qemu-malta.ld"

        self.add_cmake_options(
            LIBCXX_TEST_COMPILER_FLAGS=test_compile_flags,
            LIBCXX_TEST_LINKER_FLAGS=test_linker_flags,
            LIBCXX_SLOW_TEST_HOST=True,
        )  # some tests need more tolerance/less iterations on QEMU

        self.add_cmake_options(
            LIBCXX_ENABLE_SHARED=False,  # not yet
            LIBCXX_ENABLE_STATIC=True,
            # no threads on baremetal newlib
            LIBCXX_ENABLE_THREADS=not self.target_info.is_baremetal(),
            # baremetal the -fPIC build doesn't work for some reason (runs out of CALL16 relocations)
            # Not sure how this can happen since LLD includes multigot
            LIBCXX_BUILD_POSITION_DEPENDENT=self.target_info.is_baremetal(),
            LIBCXX_ENABLE_EXPERIMENTAL_LIBRARY=False,  # not yet
            LIBCXX_INCLUDE_BENCHMARKS=False,
            LIBCXX_INCLUDE_DOCS=False,
            # When cross compiling we link the ABI library statically (except baremetal since that doens;t have it yet)
            LIBCXX_ENABLE_STATIC_ABI_LIBRARY=not self.target_info.is_baremetal(),
        )
        if self.only_compile_tests:
            executor = self.commandline_to_str([self.source_dir / "utils/compile_only.py"])
        elif self.collect_test_binaries:
            executor = self.commandline_to_str(
                [self.source_dir / "utils/copy_files.py", "--output-dir", self.collect_test_binaries],
            )
        elif self.target_info.is_baremetal():
            run_qemu_script = self.target_info.sdk_root_dir / "baremetal/mips64-qemu-elf/bin/run_with_qemu.py"
            if not run_qemu_script.exists():
                self.warning(
                    "run_with_qemu.py is needed to run libcxx baremetal tests but could not find it:",
                    run_qemu_script,
                    "does not exist",
                )
            prefix = [str(run_qemu_script), "--qemu", str(BuildQEMU.qemu_binary(self)), "--timeout", "20"]
            prefix_list = '[\\"' + '\\", \\"'.join(prefix) + '\\"]'
            executor = "PrefixExecutor(" + prefix_list + ", LocalExecutor())"
        elif self.nfs_mounted_path:
            self.libcxx_lit_jobs = " -j1"  # We can only run one job here since we are using scp
            self.fatal("nfs_mounted_path not portend to new libc++ test infrastructure yet")
            executor = (
                f'SSHExecutorWithNFSMount(\\"{self.qemu_host}\\", nfs_dir=\\"{self.nfs_mounted_path}\\",'
                f'path_in_target=\\"{self.nfs_path_in_qemu}\\", config=self,'
                f'username=\\"{self.qemu_user}\\", port={self.qemu_port})'
            )
        else:
            self.libcxx_lit_jobs = " -j1"  # We can only run one job here since we are using scp
            executor = self.commandline_to_str(
                [self.source_dir / "utils/ssh.py", "--host", f"{self.qemu_user}@{self.qemu_host}:{self.qemu_port}"],
            )
        if self.target_info.is_baremetal():
            target_info = "libcxx.test.target_info.BaremetalNewlibTI"
        else:
            target_info = "libcxx.test.target_info.CheriBSDRemoteTI"
        # add the config options required for running tests:
        self.add_cmake_options(LIBCXX_EXECUTOR=executor, LIBCXX_TARGET_INFO=target_info, LIBCXX_RUN_LONG_TESTS=False)

    def run_tests(self):
        if self.target_info.is_baremetal():
            self.info("Baremetal tests not implemented")
            return
        if self.compiling_for_host():
            self.run_make("check-cxx", cwd=self.build_dir)
        elif self.can_run_binaries_on_remote_morello_board():
            executor = [self.source_dir / "utils/ssh.py", "--host", self.config.remote_morello_board]
            # The Morello board has 4 CPUs, so run 4 tests in parallel.
            self.run_cmd(
                [
                    sys.executable,
                    self.build_dir / "bin/llvm-lit",
                    "-j4",
                    "-vv",
                    f"--xunit-xml-output={self.build_dir / 'test-results.xml'}",
                    "-Dexecutor=" + self.commandline_to_str(executor),
                    "test",
                ],
                cwd=self.build_dir,
            )
        else:
            # long running test -> speed up by using a kernel without invariants
            self.target_info.run_cheribsd_test_script(
                "run_libcxx_tests.py",
                "--parallel-jobs",
                self.test_jobs,
                "--ssh-executor-script",
                self.source_dir / "utils/ssh.py",
                use_benchmark_kernel_by_default=True,
            )


class _BuildLlvmRuntimes(CrossCompileCMakeProject):
    do_not_add_to_targets = True
    _always_add_suffixed_targets = True
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY

    # The following have to be set in subclasses
    llvm_project: "typing.ClassVar[type[BuildLLVMMonoRepoBase]]"
    # TODO: add compiler-rt
    _enabled_runtimes: "typing.ClassVar[tuple[str, ...]]" = ("libunwind", "libcxxabi", "libcxx")
    test_against_running_qemu_instance = False
    test_localhost_via_ssh = False

    def get_enabled_runtimes(self) -> "list[str]":
        return list(self._enabled_runtimes)

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        if not cls.get_crosscompile_target().is_native():
            return super().dependencies(config)
        return (
            *super().dependencies(config),
            cls.llvm_project.get_class_for_target(CompilationTargets.NATIVE_NON_PURECAP).target,
        )

    @classproperty
    def repository(self):
        return ReuseOtherProjectDefaultTargetRepository(self.llvm_project, subdirectory="runtimes", do_update=True)

    @property
    def custom_c_preprocessor(self):
        if self.compiling_for_host():
            return (
                self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE_NON_PURECAP)
                / "bin/clang-cpp"
            )
        return None

    @property
    def custom_c_compiler(self):
        if self.compiling_for_host():
            return (
                self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE_NON_PURECAP)
                / "bin/clang"
            )
        return None

    @property
    def custom_cxx_compiler(self):
        if self.compiling_for_host():
            return (
                self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE_NON_PURECAP)
                / "bin/clang++"
            )
        return None

    @property
    def qemu_instance(self):
        if self.compiling_for_host():
            return None
        elif self.target_info.is_cheribsd():
            return LaunchCheriBSD.get_instance(self, cross_target=self.crosscompile_target.get_rootfs_target())
        elif self.target_info.is_freebsd():
            return LaunchFreeBSD.get_instance(self, cross_target=self.crosscompile_target.get_rootfs_target())
        return None

    @property
    def test_ssh_config_path(self):
        return self.build_dir / "ssh_config"

    def get_localhost_test_executor_command(self) -> "list[str]":
        if self.test_localhost_via_ssh:
            assert self.compiling_for_host()
            ssh_host = self.config.get_user_name() + "@" + platform.node()
            return [f"{self.source_dir / '../libcxx/utils/ssh.py'}", "--host", ssh_host]
        return [
            f"{self.source_dir / '../libcxx/utils/ssh.py'}",
            "--host",
            "cheribsd-test-instance",
            f"--extra-ssh-args=-F {self.test_ssh_config_path}",
            f"--extra-scp-args=-F {self.test_ssh_config_path}",
        ]

    def add_asan_flags(self):
        # Use asan+ubsan
        self.add_cmake_options(LLVM_USE_SANITIZER="Address")

    def add_msan_flags(self):
        self.add_cmake_options(LLVM_USE_SANITIZER="MemoryWithOrigins")

    def setup(self):
        super().setup()
        lit_args = f'--xunit-xml-output "{self.build_dir}/test-results.xml" --max-time 3600 --timeout 120 -sv'
        external_cxxabi = None
        enabled_runtimes = self.get_enabled_runtimes()
        # CMake will attempt to build a C++ executable when detecting compiler features, but
        # it's possible that this target is the actual provider of libc++ (e.g. for baremetal)
        if not self.compiling_for_host():
            self.add_cmake_options(_CMAKE_FEATURE_DETECTION_TARGET_TYPE="STATIC_LIBRARY")
        if self.target_info.is_freebsd() and self.llvm_project is not BuildUpstreamLLVM:
            # When targeting FreeBSD we use libcxxrt instead of the local libc++abi:
            with suppress(ValueError):
                enabled_runtimes.remove("libcxxabi")
            external_cxxabi = "libcxxrt"
        if self.llvm_project is BuildUpstreamLLVM and self.compiling_for_cheri():
            with suppress(ValueError):
                enabled_runtimes.remove("libunwind")  # CHERI fixes have not been upstreamed.
        target_test_flags = self.commandline_to_str(self.essential_compiler_and_linker_flags)

        self.add_cmake_options(LLVM_INCLUDE_TESTS=True)  # Ensure that we also build tests
        self.add_cmake_options(LLVM_ENABLE_ASSERTIONS=True)
        if "compiler-rt" in enabled_runtimes:
            self.add_cmake_options(
                COMPILER_RT_INCLUDE_TESTS=True,
                # Currently causes lots of test failures if true.
                # TODO: COMPILER_RT_DEBUG=self.build_type.is_debug,
                COMPILER_RT_DEBUG=False,
                COMPILER_RT_TEST_COMPILER_CFLAGS=self.commandline_to_str(self.essential_compiler_and_linker_flags),
                CMAKE_LINKER=self.target_info.linker,  # set to ld.lld to ensure compiler-rt tests run
            )
            if self.get_compiler_info(self.CC).is_clang:
                # Some of the tests (most/only compiler-rt) want to find the LLVM utilities. Make sure that the ones
                # from the compiler are picked up rather than whatever system-wide LLVM happens to be installed.
                self.add_cmake_options(LLVM_BINARY_DIR=self.CC.parent.parent)

        if "libunwind" in enabled_runtimes:
            self.add_cmake_options(
                LIBUNWIND_ENABLE_STATIC=True,
                LIBUNWIND_ENABLE_SHARED=not self.target_info.must_link_statically,
                LIBUNWIND_IS_BAREMETAL=self.target_info.is_baremetal(),
                LIBUNWIND_ENABLE_THREADS=not self.target_info.is_baremetal(),
                LIBUNWIND_USE_FRAME_HEADER_CACHE=not self.target_info.is_baremetal(),
                LIBUNWIND_TEST_TARGET_FLAGS=target_test_flags,
                LIBUNWIND_ENABLE_ASSERTIONS=True,
            )
            if "libcxxabi" in enabled_runtimes:
                self.add_cmake_options(LIBUNWIND_CXX_ABI="libcxxabi")
            if self.compiling_for_host() and (OSInfo.IS_MAC or OSInfo.is_ubuntu()):
                # Can't link libc++abi on MacOS and libsupc++ statically on Ubuntu
                self.add_cmake_options(LIBUNWIND_TEST_ENABLE_EXCEPTIONS=False)
                # Static linking is broken on Ubuntu 16.04
                self.add_cmake_options(LIBUINWIND_BUILD_STATIC_TEST_BINARIES=False)
            if self.target_info.is_baremetal():
                # work around error: use of undeclared identifier 'alloca', also stack is small
                self.add_cmake_options(LIBUNWIND_REMEMBER_HEAP_ALLOC=True)
        if "libcxxabi" in enabled_runtimes:
            self.add_cmake_options(
                LIBCXXABI_ENABLE_STATIC=True,
                LIBCXXABI_ENABLE_SHARED=not self.target_info.must_link_statically,
                LIBCXXABI_TEST_TARGET_FLAGS=target_test_flags,
            )
            if "libunwind" in enabled_runtimes:
                self.add_cmake_options(LIBCXXABI_USE_LLVM_UNWINDER=True, LIBCXXABI_ENABLE_STATIC_UNWINDER=True)
            if self.target_info.is_baremetal():
                self.add_cmake_options(
                    LIBCXXABI_ENABLE_THREADS=False,
                    LIBCXXABI_NON_DEMANGLING_TERMINATE=True,  # reduces code size
                    LIBCXXABI_BAREMETAL=True,
                )
        if "libcxx" in enabled_runtimes:
            self.add_cmake_options(
                LIBCXX_ENABLE_SHARED=not self.target_info.must_link_statically,
                LIBCXX_ENABLE_STATIC=True,
                LIBCXX_INCLUDE_TESTS=True,
                LIBCXX_ENABLE_EXCEPTIONS=not self.target_info.is_baremetal(),
                LIBCXX_ENABLE_RTTI=True,  # Ensure typeinfo symbols are always available
                LIBCXX_TEST_TARGET_FLAGS=target_test_flags,
            )
            if external_cxxabi is not None:
                self.add_cmake_options(LIBCXX_CXX_ABI=external_cxxabi)
                if not self.compiling_for_host():
                    self.add_cmake_options(
                        LIBCXX_CXX_ABI_INCLUDE_PATHS=self.sdk_sysroot / "usr/include/c++/v1/",
                        LIBCXX_CXX_ABI_LIBRARY_PATH=self.sdk_sysroot / "usr" / self.target_info.default_libdir,
                    )
                # LIBCXX_ENABLE_ABI_LINKER_SCRIPT is needed if we use libcxxrt/system libc++abi in the tests
                self.add_cmake_options(LIBCXX_ENABLE_STATIC_ABI_LIBRARY=False)
                self.add_cmake_options(LIBCXX_ENABLE_ABI_LINKER_SCRIPT=not self.target_info.must_link_statically)
            else:
                # When using the locally-built libc++abi, we link the ABI library objects as part of libc++.so
                assert "libcxxabi" in enabled_runtimes, enabled_runtimes
                self.add_cmake_options(LIBCXX_CXX_ABI="libcxxabi")
                self.add_cmake_options(LIBCXX_ENABLE_STATIC_ABI_LIBRARY=True)
            if self.target_info.is_baremetal():
                self.add_cmake_options(
                    LIBCXX_ENABLE_THREADS=False,
                    LIBCXX_ENABLE_PARALLEL_ALGORITHMS=False,
                    LIBCXX_ENABLE_MONOTONIC_CLOCK=False,  # Missing CLOCK_MONOTONIC support.
                    LIBCXX_ENABLE_FILESYSTEM=False,  # no <dirent.h>
                    LIBCXX_ENABLE_RANDOM_DEVICE=False,  # no /dev/urandom or similar entropy source
                    LIBCXX_ENABLE_LOCALIZATION=True,  # NB: locales are required for <iostream>
                    LIBCXX_ENABLE_WIDE_CHARACTERS=False,  # mostly there but missing wcstold()
                    # TODO: to reduce size:
                    # LIBCXX_ENABLE_LOCALIZATION=False,  # NB: locales are required for <iostream>
                    # LIBCXX_ENABLE_UNICODE=False,  # reduce size
                )

        if self.target_info.is_baremetal():
            # pretend that we are a UNIX platform to prevent CMake errors in HandleLLVMOptions.cmake
            self.add_cmake_options(UNIX=1)
            self.COMMON_FLAGS.append("-D_GNU_SOURCE=1")  # strtoll_l is guarded by __GNU_VISIBLE

        test_executor: "list[str]" = []
        if not self.compiling_for_host():
            if self.target_info.is_baremetal() and (self.source_dir / "../libcxx/utils/qemu_baremetal.py").exists():
                test_executor = [
                    str(self.source_dir / "../libcxx/utils/qemu_baremetal.py"),
                    f"--qemu={BuildQEMU.qemu_binary(self)}",
                ]
                lit_args += " -Dlong_tests=False"
                self.source_dir / "../libcxx/utils/qemu_baremetal.py"
            elif self._have_compile_only_executor():
                # The compile_only executor does not exist for upstream (yet)
                test_executor = [str(self.source_dir / "../libcxx/utils/compile_only.py")]
            elif self.qemu_instance is not None:
                test_executor = self.get_localhost_test_executor_command()
                # Trying to run more than one test in parallel will fail since we only have one CPU:
                # sshd[798]: error: beginning MaxStartups throttling
                # sshd[838]: error: no more sessions
                lit_args += " -j1"
        elif self.test_localhost_via_ssh:
            test_executor = self.get_localhost_test_executor_command()
        if test_executor:
            self.set_cmake_flag_for_each_runtime(EXECUTOR=self.commandline_to_str(test_executor))
        # The cheribuild default RPATH settings break the linker script (but should also be unnecessary without it).
        self.add_cmake_options(
            CMAKE_INSTALL_RPATH_USE_LINK_PATH=False,
            CMAKE_BUILD_RPATH_USE_ORIGIN=False,
            CMAKE_INSTALL_RPATH="",
            _replace=True,
        )
        self.add_cmake_options(LLVM_ENABLE_RUNTIMES=";".join(enabled_runtimes), LLVM_LIT_ARGS=lit_args)

    def _have_compile_only_executor(self):
        # The compile_only executor does not exist upstream (yet).
        return self.llvm_project is BuildCheriLLVM

    def set_cmake_flag_for_each_runtime(self, **kwargs):
        flags = {f"{x.upper()}_{k}": v for x in self.get_enabled_runtimes() for k, v in kwargs.items()}
        self.add_cmake_options(**flags)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        if cls.get_crosscompile_target().is_native():
            cls.test_localhost_via_ssh = cls.add_bool_option(
                "test-localhost-via-ssh",
                help="Use the ssh.py executor for localhost (to check that it works correctly)",
            )
        if not cls.get_crosscompile_target().is_native():
            cls.test_against_running_qemu_instance = cls.add_bool_option(
                "test-against-running-qemu-instance",
                help="Run tests against a currently running QEMU instance using the ssh.py executor.",
            )
        cls.qemu_test_jobs = cls.add_config_option(
            "parallel-qemu-test-jobs",
            help="Number of QEMU instances spawned to run tests (default: number of build jobs (-j flag) / 2)",
            default=lambda c, p: max(c.make_jobs / 2, 1),
            kind=int,
        )

    def configure(self, **kwargs) -> None:
        if "libcxx" in self.get_enabled_runtimes():
            if GitRepository.contains_commit(self, "64d413efdd76f2e6464ae6f578161811b9d12411", src_dir=self.source_dir):
                self.add_cmake_options(LIBCXX_HARDENING_MODE="extensive")
            else:
                self.add_cmake_options(LIBCXX_ENABLE_ASSERTIONS=True)
                # Need to export the symbols from debug.cpp to allow linking code that defines _LIBCPP_DEBUG=1
                self.add_cmake_options(LIBCXX_ENABLE_BACKWARDS_COMPATIBILITY_DEBUG_MODE_SYMBOLS=True)
        super().configure(**kwargs)

    def compile(self, **kwargs):
        if self.qemu_instance is not None:
            config_contents = generate_ssh_config_file_for_qemu(
                ssh_port=self.qemu_instance.ssh_forwarding_port,
                ssh_key=self.config.test_ssh_key,
                config=self.config,
            )
            self.write_file(
                self.test_ssh_config_path,
                contents=config_contents,
                overwrite=True,
                print_verbose_only=False,
            )
        return super().compile()

    def run_tests(self):
        test_jobs = self.config.make_jobs
        executor_lit_args = []
        if self.test_against_running_qemu_instance:
            test_jobs = 1
            if not ssh_host_accessible_uncached(
                "cheribsd-test-instance",
                ssh_args=("-F", str(self.test_ssh_config_path)),
                config=self.config,
            ):
                ssh_key_contents = self.config.test_ssh_key.read_text(encoding="utf-8").strip()
                return self.fatal(
                    "Cannot connect to ssh host",
                    fixit_hint=f"Try running `cheribuild.py {self.qemu_instance.target}`.\n"
                    + coloured(AnsiColour.blue, "If that does not work, try running ")
                    + coloured(AnsiColour.yellow, f"echo '{ssh_key_contents}' >> /root/.ssh/authorized_keys")
                    + coloured(AnsiColour.blue, " inside QEMU."),
                )
            executor_lit_args = ["-Dexecutor=" + self.commandline_to_str(self.get_localhost_test_executor_command())]
        elif self.can_run_binaries_on_remote_morello_board():
            executor = [self.source_dir / "utils/ssh.py", "--host", self.config.remote_morello_board]
            executor_lit_args = ["-Dexecutor=" + self.commandline_to_str(executor)]
            # The Morello board has 4 CPUs, so run 4 tests in parallel.
            test_jobs = 4
        elif self.target_info.is_cheribsd() and not self.compiling_for_host():
            test_jobs = self.qemu_test_jobs
            if "libunwind" in self.get_enabled_runtimes():
                self.target_info.run_cheribsd_test_script(
                    "run_libunwind_tests.py",
                    "--lit-debug-output",
                    "--ssh-executor-script",
                    self.source_dir / "../libcxx/utils/ssh.py",
                    mount_sysroot=True,
                )
            if "libcxx" in self.get_enabled_runtimes():
                self.target_info.run_cheribsd_test_script(
                    "run_libcxx_tests.py",
                    "--lit-debug-output",
                    "--ssh-executor-script",
                    self.source_dir / "../libcxx/utils/ssh.py",
                    "--parallel-jobs",
                    test_jobs,
                    mount_sysroot=True,
                )
            return
        # Without setting LC_ALL lit attempts to encode some things as ASCII and fails.
        # This only happens on FreeBSD, but we might as well set it everywhere
        with self.set_env(
            LC_ALL="en_US.UTF-8",
            FILECHECK_DUMP_INPUT_ON_FAILURE=1,
            LIT_OPTS=self.commandline_to_str(extra_llvm_lit_opts(self, test_jobs=test_jobs) + executor_lit_args),
            print_verbose_only=False,
        ):
            self.run_cmd("cmake", "--build", self.build_dir, "--verbose", "--target", "check-runtimes")


class _HostCompilerMixin(_BuildLlvmRuntimes if typing.TYPE_CHECKING else object):
    supported_architectures = CompilationTargets.ALL_NATIVE
    default_architecture = CompilationTargets.NATIVE

    @property
    def custom_c_preprocessor(self):
        assert self.compiling_for_host()
        return self.target_info.host_c_preprocessor(self.config)

    @property
    def custom_c_compiler(self):
        assert self.compiling_for_host()
        return self.target_info.host_c_compiler(self.config)

    @property
    def custom_cxx_compiler(self):
        assert self.compiling_for_host()
        return self.target_info.host_cxx_compiler(self.config)


class _UpstreamLLVMMixin(_BuildLlvmRuntimes if typing.TYPE_CHECKING else object):
    llvm_project: "typing.ClassVar[type[BuildLLVMMonoRepoBase]]" = BuildUpstreamLLVM
    supported_architectures = (
        CompilationTargets.ALL_NATIVE
        + CompilationTargets.ALL_PICOLIBC_TARGETS
        + CompilationTargets.ALL_SUPPORTED_FREEBSD_TARGETS
        + CompilationTargets.ALL_CHERIBSD_NON_CHERI_TARGETS
        + CompilationTargets.ALL_CHERIBSD_NON_CHERI_FOR_PURECAP_ROOTFS_TARGETS
    )


class BuildLibunwind(_BuildLlvmRuntimes):
    target = "libunwind"
    llvm_project = BuildCheriLLVM
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS
    default_architecture = CompilationTargets.NATIVE
    default_build_type = BuildType.DEBUG
    _enabled_runtimes: "typing.ClassVar[tuple[str, ...]]" = ("libunwind",)


class BuildLibunwindWithHostCompiler(_HostCompilerMixin, BuildLibunwind):
    target = "libunwind-with-host-compiler"


class BuildUpstreamLibunwind(_UpstreamLLVMMixin, BuildLibunwind):
    target = "upstream-libunwind"


class BuildUpstreamLibunwindWithHostCompiler(_HostCompilerMixin, BuildUpstreamLibunwind):
    target = "upstream-libunwind-with-host-compiler"


class BuildCompilerRtRuntimesBuild(_BuildLlvmRuntimes):
    target = "compiler-rt-runtimes-build"
    llvm_project = BuildCheriLLVM
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS
    default_architecture = CompilationTargets.NATIVE
    default_build_type = BuildType.DEBUG
    _enabled_runtimes: "typing.ClassVar[tuple[str, ...]]" = ("compiler-rt",)


class BuildCompilerRtRuntimesBuildWithHostCompiler(_HostCompilerMixin, BuildCompilerRtRuntimesBuild):
    target = "compiler-rt-runtimes-build-with-host-compiler"


class BuildUpstreamCompilerRtRuntimesBuild(_UpstreamLLVMMixin, BuildCompilerRtRuntimesBuild):
    target = "upstream-compiler-rt-runtimes-build"


class BuildUpstreamCompilerRtRuntimesBuildWithHostCompiler(_HostCompilerMixin, BuildUpstreamCompilerRtRuntimesBuild):
    target = "upstream-compiler-rt-runtimes-build-with-host-compiler"


class BuildLlvmLibs(_BuildLlvmRuntimes):
    target = "llvm-libs"
    llvm_project = BuildCheriLLVM
    supported_architectures = (
        CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS + CompilationTargets.ALL_PICOLIBC_TARGETS
    )
    default_architecture = CompilationTargets.NATIVE
    default_build_type = BuildType.DEBUG

    @classproperty
    def cross_install_dir(self):
        # For picolibc, we do actually want to install to the sysroot as this target provides the C++ standard library.
        if self._xtarget in CompilationTargets.ALL_PICOLIBC_TARGETS:
            return DefaultInstallDir.ROOTFS_LOCALBASE
        return super().cross_install_dir


class BuildLlvmLibsWithHostCompiler(_HostCompilerMixin, BuildLlvmLibs):
    target = "llvm-libs-with-host-compiler"


class BuildUpstreamLlvmLibs(_UpstreamLLVMMixin, _BuildLlvmRuntimes):
    target = "upstream-llvm-libs"
    default_architecture = CompilationTargets.NATIVE
    default_build_type = BuildType.DEBUG


class BuildUpstreamLlvmLibsWithHostCompiler(_HostCompilerMixin, BuildUpstreamLlvmLibs):
    target = "upstream-llvm-libs-with-host-compiler"
