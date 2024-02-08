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
import typing

from .project import AutotoolsProject, DefaultInstallDir, GitRepository
from .simple_project import BoolConfigOption


class BuildGnuBinutils(AutotoolsProject):
    target = "gnu-binutils"
    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/binutils.git", default_branch="cheribsd", force_branch=True
    )
    native_install_dir = DefaultInstallDir.CHERI_SDK
    full_install = BoolConfigOption(
        "install-all-tools", help="Whether to install all binutils tools instead" "of only as, ld and objdump"
    )

    def setup(self):
        super().setup()
        # http://marcelog.github.io/articles/cross_freebsd_compiler_in_linux.html

        # If we don't use a patched binutils version on linux we get an ld binary that is
        # only able to handle 32 bit mips:
        # GNU ld (GNU Binutils) 2.18
        # Supported emulations:
        #     elf32ebmip

        # The version from the FreeBSD source tree supports the right targets:
        # GNU ld 2.17.50 [FreeBSD] 2007-07-03
        # Supported emulations:
        #    elf64btsmip_fbsd
        #    elf32btsmip_fbsd
        #    elf32ltsmip_fbsd
        #    elf64btsmip_fbsd
        #    elf64ltsmip_fbsd
        #    elf32btsmipn32_fbsd
        #    elf32ltsmipn32_fbsd
        self.configure_args.extend(
            [
                # on cheri gcc -dumpmachine returns mips64-undermydesk-freebsd, however this is not accepted by BFD
                # if we just pass --target=mips64 this apparently defaults to mips64-unknown-elf on freebsd
                # and also on Linux, but let's be explicit in case it assumes ELF binaries to target linux
                # "--target=mips64-undermydesk-freebsd",  # binutils for MIPS64/CHERI
                "--target=mips64-unknown-freebsd",  # binutils for MIPS64/FreeBSD
                "--disable-werror",  # -Werror won't work with recent compilers
                "--enable-ld",  # enable linker (is default, but just be safe)
                "--enable-libssp",  # not sure if this is needed
                "--enable-64-bit-bfd",  # Make sure we always have 64 bit support
                "--enable-targets=all",
                "--disable-gprof",
                "--disable-gold",
                "--disable-nls",
                "--disable-info",
                #  "--program-prefix=cheri-unknown-freebsd-",
                "MAKEINFO=missing",  # don't build docs, this will fail on recent Linux systems
            ]
        )
        self.configure_args.append("--disable-shared")
        # newer compilers will default to -std=c99 which will break binutils:
        cflags = "-std=gnu89 -O2"
        info = self.get_compiler_info(self.CC)
        if info.compiler == "clang" or (info.compiler == "gcc" and info.version >= (4, 6, 0)):
            cflags += " -Wno-unused"
        self.configure_environment["CFLAGS"] = cflags

    def compile(self, **kwargs):
        self.run_make("all-ld", logfile_name="build")
        self.run_make("all-gas", logfile_name="build")
        self.run_make("all-binutils", logfile_name="build")

    def install(self, **kwargs):
        bindir = self.install_dir / "bin"
        if not self.full_install:
            # we don't want to install all programs, as the rest comes from elftoolchain
            self.run_make("install-gas", logfile_name="install", append_to_logfile=True, parallel=False)
            self.delete_file(bindir / "mips64-unknown-freebsd-ld")
            self.run_make("install-ld", logfile_name="install", append_to_logfile=True, parallel=False)
            # we also need the linker scripts so this is not enough:
            # self.install_file(self.build_dir / "ld/ld-new", bindir / "ld.bfd", force=True)
            self.move_file(bindir / "mips64-unknown-freebsd-ld", bindir / "mips64-unknown-freebsd-ld.bfd")
            installed_tools = ["as", "ld.bfd"]
            # copy objdump from the build dir
            self.install_file(self.build_dir / "binutils/objdump", bindir / "mips64-unknown-freebsd-objdump")
            installed_tools.append("objdump")
        else:
            super().install()
            installed_tools = "addr2line ranlib strip ar nm readelf as objcopy size c++filt objdump strings".split()
            # create links for ld:
            self.create_triple_prefixed_symlinks(bindir / "ld.bfd")
        for tool in installed_tools:
            prefixed_name = "mips64-unknown-freebsd-" + tool
            if not (bindir / prefixed_name).is_file():
                self.fatal("Binutils binary", prefixed_name, "is missing!")
            # create the right symlinks to the tool (ld -> mips64-unknown-elf-ld, etc)
            # Also symlink cheri-unknown-freebsd-ld -> ld (and the other targets)
            self.create_triple_prefixed_symlinks(bindir / prefixed_name, tool_name=tool, create_unprefixed_link=True)

    @property
    def triple_prefixes_for_binaries(self) -> typing.Iterable[str]:
        return ["cheri-unknown-freebsd-"]  # compat only

    def process(self):
        self.warning(
            "GNU binutils should only be built if you know what you are doing since the linker "
            "is incredibly buggy and the assembler doesn't support all features that clang does."
        )
        if not self.query_yes_no("Are you sure you want to build this code?"):
            return
        super().process()
