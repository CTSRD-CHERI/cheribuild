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

from .crosscompileproject import *
from ..cheribsd import BuildCHERIBSD
from ..llvm import BuildLLVM
from ..run_qemu import LaunchCheriBSD
from ...config.loader import ComputedDefaultValue
from ...utils import OSInfo, statusUpdate
import os
from pathlib import Path

installToCXXDir = ComputedDefaultValue(
    function=lambda config, project: BuildCHERIBSD.rootfsDir(config) / "extra/c++",
    asString="$CHERIBSD_ROOTFS/extra/c++")


class BuildLibunwind(CrossCompileCMakeProject):
    repository = "https://github.com/CTSRD-CHERI/libunwind.git"
    defaultInstallDir = installToCXXDir

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # Adding -ldl won't work: no libdl in /usr/libcheri
        self.add_cmake_options(LIBUNWIND_HAS_DL_LIB=False)
        self.add_cmake_options(LLVM_CONFIG_PATH=self.compiler_dir / "llvm-config")
        # TODO: this breaks the build: LLVM_LIBDIR_SUFFIX="cheri"
        # Now that cheribsd includes libc++ we no longer need this:
        # self.COMMON_FLAGS.append("-isystem")
        # self.COMMON_FLAGS.append(str(BuildLibCXX.sourceDir / "include"))


class BuildLibCXXRT(CrossCompileCMakeProject):
    repository = "https://github.com/CTSRD-CHERI/libcxxrt.git"
    defaultInstallDir = installToCXXDir

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.add_cmake_options(LIBUNWIND_PATH=BuildLibunwind.installDir / "lib",
                               CMAKE_INSTALL_RPATH_USE_LINK_PATH=True)
        if self.compiling_for_host():
            self.add_cmake_options(BUILD_TESTS=True)
            if OSInfo.isUbuntu():
                self.add_cmake_options(COMPARE_TEST_OUTPUT_TO_SYSTEM_OUTPUT=False)
                # Seems to be needed for at least jenkins (it says relink with -pie)
                self.add_cmake_options(CMAKE_POSITION_INDEPENDENT_CODE=True)
                # The static libc.a on Ubuntu is not compiled with -fPIC so we can't link to it..
                self.add_cmake_options(NO_STATIC_TEST=True)
            self.add_cmake_options(NO_UNWIND_LIBRARY=False)
        else:
            # TODO: __sync_fetch_and_add in exceptions code
            self.add_cmake_options(NO_SHARED=True, DISABLE_EXCEPTIONS_RTTI=True, NO_UNWIND_LIBRARY=True)
            self.add_cmake_options(COMPARE_TEST_OUTPUT_TO_SYSTEM_OUTPUT=False)
            if not self.baremetal:
                self.add_cmake_options(BUILD_TESTS=True)

    def install(self, **kwargs):
        libdir = self.installDir / "libcheri" if self.compiling_for_cheri() else self.installDir / "lib"
        self.installFile(self.buildDir / "lib/libcxxrt.a", libdir / "libcxxrt.a", force=True)
        # self.installFile(self.buildDir / "lib/libcxxrt.a", libdir / "libcxxrt.so", force=True)
        # self.installFile(self.buildDir / "lib/libcxxrt.so", self.installDir / "usr/libcheri/libcxxrt.so", force=True)


class BuildLibCXX(CrossCompileCMakeProject):
    repository = "https://github.com/CTSRD-CHERI/libcxx.git"
    defaultInstallDir = installToCXXDir
    dependencies = ["libcxxrt"]

    use_libcxxrt = True

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.collect_test_binaries = cls.addPathOption("collect-test-binaries", metavar="TEST_PATH",
                                                      help="Instead of running tests copy them to $TEST_PATH")
        cls.nfs_mounted_path = cls.addPathOption("nfs-mounted-path", metavar="PATH", help="Use a PATH as a directory"
                                                                                          "that is NFS mounted inside QEMU instead of using scp to copy "
                                                                                          "individual tests")
        cls.nfs_path_in_qemu = cls.addPathOption("nfs-mounted-path-in-qemu", metavar="PATH",
                                                 help="The path used inside QEMU to refer to nfs-mounted-path")
        cls.qemu_host = cls.addConfigOption("ssh-host", help="The QEMU SSH hostname to connect to for running tests",
                                            default=lambda c, p: "localhost")
        cls.qemu_port = cls.addConfigOption("ssh-port", help="The QEMU SSH port to connect to for running tests",
                                            kind=str, default=lambda c, p: LaunchCheriBSD.sshForwardingPort)
        cls.qemu_user = cls.addConfigOption("shh-user", default="root", help="The CheriBSD used for running tests")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.COMMON_FLAGS.append("-D__LP64__=1")  # HACK to get it to compile
        if self.crossCompileTarget == CrossCompileTarget.NATIVE:
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
            # LLVM_CONFIG_PATH=BuildLLVM.buildDir / "bin/llvm-config",
            LLVM_CONFIG_PATH=self.config.sdkBinDir / "llvm-config",
            LLVM_EXTERNAL_LIT=BuildLLVM.buildDir / "bin/llvm-lit",
            LIBCXXABI_USE_LLVM_UNWINDER=False,  # we have a fake libunwind in libcxxrt
            LLVM_LIT_ARGS="--xunit-xml-output " + os.getenv("WORKSPACE", ".") +
                          "/lit-test-results.xml --max-time 3600 --timeout 120 -s -vv"
        )
        if self.use_libcxxrt:
            # select libcxxrt as the runtime library
            self.add_cmake_options(
                LIBCXX_CXX_ABI="libcxxrt",
                LIBCXX_CXX_ABI_LIBNAME="libcxxrt",
                LIBCXX_CXX_ABI_INCLUDE_PATHS=BuildLibCXXRT.sourceDir / "src",
                LIBCXX_CXX_ABI_LIBRARY_PATH=BuildLibCXXRT.buildDir / "lib",

            )
        else:
            self.add_cmake_options(LIBCXX_CXX_ABI="none")  # currently not built..

    def addCrossFlags(self):
        # TODO: do I even need the toolchain file to cross compile?

        self.add_cmake_options(LIBCXX_TARGET_TRIPLE=self.targetTriple,
                               LIBCXX_SYSROOT=self.sdkSysroot)

        self.add_cmake_options(
            LIBCXX_ENABLE_SHARED=False,  # not yet
            LIBCXX_ENABLE_STATIC=True,
            LIBCXX_ENABLE_EXPERIMENTAL_LIBRARY=False,  # not yet
            LIBCXX_INCLUDE_BENCHMARKS=False,
            LIBCXX_INCLUDE_DOCS=False,
            # exceptions and rtti still missing:
            LIBCXX_ENABLE_EXCEPTIONS=False,
            LIBCXX_ENABLE_RTTI=False,
            # When cross compiling we link the ABI library statically (except baremetal since that doens;t have it yet)
            LIBCXX_ENABLE_STATIC_ABI_LIBRARY=not self.baremetal,
        )
        if self.collect_test_binaries:
            executor = "CollectBinariesExecutor('{path}', self)".format(path=self.collect_test_binaries)
        elif self.nfs_mounted_path:
            executor = "SSHExecutorWithNFSMount('{host}', nfs_dir='{nfs_dir}', path_in_target='{nfs_in_target}'," \
                       " config=self, username='{user}', port={port})".format(host=self.qemu_host, user=self.qemu_user,
                                                                              port=self.qemu_port,
                                                                              nfs_dir=self.nfs_mounted_path,
                                                                              nfs_in_target=self.nfs_path_in_qemu)
        else:
            executor = "SSHExecutor('{host}', username='{user}', port={port})".format(
                host=self.qemu_host, user=self.qemu_user, port=self.qemu_port)
        # add the config options required for running tests:
        if not self.baremetal:
            self.add_cmake_options(
                LIBCXX_EXECUTOR=executor,
                LIBCXX_TARGET_INFO="libcxx.test.target_info.CheriBSDRemoteTI",
                LIBCXX_RUN_LONG_TESTS=False
            )

class BuildCompilerRtBaremetal(CrossCompileCMakeProject):
    repository = "https://github.com/llvm-mirror/compiler-rt.git"
    projectName = "compiler-rt-baremetal"
    crossInstallDir = CrossInstallDir.SDK
    baremetal = True

    def __init__(self, config: CheriConfig):
        if self.crossCompileTarget == CrossCompileTarget.CHERI:
            statusUpdate("Cannot compile newlib in purecap mode, building mips instead")
            self.crossCompileTarget = CrossCompileTarget.MIPS  # won't compile as a CHERI binary!
        super().__init__(config)

        self.COMMON_FLAGS.append("-v")
        self.COMMON_FLAGS.append("-ffreestanding")
        self.add_cmake_options(
            # LLVM_CONFIG_PATH=BuildLLVM.buildDir / "bin/llvm-config",
            LLVM_CONFIG_PATH=self.config.sdkBinDir / "llvm-config",
            LLVM_EXTERNAL_LIT=BuildLLVM.buildDir / "bin/llvm-lit",
            COMPILER_RT_BUILD_BUILTINS=True,
            COMPILER_RT_BUILD_SANITIZERS=False,
            COMPILER_RT_BUILD_XRAY=False,
            COMPILER_RT_BUILD_LIBFUZZER=False,
            COMPILER_RT_BUILD_PROFILE=False,
            COMPILER_RT_BAREMETAL_BUILD=self.baremetal,
            # COMPILER_RT_DEFAULT_TARGET_TRIPLE=self.targetTriple,
            COMPILER_RT_DEFAULT_TARGET_ONLY=True,
            # BUILTIN_SUPPORTED_ARCH="mips64",
            TARGET_TRIPLE=self.targetTriple,
        )

    def configure(self, **kwargs):
        self.configureArgs[0] = str(self.sourceDir / "lib/builtins")
        super().configure()

    def install(self, **kwargs):
        super().install(**kwargs)
        libname = "libclang_rt.builtins-mips64.a"
        self.moveFile(self.installDir / "lib/generic" / libname, self.installDir / "lib" / libname)


class BuildLibCXXBaremetal(BuildLibCXX):
    repository = "https://github.com/CTSRD-CHERI/libcxx.git"
    # dependencies = ["libcxxrt-baremetal"]
    projectName = "libcxx-baremetal"
    # target = "libcxx-baremetal"
    baremetal = True
    crossInstallDir = CrossInstallDir.SDK
    use_libcxxrt = False  # TODO: for now no runtime library

    def __init__(self, config: CheriConfig):
        if self.crossCompileTarget == CrossCompileTarget.CHERI:
            statusUpdate("Cannot compile newlib in purecap mode, building mips instead")
            self.crossCompileTarget = CrossCompileTarget.MIPS  # won't compile as a CHERI binary!
        super().__init__(config)

        # self.COMMON_FLAGS.append("-v")
        # Seems to be necessary :(
        self.COMMON_FLAGS.extend(["-mxgot", "-mllvm", "-mxmxgot"])
        self.add_cmake_options(CMAKE_EXE_LINKER_FLAGS="-Wl,-T,qemu-malta.ld")


class BuildLibCXXRTBaremetal(BuildLibCXXRT):
    repository = "https://github.com/CTSRD-CHERI/libcxxrt.git"
    projectName = "libcxxrt-baremetal"
    crossInstallDir = CrossInstallDir.SDK
    baremetal = True

    def __init__(self, config: CheriConfig):
        if self.crossCompileTarget == CrossCompileTarget.CHERI:
            statusUpdate("Cannot compile newlib in purecap mode, building mips instead")
            self.crossCompileTarget = CrossCompileTarget.MIPS  # won't compile as a CHERI binary!
        super().__init__(config)
        # self.COMMON_FLAGS.append("-v")
        self.COMMON_FLAGS.append("-Dsched_yield=abort")  # UNIPROCESSOR, should never happen
