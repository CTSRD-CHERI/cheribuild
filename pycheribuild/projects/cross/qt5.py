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

# This class is used to build qtbase and all of qt5
class BuildQtWithConfigureScript(CrossCompileProject):
    doNotAddToTargets = True
    # requiresGNUMake = True
    defaultOptimizationLevel = ["-O2"]
    add_host_target_build_config_options = False

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configureCommand = self.sourceDir / "configure"
        self.linkDynamic = True  # Build system adds -static automatically

    def configure(self, **kwargs):
        if not self.needsConfigure() and not self.config.forceConfigure:
            return
        if self.crossCompileTarget != CrossCompileTarget.NATIVE:
            # make sure we use libc++ (only happens with mips64-unknown-freebsd10 and greater)
            compiler_flags = self.COMMON_FLAGS + ["-target", self.targetTriple + "12"]
            linker_flags = self.default_ldflags + ["-target", self.targetTriple + "12", "-v"]

            if self.crossCompileTarget == CrossCompileTarget.CHERI:
                linker_flags += ["-static"]  # dynamically linked C++ doesn't work yet
                self.configureArgs.append("QMAKE_LIBDIR=" + str(self.config.sdkSysrootDir / "usr/libcheri"))
            # self.configureArgs.append("QMAKE_CXXFLAGS+=-stdlib=libc++")

            # The build system already passes these:
            linker_flags = filter(lambda s: not s.startswith("--sysroot"), linker_flags)
            compiler_flags = filter(lambda s: not s.startswith("--sysroot"), compiler_flags)

            self.configureArgs.extend([
                "-device", "freebsd-generic-clang",
                "-device-option", "CROSS_COMPILE={}/{}-".format(self.config.sdkBinDir, self.targetTriple),
                "-device-option", "COMPILER_FLAGS=" + commandline_to_str(compiler_flags),
                "-device-option", "LINKER_FLAGS=" + commandline_to_str(linker_flags),
                "-sysroot", self.config.sdkSysrootDir,
                "-static"
            ])

        self.configureArgs.extend([
            "-nomake", "examples",
            # TODO: build the tests and run them them
            "-nomake", "tests",  # "-developer-build",
            # To ensure the host and cross-compiled version is the same also disable opengl and dbus there
            "-no-opengl", "-no-dbus",
            # Missing configure check for evdev means it will fail to compile for CHERI
            "-no-evdev"
        ])
        # currently causes build failures:
        # Seems like I need to define PNG_READ_GAMMA_SUPPORTED
        self.configureArgs.append("-qt-libpng")

        if self.debugInfo:
            self.configureArgs.append("-debug")
        else:
            self.configureArgs.append("-optimize-size")

        self.configureArgs.extend(["-opensource", "-confirm-license"])

        self.deleteFile(self.buildDir / "config.cache")
        self.deleteFile(self.buildDir / "config.opt")
        self.deleteFile(self.buildDir / "config.status")
        super().configure()

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()


class BuildQt5(BuildQtWithConfigureScript):
    repository = "https://github.com/RichardsonAlex/qt5"
    gitBranch = "5.9"
    skipGitSubmodules = True  # init-repository does it for us

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.allModules = cls.addBoolOption("all-modules", showHelp=True,
                                          help="Build all modules (even those that don't make sense for CHERI)")

    def configure(self, **kwargs):
        if not self.allModules:
            modules_to_skip = "qtgamepad qtlocation".split()
            # TODO: skip modules that just increase compile time
        super().configure(**kwargs)

    def update(self):
        super().update()
        # qtlocation breaks for some reason if qt5 is forked on github
        runCmd("perl", "init-repository", "--module-subset=default,-qtlocation", "-f", "--branch", cwd=self.sourceDir)


class BuildQtBase(BuildQtWithConfigureScript):
    repository = "https://github.com/RichardsonAlex/qtbase"
    gitBranch = "5.9"

    def __init__(self, config: CheriConfig):
        self.sourceDir = config.sourceRoot / "qt5/qtbase"
        super().__init__(config)
