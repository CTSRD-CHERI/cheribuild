#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Jessica Clarke
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

from .crosscompileproject import CrossCompileAutotoolsProject, CrossCompileCMakeProject
from ..project import GitRepository


class BuildSDL(CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/libsdl-org/SDL.git", default_branch="SDL2", force_branch=True)
    dependencies = ("libx11", "libxext", "libxrandr", "libxrender", "libxcursor", "libxi", "libxscrnsaver")

    def setup(self):
        super().setup()
        # Tests don't build for purecap until https://github.com/CTSRD-CHERI/llvm-project/pull/624 is included in
        # CheriBSD (and it needs to be in the main branch not just dev).
        if self.compiling_for_cheri():
            self.add_cmake_options(SDL_TEST=False, SDL_TESTS=False)
        self.cross_warning_flags.append("-Wno-error=incompatible-function-pointer-types")


class BuildSDLMixer(CrossCompileAutotoolsProject):
    target = "sdl-mixer"
    repository = GitRepository("https://github.com/libsdl-org/SDL_mixer.git", default_branch="SDL2", force_branch=True)
    dependencies = ("sdl",)

    def setup(self) -> None:
        super().setup()
        self.configure_args.append("--disable-music-flac")


class BuildSDLNet(CrossCompileAutotoolsProject):
    target = "sdl-net"
    repository = GitRepository("https://github.com/libsdl-org/SDL_net.git", default_branch="SDL2", force_branch=True)
    dependencies = ("sdl",)

    def setup(self):
        super().setup()
        self.configure_args.append("--disable-sdltest")
        self.configure_args.append("--disable-examples")
