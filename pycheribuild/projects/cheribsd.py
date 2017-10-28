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
import collections
import copy
import os
import shutil
import subprocess
import sys

from pathlib import Path
from ..config.loader import ComputedDefaultValue
from ..config.chericonfig import CrossCompileTarget
from ..project import *
from ..utils import *


# noinspection PyUnusedLocal
def defaultKernelConfig(config: CheriConfig, project):
    # make sure we use a kernel with 128 bit CPU features selected
    # or a purecap kernel is selected
    kernconf_name = "CHERI{bits}{pure}_MALTA64"
    cheri_bits = "128" if config.cheriBits == 128 else ""
    cheri_pure = "_PURECAP" if project.purecapKernel else ""
    return kernconf_name.format(bits=cheri_bits, pure=cheri_pure)

class FreeBSDCrossTools(CMakeProject):
    repository = "https://github.com/RichardsonAlex/freebsd-crossbuild.git"
    defaultInstallDir = CMakeProject._installToBootstrapTools
    projectName = "freebsd-crossbuild"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.freebsd_source_dir = cls.addPathOption("freebsd-source-directory",
                                                   help="The path to the FreeBSD source tree used for building the"
                                                        " cross tools. Defaults to the CheriBSD source directory")

    def configure(self, **kwargs):
        freebsd_dir = self.freebsd_source_dir if self.freebsd_source_dir else BuildCHERIBSD.sourceDir
        self.add_cmake_options(CHERIBSD_DIR=freebsd_dir, CMAKE_C_COMPILER=self.config.clangPath)
        super().configure()


class FreeBSDMakeOptions(object):
    def __init__(self, **kwargs):
        self._cmdline_vars = collections.OrderedDict()
        self._with_options = collections.OrderedDict()
        self.env_vars = {}
        self.add(**kwargs)

    def add(self, **kwargs):
        for k, v in kwargs.items():
            if isinstance(v, bool):
                self._with_options[k] = v
            else:
                assert v is None or isinstance(v, str)
                self._cmdline_vars[k] = v

    @property
    def commandline_flags(self) -> list:
        result = []
        for k, v in self._with_options.items():
            result.append(("-DWITH_" if v else "-DWITHOUT_") + k)
        for k, v in self._cmdline_vars.items():
            if v is None:
                result.append("-D" + k)
            else:
                assert isinstance(v, str)
                result.append(k + "=" + v)
        return result


class _BuildFreeBSD(Project):
    dependencies = ["llvm"]
    if not IS_FREEBSD:
        dependencies.append("freebsd-crossbuild")
    repository = "https://github.com/freebsd/freebsd.git"
    doNotAddToTargets = True
    target_arch = None  # type: CrossCompileTarget
    kernelConfig = None  # type: str
    crossbuild = False
    skipBuildworld = False

    defaultExtraMakeOptions = [
        # "-DWITHOUT_HTML",  # should not be needed
        # "-DWITHOUT_SENDMAIL", "-DWITHOUT_MAIL",  # no need for sendmail
        # "-DWITHOUT_SVNLITE",  # no need for SVN
        # "-DWITHOUT_GAMES",  # not needed
        # "-DWITHOUT_MAN",  # seems to be a majority of the install time
        # "-DWITH_FAST_DEPEND",  # no separate make depend step, do it while compiling
        # "-DWITH_INSTALL_AS_USER", should be enforced by -DNO_ROOT
        # "-DWITH_DIRDEPS_BUILD", "-DWITH_DIRDEPS_CACHE",  # experimental fast build options
        # "-DWITH_LIBCHERI_JEMALLOC"  # use jemalloc instead of -lmalloc_simple
    ]

    @classmethod
    def rootfsDir(cls, config):
        return cls.getInstallDir(config)

    @classmethod
    def setupConfigOptions(cls, buildKernelWithClang: bool = False, makeOptionsShortname=None, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.subdirOverride = cls.addConfigOption("subdir", kind=str, metavar="DIR", showHelp=True,
                                                 help="Only build subdir DIR instead of the full tree. "
                                                      "Useful for quickly rebuilding an individual program/library")
        cls.keepOldRootfs = cls.addBoolOption("keep-old-rootfs", help="Don't remove the whole old rootfs directory. "
                                              " This can speed up installing but may cause strange errors so is off "
                                              "by default")
        defaultExternalToolchain = ComputedDefaultValue(function=lambda config, proj: config.sdkDir,
                                                        asString="$CHERI_SDK_DIR")
        cls.mipsToolchainPath = cls.addPathOption("mips-toolchain", help="Path to the mips64-unknown-freebsd-* tools",
                                                  default=defaultExternalToolchain)
        # For compatibility we still accept --cheribsd-make-options here
        cls.makeOptions = cls.addConfigOption("build-options", default=cls.defaultExtraMakeOptions, kind=list,
                                              metavar="OPTIONS", shortname=makeOptionsShortname,  # compatibility
                                              help="Additional make options to be passed to make when building "
                                                   "CHERIBSD. See `man src.conf` for more info.",
                                              showHelp=True)
        # override in CheriBSD
        cls.useExternalToolchainForKernel = cls.addBoolOption("use-external-toolchain-for-kernel", showHelp=True,
                                                              help="build the kernel with the external toolchain",
                                                              default=buildKernelWithClang)
        cls.useExternalToolchainForWorld = cls.addBoolOption("use-external-toolchain-for-world", showHelp=True,
                                                             help="build world with the external toolchain", default=True)
        cls.linkKernelWithLLD = cls.addBoolOption("link-kernel-with-lld")
        cls.forceBFD = cls.addBoolOption("force-bfd")
        cls.addDebugInfoFlag = cls.addBoolOption("debug-info",
                                                 help="pass make flags for building debug info",
                                                 default=True, showHelp=True)
        cls.buildTests = cls.addBoolOption("build-tests", help="Build the tests too (-DWITH_TESTS)", showHelp=True)
        cls.auto_obj = cls.addBoolOption("auto-obj", help="Use -DWITH_AUTO_OBJ (experimental)", showHelp=True, default=True)
        cls.minimal = cls.addBoolOption("minimal", help="Don't build all of FreeBSD, just what is needed for running"
                                                        " most CHERI tests/benchmarks", showHelp=True)
        cls.fastRebuild = cls.addBoolOption("fast", showHelp=True,
                                            help="Skip some (usually) unnecessary build steps to speed up rebuilds")
        if not IS_FREEBSD:
            cls.crossbuild = cls.addBoolOption("crossbuild", help="Try to compile FreeBSD on non-FreeBSD machines")
        else:
            cls.crossbuild = False

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
            return  # ignore these messages caused by (unnecessary?) recursive make invocations|
        elif line.endswith(b"missing (created)\n"):
            return  # ignore these from installworld
        elif line.startswith(b"[Creating objdir"):
            return  # ignore the WITH_AUTO_OBJ messages
        else:
            self._showLineStdoutFilter(line)

    def __init__(self, config: CheriConfig, archBuildFlags: list=None):
        super().__init__(config)
        if archBuildFlags is None:
            if self.target_arch == CrossCompileTarget.MIPS:
                # The following is broken: (https://github.com/CTSRD-CHERI/cheribsd/issues/102)
                # "CPUTYPE=mips64",  # mipsfpu for hardware float
                archBuildFlags = ["TARGET=mips", "TARGET_ARCH=mips64"]
            elif self.target_arch == CrossCompileTarget.NATIVE:
                archBuildFlags = ["TARGET=amd64"]
        self.cross_toolchain_config = FreeBSDMakeOptions()
        self.common_options = FreeBSDMakeOptions()
        self.commonMakeArgs.extend(archBuildFlags)
        self.common_options.env_vars = {"MAKEOBJDIRPREFIX": str(self.buildDir)}
        self.commonMakeArgs.extend([
            "-DDB_FROM_SRC",  # don't use the system passwd file
            # "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            "-DNO_ROOT",  # use this even if current user is root, as without it the METALOG file is not created
            "-DWITHOUT_GDB",
            # '"-DCHERI_CC_COLOR_DIAGNOSTICS",  # force -fcolor-diagnostics
        ])
        if self.crossbuild:
            self.crossBinDir = self.config.outputRoot / "freebsd-cross/bin"
            self.addCrossBuildOptions()
            self.mipsToolchainPath = self.config.sdkDir
            self.useExternalToolchainForWorld = True
            self.useExternalToolchainForKernel = True

        # external toolchain options:
        self.externalToolchainCompiler = None
        self._setup_cross_toolchain_config()

        if self.addDebugInfoFlag:
            self.common_options.add(DEBUG_FLAGS="-g")

        # tests off by default because they take a long time and often seems to break
        # the creation of disk-image (METALOG is invalid)
        self.common_options.add(TESTS=self.buildTests)

        if self.minimal:
            self.common_options.add(MAN=False, KERBEROS=False, SVN=False, SVNLITE=False, MAIL=False, SENDMAIL=False,
                                    EXAMPLES=False, LOCALES=False, NLS=False, CDDL=False)

        # doesn't appear to work for buildkernel
        # if self.auto_obj:
        #     # seems like it should speed up the build significantly
        #     self.common_options.add(AUTO_OBJ=True)

        if not self.config.verbose and not self.config.quiet:
            # By default we only want to print the status updates -> use make -s so we have to do less filtering
            self.commonMakeArgs.append("-s")

        # build only part of the tree
        if self.subdirOverride:
            self.common_options.add(SUBDIR_OVERRIDE=self.subdirOverride)

        # If WITH_LD_IS_LLD is set (e.g. by reading src.conf) the symlink ld -> ld.bfd in $BUILD_DIR/tmp/ won't be
        # created and the build system will then fall back to using /usr/bin/ld which won't work!
        self.common_options.add(LLD_IS_LD=False)

        self.destdir = self.installDir
        self.kernelToolchainAlreadyBuilt = False
        self.commonMakeArgs.extend(self.makeOptions)

    @property
    def make_cmdline_flags(self) -> list:
        return self.commonMakeArgs + self.common_options.commandline_flags

    def _setup_cross_toolchain_config(self):
        self.cross_toolchain_config.add(
            GCC=False, CLANG=False, GNUCXX=False,  # Take a long time and not needed
            GCC_BOOTSTRAP=False, CLANG_BOOTSTRAP=False,  # not needed as we have a compiler
            LLD_BOOTSTRAP=False,  # and also a linker
            LIB32=False,  # takes a long time and not needed
        )
        # self.cross_toolchain_config.add(CROSS_COMPILER=Falses) # This sets too much, we want elftoolchain and binutils

        if self.target_arch == CrossCompileTarget.NATIVE:
            cross_prefix = str(self.config.sdkBinDir) + "/"  # needs to end with / for concatenation
            target_flags = " -fuse-ld=lld -Wno-error=unused-command-line-argument -Wno-unused-command-line-argument"
            self.crossLD = cross_prefix + "ld.bfd" if self.forceBFD else cross_prefix + "ld.lld"
            self.useExternalToolchainForWorld = True
            self.useExternalToolchainForKernel = True
            self.linkKernelWithLLD = True
            # DONT SET XAS!!! It prevents bfd from being built
            # self.cross_toolchain_config.add(XAS="/usr/bin/as")  # TODO: would be nice if we could compile the asm with clang

        elif self.mipsToolchainPath:
            cross_prefix = str(self.mipsToolchainPath / "bin") + "/"
            target_flags = " -integrated-as -fcolor-diagnostics -mcpu=mips4"
            self.crossLD = cross_prefix + "ld.bfd" if self.forceBFD else cross_prefix + "ld.lld"
            # for some reason this is not inferred....
            if self.crossbuild:
                # For some reason STRINGS is not set
                self.cross_toolchain_config.add(STRINGS="strings")
            # add CSTD=gnu11?
            # self.cross_toolchain_config.add(STATIC_LIBPAM=False)  # broken for MIPS
            # self.cross_toolchain_config.add(WERROR="-Wno-error")
            # Won't compile with CHERI clang yet
            self.cross_toolchain_config.add(RESCUE=False)
            self.cross_toolchain_config.add(BOOT=False)  # bootloaders won't link with LLD yet
            # DONT SET XAS!!! It prevents bfd from being built
            # self.cross_toolchain_config.add(XAS=cross_prefix + "clang " + target_flags)
        else:
            fatalError("Invalid state, should have a cross env")
            sys.exit(1)

        self.externalToolchainCompiler = Path(cross_prefix + "clang")
        self.cross_toolchain_config.add(
            XCC=cross_prefix + "clang" + target_flags,
            XCXX=cross_prefix + "clang++" + target_flags,
            XCPP=cross_prefix + "clang-cpp" + target_flags,
            X_COMPILER_TYPE="clang",
            XOBJDUMP=cross_prefix + "llvm-objdump",
            OBJDUMP=cross_prefix + "llvm-objdump",
            # FIXME: LLD doesn't quite work yet, it needs some extra flags to be passed
            # XLD=self.crossLD,
        )

    @property
    def buildworldArgs(self) -> list:
        result = self.make_cmdline_flags
        # FIXME: once it works for buildkernel remove here
        if self.auto_obj:
            result.append("-DWITH_AUTO_OBJ")
        if self.useExternalToolchainForWorld:
            if not self.externalToolchainCompiler.exists():
                fatalError("Requested build of world with external toolchain, but", self.externalToolchainCompiler,
                           "doesn't exist!")
            result += self.cross_toolchain_config.commandline_flags
        return result

    def kernelMakeArgsForConfig(self, kernconf: str) -> list:
        kernelMakeFlags = self.make_cmdline_flags
        if "-DWITH_AUTO_OBJ" in kernelMakeFlags:
            kernelMakeFlags.remove("-DWITH_AUTO_OBJ")
        if self.useExternalToolchainForKernel:
            if not self.externalToolchainCompiler.exists():
                fatalError("Requested build of kernel with external toolchain, but", self.externalToolchainCompiler,
                           "doesn't exist!")
            # We can't use LLD for the kernel yet but there is a flag to experiment with it
            if self.target_arch == CrossCompileTarget.NATIVE:
                cross_prefix = str(self.config.sdkBinDir) + "/"
            else:
                cross_prefix = str(self.mipsToolchainPath / "bin/mips64-unknown-freebsd-")

            kernel_options = copy.deepcopy(self.cross_toolchain_config)

            if self.linkKernelWithLLD:
                linker = cross_prefix + "ld.lld"
                fuse_ld_flag = "-fuse-ld=lld"
            else:
                fuse_ld_flag = "-fuse-ld=bfd"
                linker = cross_prefix + "ld.bfd" if self.crossbuild else "ld.bfd"
            kernel_options.add(LD=linker, XLD=linker,
                               LDFLAGS=fuse_ld_flag, HACK_LDFLAGS=fuse_ld_flag, TRAMP_LDFLAGS=fuse_ld_flag)
            kernelMakeFlags.extend(kernel_options.commandline_flags)
        if self.crossbuild:
            kernelMakeFlags.append("-DWITHOUT_KERNEL_TRAMPOLINE")
            # kernelMakeFlags.append("OBJCOPY=false")
            # Debug won't work yet (bad objcopy)
            kernelMakeFlags = list(filter(lambda s: not s.startswith("DEBUG"), kernelMakeFlags))
            kernelMakeFlags.append("-DINSTALL_NODEBUG")
        kernelMakeFlags.append("KERNCONF=" + kernconf)
        return kernelMakeFlags

    def clean(self) -> ThreadJoiner:
        if self.config.skipBuildworld:
            # TODO: only clean the current kernel config not all of them
            kernelBuildDir = self.buildDir / ("mips.mips64" + str(self.sourceDir) + "/sys/")
            return self.asyncCleanDirectory(kernelBuildDir)
        else:
            return super().clean()

    def runMake(self, *args, env: dict=None, **kwargs):
        if env is None:
            env = self.common_options.env_vars
        super().runMake(*args, env=env, **kwargs)

    def _buildkernel(self, kernconf: str):
        kernelMakeArgs = self.kernelMakeArgsForConfig(kernconf)
        # needKernelToolchain = not self.useExternalToolchainForKernel
        dontNeedKernelToolchain = self.useExternalToolchainForKernel and self.linkKernelWithLLD
        if self.crossbuild:
            dontNeedKernelToolchain = True
        if not dontNeedKernelToolchain and not self.kernelToolchainAlreadyBuilt:
            # we need to build GCC to build the kernel:
            kernel_toolchain_opts = FreeBSDMakeOptions(LLD_BOOTSTRAP=False, CLANG=False, CLANG_BOOTSTRAP=False)
            kernel_toolchain_opts.add(GCC_BOOTSTRAP=self.useExternalToolchainForKernel)
            toolchainOpts = self.make_cmdline_flags + kernel_toolchain_opts.commandline_flags
            # FIXME: once it works for buildkernel remove here
            if self.auto_obj:
                toolchainOpts.append("-DWITH_AUTO_OBJ")
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
        if not self.config.skipBuildworld:
            fastArgs = ["-DWORLDFAST"] if self.fastRebuild else []
            self.runMake(self.buildworldArgs + fastArgs + self.jflag, "buildworld", cwd=self.sourceDir)
        if not self.subdirOverride:
            self._buildkernel(kernconf=self.kernelConfig)

    def _removeOldRootfs(self):
        assert self.config.clean or not self.keepOldRootfs
        if self.config.skipBuildworld:
            self.makedirs(self.installDir)
        else:
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # We have to keep the rootfs directory in case it has been NFS mounted
            self.cleanDirectory(self.installDir, keepRoot=True)

    @property
    def makeInstallEnv(self):
        result = super().makeInstallEnv
        result.update(self.common_options.env_vars)
        return result

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
        if not self.config.skipBuildworld:
            installworldArgs = self.buildworldArgs.copy()
            # https://github.com/CTSRD-CHERI/cheribsd/issues/220
            # installworld reads compiler metadata which was written by kernel-toolchain which means that
            # it will attempt to install libc++ because compiler for kernel is now clang and not GCC
            # as a workaround force writing the compiler metadata by invoking the _compiler-metadata target

            try:
                self.runMake(installworldArgs, "_build-metadata", cwd=self.sourceDir, env=self.makeInstallEnv)
            except subprocess.CalledProcessError:
                try:
                    # support building old versions of cheribsd before _compiler-metadata was renamed to _build-metadata
                    self.runMake(installworldArgs + ["_compiler-metadata"], cwd=self.sourceDir, env=self.makeInstallEnv)
                except subprocess.CalledProcessError:
                    warningMessage("Failed to run either target _compiler-metadata or "
                                   "_build_metadata, build system has changed!")

            installworldWithJflag = installworldArgs + [self.config.makeJFlag]
            self.runMakeInstall(args=installworldWithJflag, target="installworld", cwd=self.sourceDir)
            self.runMakeInstall(args=installworldWithJflag , target="distribution", cwd=self.sourceDir)

    def add_mips_crossbuildOptions(self):
        self.common_options.add(CROSS_BINUTILS_PREFIX=str(self.config.sdkBinDir) + "/mips64-unknown-freebsd-")
        if not self.forceBFD:
            self.common_options.add(MIPS_LINK_WITH_LLD=None)
        self.common_options.add(BOOT=False)
        if self.forceBFD:
            self.common_options.env_vars["XLDFLAGS"] = "-fuse-ld=bfd"
        else:
            self.common_options.env_vars["XLDFLAGS"] = "-fuse-ld=lld"
        # self.common_options.env_vars["XCFLAGS"] = "-fuse-ld=bfd"

    def add_x86_crossbuildOptions(self):
        self.common_options.add(CROSS_BINUTILS_PREFIX=str(self.config.sdkBinDir) + "/")
        # seems to be missing some include paths which appears to work on freebsd
        self.common_options.add(BHYVE=False)
        self.common_options.add(CTF=False)  # can't crossbuild ctfconvert yet
        self.common_options.add(BOOT=True)

    def addCrossBuildOptions(self):
        # when cross compiling we need to specify the path to the bsd makefiles (-m src/share/mk)
        self.makeCommand = shutil.which("bmake", path=self.config.dollarPathWithOtherTools) # make is usually gnu make
        # TODO: is this needed?
        # self.commonMakeArgs.extend(["-m", str(self.sourceDir / "share/mk")])
        # we also need to ensure that our SDK build tools are being picked up first
        build_path = str(self.config.sdkBinDir) + ":" + str(self.crossBinDir)
        self.common_options.env_vars["PATH"] = build_path
        # Tell glibc functions to be POSIX compatible
        # Would be ideal, but it seems like there is too much that depends on non-posix flags
        # self.common_options.env_vars["POSIXLY_CORRECT"] = "1"
        self.common_options.add(PATH=build_path)
        # kerberos still needs some changes:
        # self.commonMakeArgs.append("-DWITHOUT_KERBEROS")
        # building without an external toolchain won't work:
        self.mipsToolchainPath = self.config.sdkDir
        self.common_options.add(BINUTILS_BOOTSTRAP=False, ELFTOOLCHAIN_BOOTSTRAP=False)
        # TODO: not sure this is needed
        # self.commonMakeArgs.append("AWK=" + str(self.config.sdkBinDir / "nawk"))

        # use clang for the build tools:
        self.common_options.env_vars["CC"] = str(self.config.clangPath)
        self.common_options.env_vars["CXX"] = str(self.config.clangPlusPlusPath)
        # TODO: also set these? (I guess not as we always want to force crossbuilding and x86_64 is not recognized)
        # self.common_options.env_vars["MACHINE"] = "amd64"
        # self.common_options.env_vars["MACHINE_ARCH"] = "amd64"

        if IS_MAC:
            # For some reason on a mac bmake can't execute elftoolchain objcopy -> use gnu version
            # self._addRequiredSystemTool("gobjcopy", homebrewPackage="binutils")
            # self.common_options.add(OBJDUMP="gobjdump", OBJCOPY="gobjcopy")
            self.common_options.add(OBJDUMP=str(self.config.sdkBinDir / "llvm-objdump"),
                                    OBJCOPY=str(self.config.sdkBinDir / "objcopy"))
            # DEBUG files are too big, can't use objcopy for serparate debug files
            self.common_options.add(DEBUG_FILES=False)
            self.common_options.add(CROSSBUILD="mac")  # TODO: infer in makefile
        else:
            assert IS_LINUX, sys.platform
            self.common_options.add(CROSSBUILD="linux")  # TODO: infer in makefile

        # don't build all the bootstrap tools (just pretend we are running freebsd 42):
        self.common_options.env_vars["OSRELDATE"] = "4204345"

        # localedef is really hard to crosscompile -> skip this for now
        self.common_options.add(LOCALES=False)


        # bootstrap tool won't build
        self.common_options.add(SYSCONS=False, USB=False, GPL_DTC=False, GAMES=False)
        self.common_options.add(CDDL=False)  # lots of bootstrap tools issues

        # needs lint binary but will also set MK_INCLUDES:=no which we need (see src.opts.mk)
        # self.common_options.add(TOOLCHAIN=False)
        self.common_options.add(BINUTILS=False, CLANG=False, GCC=False, GDB=False, LLD=False, LLDB=False)

        # TODO: build these for zoneinfo setup
        # "zic", "tzsetup"
        self.common_options.add(ZONEINFO=False)

        self.common_options.add(KERBEROS=False)  # needs some more work with bootstrap tools

        # won't work with CHERI
        self.common_options.add(DIALOG=False)

        # won't work on a case-insensitive file system and is also really slow (and missing tools on linux)
        self.common_options.add(MAN=False)
        # links from /usr/bin/mail to /usr/bin/Mail won't work on case-insensitve fs
        self.common_options.add(MAIL=False)
        self.common_options.add(SENDMAIL=False)  # libexec somehow won't compile

        # self.common_options.add(AMD=False)  # for some reason nfd_prot.h is missing (probably wrong bootstrap tool)

        self.common_options.add(RESCUE=False)  # needs crunchgen

        # self.common_options.add(AT=False)  # needs static_pam

        # TODO: remove this
        self.common_options.add(NO_SHARE=None)

        # We don't want separate .debug for now
        self.common_options.add(DEBUG_FILES=False)

        if self.target_arch == CrossCompileTarget.NATIVE:
            self.add_x86_crossbuildOptions()
        else:
            self.add_mips_crossbuildOptions()

    def prepareFreeBSDCrossEnv(self):
        self.cleanDirectory(self.crossBinDir)

        # From Makefile.inc1:
        # ITOOLS=	[ awk cap_mkdb cat chflags chmod chown cmp cp \
        # date echo egrep find grep id install ${_install-info} \
        # ln make mkdir mtree mv pwd_mkdb \
        # rm sed services_mkdb sh strip sysctl test true uname wc ${_zoneinfo} \
        # ${LOCAL_ITOOLS}
        # TODO: pwd_mkdb, cap_mkdb, services,
        # strip? sysctl?
        # Add links for the ones not installed by freebsd-crossbuild:
        host_tools = [
            # basic commands
            "basename", "chmod", "chown", "cmp", "cp", "date", "dirname", "echo", "env",
            "id", "ln", "mkdir", "mv", "rm", "ls", "tee",
            "tr", "true", "uname", "wc", "sleep",
            "hostname", "patch", "which",
            # compiler and make
            "cc", "cpp", "c++", "gperf", "m4",  # compiler tools
            "lorder", "join",  # linking libraries
            "bmake", "nice",  # calling make
            "gzip",  # needed to generate some stuff
            "git",  # to check for updates
            "touch", "realpath", "head",  # used by kernel build scripts
            "python3",  # for the fake sysctl wrapper
            # "asn1_compile",  # kerberos stuff
            "fmt",  # needed by latest freebsd
            "bzip2", "dd",  # needed by bootloader
        ]

        searchpath = self.config.dollarPathWithOtherTools
        if IS_MAC:
            host_tools += ["chflags"]  # missing on linux
            host_tools += ["gobjdump", "gobjcopy", "bsdwhatis"]
            searchpath = "/usr/local/opt/heimdal/libexec/heimdal/:/usr/local/opt/m4/bin" + os.pathsep + searchpath
        else:
            host_tools += ["objcopy", "objdump"]
            host_tools += ["lesspipe", "dircolors"]
            # create a fake chflags for linux
            self.writeFile(self.crossBinDir / "chflags", """#!/usr/bin/env python3
import sys
print("NOOP chflags:", sys.argv, file=sys.stderr)
""", mode=0o755, overwrite=True)

        for tool in host_tools:
            fullpath = shutil.which(tool, path=searchpath)
            if not fullpath:
                fatalError("Missing", tool, "binary")
            self.createSymlink(Path(fullpath), self.crossBinDir / tool, relative=False)
        # make installworld expects make as bmake
        self.createSymlink(self.crossBinDir / "bmake", self.crossBinDir / "make", relative=True)
        # create symlinks for the tools installed by freebsd-crosstools
        crossTools = "awk cat compile_et config file2c find install makefs mtree rpcgen sed lex yacc".split()
        crossTools += "mktemp tsort expr gencat mandoc gencat pwd_mkdb services_mkdb cap_mkdb".split()
        crossTools += "test [ sysctl makewhatis rmdir unifdef gensnmptree".split()
        crossTools += "sort grep egrep fgrep rgrep zgrep zegrep zfgrep xargs".split()
        crossTools += ["uuencode", "uudecode"]  # needed by x86 kernel
        # TODO: freebsd version of sh?
        for tool in crossTools:
            assert not tool in host_tools, tool + " should not be linked from host"
            fullpath = Path(self.config.otherToolsDir, "bin/freebsd-" + tool)
            if not fullpath.is_file():
                fatalError(tool, "binary is missing!")
            self.createSymlink(Path(fullpath), self.crossBinDir / tool, relative=False)

        # Use bash as sh (should be quicker with the builtins)
        shell = "bash"
        self.createSymlink(Path(shutil.which(shell)), self.crossBinDir / "sh", relative=False)

        self.common_options.env_vars["AWK"] = self.crossBinDir / "awk"

    def process(self):
        if not IS_FREEBSD:
            if not self.crossbuild:
                statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need"
                             " to scp the required files from another server (see --frebsd-build-server options)")
                return
            else:
                self.prepareFreeBSDCrossEnv()
        # remove any environment variables that could interfere with bmake running
        for k, v in os.environ.copy().items():
            if k in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKE_TERMERR", "MAKE_TERMOUT"):
                os.unsetenv(k)
                del os.environ[k]
        if self.config.buildenv:
            runCmd([self.makeCommand] + self.buildworldArgs + ["buildenv"], env=self.common_options.env_vars,
                   cwd=self.sourceDir)
        else:
            super().process()


class BuildFreeBSDForMIPS(_BuildFreeBSD):
    projectName = "freebsd-mips"
    target_arch = CrossCompileTarget.MIPS
    kernelConfig = "MALTA64"
    defaultInstallDir = ComputedDefaultValue(
        function=lambda config, cls: config.outputRoot / "freebsd-mips",
        asString="$INSTALL_ROOT/freebsd-mips")


class BuildFreeBSDForX86(_BuildFreeBSD):
    projectName = "freebsd-x86"
    target_arch = CrossCompileTarget.NATIVE
    defaultInstallDir = ComputedDefaultValue(
        function=lambda config, cls: config.outputRoot / "freebsd-x86",
        asString="$INSTALL_ROOT/freebsd-x86")
    kernelConfig = "GENERIC"


class BuildCHERIBSD(_BuildFreeBSD):
    projectName = "cheribsd"
    target = "cheribsd-without-sysroot"
    repository = "https://github.com/CTSRD-CHERI/cheribsd.git"
    defaultInstallDir = lambda config, cls: config.outputRoot / ("rootfs" + config.cheriBitsStr)
    appendCheriBitsToBuildDir = True
    defaultBuildDir = lambda config, cls: config.buildRoot / ("cheribsd-obj-" + config.cheriBitsStr)
    target_arch = CrossCompileTarget.CHERI

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(
            buildKernelWithClang=True,
            makeOptionsShortName="-cheribsd-make-options",
            installDirectoryHelp="Install directory for CheriBSD root file system (default: "
                                 "<OUTPUT>/rootfs256 or <OUTPUT>/rootfs128 depending on --cheri-bits)")
        # TODO: separate options for kernel/install?
        cls.kernelConfig = cls.addConfigOption("kernel-config", default=defaultKernelConfig, kind=str,
                                               metavar="CONFIG", shortname="-kernconf", showHelp=True,
                                               help="The kernel configuration to use for `make buildkernel` (default: "
                                                    "CHERI_MALTA64 or CHERI128_MALTA64 depending on --cheri-bits)")

        defaultCheriCC = ComputedDefaultValue(
            function=lambda config, unused: config.sdkDir / "bin/clang",
            asString="${SDK_DIR}/bin/clang")
        cls.cheriCC = cls.addPathOption("cheri-cc", help="Override the compiler used to build CHERI code",
                                        default=defaultCheriCC)

        cls.forceSDKLinker = cls.addBoolOption("force-sdk-linker", help="Let clang use the linker from the installed "
                                               "SDK instead of the one built in the bootstrap process. WARNING: May "
                                               "cause unexpected linker errors!")
        cls.buildFpgaKernels = cls.addBoolOption("build-fpga-kernels", showHelp=True,
                                                 help="Also build kernels for the FPGA. They will not be installed so"
                                                      " you need to copy them from the build directory.")
        cls.mipsOnly = cls.addBoolOption("mips-only", showHelp=False,
                                         help="Don't build the CHERI parts of cheribsd, only plain MIPS")
        cls.purecapKernel = cls.addBoolOption("pure-cap-kernel", showHelp=True,
                                              help="Build kernel with pure capability ABI (probably won't work!)")

    def __init__(self, config: CheriConfig):
        self.installAsRoot = os.getuid() == 0
        self.cheriCXX = self.cheriCC.parent / "clang++"
        archBuildFlags = [
            "CHERI=" + config.cheriBitsStr,
            "CHERI_CC=" + str(self.cheriCC),
            "CHERI_CXX=" + str(self.cheriCXX),
            "CHERI_LD=" + str(self.config.sdkBinDir / "ld.lld")
        ]
        if self.mipsOnly:
            archBuildFlags = [
                "TARGET=mips",
                "TARGET_ARCH=mips64",
                "-DWITHOUT_LIB32"
            ]
            # keep building a cheri kernel even with a mips userspace (mips may be broken...)
            # self.kernelConfig = "MALTA64"
        super().__init__(config, archBuildFlags=archBuildFlags)

    def _removeSchgFlag(self, *paths: "typing.Iterable[str]"):
        for i in paths:
            file = self.installDir / i
            if file.exists():
                runCmd("chflags", "noschg", str(file))

    def _removeOldRootfs(self):
        if not self.config.skipBuildworld:
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
            mipsCC = self.mipsToolchainPath / "bin/clang"
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

    def update(self):
        super().update()
        if not (self.sourceDir / "contrib/cheri-libc++/src").exists():
            runCmd("git", "submodule", "init", cwd=self.sourceDir)
            runCmd("git", "submodule", "update", cwd=self.sourceDir)


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
        if not IS_FREEBSD and not self.remotePath and not BuildCHERIBSD.crossbuild:
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
        # GNU tar doesn't accept --include
        tar_cmd = "bsdtar" if IS_LINUX else "tar"
        # use tar+untar to copy all necessary files listed in metalog to the sysroot dir
        archiveCmd = [tar_cmd, "cf", "-", "--include=./lib/", "--include=./usr/include/",
                      "--include=./usr/lib/", "--include=./usr/libcheri", "--include=./usr/libdata/",
                      # only pack those files that are mentioned in METALOG
                      "@METALOG"]
        printCommand(archiveCmd, cwd=BuildCHERIBSD.rootfsDir(self.config))
        if not self.config.pretend:
            tar_cwd = str(BuildCHERIBSD.rootfsDir(self.config))
            with subprocess.Popen(archiveCmd, stdout=subprocess.PIPE, cwd=tar_cwd) as tar:
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
        if self.config.skipBuildworld:
            statusUpdate("Not building sysroot because --skip-buildworld was passed")
            return
        with self.asyncCleanDirectory(self.config.sdkSysrootDir):
            if IS_FREEBSD or BuildCHERIBSD.crossbuild:
                self.createSysroot()
            else:
                self.copySysrootFromRemoteMachine()
            if (self.config.sdkDir / "sysroot/usr/libcheri/").is_dir():
                # clang++ expects libgcc_eh to exist:
                libgcc_eh = self.config.sdkDir / "sysroot/usr/libcheri/libgcc_eh.a"
                if not libgcc_eh.is_file():
                    warningMessage("CHERI libgcc_eh missing! You should probably update CheriBSD")
                    runCmd("ar", "rc", libgcc_eh)


class BuildCheriBsdAndSysroot(TargetAlias):
    target = "cheribsd"
    dependencies = ["cheribsd-without-sysroot", "cheribsd-sysroot"]
