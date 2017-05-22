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

installToCXXDir = ComputedDefaultValue(
    function=lambda config, project: BuildCHERIBSD.rootfsDir(config) / "extra/c++",
    asString="$CHERIBSD_ROOTFS/extra/c++")


class BuildLibCXXRT(CrossCompileCMakeProject):
    repository = "https://github.com/CTSRD-CHERI/libcxxrt.git"
    defaultInstallDir = installToCXXDir

    def __init__(self, config: CheriConfig):
        self.linkDynamic = True  # Hack: we always want to use the dynamic toolchain file, build system adds -static
        super().__init__(config)
        self.add_cmake_options(CHERI_PURE=True)

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
        self.linkDynamic = True  # Hack: we always want to use the dynamic toolchain file, build system adds -static
        super().__init__(config)
        self.COMMON_FLAGS.append("-D__LP64__=1")  # HACK to get it to compile
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
        )
        # select libcxxrt as the runtime library
        self.add_cmake_options(
            LIBCXX_CXX_ABI="libcxxrt",
            LIBCXX_CXX_ABI_LIBNAME="libcxxrt",
            LIBCXX_CXX_ABI_INCLUDE_PATHS=BuildLibCXXRT.sourceDir / "src",
            LIBCXX_CXX_ABI_LIBRARY_PATH=BuildLibCXXRT.buildDir / "lib",
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
            LIBCXX_INCLUDE_TESTS=True,
            LIBCXX_SYSROOT=config.sdkDir / "sysroot",
            LIBCXX_TARGET_TRIPLE=self.targetTriple,
            # LLVM_CONFIG_PATH=BuildLLVM.buildDir / "bin/llvm-config",
            LLVM_CONFIG_PATH=self.config.sdkBinDir / "llvm-config",
            LIBCXXABI_USE_LLVM_UNWINDER=False,  # we have a fake libunwind in libcxxrt
            LIBCXX_EXECUTOR=executor,
            LIBCXX_TARGET_INFO="libcxx.test.target_info.CheriBSDRemoteTI",
            LIBCXX_RUN_LONG_TESTS=False
        )
