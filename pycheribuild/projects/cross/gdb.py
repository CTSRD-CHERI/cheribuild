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
import sys
import typing

from .crosscompileproject import *
from ...utils import runCmd, statusUpdate, IS_MAC


class TemporarilyRemoveProgramsFromSdk(object):
    def __init__(self, programs: "typing.List[str]", config: CheriConfig, sdk_bindir: Path):
        self.programs = programs
        self.config = config
        self.sdk_bindir = sdk_bindir

    def __enter__(self):
        statusUpdate('Temporarily moving', self.programs, "from", self.sdk_bindir)
        for l in self.programs:
            if (self.sdk_bindir / l).exists():
                runCmd("mv", "-f", l, l + ".backup", cwd=self.sdk_bindir, print_verbose_only=True)
        return self

    def __exit__(self, *exc):
        statusUpdate('Restoring', self.programs, "in", self.sdk_bindir)
        for l in self.programs:
            if (self.sdk_bindir / (l + ".backup")).exists() or self.config.pretend:
                runCmd("mv", "-f", l + ".backup", l, cwd=self.sdk_bindir, print_verbose_only=True)
        return False


class BuildGDB(CrossCompileAutotoolsProject):
    path_in_rootfs = "/usr/local"  # Always install gdb as /usr/local/bin/gdb
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.ROOTFS
    repository = GitRepository("https://github.com/CTSRD-CHERI/gdb.git",
                               # Branch name is changed for every major GDB release:
                               default_branch="mips_cheri-8.3", force_branch=True,
                               old_urls=[b'https://github.com/bsdjhb/gdb.git'])
    make_kind = MakeCommandKind.GnuMake
    is_sdk_target = True
    supported_architectures = [CompilationTargets.NATIVE,
                               CompilationTargets.CHERIBSD_MIPS_HYBRID, CompilationTargets.CHERIBSD_RISCV_HYBRID,
                               CompilationTargets.CHERIBSD_MIPS_NO_CHERI, CompilationTargets.CHERIBSD_RISCV_NO_CHERI]

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

    def __init__(self, config: CheriConfig):
        self._compile_status_message = None
        if not self._xtarget.is_native():
            # We always want to build the MIPS binary static so we can just scp it over to QEMU
            self._linkage = Linkage.STATIC

        super().__init__(config)
        assert not self.compiling_for_cheri(), "Should only build this as a static MIPS binary not CHERIABI"

    def setup(self):
        super().setup()
        install_root = self.installDir if self.compiling_for_host() else self.installPrefix
        # See https://github.com/bsdjhb/kdbg/blob/master/gdb/build
        # ./configure flags
        self.configureArgs.extend([
            "--disable-nls",
            "--enable-tui",
            "--disable-ld",  # "--enable-ld",
            "--enable-64-bit-bfd",
            "--without-gnu-as",
            "--with-separate-debug-dir=/usr/lib/debug",
            "--mandir=" + str(install_root / "man"),
            "--infodir=" + str(install_root / "info"),
            # "--disable-sim",
            "--disable-werror",
            "MAKEINFO=/bin/false",
            "--with-gdb-datadir=" + str(install_root / "share/gdb"),
            "--disable-libstdcxx",
            "--with-guile=no",
            ])

        # BUILD the gui:
        if False and self.compiling_for_host():
            self.configureArgs.append("--enable-gdbtk")
            # if IS_MAC:
            # self.configureArgs.append("--with-tcl=/usr/local/opt/tcl-tk/lib")
            # self.configureEnvironment["PKG_CONFIG_PATH"] = "/usr/local/opt/tcl-tk/lib/pkgconfig:/usr/local/lib/pkgconfig"

        # extra ./configure environment variables:
        # compile flags
        # self.cross_warning_flags.extend(["-Wno-absolute-value", "-Wno-parentheses-equality"
        #                                   "-Wno-unused-function", "-Wno-unused-variable"])
        # These warnings are really noisy and useless:
        self.common_warning_flags.extend([
            "-Wno-mismatched-tags",
            "-Wno-unknown-warning-option",  # caused by the build passing -Wshadow=local
            ])
        self.CXXFLAGS.append("-Wno-mismatched-tags")
        # TODO: we should fix this:
        self.cross_warning_flags.append("-Wno-error=implicit-function-declaration")
        self.cross_warning_flags.append("-Wno-error=format")
        self.cross_warning_flags.append("-Wno-error=incompatible-pointer-types")
        self.configureArgs.append("--enable-targets=all")
        if self.compiling_for_host():
            self.LDFLAGS.append("-L/usr/local/lib")
            self.configureArgs.append("--with-expat")
            self.configureArgs.append("--with-python=" + str(sys.executable))
        else:
            self.configureArgs.extend(["--without-python", "--without-expat", "--without-libunwind-ia64"])
            self.configureEnvironment.update(gl_cv_func_gettimeofday_clobber="no",
                                             lt_cv_sys_max_cmd_len="262144",
                                             # The build system run CC without any flags to detect dependency style...
                                             # (ZW_PROG_COMPILER_DEPENDENCIES([CC])) -> for gcc3 mode which seems correct
                                             am_cv_CC_dependencies_compiler_type="gcc3",
                                             MAKEINFO="/bin/false"
                                             )
            self.COMMON_FLAGS.append("-static")  # seems like LDFLAGS is not enough
            # XXX: libtool wants to strip -static from some linker invocations,
            #      and because sbrk's availability is determined based on
            #      -static (libc.a has sbrk on RISC-V, but not libc.so.7), the
            #      dynamic links error with the missing symbol. --static isn't
            #      recognised by libtool but still accepted by the drivers, so
            #      this bypasses that.
            self.LDFLAGS.append("--static")
            self.COMMON_FLAGS.extend(["-DRL_NO_COMPAT", "-DLIBICONV_PLUG", "-fno-strict-aliasing", "-fcommon"])
            # Currently there are a lot of `undefined symbol 'elf_version'`, etc errors
            # Add -lelf to the linker command line until the source is fixed
            self.LDFLAGS.append("-lelf")
            self.configureEnvironment.update(CONFIGURED_M4="m4", CONFIGURED_BISON="byacc", TMPDIR="/tmp", LIBS="")
        if self.make_args.command == "gmake":
            self.configureEnvironment["MAKE"] = "gmake"

        self.configureEnvironment["CC_FOR_BUILD"] = str(self.host_CC)
        self.configureEnvironment["CXX_FOR_BUILD"] = str(self.host_CXX)
        self.configureEnvironment["CFLAGS_FOR_BUILD"] = "-g"
        self.configureEnvironment["CXXFLAGS_FOR_BUILD"] = "-g"

        if not self.compiling_for_host():
            self.add_configure_env_arg("AR", self.sdk_bindir / "ar")
            self.add_configure_env_arg("RANLIB", self.sdk_bindir / "ranlib")
            self.add_configure_env_arg("NM", self.sdk_bindir / "nm")

        # Some of the configure scripts are invoked lazily (during the make invocation instead of from ./configure)
        # Therefore we need to set all the enviroment variables when compiling, too.
        self.make_args.set_env(**self.configureEnvironment)

    def configure(self, **kwargs):
        if self.compiling_for_host() and IS_MAC:
            self.configureEnvironment.clear()
            print(self.configureArgs)
            # self.configureArgs.clear()
        super().configure()

    def compile(self, **kwargs):
        with TemporarilyRemoveProgramsFromSdk(["as", "ld", "objcopy", "objdump"], self.config,
                                              self.installDir):
            # also install objdump
            self.run_make(make_target="all-binutils", cwd=self.buildDir)
            self.run_make(make_target="all-gdb", cwd=self.buildDir)

    def install(self, **kwargs):
        self.runMakeInstall(target="install-gdb")
        # Install the binutils prefixed with g (like homebrew does it on MacOS)
        # objdump is useful for cases where CHERI llvm-objdump doesn't print sensible source lines
        # Also install most of the other tools in case they work better than elftoolchain
        # TODO: also build upstream ld.bfd?
        binutils = ("objdump", "objcopy", "addr2line", "readelf", "ar", "ranlib", "size", "strings")
        bindir = self.installDir / "bin"
        if self.compiling_for_host():
            for util in binutils:
                self.installFile(self.buildDir / "binutils" / util, bindir / ("g" + util))
            # nm and c++filt have a different name in the build dir:
            self.installFile(self.buildDir / "binutils/cxxfilt", bindir / "gc++filt")
            self.installFile(self.buildDir / "binutils/nm-new", bindir / "gnm")
            self.installFile(self.buildDir / "binutils/strip-new", bindir / "gstrip")
