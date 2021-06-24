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
from .crosscompileproject import (CheriConfig, CrossCompileAutotoolsProject, DefaultInstallDir, FettProjectMixin,
                                  GitRepository)
from .openssl import BuildFettOpenSSL
from .zlib import BuildFettZlib


class BuildOpenSSH(CrossCompileAutotoolsProject):
    # Just add add the FETT target below for now.
    do_not_add_to_targets = True

    repository = GitRepository("https://github.com/CTSRD-CHERI/openssh-portable.git")

    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    # LD is used with CFLAGS so don't set to ld/ld.lld
    _define_ld = False

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.add_required_system_tool("autoreconf", default="autoconf")

    def configure(self, **kwargs):
        self.add_configure_env_arg("AR", self.target_info.ar)
        self.add_configure_env_arg("DESTDIR", self.destdir)
        self.add_configure_env_arg("ac_cv_have_control_in_msghdr", "yes")
        self.run_cmd("autoreconf", str(self.source_dir), cwd=self.build_dir)
        super().configure(**kwargs)


class BuildFettOpenSSH(FettProjectMixin, BuildOpenSSH):
    target = "fett-openssh"
    repository = GitRepository("https://github.com/CTSRD-CHERI/openssh-portable.git",
                               default_branch="fett")

    dependencies = ["fett-zlib", "fett-openssl"]

    def configure(self, **kwargs):
        openssl_dir = str(BuildFettOpenSSL.get_instance(self)._install_prefix)
        self.configure_args.append(
            "--with-ssl-dir=" + str(BuildFettOpenSSL.get_instance(self).destdir) + "/" + openssl_dir)
        self.COMMON_LDFLAGS.append("-Wl,-rpath," + openssl_dir + "/lib")

        zlib_dir = str(BuildFettZlib.get_instance(self)._install_prefix)
        self.configure_args.append("--with-zlib=" + str(BuildFettZlib.get_instance(self).destdir) + "/" + zlib_dir)
        self.COMMON_LDFLAGS.append("-Wl,-rpath," + zlib_dir + "/lib")

        super().configure(**kwargs)
