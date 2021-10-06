#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
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
from .crosscompileproject import CompilationTargets, CrossCompileMesonProject, GitRepository


class BuildLibDrm(CrossCompileMesonProject):
    target = "libdrm"
    dependencies = ["libpciaccess"]
    repository = GitRepository("https://gitlab.freedesktop.org/mesa/drm.git",
                               temporary_url_override="https://gitlab.freedesktop.org/arichardson/drm.git",
                               url_override_reason="https://gitlab.freedesktop.org/mesa/drm/-/merge_requests/199")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def setup(self):
        super().setup()
        if self.compiling_for_cheri():
            # Needs to be fixed properly to stop passing pointers in __u64 fields.
            # For now we just want the library to compile so that code using it does not need to be modified (but it
            # won't work at runtime yet).
            self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")
        if not self.compiling_for_host():
            self.add_meson_options(amdgpu=False, nouveau=False, intel=False, radeon=False, vmwgfx=True,
                                   omap=False, exynos=False, freedreno=False, tegra=False, etnaviv=False,
                                   valgrind=False, **{"cairo-tests": False, "freedreno-kgsl": False})

