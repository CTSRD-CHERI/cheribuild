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
import subprocess
import sys

from .crosscompileproject import (CheriConfig, CompilationTargets, CrossCompileCMakeProject, DefaultInstallDir,
                                  GitRepository)
from .llvm import BuildCheriLLVM, BuildUpstreamLLVM
from ..build_qemu import BuildQEMU
from ..cmake_project import CMakeProject
from ..project import ReuseOtherProjectDefaultTargetRepository
from ..run_qemu import LaunchCheriBSD
from ...config.chericonfig import BuildType
from ...utils import OSInfo


# A base class to set the default installation directory
class _CxxRuntimeCMakeProject(CrossCompileCMakeProject):
    do_not_add_to_targets = True
    cross_install_dir = DefaultInstallDir.SYSROOT_FOR_BAREMETAL_ROOTFS_OTHERWISE
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY

    @property
    def _rootfs_install_dir_name(self):
        return "c++"


class BuildLibunwind(_CxxRuntimeCMakeProject):
    # TODO: add an option to allow upstream llvm?
    repository = ReuseOtherProjectDefaultTargetRepository(BuildCheriLLVM, subdirectory="libunwind")
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # self.add_cmake_options(LIBUNWIND_HAS_DL_LIB=False)  # Adding -ldl won't work: no libdl in /usr/lib64c

    def configure(self, **kwargs):
        # TODO: should share some code with libcxx
        # to find the libcxx lit config files and library:
        test_compiler_flags = self.commandline_to_str(self.default_compiler_flags)
        test_linker_flags = self.commandline_to_str(self.default_ldflags)

        cxx_instance = BuildLibCXX.get_instance(self)
        if self.compiling_for_mips(include_purecap=True) and self.target_info.is_freebsd():
            # libcxxrt requires __floatundidf/__fixunsdfdi
            test_linker_flags += " -lcompiler_rt"
        self.add_cmake_options(LIBUNWIND_LIBCXX_PATH=cxx_instance.source_dir,
                               # Should use libc++ from sysroot
                               # LIBUNWIND_LIBCXX_LIBRARY_PATH=BuildLibCXX.get_build_dir(self) / "lib",
                               LIBUNWIND_LIBCXX_LIBRARY_PATH="",
                               LIBUNWIND_TEST_LINKER_FLAGS=test_linker_flags,
                               LIBUNWIND_TEST_COMPILER_FLAGS=test_compiler_flags,
                               # For the test binaries we link libcxxrt statically
                               LIBUNWIND_TEST_CXX_ABI_LIB_PATH=BuildLibCXXRT.get_build_dir(self) / "lib/libcxxrt.a",
                               LIBUNWIND_ENABLE_ASSERTIONS=True,
                               )
        # Lit multiprocessing seems broken with python 2.7 on FreeBSD (and python 3 seems faster at least for
        # libunwind/libcxx)
        self.add_cmake_options(PYTHON_EXECUTABLE=sys.executable)
        if self.compiling_for_host():
            if OSInfo.IS_MAC or OSInfo.is_ubuntu():
                # Can't link libc++abi on MacOS and libsupc++ statically on Ubuntu
                self.add_cmake_options(LIBUNWIND_TEST_ENABLE_EXCEPTIONS=False)
                # Static linking is broken on Ubuntu 16.04
                self.add_cmake_options(LIBUINWIND_BUILD_STATIC_TEST_BINARIES=False)
        else:
            self.add_cmake_options(LIBCXX_ENABLE_SHARED=False,
                                   LIBUNWIND_ENABLE_STATIC=True,
                                   LIBUNWIND_ENABLE_SHARED=not self.target_info.must_link_statically)
            # collect_test_binaries = self.build_dir / "test-output"
            # executor = self.commandline_to_str([self.source_dir / "../libcxx/utils/copy_files.py",
            #                                "--output-dir", collect_test_binaries])
            executor = self.commandline_to_str([self.source_dir / "../libcxx/utils/compile_only.py"])
            self.add_cmake_options(
                LLVM_LIT_ARGS="--xunit-xml-output " + os.getenv("WORKSPACE", ".") +
                              "/libunwind-test-results.xml --max-time 3600 --timeout 120 -s -vv -j1",
                LIBUNWIND_TARGET_TRIPLE=self.target_info.target_triple, LIBUNWIND_SYSROOT=self.sdk_sysroot)

            target_info = "libcxx.test.target_info.CheriBSDRemoteTI"
            # add the config options required for running tests:
            self.add_cmake_options(LIBUNWIND_EXECUTOR=executor, LIBUNWIND_TARGET_INFO=target_info,
                                   LIBUNWIND_CXX_ABI_LIBNAME="libcxxrt")
            version_script = self.source_dir / "Version.map.FreeBSD"
            if not version_script.exists():
                self.fatal("libunwind version script is missing, please update llvm-project!")
            self.add_cmake_options(LIBUNWIND_USE_VERSION_SCRIPT=version_script)

        # Do not link against libgcc_s when building the shared library:
        self.add_cmake_options(LIBUNWIND_USE_COMPILER_RT=True)
        super().configure(**kwargs)

    def run_tests(self):
        if self.target_info.is_baremetal():
            self.info("Baremetal tests not implemented")
            return
        if self.compiling_for_host():
            self.run_make("check-unwind", cwd=self.build_dir)
        else:
            # Check that the four tests compile and then attempt to run them:
            self.run_make("check-unwind", cwd=self.build_dir)
            self.target_info.run_cheribsd_test_script("run_libunwind_tests.py", "--lit-debug-output",
                                                      "--ssh-executor-script",
                                                      self.source_dir / "../libcxx/utils/ssh.py",
                                                      mount_sysroot=True)


class BuildLibCXXRT(_CxxRuntimeCMakeProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/libcxxrt.git")
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "list[str]":
        result = super().dependencies(config)
        return result + ["libunwind"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if not self.target_info.is_baremetal():
            self.add_cmake_options(LIBUNWIND_PATH=BuildLibunwind.get_install_dir(self) / "lib",
                                   CMAKE_INSTALL_RPATH_USE_LINK_PATH=True)
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
            self.add_cmake_options(NO_SHARED=self.force_static_linkage,
                                   DISABLE_EXCEPTIONS_RTTI=False,
                                   NO_UNWIND_LIBRARY=False)
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
                self.target_info.run_cheribsd_test_script("run_libcxxrt_tests.py",
                                                          "--libunwind-build-dir", BuildLibunwind.get_build_dir(self),
                                                          mount_builddir=True, mount_sysroot=True)


def _default_ssh_port(c, p: CMakeProject):
    xtarget = p.get_crosscompile_target()
    if not xtarget.target_info_cls.is_cheribsd():
        return None
    return LaunchCheriBSD.get_instance(p, c, cross_target=xtarget.get_rootfs_target()).ssh_forwarding_port


class BuildLibCXX(_CxxRuntimeCMakeProject):
    # TODO: add an option to allow upstream llvm?
    repository = ReuseOtherProjectDefaultTargetRepository(BuildCheriLLVM, subdirectory="libcxx")
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS
    dependencies = ["libcxxrt"]

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.only_compile_tests = cls.add_bool_option("only-compile-tests",
                                                     help="Don't attempt to run tests, only compile them")
        cls.exceptions = cls.add_bool_option("exceptions", default=True, help="Build with support for C++ exceptions")
        cls.collect_test_binaries = cls.add_path_option("collect-test-binaries", metavar="TEST_PATH",
                                                        help="Instead of running tests copy them to $TEST_PATH")
        cls.nfs_mounted_path = cls.add_path_option("nfs-mounted-path", metavar="PATH",
                                                   help="Use a PATH as a directorythat is NFS mounted inside QEMU "
                                                        "instead of using scp to copy individual tests")
        cls.nfs_path_in_qemu = cls.add_path_option("nfs-mounted-path-in-qemu", metavar="PATH",
                                                   help="The path used inside QEMU to refer to nfs-mounted-path")
        cls.qemu_host = cls.add_config_option("ssh-host", help="The QEMU SSH hostname to connect to for running tests",
                                              default="localhost")
        cls.qemu_port = cls.add_config_option("ssh-port", help="The QEMU SSH port to connect to for running tests",
                                              _allow_unknown_targets=True, default=_default_ssh_port,
                                              only_add_for_targets=CompilationTargets.ALL_SUPPORTED_CHERIBSD_TARGETS)
        cls.qemu_user = cls.add_config_option("ssh-user", default="root", help="The CheriBSD used for running tests")

        cls.test_jobs = cls.add_config_option("parallel-test-jobs",
                                              help="Number of QEMU instances spawned to run tests "
                                                   "(default: number of build jobs (-j flag) / 2)",
                                              default=lambda c, p: c.make_jobs / 2, kind=int)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
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
            LLVM_LIT_ARGS="--xunit-xml-output " + os.getenv("WORKSPACE", ".") +
                          "/libcxx-test-results.xml --max-time 3600 --timeout 120 -s -vv" + self.libcxx_lit_jobs
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
                LIBCXX_CXX_ABI_LIBRARY_PATH=BuildLibCXXRT.get_build_dir(self) / "lib")
            if not self.target_info.is_baremetal():
                # use llvm libunwind when testing
                self.add_cmake_options(LIBCXX_STATIC_CXX_ABI_LIBRARY_NEEDS_UNWIND_LIBRARY=True,
                                       LIBCXX_CXX_ABI_UNWIND_LIBRARY="unwind",
                                       LIBCXX_CXX_ABI_UNWIND_LIBRARY_PATH=BuildLibunwind.get_build_dir(self) / "lib")

        if not self.exceptions or self.target_info.is_baremetal():
            self.add_cmake_options(LIBCXX_ENABLE_EXCEPTIONS=False, LIBCXX_ENABLE_RTTI=False)
        else:
            self.add_cmake_options(LIBCXX_ENABLE_EXCEPTIONS=True, LIBCXX_ENABLE_RTTI=True)
        # TODO: remove this once stuff has been fixed:
        self.common_warning_flags.append("-Wno-ignored-attributes")

    def add_cross_flags(self):
        # TODO: do I even need the toolchain file to cross compile?

        self.add_cmake_options(LIBCXX_TARGET_TRIPLE=self.target_info.target_triple,
                               LIBCXX_SYSROOT=self.sdk_sysroot)

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

        self.add_cmake_options(LIBCXX_TEST_COMPILER_FLAGS=test_compile_flags,
                               LIBCXX_TEST_LINKER_FLAGS=test_linker_flags,
                               LIBCXX_SLOW_TEST_HOST=True)  # some tests need more tolerance/less iterations on QEMU

        self.add_cmake_options(
            LIBCXX_ENABLE_SHARED=False,  # not yet
            LIBCXX_ENABLE_STATIC=True,
            LIBCXX_ENABLE_THREADS=not self.target_info.is_baremetal(),  # no threads on baremetal newlib
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
            executor = self.commandline_to_str([self.source_dir / "utils/copy_files.py",
                                                "--output-dir", self.collect_test_binaries])
        elif self.target_info.is_baremetal():
            run_qemu_script = self.target_info.sdk_root_dir / "baremetal/mips64-qemu-elf/bin/run_with_qemu.py"
            if not run_qemu_script.exists():
                self.warning("run_with_qemu.py is needed to run libcxx baremetal tests but could not find it:",
                             run_qemu_script, "does not exist")
            prefix = [str(run_qemu_script), "--qemu", str(BuildQEMU.qemu_binary(self)), "--timeout", "20"]
            prefix_list = '[\\\"' + "\\\", \\\"".join(prefix) + "\\\"]"
            executor = "PrefixExecutor(" + prefix_list + ", LocalExecutor())"
        elif self.nfs_mounted_path:
            self.libcxx_lit_jobs = " -j1"  # We can only run one job here since we are using scp
            self.fatal("nfs_mounted_path not portend to new libc++ test infrastructure yet")
            executor = "SSHExecutorWithNFSMount(\\\"{host}\\\", nfs_dir=\\\"{nfs_dir}\\\"," \
                       "path_in_target=\\\"{nfs_in_target}\\\", config=self, username=\\\"{user}\\\"," \
                       " port={port})".format(host=self.qemu_host, user=self.qemu_user, port=self.qemu_port,
                                              nfs_dir=self.nfs_mounted_path, nfs_in_target=self.nfs_path_in_qemu)
        else:
            self.libcxx_lit_jobs = " -j1"  # We can only run one job here since we are using scp
            executor = self.commandline_to_str([self.source_dir / "utils/ssh.py",
                                                "--host", "{user}@{host}:{port}".format(host=self.qemu_host,
                                                                                        user=self.qemu_user,
                                                                                        port=self.qemu_port)])
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
            self.run_cmd([sys.executable, self.build_dir / "bin/llvm-lit", "-j4", "-vv",
                          f"--xunit-xml-output={self.build_dir / 'test-results.xml'}",
                          "-Dexecutor=" + self.commandline_to_str(executor), "test"], cwd=self.build_dir)
        else:
            # long running test -> speed up by using a kernel without invariants
            self.target_info.run_cheribsd_test_script("run_libcxx_tests.py", "--parallel-jobs", self.test_jobs,
                                                      "--ssh-executor-script", self.source_dir / "utils/ssh.py",
                                                      use_benchmark_kernel_by_default=True)


class BuildLlvmLibs(CMakeProject):
    target = "llvm-libs"
    repository = ReuseOtherProjectDefaultTargetRepository(BuildCheriLLVM, subdirectory="llvm")
    llvm_project = BuildCheriLLVM
    # TODO: support cross-compilation
    supported_architectures = [CompilationTargets.NATIVE]
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    dependencies = ["llvm"]
    default_build_type = BuildType.DEBUG

    @property
    def custom_c_preprocessor(self):
        return self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/clang-cpp"

    @property
    def custom_c_compiler(self):
        return self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/clang"

    @property
    def custom_cxx_compiler(self):
        return self.llvm_project.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/clang++"

    def setup(self):
        super().setup()
        lit_args = "--xunit-xml-output " + os.getenv("WORKSPACE", ".") + \
                   "/test-results.xml --max-time 3600 --timeout 120 -s -vv"
        self.add_cmake_options(LLVM_ENABLE_PROJECTS="libunwind;libcxxabi;libcxx",
                               # ;compiler-rt
                               LIBCXX_ENABLE_SHARED=True,
                               LIBCXX_ENABLE_STATIC=True,
                               LIBCXX_CXX_ABI="libcxxabi",
                               LIBCXX_USE_COMPILER_RT=False,
                               LIBCXXABI_USE_LLVM_UNWINDER=True,
                               CMAKE_INSTALL_RPATH_USE_LINK_PATH=True,  # Fix finding libunwind.so
                               LIBCXX_INCLUDE_TESTS=True,
                               LLVM_LIT_ARGS=lit_args,
                               LIBCXX_ENABLE_EXCEPTIONS=True,
                               LIBCXX_ENABLE_RTTI=True,
                               )
        if not self.target_info.is_macos():
            self.add_cmake_options(LIBCXX_ENABLE_STATIC_ABI_LIBRARY=True)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.test_localhost_via_ssh = cls.add_bool_option("test-localhost-via-ssh",
                                                         help="Use the ssh.py executor for localhost (to check that "
                                                              "it works correctly)")

    def compile(self, **kwargs):
        self.run_make(["unwind", "cxxabi", "cxx"])

    def install(self, **kwargs):
        self.run_make_install(target=["install-unwind", "install-cxxabi", "install-cxx"])

    def run_tests(self):
        # We can't use check-all since that will (currently) also build and test LLVM and using the
        # individual check-* targets will overwrite the XML output.
        # We could rename and merge the output files, but it seems simpler to invoke lit directly:
        # self.run_make(["check-unwind", "check-cxxabi", "check-cxx"], cwd=self.build_dir)
        args = ["--xunit-xml-output", "./llvm-libs-test-results.xml",
                "--max-time", "3600", "--timeout", "120", "-s", "-vv",
                "projects/libcxx/test", "projects/libcxxabi/test", "projects/libunwind/test"]
        if self.test_localhost_via_ssh:
            ssh_host = self.config.get_user_name() + "@" + platform.node()
            try:
                self.run_cmd(["ssh", ssh_host, "--", "echo Success."])
            except subprocess.CalledProcessError:
                self.fatal(self.get_config_option_name("test_localhost_via_ssh"), "selected but cannot ssh to",
                           ssh_host)
            executor = self.commandline_to_str([self.source_dir / "../libcxx/utils/ssh.py", "--host", ssh_host])
            args.append("-Dexecutor=" + executor)
        self.run_cmd([sys.executable, "./bin/llvm-lit"] + args, cwd=self.build_dir)


class BuildUpstreamLlvmLibs(BuildLlvmLibs):
    target = "upstream-llvm-libs"
    repository = ReuseOtherProjectDefaultTargetRepository(BuildUpstreamLLVM, subdirectory="llvm")
    llvm_project = BuildUpstreamLLVM


class BuildUpstreamLlvmLibsWithHostCompiler(BuildLlvmLibs):
    target = "upstream-llvm-libs-with-host-compiler"
    repository = ReuseOtherProjectDefaultTargetRepository(BuildUpstreamLLVM, subdirectory="llvm")
    llvm_project = BuildUpstreamLLVM

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
