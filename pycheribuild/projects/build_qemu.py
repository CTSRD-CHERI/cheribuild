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

from .project import *
from ..config.loader import ComputedDefaultValue
from ..utils import *


class BuildQEMUBase(AutotoolsProject):
    repository = GitRepository("https://github.com/qemu/qemu.git")
    defaultInstallDir = AutotoolsProject._installToSDK
    # QEMU will not work with BSD make, need GNU make
    make_kind = MakeCommandKind.GnuMake
    doNotAddToTargets = True
    is_sdk_target = True
    skipGitSubmodules = True  # we don't need these
    can_build_with_asan = True
    default_targets = "some-invalid-target"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions()
        cls.debug_info = cls.addBoolOption("debug-info")
        cls.with_sanitizers = cls.addBoolOption("sanitizers", help="Build QEMU with ASAN/UBSAN (very slow)", default=False)

        cls.use_smbd = cls.addBoolOption("use-smbd", showHelp=False, default=True,
                                         help="Don't require SMB support when building QEMU (warning: most --test "
                                              "targets will fail without smbd support)")

        cls.gui = cls.addBoolOption("gui", showHelp=True, default=False,
                                    help="Build a the graphical UI bits for QEMU (SDL,VNC)")
        cls.lto = cls.addBoolOption("use-lto", showHelp=True,
                                    help="Try to build QEMU with link-time optimization if possible", default=True)
        cls.qemu_targets = cls.addConfigOption("targets",
            showHelp=True, help="Build QEMU for the following targets", default=cls.default_targets)

    @classmethod
    def qemu_binary(cls, caller: SimpleProject):
        raise NotImplementedError()

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.addRequiredSystemTool("glibtoolize" if IS_MAC else "libtoolize", homebrew="libtool")
        self.addRequiredSystemTool("autoreconf", homebrew="autoconf")
        self.addRequiredSystemTool("aclocal", homebrew="automake")
        self.addRequiredSystemTool("python2.7", installInstructions="QEMU needs Python 2.7 installed")

        self._addRequiredPkgConfig("pixman-1", homebrew="pixman", zypper="libpixman-1-0-devel", apt="libpixman-1-dev",
                                   freebsd="pixman")
        self._addRequiredPkgConfig("glib-2.0", homebrew="glib", zypper="glib2-devel", apt="libglib2.0-dev",
                                   freebsd="glib")

        # there are some -Wdeprected-declarations, etc. warnings with new libraries/compilers and it builds
        # with -Werror by default but we don't want the build to fail because of that -> add -Wno-error
        self._extraCFlags = "-DCONFIG_DEBUG_TCG=1" if self.debug_info else "-O3"
        self._extraLDFlags = ""
        self._extraCXXFlags = ""
        if shutil.which("pkg-config"):
            glibIncludes = runCmd("pkg-config", "--cflags-only-I", "glib-2.0", captureOutput=True,
                                  print_verbose_only=True, runInPretendMode=True).stdout.decode("utf-8").strip()
            self._extraCFlags += " " + glibIncludes

        # Disable some more unneeded things (we don't usually need the GUI frontends)
        if not self.gui:
            self.configureArgs.extend(["--disable-vnc", "--disable-sdl", "--disable-gtk", "--disable-opengl"])
            if IS_MAC:
                self.configureArgs.append("--disable-cocoa")

        python_path = shutil.which("python2.7") or shutil.which("python2") or ""
        # QEMU needs python 2.7 for building:
        self.configureArgs.append("--python=" + python_path)
        if self.debug_info:
            self.configureArgs.extend(["--enable-debug", "--enable-debug-tcg"])
        else:
            # Try to optimize as much as possible:
            self.configureArgs.extend(["--disable-stack-protector"])

        if self.with_sanitizers:
            self.warning("Option --qemu/sanitizers is deprecated, use --qemu/use-asan instead")
        if self.with_sanitizers or self.use_asan:
            self.configureArgs.append("--enable-sanitizers")
            if self.lto:
                self.info("Disabling LTO for ASAN instrumented builds")
            self.lto = False

        # Having symbol information is useful for debugging and profiling
        self.configureArgs.append("--disable-strip")

        if IS_LINUX:
            # "--enable-libnfs", # version on Ubuntu 14.04 is too old? is it needed?
            # self.configureArgs += ["--enable-kvm", "--enable-linux-aio", "--enable-vte", "--enable-sdl",
            #                        "--with-sdlabi=2.0", "--enable-virtfs"]
            self.configureArgs.extend(["--disable-stack-protector"])  # seems to be broken on some Ubuntu 14.04 systems

        else:
            self.configureArgs.extend(["--disable-linux-aio", "--disable-kvm"])

    def configure(self, **kwargs):
        compiler = self.config.clangPath
        if compiler:
            ccinfo = getCompilerInfo(compiler)
            if ccinfo.compiler == "apple-clang" or (ccinfo.compiler == "clang" and ccinfo.version >= (4, 0, 0)):
                # Turn implicit function declaration into an error -Wimplicit-function-declaration
                self._extraCFlags += " -Werror=implicit-function-declaration"
                # Also make discarding const an error:
                self._extraCFlags += " -Werror=incompatible-pointer-types-discards-qualifiers"
                # silence this warning that comes lots of times (it's fine on x86)
                self._extraCFlags += " -Wno-address-of-packed-member"
                self._extraCFlags += " -Wall -Wextra -Wno-sign-compare -Wno-unused-parameter" \
                                     " -Wno-c11-extensions -Wno-missing-field-initializers"
            if self.lto and self.can_use_lto(ccinfo):
                while True:  # add a loop so I can break early
                    statusUpdate("Compiling with Clang and LLD -> trying to build with LTO enabled")
                    if ccinfo.compiler != "apple-clang":
                        # For non apple-clang compilers we need to use llvm binutils:
                        version_suffix = ""
                        if compiler.name.startswith("clang"):
                            version_suffix = compiler.name[len("clang"):]
                        llvm_ar = ccinfo.get_matching_binutil("llvm-ar")
                        llvm_ranlib = ccinfo.get_matching_binutil("llvm-ranlib")
                        llvm_nm = ccinfo.get_matching_binutil("llvm-nm")
                        lld = ccinfo.get_matching_binutil("ld.lld")
                        # Find lld with the correct version (it must match the version of clang otherwise it breaks!)
                        self._extraLDFlags += " -fuse-ld=" + shlex.quote(str(lld))
                        if not llvm_ar or not llvm_ranlib or not llvm_nm:
                            self.warning("Could not find llvm-{ar,ranlib,nm}" + version_suffix,
                                         "-> disabling LTO (qemu will be a bit slower)")
                            break
                        self.configureEnvironment.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
                        # self.make_args.env_vars.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
                        self.make_args.set(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
                    self._extraCFlags += " -flto=thin"
                    self._extraCXXFlags += " -flto=thin"
                    self._extraLDFlags += " -flto=thin"
                    if self.canUseLLd(ccinfo.path):
                        thinlto_cache_flag = "--thinlto-cache-dir="
                    else:
                        # Apple ld uses a different flag for the thinlto cache dir
                        assert ccinfo.compiler == "apple-clang"
                        thinlto_cache_flag = "-cache_path_lto,"
                    self._extraLDFlags += " -Wl," + thinlto_cache_flag + str(self.buildDir / "thinlto-cache")

                    statusUpdate("Building with LTO -> QEMU should be faster")
                    break
        self._extraCFlags += " -Wall"
        # This would have cought some problems in the past
        self._extraCFlags += " -Werror=return-type"
        if self.use_smbd:
            smbd_path = "/usr/sbin/smbd"
            if IS_FREEBSD:
                smbd_path = "/usr/local/sbin/smbd"
            elif IS_MAC:
                try:
                    prefix = runCmd("brew", "--prefix", "samba", captureOutput=True, runInPretendMode=True,
                                    print_verbose_only=True).stdout.decode("utf-8").strip()
                except subprocess.CalledProcessError:
                    prefix = self.config.otherToolsDir
                smbd_path = Path(prefix, "sbin/smbd")
                print("Guessed samba path", smbd_path)

            if (self.config.otherToolsDir / "sbin/smbd").exists():
                smbd_path = self.config.otherToolsDir / "sbin/smbd"

            self.addRequiredSystemTool(smbd_path, cheribuild_target="samba", freebsd="samba48", apt="samba",
                                       homebrew="samba")

            self.configureArgs.append("--smbd=" + str(smbd_path))
            if not Path(smbd_path).exists():
                if IS_MAC:
                    # QEMU user networking expects a smbd that accepts the same flags and config files as the samba.org
                    # sources but the macos /usr/sbin/smbd is incompatible with that:
                    warningMessage("QEMU usermode samba shares require the samba.org smbd. You will need to build it from "
                                   "source (using `cheribuild.py samba`) since the /usr/sbin/smbd shipped by MacOS is "
                                   "incompatible with QEMU")
                self.fatal("Could not find smbd -> QEMU SMB shares networking will not work",
                           fixitHint="Either install samba using the system package manager or with cheribuild. "
                                     "If you really don't need QEMU host shares you can disable the samba dependency "
                                     "by setting --qemu/no-use-smbd")

        self.configureArgs.extend([
            "--target-list=" + self.qemu_targets,
            "--disable-linux-user",
            "--disable-bsd-user",
            "--disable-xen",
            "--disable-docs",
            "--disable-rdma",
            "--disable-werror",
            "--disable-pie",  # no need to build as PIE (this just slows down QEMU)
            "--extra-cflags=" + self._extraCFlags,
            "--cxx=" + str(self.config.clangPlusPlusPath),
            "--cc=" + str(self.config.clangPath),
        ])
        if self._extraLDFlags:
            self.configureArgs.append("--extra-ldflags=" + self._extraLDFlags.strip())
        if self._extraCXXFlags:
            self.configureArgs.append("--extra-cxxflags=" + self._extraCXXFlags.strip())

        super().configure(**kwargs)

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir() and not self.config.skipUpdate:
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir, print_verbose_only=True)
        if (self.sourceDir / "pixman/pixman").exists():
            warningMessage(
                "QEMU might build the broken pixman submodule, run `git submodule deinit -f pixman` to clean")
        super().update()


class BuildQEMU(BuildQEMUBase):
    repository = GitRepository("https://github.com/CTSRD-CHERI/qemu.git", default_branch="qemu-cheri",
                               force_branch=True)
    default_targets = "cheri256-softmmu,cheri128-softmmu,cheri128magic-softmmu,mips64-softmmu"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions()
        cls.magic128 = cls.addBoolOption("magic-128")
        # Turn on unaligned loads/stores by default
        cls.unaligned = cls.addBoolOption("unaligned", showHelp=True, help="Permit un-aligned loads/stores",
                                          default=True)
        cls.statistics = cls.addBoolOption("statistics", showHelp=True,
                                           help="Collect statistics on out-of-bounds capability creation.")

    @classmethod
    def qemu_binary(cls, caller: SimpleProject):
        binary_name = "qemu-system-cheri"
        binary_name += caller.config.cheriBitsStr
        if caller.config.cheriBits == 128 and cls.get_instance(caller, cross_target=CrossCompileTarget.NATIVE).magic128:
            binary_name += "magic"
        return caller.config.qemu_bindir / os.getenv("QEMU_CHERI_PATH", binary_name)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if self.unaligned:
            self._extraCFlags += " -DCHERI_UNALIGNED"
        if self.statistics:
            self._extraCFlags += " -DDO_CHERI_STATISTICS=1"

        # the capstone disassembler doesn't support CHERI instructions:
        self.configureArgs.append("--disable-capstone")
        if self.debug_info:
            self._extraCFlags += " -DENABLE_CHERI_SANITIY_CHECKS=1"


class BuildQEMURISCV(BuildQEMUBase):
    target = "qemu-riscv"
    projectName = "qemu-riscv"
    default_targets = "riscv64-softmmu"

    @classmethod
    def qemu_binary(cls, caller: SimpleProject):
        return caller.config.sdkBinDir / "qemu-system-riscv64"


class BuildCheriOSQEMU(BuildQEMU):
    repository = GitRepository("https://github.com/CTSRD-CHERI/qemu.git", default_branch="cherios", force_branch=True)
    projectName = "cherios-qemu"
    target = "cherios-qemu"
    defaultInstallDir = ComputedDefaultValue(
        function=lambda config, project: config.outputRoot / "cherios-sdk",
        as_string="$INSTALL_ROOT/cherios-sdk")
    skip_misc_llvm_tools = False # Cannot skip these tools in upstream LLVM

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._qemuTargets = "cheri256-softmmu,cheri128-softmmu"

    @classmethod
    def qemu_binary(cls, caller: SimpleProject):
        binary_name = "qemu-system-cheri" + caller.config.cheriBitsStr
        return cls.get_instance(caller, caller.config,
                                cross_target=CrossCompileTarget.NATIVE).installDir / "bin" / binary_name
