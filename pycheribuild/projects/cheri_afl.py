#
# Copyright (c) 2019 Alex Richardson
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
from .cross.cheribsd import BuildCHERIBSD
from .project import DefaultInstallDir, GitRepository, MakeCommandKind, Project
from ..config.compilation_targets import CompilationTargets


class BuildAflCheriNinja(Project):
    default_directory_basename = "AFL-CHERI"
    target = "afl-cheri"
    repository = GitRepository("https://github.com/CTSRD-CHERI/AFL-CHERI")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    make_kind = MakeCommandKind.GnuMake
    build_in_source_dir = True

    def configure(self, **kwargs):
        pass

    def compile(self, **kwargs):
        #  $ export SDK_PATH=/path/to/cheri/sdk
        self.make_args.env_vars["SDK_PATH"] = self.config.cheri_sdk_dir
        self.make_args.env_vars["XCC"] = self.config.cheri_sdk_bindir / "clang"
        self.make_args.env_vars["LLVM_CONFIG"] = self.config.cheri_sdk_bindir / "llvm-config"
        cheribsd_mips_hybrid = BuildCHERIBSD.get_instance(self, cross_target=CompilationTargets.CHERIBSD_MIPS_HYBRID)
        cheri_mips_sysroot = cheribsd_mips_hybrid.target_info.sysroot_dir
        base_xcflags = "-target mips64-unknown-freebsd13 -mcpu=beri -integrated-as -msoft-float --sysroot=" + str(
            cheri_mips_sysroot)
        base_flags = self.make_args.copy()
        base_flags.env_vars["XCFLAGS"] = base_xcflags + " -mabi=n64"
        #  $  XCC=${SDK_PATH}/bin/clang XCFLAGS='-cheri-linker -target mips64-unknown-freebsd -mcpu=mips3
        #  -integrated-as -msoft-float' gmake
        self.run_make(options=base_flags, cwd=self.source_dir)
        #  $  XCC=${SDK_PATH}/bin/clang XCFLAGS='-cheri-linker -target mips64-unknown-freebsd -mcpu=mips3
        #  -integrated-as -msoft-float' gmake
        llvm_mode_flags = self.make_args.copy()
        llvm_mode_flags.env_vars["XCFLAGS"] = base_xcflags + " -mabi=purecap"
        self.run_make(options=llvm_mode_flags, cwd=self.source_dir / "llvm_mode")

    def install(self, **kwargs):
        self.make_args.set(DESTDIR=self.config.cheri_sdk_dir / "afl")
        self.run_make("install", options=self.make_args)
        self.install_file(self.build_dir / "afl-fuzz",
                          BuildCHERIBSD.get_rootfs_dir(self,
                                                       cross_target=CompilationTargets.CHERIBSD_MIPS_HYBRID) /
                          "usr/local/bin/afl-fuzz")
        self.install_file(self.build_dir / "afl-fuzz",
                          BuildCHERIBSD.get_rootfs_dir(self,
                                                       cross_target=CompilationTargets.CHERIBSD_MIPS_NO_CHERI) /
                          "usr/local/bin/afl-fuzz")

    def run_tests(self):
        # sysctl machdep.log_cheri_exceptions=0
        pass
