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

from .crosscompileproject import (CheriConfig, commandline_to_str, CrossCompileAutotoolsProject,
                                  DefaultInstallDir, FettProjectMixin, GitRepository, MakeCommandKind)
from .openssl import BuildFettOpenSSL


class BuildNginx(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/nginx.git")
    # we have to build in the source directory, out-of-source is broken
    # build_in_source_dir = True
    make_kind = MakeCommandKind.GnuMake
    add_host_target_build_config_options = False
    # custom configure script -> no --libdir
    _configure_supports_libdir = False
    _configure_supports_variables_on_cmdline = False
    _configure_understands_enable_static = False
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configureCommand = self.sourceDir / "auto/configure"
        if not self.compiling_for_host():
            self.COMMON_FLAGS.extend(["-pedantic",
                                      "-Wno-gnu-statement-expression",
                                      "-Wno-flexible-array-extensions",  # TODO: could this cause errors?
                                      # "-Wno-extended-offsetof",
                                      "-Wno-format-pedantic",
                                      ])
            self.configureEnvironment["AR"] = str(self.sdk_bindir / "cheri-unknown-freebsd-ar")
        # The makefile expects the current working directory to be the source dir. Therefore we add -f $build/Makefile
        # This is also in the makefile generated in the source dir but it doesn't work with multiple build dirs
        self.make_args.add_flags("-f", self.buildDir / "Makefile")
        self.cross_warning_flags += ["-Wno-error=cheri-capability-misuse", "-Wno-error=sign-compare"]

    def install(self, **kwargs):
        # We have to run make inside the source directory
        self.runMakeInstall(cwd=self.sourceDir)
        self.install_file(self.sourceDir / "fetchbench", self.real_install_root_dir / "sbin/fetchbench")
        # install the benchmark script
        benchmark = self.read_file(self.sourceDir / "nginx-benchmark.sh")
        if not self.compiling_for_host():
            benchmark = re.sub(r'NGINX=.*', "NGINX=\"" + str(self.installPrefix / "sbin/nginx") + "\"", benchmark)
            benchmark = re.sub(r'FETCHBENCH=.*', "FETCHBENCH=\"" + str(self.installPrefix / "sbin/fetchbench") + "\"",
                               benchmark)
        self.write_file(self.real_install_root_dir / "nginx-benchmark.sh", benchmark, overwrite=True, mode=0o755)

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()

    def configure(self):
        if self.should_include_debug_info:
            self.configureArgs.append("--with-debug")
        self.configureArgs.extend(["--without-pcre",
                                   "--without-http_rewrite_module",
                                   "--with-http_v2_module",
                                   "--with-http_ssl_module",
                                   "--without-http_gzip_module",
                                   "--without-http_rewrite_module",
                                   "--without-pcre",
                                   "--builddir=" + str(self.buildDir)])
        if not self.compiling_for_host():
            self.LDFLAGS.append("-v")
            self.configureArgs.extend(["--crossbuild=FreeBSD:12.0-CURRENT:mips",
                                       "--with-cc-opt=" + commandline_to_str(self.default_compiler_flags),
                                       "--with-ld-opt=" + commandline_to_str(self.default_ldflags),
                                       "--sysroot=" + str(self.sdk_sysroot),
                                       ])
            self.configureEnvironment["CC_TEST_FLAGS"] = commandline_to_str(self.default_compiler_flags)
            self.configureEnvironment["NGX_TEST_LD_OPT"] = commandline_to_str(self.default_ldflags)
            self.configureEnvironment["NGX_SIZEOF_int"] = "4"
            self.configureEnvironment["NGX_SIZEOF_sig_atomic_t"] = "4"  # on mips it is an int
            self.configureEnvironment["NGX_SIZEOF_long"] = "8"
            self.configureEnvironment["NGX_SIZEOF_long_long"] = "8"
            self.configureEnvironment["NGX_SIZEOF_size_t"] = "8"
            self.configureEnvironment["NGX_SIZEOF_off_t"] = "8"
            self.configureEnvironment["NGX_SIZEOF_time_t"] = "8"
            self.configureEnvironment["NGX_SIZEOF_void_p"] = str(self.target_info.pointer_size)
            self.configureEnvironment["NGX_HAVE_MAP_DEVZERO"] = "yes"
            self.configureEnvironment["NGX_HAVE_SYSVSHM"] = "yes"
            self.configureEnvironment["NGX_HAVE_MAP_ANON"] = "yes"
            self.configureEnvironment["NGX_HAVE_POSIX_SEM"] = "yes"
        super().configure(cwd=self.sourceDir)

    def compile(self, **kwargs):
        # The cwd for make needs to be the source dir and it expects an empty target name
        self.run_make(cwd=self.sourceDir)


class BuildFettNginx(FettProjectMixin, BuildNginx):
    project_name = "fett-nginx"
    path_in_rootfs = "/fett/nginx"
    repository = GitRepository("https://github.com/CTSRD-CHERI/nginx.git", default_branch="fett")
    dependencies = ["fett-openssl"]

    def configure(self):
        openssl_dir = str(BuildFettOpenSSL.get_instance(self)._installPrefix)
        self.configureEnvironment["NGX_OPENSSL_fett_path"] = str(BuildFettOpenSSL.get_instance(self).destdir) + openssl_dir
        self.configureEnvironment["NGX_OPENSSL_fett_rpath"] = openssl_dir + "/lib"
        super().configure()

    def install(self):
        super().install()
        nginx_conf = self.installDir / "conf/nginx.conf"
        if nginx_conf.is_file():
            self.deleteFile(nginx_conf)
