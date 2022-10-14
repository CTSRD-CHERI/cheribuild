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
import shutil
import typing
from pathlib import Path

from .crosscompileproject import (BuildType, CheriConfig, CompilationTargets, CrossCompileAutotoolsProject,
                                  DefaultInstallDir, GitRepository, Linkage, MakeCommandKind)
from .gmp import BuildGmp
from ..project import ComputedDefaultValue
from ...processutils import run_command
from ...utils import OSInfo, status_update


class TemporarilyRemoveProgramsFromSdk(object):
    def __init__(self, programs: "typing.List[str]", config: CheriConfig, sdk_bindir: Path):
        self.programs = programs
        self.config = config
        self.sdk_bindir = sdk_bindir

    def __enter__(self):
        status_update('Temporarily moving', self.programs, "from", self.sdk_bindir)
        for prog in self.programs:
            if (self.sdk_bindir / prog).exists():
                run_command("mv", "-f", prog, prog + ".backup", cwd=self.sdk_bindir,
                            config=self.config, print_verbose_only=True)
        return self

    def __exit__(self, *exc):
        status_update('Restoring', self.programs, "in", self.sdk_bindir)
        for prog in self.programs:
            if (self.sdk_bindir / (prog + ".backup")).exists() or self.config.pretend:
                run_command("mv", "-f", prog + ".backup", prog, cwd=self.sdk_bindir, config=self.config,
                            print_verbose_only=True)
        return False


class BuildGDBBase(CrossCompileAutotoolsProject):
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    repository = GitRepository("git://sourceware.org/git/binutils-gdb.git")
    make_kind = MakeCommandKind.GnuMake
    do_not_add_to_targets = True
    is_sdk_target = True
    default_build_type = BuildType.RELEASE
    supported_architectures = (CompilationTargets.ALL_CHERIBSD_NON_CHERI_TARGETS +
                               CompilationTargets.ALL_CHERIBSD_HYBRID_TARGETS +
                               CompilationTargets.ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS +
                               CompilationTargets.ALL_SUPPORTED_FREEBSD_TARGETS +
                               [CompilationTargets.NATIVE_NON_PURECAP])
    default_architecture = CompilationTargets.NATIVE_NON_PURECAP
    prefer_full_lto_over_thin_lto = True

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "list[str]":
        deps = super().dependencies(config)
        # For the native and native-hybrid builds gmp must be installed via ports.
        if not cls.get_crosscompile_target().is_native():
            deps.append("gmp")
        return deps

    @classmethod
    def is_toolchain_target(cls):
        return cls._xtarget is not None and cls._xtarget.is_native()

    def linkage(self):
        if not self.compiling_for_host():
            # We always want to build the CheriBSD binary static so that we can just scp it over to QEMU
            return Linkage.STATIC
        return super().linkage()

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        if self.compiling_for_host() and self.target_info.is_cheribsd():
            self.check_required_pkg_config("gmp", freebsd="gmp")
            self.check_required_pkg_config("expat", freebsd="expat")

    def __init__(self, *args, **kwargs):
        self._compile_status_message = None
        super().__init__(*args, **kwargs)

    def setup(self) -> None:
        super().setup()
        install_root = self.install_dir if self.compiling_for_host() else self.install_prefix
        # See https://github.com/bsdjhb/kdbg/blob/master/gdb/build
        # ./configure flags
        self.configure_args.extend([
            "--disable-nls",
            "--enable-tui",
            "--disable-ld",
            "--disable-gold",
            "--disable-sim",
            "--enable-64-bit-bfd",
            "--without-gnu-as",
            "--mandir=" + str(install_root / "man"),
            "--infodir=" + str(install_root / "info"),
            "--disable-werror",
            "MAKEINFO=" + str(shutil.which("false")),
            "--with-gdb-datadir=" + str(install_root / "share/gdb"),
            "--disable-libstdcxx",
            "--with-guile=no",
            ])

        if self.target_info.is_freebsd():
            self.configure_args.append("--with-separate-debug-dir=/usr/lib/debug")

        if self.use_lto:
            self.configure_args.append("--enable-lto")

        # BUILD the gui:
        if False and self.compiling_for_host():
            self.configure_args.append("--enable-gdbtk")
            # if OSInfo.IS_MAC:
            # self.configure_args.append("--with-tcl=/usr/local/opt/tcl-tk/lib")
            # self.configure_environment["PKG_CONFIG_PATH"] =
            # "/usr/local/opt/tcl-tk/lib/pkgconfig:/usr/local/lib/pkgconfig"

        # extra ./configure environment variables:
        # XXX: cannot enable this until https://sourceware.org/pipermail/gdb-patches/2020-November/173174.html
        # self.common_warning_flags.append("-Werror=implicit-function-declaration")
        if self.should_include_debug_info:
            self.COMMON_FLAGS.append("-g")
        self.COMMON_FLAGS.append("-fcommon")
        # FIXME: we have to disable these -Werror flags since otherwise the configure checks fail and GDB tries to
        # build it's own printf (which results in compiler errors).
        self.cross_warning_flags.append("-Wno-error=format")
        self.cross_warning_flags.append("-Wno-error=incompatible-pointer-types")
        self.configure_args.append("--enable-targets=all")
        if self.compiling_for_host():
            if self.target_info.is_freebsd():
                self.LDFLAGS.append(f"-L{self.target_info.localbase}/lib")  # Expat/GMP are in $LOCALBASE
            self.configure_args.append("--with-expat")
        else:
            self.configure_args.extend(["--without-python", "--without-expat", "--without-libunwind-ia64"])
            self.configure_environment.update(gl_cv_func_gettimeofday_clobber="no",
                                              lt_cv_sys_max_cmd_len="262144",
                                              # The build system run CC without any flags to detect dependency style...
                                              # (ZW_PROG_COMPILER_DEPENDENCIES([CC])) -> for gcc3 mode which seems
                                              # correct
                                              am_cv_CC_dependencies_compiler_type="gcc3",
                                              MAKEINFO="/bin/false"
                                              )
            self.configure_args.append("--with-gmp=" + str(BuildGmp.get_install_dir(self)))
            # GDB > 12 only uses --with-gmp
            self.configure_args.append("--with-libgmp-prefix=" + str(BuildGmp.get_install_dir(self)))
            # Autoconf stupidly decides which to use based on file existence
            # before trying to actually use the thing, so our passing of
            # -static means the .so fails to link in and thus the build fails.
            self.configure_args.append("--with-libgmp-type=static")

            self.COMMON_FLAGS.append("-static")  # seems like LDFLAGS is not enough
            # XXX: libtool wants to strip -static from some linker invocations,
            #      and because sbrk's availability is determined based on
            #      -static (libc.a has sbrk on RISC-V, but not libc.so.7), the
            #      dynamic links error with the missing symbol. --static isn't
            #      recognised by libtool but still accepted by the drivers, so
            #      this bypasses that.
            self.LDFLAGS.append("--static")
            self.COMMON_FLAGS.extend(["-DRL_NO_COMPAT", "-DLIBICONV_PLUG", "-fno-strict-aliasing"])
            # Currently there are a lot of `undefined symbol 'elf_version'`, etc errors
            # Add -lelf to the linker command line until the source is fixed
            self.LDFLAGS.append("-lelf")
            self.LDFLAGS.append("-lmd")
            self.configure_environment.update(CONFIGURED_M4="m4", CONFIGURED_BISON="byacc", TMPDIR="/tmp", LIBS="")
            self.configure_environment["CC_FOR_BUILD"] = str(self.host_CC)
            self.configure_environment["CXX_FOR_BUILD"] = str(self.host_CXX)
            self.configure_environment["CFLAGS_FOR_BUILD"] = "-g -fcommon"
            self.configure_environment["CXXFLAGS_FOR_BUILD"] = "-g -fcommon"

        if self.make_args.command == "gmake":
            self.configure_environment["MAKE"] = "gmake"

        if not self.compiling_for_host():
            self.add_configure_env_arg("AR", self.target_info.ar)
            self.add_configure_env_arg("RANLIB", self.target_info.ranlib)
            self.add_configure_env_arg("NM", self.target_info.nm)

        # Some of the configure scripts are invoked lazily (during the make invocation instead of from ./configure)
        # Therefore we need to set all the enviroment variables when compiling, too.
        self.make_args.set_env(**self.configure_environment)

    def configure(self, **kwargs):
        if self.compiling_for_host() and OSInfo.IS_MAC:
            self.configure_environment.clear()
            print(self.configure_args)
            # self.configure_args.clear()
        super().configure()

    def compile(self, **kwargs):
        with TemporarilyRemoveProgramsFromSdk(["as", "ld", "objcopy", "objdump"], self.config,
                                              self.install_dir):
            # also install objdump
            self.run_make(make_target="all-binutils", cwd=self.build_dir)
            self.run_make(make_target="all-gdb", cwd=self.build_dir)
            # And for native GDB also build ld.bfd
            if self.compiling_for_host():
                self.run_make(make_target="all-ld", cwd=self.build_dir)

    def install(self, **kwargs):
        self.run_make_install(target="install-gdb")
        # Install the binutils prefixed with g (like homebrew does it on MacOS)
        # objdump is useful for cases where CHERI llvm-objdump doesn't print sensible source lines
        # Also install most of the other tools in case they work better than elftoolchain
        if self.compiling_for_host():
            binutils = ("objdump", "objcopy", "addr2line", "readelf", "ar", "ranlib", "size", "strings")
            bindir = self.install_dir / "bin"
            for util in binutils:
                self.install_file(self.build_dir / "binutils" / util, bindir / ("g" + util))
            # nm and c++filt have a different name in the build dir:
            self.install_file(self.build_dir / "binutils/cxxfilt", bindir / "gc++filt")
            self.install_file(self.build_dir / "binutils/nm-new", bindir / "gnm")
            self.install_file(self.build_dir / "binutils/strip-new", bindir / "gstrip")


class BuildUpstreamGDB(BuildGDBBase):
    repository = GitRepository("git://sourceware.org/git/binutils-gdb.git")
    target = "upstream-gdb"
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, proj:
            config.output_root / "upstream-gdb" if proj._xtarget.is_native() else None,
        # NB: We set default_architecture so the unsuffixed target is native
        as_string=lambda cls:
            "$INSTALL_ROOT/upstream-gdb" if cls._xtarget is None or cls._xtarget.is_native() else None,
        inherit=BuildGDBBase._default_install_dir_fn)


class BuildGDB(BuildGDBBase):
    path_in_rootfs = "/usr/local"  # Always install gdb as /usr/local/bin/gdb
    native_install_dir = DefaultInstallDir.CHERI_SDK
    default_branch = "cheri-12"
    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/gdb.git",
        # Branch name is changed for every major GDB release:
        default_branch=default_branch,
        old_branches={"mips_cheri_7.12": default_branch,
                      "mips_cheri-8.0.1": default_branch,
                      "mips_cheri-8.2": default_branch,
                      "mips_cheri-8.3": default_branch,
                      "morello-8.3": default_branch},
        old_urls=[b'https://github.com/bsdjhb/gdb.git'])


class BuildKGDB(BuildGDB):
    repository = GitRepository("https://github.com/CTSRD-CHERI/gdb.git",
                               # Branch name is changed for every major GDB release:
                               default_branch="mips_cheri-8.3-kgdb", force_branch=True,
                               old_urls=[b'https://github.com/bsdjhb/gdb.git'])
