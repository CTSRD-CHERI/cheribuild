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
from .crosscompileproject import CrossCompileAutotoolsProject, CrossCompileCMakeProject, CrossCompileMesonProject
from ..project import DefaultInstallDir, GitRepository
from ...config.chericonfig import CheriConfig
from ...config.compilation_targets import CompilationTargets


class BuildEPollShim(CrossCompileCMakeProject):
    target = "epoll-shim"
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    repository = GitRepository("https://github.com/jiixyj/epoll-shim")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.NATIVE_IF_FREEBSD

    def configure(self, **kwargs):
        if not self.compiling_for_host():
            # external/microatf/cmake/ATFTestAddTests.cmake breaks cross-compilation
            self.add_cmake_options(BUILD_TESTING=False)
            # Set these variables to the CMake results from building natively:
            self.add_cmake_options(ALLOWS_ONESHOT_TIMERS_WITH_TIMEOUT_ZERO=True)
        super().configure()

    def run_tests(self):
        if self.compiling_for_host():
            self.run_make("test")
        else:
            self.info("Don't know how to run tests for", self.target, "when cross-compiling.")


class BuildLibFFI(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/libffi/libffi.git")
    target = "libffi"
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def configure(self, **kwargs):
        self.run_cmd(self.source_dir / "autogen.sh", cwd=self.source_dir)
        super().configure(**kwargs)


class BuildWayland(CrossCompileMesonProject):
    @classmethod
    def dependencies(cls, config: CheriConfig):
        deps = super().dependencies(config)
        target = cls.get_crosscompile_target(config)
        if not target.is_native():
            # For native builds we use the host libraries
            deps.extend(["libexpat", "libffi", "libxml2"])
            # We need a native wayland-scanner during the build
            deps.append("wayland-native")
        if target.target_info_cls.is_freebsd():
            deps += ["epoll-shim"]
        return deps

    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    # TODO: upstream patches and use https://gitlab.freedesktop.org/wayland/wayland.git
    repository = GitRepository("https://github.com/CTSRD-CHERI/wayland")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def setup(self):
        super().setup()
        # Can be set to False to avoid libxml2 depdency:
        self.add_meson_options(dtd_validation=True)
        # Avoid docbook depedency
        self.add_meson_options(documentation=False)
        if self.target_info.is_macos():
            # Only build wayland-scanner
            self.add_meson_options(libraries=False)
