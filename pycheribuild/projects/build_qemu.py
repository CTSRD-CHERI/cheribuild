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
from .project import *
from ..utils import *
from pathlib import Path
import shutil


class BuildQEMU(AutotoolsProject):
    repository = "https://github.com/CTSRD-CHERI/qemu.git"
    gitBranch = "qemu-cheri"
    defaultInstallDir = AutotoolsProject._installToSDK
    appendCheriBitsToBuildDir = True
    # QEMU will not work with BSD make, need GNU make
    make_kind = MakeCommandKind.GnuMake
    is_sdk_target = True
    skipGitSubmodules = True  # we don't need these

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions()
        cls.magic128 = cls.addBoolOption("magic-128")
        cls.debug_info = cls.addBoolOption("debug-info")
        # Turn on unaligned loads/stores by default
        cls.unaligned = cls.addBoolOption("unaligned", showHelp=True, help="Permit un-aligned loads/stores",
                                          default=True)
        cls.lto = cls.addBoolOption("use-lto", showHelp=True,
                                    help="Try to build QEMU with link-time optimization if possible", default=True)

    @classmethod
    def qemu_binary(cls, config: CheriConfig):
        binary_name = "qemu-system-cheri"
        if config.unified_sdk:
            binary_name += config.cheriBitsStr
            if config.cheriBits == 128 and cls.get_instance(config).magic128:
                binary_name += "magic"
        return config.sdkBinDir / binary_name

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._addRequiredSystemTool("glibtoolize" if IS_MAC else "libtoolize", homebrew="libtool")
        self._addRequiredSystemTool("autoreconf", homebrew="autoconf")
        self._addRequiredSystemTool("aclocal", homebrew="automake")
        self._addRequiredSystemTool("python2.7", installInstructions="QEMU needs Python 2.7 installed")

        self._addRequiredPkgConfig("pixman-1", homebrew="pixman", zypper="libpixman-1-0-devel", apt="libpixman-1-dev",
                                   freebsd="pixman")
        self._addRequiredPkgConfig("glib-2.0", homebrew="glib", zypper="glib2-devel", apt="libglib2.0-dev",
                                   freebsd="glib")

        # there are some -Wdeprected-declarations, etc. warnings with new libraries/compilers and it builds
        # with -Werror by default but we don't want the build to fail because of that -> add -Wno-error
        extraCFlags = "" if self.debug_info else "-O3"
        extraLDFlags = ""
        extraCXXFlags = ""
        if shutil.which("pkg-config"):
            glibIncludes = runCmd("pkg-config", "--cflags-only-I", "glib-2.0", captureOutput=True,
                                  printVerboseOnly=True, runInPretendMode=True).stdout.decode("utf-8").strip()
            extraCFlags += " " + glibIncludes

        compiler = self.config.clangPath
        if compiler:
            ccinfo = getCompilerInfo(compiler)
            if ccinfo.compiler == "apple-clang" or (ccinfo.compiler == "clang" and ccinfo.version >= (4, 0, 0)):
                # silence this warning that comes lots of times (it's fine on x86)
                extraCFlags += " -Wno-address-of-packed-member"
            if self.lto and self.can_use_lto(ccinfo):
                extraCFlags += " -flto=thin"
                extraCXXFlags += " -flto=thin"
                extraLDFlags += " -flto=thin"
                statusUpdate("Compiling with Clang and LLD -> building with LTO enabled (should result in faster QEMU)")
                if ccinfo.compiler != "apple-clang":
                    extraLDFlags += " -fuse-ld=lld"
                    # For non apple-clang compilers we need to use llvm binutils:
                    version_suffix = ""
                    if compiler.name.startswith("clang"):
                        version_suffix = compiler.name[len("clang"):]
                    self._addRequiredSystemTool("llvm-ar" + version_suffix)
                    self._addRequiredSystemTool("llvm-ranlib" + version_suffix)
                    self._addRequiredSystemTool("llvm-nm" + version_suffix)
                    llvm_ar = shutil.which("llvm-ar" + version_suffix)
                    llvm_ranlib = shutil.which("llvm-ranlib" + version_suffix)
                    llvm_nm = shutil.which("llvm-nm" + version_suffix)
                    self.configureEnvironment.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
                    # self.make_args.env_vars.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
                    self.make_args.set(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
        if self.config.unified_sdk:
            targets = "cheri256-softmmu,cheri128-softmmu,cheri128magic-softmmu"
        else:
            targets = "cheri-softmmu"
            if config.cheriBits == 128:
                # enable QEMU 128 bit capabilities
                # https://github.com/CTSRD-CHERI/qemu/commit/40a7fc2823e2356fa5ffe1ee1d672f1d5ec39a12
                extraCFlags += " -DCHERI_128=1" if not self.magic128 else " -DCHERI_MAGIC128=1"

        if self.unaligned:
            extraCFlags += " -DCHERI_UNALIGNED -DCHERI_C0_NULL"
        self.configureArgs.extend([
            "--target-list=" + targets,
            "--disable-linux-user",
            "--disable-bsd-user",
            "--disable-xen",
            "--disable-docs",
            "--disable-rdma",
            "--disable-werror",
            "--extra-cflags=" + extraCFlags,
            "--cxx=" + str(self.config.clangPlusPlusPath),
            "--cc=" + str(self.config.clangPath),
            ])
        python_path = shutil.which("python2.7") or shutil.which("python2") or ""
        # QEMU needs python 2.7 for building:
        self.configureArgs.append("--python=" + python_path)
        # the capstone disassembler doesn't support CHERI instructions:
        self.configureArgs.append("--disable-capstone")
        if extraLDFlags:
            self.configureArgs.append("--extra-ldflags=" + extraLDFlags.strip())
        if extraCXXFlags:
            self.configureArgs.append("--extra-cxxflags=" + extraCXXFlags.strip())
        if self.debug_info:
            self.configureArgs.extend(["--enable-debug", "--disable-strip"])
        else:
            # Try to optimize as much as possible:
            self.configureArgs.extend(["--disable-stack-protector"])

        if IS_LINUX:
            # "--enable-libnfs", # version on Ubuntu 14.04 is too old? is it needed?
            # self.configureArgs += ["--enable-kvm", "--enable-linux-aio", "--enable-vte", "--enable-sdl",
            #                        "--with-sdlabi=2.0", "--enable-virtfs"]
            self.configureArgs.extend(["--disable-stack-protector"])  # seems to be broken on some Ubuntu 14.04 systems
        else:
            self.configureArgs.extend(["--disable-linux-aio", "--disable-kvm"])

        if IS_FREEBSD:
            self.configureArgs.append("--smbd=/usr/local/sbin/smbd")

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir():
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir, printVerboseOnly=True)
        if (self.sourceDir / "pixman/pixman").exists():
            warningMessage("QEMU might build the broken pixman submodule, run `git submodule deinit -f pixman` to clean")
        super().update()
