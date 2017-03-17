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
from ..configloader import ConfigLoader
from ..utils import *


def defaultKernelConfig(config: CheriConfig, project):
    if config.cheriBits == 128:
        # make sure we use a kernel with 128 bit CPU features selected
        return "CHERI128_MALTA64"
    return "CHERI_MALTA64"


class BuildFreeBSD(Project):
    dependencies = ["llvm"]
    projectName = "freebsd-mips"
    repository = "https://github.com/freebsd/freebsd.git"

    defaultInstallDir = ConfigLoader.ComputedDefaultValue(
        function=lambda config, cls: config.outputRoot / "freebsd-mips",
        asString="$INSTALL_ROOT/freebsd-mips")

    @classmethod
    def rootfsDir(cls, config):
        return cls.getInstallDir(config)

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.subdirOverride = cls.addConfigOption("subdir", kind=str, metavar="DIR", showHelp=True,
                                                 help="Only build subdir DIR instead of the full tree. "
                                                      "Useful for quickly rebuilding an individual program/library")
        cls.keepOldRootfs = cls.addBoolOption("keep-old-rootfs", help="Don't remove the whole old rootfs directory. "
                                              " This can speed up installing but may cause strange errors so is off "
                                              "by default")
        cls.mipsToolchainPath = cls.addPathOption("mips-toolchain", help="Path to the mips64-unknown-freebsd-* tools")
        # override in CheriBSD
        cls.skipBuildworld = False
        cls.kernelConfig = "MALTA64"

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
        elif line.endswith(b"' is up to date.\n"):
            return  # ignore these messages caused by (unnecessary?) recursive make invocations
        else:
            self._showLineStdoutFilter(line)

    def __init__(self, config: CheriConfig,
                 archBuildFlags=[
                     "TARGET=mips",
                     "TARGET_ARCH=mips64",
                     # The following is broken: (https://github.com/CTSRD-CHERI/cheribsd/issues/102)
                     #"CPUTYPE=mips64",  # mipsfpu for hardware float
                     ]):
        super().__init__(config)
        self.commonMakeArgs.extend(archBuildFlags)
        self.commonMakeArgs.extend([
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            "-DNO_ROOT",  # use this even if current user is root, as without it the METALOG file is not created
            "-DWITHOUT_GDB",
            "KERNCONF=" + self.kernelConfig,
        ])
        self.externalToolchainArgs = []
        if self.mipsToolchainPath:
            cross_prefix = str(self.mipsToolchainPath / "bin/mips64-unknown-freebsd-")
            # self.externalToolchainArgs.append("CROSS_BINUTILS_PREFIX=" + cross_prefix)
            # cross assembler
            # PIC code is the default so we have to add -fno-pic
            clang_flags = " -integrated-as -mabi=n64 -fcolor-diagnostics -mxgot -fno-pic -mabicalls -D__ABICALLS__=1"
            # self.externalToolchainArgs.append("XAS=" + cross_prefix + "clang" + clang_flags)
            self.externalToolchainArgs.append("XCC=" + cross_prefix + "clang" + clang_flags)
            self.externalToolchainArgs.append("XCXX=" + cross_prefix + "clang++" + clang_flags)
            self.externalToolchainArgs.append("XCPP=" + cross_prefix + "clang-cpp" + clang_flags)
            # self.externalToolchainArgs.append("XLD=" + cross_prefix + "ld.lld")
            self.externalToolchainArgs.append("XLD_BFD=ld.bfd")
            # HACK: hardcoded path from vica
            # self.externalToolchainArgs.append("XOBJDUMP=/usr/local/bin/cheri-freebsd-objdump")
            # self.externalToolchainArgs.append("XLD_BFD=/usr/local/bin/ld.bfd -m elf64btsmip_fbsd")

            # self.externalToolchainArgs.append("XOBJDUMP=" + cross_prefix + "llvm-objdump")
            # self.externalToolchainArgs.append("OBJDUMP_FLAGS=-d -S -s -t -r -print-imm-hex")
            #add CSTD=gnu11?
            # self.externalToolchainArgs.append("XCFLAGS=-integrated-as")
            # self.externalToolchainArgs.append("XCXXLAGS=-integrated-as")
            # don't build cross GCC and cross binutils
            # self.externalToolchainArgs.append("-DWITHOUT_CROSS_COMPILER") # This sets too much, we want elftoolchain and binutils
            self.externalToolchainArgs.append("-DWITHOUT_GCC")
            self.externalToolchainArgs.append("-DWITHOUT_GCC_BOOTSTRAP")
            self.externalToolchainArgs.append("-DWITHOUT_CLANG_BOOTSTRAP")
            self.externalToolchainArgs.append("WERROR=-Wno-error")
            # self.externalToolchainArgs.append("-DWITHOUT_BINUTILS_BOOTSTRAP")
            # self.externalToolchainArgs.append("-DWITHOUT_ELFTOOLCHAIN_BOOTSTRAP")

        if not self.config.verbose and not self.config.quiet:
            # By default we only want to print the status updates -> use make -s so we have to do less filtering
            self.commonMakeArgs.append("-s")

        # build only part of the tree
        if self.subdirOverride:
            self.commonMakeArgs.append("SUBDIR_OVERRIDE=" + self.subdirOverride)

        self.destdir = self.installDir

    def clean(self) -> ThreadJoiner:
        if self.skipBuildworld:
            # TODO: only clean the current kernel config not all of them
            kernelBuildDir = self.buildDir / ("mips.mips64" + str(self.sourceDir) + "/sys/")
            return self.asyncCleanDirectory(kernelBuildDir)
        else:
            return super().clean()

    def compile(self):
        # The build seems to behave differently when -j1 is passed (it still complains about parallel make failures)
        # so just omit the flag here if the user passes -j1 on the command line
        jflag = [self.config.makeJFlag] if self.config.makeJobs > 1 else []
        if self.config.verbose:
            self.runMake(self.commonMakeArgs + self.externalToolchainArgs, "showconfig", cwd=self.sourceDir)
        if not self.skipBuildworld:
            self.runMake(self.commonMakeArgs + self.externalToolchainArgs + jflag, "buildworld", cwd=self.sourceDir)
        if not self.subdirOverride:
            # We can't use the external mips toolchain for the kernel yet..
            if self.externalToolchainArgs:
                self.runMake(self.commonMakeArgs + jflag, "kernel-toolchain", cwd=self.sourceDir,
                             compilationDbName="compile_commands_" + self.kernelConfig + ".json")
            self.runMake(self.commonMakeArgs + jflag, "buildkernel", cwd=self.sourceDir,
                         compilationDbName="compile_commands_" + self.kernelConfig + ".json")

    def _removeOldRootfs(self):
        assert self.config.clean or not self.keepOldRootfs
        if self.skipBuildworld:
            self.makedirs(self.installDir)
        else:
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # We have to keep the rootfs directory in case it has been NFS mounted
            self.cleanDirectory(self.installDir, keepRoot=True)

    def install(self):
        if self.subdirOverride:
            statusUpdate("Skipping install step because SUBDIR_OVERRIDE was set")
            return
        # keeping the old rootfs directory prior to install can sometimes cause the build to fail so delete by default
        if self.config.clean or not self.keepOldRootfs:
            self._removeOldRootfs()
        # don't use multiple jobs here
        installArgs = self.makeInstallArgs
        self.runMake(installArgs, "installkernel", cwd=self.sourceDir)
        if not self.skipBuildworld:
            self.runMake(installArgs, "installworld", cwd=self.sourceDir)
            self.runMake(installArgs, "distribution", cwd=self.sourceDir)

    def process(self):
        if not IS_FREEBSD:
            statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need to scp",
                         "the required files from another server (see --frebsd-build-server options)")
            return
        with setEnv(printVerboseOnly=False, MAKEOBJDIRPREFIX=str(self.buildDir)):
            super().process()


class BuildCHERIBSD(BuildFreeBSD):
    dependencies = ["llvm"]
    repository = "https://github.com/CTSRD-CHERI/cheribsd.git"
    defaultInstallDir = lambda config, cls: config.outputRoot / ("rootfs" + config.cheriBitsStr)
    appendCheriBitsToBuildDir = True
    defaultBuildDir = lambda config, cls: config.buildRoot / ("cheribsd-obj-" + config.cheriBitsStr)

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
                                                   "CHERIBSD. See `man src.conf` for more info.",
                                              showHelp=True)
        # TODO: separate options for kernel/install?
        cls.kernelConfig = cls.addConfigOption("kernel-cofig", default=defaultKernelConfig, kind=str,
                                               metavar="CONFIG", shortname="-kernconf", showHelp=True,
                                               help="The kernel configuration to use for `make buildkernel` (default: "
                                                    "CHERI_MALTA64 or CHERI128_MALTA64 depending on --cheri-bits)")
        cls.skipBuildworld = cls.addBoolOption("only-build-kernel", shortname="-skip-buildworld", showHelp=True,
                                               help="Skip the buildworld step -> only build and install the kernel")

        defaultCheriCC = ConfigLoader.ComputedDefaultValue(
            function=lambda config, unused: config.sdkDir / "bin/clang",
            asString="${SDK_DIR}/bin/clang")
        cls.cheriCC = cls.addPathOption("cheri-cc", help="Override the compiler used to build CHERI code",
                                        default=defaultCheriCC)

        cls.forceSDKLinker = cls.addBoolOption("force-sdk-linker", help="Let clang use the linker from the installed "
                                               "SDK instead of the one built in the bootstrap process. WARNING: May "
                                               "cause unexpected linker errors!")

    def __init__(self, config: CheriConfig):
        self.installAsRoot = os.getuid() == 0
        self.binutilsDir = config.sdkDir / "mips64/bin"
        self.cheriCXX = self.cheriCC.parent / "clang++"
        super().__init__(config, archBuildFlags=[
            "CHERI=" + config.cheriBitsStr,
            # "-dCl",  # add some debug output to trace commands properly
            "CHERI_CC=" + str(self.cheriCC),
            "CHERI_CXX=" + str(self.cheriCXX)
        ])


        self.commonMakeArgs.extend(self.makeOptions)

    def _removeSchgFlag(self, *paths: "typing.Iterable[str]"):
        for i in paths:
            file = self.installDir / i
            if file.exists():
                runCmd("chflags", "noschg", str(file))

    def _removeOldRootfs(self):
        if not self.skipBuildworld:
            if self.installAsRoot:
                # if we installed as root remove the schg flag from files before cleaning (otherwise rm will fail)
                self._removeSchgFlag(
                    "lib/libc.so.7", "lib/libcrypt.so.5", "lib/libthr.so.3", "libexec/ld-cheri-elf.so.1",
                    "libexec/ld-elf.so.1", "sbin/init", "usr/bin/chpass", "usr/bin/chsh", "usr/bin/ypchpass",
                    "usr/bin/ypchfn", "usr/bin/ypchsh", "usr/bin/login", "usr/bin/opieinfo", "usr/bin/opiepasswd",
                    "usr/bin/passwd", "usr/bin/yppasswd", "usr/bin/su", "usr/bin/crontab", "usr/lib/librt.so.1",
                    "var/empty"
                )
        super()._removeOldRootfs()

    def compile(self):
        if not self.cheriCC.is_file():
            fatalError("CHERI CC does not exist: ", self.cheriCC)
        if not self.cheriCXX.is_file():
            fatalError("CHERI CXX does not exist: ", self.cheriCXX)
        if self.mipsToolchainPath:
            mipsCC = self.mipsToolchainPath / "bin/mips64-unknown-freebsd-clang"
            if not mipsCC.is_file():
                fatalError("MIPS toolchain specified but", mipsCC, "is missing.")
        # if not (self.binutilsDir / "as").is_file():
        #     fatalError("CHERI MIPS binutils are missing. Run 'cheribuild.py binutils'?")
        programsToMove = ["cheri-unknown-freebsd-ld", "mips4-unknown-freebsd-ld", "mips64-unknown-freebsd-ld", "ld",
                          "objcopy", "objdump"]
        sdkBinDir = self.cheriCC.parent
        if not self.forceSDKLinker:
            for l in programsToMove:
                if (sdkBinDir / l).exists():
                    runCmd("mv", "-f", l, l + ".backup", cwd=sdkBinDir)
        try:
            super().compile()
        finally:
            # restore the linkers
            if not self.forceSDKLinker:
                for l in programsToMove:
                    if (sdkBinDir / (l + ".backup")).exists():
                        runCmd("mv", "-f", l + ".backup", l, cwd=sdkBinDir)
