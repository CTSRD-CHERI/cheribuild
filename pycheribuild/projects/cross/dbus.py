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
from .crosscompileproject import CompilationTargets, CrossCompileCMakeProject, GitRepository


class BuildDBus(CrossCompileCMakeProject):
    target = "dbus"
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE
    repository = GitRepository(
        "https://gitlab.freedesktop.org/dbus/dbus.git",
        old_urls=[b"https://gitlab.freedesktop.org/arichardson/dbus.git"],
    )
    dependencies = ("libexpat",)
    ctest_script_extra_args = ["--test-timeout", str(120 * 60)]  # Tests can take a long time to run

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
        if not self.compiling_for_host() and self.target_info.is_freebsd():
            self.write_file(
                self.rootfs_dir / "etc/rc.conf.d/dbus",
                contents='dbus_enable="YES"\n',
                overwrite=True,
                print_verbose_only=False,
            )
            # Slightly modified version of https://cgit.freebsd.org/ports/plain/devel/dbus/files/dbus.in
            # to add the necessary users on-demand and chmod/chown the rsync'd files
            self.write_file(
                self.rootfs_dir / self.target_info.localbase / "etc/rc.d/dbus",
                contents=f"""#!/bin/sh

# PROVIDE: dbus
# REQUIRE: DAEMON ldconfig
#
# Add the following lines to /etc/rc.conf to enable the D-BUS messaging system:
#
# dbus_enable="YES"
#

. /etc/rc.subr

: ${{dbus_enable=${{gnome_enable-NO}}}} ${{dbus_flags="--system"}}

name=dbus
rcvar=dbus_enable

command="{self.install_prefix}/bin/dbus-daemon"
pidfile="/var/run/dbus/pid"

start_precmd="dbus_prestart"
stop_postcmd="dbus_poststop"

dbus_prestart()
{{
    # See UIDs and GIDs in freebsd-ports
    if ! pw group show messagebus > /dev/null ; then
        pw groupadd -n messagebus -g 556
    fi
    if ! pw user show messagebus > /dev/null ; then
        pw useradd -n messagebus -u 556 -c "D-BUS Daemon User" -d /nonexistent -s /usr/sbin/nologin -g 556
    fi
    chown root:messagebus {self.install_prefix}/libexec/dbus-daemon-launch-helper
    chmod 4750 {self.install_prefix}/libexec/dbus-daemon-launch-helper
    chmod -R u+rwX,go+rX,go-w {self.install_prefix}/share/dbus-1 {self.install_prefix}/etc/dbus-1
    mkdir -p /var/lib/dbus
    {self.install_prefix}/bin/dbus-uuidgen --ensure
    mkdir -p /var/run/dbus
}}

dbus_poststop()
{{
    rm -f $pidfile
}}

load_rc_config ${{name}}
run_rc_command "$1"
""",
                overwrite=True,
                mode=0o755,
            )
