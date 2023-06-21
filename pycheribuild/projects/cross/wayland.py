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
import shutil
from pathlib import Path

from .crosscompileproject import CrossCompileAutotoolsProject, CrossCompileCMakeProject, CrossCompileMesonProject
from ..project import AutotoolsProject, DefaultInstallDir, GitRepository
from ..simple_project import SimpleProject
from ...config.chericonfig import CheriConfig, Linkage
from ...config.compilation_targets import CompilationTargets
from ...processutils import get_program_version, ssh_config_parameters
from ...utils import OSInfo


class BuildEPollShim(CrossCompileCMakeProject):
    target = "epoll-shim"
    repository = GitRepository("https://github.com/jiixyj/epoll-shim",
                               temporary_url_override="https://github.com/arichardson/epoll-shim",
                               url_override_reason="https://github.com/jiixyj/epoll-shim/pull/36")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def configure(self, **kwargs):
        self.add_cmake_options(ENABLE_COMPILER_WARNINGS=True)
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
    repository = GitRepository("https://github.com/wulf7/libudev-devd",
                               old_urls=[b"https://github.com/FreeBSDDesktop/libudev-devd"])
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.NATIVE_IF_FREEBSD
    dependencies = ("linux-input-h",)

    def setup(self):
        super().setup()
        self.CFLAGS.append("-I" + str(BuildLinuxInputH.get_instance(self).include_install_dir))


# Some projects unconditionally include linux/input.h to exist. For FreeBSD dev/evdev/input.h provides a
# (mostly/fully?) interface, so we just include that instead.
# XXX: the evdev-proto port downloads the Linux headers and patches those instead, but it seems to me that creating
# a file that includes the native dev/evdev/*.h is less fragile since it doesn't rely on ioctl() numbers being
# compatible, etc.
class BuildLinuxInputH(SimpleProject):
    target = "linux-input-h"
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.NATIVE_IF_FREEBSD

    def process(self):
        evdev_headers = ("input.h", "input-event-codes.h", "uinput.h")
        for header in evdev_headers:
            src_headers = Path("/usr/include") if self.compiling_for_host() else self.sdk_sysroot / "usr/include"
            dev_evdev_h = src_headers / "dev/evdev" / header
            if not dev_evdev_h.is_file():
                self.fatal("Missing evdev header:", dev_evdev_h)
            self.write_file(self.include_install_dir / "linux" / header, contents=f"#include <dev/evdev/{header}>\n",
                            overwrite=True, print_verbose_only=False)

    @property
    def include_install_dir(self) -> Path:
        if self.compiling_for_host():
            return BuildMtdev.get_install_dir(self) / "include"
        return self.sdk_sysroot / self.target_info.sysroot_install_prefix_relative / "include"


class BuildMtdev(CrossCompileAutotoolsProject):
    target = "mtdev"
    needs_full_history = True  # can't use --depth with http:// git repo
    repository = GitRepository("http://bitmath.org/git/mtdev.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        if cls.get_crosscompile_target().target_info_cls.is_freebsd():
            return (*super().dependencies(config), "linux-input-h")
        return super().dependencies(config)

    def linkage(self):
        return Linkage.STATIC

    def setup(self):
        super().setup()
        self.COMMON_FLAGS.append("-fPIC")  # need a pic archive since it's linked into a .so
        self.cross_warning_flags.append("-Wno-error=format")
        if self.target_info.is_freebsd():
            self.CFLAGS.append("-I" + str(BuildLinuxInputH.get_instance(self).include_install_dir))


class BuildLibEvdev(CrossCompileMesonProject):
    target = "libevdev"
    repository = GitRepository("https://gitlab.freedesktop.org/libevdev/libevdev.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def setup(self):
        super().setup()
        self.add_meson_options(tests="disabled")  # needs "check" library
        self.add_meson_options(documentation="disabled")  # needs "doxygen" library


class BuildLibInput(CrossCompileMesonProject):
    target = "libinput"
    repository = GitRepository("https://gitlab.freedesktop.org/libinput/libinput.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        if self.target_info.is_linux():
            self.check_required_pkg_config("libudev", apt="libudev-dev")

    @classmethod
    def dependencies(cls, config) -> "tuple[str, ...]":
        result = (*super().dependencies(config), "mtdev", "libevdev")
        if cls.get_crosscompile_target().target_info_cls.is_freebsd():
            result += ("libudev-devd", "epoll-shim")
        return result

    def setup(self):
        super().setup()
        self.add_meson_options(libwacom=False, documentation=False)  # Avoid dependency on libwacom and sphinx
        self.add_meson_options(**{"debug-gui": False})  # Avoid dependency on gtk3
        self.add_meson_options(tests=False)  # Avoid dependency on "check""
        # Does not prepend sysroot to prefix:
        if self.target_info.is_freebsd():
            self.add_meson_options(**{"epoll-dir": BuildEPollShim.get_install_dir(self)})


class BuildDejaGNU(AutotoolsProject):
    repository = GitRepository("https://git.savannah.gnu.org/git/dejagnu.git",
                               temporary_url_override="https://github.com/arichardson/dejagnu.git",
                               url_override_reason="Remote test execution is broken(-ish) upstream")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS


class BuildLibFFI(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/libffi/libffi.git",
                               temporary_url_override="https://github.com/CTSRD-CHERI/libffi.git",
                               url_override_reason="Needs lots of CHERI fixes")
    target = "libffi"
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def setup(self):
        super().setup()
        if self.get_compiler_info(self.CC).supports_warning_flag("-Werror=shorten-cap-to-int"):
            self.cross_warning_flags.append("-Werror=shorten-cap-to-int")
        if self.build_type.is_debug:
            self.configure_args.append("--enable-debug")
        self.configure_args.append("--disable-docs")  # avoid dependency on makeinfo

    def run_tests(self):
        runtest_cmd = shutil.which("runtest")
        if not runtest_cmd:
            self.dependency_error("DejaGNU is not installed.",
                                  install_instructions=OSInfo.install_instructions("runtest", False, default="dejagnu",
                                                                                   apt="dejagnu", homebrew="deja-gnu"),
                                  cheribuild_target="dejagnu", cheribuild_xtarget=CompilationTargets.NATIVE)
        if self.compiling_for_host():
            self.run_cmd("make", "check", "RUNTESTFLAGS=-a", cwd=self.build_dir,
                         env=dict(DEJAGNU=self.source_dir / ".ci/site.exp", BOARDSDIR=self.source_dir / ".ci"))
        elif self.target_info.is_cheribsd():
            # We need two minor fixes for SSH execution:
            runtest_ver = get_program_version(Path(runtest_cmd or "runtest"), program_name=b"DejaGnu",
                                              config=self.config)
            if runtest_ver < (1, 6, 4):
                self.dependency_error("DejaGnu version", runtest_ver, "cannot be used to run tests remotely,",
                                      "please install a newer version with cheribuild",
                                      cheribuild_target="dejagnu", cheribuild_xtarget=CompilationTargets.NATIVE)

            if self.can_run_binaries_on_remote_morello_board():
                self.write_file(self.build_dir / "site.exp", contents=f"""
if ![info exists boards_dir] {{
    set boards_dir {{}}
}}
lappend boards_dir "{self.build_dir}"
verbose "Global Config File: target_triplet is $target_triplet" 2
global target_list
set target_list "remote-cheribsd"
""", overwrite=True)
                ssh_options = "-o NoHostAuthenticationForLocalhost=yes"
                ssh_port = ssh_config_parameters(self.config.remote_morello_board).get("port", "22")
                ssh_user = ssh_config_parameters(self.config.remote_morello_board).get("user", "root")
                self.write_file(self.build_dir / "remote-cheribsd.exp", contents=f"""
load_generic_config "unix"
set_board_info connect ssh
set_board_info hostname {self.config.remote_morello_board}
set_board_info username {ssh_user}
set_board_info port {ssh_port}
# Work around typo in ssh.exp, it checks for ssh_useropts, but then appends the value of ssh_opts
set_board_info ssh_useropts "{ssh_options}"
set_board_info ssh_opts "{ssh_options}"
# set_board_info exec_shell "gdb-run-noninteractive.sh"
# Build tests statically linked so they pick up the local libffi library
set TOOL_OPTIONS -static
""", overwrite=True)
                self.run_cmd(["make", "check", "RUNTESTFLAGS=-a --target-board remote-cheribsd --xml"],
                             env=dict(BOARDSDIR=self.build_dir, DEJAGNU=self.build_dir / "site.exp"),
                             cwd=str(self.build_dir))
            else:
                self.target_info.run_cheribsd_test_script("run_libffi_tests.py", "--test-timeout", str(120 * 60),
                                                          mount_builddir=True, mount_sourcedir=True,
                                                          mount_sysroot=False, use_full_disk_image=True)


class BuildWayland(CrossCompileMesonProject):
    # We need a native wayland-scanner during the build
    needs_native_build_for_crosscompile = True

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        deps = super().dependencies(config)
        target = cls.get_crosscompile_target()
        if not target.is_native() or target.target_info_cls.is_cheribsd():
            # For native (non-CheriBSD) builds we use the host libraries
            deps += ("libexpat", "libffi", "libxml2")
        if target.target_info_cls.is_freebsd():
            deps += ("epoll-shim",)
        return deps
    repository = GitRepository("https://gitlab.freedesktop.org/wayland/wayland.git", default_branch="main",
                               force_branch=True, old_urls=[b"https://github.com/CTSRD-CHERI/wayland"])
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def setup(self):
        super().setup()
        # Can be set to False to avoid libxml2 depdency:
        self.add_meson_options(dtd_validation=True)
        # Avoid docbook dependency
        self.add_meson_options(documentation=False)
        if self.target_info.is_macos():
            # Only build wayland-scanner
            self.add_meson_options(libraries=False)
        # We have to install the wayland.xml file, which only happens if we enable the scanner option.
        self.add_meson_options(scanner=True)

    def install(self, **kwargs):
        super().install(**kwargs)
        # If we install the cross-compiled wayland-scanner, downstream projects will try to use the cross-compiled
        # version instead of the host binary, so for now delete wayland-scanner from the bindir after installation
        # FIXME: fix the downstream projects (e.g. kwayland-server) instead...
        if not self.compiling_for_host():
            self.delete_file(self.install_dir / "bin/wayland-scanner", warn_if_missing=True)


class BuildWaylandProtocols(CrossCompileMesonProject):
    target = "wayland-protocols"
    dependencies = ("wayland", "wayland-native")  # native wayland-scanner is needed for tests
    repository = GitRepository("https://gitlab.freedesktop.org/wayland/wayland-protocols.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def setup(self):
        super().setup()
        # Tests depend on https://gitlab.freedesktop.org/wayland/wayland-protocols/-/merge_requests/119
        self.add_meson_options(tests=False)


class BuildSeatd(CrossCompileMesonProject):
    target = "seatd"
    repository = GitRepository("https://git.sr.ht/~kennylevinsen/seatd")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def setup(self):
        super().setup()
        self.add_meson_options(**{"libseat-builtin": "enabled"})
