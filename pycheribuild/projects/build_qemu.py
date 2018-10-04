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
import subprocess


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
        cls.with_sanitizers = cls.addBoolOption("sanitizers", help="Build QEMU with ASAN/UBSAN (very slow)", default=False)
        # Turn on unaligned loads/stores by default
        cls.unaligned = cls.addBoolOption("unaligned", showHelp=True, help="Permit un-aligned loads/stores",
                                          default=True)

        cls.use_smbd = cls.addBoolOption("use-smbd", showHelp=False, default=True,
                                         help="Don't require SMB support when building QEMU (warning: most --test "
                                              "targets will fail without smbd support)")

        cls.statistics = cls.addBoolOption("statistics", showHelp=True, default=False,
                                           help="Collect statistics on out-of-bounds capability creation.")
        cls.gui = cls.addBoolOption("gui", showHelp=True, default=False,
                                    help="Build a the graphical UI bits for QEMU (SDL,VNC)")
        cls.lto = cls.addBoolOption("use-lto", showHelp=True,
                                    help="Try to build QEMU with link-time optimization if possible", default=True)
        cls.legacy_registers = cls.addBoolOption("legacy-registers", showHelp=False,
                                                 help="Build QEMU with the special registers mapped into the GPRs",
                                                 default=False)

    @classmethod
    def qemu_binary(cls, caller: SimpleProject):
        binary_name = "qemu-system-cheri"
        if caller.config.unified_sdk:
            binary_name += caller.config.cheriBitsStr
            if caller.config.cheriBits == 128 and cls.get_instance(caller, caller.config).magic128:
                binary_name += "magic"
        return caller.config.sdkBinDir / binary_name

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
        extraCFlags = "-DCONFIG_DEBUG_TCG=1" if self.debug_info else "-O3"
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
                # Turn implicit function declaration into an error -Wimplicit-function-declaration
                extraCFlags += " -Werror=implicit-function-declaration"
                # Also make discarding const an error:
                extraCFlags += " -Werror=incompatible-pointer-types-discards-qualifiers"
                # silence this warning that comes lots of times (it's fine on x86)
                extraCFlags += " -Wno-address-of-packed-member"
            if self.lto and self.can_use_lto(ccinfo):
                while True:  # add a loop so I can break early
                    statusUpdate("Compiling with Clang and LLD -> trying to build with LTO enabled")
                    if ccinfo.compiler != "apple-clang":
                        extraLDFlags += " -fuse-ld=lld"
                        # For non apple-clang compilers we need to use llvm binutils:
                        version_suffix = ""
                        if compiler.name.startswith("clang"):
                            version_suffix = compiler.name[len("clang"):]
                        llvm_ar = shutil.which("llvm-ar" + version_suffix)
                        llvm_ranlib = shutil.which("llvm-ranlib" + version_suffix)
                        llvm_nm = shutil.which("llvm-nm" + version_suffix)
                        if not llvm_ar or not llvm_ranlib or not llvm_nm:
                            self.warning("Could not find llvm-{ar,ranlib,nm}" + version_suffix,
                                         "-> disabling LTO (qemu will be a bit slower)")
                            break
                        self._addRequiredSystemTool("llvm-ar" + version_suffix)
                        self._addRequiredSystemTool("llvm-ranlib" + version_suffix)
                        self._addRequiredSystemTool("llvm-nm" + version_suffix)
                        self.configureEnvironment.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
                        # self.make_args.env_vars.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
                        self.make_args.set(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
                    extraCFlags += " -flto=thin"
                    extraCXXFlags += " -flto=thin"
                    extraLDFlags += " -flto=thin"
                    statusUpdate("Building with LTO -> QEMU should be faster")
                    break
        if self.config.unified_sdk:
            targets = "cheri256-softmmu,cheri128-softmmu,cheri128magic-softmmu"
        else:
            targets = "cheri-softmmu"
            if config.cheriBits == 128:
                # enable QEMU 128 bit capabilities
                # https://github.com/CTSRD-CHERI/qemu/commit/40a7fc2823e2356fa5ffe1ee1d672f1d5ec39a12
                extraCFlags += " -DCHERI_128=1" if not self.magic128 else " -DCHERI_MAGIC128=1"
        if self.unaligned:
            extraCFlags += " -DCHERI_UNALIGNED"
        if self.legacy_registers:
            extraCFlags += " -DCHERI_C0_NULL=0"
        if self.statistics:
            extraCFlags += " -DDO_CHERI_STATISTICS=1"
        self.configureArgs.extend([
            "--target-list=" + targets,
            "--disable-linux-user",
            "--disable-bsd-user",
            "--disable-xen",
            "--disable-docs",
            "--disable-rdma",
            "--disable-werror",
            "--disable-pie",  # no need to build as PIE (this just slows down QEMU)
            "--extra-cflags=" + extraCFlags,
            "--cxx=" + str(self.config.clangPlusPlusPath),
            "--cc=" + str(self.config.clangPath),

        ])
        # Disable some more unneeded things (we don't usually need the GUI frontends)
        if not self.gui:
            self.configureArgs.extend(["--disable-vnc", "--disable-sdl", "--disable-gtk", "--disable-opengl"])

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
            self.configureArgs.extend(["--enable-debug", "--disable-strip", "--enable-debug-tcg"])
            extraCFlags += " -DENABLE_CHERI_SANITIY_CHECKS=1"
        else:
            # Try to optimize as much as possible:
            self.configureArgs.extend(["--disable-stack-protector"])

        if self.with_sanitizers:
            self.configureArgs.append("--enable-sanitizers")

        if IS_LINUX:
            # "--enable-libnfs", # version on Ubuntu 14.04 is too old? is it needed?
            # self.configureArgs += ["--enable-kvm", "--enable-linux-aio", "--enable-vte", "--enable-sdl",
            #                        "--with-sdlabi=2.0", "--enable-virtfs"]
            self.configureArgs.extend(["--disable-stack-protector"])  # seems to be broken on some Ubuntu 14.04 systems

        else:
            self.configureArgs.extend(["--disable-linux-aio", "--disable-kvm"])

    def configure(self, **kwargs):
        if self.use_smbd:
            smbd_path = "/usr/sbin/smbd"
            if IS_FREEBSD:
                smbd_path = "/usr/local/sbin/smbd"
            elif IS_MAC:
                try:
                    prefix = runCmd("brew", "--prefix", "samba", captureOutput=True, runInPretendMode=True,
                                    printVerboseOnly=True).stdout.decode("utf-8").strip()
                except subprocess.CalledProcessError:
                    prefix = self.config.otherToolsDir
                smbd_path = Path(prefix, "sbin/smbd")
                print("Guessed samba path", smbd_path)

            if (self.config.otherToolsDir / "sbin/smbd").exists():
                smbd_path = self.config.otherToolsDir / "sbin/smbd"

            self._addRequiredSystemTool(smbd_path, cheribuild_target="samba", freebsd="samba48", apt="samba",
                                        homebrew="samba")

            self.configureArgs.append("--smbd=" + str(smbd_path))
            if not Path(smbd_path).exists():
                if IS_MAC:
                    # QEMU user networking expects a smbd that accepts the same flags and config files as the samba.org
                    # sources but the macos /usr/sbin/smbd is incompatible with that:
                    warningMessage("QEMU usermode samba shares require the samba.org smbd. You will need to build it from "
                                   "source (using `cheribuild.py samba`) since the /usr/sbin/smbd shipped by MacOS is "
                                   "incompatible with QEMU")
                fatalError("Could not find smbd -> QEMU SMB shares networking will not work",
                           fixitHint="Either install samba using the system package manager or with cheribuild. "
                                     "If you really don't need QEMU host shares you can disable the samba dependency "
                                     "by setting --qemu/no-use-smbd")
        super().configure(**kwargs)

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir():
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir, printVerboseOnly=True)
        if (self.sourceDir / "pixman/pixman").exists():
            warningMessage(
                "QEMU might build the broken pixman submodule, run `git submodule deinit -f pixman` to clean")
        super().update()
