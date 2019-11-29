#
# Copyright (c) 2017 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
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

import os
import pprint
import re
from builtins import issubclass
from enum import Enum
from pathlib import Path

from ..project import *
from ...config.chericonfig import BuildType
from ...config.target_info import CrossCompileTarget, Linkage, CompilationTargets
from ...utils import *

if typing.TYPE_CHECKING:
    from .cheribsd import BuildCHERIBSD

__all__ = ["CheriConfig", "CrossCompileCMakeProject", "CrossCompileAutotoolsProject", "CrossCompileTarget", "BuildType", # no-combine
           "CrossCompileProject", "MakeCommandKind", "Linkage", "Path", "DefaultInstallDir", # no-combine
           "CompilationTargets", "GitRepository", "commandline_to_str", "CrossCompileMixin"]  # no-combine


# TODO: remove this class:
# noinspection PyUnresolvedReferences
class CrossCompileMixin(object):
    doNotAddToTargets = True
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS
    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    _should_not_be_instantiated = True


class CrossCompileProject(CrossCompileMixin, Project):
    doNotAddToTargets = True


class CrossCompileCMakeProject(CrossCompileMixin, CMakeProject):
    doNotAddToTargets = True  # only used as base class

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

    def __init__(self, config: CheriConfig, generator: CMakeProject.Generator=CMakeProject.Generator.Ninja):
        if self.build_type != BuildType.DEFAULT:
            # no CMake equivalent for MinSizeRelWithDebInfo -> set minsizerel and force debug info
            if self.build_type == BuildType.MINSIZERELWITHDEBINFO:
                self.build_type = BuildType.MINSIZEREL
                self._force_debug_info = True
        super().__init__(config, generator)
        # This must come first:
        if not self.compiling_for_host():
            # Despite the name it should also work for baremetal newlib
            assert self.target_info.is_cheribsd or (self.target_info.is_baremetal and self.target_info.is_newlib)
            self._cmakeTemplate = includeLocalFile("files/CheriBSDToolchain.cmake.in")
            self.toolchainFile = self.buildDir / "CheriBSDToolchain.cmake"
            self.add_cmake_options(CMAKE_TOOLCHAIN_FILE=self.toolchainFile)
        # The toolchain files need at least CMake 3.6
        self.set_minimum_cmake_version(3, 7)

    def _prepare_toolchain_file(self, **kwargs):
        configured_template = self._cmakeTemplate
        for key, value in kwargs.items():
            if value is None:
                continue
            if isinstance(value, bool):
                strval = "1" if value else "0"
            elif isinstance(value, list):
                strval = commandline_to_str(value)
            else:
                strval = str(value)
            assert "@" + key + "@" in configured_template, key
            configured_template = configured_template.replace("@" + key + "@", strval)
        # work around jenkins paths that might contain @[0-9]+ in the path:
        configured_jenkins_workaround = re.sub(r"@\d+", "", configured_template)
        assert "@" not in configured_jenkins_workaround, configured_jenkins_workaround
        self.writeFile(contents=configured_template, file=self.toolchainFile, overwrite=True)

    def configure(self, **kwargs):
        if not self.compiling_for_host():
            self.COMMON_FLAGS.append("-B" + str(self.sdk_bindir))

        if self.crosscompile_target.is_cheri_purecap():
            if self._get_cmake_version() < (3, 9, 0) and not (self.sdk_sysroot / "usr/local/lib/cheri").exists():
                warningMessage("Workaround for missing custom lib suffix in CMake < 3.9")
                self.makedirs(self.sdk_sysroot / "usr/lib")
                # create a /usr/lib/cheri -> /usr/libcheri symlink so that cmake can find the right libraries
                self.createSymlink(Path("../libcheri"), self.sdk_sysroot / "usr/lib/cheri", relative=True,
                    cwd=self.sdk_sysroot / "usr/lib")
                self.makedirs(self.sdk_sysroot / "usr/local/cheri/lib")
                self.makedirs(self.sdk_sysroot / "usr/local/cheri/libcheri")
                self.createSymlink(Path("../libcheri"), self.sdk_sysroot / "usr/local/cheri/lib/cheri",
                    relative=True, cwd=self.sdk_sysroot / "usr/local/cheri/lib")
            add_lib_suffix = """
# cheri libraries are found in /usr/libcheri:
if("${CMAKE_VERSION}" VERSION_LESS 3.9)
  # message(STATUS "CMAKE < 3.9 HACK to find libcheri libraries")
  # need to create a <sysroot>/usr/lib/cheri -> <sysroot>/usr/libcheri symlink 
  set(CMAKE_LIBRARY_ARCHITECTURE "cheri")
  set(CMAKE_SYSTEM_LIBRARY_PATH "${CMAKE_FIND_ROOT_PATH}/usr/libcheri;${
  CMAKE_FIND_ROOT_PATH}/usr/local/cheri/lib;${CMAKE_FIND_ROOT_PATH}/usr/local/cheri/libcheri")
else()
    set(CMAKE_FIND_LIBRARY_CUSTOM_LIB_SUFFIX "cheri")
endif()
set(LIB_SUFFIX "cheri" CACHE INTERNAL "")
"""
        else:
            if self.compiling_for_host():
                add_lib_suffix = None
            else:
                add_lib_suffix = "# no lib suffix needed for non-purecap"

        # TODO: always avoid the toolchain file?
        if self.compiling_for_host():
            self.add_cmake_options(
                CMAKE_C_COMPILER=self.CC,
                CMAKE_CXX_COMPILER=self.CXX,
                CMAKE_ASM_COMPILER=self.CC,  # Compile assembly files with the default compiler
                CMAKE_C_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.CFLAGS),
                CMAKE_CXX_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.CXXFLAGS),
                CMAKE_ASM_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.ASMFLAGS),
                )
            custom_ldflags = commandline_to_str(self.LDFLAGS + self.default_ldflags)
            if custom_ldflags:
                self.add_cmake_options(
                    CMAKE_EXE_LINKER_FLAGS_INIT=custom_ldflags,
                    CMAKE_SHARED_LINKER_FLAGS_INIT=custom_ldflags,
                    CMAKE_MODULE_LINKER_FLAGS_INIT=custom_ldflags)
        else:
            # CMAKE_CROSSCOMPILING will be set when we change CMAKE_SYSTEM_NAME:
            # This means we may not need the toolchain file at all
            # https://cmake.org/cmake/help/latest/variable/CMAKE_CROSSCOMPILING.html
            system_name = "Generic" if self.target_info.is_baremetal else "FreeBSD"
            self._prepare_toolchain_file(
                TOOLCHAIN_SDK_BINDIR=self.sdk_bindir if not self.compiling_for_host() else self.config.cheri_sdk_bindir,
                TOOLCHAIN_COMPILER_BINDIR=self.CC.parent,
                TOOLCHAIN_TARGET_TRIPLE=self.target_info.target_triple,
                TOOLCHAIN_COMMON_FLAGS=self.default_compiler_flags,
                TOOLCHAIN_C_FLAGS=self.CFLAGS,
                TOOLCHAIN_LINKER_FLAGS=self.LDFLAGS + self.default_ldflags,
                TOOLCHAIN_CXX_FLAGS=self.CXXFLAGS,
                TOOLCHAIN_ASM_FLAGS=self.ASMFLAGS,
                TOOLCHAIN_C_COMPILER=self.CC,
                TOOLCHAIN_CXX_COMPILER=self.CXX,
                TOOLCHAIN_SYSROOT=self.sdk_sysroot,
                ADD_TOOLCHAIN_LIB_SUFFIX=add_lib_suffix,
                TOOLCHAIN_SYSTEM_PROCESSOR=self.target_info.cmake_processor_id,
                TOOLCHAIN_SYSTEM_NAME=system_name,
                TOOLCHAIN_PKGCONFIG_DIRS=self.target_info.pkgconfig_dirs,
                TOOLCHAIN_FORCE_STATIC=self.force_static_linkage,
                )
        if self.force_static_linkage:
            self.add_cmake_options(
                CMAKE_SHARED_LIBRARY_SUFFIX=".a",
                CMAKE_FIND_LIBRARY_SUFFIXES=".a",
                CMAKE_EXTRA_SHARED_LIBRARY_SUFFIXES=".a")
        if not self.compiling_for_host() and self.generator == CMakeProject.Generator.Ninja:
            # Ninja can't change the RPATH when installing: https://gitlab.kitware.com/cmake/cmake/issues/13934
            # TODO: remove once it has been fixed
            self.add_cmake_options(CMAKE_BUILD_WITH_INSTALL_RPATH=True)
        if self.target_info.is_baremetal and not self.compiling_for_host():
            self.add_cmake_options(CMAKE_EXE_LINKER_FLAGS="-Wl,-T,qemu-malta.ld")
        # TODO: BUILD_SHARED_LIBS=OFF?
        super().configure(**kwargs)


class CrossCompileAutotoolsProject(CrossCompileMixin, AutotoolsProject):
    doNotAddToTargets = True  # only used as base class

    add_host_target_build_config_options = True
    _configure_supports_libdir = True  # override in nginx
    _configure_supports_variables_on_cmdline = True  # override in nginx
    _configure_understands_enable_static = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        buildhost = self.get_host_triple()
        if not self.compiling_for_host() and self.add_host_target_build_config_options:
            autotools_triple = self.target_info.target_triple
            # Most scripts don't like the final -purecap component:
            autotools_triple = autotools_triple.replace("-purecap", "")
            # TODO: do we have to remove these too?
            #autotools_triple = autotools_triple.replace("mips64c128-", "cheri-")
            #autotools_triple = autotools_triple.replace("mips64c256-", "cheri-")
            self.configureArgs.extend(["--host=" + autotools_triple, "--target=" + autotools_triple,
                                       "--build=" + buildhost])

    def add_configure_env_arg(self, arg: str, value: "typing.Union[str,Path]"):
        if not value:
            return
        assert not isinstance(value, list), ("Wrong type:", type(value))
        assert not isinstance(value, tuple), ("Wrong type:", type(value))
        self.configureEnvironment[arg] = str(value)
        if self._configure_supports_variables_on_cmdline:
            self.configureArgs.append(arg + "=" + str(value))

    def add_configure_vars(self, **kwargs):
        for k, v in kwargs.items():
            self.add_configure_env_arg(k, v)

    def set_prog_with_args(self, prog: str, path: Path, args: list):
        fullpath = str(path)
        if args:
            fullpath += " " + commandline_to_str(args)
        self.configureEnvironment[prog] = fullpath
        if self._configure_supports_variables_on_cmdline:
            self.configureArgs.append(prog + "=" + fullpath)

    def configure(self, **kwargs):
        if self._configure_understands_enable_static:     # workaround for nginx which isn't really autotools
            if self.force_static_linkage:
                self.configureArgs.extend(["--enable-static", "--disable-shared"])
            elif self.force_dynamic_linkage:
                self.configureArgs.extend(["--disable-static", "--enable-shared"])
            # Otherwise just let the project decide
            # else:
            #    self.configureArgs.extend(["--enable-static", "--enable-shared"])

        # target triple contains a number suffix -> remove it when computing the compiler name
        if self.compiling_for_cheri() and self._configure_supports_libdir:
            # nginx configure script doesn't understand --libdir
            # make sure that we install to the right directory
            # TODO: can we use relative paths?
            self.configureArgs.append("--libdir=" + str(self.installPrefix) + "/libcheri")

        if not self.target_info.is_baremetal:
            CPPFLAGS = self.default_compiler_flags
            for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
                assert key not in self.configureEnvironment
            # autotools overrides CFLAGS -> use CC and CXX vars here
            self.set_prog_with_args("CC", self.CC, CPPFLAGS + self.CFLAGS)
            self.set_prog_with_args("CXX", self.CXX, CPPFLAGS + self.CXXFLAGS)
            # self.add_configure_env_arg("CPPFLAGS", commandline_to_str(CPPFLAGS))
            self.add_configure_env_arg("CFLAGS", commandline_to_str(self.default_compiler_flags))
            self.add_configure_env_arg("CXXFLAGS", commandline_to_str(self.default_compiler_flags))
            # this one seems to work:
            self.add_configure_env_arg("LDFLAGS", commandline_to_str(self.LDFLAGS + self.default_ldflags))

            if not self.compiling_for_host():
                self.set_prog_with_args("CPP", self.CPP, CPPFLAGS)
                self.add_configure_env_arg("LD", self.target_info.linker)

        # remove all empty items from environment:
        env = {k: v for k, v in self.configureEnvironment.items() if v}
        self.configureEnvironment.clear()
        self.configureEnvironment.update(env)
        self.print(coloured(AnsiColour.yellow, "Cross configure environment:",
                            pprint.pformat(self.configureEnvironment, width=160)))
        super().configure(**kwargs)

    def process(self):
        if not self.compiling_for_host():
            # We run all these commands with $PATH containing $CHERI_SDK/bin to ensure the right tools are used
            with setEnv(PATH=str(self.sdk_bindir) + ":" + os.getenv("PATH")):
                super().process()
        else:
            # when building the native target we just rely on the host tools in /usr/bin
            super().process()
