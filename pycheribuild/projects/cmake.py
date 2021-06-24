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
from .project import (AutotoolsProject, CheriConfig, CMakeProject, DefaultInstallDir, GitRepository,
                      MakeCommandKind, ReuseOtherProjectDefaultTargetRepository)
from ..config.compilation_targets import CompilationTargets, CrossCompileTarget
from ..utils import replace_one


# Not really autotools but same sequence of commands (other than the script being call bootstrap instead of configure)
class BuildCMake(AutotoolsProject):
    # repository = GitRepository("https://cmake.org/cmake.git")
    repository = GitRepository("https://github.com/Kitware/CMake",  # a lot faster than the official repo
                               # track the stable release branch
                               default_branch="release")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    make_kind = MakeCommandKind.Ninja

    def __init__(self, config: CheriConfig):
        super().__init__(config, configure_script="bootstrap")
        self.configure_args.append("--parallel=" + str(self.config.make_jobs))
        self.configure_args.append("--generator=Ninja")


# When cross-compiling CMake, we do so using the CMake files instead of the bootstrap script
class BuildCrossCompiledCMake(CMakeProject):
    @staticmethod
    def custom_target_name(base_target: str, xtarget: CrossCompileTarget) -> str:
        assert not xtarget.is_native()
        if xtarget.is_cheri_purecap():
            # TODO: commit patches to build purecap
            # Target is not actually purecap, just using the purecap sysroot
            result = base_target + "-" + xtarget.get_non_cheri_target().generic_suffix + "-for-purecap-rootfs"
        else:
            result = base_target + "-" + xtarget.generic_suffix
        return replace_one(result, "-crosscompiled", "")

    repository = ReuseOtherProjectDefaultTargetRepository(BuildCMake, do_update=True)
    target = "cmake-crosscompiled"  # Can't use cmake here due to command line option conflict
    default_directory_basename = "cmake"
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    supported_architectures = CompilationTargets.ALL_CHERIBSD_TARGETS

    @property
    def essential_compiler_and_linker_flags(self):
        # XXX: Ugly hack to build the -purecap targets as non-purecap. TODO: fix purecap ctest
        if self.crosscompile_target.is_cheri_purecap():
            return self.target_info.get_essential_compiler_and_linker_flags(
                xtarget=self.crosscompile_target.get_non_cheri_target())
        return super().essential_compiler_and_linker_flags

    @property
    def cmake_prefix_paths(self):
        return []  # only the default search dirs

    @property
    def pkgconfig_dirs(self):
        return []  # only the default search dirs

    def setup(self):
        super().setup()
        # Don't bother building the ncurses or Qt GUIs even if libs are available
        self.add_cmake_options(BUILD_CursesDialog=False, BUILD_QtDialog=False)
        # Prefer static libraries for 3rd-party dependencies
        self.add_cmake_options(BUILD_SHARED_LIBS=False)

    def run_tests(self):
        assert not self.compiling_for_host(), "Target is cross-compilation only"
        # TODO: generate JUnit output once https://gitlab.kitware.com/cmake/cmake/-/merge_requests/6020 is merged
        # Can't run the testsuite since many tests depend on having a C compiler installed.
        test_command = "cd /build && ./bin/ctest -N"
        self.target_info.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                                  "--test-timeout", str(120 * 60),
                                                  mount_builddir=True, mount_sourcedir=True, mount_sysroot=True)
