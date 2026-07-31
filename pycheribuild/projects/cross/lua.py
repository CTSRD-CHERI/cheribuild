#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2026 A. Theodore Markettos
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

from .crosscompileproject import CrossCompileMakefileProject
from ..project import GitRepository


class BuildLua(CrossCompileMakefileProject):
    repository = GitRepository("https://github.com/lua/lua.git")
    dependencies = ("libxml2",)

    def setup(self):
        if (self.crosscompile_target.is_cheri_purecap()):
            self.fatal("Lua currently does not support building for purecap")

        super().setup()
        compflags = [*self.essential_compiler_and_linker_flags]
        compflags += ["-lm"]
        compflags += ["-Wl,-E"]

        # Avoid dependency on libgcc_eh
        self.make_args.set(MYLDFLAGS=self.commandline_to_str(compflags))
        self.make_args.set(CC=self.CC)

    def install(self, **kwargs):
        self.install_file(self.build_dir / "lua", self.install_dir / "bin/lua")
        self.install_file(self.build_dir / "lua.h", self.install_dir / "include/lua.h")
        self.install_file(self.build_dir / "luaconf.h", self.install_dir / "include/luaconf.h")
        self.install_file(self.build_dir / "lualib.h", self.install_dir / "include//lualib.h")
        self.install_file(self.build_dir / "lauxlib.h", self.install_dir / "include/lauxlib.h")
        self.install_file(self.build_dir / "liblua.a", self.install_dir / "lib/liblua.a")
