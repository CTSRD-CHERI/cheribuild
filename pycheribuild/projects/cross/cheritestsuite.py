#
# Copyright (c) 2025 Paul Metzger
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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

from .crosscompileproject import CrossCompileMakefileProject, DefaultInstallDir, GitRepository
from ..project import (
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind
)

from ...config.compilation_targets import CompilationTargets
from ...utils import classproperty

import shutil
import os

class BuildCheriTestSuite(CrossCompileMakefileProject):
    _always_add_suffixed_targets = True
    _needs_sysroot = True
    _supported_architectures = (CompilationTargets.LINUX_MORELLO_PURECAP,)
    dependencies = ("libxo", "morello-muslc", "morello-compiler-rt-builtins")
    make_kind = MakeCommandKind.BsdMake
    repository = GitRepository("git@github.com:CTSRD-CHERI/pffm2-cheritest-wip.git")
    target = "cheritestsuite"

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE
    
    def compile(self, **kwargs):
        # Search for the build directory of compiler-rt-builtins
        compiler_rt_builtins_build_dir = None
        for d in self.cached_full_dependencies():
            if d.name == "morello-compiler-rt-builtins-linux-morello-purecap":
                compiler_rt_builtins_build_dir = d.get_or_create_project(CompilationTargets.LINUX_MORELLO_PURECAP, 
                                                                         self.config, self).get_build_dir(self)
                break
        
        # Musl libc's alltypes.h doesn't have a header guard by design and redefinition
        # errors caused by this are false positives.
        self.cross_warning_flags.append('-Wno-error=-typedef-redefinition')

        # We are including cheriintrin.h for some of the definitions
        # that are on CheriBSD in other header files. However, cheric.h
        # and cheriintrin.h define cheri_is_subset() but a comment in
        # cheric.h indicates that the intrinsic is still unstable. Therefore,
        # we use the one in cheric.h and override the definition in
        # cheriintrin.h if necessary.
        self.cross_warning_flags.append('-Wno-error=-macro-redefined')

        # Musl libc's endian.h causes these warnings
        self.cross_warning_flags.append('-Wno-error=shift-op-parentheses')
        self.cross_warning_flags.append('-Wno-error=bitwise-op-parentheses')
        # Muls libc's ucontext.h causes this warning
        self.cross_warning_flags.append('-Wno-error=strict-prototypes')

        # Overwrite dependencies because libsys and libcompiler_rt don't exist in 
        # the CHERI/Morello Linux sysroot
        self.make_args.set(
            _DP_c="",
            _DP_sys="", 
            _DP_thr="c",
            _DP_pthread="c",
        )

        # We will put the binaries here
        self.destdir = self.destdir / "rootfs" / "root"
        self.makedirs(self.destdir)

        # Copy arm64-specific headers to the machine include directory because
        # we are building for Morello
        shutil.copytree(self.source_dir / "compat_headers" / "arm64", 
                    self.source_dir / "compat_headers" / "machine",
                    dirs_exist_ok=True)

        self.make_args.set_env(
            C_INCLUDE_PATH="$C_INCLUDE_PATH:" + str(self.source_dir / "compat_headers"),
            CFLAGS=" ".join(self.default_compiler_flags() + 
                            ["-v", "-rtlib=compiler-rt", "-resource-dir={}".format(compiler_rt_builtins_build_dir)]),
            CROSS_COMPILE="",
            # Put the binary into root's home directory
            DESTDIR=str(self.destdir),
            BINOWN=os.getuid(),
            BINGRP=os.getgid(),
            BINMODE=755,
            #LD_FLAGS="--unwindlib=none",
            LD_FATAL_WARNINGS="no",
            LOCAL_LIBRARIES="bsd",
            MACHINE_CPUARCH="aarch64c",
            MACHINE_ABI="purecap",
            MACHINE_ARCH="aarch64c",
            # This is not supported by Morello LLVM
            MK_CHERI_CODEPTR_RELOCS="no", # Would WITHOUT_CHERI_CODEPTR_RELOCS be better?
            MAKESYSPATH=str(self.source_dir / "mk"),
            MAKEOBJDIRPREFIX=str(self.source_dir),
        )
        
        self.run_make(cwd=self.source_dir / "cheribsdtest")

    def install(self, **kwargs):
        self.run_make_install(cwd=self.source_dir / "cheribsdtest")

    def process(self):
        self.check_required_system_tool("bmake", homebrew="bmake", cheribuild_target="bmake")
        super().process()