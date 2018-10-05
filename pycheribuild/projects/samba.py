#
# Copyright (c) 2018 Alex Richardson
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

from .project import *
from ..utils import runCmd, setEnv, coloured, AnsiColour, IS_MAC
import os

SMB_OUT_OF_SOURCE_BUILD_WORKS = False

# Install samba from source (e.g. on MacOS where the builtin smbd is not usable by QEMU
class BuildSamba(Project):
    # if IS_MAC:
    #     defaultInstallDir = "/opt/samba"
    # else:
    defaultInstallDir = Project._installToBootstrapTools
    # TODO: the out-of source build doesn't work with bundled krb5
    if SMB_OUT_OF_SOURCE_BUILD_WORKS:
        make_kind = MakeCommandKind.CustomMakeTool
    else:
        defaultBuildDir = Project.defaultSourceDir
    repository = "https://github.com/samba-team/samba.git"
    gitBranch = "v4-9-stable"

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configureCommand = self.sourceDir / "configure"
        if SMB_OUT_OF_SOURCE_BUILD_WORKS:
            self.configureCommand = self.sourceDir / "buildtools/bin/waf"
            self.configureArgs.insert(0, "configure")
            self.make_args.set_command(self.sourceDir / "buildtools/bin/waf")
            self.make_args.add_flags("--blddir=" + str(self.buildDir))
            self.make_args.add_flags("--srcdir=" + str(self.sourceDir))
            self.configureArgs.append("--blddir=" + str(self.buildDir))
            self.configureArgs.append("--srcdir=" + str(self.sourceDir))
        # Based on https://willhaley.com/blog/compile-samba-macos/
        # Also try to disable everything that is not needed for QEMU user shares
        self.configureArgs.extend([
            "--without-ad-dc", "--without-acl-support",
            # "--without-json-audit", "--without-ldb-lmdb", (only needed in master not 4.8 stable)
            "--without-libarchive",
            "--disable-cups",
            "--disable-python",
            "--disable-gnutls",
            "--without-ldap", "--disable-iprint",
            "--without-gettext",
            "--without-ads", "--without-winbind", "--without-pam", "--without-utmp",
            "--without-syslog", "--without-regedit",
            "--disable-glusterfs", "--disable-cephfs",
            "--without-ntvfs-fileserver",
            "--without-json-audit",
            # Avoid depending on libraries from the build tree:
            "--bundled-libraries=talloc,tdb,pytdb,ldb,pyldb,tevent,pytevent",
            "--with-static-modules=ALL",
            "--prefix=" + str(self.installDir),
        ])
        if IS_MAC:
            self._addRequiredSystemTool("/usr/local/opt/krb5/bin/kinit", homebrew="krb5")
            # TODO: brew --prefix krb5
            self.configureArgs.extend(["--with-system-mitkrb5", "/usr/local/opt/krb5"])

    def configure(self, **kwargs):
        super().configure(cwd=self.sourceDir, **kwargs)

    def compile(self, **kwargs):
        if SMB_OUT_OF_SOURCE_BUILD_WORKS:
            self.runMake("build", cwd=self.sourceDir)
        else:
            super().compile(**kwargs)

    def install(self, **kwargs):
        if SMB_OUT_OF_SOURCE_BUILD_WORKS:
            self.runMake("install", cwd=self.sourceDir)
        else:
            super().install(**kwargs)

    def process(self):
        if SMB_OUT_OF_SOURCE_BUILD_WORKS and IS_MAC:
            with setEnv(PATH="/usr/local/opt/krb5/bin:/usr/local/opt/krb5/sbin:" + os.getenv("PATH", ""),
                        PKG_CONFIG_PATH="/usr/local/opt/krb5/lib/pkgconfig:" + os.getenv("PKG_CONFIG_PATH", "")):
                super().process()
        else:
            super().process()

    def needsConfigure(self):
        return True
