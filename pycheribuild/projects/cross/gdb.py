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
from ...utils import runCmd, statusUpdate, IS_MAC

import os
import shutil


class TemporarilyRemoveProgramsFromSdk(object):
    def __init__(self, programs: "typing.List[str]", config: CheriConfig):
        self.programs = programs
        self.config = config

    def __enter__(self):
        statusUpdate('Temporarily moving', self.programs, "from", self.config.sdkBinDir)
        for l in self.programs:
            if (self.config.sdkBinDir / l).exists():
                runCmd("mv", "-f", l, l + ".backup", cwd=self.config.sdkBinDir, printVerboseOnly=True)
        return self

    def __exit__(self, *exc):
        statusUpdate('Restoring', self.programs, "in", self.config.sdkBinDir)
        for l in self.programs:
            if (self.config.sdkBinDir / (l + ".backup")).exists() or self.config.pretend:
                runCmd("mv", "-f", l + ".backup", l, cwd=self.config.sdkBinDir, printVerboseOnly=True)
        return False

class BuildGDB(CrossCompileAutotoolsProject):
    defaultInstallDir = lambda config, cls: BuildCHERIBSD.rootfsDir(config) / "usr/local"
    repository = "https://github.com/bsdjhb/gdb.git"
    gitBranch = "mips_cheri"
    make_kind = MakeCommandKind.GnuMake
    defaultLinker = "lld"
    defaultOptimizationLevel = ["-O2"]
    supported_architectures = [CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]

    def __init__(self, config: CheriConfig, target_arch: CrossCompileTarget):
        assert target_arch != CrossCompileTarget.CHERI
        if self.crossCompileTarget == CrossCompileTarget.CHERI:
            statusUpdate("Cannot compile GDB as a CHERIABI binary building as MIPS instead (it can still be used to "
                         "debug CHERIABI processes)")
            target_arch = CrossCompileTarget.MIPS  # won't compile as a CHERI binary!
        if self.compiling_for_host():
            self.crossInstallDir = CrossInstallDir.SDK
        # See https://github.com/bsdjhb/kdbg/blob/master/gdb/build
        super().__init__(config, target_arch)
        installRoot = self.installDir if self.compiling_for_host() else self.installPrefix
        # ./configure flags
        self.configureArgs.extend([
            "--disable-nls",
            "--enable-tui",
            "--disable-ld",
            "--enable-64-bit-bfd",
            "--without-gnu-as",
            "--with-separate-debug-dir=/usr/lib/debug",
            "--mandir=" + str(installRoot / "man"),
            "--infodir=" + str(installRoot / "info"),
            # "--disable-sim",
            "--disable-werror",
            "MAKEINFO=/bin/false",
            "--with-gdb-datadir=" + str(installRoot / "share/gdb"),
            "--disable-libstdcxx",
            # TODO:
            "--enable-build-with-cxx",
        ])

        # BUILD the gui:
        if False and self.compiling_for_host():
            self.configureArgs.append("--enable-gdbtk")
            # if IS_MAC:
                # self.configureArgs.append("--with-tcl=/usr/local/opt/tcl-tk/lib")
                # self.configureEnvironment["PKG_CONFIG_PATH"] = "/usr/local/opt/tcl-tk/lib/pkgconfig:/usr/local/lib/pkgconfig"

        # extra ./configure environment variables:
        # compile flags
        self.warningFlags.extend(["-Wno-absolute-value", "-Wno-parentheses-equality", "-Wno-unknown-warning-option",
                                  "-Wno-unused-function", "-Wno-unused-variable"])
        # TODO: we should fix this:
        self.warningFlags.append("-Wno-error=implicit-function-declaration")
        self.warningFlags.append("-Wno-error=format")
        self.warningFlags.append("-Wno-error=incompatible-pointer-types")
        self.warningFlags.append("-Wno-mismatched-tags")  # really noisy
        if self.compiling_for_host():
            self.LDFLAGS.append("-L/usr/local/lib")
            self.configureArgs.append("--enable-targets=all")
            self.configureArgs.append("--with-expat")
        else:
            self.configureArgs.extend(["--without-python", "--enable-targets=mips64-unknown-freebsd",

                                       "--without-expat", "--without-libunwind-ia64",])
            self.configureEnvironment.update(gl_cv_func_gettimeofday_clobber="no",
                                             lt_cv_sys_max_cmd_len="262144",
                                             # The build system run CC without any flags to detect dependency style...
                                             # (ZW_PROG_COMPILER_DEPENDENCIES([CC])) -> for gcc3 mode which seems correct
                                             am_cv_CC_dependencies_compiler_type="gcc3",
                                             MAKEINFO="/bin/false"
                                             )
            self.LDFLAGS.append("-static")
            self.COMMON_FLAGS.append("-static")  # seems like LDFLAGS is not enough
            self.COMMON_FLAGS.extend(["-DRL_NO_COMPAT", "-DLIBICONV_PLUG", "-fno-strict-aliasing"])
            # Currently there are a lot of `undefined symbol 'elf_version'`, etc errors
            # Add -lelf to the linker command line until the source is fixed
            self.LDFLAGS.append("-lelf")
            self.CFLAGS.append("-std=gnu89")
            self.configureEnvironment.update(CONFIGURED_M4="m4", CONFIGURED_BISON="byacc", TMPDIR="/tmp", LIBS="")
        if self.makeCommand == "gmake":
            self.configureEnvironment["MAKE"] = "gmake"

        self.hostCC = os.getenv("HOST_CC", str(config.clangPath))
        self.hostCXX = os.getenv("HOST_CXX", str(config.clangPlusPlusPath))
        self.configureEnvironment["CC_FOR_BUILD"] = self.hostCC
        self.configureEnvironment["CXX_FOR_BUILD"] = self.hostCXX
        self.configureEnvironment["CFLAGS_FOR_BUILD"] = "-g"
        self.configureEnvironment["CXXFLAGS_FOR_BUILD"] = "-g"
        # TODO: do I need these:
        """(cd $obj; env INSTALL="/usr/bin/install -c "  INSTALL_DATA="install   -m 0644"  INSTALL_LIB="install    -m 444"  INSTALL_PROGRAM="install    -m 555"  INSTALL_SCRIPT="install   -m 555"   PYTHON="${PYTHON}" SHELL=/bin/sh CONFIG_SHELL=/bin/sh CONFIG_SITE=/usr/ports/Templates/config.site ../configure ${CONFIGURE_ARGS} )"""

    @property
    def CC(self):
        if IS_MAC and self.compiling_for_host():
            return shutil.which("gcc")  # For some reason it fails when using /usr/bin/cc
        return super().CC

    @property
    def CXX(self):
        if IS_MAC and self.compiling_for_host():
            return shutil.which("g++")  # For some reason it fails when using /usr/bin/c++
        return super().CXX

    def configure(self, **kwargs):
        if IS_MAC:
            self.configureEnvironment.clear()
            print(self.configureArgs)
            # self.configureArgs.clear()
            self.baremetal = True  # HACK to avoid adding any flags to configure (seems to be needed)
        super().configure()

    def compile(self, **kwargs):
        with TemporarilyRemoveProgramsFromSdk(["as", "ld", "objcopy", "objdump"], self.config):
            self.runMake(makeTarget="all-gdb", cwd=self.buildDir)

    def install(self, **kwargs):
        self.runMakeInstall(target="install-gdb")
