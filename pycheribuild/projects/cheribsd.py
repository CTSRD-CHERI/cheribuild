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
import subprocess
import sys

from pathlib import Path
from ..config.loader import ComputedDefaultValue
from ..project import *
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

    defaultInstallDir = ComputedDefaultValue(
        function=lambda config, cls: config.outputRoot / "freebsd-mips",
        asString="$INSTALL_ROOT/freebsd-mips")

    @classmethod
    def rootfsDir(cls, config):
        return cls.getInstallDir(config)

    @classmethod
    def setupConfigOptions(cls, *, buildKernelWithClang: bool = False, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.subdirOverride = cls.addConfigOption("subdir", kind=str, metavar="DIR", showHelp=True,
                                                 help="Only build subdir DIR instead of the full tree. "
                                                      "Useful for quickly rebuilding an individual program/library")
        cls.keepOldRootfs = cls.addBoolOption("keep-old-rootfs", help="Don't remove the whole old rootfs directory. "
                                              " This can speed up installing but may cause strange errors so is off "
                                              "by default")
        defaultExternalToolchain = ComputedDefaultValue(function=lambda config, cls: config.sdkDir,
                                                        asString="$CHERI_SDK_DIR")
        cls.mipsToolchainPath = cls.addPathOption("mips-toolchain", help="Path to the mips64-unknown-freebsd-* tools",
                                                  default=defaultExternalToolchain)
        # override in CheriBSD
        cls.skipBuildworld = False
        cls.kernelConfig = "MALTA64"
        cls.useExternalToolchainForKernel = cls.addBoolOption("use-external-toolchain-for-kernel", showHelp=True,
                                                              help="build the kernel with the external toolchain",
                                                              default=buildKernelWithClang)
        cls.useExternalToolchainForWorld = cls.addBoolOption("use-external-toolchain-for-world", showHelp=True,
                                                             help="Build world with the external toolchain"
                                                                  " (probably won't work!)")
        cls.linkKernelWithLLD = cls.addBoolOption("link-kernel-with-lld")
        cls.addDebugInfoFlag = cls.addBoolOption("debug-info",
                                                 help="pass make flags for building debug info",
                                                 default=True, showHelp=True)
        cls.buildTests = cls.addBoolOption("build-tests", help="Build the tests too (-DWITH_TESTS)", showHelp=True)

    def _stdoutFilter(self, line: bytes):
        if line.startswith(b">>> "):  # major status update
            if self._lastStdoutLineCanBeOverwritten:
                sys.stdout.buffer.write(Project._clearLineSequence)
            sys.stdout.buffer.write(line)
            flushStdio(sys.stdout)
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
        ])
        self.externalToolchainArgs = []
        self.externalToolchainCompiler = Path()
        if self.mipsToolchainPath:
            cross_prefix = str(self.mipsToolchainPath / "bin/mips64-unknown-freebsd-")
            self.externalToolchainCompiler = Path(cross_prefix + "clang")
            # self.externalToolchainArgs.append("CROSS_BINUTILS_PREFIX=" + cross_prefix)
            # cross assembler
            # PIC code is the default so we have to add -fno-pic
            # clang_flags = " -integrated-as -mabi=n64 -fcolor-diagnostics -mxgot -fno-pic -mabicalls -D__ABICALLS__=1"
            clang_flags = " -integrated-as -fcolor-diagnostics -mxgot"
            # self.externalToolchainArgs.append("XAS=" + cross_prefix + "clang" + clang_flags)
            self.externalToolchainArgs.append("XCC=" + cross_prefix + "clang" + clang_flags)
            self.externalToolchainArgs.append("XCXX=" + cross_prefix + "clang++" + clang_flags)
            self.externalToolchainArgs.append("XCPP=" + cross_prefix + "clang-cpp" + clang_flags)
            self.externalToolchainArgs.append("XLD=" + cross_prefix + "ld.lld")
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
            self.externalToolchainArgs.append("-DWITHOUT_CLANG")
            self.externalToolchainArgs.append("-DWITHOUT_GCC_BOOTSTRAP")
            self.externalToolchainArgs.append("-DWITHOUT_CLANG_BOOTSTRAP")
            self.externalToolchainArgs.append("WERROR=-Wno-error")
            # self.externalToolchainArgs.append("-DWITHOUT_BINUTILS_BOOTSTRAP")
            # self.externalToolchainArgs.append("-DWITHOUT_ELFTOOLCHAIN_BOOTSTRAP")

        if self.addDebugInfoFlag:
            self.commonMakeArgs.append("DEBUG_FLAGS=-g")

        if self.buildTests:
            self.commonMakeArgs.append("-DWITH_TESTS")
        else:
            # often seems to break the creation of disk-image (METALOG is invalid)
            self.commonMakeArgs.append("-DWITHOUT_TESTS")

        if not self.config.verbose and not self.config.quiet:
            # By default we only want to print the status updates -> use make -s so we have to do less filtering
            self.commonMakeArgs.append("-s")

        # build only part of the tree
        if self.subdirOverride:
            self.commonMakeArgs.append("SUBDIR_OVERRIDE=" + self.subdirOverride)

        self.destdir = self.installDir
        self.kernelToolchainAlreadyBuilt = False

    @property
    def buildworldArgs(self):
        if self.useExternalToolchainForWorld:
            if not self.externalToolchainCompiler.exists():
                fatalError("Requested build of world with external toolchain, but", self.externalToolchainCompiler,
                           "doesn't exist!")
            return self.commonMakeArgs + self.externalToolchainArgs
        return self.commonMakeArgs

    def kernelMakeArgsForConfig(self, kernconf: str):
        kernelMakeFlags = self.commonMakeArgs.copy()
        if self.useExternalToolchainForKernel:
            if not self.externalToolchainCompiler.exists():
                fatalError("Requested build of kernel with external toolchain, but", self.externalToolchainCompiler,
                           "doesn't exist!")
            # We can't use LLD for the kernel yet but there is a flag to experiment with it
            if self.linkKernelWithLLD:
                kernelToolChainArgs = self.externalToolchainArgs
            else:
                kernelToolChainArgs = list(filter(lambda s: not s.startswith("XLD"), self.externalToolchainArgs))
            kernelMakeFlags.extend(kernelToolChainArgs)
        kernelMakeFlags.append("KERNCONF=" + kernconf)
        return kernelMakeFlags

    def clean(self) -> ThreadJoiner:
        if self.skipBuildworld:
            # TODO: only clean the current kernel config not all of them
            kernelBuildDir = self.buildDir / ("mips.mips64" + str(self.sourceDir) + "/sys/")
            return self.asyncCleanDirectory(kernelBuildDir)
        else:
            return super().clean()

    def _buildkernel(self, kernconf: str):
        kernelMakeArgs = self.kernelMakeArgsForConfig(kernconf)
        # needKernelToolchain = not self.useExternalToolchainForKernel
        dontNeedKernelToolchain = self.useExternalToolchainForKernel and self.linkKernelWithLLD
        if not dontNeedKernelToolchain and not self.kernelToolchainAlreadyBuilt:
            # we need to build GCC to build the kernel:
            toolchainOpts = self.commonMakeArgs + ["-DWITHOUT_LLD_BOOTSTRAP", "-DWITHOUT_CLANG_BOOTSTRAP",
                                                   "-DWITHOUT_CLANG"]
            toolchainOpts.append("-DWITHOUT_GCC_BOOTSTRAP" if self.useExternalToolchainForKernel else "-DWITH_GCC_BOOTSTRAP")
            self.runMake(toolchainOpts + self.jflag, "kernel-toolchain", cwd=self.sourceDir)
            self.kernelToolchainAlreadyBuilt = True

        self.runMake(kernelMakeArgs + self.jflag, "buildkernel", cwd=self.sourceDir,
                     compilationDbName="compile_commands_" + self.kernelConfig + ".json")

    @property
    def jflag(self) -> list:
        return [self.config.makeJFlag] if self.config.makeJobs > 1 else []

    def compile(self, **kwargs):
        # The build seems to behave differently when -j1 is passed (it still complains about parallel make failures)
        # so just omit the flag here if the user passes -j1 on the command line
        if self.config.verbose:
            self.runMake(self.buildworldArgs, "showconfig", cwd=self.sourceDir)
        if not self.skipBuildworld:
            self.runMake(self.buildworldArgs + self.jflag, "buildworld", cwd=self.sourceDir)
        if not self.subdirOverride:
            self._buildkernel(kernconf=self.kernelConfig)

    def _removeOldRootfs(self):
        assert self.config.clean or not self.keepOldRootfs
        if self.skipBuildworld:
            self.makedirs(self.installDir)
        else:
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # We have to keep the rootfs directory in case it has been NFS mounted
            self.cleanDirectory(self.installDir, keepRoot=True)

    def install(self, **kwargs):
        if self.subdirOverride:
            statusUpdate("Skipping install step because SUBDIR_OVERRIDE was set")
            return
        # keeping the old rootfs directory prior to install can sometimes cause the build to fail so delete by default
        if self.config.clean or not self.keepOldRootfs:
            self._removeOldRootfs()
        # don't use multiple jobs here
        self.runMakeInstall(args=self.kernelMakeArgsForConfig(self.kernelConfig), target="installkernel",
                            cwd=self.sourceDir)
        if not self.skipBuildworld:
            installworldArgs = self.buildworldArgs.copy()
            # https://github.com/CTSRD-CHERI/cheribsd/issues/220
            # installworld reads compiler metadata which was written by kernel-toolchain which means that
            # it will attempt to install libc++ because compiler for kernel is now clang and not GCC
            # as a workaround force writing the compiler metadata by invoking the _compiler-metadata target

            try:
                runCmd(["make"] + installworldArgs + ["_build-metadata"], cwd=self.sourceDir)
            except subprocess.CalledProcessError:
                try:
                    # support building old versions of cheribsd before _compiler-metadata was renamed to _build-metadata
                    runCmd(["make"] + installworldArgs + ["_compiler-metadata"], cwd=self.sourceDir)
                except subprocess.CalledProcessError:
                    warningMessage("Failed to run either target _compiler-metadata or _build_metadata, build system has changed!")

            self.runMakeInstall(args=installworldArgs, target="installworld", cwd=self.sourceDir)
            self.runMakeInstall(args=installworldArgs, target="distribution", cwd=self.sourceDir)

    def process(self):
        if not IS_FREEBSD:
            statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need to scp",
                         "the required files from another server (see --frebsd-build-server options)")
            return
        with setEnv(printVerboseOnly=False, MAKEOBJDIRPREFIX=str(self.buildDir)):
            super().process()


class BuildCHERIBSD(BuildFreeBSD):
    projectName = "cheribsd"
    target = "cheribsd-without-sysroot"
    dependencies = ["llvm"]
    repository = "https://github.com/CTSRD-CHERI/cheribsd.git"
    defaultInstallDir = lambda config, cls: config.outputRoot / ("rootfs" + config.cheriBitsStr)
    appendCheriBitsToBuildDir = True
    defaultBuildDir = lambda config, cls: config.buildRoot / ("cheribsd-obj-" + config.cheriBitsStr)

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(
            buildKernelWithClang=True,
            installDirectoryHelp="Install directory for CheriBSD root file system (default: "
                                   "<OUTPUT>/rootfs256 or <OUTPUT>/rootfs128 depending on --cheri-bits)")
        defaultExtraMakeOptions = [
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

        defaultCheriCC = ComputedDefaultValue(
            function=lambda config, unused: config.sdkDir / "bin/clang",
            asString="${SDK_DIR}/bin/clang")
        cls.cheriCC = cls.addPathOption("cheri-cc", help="Override the compiler used to build CHERI code",
                                        default=defaultCheriCC)

        cls.forceSDKLinker = cls.addBoolOption("force-sdk-linker", help="Let clang use the linker from the installed "
                                               "SDK instead of the one built in the bootstrap process. WARNING: May "
                                               "cause unexpected linker errors!")
        cls.buildFpgaKernels = cls.addBoolOption("build-fpga-kernels", help="Also build kernels for the FPGA. They will "
                                                 "not be installed so you need to copy them from the build directory.",
                                                  showHelp=True)
        cls.mipsOnly = cls.addBoolOption("mips-only", showHelp=False,
                                         help="Don't build the CHERI parts of cheribsd, only plain MIPS")

    def __init__(self, config: CheriConfig):
        self.installAsRoot = os.getuid() == 0
        self.cheriCXX = self.cheriCC.parent / "clang++"
        archBuildFlags = [
            "CHERI=" + config.cheriBitsStr,
            "CHERI_CC=" + str(self.cheriCC),
            "CHERI_CXX=" + str(self.cheriCXX)
        ]
        if self.mipsOnly:
            archBuildFlags = [
                "TARGET=mips",
                "TARGET_ARCH=mips64",
                "-DWITHOUT_LIB32"
            ]
            self.kernelConfig = "MALTA64"
        super().__init__(config, archBuildFlags=archBuildFlags)

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
        programsToMove = ["cheri-unknown-freebsd-ld", "mips4-unknown-freebsd-ld", "mips64-unknown-freebsd-ld", "ld",
                          "objcopy", "objdump"]
        sdkBinDir = self.cheriCC.parent
        if not self.forceSDKLinker:
            for l in programsToMove:
                if (sdkBinDir / l).exists():
                    runCmd("mv", "-f", l, l + ".backup", cwd=sdkBinDir)
        try:
            super().compile()
            if self.buildFpgaKernels:
                for conf in ("USBROOT", "SDROOT", "NFSROOT", "MDROOT"):
                    prefix = "CHERI128_DE4_" if self.config.cheriBits == 128 else "CHERI_DE4_"
                    self._buildkernel(kernconf=prefix + conf)
        finally:
            # restore the linkers
            if not self.forceSDKLinker:
                for l in programsToMove:
                    if (sdkBinDir / (l + ".backup")).exists():
                        runCmd("mv", "-f", l + ".backup", l, cwd=sdkBinDir)


class BuildCheriBsdSysroot(SimpleProject):
    projectName = "cheribsd-sysroot"
    dependencies = ["cheribsd-without-sysroot"]

    def fixSymlinks(self):
        # copied from the build_sdk.sh script
        # TODO: we could do this in python as well, but this method works
        fixlinksSrc = includeLocalFile("files/fixlinks.c")
        runCmd("cc", "-x", "c", "-", "-o", self.config.sdkDir / "bin/fixlinks", input=fixlinksSrc)
        runCmd(self.config.sdkDir / "bin/fixlinks", cwd=self.config.sdkSysrootDir / "usr/lib")

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        if not IS_FREEBSD and not self.remotePath:
            configOption = "'--" + self.target + "/" + "remote-sdk-path'"
            fatalError("Path to the remote SDK is not set, option", configOption, "must be set to a path that "
                       "scp understands (e.g. vica:~foo/cheri/output/sdk256)")
            sys.exit("Cannot continue...")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        if not IS_FREEBSD:
            cls.remotePath = cls.addConfigOption("remote-sdk-path", showHelp=True, metavar="PATH", help="The path to "
                                                 "the CHERI SDK on the remote FreeBSD machine (e.g. "
                                                 "vica:~foo/cheri/output/sdk256)")

    def copySysrootFromRemoteMachine(self):
        statusUpdate("Cannot build disk image on non-FreeBSD systems, will attempt to copy instead.")
        assert self.remotePath
        # noinspection PyAttributeOutsideInit
        self.remotePath = os.path.expandvars(self.remotePath)
        remoteSysrootArchive = self.remotePath + "/" + self.config.sysrootArchiveName
        statusUpdate("Will copy the sysroot files from ", remoteSysrootArchive, sep="")
        if not self.queryYesNo("Continue?"):
            return

        # now copy the files
        self.makedirs(self.config.sdkSysrootDir)
        self.copyRemoteFile(remoteSysrootArchive, self.config.sdkDir / self.config.sysrootArchiveName)
        runCmd("tar", "xzf", self.config.sdkDir / self.config.sysrootArchiveName, cwd=self.config.sdkDir)

    def createSysroot(self):
        # we need to add include files and libraries to the sysroot directory
        self.makedirs(self.config.sdkSysrootDir / "usr")
        # use tar+untar to copy all necessary files listed in metalog to the sysroot dir
        archiveCmd = ["tar", "cf", "-", "--include=./lib/", "--include=./usr/include/",
                      "--include=./usr/lib/", "--include=./usr/libcheri", "--include=./usr/libdata/",
                      # only pack those files that are mentioned in METALOG
                      "@METALOG"]
        printCommand(archiveCmd, cwd=BuildCHERIBSD.rootfsDir(self.config))
        if not self.config.pretend:
            with subprocess.Popen(archiveCmd, stdout=subprocess.PIPE, cwd=str(BuildCHERIBSD.rootfsDir(self.config))) as tar:
                runCmd(["tar", "xf", "-"], stdin=tar.stdout, cwd=self.config.sdkSysrootDir)
        if not (self.config.sdkSysrootDir / "lib/libc.so.7").is_file():
            fatalError(self.config.sdkSysrootDir, "is missing the libc library, install seems to have failed!")

        # fix symbolic links in the sysroot:
        print("Fixing absolute paths in symbolic links inside lib directory...")
        self.fixSymlinks()
        # create an archive to make it easier to copy the sysroot to another machine
        self.deleteFile(self.config.sdkDir / self.config.sysrootArchiveName, printVerboseOnly=True)
        runCmd("tar", "-czf", self.config.sdkDir / self.config.sysrootArchiveName, "sysroot",
               cwd=self.config.sdkDir)
        print("Successfully populated sysroot")

    def process(self):
        with self.asyncCleanDirectory(self.config.sdkSysrootDir):
            if IS_FREEBSD:
                self.createSysroot()
            else:
                self.copySysrootFromRemoteMachine()
            # clang++ expects libgcc_eh to exist:
            libgcc_eh = self.config.sdkDir / "sysroot/usr/libcheri/libgcc_eh.a"
            if not libgcc_eh.is_file():
                warningMessage("CHERI libgcc_eh missing! You should probably update CheriBSD")
                runCmd("ar", "rc", libgcc_eh)


class BuildCheriBsdAndSysroot(TargetAlias):
    target = "cheribsd"
    dependencies = ["cheribsd-without-sysroot", "cheribsd-sysroot"]
