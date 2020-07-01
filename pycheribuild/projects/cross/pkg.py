#
# Copyright (c) 2020 Alex Richardson
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
from .crosscompileproject import CompilationTargets, CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository
from ...utils import commandline_to_str


class BuildPkg(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/freebsd/pkg.git")
    _default_architecture = CompilationTargets.NATIVE
    _configure_understands_enable_static = False
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS
    path_in_rootfs = "/usr/local"
    build_in_source_dir = True  # Seems to fail when using out-of-source builds

    def setup(self):
        super().setup()
        # The configure script won't accept --target (but does allow --host/--build)
        if not self.compiling_for_host():
            for i in self.configure_args:
                if i.startswith("--target="):
                    self.configure_args.remove(i)
                    break  # avoid continuing iteration with a modified container
        self.common_warning_flags.append("-Werror=implicit-function-declaration")
        if self.target_info.is_macos():
            try:
                prefix = self.run_cmd("brew", "--prefix", "openssl", capture_output=True, run_in_pretend_mode=True,
                                      print_verbose_only=True).stdout.decode("utf-8").strip()
                self.COMMON_LDFLAGS.append("-L" + prefix + "/lib")
                self.COMMON_FLAGS.append("-I" + prefix + "/include")
                self.make_args.set_env(CPPFLAGS="-I" + prefix + "/include")
            except Exception as e:
                self.fatal("Could not find openssl:", e, fixit_hint="brew install openssl")
            try:
                prefix = self.run_cmd("brew", "--prefix", "libarchive", capture_output=True, run_in_pretend_mode=True,
                                      print_verbose_only=True).stdout.decode("utf-8").strip()
                self.COMMON_LDFLAGS.append("-L" + prefix + "/lib")
                self.COMMON_FLAGS.append("-I" + prefix + "/include")
            except Exception as e:
                self.fatal("Could not find libarchive:", e, fixit_hint="brew install libarchive")
        if self.build_type.should_include_debug_info:
            self.COMMON_FLAGS.append("-g")
        self.make_args.set_env(CPPFLAGS=commandline_to_str(
            self.COMMON_FLAGS + self.compiler_warning_flags + self.optimization_flags + self.COMMON_FLAGS))
        self.make_args.set_env(LDFLAGS=commandline_to_str(self.default_ldflags))

    def compile(self, **kwargs):
        super().compile(parallel=False, **kwargs)

    def install(self, **kwargs):
        self.makedirs(self.install_dir / "etc")
        super().install(**kwargs)

    def run_tests(self):
        if not self.compiling_for_host():
            self.fatal("Cannot run tests for non-native builds (yet).")
            return
        self.run_make("check", stdout_filter=None, parallel=False)
