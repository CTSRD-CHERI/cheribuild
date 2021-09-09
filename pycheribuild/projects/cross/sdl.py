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

from .crosscompileproject import CrossCompileAutotoolsProject
from .x11 import BuildLibX11
from ..project import GitRepository


class BuildSDL(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/libsdl-org/SDL.git")
    dependencies = ["libx11", "libxext", "libxrandr", "libxrender", "libxcursor", "libxi", "libxscrnsaver"]

    def setup(self):
        super().setup()
        # AC_PATH_X doesn't use pkg-config so have to specify manually
        self.configure_args.append("--x-includes=" + str(BuildLibX11.get_install_dir(self) / "include"))
        self.configure_args.append("--x-libraries=" + str(BuildLibX11.get_install_dir(self) / "lib"))


class BuildSDL_Mixer(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/libsdl-org/SDL_mixer.git")
    dependencies = ["sdl"]


class BuildSDL_Net(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/libsdl-org/SDL_net.git")
    dependencies = ["sdl"]
