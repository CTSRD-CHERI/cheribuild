#
# Copyright (c) 2019 Hesham Almatary
# Copyright (c) 2017 Alex Richardson
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
from ...utils import statusUpdate, IS_MAC, runCmd
from ...config.loader import ComputedDefaultValue
from pathlib import Path
import tempfile


class BuildNewlibRtemsRiscv(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/newlib")
    projectName = "newlib-rtems-riscv"
    make_kind = MakeCommandKind.GnuMake
    rtems = True
    add_host_target_build_config_options = False
    # TODO Make this -O2 once a bug with LLVM/Clang relaxation is fixed
    defaultOptimizationLevel = ["-O0"]
    _configure_supports_libdir = False
    _configure_supports_variables_on_cmdline = True
    crossInstallDir = CrossInstallDir.SDK
    supported_architectures = CrossCompileAutotoolsProject.CAN_TARGET_ALL_BAREMETAL_TARGETS
    default_architecture = CrossCompileTarget.RISCV
    # build_in_source_dir = True  # we have to build in the source directory

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.locale_support = cls.addBoolOption("locale-support", showHelp=False, help="Build with locale support")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._installPrefix = self._installPrefix.parent  # newlib install already appends the triple
        self._installDir = self._installDir.parent  # newlib install already appends the triple
        print("after:", self.installDir, "_=", self._installDir, "dest=", self.destdir, "real=", self.real_install_root_dir)
        self.configureCommand = self.sourceDir / "configure"
        # FIXME: how can I force it to run a full configure step (this is needed because it runs the newlib configure
        # step during make all rather than during ./configure
        self.make_args.env_vars["newlib_cv_ldbl_eq_dbl"] = "yes"
        # ensure that we don't fall back to system headers (but do use stddef.h from clang...)
        self.COMMON_FLAGS.extend(["--sysroot", "/this/path/does/not/exist"])
        if IS_MAC:
            self.add_configure_vars(LDFLAGS="-fuse-ld=/usr/bin/ld")

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()

    def add_configure_vars(self, **kwargs):
        # newlib is annoying, we need to pass all these arguments to make as well because it won't run all
        # the configure steps...
        for k, v in kwargs.items():
            self.add_configure_env_arg(k, v)
            # self.make_args.env_vars[k] = str(v)
            if k.endswith("_FOR_BUILD"):
                k2 = k[0:-len("_FOR_BUILD")]
                self.add_configure_env_arg(k2, v)

    def configure(self):
        target_cflags = commandline_to_str(self._essential_compiler_and_linker_flags + self.COMMON_FLAGS)
        bindir = self.config.sdkBinDir

        self.add_configure_vars(
            AS_FOR_TARGET=str(bindir / "clang"),  # + target_cflags,
            CC_FOR_TARGET=str(bindir / "clang"),  # + target_cflags,
            CXX_FOR_TARGET=str(bindir / "clang++"),  # + target_cflags,
            AR_FOR_TARGET=bindir / "ar", STRIP_FOR_TARGET=bindir / "strip",
            OBJCOPY_FOR_TARGET=bindir / "objcopy", RANLIB_FOR_TARGET=bindir / "ranlib",
            OBJDUMP_FOR_TARGET=bindir / "llvm-objdump",
            READELF_FOR_TARGET=bindir / "readelf", NM_FOR_TARGET=bindir / "nm",
            # Set all the flags:
            CFLAGS_FOR_TARGET=target_cflags + "-march=rv64imafdc -mabi=lp64d",
            CCASFLAGS_FOR_TARGET=target_cflags,
            FLAGS_FOR_TARGET=target_cflags,
            # Some build tools are needed:
            CC_FOR_BUILD=self.config.clangPath,
            CXX_FOR_BUILD=self.config.clangPlusPlusPath,
            # long double is the same as double
            newlib_cv_ldbl_eq_dbl="yes",
            LD_FOR_TARGET=str(bindir / "ld.lld"), LDFLAGS_FOR_TARGET="-fuse-ld=lld",
        )
        self.configureArgs.extend([
            "--enable-newlib-io-c99-formats",
            "--disable-libstdcxx"  # not sure if this is needed
        ])

        self.configureArgs.append("--target=" + self.targetTriple)
        self.configureArgs.append("--with-newlib")

        super().configure()

    def install(self, **kwargs):
        super().install(**kwargs)
