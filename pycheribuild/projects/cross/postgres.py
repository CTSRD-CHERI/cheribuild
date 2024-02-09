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
import re

from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository, MakeCommandKind
from ..simple_project import BoolConfigOption
from ...utils import OSInfo


class BuildPostgres(CrossCompileAutotoolsProject):
    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/postgres.git", default_branch="96-cheri", force_branch=True
    )
    # we have to build in the source directory, out-of-source is broken
    # build_in_source_dir = True
    make_kind = MakeCommandKind.GnuMake
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    enable_assertions = BoolConfigOption("assertions", default=True, help="Build with assertions enabled")

    def setup(self):
        super().setup()
        if self.enable_assertions:
            self.COMMON_FLAGS.append("-DUSE_ASSERT_CHECKING=1")
            # self.COMMON_FLAGS.append("-DLOCK_DEBUG=1")
            self.configure_args.append("--enable-cassert")

        self.common_warning_flags.extend(
            [
                "-pedantic",
                "-Wno-gnu-statement-expression",
                "-Wno-flexible-array-extensions",  # TODO: could this cause errors?
                "-Wno-format-pedantic",
            ]
        )
        self.LDFLAGS.append("-pthread")
        if OSInfo.IS_FREEBSD and self.compiling_for_host():
            # Something werid is happending with the locale code (somehow not being built -FPIC?):
            # /usr/local/llvm60/bin/ld: error: can't create dynamic relocation R_X86_64_PC32 against symbol:
            # _CurrentRuneLocale in readonly segment; recompile object files with -fPIC
            # >>> defined in /lib/libc.so.7
            # >>> referenced by pgstrcasecmp.c:37 (/exports/users/alr48/sources/postgres/src/port/pgstrcasecmp.c:37)
            # >>>               pgstrcasecmp.o:(pg_strcasecmp) in archive ../../src/port/libpgport.a
            #
            # As a workaround I'll just add -fPIC
            self.COMMON_FLAGS.append("-fPIC")

        if OSInfo.IS_FREEBSD or not self.compiling_for_host():
            # postgres can't find readline on FreeBSD:
            self.COMMON_FLAGS.append("-I/usr/include/edit")
        if not self.compiling_for_host():
            self.add_configure_and_make_env_arg("AR", str(self.sdk_bindir / "llvm-ar"))
            # tell postgres configure that %zu works in printf()
            self.add_configure_and_make_env_arg("PRINTF_SIZE_T_SUPPORT", "yes")
            # currently we can only build static:
            # self.LDFLAGS.append("-static")
            # self.COMMON_FLAGS.append("-static")  # adding it to LDFLAGS only doesn't seem to be enough
            self.configure_args.extend(["--without-libxml", "--without-readline", "--without-gssapi"])
        else:
            self.configure_args.extend(["--with-libxml", "--with-readline", "--without-gssapi"])

        if self.force_static_linkage:
            self.add_configure_env_arg("LDFLAGS_EX", "-static")
            self.COMMON_FLAGS.append("-DDISABLE_LOADABLE_MODULES=1")
        if self.should_include_debug_info:
            self.configure_args.append("--enable-debug")
        else:
            self.configure_args.append("--disable-debug")

    def install(self, **kwargs):
        super().install()
        install_tests_args = self.make_args.copy()
        install_tests_args.add_flags("-C", "src/test/regress")
        self.run_make_install(target="install-tests", options=install_tests_args)
        # install the benchmark script
        for benchname in ("postgres-benchmark.sh", "postgres-initdb-benchmark.sh"):
            benchmark = self.read_file(self.source_dir / benchname)
            if self.install_prefix:
                pg_root = str(self.install_prefix)
            else:
                pg_root = str(self.install_dir)
            benchmark = re.sub(r'POSTGRES_ROOT=".*"', 'POSTGRES_ROOT="' + pg_root + '"', benchmark)
            self.write_file(self.real_install_root_dir / benchname, benchmark, overwrite=True, mode=0o755)
        self.install_file(
            self.source_dir / "run-postgres-tests.sh", self.real_install_root_dir / "run-postgres-tests.sh"
        )

    @property
    def default_ldflags(self):
        # HACK: we still want to build modules when forcing static we just ignore them
        result = super().default_ldflags
        if "-static" in result:
            result.remove("-static")
        return result

    def should_use_extra_c_compat_flags(self):
        return True

    def needs_configure(self):
        return not (self.build_dir / "GNUmakefile").exists()

    def run_tests(self):
        if self.compiling_for_host():
            self.run_make("check", cwd=self.build_dir / "src/test/regress", stdout_filter=None)
            # self.run_make("check", cwd=self.build_dir / "src/interfaces/ecpg/test", stdout_filter=None)
        else:
            locale_dir = self.rootfs_dir / "usr/share/locale"
            self.target_info.run_cheribsd_test_script(
                "run_postgres_tests.py",
                "--smb-mount-directory",
                str(self.install_dir) + ":" + str(self.install_prefix),
                "--locale-files-dir",
                locale_dir,
                mount_builddir=False,
                # long running test -> speed up by using a kernel without
                # invariants
                use_benchmark_kernel_by_default=True,
            )
