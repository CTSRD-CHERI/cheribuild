#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright 2022 Alex Richardson
# Copyright 2022 Google LLC
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
from .crosscompileproject import CrossCompileMesonProject, GitRepository
from ...config.compilation_targets import CompilationTargets


class BuildPicoLibc(CrossCompileMesonProject):
    target = "picolibc"
    repository = GitRepository("https://github.com/picolibc/picolibc.git")
    supported_architectures = [CompilationTargets.NATIVE]
    _always_add_suffixed_targets = True

    def setup(self):
        super().setup()
        self.add_meson_options(tests=True, multilib=False, **{
            "io-long-long": True,
            "tests-enable-stack-protector": False,
        })
        if self.compiling_for_host():  # see scripts/do-native-configure
            self.add_meson_options(**{
                "tls-model": "global-dynamic",
                "errno-function": "auto",
                "use-stdlib": True,
                "picocrt": False,
                "picolib": False,
                "semihost": False,
                "posix-console": True,
                "native-tests": True,
                "tinystdio": False,  # currently fails to build due to a linker error when building tests.
            })
