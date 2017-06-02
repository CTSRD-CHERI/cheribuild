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
from ...utils import commandline_to_str, runCmd

class BuildQt5(CrossCompileProject):
    repository = "https://github.com/qt/qt5"
    gitBranch = "5.9"
    skipGitSubmodules = True  # init-repository does it for us
    requiresGNUMake = True
    defaultOptimizationLevel = ["-O2"]
    add_host_target_build_config_options = False

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configureCommand = self.sourceDir / "configure"
        self.linkDynamic = True  # Build system adds -static automatically

    def update(self):
        super().update()
        runCmd("perl", "init-repository", "-f", cwd=self.sourceDir)

    def configure(self, **kwargs):
        if not self.needsConfigure() and not self.config.forceConfigure:
            return
        if self.crossCompileTarget != CrossCompileTarget.NATIVE:
            # make sure we use libc++ (only happens with mips64-unknown-freebsd10 and greater)
            compiler_flags = commandline_to_str(self.COMMON_FLAGS + ["-target", self.targetTriple + "12"])
            linker_flags = commandline_to_str(self.default_ldflags + ["-target", self.targetTriple + "12",
                                                                      # on cheri we need __atomic_store, etc.
                                                                      "-lcompiler_rt", "-v"])
            # self.configureArgs.append("QMAKE_CXXFLAGS+=-stdlib=libc++")
            self.configureArgs.extend([
                "-device", "freebsd-generic-clang",
                "-device-option", "CROSS_COMPILE={}/{}-".format(self.config.sdkBinDir, self.targetTriple),
                "-device-option", "COMPILER_FLAGS=" + compiler_flags,
                "-device-option", "LINKER_FLAGS=" + linker_flags,
                "-sysroot", self.config.sdkSysrootDir,
                "-static"
            ])
        if self.crossCompileTarget == CrossCompileTarget.CHERI:
            self.configureArgs.append("QMAKE_LIBDIR=" + str(self.config.sdkSysrootDir / "usr/libcheri"))

        if self.debugInfo:
            self.configureArgs.append("-debug")
        else:
            self.configureArgs.append("-optimize-size")

        self.configureArgs.extend([
            "-nomake", "examples",
            # To ensure the host and cross-compiled version is the same also disable opengl and dbus there
            "-no-opengl", "-no-dbus",
            # TODO: build the tests and run them them
            # "-developer-build"
            "-nomake", "tests",
            # QtGamepad assumes evdev is available on !WINDOWS !OSX
            "-skip", "qtgamepad",
        ])

        self.configureArgs.extend(["-opensource", "-confirm-license"])

        # currently causes build failures:
        # Seems like I need to define PNG_READ_GAMMA_SUPPORTED
        self.configureArgs.append("-qt-libpng")

        self.deleteFile(self.buildDir / "config.cache")
        self.deleteFile(self.buildDir / "config.opt")
        self.deleteFile(self.buildDir / "config.status")
        super().configure()

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()

