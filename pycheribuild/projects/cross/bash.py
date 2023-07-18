#
# Copyright (c) 2020 Jessica Clarke
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

from pathlib import Path

from .cheribsd import BuildFreeBSD
from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository
from ..simple_project import BoolConfigOption


class BuildBash(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/bash",
                               default_branch="cheri")
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    path_in_rootfs = "/usr/local"
    set_as_root_shell = BoolConfigOption("set-as-root-shell", show_help=True,
                                         help="Set root's shell to bash (in the target rootfs)")

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            self.add_configure_vars(CC_FOR_BUILD=self.host_CC)

        # Our bison seems to generate incompatible results, and for some reason
        # the dependencies logic wants to run it. Disable it as per
        # aclocal.m4's advice.
        self.add_configure_vars(BISON=":")
        self.add_configure_vars(INTLBISON=":")
        self.add_configure_vars(YACC=":")

        # Bash is horrible K&R C in many places and deliberately uses
        # declarations with no protoype. Hopefully it gets everything right.
        self.cross_warning_flags.append("-Wno-error=cheri-prototypes")

    def install(self, **kwargs):
        if self.destdir:
            self.make_args.set(DESTDIR=self.destdir)
        super().install(**kwargs)

        if not self.compiling_for_host():
            self.create_symlink(Path("/usr/local/bin/bash"), self.destdir / "bin/bash", relative=False)
            self.add_unique_line_to_file(self.destdir / "etc/shells", "/usr/local/bin/bash")
            if self.set_as_root_shell:
                def rewrite(old):
                    new = []
                    for line in old:
                        fields = line.split(':')
                        if len(fields) == 10 and fields[0] == "root":
                            line = ':'.join(fields[0:9] + ["/usr/local/bin/bash"])
                        new.append(line)
                    return new

                freebsd_builddir = self.target_info.get_rootfs_project(t=BuildFreeBSD, caller=self).objdir
                pwd_mkdb_cmd = freebsd_builddir / "tmp/legacy/usr/sbin/pwd_mkdb"
                self.rewrite_file(self.destdir / "etc/master.passwd", rewrite)
                self.run_cmd([pwd_mkdb_cmd, "-p", "-d", self.destdir / "etc", self.destdir / "etc/master.passwd"])
