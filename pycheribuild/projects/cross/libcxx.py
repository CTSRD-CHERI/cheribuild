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

from .crosscompileproject import *
from ..build_qemu import BuildQEMU
from ..llvm import BuildCheriLLVM
from ..project import ReuseOtherProjectDefaultTargetRepository
from ..run_qemu import LaunchCheriBSD
from ...utils import OSInfo, setEnv, runCmd, warningMessage, commandline_to_str, IS_MAC


# A base class to set the default installation directory
class _CxxRuntimeCMakeProject(CrossCompileCMakeProject):
    doNotAddToTargets = True
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
        # Adding -ldl won't work: no libdl in /usr/libcheri
        self.add_cmake_options(LIBUNWIND_HAS_DL_LIB=False)
        self.lit_path = BuildCheriLLVM.getBuildDir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-lit"
        self.add_cmake_options(
            LLVM_PATH=BuildCheriLLVM.getSourceDir(self, cross_target=CompilationTargets.NATIVE) / "llvm",
            LLVM_EXTERNAL_LIT=self.lit_path,
            )

    def configure(self, **kwargs):
        # TODO: should share some code with libcxx
        # to find the libcxx lit config files and library:
        test_compiler_flags = commandline_to_str(self.default_compiler_flags)
        test_linker_flags = commandline_to_str(self.default_ldflags)

        cxx_instance = BuildLibCXX.get_instance(self)

        self.add_cmake_options(LIBUNWIND_LIBCXX_PATH=cxx_instance.sourceDir,
                               # Should use libc++ from sysroot
                               # LIBUNWIND_LIBCXX_LIBRARY_PATH=BuildLibCXX.getBuildDir(self) / "lib",
                               LIBUNWIND_LIBCXX_LIBRARY_PATH="",
                               LIBUNWIND_TEST_LINKER_FLAGS=test_linker_flags,
                               LIBUNWIND_TEST_COMPILER_FLAGS=test_compiler_flags,
                               # For the test binaries we link libcxxrt statically
                               LIBUNWIND_TEST_CXX_ABI_LIB=BuildLibCXXRT.getBuildDir(self) / "lib/libcxxrt.a",
                               LIBUNWIND_ENABLE_ASSERTIONS=True,
                               )
        # Lit multiprocessing seems broken with python 2.7 on FreeBSD (and python 3 seems faster at least for libunwind/libcxx)
        self.add_cmake_options(PYTHON_EXECUTABLE=sys.executable)
        if self.compiling_for_host():
            if IS_MAC or OSInfo.isUbuntu():
                # Can't link libc++abi on MacOS and libsupc++ statically on Ubuntu
                self.add_cmake_options(LIBUNWIND_TEST_ENABLE_EXCEPTIONS=False)
                # Static linking is broken on Ubuntu 16.04
                self.add_cmake_options(LIBUINWIND_BUILD_STATIC_TEST_BINARIES=False)
        else:
            self.add_cmake_options(LIBCXX_ENABLE_SHARED=False,
                                   LIBUNWIND_ENABLE_SHARED=True)
            collect_test_binaries = self.buildDir / "test-output"
            executor = "CollectBinariesExecutor(\\\"{path}\\\", self)".format(path=collect_test_binaries)
            self.add_cmake_options(
                LLVM_LIT_ARGS="--xunit-xml-output " + os.getenv("WORKSPACE", ".") +
                              "/libunwind-test-results.xml --max-time 3600 --timeout 120 -s -vv -j1",
                LIBUNWIND_TARGET_TRIPLE=self.target_info.target_triple, LIBUNWIND_SYSROOT=self.sdk_sysroot)

            target_info = "libcxx.test.target_info.CheriBSDRemoteTI"
            # add the config options required for running tests:
            self.add_cmake_options(LIBUNWIND_EXECUTOR=executor, LIBUNWIND_TARGET_INFO=target_info,
                                   LIBUNWIND_CXX_ABI_LIBNAME="libcxxrt")
            version_script = self.sourceDir / "Version.map.FreeBSD"
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
            runCmd("ninja", "check-unwind", "-v", cwd=self.buildDir)
        else:
            # Check that the four tests compile and then attempt to run them:
            # TODO: run the three combinations here too?
            runCmd("ninja", "check-unwind", "-v", cwd=self.buildDir)
            self.run_cheribsd_test_script("run_libunwind_tests.py", "--lit-debug-output",
                                          "--llvm-lit-path", self.lit_path, mount_sysroot=True)


class BuildLibCXXRT(_CxxRuntimeCMakeProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/libcxxrt.git")
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS

    @classmethod
    def dependencies(cls, config: CheriConfig):
        result = super().dependencies(config)
        return result + ["libunwind"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if not self.target_info.is_baremetal():
            self.add_cmake_options(LIBUNWIND_PATH=BuildLibunwind.getInstallDir(self) / "lib",
                                   CMAKE_INSTALL_RPATH_USE_LINK_PATH=True)
        if self.compiling_for_host():
            assert not self.target_info.is_baremetal()
            self.add_cmake_options(BUILD_TESTS=True, TEST_LIBUNWIND=True)
            if OSInfo.isUbuntu():
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
        libdir = self.installDir / "libcheri" if self.compiling_for_cheri() else self.installDir / "lib"
        self.installFile(self.buildDir / "lib/libcxxrt.a", libdir / "libcxxrt.a", force=True)
        # self.installFile(self.buildDir / "lib/libcxxrt.a", libdir / "libcxxrt.so", force=True)
        # self.installFile(self.buildDir / "lib/libcxxrt.so", self.installDir / "usr/libcheri/libcxxrt.so", force=True)

    def run_tests(self):
        if self.target_info.is_baremetal():
            self.info("Baremetal tests not implemented")
            return
        # TODO: this won't work on macOS
        with setEnv(LD_LIBRARY_PATH=self.buildDir / "lib"):
            if self.compiling_for_host():
                runCmd("ctest", ".", "-VV", cwd=self.buildDir)
            else:
                self.run_cheribsd_test_script("run_libcxxrt_tests.py",
                                              "--libunwind-build-dir", BuildLibunwind.getBuildDir(self),
                                              mount_builddir=True, mount_sysroot=True)


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
        cls.nfs_mounted_path = cls.add_path_option("nfs-mounted-path", metavar="PATH", help="Use a PATH as a directory"
                                                                                          "that is NFS mounted inside QEMU instead of using scp to copy "
                                                                                          "individual tests")
        cls.nfs_path_in_qemu = cls.add_path_option("nfs-mounted-path-in-qemu", metavar="PATH",
                                                 help="The path used inside QEMU to refer to nfs-mounted-path")
        cls.qemu_host = cls.add_config_option("ssh-host", help="The QEMU SSH hostname to connect to for running tests",
                                            default=lambda c, p: "localhost")
        cls.qemu_port = cls.add_config_option("ssh-port",
            help="The QEMU SSH port to connect to for running tests", _allow_unknown_targets=True,
            default=lambda c, p: LaunchCheriBSD.get_instance(p, c, cross_target=CompilationTargets.CHERIBSD_MIPS_HYBRID).sshForwardingPort,
            only_add_for_targets=[CompilationTargets.CHERIBSD_MIPS_PURECAP, CompilationTargets.CHERIBSD_MIPS_HYBRID, CompilationTargets.CHERIBSD_MIPS_NO_CHERI])
        cls.qemu_user = cls.add_config_option("ssh-user", default="root", help="The CheriBSD used for running tests")

        cls.test_jobs = cls.add_config_option("parallel-test-jobs", help="Number of QEMU instances spawned to run tests "
                                                                       "(default: number of build jobs (-j flag) / 2)",
                                            default=lambda c, p: c.makeJobs / 2, kind=int)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if self.qemu_host:
            self.qemu_host = os.path.expandvars(self.qemu_host)
        self.libcxx_lit_jobs = ""

        if self.compiling_for_host():
            self.add_cmake_options(LIBCXX_ENABLE_SHARED=True, LIBCXX_ENABLE_STATIC_ABI_LIBRARY=False)
            if OSInfo.isUbuntu():
                # Ubuntu packagers think that static linking should not be possible....
                self.add_cmake_options(LIBCXX_ENABLE_STATIC=False)
        else:
            self.addCrossFlags()
        # add the common test options
        self.add_cmake_options(
            CMAKE_INSTALL_RPATH_USE_LINK_PATH=True,  # Fix finding libunwind.so
            LIBCXX_INCLUDE_TESTS=True,
            LLVM_PATH=BuildCheriLLVM.getSourceDir(self, cross_target=CompilationTargets.NATIVE) / "llvm",
            LLVM_EXTERNAL_LIT=BuildCheriLLVM.getBuildDir(self, cross_target=CompilationTargets.NATIVE) / "bin/llvm-lit",
            LIBCXXABI_USE_LLVM_UNWINDER=False,  # we have a fake libunwind in libcxxrt
            LLVM_LIT_ARGS="--xunit-xml-output " + os.getenv("WORKSPACE", ".") +
                          "/libcxx-test-results.xml --max-time 3600 --timeout 120 -s -vv" + self.libcxx_lit_jobs
        )
        # Lit multiprocessing seems broken with python 2.7 on FreeBSD (and python 3 seems faster at least for libunwind/libcxx)
        self.add_cmake_options(PYTHON_EXECUTABLE=sys.executable)
        # select libcxxrt as the runtime library (except on macos where this doesn't seem to work very well)
        if not (self.compiling_for_host() and IS_MAC):
            self.add_cmake_options(
                LIBCXX_CXX_ABI="libcxxrt",
                LIBCXX_CXX_ABI_LIBNAME="libcxxrt",
                LIBCXX_CXX_ABI_INCLUDE_PATHS=BuildLibCXXRT.getSourceDir(self) / "src",
                LIBCXX_CXX_ABI_LIBRARY_PATH=BuildLibCXXRT.getBuildDir(self) / "lib")
            if not self.target_info.is_baremetal():
                # use llvm libunwind when testing
                self.add_cmake_options(LIBCXX_STATIC_CXX_ABI_LIBRARY_NEEDS_UNWIND_LIBRARY=True,
                                       LIBCXX_CXX_ABI_UNWIND_LIBRARY="unwind",
                                       LIBCXX_CXX_ABI_UNWIND_LIBRARY_PATH=BuildLibunwind.getBuildDir(self) / "lib")

        if not self.exceptions or self.target_info.is_baremetal():
            self.add_cmake_options(LIBCXX_ENABLE_EXCEPTIONS=False, LIBCXX_ENABLE_RTTI=False)
        else:
            self.add_cmake_options(LIBCXX_ENABLE_EXCEPTIONS=True, LIBCXX_ENABLE_RTTI=True)
        # TODO: remove this once stuff has been fixed:
        self.common_warning_flags.append("-Wno-ignored-attributes")
        print(self.common_warning_flags)

    def addCrossFlags(self):
        # TODO: do I even need the toolchain file to cross compile?

        self.add_cmake_options(LIBCXX_TARGET_TRIPLE=self.target_info.target_triple,
                               LIBCXX_SYSROOT=self.sdk_sysroot)

        if self.compiling_for_cheri():
            # Ensure that we don't have failing tests due to cheri bugs
            self.common_warning_flags.append("-Werror=cheri")

        # We need to build with -G0 otherwise we get R_MIPS_GPREL16 out of range linker errors
        test_compile_flags = commandline_to_str(self.default_compiler_flags)
        test_linker_flags = commandline_to_str(self.default_ldflags)
        print("test_compile_flags:", test_compile_flags)

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
                               LIBCXX_SLOW_TEST_HOST=True) # some tests need more tolerance/less iterations on QEMU

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
            executor = "CompileOnlyExecutor()"
        elif self.collect_test_binaries:
            executor = "CollectBinariesExecutor(\\\"{path}\\\", self)".format(path=self.collect_test_binaries)
        elif self.target_info.is_baremetal():
            run_qemu_script = self.target_info.sdk_root_dir / "baremetal/mips64-qemu-elf/bin/run_with_qemu.py"
            if not run_qemu_script.exists():
                warningMessage("run_with_qemu.py is needed to run libcxx baremetal tests but could not find it:",
                               run_qemu_script, "does not exist")
            prefix = [str(run_qemu_script), "--qemu", str(BuildQEMU.qemu_binary(self)), "--timeout", "20"]
            prefix_list = '[\\\"' + "\\\", \\\"".join(prefix) + "\\\"]"
            executor = "PrefixExecutor(" + prefix_list + ", LocalExecutor())"
            print(executor)
        elif self.nfs_mounted_path:
            self.libcxx_lit_jobs = " -j1" # We can only run one job here since we are using scp
            executor = "SSHExecutorWithNFSMount(\\\"{host}\\\", nfs_dir=\\\"{nfs_dir}\\\", path_in_target=\\\"{nfs_in_target}\\\"," \
                       " config=self, username=\\\"{user}\\\", port={port})".format(host=self.qemu_host, user=self.qemu_user,
                                                                                    port=self.qemu_port,
                                                                                    nfs_dir=self.nfs_mounted_path,
                                                                                    nfs_in_target=self.nfs_path_in_qemu)
        else:
            self.libcxx_lit_jobs = " -j1" # We can only run one job here since we are using scp
            executor = "SSHExecutor('{host}', username='{user}', port={port}, config=self)".format(
                host=self.qemu_host, user=self.qemu_user, port=self.qemu_port)
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
            runCmd("ninja", "check-cxx", "-v", cwd=self.buildDir)
        else:
            #  "--lit-debug-output"?
            self.run_cheribsd_test_script("run_libcxx_tests.py", "--parallel-jobs", self.test_jobs,
                                          # long running test -> speed up by using a kernel without invariants
                                          use_benchmark_kernel_by_default=True)
