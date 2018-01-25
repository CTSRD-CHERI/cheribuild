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
import os
import shutil


class BuildQEMU(AutotoolsProject):
    repository = "https://github.com/CTSRD-CHERI/qemu.git"
    gitBranch = "qemu-cheri"
    defaultInstallDir = AutotoolsProject._installToSDK
    appendCheriBitsToBuildDir = True
    # QEMU will not work with BSD make, need GNU make
    make_kind = MakeCommandKind.GnuMake
    skipGitSubmodules = True  # we don't need these

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions()
        cls.magic128 = cls.addBoolOption("magic-128")

    @classmethod
    def qemu_binary(cls, config: CheriConfig):
        binary_name = "qemu-system-cheri"
        if config.unified_sdk:
            binary_name += config.cheriBitsStr
            if config.cheriBits == 128 and cls.magic128:
                binary_name += "magic"
        return config.sdkBinDir / binary_name

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._addRequiredSystemTool("pkg-config")
        self._addRequiredSystemTool("glibtoolize" if IS_MAC else "libtoolize", homebrewPackage="libtool")
        self._addRequiredSystemTool("autoreconf", homebrewPackage="autoconf")
        self._addRequiredSystemTool("aclocal", homebrewPackage="automake")
        self._addRequiredSystemTool("python", installInstructions="QEMU needs Python 2 installed as the python binary")

        # TODO: suggest on Ubuntu install libglib2.0-dev libpixman-1-dev libsdl2-dev libgtk2.0-dev

        # there are some -Wdeprected-declarations, etc. warnings with new libraries/compilers and it builds
        # with -Werror by default but we don't want the build to fail because of that -> add -Wno-error
        extraCFlags = "-O3 -Wno-error"
        if shutil.which("pkg-config"):
            glibIncludes = runCmd("pkg-config", "--cflags-only-I", "glib-2.0", captureOutput=True,
                                  printVerboseOnly=True, runInPretendMode=True).stdout.decode("utf-8").strip()
            extraCFlags += " " + glibIncludes

        ccinfo = getCompilerInfo(os.getenv("CC", shutil.which("cc")))
        if ccinfo.compiler.endswith("clang"):
            # silence this warning that comes lots of times (it's fine on x86)
            extraCFlags += " -Wno-address-of-packed-member"
        if self.config.unified_sdk:
            targets = "cheri256-softmmu,cheri128-softmmu,cheri128magic-softmmu"
        else:
            targets = "cheri-softmmu"
            if config.cheriBits == 128:
                # enable QEMU 128 bit capabilities
                # https://github.com/CTSRD-CHERI/qemu/commit/40a7fc2823e2356fa5ffe1ee1d672f1d5ec39a12
                extraCFlags += " -DCHERI_128=1" if not self.magic128 else " -DCHERI_MAGIC128=1"
        self.configureArgs.extend([
            "--target-list=" + targets,
            "--disable-linux-user",
            "--disable-bsd-user",
            "--disable-xen",
            "--disable-docs",
            "--disable-rdma",
            "--disable-werror",
            "--extra-cflags=" + extraCFlags,
        ])
        if IS_LINUX:
            # "--enable-libnfs", # version on Ubuntu 14.04 is too old? is it needed?
            # self.configureArgs += ["--enable-kvm", "--enable-linux-aio", "--enable-vte", "--enable-sdl",
            #                        "--with-sdlabi=2.0", "--enable-virtfs"]
            self.configureArgs.extend(["--disable-stack-protector"])  # seems to be broken on some Ubuntu 14.04 systems
        else:
            self.configureArgs.extend(["--disable-linux-aio", "--disable-kvm"])

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir():
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir, printVerboseOnly=True)
        if (self.sourceDir / "pixman/pixman").exists():
            warningMessage("QEMU might build the broken pixman submodule, run `git submodule deinit -f pixman` to clean")
        super().update()
