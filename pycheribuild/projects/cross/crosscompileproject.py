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
from enum import Enum, auto
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
    config = None  # type: CheriConfig
#    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY  # fake install inside the build directory
#    cross_install_dir = DefaultInstallDir.ROOTFS
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS

    # noinspection PyTypeChecker
    default_build_type = BuildType.DEFAULT
    forceDefaultCC = False  # If true fall back to /usr/bin/cc there
    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    _should_not_be_instantiated = True
    defaultOptimizationLevel = ("-O2",)
    can_build_with_asan = True

    # noinspection PyProtectedMember
    @property
    def _no_overwrite_allowed(self) -> "typing.Tuple[str]":
        assert isinstance(self, SimpleProject)
        return super()._no_overwrite_allowed + ("baremetal",)

    needs_mxcaptable_static = False     # E.g. for postgres which is just over the limit:
    #﻿warning: added 38010 entries to .cap_table but current maximum is 32768; try recompiling non-performance critical source files with -mllvm -mxcaptable
    # FIXME: postgres would work if I fixed captable to use the negative immediate values
    needs_mxcaptable_dynamic = False    # This might be true for Qt/QtWebkit

    @property
    def baremetal(self):
        return self.target_info.is_baremetal

    @property
    def compiler_warning_flags(self):
        if self.compiling_for_host():
            return self.common_warning_flags + self.host_warning_flags
        else:
            return self.common_warning_flags + self.cross_warning_flags

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if self.cross_build_type in (BuildType.DEBUG, BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO):
            assert self.include_debug_info, "Need --" + self.target + "/debug-info if build-type is " + str(self.cross_build_type.value)
        # convert the tuples into mutable lists (this is needed to avoid modifying class variables)
        # See https://github.com/CTSRD-CHERI/cheribuild/issues/33
        self.defaultOptimizationLevel = list(self.defaultOptimizationLevel)
        self.cross_warning_flags = ["-Wall", "-Werror=cheri-capability-misuse", "-Werror=implicit-function-declaration",
                                    "-Werror=format", "-Werror=undefined-internal", "-Werror=incompatible-pointer-types",
                                    "-Werror=mips-cheri-prototypes", "-Werror=cheri-bitwise-operations"]
        # Make underaligned capability loads/stores an error and require an explicit cast:
        self.cross_warning_flags.append("-Werror=pass-failed")
        self.host_warning_flags = []
        self.common_warning_flags = []

        target_arch = self._crossCompileTarget
        # sanity check:
        assert target_arch is not None and target_arch is not CompilationTargets.NONE
        assert self.get_crosscompile_target(config) is target_arch
        assert isinstance(target_arch, CrossCompileTarget)
        # compiler flags:
        self.COMMON_FLAGS = self.target_info.required_compile_flags()
        if target_arch.is_cheri_purecap([CPUArchitecture.MIPS64]) and self.force_static_linkage:
            # clang currently gets the TLS model wrong:
            # https://github.com/CTSRD-CHERI/cheribsd/commit/f863a7defd1bdc797712096b6778940cfa30d901
            self.COMMON_FLAGS.append("-ftls-model=initial-exec")
            # TODO: remove the data-depedent provenance flag:
            if self.should_use_extra_c_compat_flags():
                self.COMMON_FLAGS.extend(self.extra_c_compat_flags)  # include cap-table-abi flags

        # We might be setting too many flags, ignore this (for now)
        if not self.compiling_for_host():
            self.COMMON_FLAGS.append("-Wno-unused-command-line-argument")

        assert self.installDir, "must be set"
        statusUpdate(self.target, "INSTALLDIR = ", self._installDir, "INSTALL_PREFIX=", self._installPrefix,
                     "DESTDIR=", self.destdir)

        if self.include_debug_info:
            if not self.target_info.is_macos:
                self.COMMON_FLAGS.append("-ggdb")
        self.CFLAGS = []
        self.CXXFLAGS = []
        self.ASMFLAGS = []
        self.LDFLAGS = []
        self.COMMON_LDFLAGS = []
        # Don't build CHERI with ASAN since that doesn't work or make much sense
        if self.use_asan and not self.compiling_for_cheri():
            self.COMMON_FLAGS.append("-fsanitize=address")
            self.COMMON_LDFLAGS.append("-fsanitize=address")

    def should_use_extra_c_compat_flags(self):
        # TODO: add a command-line option and default to true for
        return self.compiling_for_cheri() and self.baremetal

    @property
    def extra_c_compat_flags(self):
        if not self.compiling_for_cheri():
            return []
        # Build with virtual address interpretation, data-dependent provenance and pcrelative captable ABI
        return ["-cheri-uintcap=addr", "-Xclang", "-cheri-data-dependent-provenance"]

    @property
    def optimizationFlags(self):
        cbt = self.cross_build_type
        if cbt == BuildType.DEFAULT:
            return self.defaultOptimizationLevel + self._optimizationFlags
        elif cbt == BuildType.DEBUG:
            return ["-O0"] + self._optimizationFlags
        elif cbt in (BuildType.RELEASE, BuildType.RELWITHDEBINFO):
            return ["-O2"] + self._optimizationFlags
        elif cbt in (BuildType.MINSIZEREL, BuildType.MINSIZERELWITHDEBINFO):
            return ["-Os"] + self._optimizationFlags

    @property
    def default_compiler_flags(self):
        result = []
        if self.use_lto:
            result.append("-flto")
        if self.use_cfi:
            if not self.use_lto:
                self.fatal("Cannot use CFI without LTO!")
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
            result.append("-fvisibility=hidden")
        if self.compiling_for_host():
            return result + self.COMMON_FLAGS + self.compiler_warning_flags
        result += self.target_info.essential_compiler_and_linker_flags + self.optimizationFlags
        result += self.COMMON_FLAGS + self.compiler_warning_flags
        if self.config.csetbounds_stats:
            result.extend(["-mllvm", "-collect-csetbounds-output=" + str(self.csetbounds_stats_file),
                           "-mllvm", "-collect-csetbounds-stats=csv",
                           # "-Xclang", "-cheri-bounds=everywhere-unsafe"])
                           "-Xclang", "-cheri-bounds=aggressive"])
        # Add mxcaptable for projects that need it
        if self.compiling_for_cheri() and self.config.cheri_cap_table_abi != "legacy":
            if self.force_static_linkage and self.needs_mxcaptable_static:
                result.append("-mxcaptable")
            if self.force_dynamic_linkage and self.needs_mxcaptable_dynamic:
                result.append("-mxcaptable")
        # Do the same for MIPS to get even performance comparisons
        elif self.compiling_for_mips(include_purecap=False):
            if self.force_static_linkage and self.needs_mxcaptable_static:
                result.extend(["-mxgot", "-mllvm", "-mxmxgot"])
            if self.force_dynamic_linkage and self.needs_mxcaptable_dynamic:
                result.extend(["-mxgot", "-mllvm", "-mxmxgot"])
        return result

    @property
    def default_ldflags(self):
        result = list(self.COMMON_LDFLAGS)
        if self.force_static_linkage:
            result.append("-static")
        if self.use_lto:
            result.append("-flto")
        if self.use_cfi:
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
        if self.compiling_for_host():
            return result

        # Should work fine without linker emulation (the linker should infer it from input files)
        # if self.compiling_for_cheri():
        #     emulation = "elf64btsmip_cheri_fbsd" if not self.baremetal else "elf64btsmip_cheri"
        # elif self.compiling_for_mips(include_purecap=False):
        #     emulation = "elf64btsmip_fbsd" if not self.baremetal else "elf64btsmip"
        # result.append("-Wl,-m" + emulation)
        result += self.target_info.essential_compiler_and_linker_flags + [
            "-fuse-ld=" + str(self.target_info.linker),
            # Should no longer be needed now that I added a hack for .eh_frame
            # "-Wl,-z,notext",  # needed so that LLD allows text relocations
            ]
        if self.include_debug_info and not ".bfd" in self.target_info.linker.name:
            # Add a gdb_index to massively speed up running GDB on CHERIBSD:
            result.append("-Wl,--gdb-index")
        if self.target_info.is_cheribsd and self.config.withLibstatcounters:
            # We need to include the constructor even if there is no reference to libstatcounters:
            # TODO: always include the .a file?
            result += ["-Wl,--whole-archive", "-lstatcounters", "-Wl,--no-whole-archive"]
        return result

    @classmethod
    def setup_config_options(cls, **kwargs):
        assert issubclass(cls, SimpleProject)
        super().setup_config_options(**kwargs)
        cls._optimizationFlags = cls.add_config_option("optimization-flags", kind=list, metavar="OPTIONS",
                                                     default=[])
        cls.cross_build_type = cls.add_config_option("cross-build-type",
            help="Optimization+debuginfo defaults (supports the same values as CMake plus 'DEFAULT' which does not pass"
                 " any additional flags to the configure script). Note: The overrides the CMake --build-type option.",
            default=cls.default_build_type, kind=BuildType, enum_choice_strings=[t.value for t in BuildType])

    @property
    def include_debug_info(self) -> bool:
        force_debug_info = getattr(self, "_force_debug_info", None)
        if force_debug_info is not None:
            return force_debug_info
        return self.cross_build_type.should_include_debug_info

    def configure(self, **kwargs):
        env = dict()
        if not self.compiling_for_host():
            env.update(PKG_CONFIG_LIBDIR=self.target_info.pkgconfig_dirs, PKG_CONFIG_SYSROOT_DIR=self.crossSysrootPath)
        with setEnv(**env):
            super().configure(**kwargs)


class CrossCompileProject(CrossCompileMixin, Project):
    doNotAddToTargets = True


class CrossCompileCMakeProject(CrossCompileMixin, CMakeProject):
    doNotAddToTargets = True  # only used as base class
    defaultCMakeBuildType = "RelWithDebInfo"  # default to O2

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

    def __init__(self, config: CheriConfig, generator: CMakeProject.Generator=CMakeProject.Generator.Ninja):
        if self.cross_build_type != BuildType.DEFAULT:
            # no CMake equivalent for MinSizeRelWithDebInfo -> set minsizerel and force debug info
            if self.cross_build_type == BuildType.MINSIZERELWITHDEBINFO:
                self.cmakeBuildType = BuildType.MINSIZEREL.value
                self._force_debug_info = True
            else:
                self.cmakeBuildType = self.cross_build_type.value
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
            system_name = "Generic" if self.baremetal else "FreeBSD"
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
        if self.baremetal and not self.compiling_for_host():
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

        if not self.baremetal:
            CPPFLAGS = self.default_compiler_flags
            for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
                assert key not in self.configureEnvironment
            # autotools overrides CFLAGS -> use CC and CXX vars here
            self.set_prog_with_args("CC", self.CC, CPPFLAGS + self.CFLAGS)
            self.set_prog_with_args("CXX", self.CXX, CPPFLAGS + self.CXXFLAGS)
            # self.add_configure_env_arg("CPPFLAGS", commandline_to_str(CPPFLAGS))
            self.add_configure_env_arg("CFLAGS", commandline_to_str(self.optimizationFlags + self.compiler_warning_flags))
            self.add_configure_env_arg("CXXFLAGS", commandline_to_str(self.optimizationFlags + self.compiler_warning_flags))
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
