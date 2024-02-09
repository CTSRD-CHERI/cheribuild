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
import shutil

from .crosscompileproject import CrossCompileProject, DefaultInstallDir, GitRepository


class BuildOpenSSL(CrossCompileProject):
    build_in_source_dir = True
    # nginx looks in /usr/local
    path_in_rootfs = "/usr/local"

    repository = GitRepository(
        "https://github.com/CTSRD-CHERI/openssl.git", default_branch="OpenSSL_1_1_1-stable-cheri"
    )

    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE

    @classmethod
    def get_version(cls) -> "tuple[int, ...]":
        return 1, 1, 1

    def setup(self):
        super().setup()
        self.configure_command = shutil.which("perl")
        self.set_configure_prog_with_args("CC", self.CC, [*self.default_compiler_flags, "-fuse-ld=lld"])
        self.add_configure_env_arg("AR", self.target_info.ar)
        self.configure_args.append(str(self.source_dir / "Configure"))
        self.configure_args.append("BSD-generic64")
        self.configure_args.append("-shared")

        self.configure_args.append("--prefix=" + str(self._install_prefix))
        self.make_args.set(DESTDIR=str(self.destdir))

        if not self._xtarget.is_native():
            self.configure_args.append("--openssldir=" + str(self._install_prefix))
