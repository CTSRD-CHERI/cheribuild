#
# Copyright (c) 2016 Alex Richardson
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
import shlex
import shutil
import subprocess
import typing
from pathlib import Path
from typing import Optional, Sequence

from .simple_project import _default_stdout_filter
from .project import _CMakeAndMesonSharedLogic, MakeCommandKind, Project
from ..config.chericonfig import BuildType
from ..processutils import commandline_to_str, run_command
from ..targets import target_manager
from ..utils import include_local_file, InstallInstructions, OSInfo

__all__ = ["CMakeProject"]  # no-combine


class CMakeProject(_CMakeAndMesonSharedLogic):
    """
    Like Project but automatically sets up the defaults for CMake projects
    Sets configure command to CMake, adds -DCMAKE_INSTALL_PREFIX=installdir
    and checks that CMake is installed
    """
    do_not_add_to_targets: bool = True
    compile_db_requires_bear: bool = False  # cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON does it
    generate_cmakelists: bool = False  # There is already a CMakeLists.txt
    make_kind: MakeCommandKind = MakeCommandKind.CMake
    _default_cmake_generator_arg: str = "-GNinja"  # We default to using the Ninja generator since it's faster
    _configure_tool_name: str = "CMake"
    default_build_type: BuildType = BuildType.RELWITHDEBINFO
    # Some projects (e.g. LLVM) don't store the CMakeLists.txt in the project root directory.
    root_cmakelists_subdirectory: Optional[Path] = None
    ctest_script_extra_args: Sequence[str] = tuple()
    # 3.13.4 is the minimum version for LLVM and that also allows us to use "cmake --build -j <N>" unconditionally.
    _minimum_cmake_or_meson_version: "tuple[int, ...]" = (3, 13, 4)

    def _toolchain_file_list_to_str(self, value: list) -> str:
        assert isinstance(value, list), f"Expected a list and not {type(value)}: {value}"
        return ";".join(map(str, value))

    def _toolchain_file_command_args_to_str(self, value: _CMakeAndMesonSharedLogic.CommandLineArgs) -> str:
        return commandline_to_str(value.args)

    def _toolchain_file_env_var_path_list_to_str(self, value: _CMakeAndMesonSharedLogic.EnvVarPathList) -> str:
        # We store the raw ':'-separated list in the CMake toolchain file since it's also set using set(ENV{FOO} ...)
        return ":".join(map(str, value.paths))

    def _bool_to_str(self, value: bool) -> str:
        return "TRUE" if value else "FALSE"

    def _configure_tool_install_instructions(self) -> InstallInstructions:
        return OSInfo.install_instructions("cmake", False, default="cmake", homebrew="cmake", zypper="cmake",
                                           apt="cmake", freebsd="cmake", cheribuild_target="cmake")

    @property
    def _get_version_args(self) -> dict:
        return dict(program_name=b"cmake")

    @property
    def _build_type_basic_compiler_flags(self):
        # No need to add any flags here, cmake does it for us already
        return []

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        super().setup_config_options(**kwargs)
        cls.cmake_options = cls.add_config_option("cmake-options", default=[], kind=list, metavar="OPTIONS",
                                                  help="Additional command line options to pass to CMake")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.configure_command = os.getenv("CMAKE_COMMAND", None)
        if self.configure_command is None:
            self.configure_command = "cmake"
        # allow a -G flag in cmake-options to override the default generator (Ninja).
        custom_generator = next((x for x in self.cmake_options if x.startswith("-G")), None)
        generator = custom_generator if custom_generator else self._default_cmake_generator_arg
        self.ctest_environment: "dict[str, str]" = {}
        self.configure_args.append(generator)
        self.build_type_var_suffix = ""
        if "Ninja" in generator:
            self.make_args.subkind = MakeCommandKind.Ninja
            self.check_required_system_tool("ninja", homebrew="ninja", apt="ninja-build")
        elif "Makefiles" in generator:
            self.make_args.subkind = MakeCommandKind.DefaultMake
            self.check_required_system_tool("make")
        else:
            self.make_args.subkind = MakeCommandKind.CustomMakeTool  # VS/XCode, etc.
        self._toolchain_file: "Optional[Path]" = None
        if not self.compiling_for_host():
            self._toolchain_template = include_local_file("files/CrossToolchain.cmake.in")
            self._toolchain_file = self.build_dir / "CrossToolchain.cmake"

    def setup(self) -> None:
        super().setup()
        # CMake 3.13+ supports explicit source+build dir arguments
        cmakelists_dir = self.source_dir
        if self.root_cmakelists_subdirectory is not None:
            assert not self.root_cmakelists_subdirectory.is_absolute()
            cmakelists_dir = self.source_dir / self.root_cmakelists_subdirectory
        if self._get_configure_tool_version() >= (3, 13):
            self.configure_args.extend(["-S", str(cmakelists_dir), "-B", str(self.build_dir)])
        else:
            self.configure_args.append(str(cmakelists_dir))
        if self.build_type != BuildType.DEFAULT:
            if self.build_type == BuildType.MINSIZERELWITHDEBINFO:
                # no CMake equivalent for MinSizeRelWithDebInfo -> set MinSizeRel and force debug info
                self._force_debug_info = True
                self.add_cmake_options(CMAKE_BUILD_TYPE=BuildType.MINSIZEREL.value)
                self.build_type_var_suffix = "_" + BuildType.MINSIZEREL.value.upper()
            else:
                self.add_cmake_options(CMAKE_BUILD_TYPE=self.build_type.value)
                self.build_type_var_suffix = "_" + self.build_type.value.upper()
        if self.config.create_compilation_db:
            # TODO: always generate it?
            self.configure_args.append("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
        if self.compiling_for_host():
            # When building natively, pass arguments on the command line instead of using the toolchain file.
            # This makes it a lot easier to reproduce the builds outside cheribuild.
            self.add_cmake_options(CMAKE_PREFIX_PATH=self._toolchain_file_list_to_str(self.cmake_prefix_paths))
        else:
            self.add_cmake_options(CMAKE_TOOLCHAIN_FILE=self._toolchain_file)

        if self.install_prefix != self.install_dir:
            assert self.destdir, "custom install prefix requires DESTDIR being set!"
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.install_prefix)
            if self.target_info.is_baremetal() and str(self.install_prefix) == "/":
                self.add_cmake_options(CMAKE_INSTALL_INCLUDEDIR="/include"),  # Don't add the extra /usr in the sysroot
        else:
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.install_dir)
        if not self.compiling_for_host():
            # TODO: set CMAKE_STRIP, CMAKE_NM, CMAKE_OBJDUMP, CMAKE_READELF, CMAKE_DLLTOOL, CMAKE_DLLTOOL,
            #  CMAKE_ADDR2LINE
            self.add_cmake_options(
                _CMAKE_TOOLCHAIN_LOCATION=self.target_info.sdk_root_dir / "bin",
                CMAKE_LINKER=self.target_info.linker)

        if self.target_info.additional_executable_link_flags:
            self.add_cmake_options(
                CMAKE_REQUIRED_LINK_OPTIONS=commandline_to_str(self.target_info.additional_executable_link_flags))
            # TODO: if this doesn't work we can set CMAKE_TRY_COMPILE_TARGET_TYPE to build a static lib instead
            # https://cmake.org/cmake/help/git-master/variable/CMAKE_TRY_COMPILE_TARGET_TYPE.html
            # XXX: we should have everything set up correctly so this should no longer be needed for FreeBSD
            if self.target_info.is_baremetal():
                self.add_cmake_options(CMAKE_TRY_COMPILE_TARGET_TYPE="STATIC_LIBRARY")
        if self.force_static_linkage:
            self.add_cmake_options(
                CMAKE_SHARED_LIBRARY_SUFFIX=".a",
                CMAKE_FIND_LIBRARY_SUFFIXES=".a",
                CMAKE_EXTRA_SHARED_LIBRARY_SUFFIXES=".a")
        else:
            # Use $ORIGIN in the build RPATH (this should make it easier to run tests without having the absolute
            # build directory mounted).
            self.add_cmake_options(CMAKE_BUILD_RPATH_USE_ORIGIN=True)
            # Infer the RPATH needed for each executable.
            self.add_cmake_options(CMAKE_INSTALL_RPATH_USE_LINK_PATH=True)
            # CMake does not add the install directory even if it's a non-default location, so add it manually.
            self.add_cmake_options(CMAKE_INSTALL_RPATH="$ORIGIN/../lib")
        if not self.compiling_for_host() and self.make_args.subkind == MakeCommandKind.Ninja:
            # Ninja can't change the RPATH when installing: https://gitlab.kitware.com/cmake/cmake/issues/13934
            # Fixed in https://gitlab.kitware.com/cmake/cmake/-/merge_requests/6240 (3.21.20210625)
            self.add_cmake_options(
                CMAKE_BUILD_WITH_INSTALL_RPATH=self._get_configure_tool_version() < (3, 21, 20210625))
        # NB: Don't add the user provided options here, we append add them in setup_late() so that they are put last.

    def setup_late(self):
        super().setup_late()
        custom_ldflags = self.default_ldflags + self.LDFLAGS
        self.add_cmake_options(
            CMAKE_C_COMPILER=self.CC,
            CMAKE_CXX_COMPILER=self.CXX,
            CMAKE_ASM_COMPILER=self.CC,  # Compile assembly files with the default compiler
            # All of these should be commandlines not CMake lists:
            CMAKE_C_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.CFLAGS),
            CMAKE_CXX_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.CXXFLAGS),
            CMAKE_ASM_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.ASMFLAGS),
            CMAKE_EXE_LINKER_FLAGS_INIT=commandline_to_str(
                custom_ldflags + self.target_info.additional_executable_link_flags),
            CMAKE_SHARED_LINKER_FLAGS_INIT=commandline_to_str(
                custom_ldflags + self.target_info.additional_shared_library_link_flags),
            CMAKE_MODULE_LINKER_FLAGS_INIT=commandline_to_str(
                custom_ldflags + self.target_info.additional_shared_library_link_flags),
        )
        if self.optimization_flags:
            # If the project uses custom optimization flags (e.g. SPEC), override the CMake defaults defined in
            # Modules/Compiler/GNU.cmake. Just adding them to CMAKE_<LANG>_FLAGS_INIT is not enough since the
            # CMAKE_<LANG>_FLAGS_<CONFIG>_INIT and  CMAKE_<LANG>_FLAGS variables will be appended and override the
            # optimization flags that we passed as part of CMAKE_<LANG>_FLAGS_INIT.
            flags = " " + commandline_to_str(self.optimization_flags)
            if self.build_type.is_release:
                flags += " -DNDEBUG"
            self.add_cmake_options(**{f"CMAKE_C_FLAGS{self.build_type_var_suffix}": flags,
                                      f"CMAKE_CXX_FLAGS{self.build_type_var_suffix}": flags})
        # Add the options from the config file now so that they are added after child class setup() calls.
        self.configure_args.extend(self.cmake_options)

    def add_cmake_options(self, *, _include_empty_vars=False, _replace=True, **kwargs) -> None:
        return self._add_configure_options(_config_file_options=self.cmake_options, _replace=_replace,
                                           _include_empty_vars=_include_empty_vars, **kwargs)

    def set_minimum_cmake_version(self, major: int, minor: int, patch: int = 0) -> None:
        new_version = (major, minor, patch)
        assert self._minimum_cmake_or_meson_version is None or new_version >= self._minimum_cmake_or_meson_version
        self._minimum_cmake_or_meson_version = new_version

    def _cmake_install_stdout_filter(self, line: bytes) -> None:
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        self._show_line_stdout_filter(line)

    def set_lto_binutils(self, ar, ranlib, nm, ld) -> None:
        # LD is never invoked directly, so the -fuse-ld=/--ld-path flag is sufficient
        self.add_cmake_options(CMAKE_AR=ar, CMAKE_RANLIB=ranlib)

    def needs_configure(self) -> bool:
        if self.config.pretend and (self.force_configure or self.with_clean):
            return True
        # CMake is smart enough to detect when it must be reconfigured -> skip configure if cache exists
        cmake_cache = self.build_dir / "CMakeCache.txt"
        assert self.make_args.kind == MakeCommandKind.CMake
        build_file = "build.ninja" if self.make_args.subkind == MakeCommandKind.Ninja else "Makefile"
        return not cmake_cache.exists() or not (self.build_dir / build_file).exists()

    def generate_cmake_toolchain_file(self, file: Path) -> None:
        # CMAKE_CROSSCOMPILING will be set when we change CMAKE_SYSTEM_NAME:
        # This means we may not need the toolchain file at all
        # https://cmake.org/cmake/help/latest/variable/CMAKE_CROSSCOMPILING.html
        # TODO: avoid the toolchain file and set all flags on the command line
        self._prepare_toolchain_file_common(file, TOOLCHAIN_FORCE_STATIC=self.force_static_linkage,
                                            TOOLCHAIN_FILE_PATH=file.absolute())

    def configure(self, **kwargs) -> None:
        # make sure we get a completely fresh cache when --reconfigure is passed:
        cmake_cache = self.build_dir / "CMakeCache.txt"
        if self.force_configure:
            self.delete_file(cmake_cache)
        if self._toolchain_file is not None:
            self.generate_cmake_toolchain_file(self._toolchain_file)
        super().configure(**kwargs)
        if self.config.copy_compilation_db_to_source_dir and (self.build_dir / "compile_commands.json").exists():
            self.install_file(self.build_dir / "compile_commands.json", self.source_dir / "compile_commands.json",
                              force=True)

    def install(self, _stdout_filter=_default_stdout_filter) -> None:
        if _stdout_filter is _default_stdout_filter:
            _stdout_filter = self._cmake_install_stdout_filter
        super().install(_stdout_filter=_stdout_filter)

    def run_tests(self) -> None:
        if (self.build_dir / "CTestTestfile.cmake").exists() or self.config.pretend:
            # We can run tests using CTest
            if self.compiling_for_host():
                self.run_cmd(shutil.which(os.getenv("CTEST_COMMAND", "ctest")) or "ctest", "-V", "--output-on-failure",
                             cwd=self.build_dir, env=self.ctest_environment)
            else:
                try:
                    cmake_xtarget = self.crosscompile_target
                    # Use a string here instead of BuildCrossCompiledCMake to avoid a cyclic import.
                    cmake_target = target_manager.get_target("cmake-crosscompiled", cmake_xtarget, self.config, self)
                    cmake_project = cmake_target.project_class.get_instance(self, cross_target=cmake_xtarget)
                    expected_ctest_path = cmake_project.install_dir / "bin/ctest"
                    if not expected_ctest_path.is_file():
                        self.dependency_error(f"cannot find CTest binary ({expected_ctest_path}) to run tests.",
                                              cheribuild_target=cmake_project.target, cheribuild_xtarget=cmake_xtarget)
                    # --output-junit needs version 3.21
                    min_version = "3.21"
                    if not list(cmake_project.install_dir.glob("share/*/Help/release/" + min_version + ".rst")):
                        self.dependency_error("cannot find release notes for CMake", min_version,
                                              "- installed CMake version is too old",
                                              cheribuild_target=cmake_project.target, cheribuild_xtarget=cmake_xtarget)
                except LookupError:
                    self.warning("Do not know how to cross-compile CTest for", self.target_info, "-> cannot run tests")
                    return
                args = ["--cmake-install-dir", str(cmake_project.install_dir)]
                for var, value in self.ctest_environment.items():
                    args.append("--test-setup-command=export " + shlex.quote(var + "=" + value))
                args.extend(self.ctest_script_extra_args)
                self.target_info.run_cheribsd_test_script("run_ctest_tests.py", *args, mount_builddir=True,
                                                          mount_sysroot=True, mount_sourcedir=True,
                                                          use_full_disk_image=self.tests_need_full_disk_image)
        else:
            if self.has_optional_tests:
                self.fatal("Can't run tests for projects that were built with tests disabled. ",
                           "Please re-run build the target with --", self.get_config_option_name("build_tests"), sep="")
            self.warning("Do not know how to run tests for", self.target)

    @staticmethod
    def find_package(name: str) -> bool:
        try:
            cmd = "cmake --find-package -DCOMPILER_ID=Clang -DLANGUAGE=CXX -DMODE=EXIST -DQUIET=TRUE".split()
            cmd.append("-DNAME=" + name)
            return run_command(cmd).returncode == 0
        except subprocess.CalledProcessError:
            return False


class MakefileProject(Project):
    """A very simple project that just set some defualt variables such as CC/CXX, etc"""
    do_not_add_to_targets: bool = True
    build_in_source_dir: bool = True  # Most makefile projects don't support out-of-source builds
    # Default to GNU make since that's what most makefile projects require.
    make_kind: MakeCommandKind = MakeCommandKind.GnuMake
    _define_ld: bool = False
    set_commands_on_cmdline: bool = False  # Set variables such as CC/CXX on the command line instead of the environment

    def setup(self) -> None:
        super().setup()
        # Most projects expect that a plain $CC foo.c will work, so we include the -target, etc. flags in CC
        essential_flags = self.essential_compiler_and_linker_flags
        self.set_make_cmd_with_args("CC", self.CC, essential_flags)
        self.set_make_cmd_with_args("CPP", self.CPP, essential_flags)
        self.set_make_cmd_with_args("CXX", self.CXX, essential_flags)
        self.set_make_cmd_with_args("CCLD", self.CC, essential_flags)
        self.set_make_cmd_with_args("CXXLD", self.CXX, essential_flags)
        self.make_args.set_env(AR=self.target_info.ar)

        # Some projects expect LD to be CCLD others really mean the raw linker
        if self._define_ld:
            self.make_args.set_env(LD=self.target_info.linker)

        # Set values in the environment so that projects can override them
        cppflags = self.default_compiler_flags
        self.make_args.set_env(
            CFLAGS=commandline_to_str(cppflags + self.CFLAGS),
            CXXFLAGS=commandline_to_str(cppflags + self.CXXFLAGS),
            CPPFLAGS=commandline_to_str(cppflags + self.CFLAGS),
            LDFLAGS=commandline_to_str(self.default_ldflags + self.LDFLAGS),
        )

    def set_make_cmd_with_args(self, var, cmd: Path, args: list) -> None:
        value = str(cmd)
        if args:
            value += " " + self.commandline_to_str(args)
        if self.set_commands_on_cmdline:
            self.make_args.set(**{var: value})
        else:
            self.make_args.set_env(**{var: value})

    def _do_generate_cmakelists(self) -> "typing.NoReturn":
        raise ValueError(f"Should not be called for CMake project {self.target}")
