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
import shlex
import shutil
import subprocess
import sys

from pathlib import Path
from ..project import *
from ...config.loader import ComputedDefaultValue
from ...config.chericonfig import CrossCompileTarget
from ...utils import *


# noinspection PyUnusedLocal
def defaultKernelConfig(config: CheriConfig, project: "BuildCHERIBSD"):
    if project._crossCompileTarget == CrossCompileTarget.NATIVE:
        return "GENERIC"
    elif project._crossCompileTarget == CrossCompileTarget.MIPS:
        return "MALTA64"
    # make sure we use a kernel with 128 bit CPU features selected
    # or a purecap kernel is selected
    kernconf_name = "CHERI{bits}{pure}_MALTA64{mfs}"
    cheri_bits = "128" if config.cheriBits == 128 else ""
    cheri_pure = "_PURECAP" if project.purecapKernel else ""
    mfs_root_img = "_MFS_ROOT" if project.mfs_root_image else ""
    return kernconf_name.format(bits=cheri_bits, pure=cheri_pure, mfs=mfs_root_img)


class FreeBSDCrossTools(CMakeProject):
    repository = "https://github.com/arichardson/freebsd-crossbuild.git"
    defaultInstallDir = CMakeProject._installToBootstrapTools
    projectName = "freebsd-crossbuild"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.freebsd_source_dir = cls.addPathOption("freebsd-source-directory",
                                                   help="The path to the FreeBSD source tree used for building the"
                                                        " cross tools. Defaults to the CheriBSD source directory")

    def configure(self, **kwargs):
        freebsd_dir = self.freebsd_source_dir if self.freebsd_source_dir else BuildCHERIBSD.getSourceDir(self.config)
        self.add_cmake_options(CHERIBSD_DIR=freebsd_dir, CMAKE_C_COMPILER=self.config.clangPath)
        super().configure()


class _BuildFreeBSD(Project):
    dependencies = ["llvm"]
    repository = "https://github.com/freebsd/freebsd.git"
    make_kind = MakeCommandKind.BsdMake
    doNotAddToTargets = True
    kernelConfig = None  # type: str
    crossbuild = False
    skipBuildworld = False
    baremetal = True  # We are building the full OS so we don't need a sysroot
    # Only CheriBSD can target CHERI, upstream FreeBSD won't work
    supported_architectures = [CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]

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
    def setupConfigOptions(cls, buildKernelWithClang: bool=True, makeOptionsShortname=None, **kwargs):
        super().setupConfigOptions(add_common_cross_options=False, **kwargs)
        cls.subdirOverride = cls.addConfigOption("subdir-with-deps", kind=str, metavar="DIR", showHelp=True,
                                                 help="Only build subdir DIR instead of the full tree.#"
                                                      "This uses the SUBDIR_OVERRIDE mechanism so will build much more"
                                                      "than just that directory")

        cls.explicit_subdirs_only = cls.addConfigOption("subdir", kind=list, metavar="SUBDIRS", showHelp=True,
            help="Only build subdirs SUBDIRS instead of the full tree. Useful for quickly rebuilding an individual"
                 " programs/libraries. If more than one dir is passed they will be processed in order."
                 " Note: This will break if not all dependencies have been built.")

        cls.keepOldRootfs = cls.addBoolOption("keep-old-rootfs",
            help="Don't remove the whole old rootfs directory.  This can speed up installing but may cause strange"
                 " errors so is off by default.")
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
            help="build the kernel with the external toolchain", default=buildKernelWithClang)
        cls.useExternalToolchainForWorld = cls.addBoolOption("use-external-toolchain-for-world", showHelp=True,
            help="build world with the external toolchain", default=True)
        cls.linker_for_world = cls.addConfigOption("linker-for-world", default="lld", choices=["bfd", "lld"],
                                                   help="The linker to use for world")
        cls.linker_for_kernel = cls.addConfigOption("linker-for-kernel", default="lld", choices=["bfd", "lld"],
                                                    help="The linker to use for the kernel")
        cls.addDebugInfoFlag = cls.addBoolOption("debug-info", default=True, showHelp=True,
                                                 help="pass make flags for building with debug info")
        cls.buildTests = cls.addBoolOption("build-tests", help="Build the tests too (-DWITH_TESTS)", showHelp=True)
        cls.auto_obj = cls.addBoolOption("auto-obj", help="Use -DWITH_AUTO_OBJ (experimental)", showHelp=True,
                                         default=True)
        cls.with_manpages = cls.addBoolOption("with-manpages", help="Also install manpages. This is off by default"
                                                                    " since they can just be read from the host.")
        cls.minimal = cls.addBoolOption("minimal", showHelp=True,
            help="Don't build all of FreeBSD, just what is needed for running most CHERI tests/benchmarks")
        cls.fastRebuild = cls.addBoolOption("fast", showHelp=True,
            help="Skip some (usually) unnecessary build steps to speed up rebuilds")
        if IS_FREEBSD:
            cls.crossbuild = False
        elif is_jenkins_build():
            cls.crossbuild = True
        else:
            cls.crossbuild = cls.addBoolOption("crossbuild", help="Try to compile FreeBSD on non-FreeBSD machines")

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
        elif line.startswith(b"[Creating objdir") or line.startswith(b"[Creating nested objdir"):
            return  # ignore the WITH_AUTO_OBJ messages
        else:
            self._showLineStdoutFilter(line)

    def __init__(self, config: CheriConfig, archBuildFlags: dict = None):
        super().__init__(config)
        if archBuildFlags is None:
            if self._crossCompileTarget == CrossCompileTarget.MIPS:
                # The following is broken: (https://github.com/CTSRD-CHERI/cheribsd/issues/102)
                # "CPUTYPE=mips64",  # mipsfpu for hardware float
                archBuildFlags = {"TARGET": "mips", "TARGET_ARCH": config.mips_float_abi.freebsd_target_arch()}
            elif self._crossCompileTarget == CrossCompileTarget.NATIVE:
                archBuildFlags = {"TARGET": "amd64"}
            else:
                assert False, "This should not be reached!"
        self.cross_toolchain_config = MakeOptions(MakeCommandKind.BsdMake, self)
        self.make_args.set(**archBuildFlags)
        self.make_args.env_vars = {"MAKEOBJDIRPREFIX": str(self.buildDir)}
        # TODO: once we have merged the latest upstream changes use MAKEOBJDIR instead to get a more sane hierarchy
        # self.common_options.env_vars = {"MAKEOBJDIR": str(self.buildDir)}
        self.make_args.set(
            DB_FROM_SRC=True,  # don't use the system passwd file
            # NO_WERROR=True,  # make sure we don't fail if clang introduces a new warning
            NO_CLEAN=True,  # don't clean, we have the --clean flag for that
            NO_ROOT=True,  # use this even if current user is root, as without it the METALOG file is not created
            WITHOUT_GDB=True,
        )
        if self.crossbuild:
            self.crossBinDir = self.config.outputRoot / "freebsd-cross/bin"
            self.addCrossBuildOptions()
            self.useExternalToolchainForWorld = True
            self.useExternalToolchainForKernel = True

        # external toolchain options:
        self.externalToolchainCompiler = None
        self._setup_cross_toolchain_config()

        if self.addDebugInfoFlag:
            self.make_args.set(DEBUG_FLAGS="-g")
            # Don't split the debug info from the binary, just keep it as part of the binary
            # This means we can just scp the file over to a cheribsd instace, run gdb and get symbols and sources.
            self.make_args.set_with_options(DEBUG_FILES=False)

        # tests off by default because they take a long time and often seems to break
        # the creation of disk-image (METALOG is invalid)
        self.make_args.set_with_options(TESTS=self.buildTests)

        # only build manpages by default
        self.make_args.set_with_options(MAN=self.with_manpages)

        if self.minimal:
            self.make_args.set_with_options(MAN=False, KERBEROS=False, SVN=False, SVNLITE=False, MAIL=False,
                                            SENDMAIL=False, EXAMPLES=False, LOCALES=False, NLS=False, CDDL=False)

        # doesn't appear to work for buildkernel
        # if self.auto_obj:
        #     # seems like it should speed up the build significantly
        #     self.common_options.add(AUTO_OBJ=True)

        if not self.config.verbose and not self.config.quiet:
            # By default we only want to print the status updates -> use make -s so we have to do less filtering
            self.make_args.add_flags("-s")

        # build only part of the tree
        if self.subdirOverride:
            self.make_args.set(SUBDIR_OVERRIDE=self.subdirOverride)

        # If WITH_LD_IS_LLD is set (e.g. by reading src.conf) the symlink ld -> ld.bfd in $BUILD_DIR/tmp/ won't be
        # created and the build system will then fall back to using /usr/bin/ld which won't work!
        self.make_args.set_with_options(LLD_IS_LD=False)

        self.destdir = self.installDir
        self.installPrefix = Path("/")
        self.kernelToolchainAlreadyBuilt = False
        for option in self.makeOptions:
            if "=" in option:
                args = {option.split("=")[0]: option.split("=")[1]}
                self.make_args.set(**args)
            else:
                self.make_args.add_flags(option)

    def _setup_cross_toolchain_config(self):
        self.cross_toolchain_config.set_with_options(
            GCC=False, CLANG=False,  # Take a long time and not needed
            GCC_BOOTSTRAP=False, CLANG_BOOTSTRAP=False,  # not needed as we have a compiler
            LLD_BOOTSTRAP=False,  # and also a linker
            LIB32=False,  # takes a long time and not needed
        )

        # self.cross_toolchain_config.add(CROSS_COMPILER=Falses) # This sets too much, we want elftoolchain and binutils

        if self._crossCompileTarget == CrossCompileTarget.NATIVE:
            cross_prefix = str(self.config.sdkBinDir) + "/"  # needs to end with / for concatenation
            # target_flags = " -fuse-ld=lld -Wno-error=unused-command-line-argument -Wno-unused-command-line-argument"
            target_flags = ""
            self.useExternalToolchainForWorld = True
            self.useExternalToolchainForKernel = True
            self.linker_for_kernel = "lld"  # bfd won't work here
            self.linker_for_world = "lld"
            # DONT SET XAS!!! It prevents bfd from being built
            # self.cross_toolchain_config.set(XAS="/usr/bin/as")
        elif self.mipsToolchainPath:
            cross_prefix = str(self.mipsToolchainPath / "bin") + "/"
            target_flags = " -integrated-as -fcolor-diagnostics -mcpu=mips4"
            # for some reason this is not inferred....
            # if self.crossbuild:
            #     # For some reason STRINGS is not set
            #     self.cross_toolchain_config.set(STRINGS="strings")
            self.cross_toolchain_config.set_with_options(RESCUE=False,  # Won't compile with CHERI clang yet
                                                         BOOT=False)  # bootloaders won't link with LLD yet
            # DONT SET XAS!!! It prevents bfd from being built
            # self.cross_toolchain_config.set(XAS=cross_prefix + "clang " + target_flags)
        else:
            fatalError("Invalid state, should have a cross env")
            sys.exit(1)

        self.externalToolchainCompiler = Path(cross_prefix + "clang")
        # TODO: should I be setting this in the environment instead?
        self.cross_toolchain_config.set_env(
            XCC=cross_prefix + "clang",
            XCXX=cross_prefix + "clang++",
            XCPP=cross_prefix + "clang-cpp",
            X_COMPILER_TYPE="clang",  # This is needed otherwise the build assumes it should build with $CC
            XOBJDUMP=cross_prefix + "llvm-objdump",
            OBJDUMP=cross_prefix + "llvm-objdump",
        )
        if self.linker_for_world == "bfd":
            # self.cross_toolchain_config.set_env(XLDFLAGS="-fuse-ld=bfd")
            target_flags += " -fuse-ld=bfd -Qunused-arguments"
        else:
            assert self.linker_for_world == "lld"
            # TODO: we should have a better way of passing linker flags than adding them to XCFLAGS
            linker_flags = "-fuse-ld=lld -Qunused-arguments"
            # self.cross_toolchain_config.set_env(XLDFLAGS=linker_flags)
            target_flags += " " + linker_flags
            # Don't set XLD when using bfd since it will pick up ld.bfd from the build directory
            self.cross_toolchain_config.set_env(XLD=cross_prefix + "ld.lld"),

        if self.linker_for_kernel == "lld" and self.linker_for_world == "lld":
            self.cross_toolchain_config.set_with_options(BINUTILS_BOOTSTRAP=False)

        if target_flags:
            self.cross_toolchain_config.set_env(XCFLAGS=target_flags)

    @property
    def buildworldArgs(self) -> MakeOptions:
        result = self.make_args.copy()
        # FIXME: once it works for buildkernel remove here
        if self.auto_obj:
            result.set_with_options(AUTO_OBJ=True)
        if self.useExternalToolchainForWorld:
            if not self.externalToolchainCompiler.exists():
                fatalError("Requested build of world with external toolchain, but", self.externalToolchainCompiler,
                           "doesn't exist!")
            result.update(self.cross_toolchain_config)
        return result

    def kernelMakeArgsForConfig(self, kernconf: str) -> MakeOptions:
        kernel_options = self.make_args.copy()
        kernel_options.set_with_options(AUTO_OBJ=False)  # TODO: remove this
        if self.useExternalToolchainForKernel:
            if not self.externalToolchainCompiler.exists():
                fatalError("Requested build of kernel with external toolchain, but", self.externalToolchainCompiler,
                           "doesn't exist!")
            # We can't use LLD for the kernel yet but there is a flag to experiment with it
            if self._crossCompileTarget == CrossCompileTarget.NATIVE:
                cross_prefix = str(self.config.sdkBinDir) + "/"
            else:
                cross_prefix = str(self.mipsToolchainPath / "bin/mips64-unknown-freebsd-")

            kernel_options.update(self.cross_toolchain_config)
            fuse_ld_flag = "-fuse-ld=" + self.linker_for_kernel
            if self.linker_for_kernel == "lld":
                fuse_ld_flag += " -Wl,--process-cap-relocs"
            linker = cross_prefix + "ld." + self.linker_for_kernel
            kernel_options.remove_var("LDFLAGS")
            kernel_options.set(LD=linker, XLD=linker, HACK_EXTRA_FLAGS="-shared " + fuse_ld_flag,
                               TRAMP_LDFLAGS=fuse_ld_flag)
            kernel_options.set_env(LDFLAGS=fuse_ld_flag, XLDFLAGS=fuse_ld_flag)
        if self.crossbuild:
            kernel_options.set_with_options(KERNEL_TRAMPOLINE=False)
            kernel_options.remove_var("DEBUG")
            kernel_options.remove_all(lambda s: s.startswith("DEBUG"))
            kernel_options.set(INSTALL_NODEBUG=True)
        kernel_options.set(KERNCONF=kernconf)
        return kernel_options

    def runMake(self, makeTarget="", *, options: MakeOptions = None, parallel=True, **kwargs):
        # make behaves differently with -j1 and not j flags -> remove the j flag if j1 is requested
        if parallel and self.config.makeJobs == 1:
            parallel = False
        super().runMake(makeTarget, options=options, cwd=self.sourceDir, parallel=parallel, **kwargs)

    def clean(self) -> ThreadJoiner:
        root_builddir = self.objdir
        if self.config.skipBuildworld:
            kernel_dir = self.kernel_objdir(self.kernelConfig)
            print(kernel_dir)
            if kernel_dir and kernel_dir.parent.exists():
                builddir = kernel_dir
            else:
                warningMessage("Do not know the full path to the kernel build directory, will clean the whole tree!")
                builddir = root_builddir
        else:
            builddir = root_builddir
        if builddir.exists() and self.buildDir.exists():
            assert not os.path.relpath(str(builddir.resolve()), str(self.buildDir.resolve())).startswith(".."), builddir
        return self.asyncCleanDirectory(builddir)

    def _buildkernel(self, kernconf: str, mfs_root_image: Path = None):
        kernelMakeArgs = self.kernelMakeArgsForConfig(kernconf)
        if mfs_root_image:
            kernelMakeArgs.set(MFS_IMAGE=mfs_root_image)
            if "MFS_ROOT" not in kernconf:
                warningMessage("Attempting to build an MFS_ROOT kernel but kernel config name sounds wrong")
        # needKernelToolchain = not self.useExternalToolchainForKernel
        dontNeedKernelToolchain = self.useExternalToolchainForKernel and self.linker_for_kernel == "lld"
        if self.crossbuild:
            dontNeedKernelToolchain = True
        if not dontNeedKernelToolchain and not self.kernelToolchainAlreadyBuilt:
            # we need to build GCC to build the kernel:
            kernel_toolchain_opts = self.make_args.copy()
            kernel_toolchain_opts.set_with_options(LLD_BOOTSTRAP=False, CLANG=False, CLANG_BOOTSTRAP=False)
            kernel_toolchain_opts.set_with_options(GCC_BOOTSTRAP=self.useExternalToolchainForKernel)
            if self.auto_obj:
                kernel_toolchain_opts.set_with_options(AUTO_OBJ=True)
            self.runMake("kernel-toolchain", options=kernel_toolchain_opts)
            self.kernelToolchainAlreadyBuilt = True
        self.runMake("buildkernel", options=kernelMakeArgs,
                     compilationDbName="compile_commands_" + self.kernelConfig + ".json")

    def _installkernel(self, kernconf):
        # don't use multiple jobs here
        install_kernel_args = self.kernelMakeArgsForConfig(kernconf)
        install_kernel_args.env_vars.update(self.makeInstallEnv)
        # Also install all other kernels that were potentially built
        install_kernel_args.set(NO_INSTALLEXTRAKERNELS="no")
        self.runMake("installkernel", options=install_kernel_args, parallel=False)

    @property
    def jflag(self) -> list:
        return [self.config.makeJFlag] if self.config.makeJobs > 1 else []

    def compile(self, mfs_root_image: Path=None, **kwargs):
        # The build seems to behave differently when -j1 is passed (it still complains about parallel make failures)
        # so just omit the flag here if the user passes -j1 on the command line
        build_args = self.buildworldArgs
        if self.config.verbose:
            self.runMake("showconfig", options=build_args)
        if not self.config.skipBuildworld:
            if self.fastRebuild:
                build_args.set(WORLDFAST=True)
            self.runMake("buildworld", options=build_args)
        if not self.subdirOverride:
            self._buildkernel(kernconf=self.kernelConfig, mfs_root_image=mfs_root_image)

    def _removeOldRootfs(self):
        assert self.config.clean or not self.keepOldRootfs
        if self.config.skipBuildworld:
            self.makedirs(self.installDir)
        else:
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # We have to keep the rootfs directory in case it has been NFS mounted
            self.cleanDirectory(self.installDir, keepRoot=True)

    def _query_buildenv_path(self, args, var):
        try:
            bw_flags = args.all_commandline_args + ["buildenv",
                                                    "BUILDENV_SHELL=" + str(self.make_args.command) + " -V " + var]
            if self.crossbuild:
                bw_flags.append("PATH=" + os.getenv("PATH"))
            if not self.sourceDir.exists():
                assert self.config.pretend, "This should only happen when running in a test environment"
                return None
            # https://github.com/freebsd/freebsd/commit/1edb3ba87657e28b017dffbdc3d0b3a32999d933
            cmd = runCmd([self.make_args.command] + bw_flags, env=args.env_vars, cwd=self.sourceDir,
                         runInPretendMode=True, captureOutput=True)
            lines = cmd.stdout.strip().split(b"\n")
            last_line = lines[-1].decode("utf-8").strip()
            if last_line.startswith("/") and cmd.returncode == 0:
                self.verbose_print("BUILDENV var", var, "was", last_line)
                return Path(last_line)
            warningMessage("Failed to query", var, "-- output was:", lines)
            return None
        except subprocess.CalledProcessError as e:
            warningMessage("Could not query make variable", var, "for buildworld root objdir: ", e)
            return None

    @property
    def objdir(self):
        # TODO use https://github.com/pydanny/cached-property ?
        objdir = self._query_buildenv_path(self.buildworldArgs, ".OBJDIR")
        if not objdir or objdir == Path():
            # just clean the whole directory instead
            warningMessage("Could not infer buildworld root objdir")
            return self.buildDir
        return objdir

    def kernel_objdir(self, config):
        result = self.objdir / "sys"
        if result.exists():
            return Path(result) / config
        warningMessage("Could not infer buildkernel objdir")
        return None

    @property
    def installworld_args(self):
        result = self.buildworldArgs
        result.env_vars.update(self.makeInstallEnv)
        return result

    def install(self, all_kernel_configs: str=None, **kwargs):
        if self.subdirOverride:
            statusUpdate("Skipping install step because SUBDIR_OVERRIDE was set")
            return
        # keeping the old rootfs directory prior to install can sometimes cause the build to fail so delete by default
        if self.config.clean or not self.keepOldRootfs:
            self._removeOldRootfs()

        if not all_kernel_configs:
            all_kernel_configs = self.kernelConfig
        self._installkernel(kernconf=all_kernel_configs)

        if not self.config.skipBuildworld:
            install_world_args = self.installworld_args
            # https://github.com/CTSRD-CHERI/cheribsd/issues/220
            # installworld reads compiler metadata which was written by kernel-toolchain which means that
            # it will attempt to install libc++ because compiler for kernel is now clang and not GCC
            # as a workaround force writing the compiler metadata by invoking the _compiler-metadata target
            try:
                self.runMake("_build-metadata", options=install_world_args)
            except subprocess.CalledProcessError:
                try:
                    # support building old versions of cheribsd before _compiler-metadata was renamed to _build-metadata
                    self.runMake("_compiler-metadata", options=install_world_args)
                except subprocess.CalledProcessError:
                    warningMessage("Failed to run either target _compiler-metadata or "
                                   "_build_metadata, build system has changed!")
            self.runMake("installworld", options=install_world_args)
            self.runMake("distribution", options=install_world_args)

    def addCrossBuildOptions(self):
        # we also need to ensure that our SDK build tools are being picked up first
        # build_path = str(self.config.sdkBinDir) + ":" + str(self.crossBinDir)
        # self.make_args.env_vars["PATH"] = build_path

        # Tell glibc functions to be POSIX compatible
        # Would be ideal, but it seems like there is too much that depends on non-posix flags
        # self.common_options.env_vars["POSIXLY_CORRECT"] = "1"
        # self.make_args.set(PATH=build_path)
        # building without an external toolchain won't work:
        self.mipsToolchainPath = self.config.sdkDir
        self.make_args.set_with_options(ELFTOOLCHAIN_BOOTSTRAP=True)
        # use clang for the build tools:
        self.make_args.set_env(CC=str(self.config.clangPath), CXX=str(self.config.clangPlusPlusPath))

        # we don't build elftoolchain during buildworld so for the kernel we need to set these variables
        self.make_args.set_env(XOBJDUMP=self.config.sdkBinDir / "llvm-objdump")
        # TODO: use llvm-objcopy?
        self.make_args.set_env(OBJCOPY=self.config.sdkBinDir / "objcopy")
        # This is not actually the path to the strip binary but rather a flag to install
        # self.make_args.env_vars["STRIP"] = self.config.sdkBinDir / "strip"

        # don't build all the bootstrap tools (just pretend we are running freebsd 42):
        # self.make_args.env_vars["OSRELDATE"] = "4204345"

        # localedef is really hard to crosscompile -> skip this for now
        self.make_args.set_with_options(LOCALES=False)

        # These all seem to work now
        # self.make_args.set_with_options(SYSCONS=False, USB=False, GPL_DTC=False)
        # self.make_args.set_with_options(CDDL=False)  # lots of bootstrap tools issues

        self.make_args.set_with_options(BINUTILS=True, CLANG=False, GCC=False, GDB=False, LLD=False, LLDB=False)

        # TODO: build these for zoneinfo setup
        # "zic", "tzsetup"
        # self.make_args.set_with_options(ZONEINFO=False)

        # self.make_args.set_with_options(KERBEROS=False)  # needs some more work with bootstrap tools

        # won't work with CHERI
        # self.common_options.add(DIALOG=False)

        # won't work on a case-insensitive file system and is also really slow (and missing tools on linux)
        self.make_args.set_with_options(MAN=False)
        # links from /usr/bin/mail to /usr/bin/Mail won't work on case-insensitve fs
        self.make_args.set_with_options(MAIL=False)
        self.make_args.set_with_options(SENDMAIL=False)  # libexec somehow won't compile

        self.make_args.set_with_options(VT=False)

        # We don't want separate .debug for now
        self.make_args.set_with_options(DEBUG_FILES=False)

        if self._crossCompileTarget == CrossCompileTarget.NATIVE:
            cross_binutils_prefix = str(self.config.sdkBinDir) + "/"
            self.make_args.set_with_options(BHYVE=False,
                                            # seems to be missing some include paths which appears to work on freebsd
                                            CTF=False)  # can't crossbuild ctfconvert yet
            self.make_args.set_with_options(BOOT=True)
        else:
            cross_binutils_prefix = str(self.config.sdkBinDir) + "/mips64-unknown-freebsd-"
            self.make_args.set_with_options(BOOT=False)
        # This should no longer be necessary since we can bootstrap elftoolchain
        # self.make_args.set_env(CROSS_BINUTILS_PREFIX=cross_binutils_prefix)

    def prepareFreeBSDCrossEnv(self):
        assert False, "This is no longer needed"
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
            host_tools += ["bsdwhatis"]
            searchpath = "/usr/local/opt/heimdal/libexec/heimdal/:/usr/local/opt/m4/bin" + os.pathsep + searchpath
        else:
            host_tools += ["lesspipe", "dircolors"]
            # create a fake chflags for linux
            self.writeFile(self.crossBinDir / "chflags", """#!/usr/bin/env python3
import sys
print("NOOP chflags:", sys.argv, file=sys.stderr)
""", mode=0o755, overwrite=True)

        # We need awk, sed and grep for the initial bootstrap-tools stage, when they get replaced with real ones
        self.temporary_crossbuild_tools = ["awk", "sed", "grep", "sort", "find", "install", "mtree"]
        for t in self.temporary_crossbuild_tools:
            host_tools.append(t)
        # awk is special because it may also be nawk (e.g. on Ubuntu)
        awk = shutil.which("nawk") or shutil.which("awk") or "awk"
        self.createSymlink(awk, self.crossBinDir / "awk", relative=False)

        for tool in host_tools:
            fullpath = shutil.which(tool, path=searchpath)
            if not fullpath:
                fatalError("Missing", tool, "binary")
                continue
            self.createSymlink(Path(fullpath), self.crossBinDir / tool, relative=False)

        self.createSymlink(self.config.sdkBinDir / "objcopy", self.crossBinDir / "objcopy", relative=False)
        self.createSymlink(self.config.sdkBinDir / "llvm-objdump", self.crossBinDir / "objdump", relative=False)
        self.createSymlink(self.config.sdkBinDir / "strip", self.crossBinDir / "strip", relative=False)

        # make installworld expects make as bmake
        self.createSymlink(self.crossBinDir / "bmake", self.crossBinDir / "make", relative=True)
        # create symlinks for the tools installed by freebsd-crosstools
        crossTools = "awk cat compile_et config file2c find install makefs mtree rpcgen sed lex yacc".split()
        crossTools += "mktemp tsort expr gencat mandoc gencat pwd_mkdb services_mkdb cap_mkdb".split()
        crossTools += "test [ sysctl makewhatis rmdir unifdef gensnmptree strfile".split()
        crossTools += "sort grep egrep fgrep rgrep zgrep zegrep zfgrep xargs".split()
        crossTools += ["uuencode", "uudecode"]  # needed by x86 kernel

        # TODO: freebsd version of sh?
        # for tool in crossTools:
        #     assert not tool in host_tools, tool + " should not be linked from host"
        #     fullpath = Path(self.config.otherToolsDir, "bin/freebsd-" + tool)
        #     if not fullpath.is_file():
        #         fatalError(tool, "binary is missing!")
        #     self.createSymlink(Path(fullpath), self.crossBinDir / tool, relative=False)

        # /bin/sh on Ubuntu doesn't like shift without any arguments, let's use bash as bin/sh instead...
        # This is a problem when running ncurses MKfallback.sh
        shell = "bash"
        self.createSymlink(Path(shutil.which(shell)), self.crossBinDir / "sh", relative=False)

        self.make_args.env_vars["AWK"] = self.crossBinDir / "awk"

    def process(self):
        if not IS_FREEBSD:
            if not self.crossbuild:
                statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need"
                             " to scp the required files from another server (see --frebsd-build-server options)")
                return
            # self.prepareFreeBSDCrossEnv()
        # remove any environment variables that could interfere with bmake running
        for k, v in os.environ.copy().items():
            if k in ("MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKE_TERMERR", "MAKE_TERMOUT", "MAKE"):
                os.unsetenv(k)
                del os.environ[k]

        buildenv_target = "buildenv"
        if self._crossCompileTarget == CrossCompileTarget.CHERI and self.config.libcheri_buildenv:
            buildenv_target = "libcheribuildenv"

        if self.explicit_subdirs_only:
            # Allow building a single FreeBSD/CheriBSD directory using the BUILDENV_SHELL trick
            for subdir in self.explicit_subdirs_only:
                args = self.installworld_args
                is_lib = subdir.startswith("lib/") or "/lib/" in subdir or subdir.endswith("/lib")
                make_in_subdir = "make -C \"" + subdir + "\" "
                if self.config.skipInstall:
                    install_cmd = "echo \"  Skipping make install\""
                else:
                    install_cmd = make_in_subdir + "install"
                    # if we are building a library also install to the sysroot so that other targets afterwards use the
                    # updated static lib
                    if is_lib:
                        # Due to all the bmake + shell escaping I need 4 dollars here to get it to expand SYSROOT
                        sysroot_var = sysroot="\"$$$${SYSROOT}\""
                        install_cmd = "if [ -n {sysroot} ]; then {make} install DESTDIR={sysroot}; fi && ".format(
                            make=make_in_subdir, sysroot=sysroot_var) + install_cmd
                colour_diags = "export CLANG_FORCE_COLOR_DIAGNOSTICS=always; " if self.config.clang_colour_diags else ""
                build_cmd = "{colour_diags} {clean} && {build} && {install} && echo \"  Done.\"".format(
                    build=make_in_subdir + "all " + " ".join(self.jflag),
                    clean=make_in_subdir + "clean" if self.config.clean else "echo \"  Skipping make clean\"",
                    install=install_cmd, colour_diags=colour_diags)
                args.set(BUILDENV_SHELL="sh -ex -c '" + build_cmd + "' || exit 1")
                statusUpdate("Building", subdir, "using", buildenv_target, "target")
                runCmd([self.make_args.command] + args.all_commandline_args + [buildenv_target], env=args.env_vars,
                       cwd=self.sourceDir)
                # If we are building a library we want to build both the CHERI and the mips version (unless the
                # user explicitly specified --libcheri-buildenv)
                if self._crossCompileTarget == CrossCompileTarget.CHERI and is_lib and buildenv_target != "libcheribuildenv":
                    statusUpdate("Building", subdir, "using libcheribuildenv target")
                    runCmd([self.make_args.command] + args.all_commandline_args + ["libcheribuildenv"], env=args.env_vars,
                           cwd=self.sourceDir)

        elif self.config.buildenv or self.config.libcheri_buildenv:
            args = self.buildworldArgs
            args.remove_flag("-s")  # buildenv should not be silent
            runCmd([self.make_args.command] + args.all_commandline_args + [buildenv_target], env=args.env_vars,
                   cwd=self.sourceDir)
        else:
            super().process()


# TODO: remove these two
class BuildFreeBSDForMIPS(_BuildFreeBSD):
    projectName = "freebsd-mips"
    target = "freebsd-mips"
    _crossCompileTarget = CrossCompileTarget.MIPS
    supported_architectures = []  # Don't add any other configs
    kernelConfig = "MALTA64"
    defaultInstallDir = ComputedDefaultValue(
        function=lambda config, cls: config.outputRoot / "freebsd-mips",
        asString="$INSTALL_ROOT/freebsd-mips")


class BuildFreeBSDForX86(_BuildFreeBSD):
    projectName = "freebsd-x86"
    target = "freebsd-x86"
    _crossCompileTarget = CrossCompileTarget.NATIVE
    supported_architectures = []  # Don't add any other configs
    defaultInstallDir = ComputedDefaultValue(
        function=lambda config, cls: config.outputRoot / "freebsd-x86",
        asString="$INSTALL_ROOT/freebsd-x86")
    kernelConfig = "GENERIC"


# noinspection PyProtectedMember
def cheribsd_install_dir(config: CheriConfig, project: _BuildFreeBSD):
    if project._crossCompileTarget == CrossCompileTarget.CHERI:
        return config.outputRoot / ("rootfs" + config.cheriBitsStr)
    elif project._crossCompileTarget == CrossCompileTarget.MIPS:
        return config.outputRoot / "rootfs-mips"
    else:
        assert project._crossCompileTarget == CrossCompileTarget.NATIVE
        return config.outputRoot / "rootfs-x86"


# noinspection PyProtectedMember
def cheribsd_build_dir(config: CheriConfig, project: _BuildFreeBSD):
    if project._crossCompileTarget == CrossCompileTarget.CHERI:
        # TODO: change this to be the default build dir name
        return config.buildRoot / ("cheribsd-obj-" + config.cheriBitsStr)
    else:
        return project.buildDirForTarget(config, project._crossCompileTarget)

class BuildCHERIBSD(_BuildFreeBSD):
    projectName = "cheribsd"
    target = "cheribsd"
    repository = "https://github.com/CTSRD-CHERI/cheribsd.git"
    defaultInstallDir = cheribsd_install_dir
    appendCheriBitsToBuildDir = True
    defaultBuildDir = cheribsd_build_dir
    _crossCompileTarget = CrossCompileTarget.CHERI
    supported_architectures = [CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(
            buildKernelWithClang=True,
            makeOptionsShortName="-cheribsd-make-options",
            installDirectoryHelp="Install directory for CheriBSD root file system (default: "
                                 "<OUTPUT>/rootfs256 or <OUTPUT>/rootfs128 depending on --cheri-bits)")
        # Avoid duplicate --kerneconf string for cheribsd-native vs cheribsd
        kernconf_shortname = "-kernconf" if cls._crossCompileTarget == CrossCompileTarget.CHERI else None
        cls.kernelConfig = cls.addConfigOption("kernel-config", default=defaultKernelConfig, kind=str,
           metavar="CONFIG", shortname=kernconf_shortname, showHelp=True,
           help="The kernel configuration to use for `make buildkernel` (default: CHERI_MALTA64 or CHERI128_MALTA64"
                " depending on --cheri-bits)")

        defaultCheriCC = ComputedDefaultValue(
            function=lambda config, unused: config.sdkDir / "bin/clang",
            asString="${SDK_DIR}/bin/clang")
        cls.cheriCC = cls.addPathOption("cheri-cc", help="Override the compiler used to build CHERI code",
                                        default=defaultCheriCC)

        cls.buildFpgaKernels = cls.addBoolOption("build-fpga-kernels", showHelp=True,
                                                 help="Also build kernels for the FPGA. They will not be installed so"
                                                      " you need to copy them from the build directory.")
        cls.mfs_root_image = cls.addPathOption("mfs-root-image", help="Path to an MFS root image to embed in the"
                                                                      "kernel that will be booted from")
        cls.purecapKernel = cls.addBoolOption("pure-cap-kernel", showHelp=True,
                                              help="Build kernel with pure capability ABI (probably won't work!)")

    @property
    def mipsOnly(self) -> bool: # Compat
        return self._crossCompileTarget == CrossCompileTarget.MIPS

    def __init__(self, config: CheriConfig):
        self.installAsRoot = os.getuid() == 0
        self.cheriCXX = self.cheriCC.parent / "clang++"
        archBuildFlags = {
            "CHERI": config.cheriBitsStr,
            "CHERI_CC": str(self.cheriCC),
            "CHERI_CXX": str(self.cheriCXX),
            "CHERI_LD": str(config.sdkBinDir / "ld.lld"),
            "TARGET": "mips",
            "TARGET_ARCH": config.mips_float_abi.freebsd_target_arch()
        }
        if self.mipsOnly:
            archBuildFlags = {"TARGET": "mips",
                              "TARGET_ARCH": config.mips_float_abi.freebsd_target_arch(),
                              "WITHOUT_LIB32": True}
            # keep building a cheri kernel even with a mips userspace (mips may be broken...)
            # self.kernelConfig = "MALTA64"
        super().__init__(config, archBuildFlags=archBuildFlags)
        if self.config.cheri_cap_table_abi:
            self.cross_toolchain_config.set(CHERI_USE_CAP_TABLE=self.config.cheri_cap_table_abi)
        self.extra_kernels = []
        self.extra_kernels_with_mfs = []
        if self.buildFpgaKernels:
            if self.mipsOnly:
                prefix = "BERI_DE4_"
            elif self.config.cheriBits == 128:
                prefix = "CHERI128_DE4_"
            elif self.config.cheriBits == 256:
                prefix = "CHERI_DE4_"
            else:
                prefix = "INVALID_KERNCONF_"
                fatalError("Invalid CHERI BITS")
            # TODO: build the benchmark kernels? TODO: remove the MDROOT option?
            for conf in ("USBROOT", "SDROOT", "NFSROOT", "MDROOT"):
                self.extra_kernels.append(prefix + conf)
            if self.mfs_root_image:
                self.extra_kernels_with_mfs.append(prefix + "MFS_ROOT")

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

    def compile(self, **kwargs):
        if not self.cheriCC.is_file():
            fatalError("CHERI CC does not exist: ", self.cheriCC)
        if not self.cheriCXX.is_file():
            fatalError("CHERI CXX does not exist: ", self.cheriCXX)
        if self.mipsToolchainPath:
            mipsCC = self.mipsToolchainPath / "bin/clang"
            if not mipsCC.is_file():
                fatalError("MIPS toolchain specified but", mipsCC, "is missing.")
        super().compile(mfs_root_image=self.mfs_root_image, **kwargs)
        # We could also just pass multiple values in KERNCONF to build all those kernels. However, if MFS_ROOT is set
        # that will apply to all those kernels and embed the rootfs even if not needed
        for i in self.extra_kernels:
            self._buildkernel(kernconf=i)
        for i in self.extra_kernels_with_mfs:
            self._buildkernel(kernconf=i, mfs_root_image=self.mfs_root_image)

    def install(self, **kwargs):
        all_kernel_configs = self.kernelConfig
        # If we build the FPGA kernels also install them into boot:
        for i in self.extra_kernels + self.extra_kernels_with_mfs:
            all_kernel_configs += " " + i
        super().install(all_kernel_configs=all_kernel_configs, **kwargs)

    def update(self):
        super().update()
        if not (self.sourceDir / "contrib/cheri-libc++/src").exists():
            runCmd("git", "submodule", "init", cwd=self.sourceDir)
            runCmd("git", "submodule", "update", cwd=self.sourceDir)


class BuildCheriBsdSysroot(SimpleProject):
    projectName = "cheribsd-sysroot"
    dependencies = ["cheribsd"]

    rootfs_source_class = BuildCHERIBSD  # type: BuildCHERIBSD

    def fixSymlinks(self):
        # copied from the build_sdk.sh script
        # TODO: we could do this in python as well, but this method works
        fixlinksSrc = includeLocalFile("files/fixlinks.c")
        runCmd("cc", "-x", "c", "-", "-o", self.config.sdkDir / "bin/fixlinks", input=fixlinksSrc)
        runCmd(self.config.sdkDir / "bin/fixlinks", cwd=self.config.sdkSysrootDir / "usr/lib")

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        if not IS_FREEBSD and not self.remotePath and not self.rootfs_source_class.get_instance(self.config).crossbuild:
            configOption = "'--" + self.target + "/" + "remote-sdk-path'"
            fatalError("Path to the remote SDK is not set, option", configOption, "must be set to a path that "
                       "scp understands (e.g. vica:~foo/cheri/output/sdk)")
            if not self.config.pretend:
                sys.exit("Cannot continue...")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        if not IS_FREEBSD:
            cls.remotePath = cls.addConfigOption("remote-sdk-path", showHelp=True, metavar="PATH", help="The path to "
                                                 "the CHERI SDK on the remote FreeBSD machine (e.g. "
                                                 "vica:~foo/cheri/output/sdk)")

    def copySysrootFromRemoteMachine(self):
        statusUpdate("Cannot build disk image on non-FreeBSD systems, will attempt to copy instead.")
        if not self.remotePath:
            fatalError("Missing remote SDK path: Please set --cheribsd-sysroot/remote-sdk-path (or --cheribsd/crossbuild)")
            if self.config.pretend:
                self.remotePath = "someuser@somehose:this/path/does/not/exist"
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
        runCmd("tar", "-czf", self.config.sdkDir / self.config.sysrootArchiveName, self.config.sdkSysrootDir.name,
               cwd=self.config.sdkDir)
        print("Successfully populated sysroot")

    def process(self):
        if self.config.skipBuildworld:
            statusUpdate("Not building sysroot because --skip-buildworld was passed")
            return
        # prepare for a unified SDK that contains sysroot128/sysroot256
        if not self.config.unified_sdk:
            unprefixed_sysroot = self.config.sdkDir / "sysroot"
            if unprefixed_sysroot.is_dir() and not unprefixed_sysroot.is_symlink():
                self.cleanDirectory(unprefixed_sysroot)
                if not self.config.pretend:
                    unprefixed_sysroot.rmdir()
                self.createSymlink(self.config.sdkSysrootDir, unprefixed_sysroot)

        with self.asyncCleanDirectory(self.config.sdkSysrootDir):
            if IS_FREEBSD or self.rootfs_source_class.get_instance(self.config).crossbuild:
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
    target = "cheribsd-with-sysroot"
    dependencies = ["cheribsd", "cheribsd-sysroot"]


class BuildFreeBSDBootstrapTools(Project):
    target = "freebsd-bootstrap-tools"
    projectName = "freebsd-bootstrap-tools"
    repository = "https://github.com/arichardson/cheribsd"
    gitBranch = "crossbuild-bootstrap-tools"
    make_kind = MakeCommandKind.BsdMake
    defaultInstallDir = Project._installToBootstrapTools

    _stdoutFilter = _BuildFreeBSD._stdoutFilter

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.make_args.set_env(MAKEOBJDIRPREFIX=self.buildDir)
        # TODO: fix this
        # self.make_args.set_with_options(CDDL=False)
        self.make_args.set(NO_CLEAN=True)
        if not self.config.verbose:
            self.make_args.add_flags("-s")

    def compile(self, cwd: Path = None):
        "cross-bootstrap-tools-install"
        self.runMake("cross-bootstrap-tools", cwd=self.sourceDir)

    def install(self, **kwargs):
        self.makedirs(self.installDir / "bin")
        self.make_args.set(BOOTSTRAP_TOOLS_DESTDIR=self.installDir)
        self.runMake("cross-bootstrap-tools-install", cwd=self.sourceDir)
        # TODO: symlinks?
        if not IS_FREEBSD:
            self.createSymlink(self.installDir / "bin/freebsd-makefs", self.installDir / "bin/makefs")
