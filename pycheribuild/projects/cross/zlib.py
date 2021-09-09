#
# Copyright (c) 2020 SRI International
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
from .crosscompileproject import (CrossCompileAutotoolsProject, DefaultInstallDir, FettProjectMixin, GitRepository)


class BuildZlib(CrossCompileAutotoolsProject):
    # Just add add the FETT target below for now.
    do_not_add_to_targets = True

    repository = GitRepository("https://github.com/CTSRD-CHERI/zlib.git")

    # Enable the same hacks as nginx since this isn't really autoconf...
    add_host_target_build_config_options = False
    _configure_understands_enable_static = False
    _configure_supports_variables_on_cmdline = False

    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            # If we don't set this, the build will use the macOS host libtool instead of llvm-ar and then complain
            # because the .o files are not macOS object files.
            self.add_configure_vars(uname=self.target_info.cmake_system_name,
                                    AR=self.sdk_bindir / "llvm-ar", RANLIB=self.sdk_bindir / "llvm-ranlib")


class BuildFettZlib(FettProjectMixin, BuildZlib):
    target = "fett-zlib"
    repository = GitRepository("https://github.com/CTSRD-CHERI/zlib.git", default_branch="fett")
