#
# Copyright (c) 2017 Alex Richardson
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
from .crosscompileproject import (CheriConfig, CrossCompileAutotoolsProject, DefaultInstallDir, FettProjectMixin,
                                  GitRepository, Linkage)
from .qt5 import BuildQtWebkit


class BuildSQLite(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/sqlite.git",
                               default_branch="3.22.0-cheri", force_branch=True)
    _needed_for_webkit = True

    def linkage(self):
        if not self.compiling_for_host() and (
                self._needed_for_webkit and BuildQtWebkit.get_instance(self, self.config).force_static_linkage):
            return Linkage.STATIC  # make sure it works with webkit
        return super().linkage()

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            self.configure_environment["BUILD_CC"] = self.host_CC
            # self.configure_environment["BUILD_CFLAGS"] = "-integrated-as"
            self.configure_args.extend([
                "--disable-amalgamation",  # don't concatenate sources
                "--disable-load-extension",
                ])
        # always disable tcl, since it tries to install to /usr on Ubuntu
        self.configure_args.append("--disable-tcl")
        self.configure_args.append("--disable-amalgamation")
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")

        if self.target_info.is_freebsd():
            self.configure_args.append("--disable-editline")
            # not sure if needed:
            self.configure_args.append("--disable-readline")

        if self.build_type.should_include_debug_info:
            self.COMMON_FLAGS.append("-g")
        if self.build_type.is_debug:
            self.configure_args.append("--enable-debug")

    def compile(self, **kwargs):
        # create the required metadata
        self.run_cmd(self.source_dir / "create-fossil-manifest", cwd=self.source_dir)
        super().compile()

    def install(self, **kwargs):
        super().install()

    def needs_configure(self):
        return not (self.build_dir / "Makefile").exists()


class BuildFettSQLite(FettProjectMixin, BuildSQLite):
    target = "fett-sqlite"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sqlite.git", default_branch="fett")
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    _needed_for_webkit = False

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configure_args.extend(["--enable-fts3"])
