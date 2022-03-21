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
from .crosscompileproject import CrossCompileCMakeProject, DefaultInstallDir, GitRepository


class BuildDBus(CrossCompileCMakeProject):
    target = "dbus"
    repository = GitRepository("https://gitlab.freedesktop.org/dbus/dbus.git",
                               temporary_url_override="https://gitlab.freedesktop.org/arichardson/dbus.git",
                               url_override_reason="Various fixes for FreeBSD and CHERI (most submitted as MRs)")
    dependencies = ["libexpat"]
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    path_in_rootfs = "/usr/local"  # Always install to /usr/local/share so that it's in the default search path

    def setup(self):
        super().setup()
        # Disable documentation to reduce dependencies
        self.add_cmake_options(DBUS_ENABLE_DOXYGEN_DOCS=False, DBUS_ENABLE_XML_DOCS=False)
        # Skip glib support for now:
        self.add_cmake_options(DBUS_WITH_GLIB=False)
        if not self.compiling_for_host():
            self.add_cmake_options(DBUS_SESSION_SOCKET_DIR="/tmp")
            self.add_cmake_options(CMAKE_INSTALL_LOCALSTATEDIR="/var")  # don't use /usr/local/var/

        # Testing malloc failures makes the testsuite painfully slow.
        self.ctest_environment["DBUS_TEST_MALLOC_FAILURES"] = "0"

    def install(self, **kwargs):
        super().install()
        if self.target_info.is_freebsd():
            self.download_file(self.install_dir / "etc/rc.d/dbus",
                               "https://cgit.freebsd.org/ports/plain/devel/dbus/files/dbus.in")
            self.replace_in_file(self.install_dir / "etc/rc.d/dbus", {"%%PREFIX%%": str(self.install_prefix)})
            if not self.config.pretend:
                (self.install_dir / "etc/rc.d/dbus").chmod(0o755)
