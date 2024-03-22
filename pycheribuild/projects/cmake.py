#
# Copyright (c) 2016, 2021 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
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
from .cmake_project import CMakeProject
from .cross.crosscompileproject import CrossCompileCMakeProject
from .project import (
    AutotoolsProject,
    CheriConfig,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
    ReuseOtherProjectDefaultTargetRepository,
)
from ..config.chericonfig import BuildType, Linkage
from ..config.compilation_targets import CompilationTargets, CrossCompileTarget
from ..targets import target_manager
from ..utils import replace_one


# CMake uses libuv, which currently causes CTest to crash.
# We should fix this in libuv, and build against the system libuv until the change has been upstreamed and CMake has
# pulled in the fixes.
class BuildLibuv(CrossCompileCMakeProject):
    target = "libuv"
    repository = GitRepository(
        "https://github.com/libuv/libuv.git",
        temporary_url_override="https://github.com/arichardson/libuv.git",
        url_override_reason="https://github.com/libuv/libuv/pull/3756",
    )


# Not really autotools but same sequence of commands (other than the script being call bootstrap instead of configure)
class BuildCMake(AutotoolsProject):
    repository = GitRepository(
        "https://github.com/Kitware/CMake",  # a lot faster than "https://cmake.org/cmake.git"
        # track the stable release branch
        default_branch="release",
    )
    default_architecture = CompilationTargets.NATIVE
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    make_kind = MakeCommandKind.Ninja
    add_host_target_build_config_options = False

    @classmethod
    def dependencies(cls, _: CheriConfig) -> "tuple[str, ...]":
        if cls.get_crosscompile_target().is_cheri_purecap():
            return ("libuv",)
        return tuple()

    def setup(self) -> None:
        super().setup()
        self.configure_command = self.source_dir / "bootstrap"
        self.configure_args.append("--parallel=" + str(self.config.make_jobs))
        self.configure_args.append("--generator=Ninja")
        if self.crosscompile_target.is_cheri_purecap():
            # CTest is broken on purecap Morello with the bundled libuv (passes pointers via a pipe)
            # NB: --bootstrap-system-libuv is not needed since the usage outside CTest is fine.
            self.configure_args.append("--system-libuv")
            self.configure_args.append("--bootstrap-system-libuv")
            self.configure_environment["CMAKE_PREFIX_PATH"] = str(BuildLibuv.get_install_dir(self))
            self.configure_environment["LDFLAGS"] = (
                f"-L{BuildLibuv.get_install_dir(self) / 'lib'} "
                f"-Wl,-rpath,{BuildLibuv.get_install_dir(self) / 'lib'}"
            )

    def run_tests(self) -> None:
        self.run_make("test", logfile_name="test")


# When cross-compiling CMake, we do so using the CMake files instead of the bootstrap script
class BuildCrossCompiledCMake(CMakeProject):
    @staticmethod
    def custom_target_name(base_target: str, xtarget: CrossCompileTarget) -> str:
        assert not xtarget.is_native()
        return replace_one(base_target + "-" + xtarget.generic_target_suffix, "-crosscompiled", "")

    repository = ReuseOtherProjectDefaultTargetRepository(BuildCMake, do_update=True)
    target = "cmake-crosscompiled"  # Can't use cmake here due to command line option conflict
    dependencies = ("libuv",)
    default_directory_basename = "cmake"
    default_build_type = BuildType.RELEASE  # Don't include debug info by default
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_TARGETS

    def linkage(self):
        # We always want to build the CheriBSD CTest binary static so that we can use in QEMU without needing libuv.
        assert "libuv" in self.dependencies
        return Linkage.STATIC

    @property
    def cmake_prefix_paths(self):
        return [BuildLibuv.get_install_dir(self)]  # only the libuv search dir

    @property
    def pkgconfig_dirs(self):
        return []  # only the default search dirs

    def setup(self):
        super().setup()
        assert not self.compiling_for_host(), "Target is cross-compilation only"
        # Don't bother building the ncurses or Qt GUIs even if libs are available
        self.add_cmake_options(BUILD_CursesDialog=False, BUILD_QtDialog=False)
        # Prefer static libraries for 3rd-party dependencies
        self.add_cmake_options(BUILD_SHARED_LIBS=False)
        self.add_cmake_options(CMAKE_USE_SYSTEM_LIBRARY_LIBUV=True)
        # CMake can't find the static libuv due to a different libname
        self.add_cmake_options(
            LibUV_LIBRARY=BuildLibuv.get_install_dir(self) / self.target_info.default_libdir / "libuv_a.a"
        )

    def run_tests(self):
        # TODO: generate JUnit output once https://gitlab.kitware.com/cmake/cmake/-/merge_requests/6020 is merged
        # Can't run the testsuite since many tests depend on having a C compiler installed.
        test_command = "cd /build && ./bin/ctest -N"
        self.target_info.run_cheribsd_test_script(
            "run_simple_tests.py",
            "--test-command",
            test_command,
            "--test-timeout",
            str(120 * 60),
            mount_builddir=True,
            mount_sourcedir=True,
            mount_sysroot=True,
        )


# Add a cmake-native target for consistency.
target_manager.add_target_alias("cmake-native", "cmake")
