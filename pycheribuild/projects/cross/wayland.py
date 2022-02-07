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
from ..project import DefaultInstallDir, GitRepository, SimpleProject
from ...config.chericonfig import CheriConfig
from ...config.compilation_targets import CompilationTargets
from ...config.target_info import Linkage


class BuildEPollShim(CrossCompileCMakeProject):
    target = "epoll-shim"
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    repository = GitRepository("https://github.com/jiixyj/epoll-shim")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def configure(self, **kwargs):
        if not self.compiling_for_host():
            # external/microatf/cmake/ATFTestAddTests.cmake breaks cross-compilation
            self.add_cmake_options(BUILD_TESTING=False)
            # Set these variables to the CMake results from building natively:
            self.add_cmake_options(ALLOWS_ONESHOT_TIMERS_WITH_TIMEOUT_ZERO=True)
        super().configure()

    def install(self, **kwargs):
        if self.target_info.is_linux():
            self.info("Install not supported on linux, target only exists to run tests.")
        else:
            super().install(**kwargs)

    def run_tests(self):
        if self.compiling_for_host():
            self.run_make("test")
        else:
            self.info("Don't know how to run tests for", self.target, "when cross-compiling.")


class BuildLibUdevDevd(CrossCompileMesonProject):
    target = "libudev-devd"
    repository = GitRepository("https://github.com/FreeBSDDesktop/libudev-devd")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.NATIVE_IF_FREEBSD


# Some projects unconditionally include linux/input.h to exist. For FreeBSD dev/evdev/input.h provides a
# (mostly/fully?) interface, so we just include that instead.
# XXX: the evdev-proto port downloads the Linux headers and patches those instead, but it seems to me that creating
# a file that includes the native dev/evdev/*.h is less fragile since it doesn't rely on ioctl() numbers being
# compatible, etc.
class BuildLinux_Input_H(SimpleProject):
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.NATIVE_IF_FREEBSD

    def process(self):
        evdev_headers = ("input.h", "input-event-codes.h", "uinput.h")
        self.makedirs(self.sdk_sysroot / "usr/include/linux")
        for header in evdev_headers:
            dev_evdev_h = self.sdk_sysroot / "usr/include/dev/evdev" / header
            if not dev_evdev_h.is_file():
                self.fatal("Missing evdev header:", dev_evdev_h)
            self.write_file(self.sdk_sysroot / "usr/include/linux" / header,
                            contents=f"#include <dev/evdev/{header}>\n", overwrite=True)


class BuildMtdev(CrossCompileAutotoolsProject):
    target = "mtdev"
    needs_full_history = True  # can't use --depth with http:// git repo
    repository = GitRepository("http://bitmath.org/git/mtdev.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "list[str]":
        if cls.get_crosscompile_target(config).target_info_cls.is_freebsd():
            return super().dependencies(config) + ["linux-input-h"]
        return super().dependencies(config)

    def linkage(self):
        return Linkage.STATIC

    def setup(self):
        super().setup()
        self.COMMON_FLAGS.append("-fPIC")  # need a pic archive since it's linked into a .so
        self.cross_warning_flags.append("-Wno-error=format")

    def configure(self, **kwargs):
        self.run_cmd(self.source_dir / "autogen.sh", cwd=self.source_dir)
        super().configure(**kwargs)


class BuildLibEvdev(CrossCompileMesonProject):
    target = "libevdev"
    repository = GitRepository("https://gitlab.freedesktop.org/libevdev/libevdev.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def setup(self):
        super().setup()
        self.add_meson_options(tests="disabled")  # needs "check" library
        self.add_meson_options(documentation="disabled")  # needs "doxygen" library


class BuildLibInput(CrossCompileMesonProject):
    target = "libinput"
    repository = GitRepository("https://gitlab.freedesktop.org/libinput/libinput.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    @classmethod
    def dependencies(cls, config) -> "list[str]":
        result = super().dependencies(config) + ["mtdev", "libevdev"]
        if cls.get_crosscompile_target(config).target_info_cls.is_freebsd():
            result.extend(["libudev-devd", "epoll-shim"])
        return result

    def setup(self):
        super().setup()
        self.add_meson_options(libwacom=False, documentation=False)  # Avoid dependency on libwacom and sphinx
        self.add_meson_options(**{"debug-gui": False})  # Avoid dependency on gtk3
        self.add_meson_options(tests=False)  # Avoid dependency on "check""
        # Does not prepend sysroot to prefix:
        if self.target_info.is_freebsd():
            self.add_meson_options(**{"epoll-dir": BuildEPollShim.get_install_dir(self)})


class BuildLibFFI(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/libffi/libffi.git")
    target = "libffi"
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]

    def configure(self, **kwargs):
        self.run_cmd(self.source_dir / "autogen.sh", cwd=self.source_dir)
        super().configure(**kwargs)


class BuildWayland(CrossCompileMesonProject):
    # We need a native wayland-scanner during the build
    needs_native_build_for_crosscompile = True
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "list[str]":
        deps = super().dependencies(config)
        target = cls.get_crosscompile_target(config)
        if not target.is_native():
            # For native builds we use the host libraries
            deps.extend(["libexpat", "libffi", "libxml2"])
        if target.target_info_cls.is_freebsd():
            deps += ["epoll-shim"]
        return deps
    repository = GitRepository("https://gitlab.freedesktop.org/wayland/wayland.git", default_branch="main",
                               force_branch=True, old_urls=[b"https://github.com/CTSRD-CHERI/wayland"])
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
        if not self.compiling_for_host():
            # Don't cross-compile wayland-scanner, otherwise downstream projects will try to use the cross-compiled
            # version instead of the host binary
            self.add_meson_options(scanner=False)


class BuildWaylandProtocols(CrossCompileMesonProject):
    target = "wayland-protocols"
    dependencies = ["wayland", "wayland-native"]  # native wayland-scanner is needed for tests
    repository = GitRepository("https://gitlab.freedesktop.org/wayland/wayland-protocols.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + [CompilationTargets.NATIVE]
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def setup(self):
        super().setup()
        # Tests depend on https://gitlab.freedesktop.org/wayland/wayland-protocols/-/merge_requests/119
        self.add_meson_options(tests=False)
