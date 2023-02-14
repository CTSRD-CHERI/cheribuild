#
# Copyright (c) 2017 Alex Richardson
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
from .crosscompileproject import CrossCompileAutotoolsProject, GitRepository


class BuildSQLite(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/sqlite.git",
                               default_branch="3.22.0-cheri", force_branch=True)

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        # XXX: Disabling amalgamation should remove the requirement for tclsh, but it seems the build still invokes it.
        self.check_required_system_tool("tclsh", freebsd="tcl-wrapper")

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            self.configure_environment["BUILD_CC"] = str(self.host_CC)
        self.configure_args.append("--with-pic")  # ensure that static lib can be embedded in qtbase
        # always disable tcl, since it tries to install to /usr on Ubuntu
        self.configure_args.append("--disable-tcl")
        self.configure_args.append("--disable-amalgamation")
        self.configure_args.append("--disable-load-extension")
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")

        if self.target_info.is_freebsd():
            self.configure_args.append("--disable-editline")
            # not sure if needed:
            self.configure_args.append("--disable-readline")

        if self.build_type.should_include_debug_info:
            self.COMMON_FLAGS.append("-g")
        if self.build_type.is_debug:
            self.configure_args.append("--enable-debug")

        # Enables the sqlite3_column_table_name16 API (needed by QtBase)
        self.COMMON_FLAGS.append("-DSQLITE_ENABLE_COLUMN_METADATA=1")

    def compile(self, **kwargs):
        # create the required metadata
        self.run_cmd(self.source_dir / "create-fossil-manifest", cwd=self.source_dir)
        super().compile()

    def install(self, **kwargs):
        super().install()

    def needs_configure(self):
        return not (self.build_dir / "Makefile").exists()
