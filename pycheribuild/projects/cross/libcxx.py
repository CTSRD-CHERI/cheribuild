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
from ..run_qemu import LaunchQEMU
from ...config.loader import ComputedDefaultValue
from ...utils import IS_LINUX, parseOSRelease


installToCXXDir = ComputedDefaultValue(
    function=lambda config, project: BuildCHERIBSD.rootfsDir(config) / "extra/c++",
    asString="$CHERIBSD_ROOTFS/extra/c++")


class BuildLibunwind(CrossCompileCMakeProject):
    repository = "https://github.com/CTSRD-CHERI/libunwind.git"
    defaultInstallDir = installToCXXDir

    def __init__(self, config: CheriConfig):
        self.linkDynamic = True   # Hack: we always want to use the dynamic toolchain file, cmake builds both static and dynamic
        super().__init__(config)
        # Adding -ldl won't work: no libdl in /usr/libcheri
        self.add_cmake_options(LIBUNWIND_HAS_DL_LIB=False)
        # TODO: this breaks the build: LLVM_LIBDIR_SUFFIX="cheri"
        self.COMMON_FLAGS.append("-isystem")
        self.COMMON_FLAGS.append(str(BuildLibCXX.sourceDir / "include"))
        self._forceLibCXX = False


class BuildLibCXXRT(CrossCompileCMakeProject):
    repository = "https://github.com/CTSRD-CHERI/libcxxrt.git"
    defaultInstallDir = installToCXXDir

    def __init__(self, config: CheriConfig):
        self.linkDynamic = True  # Hack: we always want to use the dynamic toolchain file, cmake builds both static and dynamic
        super().__init__(config)
        self.add_cmake_options(LIBUNWIND_PATH=BuildLibunwind.buildDir / "lib")
        if self.crossCompileTarget == CrossCompileTarget.CHERI:
            # TODO: __sync_fetch_and_add in exceptions code
            self.add_cmake_options(NO_SHARED=True, DISABLE_EXCEPTIONS_RTTI=True, NO_UNWIND_LIBRARY=True)
        else:
            self.add_cmake_options(BUILD_TESTS=True)
            if IS_LINUX and "ubuntu" in parseOSRelease()["ID_LIKE"]:
                # Ubuntu packagers think that static linking should not be possible....
                self.add_cmake_options(HAVE_STATIC_GCC_S=False, COMPARE_TEST_OUTPUT_TO_SYSTEM_OUTPUT=False)
            self.add_cmake_options(NO_UNWIND_LIBRARY=False, TEST_LIBUNWIND=True)

    def install(self, **kwargs):
        self.installFile(self.buildDir / "lib/libcxxrt.a", self.installDir / "libcheri/libcxxrt.a", force=True)
        # self.installFile(self.buildDir / "lib/libcxxrt.so", self.installDir / "usr/libcheri/libcxxrt.so", force=True)


class BuildLibCXX(CrossCompileCMakeProject):
    repository = "https://github.com/CTSRD-CHERI/libcxx.git"
    defaultInstallDir = installToCXXDir
    dependencies = ["libcxxrt"]

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
                                            kind=str, default=lambda c, p: LaunchQEMU.sshForwardingPort)
        cls.qemu_user = cls.addConfigOption("shh-user", default="root", help="The CheriBSD used for running tests")

    def __init__(self, config: CheriConfig):
        self.linkDynamic = True  # Hack: we always want to use the dynamic toolchain file, cmake builds both static and dynamic
        super().__init__(config)
        self.COMMON_FLAGS.append("-D__LP64__=1")  # HACK to get it to compile
        if self.crossCompileTarget == CrossCompileTarget.NATIVE:
            self.add_cmake_options(LIBCXX_ENABLE_SHARED=True, LIBCXX_ENABLE_STATIC_ABI_LIBRARY=False)
        else:
            self.addCrossFlags()
        # add the common test options
        self.add_cmake_options(
            LIBCXX_INCLUDE_TESTS=True,
            # LLVM_CONFIG_PATH=BuildLLVM.buildDir / "bin/llvm-config",
            LLVM_CONFIG_PATH=self.config.sdkBinDir / "llvm-config",
            LIBCXXABI_USE_LLVM_UNWINDER=False,  # we have a fake libunwind in libcxxrt
        )
        # select libcxxrt as the runtime library
        self.add_cmake_options(
            LIBCXX_CXX_ABI="libcxxrt",
            LIBCXX_CXX_ABI_LIBNAME="libcxxrt",
            LIBCXX_CXX_ABI_INCLUDE_PATHS=BuildLibCXXRT.sourceDir / "src",
            LIBCXX_CXX_ABI_LIBRARY_PATH=BuildLibCXXRT.buildDir / "lib",
        )

    def addCrossFlags(self):
        # TODO: do I even need the toolchain file to cross compile?
        self.add_cmake_options(
            LIBCXX_ENABLE_SHARED=False,  # not yet
            LIBCXX_ENABLE_STATIC=True,
            LIBCXX_ENABLE_EXPERIMENTAL_LIBRARY=False,  # not yet
            LIBCXX_INCLUDE_BENCHMARKS=False,
            LIBCXX_INCLUDE_DOCS=False,
            # exceptions and rtti still missing:
            LIBCXX_ENABLE_EXCEPTIONS=False,
            LIBCXX_ENABLE_RTTI=False,
            # When cross compiling we link the ABI library statically
            LIBCXX_ENABLE_STATIC_ABI_LIBRARY=True,
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
        self.add_cmake_options(
            LIBCXX_SYSROOT=self.config.sdkDir / "sysroot",
            LIBCXX_TARGET_TRIPLE=self.targetTriple,
            LIBCXX_EXECUTOR=executor,
            LIBCXX_TARGET_INFO="libcxx.test.target_info.CheriBSDRemoteTI",
            LIBCXX_RUN_LONG_TESTS=False
        )
