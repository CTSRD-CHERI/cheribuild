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
from ...utils import statusUpdate
import tempfile
import shutil


class BuildNewlibBaremetal(CrossCompileAutotoolsProject):
    repository = "git://sourceware.org/git/newlib-cygwin.git"
    projectName = "newlib-baremetal"
    # we have to build in the source directory, out-of-source is broken
    # defaultBuildDir = CrossCompileAutotoolsProject.defaultSourceDir
    requiresGNUMake = True
    add_host_target_build_config_options = False
    defaultOptimizationLevel = ["-O2"]
    _configure_supports_libdir = False
    _configure_supports_variables_on_cmdline = True

    def __init__(self, config: CheriConfig):
        if self.crossCompileTarget == CrossCompileTarget.CHERI:
            statusUpdate("Cannot compile newlib in purecap mode, building mips instead")
            self.crossCompileTarget = CrossCompileTarget.MIPS  # won't compile as a CHERI binary!
        super().__init__(config)
        self.configureCommand = self.sourceDir / "newlib/configure"
        statusUpdate("COMMON FLAGS were", self.COMMON_FLAGS)
        self.COMMON_FLAGS = ['-integrated-as', '-G0', '-mabi=n64', '-mcpu=mips4']
        self.triple = "mips64-qemu-elf"

    def install(self, **kwargs):
        # self.runMakeInstall()
        pass

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()

    @property
    def targetTripleWithVersion(self):
        return "mips64-unknown-elf"

    def configure(self):
        self.configureArgs.extend([
            "--enable-malloc-debugging",
            "--enable-newlib-long-time_t",
            "--enable-newlib-io-c99-formats",
            "--enable-newlib-io-long-long",
            "--disable-newlib-io-long-double"
            "--disable-newlib-supplied-syscalls"
            "--disable-newlib-mb"
        ])
        self.configureEnvironment["CC"] = self.sdkBinDir / "clang"
        self.configureEnvironment["CXX"] = self.sdkBinDir / "clang++"
        self.configureEnvironment["AR"] = self.sdkBinDir / "ar"
        self.configureEnvironment["STRIP"] = self.sdkBinDir / "strip"
        self.configureEnvironment["OBJCOPY"] = self.sdkBinDir / "objcopy"
        self.configureEnvironment["RANLIB"] = self.sdkBinDir / "ranlib"
        self.configureEnvironment["READELF"] = self.sdkBinDir / "readelf"
        self.configureEnvironment["AS"] = str(self.sdkBinDir / "clang") + " -integrated-as -target " + self.targetTripleWithVersion
        self.configureEnvironment["NM"] = self.sdkBinDir / "nm"
        self.configureArgs.append("--build=" + self.triple)
        super().configure()

    # def compile(self, **kwargs):
        # super().compile(cwd=self.sourceDir)