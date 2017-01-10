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
import os
import sys
from pathlib import Path

from ..project import Project
from ..utils import *


def defaultKernelConfig(config: CheriConfig):
    if config.cheriBits == 128:
        # make sure we use a kernel with 128 bit CPU features selected
        return "CHERI128_MALTA64"
    return "CHERI_MALTA64"


class BuildCHERIBSD(Project):
    dependencies = ["llvm"]
    repository = "https://github.com/CTSRD-CHERI/cheribsd.git"
    defaultInstallDir = lambda cls, config: config.outputRoot / ("rootfs" + config.cheriBitsStr)
    appendCheriBitsToBuildDir = True
    defaultBuildDir = lambda cls, config: config.buildRoot / ("cheribsd-obj-" + config.cheriBitsStr)


    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(installDirectoryHelp="Install directory for CheriBSD root file system (default: "
                                   "<OUTPUT>/rootfs256 or <OUTPUT>/rootfs128 depending on --cheri-bits)")
        defaultExtraMakeOptions = [
            "DEBUG_FLAGS=-g",  # enable debug stuff
            "-DWITHOUT_TESTS",  # seems to break the creation of disk-image (METALOG is invalid)
            "-DWITHOUT_HTML",  # should not be needed
            "-DWITHOUT_SENDMAIL", "-DWITHOUT_MAIL",  # no need for sendmail
            "-DWITHOUT_SVNLITE",  # no need for SVN
            # "-DWITHOUT_GAMES",  # not needed
            # "-DWITHOUT_MAN",  # seems to be a majority of the install time
            # "-DWITH_FAST_DEPEND",  # no separate make depend step, do it while compiling
            # "-DWITH_INSTALL_AS_USER", should be enforced by -DNO_ROOT
            # "-DWITH_DIRDEPS_BUILD", "-DWITH_DIRDEPS_CACHE",  # experimental fast build options
            # "-DWITH_LIBCHERI_JEMALLOC"  # use jemalloc instead of -lmalloc_simple
        ]
        # For compatibility we still accept --cheribsd-make-options here
        cls.makeOptions = cls.addConfigOption("build-options", default=defaultExtraMakeOptions, kind=list,
                                              metavar="OPTIONS", shortname="-cheribsd-make-options",  # compatibility
                                              help="Additional make options to be passed to make when building "
                                                   "CHERIBSD. See `man src.conf` for more info.")
        # TODO: separate options for kernel/install?
        cls.kernelConfig = cls.addConfigOption("kernel-cofig", default=defaultKernelConfig, kind=str,
                                               metavar="CONFIG", shortname="-kernconf",
                                               help="The kernel configuration to use for `make buildkernel` (default: "
                                                    "CHERI_MALTA64 or CHERI128_MALTA64 depending on --cheri-bits)")
        cls.skipBuildworld = cls.addBoolOption("only-build-kernel", shortname="-skip-buildworld",
                                               help="Skip the buildworld step -> only build and install the kernel")

        cls.forceClang = cls.addBoolOption("force-clang", help="Use clang for building everything")
        cls.forceSDKLinker = cls.addBoolOption("force-sdk-linker", help="Let clang use the linker from the installed "
                                               "SDK instead of the one built in the bootstrap process. WARNING: May "
                                               "cause unexpected linker errors!")

    @classmethod
    def rootfsDir(cls, config):
        return cls.getInstallDir(config)

    def __init__(self, config: CheriConfig, *, projectName="cheribsd"):
        super().__init__(config, gitRevision=config.cheriBsdRevision)
        self.binutilsDir = self.config.sdkDir / "mips64/bin"
        self.cheriCC = self.config.sdkDir / "bin/clang"
        self.cheriCXX = self.config.sdkDir / "bin/clang++"
        self.installAsRoot = os.getuid() == 0
        self.commonMakeArgs.extend([
            "CHERI=" + self.config.cheriBitsStr,
            # "-dCl",  # add some debug output to trace commands properly
            "CHERI_CC=" + str(self.cheriCC),
            # "CPUTYPE=mips64", # mipsfpu for hardware float
            # (apparently no longer supported: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            "-DNO_ROOT",  # use this even if current user is root, as without it the METALOG file is not created
            # "CROSS_BINUTILS_PREFIX=" + str(self.binutilsDir),  # use the CHERI-aware binutils and not the builtin ones
            # TODO: once clang can build the kernel:
            #  "-DCROSS_COMPILER_PREFIX=" + str(self.config.sdkDir / "bin")
            "KERNCONF=" + self.kernelConfig,
        ])

        if self.forceClang:
            self.commonMakeArgs.append("XCC=" + str(self.config.sdkDir / "bin/cheri-unknown-freebsd-clang") + " -integrated-as")
            self.commonMakeArgs.append("XCXX=" + str(self.config.sdkDir / "bin/cheri-unknown-freebsd-clang++") + " -integrated-as")
            self.commonMakeArgs.append("XCFLAGS=-integrated-as")
            self.commonMakeArgs.append("XCXXLAGS=-integrated-as")

        self.commonMakeArgs.extend(self.makeOptions)
        if not (self.config.verbose or self.config.quiet):
            # By default we only want to print the status updates -> use make -s so we have to do less filtering
            self.commonMakeArgs.append("-s")

    def _stdoutFilter(self, line: bytes):
        if line.startswith(b">>> "):  # major status update
            if self._lastStdoutLineCanBeOverwritten:
                sys.stdout.buffer.write(Project.clearLineSequence)
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
            self._lastStdoutLineCanBeOverwritten = False
        elif line.startswith(b"===> "):  # new subdirectory
            self._lineNotImportantStdoutFilter(line)
        elif line == b"--------------------------------------------------------------\n":
            return  # ignore separator around status updates
        elif line == b"\n":
            return  # ignore empty lines when filtering
        elif line.endswith(b"'is up to date.\n"):
            return  # ignore these messages caused by (unnecessary?) recursive make invocations
        else:
            self._showLineStdoutFilter(line)

    def _removeSchgFlag(self, *paths: "typing.Iterable[str]"):
        for i in paths:
            file = self.installDir / i
            if file.exists():
                runCmd("chflags", "noschg", str(file))

    def setupEnvironment(self):
        if not self.cheriCC.is_file():
            fatalError("CHERI CC does not exist: ", self.cheriCC)
        if not self.cheriCXX.is_file():
            fatalError("CHERI CXX does not exist: ", self.cheriCXX)
        # if not (self.binutilsDir / "as").is_file():
        #     fatalError("CHERI MIPS binutils are missing. Run 'cheribuild.py binutils'?")
        if not self.skipBuildworld:
            if self.installAsRoot:
                # we need to remove the schg flag as otherwise rm -rf will fail to remove these files
                self._removeSchgFlag(
                    "lib/libc.so.7", "lib/libcrypt.so.5", "lib/libthr.so.3", "libexec/ld-cheri-elf.so.1",
                    "libexec/ld-elf.so.1", "sbin/init", "usr/bin/chpass", "usr/bin/chsh", "usr/bin/ypchpass",
                    "usr/bin/ypchfn", "usr/bin/ypchsh", "usr/bin/login", "usr/bin/opieinfo", "usr/bin/opiepasswd",
                    "usr/bin/passwd", "usr/bin/yppasswd", "usr/bin/su", "usr/bin/crontab", "usr/lib/librt.so.1",
                    "var/empty"
                )
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # if we installed as root remove the schg flag from files before cleaning (otherwise rm will fail)
            self._cleanDir(self.installDir, force=True)
        else:
            self.makedirs(self.installDir)

    def clean(self):
        if self.skipBuildworld:
            # TODO: only clean the current kernel config not all of them
            kernelBuildDir = self.buildDir / ("mips.mips64" + str(self.sourceDir) + "/sys/")
            self._cleanDir(kernelBuildDir)
        else:
            super().clean()

    def compile(self):
        self.setupEnvironment()
        programsToMove = ["cheri-unknown-freebsd-ld", "mips4-unknown-freebsd-ld", "mips64-unknown-freebsd-ld", "ld",
                          "objcopy", "objdump"]
        sdkBinDir = self.cheriCC.parent
        if not self.forceSDKLinker:
            for l in programsToMove:
                if (sdkBinDir / l).exists():
                    runCmd("mv", "-f", l, l + ".backup", cwd=sdkBinDir)
        try:
            if not self.skipBuildworld:
                self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildworld", cwd=self.sourceDir)
            self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildkernel", cwd=self.sourceDir,
                         compilationDbName="compile_commands_" + self.kernelConfig + ".json")
        finally:
            # restore the linkers
            if not self.forceSDKLinker:
                for l in programsToMove:
                    if (sdkBinDir / (l + ".backup")).exists():
                        runCmd("mv", "-f", l + ".backup", l, cwd=sdkBinDir)

    def install(self):
        # don't use multiple jobs here
        installArgs = self.commonMakeArgs + ["DESTDIR=" + str(self.installDir)]
        self.runMake(installArgs, "installkernel", cwd=self.sourceDir)
        if not self.skipBuildworld:
            self.runMake(installArgs, "installworld", cwd=self.sourceDir)
            self.runMake(installArgs, "distribution", cwd=self.sourceDir)

    def process(self):
        if not IS_FREEBSD:
            statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need to scp",
                         "the required files from another server (see --frebsd-build-server options)")
            return
        # make sure the new clang and other tool are picked up
        # TODO: this shouldn't be needed, we build binutils as part of cheribsd
        path = os.getenv("PATH")
        if not path.startswith(str(self.config.sdkDir)):
            path = str(self.config.sdkDir / "bin") + ":" + path
        with setEnv(MAKEOBJDIRPREFIX=str(self.buildDir), PATH=path):
            super().process()
