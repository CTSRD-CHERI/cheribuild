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
                               old_urls=[b"https://gitlab.freedesktop.org/arichardson/drm.git"])
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


class BuildLibGlvnd(CrossCompileMesonProject):
    target = "libglvnd"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/glvnd/libglvnd.git",
                               temporary_url_override="https://gitlab.freedesktop.org/arichardson/libglvnd.git",
                               url_override_reason="Needs https://github.com/KhronosGroup/EGL-Registry/pull/137/")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def setup(self):
        super().setup()
        self.add_meson_options(glx="enabled")
        if self.compiling_for_cheri():
            self.add_meson_options(asm="disabled")


class BuildMesa(CrossCompileMesonProject):
    target = "mesa"
    repository = GitRepository("https://gitlab.freedesktop.org/mesa/mesa.git",
                               temporary_url_override="https://gitlab.freedesktop.org/arichardson/mesa.git",
                               url_override_reason="Various incorrect changes to allow purecap compilation")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]
    include_x11 = True
    include_wayland = True

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        result = super().dependencies(config) + ["libdrm", "libglvnd"]
        if cls.include_wayland:
            result.append("wayland")
        if cls.include_x11:
            result.extend(["libx11", "libxshmfence", "libxxf86vm"])
        return result

    def check_system_dependencies(self):
        # TODO: check for python-mako
        super().check_system_dependencies()

    def setup(self):
        super().setup()
        platforms = []
        if self.include_wayland:
            platforms.append("wayland")
        if self.include_x11:
            platforms.append("x11")
        meson_args = {
            "vulkan-drivers": [],  # TODO: swrast?
            "dri-drivers": [],
            "gallium-drivers": ["virgl", "swrast"],
            "egl-native-platform": platforms[0] if platforms else "",
        }
        self.add_meson_options(gbm="enabled", egl="enabled", glvnd=True, llvm="disabled", osmesa=False,
                               platforms=platforms,
                               _include_empty_vars=True, _implicitly_convert_lists=True, **meson_args)
        # threads_posix.h:274:13: error: releasing mutex 'mtx' that was not held [-Werror,-Wthread-safety-analysis]
        self.cross_warning_flags.append("-Wno-thread-safety-analysis")
        # There are quite a lot of -Wcheri-capability-misuse warnings, but for now we just want the library to exist
        # and don't need to be functional.
        # TODO: actually look at those warnings and see which of them matter.
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


class BuildLibEpoxy(CrossCompileMesonProject):
    target = "libepoxy"
    dependencies = ["mesa"]
    repository = GitRepository("https://github.com/anholt/libepoxy",
                               temporary_url_override="https://github.com/arichardson/libepoxy",
                               url_override_reason="https://github.com/anholt/libepoxy/pull/261")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def setup(self):
        super().setup()


class BuildVirglRenderer(CrossCompileMesonProject):
    target = "virglrenderer"
    dependencies = ["libepoxy", "libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/virgl/virglrenderer")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def setup(self):
        super().setup()
