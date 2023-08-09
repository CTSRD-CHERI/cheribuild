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

import os

from .project import DefaultInstallDir, GitRepository, MakeCommandKind, Project
from ..utils import OSInfo

SMB_OUT_OF_SOURCE_BUILD_WORKS = False


# Install samba from source (e.g. on MacOS where the builtin smbd is not usable by QEMU
class BuildSamba(Project):
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    # TODO: the out-of source build doesn't work with bundled krb5
    if SMB_OUT_OF_SOURCE_BUILD_WORKS:
        make_kind = MakeCommandKind.CustomMakeTool
    else:
        build_in_source_dir = True
    # NB: We can't update beyond 4.13 due to https://bugzilla.samba.org/show_bug.cgi?id=15024
    repository = GitRepository("https://github.com/CTSRD-CHERI/samba.git",
                               old_urls=[b"https://github.com/samba-team/samba.git"],
                               default_branch="v4-13-stable", force_branch=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_command = self.source_dir / "configure"
        if SMB_OUT_OF_SOURCE_BUILD_WORKS:
            self.configure_command = self.source_dir / "buildtools/bin/waf"
            self.configure_args.insert(0, "configure")
            self.make_args.set_command(self.source_dir / "buildtools/bin/waf")
            self.make_args.add_flags("--blddir=" + str(self.build_dir))
            self.make_args.add_flags("--srcdir=" + str(self.source_dir))
            self.configure_args.append("--blddir=" + str(self.build_dir))
            self.configure_args.append("--srcdir=" + str(self.source_dir))

    def setup(self):
        super().setup()
        # Based on https://willhaley.com/blog/compile-samba-macos/
        # Also try to disable everything that is not needed for QEMU user shares
        self.configure_args.extend([
            "--disable-cephfs",
            "--disable-cups",
            "--disable-iprint",
            "--disable-glusterfs",
            "--disable-python",
            "--without-acl-support",
            "--without-ad-dc",
            "--without-ads",
            "--without-ldap",
            "--without-pam",
            "--without-quotas",
            "--without-regedit",
            "--without-syslog",
            "--without-utmp",
            "--without-winbind",
            # "--without-json-audit", "--without-ldb-lmdb", (only needed in master not 4.8 stable)
            "--prefix=" + str(self.install_dir),
        ])
        #  version 4.9 "--without-json-audit",
        self.configure_args.append("--without-json")

        if OSInfo.IS_MAC:
            self.configure_args.extend(["--with-system-mitkrb5", self.get_homebrew_prefix("krb5")])

    def configure(self, **kwargs):
        # XXX: Can't call contains_commit inside setup() since we might not have cloned the repo yet.
        if self.repository.contains_commit(self, "91c024dfd8ecf909f23ab8ee3816ae6a4c9b881c", src_dir=self.source_dir):
            # current master branch doesn't need as many workarounds
            self.configure_args.extend([
                # Avoid depending on libraries from the build tree:
                "--bundled-libraries=ALL", "--with-static-modules=ALL",
                "--enable-debug",
            ])
        else:
            self.configure_args.extend([
                "--without-ntvfs-fileserver", "--without-dnsupdate",
                # Avoid depending on libraries from the build tree:
                "--bundled-libraries=talloc,tdb,pytdb,ldb,pyldb,tevent,pytevent", "--with-static-modules=ALL",
            ])
        super().configure(cwd=self.source_dir, **kwargs)

    def compile(self, **kwargs):
        if SMB_OUT_OF_SOURCE_BUILD_WORKS:
            self.run_make("build", cwd=self.source_dir)
        else:
            super().compile(**kwargs)

    def install(self, **kwargs):
        if SMB_OUT_OF_SOURCE_BUILD_WORKS:
            self.run_make_install(cwd=self.source_dir)
        else:
            super().install(**kwargs)

    def process(self):
        if OSInfo.IS_MAC:
            # We need icu4c, libarchive, readline and krb5 from homebrew:
            homebrew_dirs = [str(self.get_homebrew_prefix(pkg)) for pkg in ("krb5", "libarchive", "readline", "icu4c")]
            with self.set_env(PATH=':'.join([x + "/bin" for x in homebrew_dirs]) + ':' +
                                   ':'.join([x + "/sbin" for x in homebrew_dirs]) + ':' +
                                   os.getenv("PATH", ""),
                              PKG_CONFIG_PATH=':'.join([x + "/lib/pkgconfig" for x in homebrew_dirs]) + ':' +
                                              os.getenv("PKG_CONFIG_PATH", ""),
                              LDFLAGS=' '.join(["-L" + x + "/lib" for x in homebrew_dirs]),
                              CPPFLAGS=' '.join(["-I" + x + "/include" for x in homebrew_dirs]),
                              CFLAGS=' '.join(["-I" + x + "/include" for x in homebrew_dirs])):
                super().process()
        else:
            super().process()

    def needs_configure(self):
        return True

    def clean(self):
        self.clean_directory(self.source_dir / "bin", ensure_dir_exists=False)
        return super().clean()
