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
from .crosscompileproject import (CheriConfig, CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository,
                                  MakeCommandKind)

class BuildSQLbox(CrossCompileAutotoolsProject):
    # Just add add the FETT target below for now.
    doNotAddToTargets = True
    build_in_source_dir = True

    repository = GitRepository("https://github.com/kristapsdz/sqlbox.git")

    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    cross_install_dir = DefaultInstallDir.ROOTFS

    make_kind = MakeCommandKind.BsdMake
    add_host_target_build_config_options = False
    _configure_supports_prefix = False
    _configure_supports_libdir = False
    _configure_supports_variables_on_cmdline = False
    _configure_understands_enable_static = False

    def setup(self):
        super().setup()
        self.configureArgs.append("PREFIX=" + str(self.installPrefix))

    def needsConfigure(self):
        return not (self.buildDir / "config.h").exists()


class BuildFettSQLbox(BuildSQLbox):
    project_name = "fett-sqlbox"
    path_in_rootfs = "/fett"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sqlbox.git",
                               default_branch="fett")

    dependencies = ["fett-sqlite"]

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            self.COMMON_LDFLAGS.append("-L" + str(self.rootfs_dir / "fett/lib"))
            self.COMMON_FLAGS.append("-I" + str(self.rootfs_dir / "fett/include"))
