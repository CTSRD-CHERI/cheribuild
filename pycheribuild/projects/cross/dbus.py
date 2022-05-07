#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2022 Alex Richardson
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
from .crosscompileproject import CrossCompileCMakeProject, GitRepository


class BuildDBus(CrossCompileCMakeProject):
    target = "dbus"
    repository = GitRepository("https://gitlab.freedesktop.org/dbus/dbus.git",
                               temporary_url_override="https://gitlab.freedesktop.org/arichardson/dbus.git",
                               url_override_reason="Various fixes for FreeBSD and CHERI (most submitted as MRs)")
    dependencies = ["libexpat"]

    def setup(self):
        super().setup()
        # Disable documentation to reduce dependencies
        self.add_cmake_options(DBUS_ENABLE_DOXYGEN_DOCS=False, DBUS_ENABLE_XML_DOCS=False)
        # Work around https://gitlab.freedesktop.org/pkg-config/pkg-config/-/issues/52:
        self.add_cmake_options(DBUS_RELOCATABLE=False)
        # Skip glib support for now:
        self.add_cmake_options(DBUS_WITH_GLIB=False)
        if not self.compiling_for_host():
            self.add_cmake_options(DBUS_SESSION_SOCKET_DIR="/tmp")
            self.add_cmake_options(TEST_SOCKET_DIR="/tmp")  # Don't try to create test sockets on SMBFS
            self.add_cmake_options(CMAKE_INSTALL_LOCALSTATEDIR="/var")  # don't use /usr/local/var/

        # Testing malloc failures makes the testsuite painfully slow.
        self.ctest_environment["DBUS_TEST_MALLOC_FAILURES"] = "0"

    def install(self, **kwargs):
        super().install()
        if self.target_info.is_freebsd():
            rc_file = self.rootfs_dir / self.target_info.localbase / "etc/rc.d/dbus"
            self.download_file(rc_file, "https://cgit.freebsd.org/ports/plain/devel/dbus/files/dbus.in")
            self.replace_in_file(rc_file, {"%%PREFIX%%": str(self.install_prefix)})
            if not self.config.pretend:
                rc_file.chmod(0o755)
        if not self.compiling_for_host() and self.target_info.is_freebsd():
            # See UIDs and GIDs in freebsd-ports
            self.write_file(self.rootfs_dir / "etc/rc.conf.d/dbus", contents="dbus_enable=\"YES\"\n",
                            overwrite=True, print_verbose_only=False)
            self.add_unique_line_to_file(self.rootfs_dir / "etc/group", "messagebus:*:556:")
            self.add_unique_line_to_file(self.rootfs_dir / "etc/passwd",
                                         "messagebus:*:556:556:D-BUS Daemon User:/nonexistent:/usr/sbin/nologin")
            # FIXME: or should we suggest post-install `pw groupadd -n messagebus -g 556` followed by
            # `pw useradd -n messagebus -u 556 -c "D-BUS Daemon User" -d /nonexistent -s /usr/sbin/nologin -g 556`
