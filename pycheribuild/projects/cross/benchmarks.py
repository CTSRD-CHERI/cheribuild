#
# Copyright (c) 2018 Alex Richardson
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
from ...utils import setEnv, IS_FREEBSD
from pathlib import Path
import tempfile


class BuildMibench(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/mibench")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    projectName = "mibench"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True

    def compile(self, **kwargs):
        with setEnv(MIPS_SDK=self.config.sdkDir,
                    CHERI128_SDK=self.config.sdkDir,
                    CHERI256_SDK=self.config.sdkDir,
                    CHERI_SDK=self.config.sdkDir):
            # We can't fall back to /usr/bin/ar here since that breaks on MacOS
            self.make_args.set(AR=str(self.config.sdkBinDir / "ar") + " rc")
            self.make_args.set(AR2=str(self.config.sdkBinDir / "ranlib"))
            self.make_args.set(ADDITIONAL_CFLAGS=" ".join(self.default_compiler_flags))
            if self.compiling_for_host():
                self.make_args.set(VERSION="x86")
            if self.compiling_for_mips():
                self.make_args.set(VERSION="mips", MIPS_SYSROOT=self.config.get_sysroot_path(CrossCompileTarget.MIPS))
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.make_args.set(VERSION="cheri128", CHERI128_SYSROOT=self.config.cheriSysrootDir)
                else:
                    assert self.config.cheriBits == 256
                    self.make_args.set(VERSION="cheri256", CHERI256_SYSROOT=self.config.cheriSysrootDir)
            self.runMake("bundle_dump")

    def install(self, **kwargs):
        pass  # skip install for now...


class BuildOlden(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/olden")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    projectName = "olden"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True

    def compile(self, **kwargs):
        with setEnv(MIPS_SDK=self.config.sdkDir,
                    CHERI128_SDK=self.config.sdkDir,
                    CHERI256_SDK=self.config.sdkDir,
                    CHERI_SDK=self.config.sdkDir):
            self.make_args.set(SYSROOT_DIRNAME=self.crossSysrootPath.name)
            self.make_args.add_flags("-f", "Makefile.jenkins")
            self.make_args.set(ADDITIONAL_CFLAGS=" ".join(self.default_compiler_flags))
            if self.compiling_for_host():
                self.runMake("x86")
            if self.compiling_for_mips():
                self.runMake("mips")
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.runMake("cheriabi128")
                else:
                    assert self.config.cheriBits == 256
                    self.runMake("cheriabi256")

    def install(self, **kwargs):
        pass  # skip install for now...
