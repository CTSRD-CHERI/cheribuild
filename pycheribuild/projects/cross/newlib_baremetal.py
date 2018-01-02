#
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
from ...utils import statusUpdate, IS_MAC
from ...config.loader import ComputedDefaultValue
from pathlib import Path


class BuildNewlibBaremetal(CrossCompileAutotoolsProject):
    repository = "https://github.com/CTSRD-CHERI/newlib"
    projectName = "newlib-baremetal"
    requiresGNUMake = True
    baremetal = True
    add_host_target_build_config_options = False
    defaultOptimizationLevel = ["-O2"]
    _configure_supports_libdir = False
    _configure_supports_variables_on_cmdline = True
    crossInstallDir = CrossInstallDir.SDK
    # defaultBuildDir = CrossCompileAutotoolsProject.defaultSourceDir  # we have to build in the source directory

    def __init__(self, config: CheriConfig, target_arch: CrossCompileTarget):
        if self.crossCompileTarget == CrossCompileTarget.CHERI:
            statusUpdate("Cannot compile newlib in purecap mode, building mips instead")
            self.crossCompileTarget = CrossCompileTarget.MIPS  # won't compile as a CHERI binary!
        super().__init__(config, target_arch)
        self.installDir = self.installDir.parent  # newlib install already appends the triple
        #self.configureCommand = Path("/this/path/does/not/exist")
        self.configureCommand = self.sourceDir / "configure"
        # self.COMMON_FLAGS = ['-integrated-as', '-G0', '-mabi=n64', '-mcpu=mips4']
        # self.COMMON_FLAGS = ['-integrated-as', '-mabi=n64', '-mcpu=mips4']
        # print(self.COMMON_FLAGS)
        # FIXME: how can I force it to run a full configure step (this is needed because it runs the newlib configure
        # step during make all rather than during ./configure
        # ensure that we don't fall back to system headers (but do use stddef.h from clang...)
        self.COMMON_FLAGS.extend(["--sysroot", "/this/path/does/not/exist"])
        self.target_cflags = "-target " + self.targetTripleWithVersion + " ".join(self.COMMON_FLAGS)
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
            CFLAGS_FOR_TARGET=self.target_cflags,
            CCASFLAGS_FOR_TARGET=self.target_cflags + " -mabicalls",
            FLAGS_FOR_TARGET=self.target_cflags,
            # Some build tools are needed:
            CC_FOR_BUILD=self.config.clangPath,
            CXX_FOR_BUILD=self.config.clangPlusPlusPath,
            # long double is the same as double
            newlib_cv_ldbl_eq_dbl="yes",
            LD_FOR_TARGET=str(bindir / "ld.lld"), LDFLAGS_FOR_TARGET="-fuse-ld=lld",
        )
        self.make_args.env_vars["newlib_cv_ldbl_eq_dbl"] = "yes"
        if IS_MAC:
            self.add_configure_vars(LDFLAGS="-fuse-ld=/usr/bin/ld")

    # def install(self, **kwargs):
    #     # self.runMakeInstall(cwd=self.buildDir / "newlib")
    #     self.runMakeInstall(cwd=self.buildDir / "libgloss")

    # def compile(self, **kwargs):
    #     # super().compile(cwd=self.buildDir / "newlib")
    #     self.make_args.env_vars["MULTILIB"] = self.target_cflags + " -mabicalls"
    #     super().compile(cwd=self.buildDir / "libgloss")

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()

    @property
    def targetTripleWithVersion(self):
        return "mips64-unknown-elf"

    def add_configure_vars(self, **kwargs):
        # newlib is annoying, we need to pass all these arguments to make as well because it won't run all
        # the configure steps...
        for k, v in kwargs.items():
            self.add_configure_env_arg(k, v)
            # self.make_args.env_vars[k] = str(v)
            if k.endswith("_FOR_BUILD"):
                k2 = k[0:-len("_FOR_BUILD")]
                self.add_configure_env_arg(k2, v)
                # self.make_args.env_vars[k2] = str(v)

    def configure(self):
        self.configureArgs.extend([
            "--enable-malloc-debugging",
            "--enable-newlib-long-time_t",  # we want time_t to be long and not int!
            "--enable-newlib-io-c99-formats",
            "--enable-newlib-io-long-long",
            # --enable-newlib-io-pos-args (probably not needed)
            "--disable-newlib-io-long-double",  # we don't need this, MIPS long double == double
            "--enable-newlib-io-float",
            # "--disable-newlib-supplied-syscalls"
            "--enable-newlib-mb",  # needed for locale support

            "--disable-libstdcxx",  # not sure if this is needed

            # we don't have any multithreading support on baremetal
            "--disable-newlib-multithread",

            "--enable-newlib-global-atexit",  # TODO: is this needed?
            # --enable-newlib-nano-malloc (should we do this?)
            "--disable-multilib",

            # TODO: smaller lib? "--enable-target-optspace"

            # FIXME: these don't seem to work
            "--enable-serial-build-configure",
            "--enable-serial-target-configure",
            "--enable-serial-host-configure",
        ])

        # self.configureArgs.append("--host=" + self.targetTriple)
        self.configureArgs.append("--target=" + self.targetTriple)
        self.configureArgs.append("--disable-multilib")
        self.configureArgs.append("--with-newlib")
        super().configure()

