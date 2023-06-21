#
# Copyright (c) 2021 SRI International
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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
from .crosscompileproject import (
    CrossCompileAutotoolsProject,
    CrossCompileCMakeProject,
    DefaultInstallDir,
    GitRepository,
    SubversionRepository,
)
from .expat import BuildExpat
from ..project import ReuseOtherProjectRepository


class BuildPcre(CrossCompileAutotoolsProject):
    target = "pcre"

    repository = SubversionRepository("svn://vcs.pcre.org/pcre",
                                      default_branch="code/trunk")

    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def configure(self, **kwargs):
        self.run_cmd("autoreconf", "-i", cwd=self.source_dir)
        super().configure(**kwargs)


class BuildApr(CrossCompileAutotoolsProject):
    target = "apr"
    repository = GitRepository("https://github.com/CTSRD-CHERI/apr.git",
                               default_branch="cheri")

    dependencies = ("libexpat",)

    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def setup(self):
        super().setup()
        self.configure_args.extend([
            "--enable-threads",
            "--enable-posix-shm",
            "--with-devrandom",
            "--with-expat=" + str(BuildExpat.get_install_dir(self)),
            ])
        if self.build_type.is_debug:
            self.configure_args.append("--enable-debug")

        # configure is too broken to add the -L path needed
        # for -lexpat to the generated Makefile though it
        # does use it while testing if libexpat works
        self.LDFLAGS.append("-L" + str(self.install_dir / "lib"))

        if not self.compiling_for_host():
            # Can't determine these when cross-compiling
            self.configure_environment.update(ac_cv_file__dev_zero="yes",
                                              ac_cv_mmap__dev_zero="yes",
                                              # XXX: This might be yes on Linux
                                              ac_cv_func_setpgrp_void="no",
                                              ac_cv_struct_rlimit="yes",
                                              ac_cv_func_sem_open="yes",
                                              apr_cv_process_shared_works="yes",
                                              apr_cv_mutex_robust_shared="yes",
                                              # XXX: This might be yes on Linux
                                              apr_cv_tcp_nodelay_with_cork="no",
                                              )

    def configure(self, **kwargs):
        self.run_cmd("./buildconf", cwd=self.source_dir)
        super().configure(**kwargs)


class BuildApache(CrossCompileAutotoolsProject):
    target = "apache"
    repository = GitRepository("https://github.com/CTSRD-CHERI/apache-httpd.git",
                               default_branch="2.4.x-cheri")

    dependencies = ("apr", "pcre")

    def setup(self):
        super().setup()
        self.configure_args.extend([
            "--enable-layout=FreeBSD",
            "--enable-http",
            "--enable-mod-ssl",
            "--with-expat=" + str(BuildExpat.get_install_dir(self)),
            "--with-pcre=" + str(BuildPcre.get_install_dir(self)),
            "--with-apr=" + str(BuildApr.get_install_dir(self)),
            ])
        if self.build_type.is_debug:
            self.configure_args.append("--enable-debugger-mode")

        # The cross-compile always assumes this is true (so one
        # can't normally cross-compile Apache), and it's a fatal
        # error, so just lie always.
        self.configure_environment.update(ap_cv_void_ptr_lt_long="no")

        # mod_ssl doesn't use -L from --with-expat
        self.LDFLAGS.append("-L" + str(self.install_dir / "lib"))

    def configure(self, **kwargs):
        self.run_cmd("./buildconf", cwd=self.source_dir)
        super().configure(**kwargs)

        # gen_test_char rules in server/ assume a native build
        if not self.compiling_for_host():
            self.run_cmd(str(self.host_CC), "-DCROSS_COMPILE", "-c",
                         self.source_dir / "server" / "gen_test_char.c",
                         "-o", "gen_test_char.lo",
                         cwd=self.build_dir / "server")
            self.run_cmd(str(self.host_CC), "gen_test_char.lo", "-o",
                         "gen_test_char", cwd=self.build_dir / "server")


class BuildSSLProc(CrossCompileCMakeProject):
    target = "sslproc"

    repository = GitRepository("https://github.com/CTSRD-CHERI/sslproc.git")

    has_optional_tests = True
    default_build_tests = False
    show_optional_tests_in_help = True

    def setup(self):
        super().setup()
        self.add_cmake_options(BUILD_TESTS=self.build_tests)


class BuildSSLProcApache(BuildApache):
    target = "apache-sslproc"

    repository = ReuseOtherProjectRepository(BuildApache, do_update=True)

    dependencies = (*BuildApache.dependencies, "sslproc")

    def setup(self):
        super().setup()
        self.configure_args.append(
            "--with-sslproc=" + str(BuildSSLProc.get_install_dir(self)),
            )
