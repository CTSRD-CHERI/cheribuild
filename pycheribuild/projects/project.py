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
import contextlib
import copy
import datetime
import inspect
import os
import re
import shutil
import subprocess
import sys
import time
import typing
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from .repository import (
    ExternallyManagedSourceRepository,
    GitRepository,
    MercurialRepository,
    ReuseOtherProjectDefaultTargetRepository,
    ReuseOtherProjectRepository,
    SourceRepository,
    SubversionRepository,
    TargetBranchInfo,
)
from .simple_project import SimpleProject, _default_stdout_filter
from ..config.chericonfig import BuildType, CheriConfig, ComputedDefaultValue, Linkage, supported_build_type_strings
from ..config.config_loader_base import ConfigOptionBase
from ..config.target_info import (
    AbstractProject,
    AutoVarInit,
    BasicCompilationTargets,
    CPUArchitecture,
    CrossCompileTarget,
    DefaultInstallDir,
    TargetInfo,
)
from ..processutils import (
    CompilerInfo,
    commandline_to_str,
    get_program_version,
    get_version_output,
    run_command,
    ssh_host_accessible,
)
from ..utils import (
    AnsiColour,
    InstallInstructions,
    OSInfo,
    ThreadJoiner,
    cached_property,
    classproperty,
    coloured,
    remove_duplicates,
    status_update,
)

__all__ = ["Project", "AutotoolsProject", "CheriConfig", "MakeOptions", "MakeCommandKind",  # no-combine
           "MercurialRepository", "CrossCompileTarget", "CPUArchitecture", "GitRepository",  # no-combine
           "commandline_to_str", "ComputedDefaultValue", "TargetInfo", "ReuseOtherProjectRepository",  # no-combine
           "ExternallyManagedSourceRepository", "ReuseOtherProjectDefaultTargetRepository",  # no-combine
           "MakefileProject", "TargetBranchInfo", "Linkage", "BasicCompilationTargets",  # no-combine
           "DefaultInstallDir", "BuildType", "SubversionRepository", "default_source_dir_in_subdir"]  # no-combine


def install_dir_not_specified(_: CheriConfig, project: "Project"):
    raise RuntimeError("install_dir_not_specified! dummy impl must not be called: " + str(project))


def _default_build_dir(_: CheriConfig, project: "SimpleProject"):
    assert isinstance(project, Project)
    return project.build_dir_for_target(project.crosscompile_target)


class MakeCommandKind(Enum):
    DefaultMake = "system default make"
    GnuMake = "GNU make"
    BsdMake = "BSD make"
    Ninja = "ninja"
    CMake = "cmake"
    CustomMakeTool = "custom make tool"


class MakeOptions:
    def __init__(self, kind: MakeCommandKind, project: SimpleProject, **kwargs) -> None:
        self.__project = project
        self._vars: "typing.OrderedDict[str, str]" = OrderedDict()
        # Used by e.g. FreeBSD:
        self._with_options: "typing.OrderedDict[str, bool]" = OrderedDict()
        self._flags: "list[str]" = []
        self.env_vars: "dict[str, str]" = {}
        self.set(**kwargs)
        self.kind = kind
        # We currently need to differentiate cmake driving ninja and cmake driving make since there is no
        # generator-independent option to pass -k (and ninja/make expect a different format)
        self.subkind = None
        self.__can_pass_j_flag: "Optional[bool]" = None
        self.__command: "Optional[str]" = None
        self.__command_args: "list[str]" = []

    def __deepcopy__(self, memo) -> "typing.NoReturn":
        raise RuntimeError("Should not be called!")

    @staticmethod
    def __do_set(target_dict: "dict[str, str]", **kwargs) -> None:
        for k, v in kwargs.items():
            if isinstance(v, bool):
                v = "1" if v else "0"
            if isinstance(v, (Path, int)):
                v = str(v)
            assert isinstance(v, str), "Should only pass int/bool/str/Path here and not " + str(type(v))
            target_dict[k] = v

    def set(self, **kwargs) -> None:
        self.__do_set(self._vars, **kwargs)

    def set_env(self, **kwargs) -> None:
        self.__do_set(self.env_vars, **kwargs)

    def set_with_options(self, **kwargs) -> None:
        """
        For every argument in kwargs sets a WITH_FOO if FOO=True or a WITHOUT_FOO if FOO=False
        Used by the FreeBSD build sysmtem: e.g. make -DWITH_MAN / -DWITHOUT_MAN
        :return: dict of VAR=True/False
        """
        for k, v in kwargs.items():
            assert not k.startswith("WITH_"), "Invalid WITH/WITHOUT options name " + k
            assert not k.startswith("WITHOUT_"), "Invalid WITH/WITHOUT options name " + k
            assert isinstance(v, bool)
            self._with_options[k] = v

    def add_flags(self, *args) -> None:
        """
        :param args: the flags to add (e.g. -j 16, etc.)
        """
        self._flags.extend(args)

    def _get_defined_var(self, name) -> str:
        # BSD make supports a -DVAR syntax but GNU doesn't
        if self.kind == MakeCommandKind.BsdMake:
            return "-D" + name
        else:
            assert self.kind in (MakeCommandKind.GnuMake, MakeCommandKind.DefaultMake)
            return name + "=1"

    @property
    def is_gnu_make(self):
        if self.kind == MakeCommandKind.GnuMake:
            return True
        if self.kind != MakeCommandKind.DefaultMake:
            return False
        # otherwise parse make --version
        return b"GNU Make" in get_version_output(Path(self.command))

    @property
    def command(self) -> str:
        # Don't cache this value in case the user changes the kind
        if self.__command is not None:
            return self.__command
        cmd = self.__infer_command()
        assert self.kind == MakeCommandKind.CustomMakeTool or not Path(cmd).is_absolute()
        return cmd

    # noinspection PyProtectedMember
    def __infer_command(self) -> str:
        if self.kind == MakeCommandKind.DefaultMake:
            if OSInfo.IS_MAC and shutil.which("gmake"):
                # Using /usr/bin/make on macOS breaks compilation DB creation with bear since SIP prevents it from
                # injecting shared libraries into any process that is installed as part of the system.
                # Prefer homebrew-installed gmake if it is available.
                return "gmake"
            else:
                self.__project.check_required_system_tool("make")
                return "make"
        elif self.kind == MakeCommandKind.GnuMake:
            if OSInfo.IS_LINUX and not shutil.which("gmake"):
                status_update("Could not find `gmake` command, assuming `make` is GNU make")
                self.__project.check_required_system_tool("make")
                return "make"
            else:
                self.__project.check_required_system_tool("gmake", homebrew="make")
                return "gmake"
        elif self.kind == MakeCommandKind.BsdMake:
            return "make" if OSInfo.IS_FREEBSD else "bmake"
        elif self.kind == MakeCommandKind.Ninja:
            self.__project.check_required_system_tool("ninja", homebrew="ninja", apt="ninja-build")
            return "ninja"
        elif self.kind == MakeCommandKind.CMake:
            self.__project.check_required_system_tool("cmake", default="cmake", homebrew="cmake", zypper="cmake",
                                                      apt="cmake", freebsd="cmake")
            assert self.subkind is not None
            return "cmake"
        else:
            if self.__command is not None:
                return self.__command
            self.__project.fatal("Cannot infer path from CustomMakeTool. Set self.make_args.set_command(\"tool\")")
            raise RuntimeError()

    def set_command(self, value, can_pass_j_flag=True, early_args: "Optional[list[str]]" = None):
        self.__command = str(value)
        if early_args is None:
            early_args = []
        self.__command_args = early_args
        assert isinstance(self.__command_args, list)
        if not Path(value).is_absolute():
            self.__project.check_required_system_tool(value)
        self.__can_pass_j_flag = can_pass_j_flag

    def all_commandline_args(self, config) -> "list[str]":
        return self.get_commandline_args(config=config)

    def get_commandline_args(self, *, targets: "Optional[list[str]]" = None, jobs: "Optional[int]" = None,
                             verbose=False, continue_on_error=False, config: CheriConfig) -> "list[str]":
        assert self.kind
        result = list(self.__command_args)
        actual_build_tool = self.kind
        # TODO: this code is rather ugly. It would probably be a lot simpler to use inheritance.
        if self.kind == MakeCommandKind.CMake:
            assert self.subkind is not None
            # For CMake we pass target, jobs, and verbose directly to cmake, all other options are fowarded to the real
            # build tool. Ideally we wouldn't care about the underlying build tool, but we want to be able to pass the
            # -k flag.
            actual_build_tool = self.subkind
            result.extend(["--build", "."])
            cmake_version = get_program_version(Path(shutil.which(self.command) or "cmake"), config=config)
            if jobs:
                # -j added in 3.12: https://cmake.org/cmake/help/latest/release/3.12.html#command-line
                if cmake_version < (3, 12, 0):
                    result.extend(["-j", str(jobs)])
                    jobs = None  # don't pass the flag to the build tool again
            if verbose:
                # --verbose added in 3.14: https://cmake.org/cmake/help/latest/release/3.14.html#command-line
                if cmake_version >= (3, 14, 0):
                    result.append("--verbose")
                    verbose = None  # don't pass the flag to the build tool again
            if targets:
                # CMake 3.15 allows multiple targets to be passed to --target. For older versions we pass the
                # targets as arguments to the build tool. This will work for make and ninja (and other generators are
                # not really supported anyway).
                result.append("--target")
                assert all(isinstance(t, str) for t in targets), "Invalid empty/non-string target name"
                if cmake_version >= (3, 15, 0):
                    result.extend(targets)
                    targets = None  # don't pass the targets to the build tool again
                else:
                    result.append(targets[0])
                    targets = targets[1:]  # pass remaining targets to the build tool directly
            # Forward all remaining arguments to make/ninja
            result.append("--")

        # All other options are forwarded to the actual tool.
        if jobs and self.can_pass_jflag:
            result.append("-j" + str(jobs))
        # Cmake and ninja have an explicit verbose flag, other build tools use custom env vars, etc.
        if verbose and actual_build_tool == MakeCommandKind.Ninja:
            result.append("-v")
        if targets:
            assert all(isinstance(t, str) for t in targets), "Invalid empty/non-string target name"
            result.extend(targets)

        # First all the variables:
        for k, v in self._vars.items():
            assert isinstance(v, str)
            if v == "1":
                result.append(self._get_defined_var(k))
            else:
                result.append(k + "=" + v)
        # then the WITH/WITHOUT variables:
        for k, v in self._with_options.items():
            result.append(self._get_defined_var("WITH_" if v else "WITHOUT_") + k)
        # and finally the command line flags like -k
        result.extend(self._flags)
        if continue_on_error:
            continue_flag = "-k"
            if actual_build_tool == MakeCommandKind.Ninja:
                # Ninja expects a maximum number of jobs that can fail instead of continuing for as long as possible.
                continue_flag += "50"
            result.append(continue_flag)
        return result

    def remove_var(self, variable) -> None:
        if variable in self._vars:
            del self._vars[variable]
        if variable in self._with_options:
            del self._with_options[variable]
        for flag in self._flags.copy():
            if flag.strip() == "-D" + variable or flag.startswith(variable + "="):
                self._flags.remove(flag)

    def get_var(self, variable, default: "Optional[str]" = None) -> Optional[str]:
        return self._vars.get(variable, default)

    def remove_flag(self, flag: str) -> None:
        if flag in self._flags:
            self._flags.remove(flag)

    def remove_all(self, predicate: "Callable[[str], bool]") -> None:
        keys = list(self._vars.keys())
        for k in keys:
            if predicate(k):
                del self._vars[k]

    def copy(self) -> "MakeOptions":
        result = copy.copy(self)
        # Make sure that the list and dict objects are different
        result._vars = copy.deepcopy(self._vars)
        result._with_options = copy.deepcopy(self._with_options)
        result._flags = copy.deepcopy(self._flags)
        result.env_vars = copy.deepcopy(self.env_vars)
        return result

    def update(self, other: "MakeOptions"):
        self._vars.update(other._vars)
        self._with_options.update(other._with_options)
        self._flags.extend(other._flags)
        self.env_vars.update(other.env_vars)

    @property
    def can_pass_jflag(self):
        if self.__can_pass_j_flag is not None:
            return self.__can_pass_j_flag
        return self.kind != MakeCommandKind.CustomMakeTool


# noinspection PyProtectedMember
def _default_install_dir_handler(_: CheriConfig, project: "Project") -> Path:
    return project.target_info.default_install_dir(project.get_default_install_dir_kind())


def _default_install_dir_str(project: "Project") -> str:
    install_dir = project.get_default_install_dir_kind()
    return str(install_dir.value)
    # fatal_error("Unknown install dir for", project.target)


def _default_source_dir(config: CheriConfig, project: "Project", subdir: Path = Path()) -> "Optional[Path]":
    if project.repository is not None and isinstance(project.repository, ReuseOtherProjectRepository):
        # For projects that reuse other source directories, we return None to use the default for the source project.
        return None
    if project.default_directory_basename:
        return Path(config.source_root / subdir / project.default_directory_basename)
    return Path(config.source_root / subdir / project.target)


def default_source_dir_in_subdir(subdir: Path) -> ComputedDefaultValue[Path]:
    """
    :param subdir: the subdirectory below the source root (e.g. qt5 or kde-frameworks)
    :return: A ComputedDefaultValue for projects that build in a subdirectory below the source root.
    """
    return ComputedDefaultValue(
        function=lambda config, project: _default_source_dir(config, project, subdir),
        as_string=lambda cls: f"$SOURCE_ROOT/{subdir}/{(cls.default_directory_basename or cls.target)}")


class Project(SimpleProject):
    repository: SourceRepository
    # is_large_source_repository can be set to true to set some git config options to speed up operations:
    # Ideally this would be a flag in GitRepository, but that will not work with inheritance (since some
    # subclasses use different repositories and they would all have to set that flag again). Annoying for LLVM/FreeBSD
    is_large_source_repository: bool = False
    git_revision: Optional[str] = None
    needs_full_history: bool = False  # Some projects need the full git history when cloning
    skip_git_submodules: bool = False
    compile_db_requires_bear: bool = True
    do_not_add_to_targets: bool = True
    set_pkg_config_path: bool = True  # set the PKG_CONFIG_* environment variables when building
    can_run_parallel_install: bool = False  # Most projects don't work well with parallel installation
    default_source_dir: ComputedDefaultValue[Optional[Path]] = ComputedDefaultValue(
        function=_default_source_dir, as_string=lambda cls: "$SOURCE_ROOT/" + cls.default_directory_basename)
    # Some projects (e.g. python) need a native build for build tools, etc.
    needs_native_build_for_crosscompile: bool = False
    # Some projects build docbook xml files and in order to do so we need to set certain env vars to skip the
    # DTD validation with newer XML processing tools.
    builds_docbook_xml: bool = False
    # Some projects have build flags to enable/disable test building. For some projects skipping them can result in a
    # significant build speedup as they should not be needed for most users.
    has_optional_tests: bool = False
    default_build_tests: bool = True  # whether to build tests by default
    show_optional_tests_in_help: bool = True  # whether to show the --foo/build-tests in --help
    add_gdb_index = True  # whether to build with -Wl,--gdb-index if the linker supports it
    _initial_source_dir: Optional[Path]

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        if cls.needs_native_build_for_crosscompile and not cls.get_crosscompile_target().is_native():
            return (cls.get_class_for_target(BasicCompilationTargets.NATIVE).target,)
        return tuple()

    @classmethod
    def project_build_dir_help(cls) -> str:
        result = "$BUILD_ROOT/"
        if isinstance(cls.default_directory_basename, ComputedDefaultValue):
            result += cls.default_directory_basename.as_string
        else:
            result += cls.default_directory_basename
        if cls._xtarget is not BasicCompilationTargets.NATIVE or cls.add_build_dir_suffix_for_native:
            result += "-$TARGET"
        result += "-build"
        return result

    default_build_dir: ComputedDefaultValue[Path] = ComputedDefaultValue(
        function=_default_build_dir, as_string=lambda cls: cls.project_build_dir_help())

    make_kind: MakeCommandKind = MakeCommandKind.DefaultMake
    """
    The kind of too that is used for building and installing (defaults to using "make")
    Set this to MakeCommandKind.GnuMake if the build system needs GNU make features or BsdMake if it needs bmake
    """

    # A per-project config option to generate a CMakeLists.txt that just has a custom taget that calls cheribuild.py
    @property
    def generate_cmakelists(self):
        return self.config.generate_cmakelists

    @classmethod
    def get_source_dir(cls, caller: AbstractProject, cross_target: "Optional[CrossCompileTarget]" = None):
        return cls._get_instance_no_setup(caller, cross_target).source_dir

    @classmethod
    def get_build_dir(cls, caller: AbstractProject, cross_target: "Optional[CrossCompileTarget]" = None):
        return cls._get_instance_no_setup(caller, cross_target).build_dir

    @classmethod
    def get_install_dir(cls, caller: AbstractProject, cross_target: "Optional[CrossCompileTarget]" = None):
        return cls._get_instance_no_setup(caller, cross_target).real_install_root_dir

    def build_dir_for_target(self, target: CrossCompileTarget) -> Path:
        return self.config.build_root / (
            self.default_directory_basename + self.build_configuration_suffix(target) + "-build")

    default_use_asan: bool = False

    @classproperty
    def can_build_with_asan(self) -> bool:
        return self._xtarget is None or not self._xtarget.is_cheri_purecap()

    @classproperty
    def can_build_with_cfi(self) -> bool:
        return self._xtarget is None or not self._xtarget.is_cheri_purecap()

    @classproperty
    def can_build_with_ccache(self) -> bool:
        return False

    @classmethod
    def get_default_install_dir_kind(cls) -> DefaultInstallDir:
        if cls.default_install_dir is not None:
            install_dir = cls.default_install_dir
        else:
            if cls._xtarget is not None and cls._xtarget.is_native():
                install_dir = cls.native_install_dir
            else:
                install_dir = cls.cross_install_dir
        if install_dir is None and cls._default_install_dir_fn is Project._default_install_dir_fn:
            raise RuntimeError(
                "native_install_dir/cross_install_dir/_default_install_dir_fn not specified for " + cls.target)
        if install_dir == DefaultInstallDir.SYSROOT_FOR_BAREMETAL_ROOTFS_OTHERWISE:
            if cls._xtarget is not None and (
                    cls._xtarget.target_info_cls.is_baremetal() or cls._xtarget.target_info_cls.is_rtems()):
                install_dir = DefaultInstallDir.ROOTFS_LOCALBASE
            else:
                install_dir = DefaultInstallDir.ROOTFS_OPTBASE
        return install_dir

    default_install_dir: Optional[DefaultInstallDir] = None
    # To provoide different install locations when cross-compiling and when native
    native_install_dir: Optional[DefaultInstallDir] = None
    cross_install_dir: Optional[DefaultInstallDir] = None
    # For more precise control over the install dir it is possible to provide a callback function
    _default_install_dir_fn: ComputedDefaultValue[Path] = ComputedDefaultValue(function=_default_install_dir_handler,
                                                                               as_string=_default_install_dir_str)
    """ The default installation directory """

    @property
    def _rootfs_install_dir_name(self):
        return self.default_directory_basename

    # useful for cross compile projects that use a prefix and DESTDIR
    _install_prefix: Optional[Path] = None
    _install_dir: Path
    destdir: Optional[Path] = None

    __can_use_lld_map: "dict[str, bool]" = dict()

    def can_use_lld(self, compiler: Path) -> bool:
        command = [str(compiler), *self.essential_compiler_and_linker_flags,
                   "-fuse-ld=lld", "-xc", "-o", "/dev/null", "-"]
        command_str = commandline_to_str(command)
        if command_str not in Project.__can_use_lld_map:
            assert compiler.is_absolute(), compiler
            # Don't cache the result for a non-existent compiler (we could still be building it)
            if not compiler.exists():
                return False
            try:
                self.run_cmd(command, run_in_pretend_mode=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, raise_in_pretend_mode=True,
                             input="int main() { return 0; }\n", print_verbose_only=True)
                status_update(compiler, "supports -fuse-ld=lld, linking should be much faster!")
                Project.__can_use_lld_map[command_str] = True
            except subprocess.CalledProcessError:
                status_update(compiler, "does not support -fuse-ld=lld, using slower bfd instead")
                Project.__can_use_lld_map[command_str] = False
        return Project.__can_use_lld_map[command_str]

    def can_run_binaries_on_remote_morello_board(self) -> bool:
        morello_ssh_hostname = self.config.remote_morello_board
        return morello_ssh_hostname and self.target_info.is_cheribsd() and self.compiling_for_aarch64(
            include_purecap=True) and ssh_host_accessible(morello_ssh_hostname)

    def can_use_lto(self, ccinfo: CompilerInfo) -> bool:
        if ccinfo.compiler == "apple-clang":
            return True
        elif ccinfo.compiler == "clang" and (
                not self.compiling_for_host() or (ccinfo.version >= (4, 0, 0) and self.can_use_lld(ccinfo.path))):
            return True
        return self.compiling_for_host() and ccinfo.compiler == "gcc"

    def can_use_thinlto(self, ccinfo: CompilerInfo) -> bool:
        # ThinLTO requires Clang+LLD or Apple Clang+Apple ld.
        return self.can_use_lto(ccinfo) and ccinfo.compiler != "gcc"

    def check_system_dependencies(self) -> None:
        # Check that the make command exists (this will also add it to the required system tools)
        if self.make_args.command is None:
            self.fatal("Make command not set!")
        if self.config.create_compilation_db and self.compile_db_requires_bear:
            if self.make_args.is_gnu_make and False:
                # use compiledb instead of bear for gnu make
                self.check_required_system_tool("compiledb",
                                                instructions=InstallInstructions("Run `pip install --user compiledb``"))
            else:
                self.check_required_system_tool("bear", homebrew="bear", cheribuild_target="bear")
                self._compiledb_tool = "bear"
        super().check_system_dependencies()

    lto_by_default: bool = False  # Don't default to LTO
    prefer_full_lto_over_thin_lto: bool = False  # If LTO is enabled, use LLVM's ThinLTO by default
    lto_set_ld: bool = True
    default_build_type: BuildType = BuildType.DEFAULT
    default_auto_var_init: AutoVarInit = AutoVarInit.NONE
    use_lto: bool

    @classmethod
    def setup_config_options(cls, install_directory_help="", **kwargs) -> None:
        super().setup_config_options(**kwargs)
        if cls.source_dir is None:
            cls._initial_source_dir = cls.add_optional_path_option(
                "source-directory", metavar="DIR", default=cls.default_source_dir,
                help="Override default source directory for " + cls.target)
        # --<target>-<suffix>/build-directory is not inherited from the unsuffixed target (unless there is only one
        # supported target).
        default_xtarget = cls.default_architecture
        if cls._xtarget is not None or default_xtarget is not None:
            cls.build_dir = cls.add_path_option("build-directory", metavar="DIR", default=cls.default_build_dir,
                                                help="Override default source directory for " + cls.target,
                                                use_default_fallback_config_names=cls._xtarget == default_xtarget)
        if cls.can_build_with_asan:
            asan_default = ComputedDefaultValue(
                function=lambda config, proj: (
                    False if proj.crosscompile_target.is_cheri_purecap() else proj.default_use_asan),
                as_string=str(cls.default_use_asan))
            cls.use_asan = cls.add_bool_option("use-asan", default=asan_default,
                                               help="Build with AddressSanitizer enabled")
        else:
            cls.use_asan = False
        if cls.can_build_with_ccache:
            cls.use_ccache = cls.add_bool_option("use-ccache", default=False,
                                                 help="Build with CCache")
        else:
            cls.use_ccache = False
        cls.auto_var_init = cls.add_config_option(
            "auto-var-init",
            kind=AutoVarInit,
            default=ComputedDefaultValue(
                lambda config, proj: proj.default_auto_var_init,
                lambda c: (
                    'the value of the global --skip-update option (defaults to "' + c.default_auto_var_init.value + '")'
                ),
            ),
            help="Whether to initialize all local variables (currently only supported when compiling with clang)",
        )
        cls.skip_update = cls.add_bool_option("skip-update",
                                              default=ComputedDefaultValue(lambda config, proj: config.skip_update,
                                                                           "the value of the global --skip-update "
                                                                           "option"),
                                              help="Override --skip-update/--no-skip-update for this target only ")
        cls.force_configure = cls.add_bool_option("reconfigure", altname="force-configure",
                                                  default=ComputedDefaultValue(
                                                      lambda config, proj: config.force_configure,
                                                      "the value of the global --reconfigure/--force-configure option"),
                                                  help="Override --(no-)reconfigure/--(no-)force-configure for this "
                                                       "target only")

        if not install_directory_help:
            install_directory_help = "Override default install directory for " + cls.target
        cls._install_dir = cls.add_path_option("install-directory", metavar="DIR", help=install_directory_help,
                                               default=cls._default_install_dir_fn)
        if "repository" in dir(cls) and isinstance(cls.repository, GitRepository) and \
                "git_revision" not in cls.__dict__:
            cls.git_revision = cls.add_config_option("git-revision", metavar="REVISION",
                                                     help="The git revision to checkout prior to building. Useful if "
                                                          "HEAD is broken for one "
                                                          "project but you still want to update the other projects.")
            # TODO: can argparse action be used to store to the class member directly?
            # seems like I can create a new action a pass a reference to the repository:
            # class FooAction(argparse.Action):
            # def __init__(self, option_strings, dest, nargs=None, **kwargs):
            #     if nargs is not None:
            #         raise ValueError("nargs not allowed")
            #     super(FooAction, self).__init__(option_strings, dest, **kwargs)
            # def __call__(self, parser, namespace, values, option_string=None):
            #     print('%r %r %r' % (namespace, values, option_string))
            #     setattr(namespace, self.dest, values)
            cls._repository_url = cls.add_config_option("repository", kind=str, help="The URL of the git repository",
                                                        default=cls.repository.url, metavar="REPOSITORY")
        cls.use_lto = cls.add_bool_option("use-lto", help="Build with link-time optimization (LTO)",
                                          default=cls.lto_by_default)
        if cls.can_build_with_cfi:
            cls.use_cfi = cls.add_bool_option("use-cfi", help="Build with LLVM CFI (requires LTO)", default=False)
        else:
            cls.use_cfi = False
        cls._linkage = cls.add_config_option("linkage", default=Linkage.DEFAULT, kind=Linkage,
                                             help="Build static or dynamic (or use the project default)")

        cls.build_type = typing.cast(BuildType, cls.add_config_option(
            "build-type", default=cls.default_build_type, kind=BuildType,
            enum_choice_strings=supported_build_type_strings,
            help="Optimization+debuginfo defaults (supports the same values as CMake (as well as 'DEFAULT' which"
                 " does not pass any additional flags to the configure command)."))

        if cls.has_optional_tests and "build_tests" not in cls.__dict__:
            cls.build_tests = cls.add_bool_option("build-tests", help="Build the tests",
                                                  default=cls.default_build_tests,
                                                  show_help=cls.show_optional_tests_in_help)

    def linkage(self) -> Linkage:
        if self.target_info.must_link_statically:
            return Linkage.STATIC
        if self._linkage == Linkage.DEFAULT:
            if self.compiling_for_host():
                return Linkage.DEFAULT  # whatever the project chooses as a default
            else:
                return self.config.crosscompile_linkage  # either force static or force dynamic
        return self._linkage

    @property
    def force_static_linkage(self) -> bool:
        return self.linkage() == Linkage.STATIC

    @property
    def force_dynamic_linkage(self) -> bool:
        return self.linkage() == Linkage.DYNAMIC

    _force_debug_info: Optional[bool] = None  # Override the debug info setting from --build-type

    @property
    def should_include_debug_info(self) -> bool:
        if self._force_debug_info is not None:
            return self._force_debug_info
        return self.build_type.should_include_debug_info

    def should_use_extra_c_compat_flags(self) -> bool:
        # TODO: add a command-line option and default to true for
        return self.compiling_for_cheri() and self.target_info.is_baremetal()

    @property
    def extra_c_compat_flags(self):
        if not self.compiling_for_cheri():
            return []
        # Build with virtual address interpretation, data-dependent provenance and pcrelative captable ABI
        return ["-cheri-uintcap=addr", "-Xclang", "-cheri-data-dependent-provenance"]

    @property
    def optimization_flags(self):
        return self._build_type_basic_compiler_flags

    @property
    def _build_type_basic_compiler_flags(self):
        # Not needed for CMakeProjects since those already add flags based on build type
        cbt = self.build_type
        if cbt == BuildType.DEFAULT:
            return []
        elif cbt == BuildType.DEBUG:
            # TODO: once clang's -Og is useful: if self.get_compiler_info(self.CC).supports_Og_flag:
            if self.get_compiler_info(self.CC).compiler == "gcc" or self.use_asan:
                return ["-Og"]
            return ["-O0"]
        elif cbt in (BuildType.RELEASE, BuildType.RELWITHDEBINFO):
            return ["-O2"]
        elif cbt in (BuildType.MINSIZEREL, BuildType.MINSIZERELWITHDEBINFO):
            return ["-Os"]

    @property
    def compiler_warning_flags(self) -> "list[str]":
        if self.compiling_for_host():
            return self.common_warning_flags + self.host_warning_flags
        else:
            return self.common_warning_flags + self.cross_warning_flags

    @property
    def default_compiler_flags(self) -> "list[str]":
        assert self._setup_called
        result = []
        if self.use_lto:
            result.extend(self._lto_compiler_flags)
        if self.use_cfi:
            if not self.use_lto:
                self.fatal("Cannot use CFI without LTO!")
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
            result.append("-fsanitize-cfi-cross-dso")
            result.append("-fvisibility=hidden")
        result.extend(self.essential_compiler_and_linker_flags)
        result.extend(self.optimization_flags)
        result.extend(self.COMMON_FLAGS)
        result.extend(self.compiler_warning_flags)
        if self.config.use_cheri_ubsan and self.crosscompile_target.is_hybrid_or_purecap_cheri():
            compiler = self.get_compiler_info(self.target_info.c_compiler)
            # This needs to be checked late since we depend on the --target/-mabi flags for the -fsanitize= check.
            if compiler.supports_sanitizer_flag("-fsanitize=cheri", result):
                result.append("-fsanitize=cheri")
                if not self.config.use_cheri_ubsan_runtime:
                    result.append("-fsanitize-trap=cheri")
            else:
                self.warning("Compiler", compiler.path, "does not support -fsanitize=cheri, please update your SDK")
        if self.compiling_for_host():
            return result
        if self.config.csetbounds_stats:
            result.extend(["-mllvm", "-collect-csetbounds-output=" + str(self.csetbounds_stats_file),
                           "-mllvm", "-collect-csetbounds-stats=csv",
                           # "-Xclang", "-cheri-bounds=everywhere-unsafe"])
                           ])
        return result

    @property
    def default_ldflags(self) -> "list[str]":
        result = list(self.COMMON_LDFLAGS)
        if self.use_lto:
            result.extend(self._lto_linker_flags)
        if self.force_static_linkage:
            result.append("-static")
        if self.use_cfi:
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
            result.append("-fsanitize-cfi-cross-dso")
        if self.compiling_for_host():
            return result

        # Should work fine without linker emulation (the linker should infer it from input files)
        # if self.compiling_for_cheri():
        #     emulation = "elf64btsmip_cheri_fbsd" if not self.target_info.is_baremetal() else "elf64btsmip_cheri"
        # elif self.compiling_for_mips(include_purecap=False):
        #     emulation = "elf64btsmip_fbsd" if not self.target_info.is_baremetal() else "elf64btsmip"
        # result.append("-Wl,-m" + emulation)
        result += self.essential_compiler_and_linker_flags
        ccinfo = self.get_compiler_info(self.CC)
        result.extend(ccinfo.linker_override_flags(self.target_info.linker))
        if self.should_include_debug_info and self.add_gdb_index and ".bfd" not in self.target_info.linker.name:
            # Add a gdb_index to massively speed up running GDB on CHERIBSD:
            result.append("-Wl,--gdb-index")
            # Also reduce the size of debug info to make copying files over faster
            result.append("-Wl,--compress-debug-sections=zlib")
        if self.target_info.is_cheribsd() and self.config.with_libstatcounters:
            # We need to include the constructor even if there is no reference to libstatcounters:
            # TODO: always include the .a file?
            result += ["-Wl,--whole-archive", "-lstatcounters", "-Wl,--no-whole-archive"]
        return result

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # set up the install/build/source directories (allowing overrides from config file)
        assert isinstance(self.repository, SourceRepository), self.target + " repository member is wrong!"
        if hasattr(self, "_repository_url") and isinstance(self.repository, GitRepository):
            # TODO: remove this and use a custom argparse.Action subclass
            self.repository.url = self._repository_url

        if isinstance(self.default_directory_basename, ComputedDefaultValue):
            self.default_directory_basename = self.default_directory_basename(self.config, self)
        if isinstance(self.repository, ReuseOtherProjectRepository):
            initial_source_dir = inspect.getattr_static(self, "_initial_source_dir")
            assert isinstance(initial_source_dir, ConfigOptionBase)
            # noinspection PyProtectedMember
            assert initial_source_dir._get_default_value(self.config, self) is None, \
                "initial source dir != None for ReuseOtherProjectRepository"
        if self.source_dir is None:
            self.source_dir = self.repository.get_real_source_dir(self, self._initial_source_dir)
        else:
            if isinstance(self.source_dir, ComputedDefaultValue):
                self.source_dir = self.source_dir(self.config, self)
            self._initial_source_dir = self.source_dir

        if self.build_in_source_dir:
            assert not self.build_via_symlink_farm, "Using a symlink farm only makes sense with a separate build dir"
            if self.config.debug_output:
                self.info("Cannot build", self.target, "in a separate build dir, will build in", self.source_dir)
            self.build_dir = self.source_dir

        self.configure_command = None
        # non-assignable variables:
        self.configure_args: "list[str]" = []
        self.configure_environment: "dict[str, str]" = {}
        self.make_args = MakeOptions(self.make_kind, self)
        self._compiledb_tool: Optional[str] = None
        if self.config.create_compilation_db and self.compile_db_requires_bear:
            # CompileDB seems to generate broken compile_commands,json
            if self.make_args.is_gnu_make and False:
                # use compiledb instead of bear for gnu make
                # https://blog.jetbrains.com/clion/2018/08/working-with-makefiles-in-clion-using-compilation-db/
                self._compiledb_tool = "compiledb"
            else:
                self._compiledb_tool = "bear"
        self._force_clean = False
        self._prevent_assign = True

        # Setup destdir and installprefix:
        if not self.compiling_for_host():
            install_dir_kind = self.get_default_install_dir_kind()
            # Install to SDK if CHERIBSD_ROOTFS is the install dir but we are not building for CheriBSD
            if install_dir_kind == DefaultInstallDir.ROOTFS_LOCALBASE:
                self._install_prefix = Path("/", self.target_info.sysroot_install_prefix_relative)
                self.destdir = self._install_dir
            elif install_dir_kind in (DefaultInstallDir.ROOTFS_OPTBASE, DefaultInstallDir.KDE_PREFIX):
                relative_to_rootfs = os.path.relpath(str(self._install_dir), str(self.rootfs_dir))
                if relative_to_rootfs.startswith(os.path.pardir):
                    self.verbose_print("Custom install dir", self._install_dir,
                                       "-> using / as install prefix for", self.target)
                    self._install_prefix = Path("/")
                    self.destdir = self._install_dir
                else:
                    self._install_prefix = Path("/", relative_to_rootfs)
                    self.destdir = self.rootfs_dir
            elif install_dir_kind in (None, DefaultInstallDir.DO_NOT_INSTALL, DefaultInstallDir.IN_BUILD_DIRECTORY,
                                      DefaultInstallDir.CUSTOM_INSTALL_DIR):
                self._install_prefix = self._install_dir
                self.destdir = None
            else:
                assert self._install_prefix and self.destdir is not None, "both must be set!"

        # convert the tuples into mutable lists (this is needed to avoid modifying class variables)
        # See https://github.com/CTSRD-CHERI/cheribuild/issues/33
        # FIXME: this should move to target_info
        self.cross_warning_flags = ["-Werror=implicit-function-declaration",
                                    "-Werror=format", "-Werror=incompatible-pointer-types"]
        self.host_warning_flags = []
        self.common_warning_flags = []
        target_arch = self.crosscompile_target
        # compiler flags:
        self.COMMON_FLAGS = self.target_info.default_initial_compile_flags()
        if target_arch.is_cheri_purecap([CPUArchitecture.MIPS64]) and self.force_static_linkage:
            # clang currently gets the TLS model wrong:
            # https://github.com/CTSRD-CHERI/cheribsd/commit/f863a7defd1bdc797712096b6778940cfa30d901
            self.COMMON_FLAGS.append("-ftls-model=initial-exec")
            # TODO: remove the data-dependent provenance flag:
            if self.should_use_extra_c_compat_flags():
                self.COMMON_FLAGS.extend(self.extra_c_compat_flags)  # include cap-table-abi flags

        assert self._install_dir, "must be set"
        if self.should_include_debug_info and not self.target_info.is_macos():
            self.COMMON_FLAGS.append("-ggdb")
            if not self.compiling_for_mips(include_purecap=True):
                # compressed debug info is broken on big endian until
                # we depend on a lld version with the fix.
                self.COMMON_FLAGS.append("-gz")
        self.CFLAGS: "list[str]" = []
        self.CXXFLAGS: "list[str]" = []
        self.ASMFLAGS: "list[str]" = []
        self.LDFLAGS: "list[str]" = self.target_info.required_link_flags()
        self.COMMON_LDFLAGS: "list[str]" = []
        # Don't build CHERI with ASAN since that doesn't work or make much sense
        if self.use_asan and not self.compiling_for_cheri():
            self.COMMON_FLAGS.append("-fsanitize=address")
            self.COMMON_LDFLAGS.append("-fsanitize=address")
        if self.crosscompile_target.is_libcompat_target():
            self.COMMON_LDFLAGS.append("-L" + str(self.sdk_sysroot / "usr" / self.target_info.default_libdir))

        self._lto_linker_flags: "list[str]" = []
        self._lto_compiler_flags: "list[str]" = []

    @cached_property
    def dependency_install_prefixes(self) -> "list[Path]":
        # TODO: if this is too slow we could look at the direct dependencies only
        deps = self.cached_full_dependencies()
        all_install_dirs = dict()  # Use a dict to ensure reproducible order (guaranteed since Python 3.6)
        for d in deps:
            if d.xtarget is not self.crosscompile_target:
                continue  # Don't add pkg-config directories for targets with a different architecture
            project = d.get_or_create_project(None, self.config, caller=self)
            install_dir = project.install_dir
            if install_dir is not None:
                all_install_dirs[install_dir] = 1
        # Don't add the rootfs directory, since e.g. target_info.pkgconfig_candidates(<rootfs>) will not return the
        # correct values. For the root directory we rely on the methods in target_info instead.
        with contextlib.suppress(LookupError):  # If there isn't a rootfs, there is no need to skip that project.
            all_install_dirs.pop(self.rootfs_dir, None)
        return list(all_install_dirs.keys())

    @property
    def pkgconfig_dirs(self) -> "list[str]":
        dependency_pkgconfig_dirs = self.target_info.pkgconfig_dirs
        for d in self.dependency_install_prefixes:
            dependency_pkgconfig_dirs.extend(self.target_info.pkgconfig_candidates(d))
        return remove_duplicates(dependency_pkgconfig_dirs)

    @property
    def host_dependency_prefixes(self) -> "list[Path]":
        """:return: a list of prefixes for native dependencies (only for cross-compilation)"""
        assert not self.compiling_for_host()
        result = dict()  # Use a dict to ensure reproducible order (guaranteed since Python 3.6)
        if self.needs_native_build_for_crosscompile:
            result[self.get_install_dir(self, cross_target=BasicCompilationTargets.NATIVE)] = True
        for d in self.cached_full_dependencies():
            if d.xtarget.is_native() and not d.project_class.is_toolchain_target():
                result[d.get_or_create_project(d.xtarget, self.config, caller=self).install_dir] = True
        result[self.config.other_tools_dir] = True
        return list(result.keys())

    def setup(self):
        super().setup()
        self.verbose_print(
            self.target, f"INSTALLDIR={self._install_dir}", f"INSTALL_PREFIX={self._install_prefix}",
            f"DESTDIR={self.destdir}",
        )
        if self.set_pkg_config_path:
            pkg_config_args = dict()
            if self.compiling_for_host():
                # We have to add the boostrap tools pkgconfig directory to PKG_CONFIG_PATH so that it is searched in
                # addition to the default paths. Note: We do not set PKG_CONFIG_LIBDIR since that overrides the default.
                pkg_config_args = dict(
                    PKG_CONFIG_PATH=":".join([*self.pkgconfig_dirs, os.getenv("PKG_CONFIG_PATH", "")]))
                if self.target_info.pkg_config_libdir_override is not None:
                    pkg_config_args["PKG_CONFIG_LIBDIR"] = self.target_info.pkg_config_libdir_override
            elif self.needs_sysroot:
                # We need to set the PKG_CONFIG variables both when configuring and when running make since some
                # projects (e.g. GDB) run the configure scripts lazily during the make all stage. If we don't set
                # them*, these configure steps will find the libraries on the host instead and cause the build to fail.
                # PKG_CONFIG_PATH: list of directories to be searched for .pc files before the default locations.
                # PKG_CONFIG_LIBDIR: list of directories to replace the default pkg-config search path.
                # Since we only want libraries from our sysroots we set both.
                pkgconfig_dirs = ":".join(self.pkgconfig_dirs)
                pkg_config_args = dict(
                    PKG_CONFIG_PATH=pkgconfig_dirs,
                    PKG_CONFIG_LIBDIR=pkgconfig_dirs,
                    PKG_CONFIG_SYSROOT_DIR=str(self.target_info.sysroot_dir),
                )
            if pkg_config_args:
                self.configure_environment.update(pkg_config_args)
                self.make_args.set_env(**pkg_config_args)
        cc_info = self.get_compiler_info(self.CC)
        if self.use_lto and self.CC.exists():
            self.add_lto_build_options(cc_info)

        if self.crosscompile_target.is_hybrid_or_purecap_cheri():
            self.cross_warning_flags += ["-Werror=cheri-capability-misuse", "-Werror=cheri-bitwise-operations"]
            # The morello compiler still uses the old flag name
            supports_new_flag = cc_info.supports_warning_flag("-Werror=cheri-prototypes")
            self.cross_warning_flags.append("-Werror=cheri-prototypes" if supports_new_flag else
                                            "-Werror=mips-cheri-prototypes")
            # Make underaligned capability loads/stores an error and require an explicit cast:
            self.cross_warning_flags.append("-Werror=pass-failed")
        if self.CC.exists() and cc_info.is_clang:
            self.cross_warning_flags += ["-Werror=undefined-internal"]

        # We might be setting too many flags, ignore this (for now)
        if not self.compiling_for_host() and self.CC.exists() and self.get_compiler_info(self.CC).is_clang:
            self.COMMON_FLAGS.append("-Wno-error=unused-command-line-argument")
        if self.builds_docbook_xml and OSInfo.IS_MAC:
            catalog = self.get_homebrew_prefix() / "etc/xml/catalog"
            if not catalog.exists():
                self.dependency_error(OSInfo.install_instructions("docbook-xsl", False, homebrew="docbook-xsl"))
            # Without XML_CATALOG_FILES we get the following error: "I/O error : Attempt to load network entity"
            self.configure_environment["XML_CATALOG_FILES"] = str(catalog)
            self.make_args.set_env(XML_CATALOG_FILES=catalog)

    def set_lto_binutils(self, ar, ranlib, nm, ld) -> None:
        self.fatal("Building", self.target, "with LTO is not supported (yet).")
        # raise NotImplementedError()

    def add_lto_build_options(self, ccinfo: CompilerInfo) -> bool:
        compiler = ccinfo.path
        if not self.can_use_lto(ccinfo):
            return False
        self.info("Trying to build with LTO enabled")
        if ccinfo.compiler == "clang":
            # For non apple-clang compilers we need to use llvm binutils:
            version_suffix = ""
            if compiler.name.startswith("clang"):
                version_suffix = compiler.name[len("clang"):]
            llvm_ar = ccinfo.get_matching_binutil("llvm-ar")
            llvm_ranlib = ccinfo.get_matching_binutil("llvm-ranlib")
            llvm_nm = ccinfo.get_matching_binutil("llvm-nm")
            lld = ccinfo.get_matching_binutil("ld.lld")
            # Find lld with the correct version (it must match the version of clang otherwise it breaks!)
            self._lto_linker_flags.extend(ccinfo.linker_override_flags(lld, linker_type="lld"))
            if not llvm_ar or not llvm_ranlib or not llvm_nm:
                self.warning("Could not find llvm-{ar,ranlib,nm}" + version_suffix,
                             "-> disabling LTO (resulting binary will be a bit slower)")
                return False
            ld = lld if self.lto_set_ld else None
            self.set_lto_binutils(ar=llvm_ar, ranlib=llvm_ranlib, nm=llvm_nm, ld=ld)
        if self.prefer_full_lto_over_thin_lto or not self.can_use_thinlto(ccinfo):
            self._lto_compiler_flags.append("-flto")
            self._lto_linker_flags.append("-flto")
        else:
            self._lto_compiler_flags.append("-flto=thin")
            self._lto_linker_flags.append("-flto=thin")
            if ccinfo.compiler == "apple-clang":
                # Apple ld uses a different flag for the thinlto cache dir
                thinlto_cache_flag = "-cache_path_lto,"
            else:
                thinlto_cache_flag = "--thinlto-cache-dir="
            self._lto_linker_flags.append("-Wl," + thinlto_cache_flag + str(self.build_dir / "thinlto-cache"))
        if self.compiling_for_cheri_hybrid([CPUArchitecture.AARCH64]):
            # Hybrid flags are not inferred from the input files, so we have to explicitly pass -mattr= to ld.lld.
            self._lto_linker_flags.extend(["-Wl,-mllvm,-mattr=+morello"])
        self.info("Building with LTO")
        return True

    @cached_property
    def rootfs_dir(self) -> Path:
        xtarget = self.crosscompile_target.get_rootfs_target()
        # noinspection PyProtectedMember
        return self.target_info._get_rootfs_class(xtarget).get_install_dir(self, xtarget)

    @property
    def _no_overwrite_allowed(self) -> "Sequence[str]":
        return (*super()._no_overwrite_allowed, "configure_args", "configure_environment", "make_args")

    # Make sure that API is used properly
    def __setattr__(self, name, value) -> None:
        # if self.__dict__.get("_locked") and name == "x":
        #     raise AttributeError, "MyClass does not allow assignment to .x member"
        # self.__dict__[name] = value
        if self.__dict__.get("_prevent_assign"):
            # assert name not in ("source_dir", "build_dir", "install_dir")
            assert name != "install_dir", "install_dir should not be modified, only _install_dir or _install_prefix"
            assert name != "install_prefix", "install_prefix should not be modified, only _install_dir or " \
                                             "_install_prefix"
            if name in self._no_overwrite_allowed:
                import traceback
                traceback.print_stack()
                raise RuntimeError(self.__class__.__name__ + "." + name + " mustn't be set. Called from" +
                                   self.__class__.__name__)
        self.__dict__[name] = value

    def _get_make_commandline(self, make_target: "Optional[Union[str, list[str]]]", make_command,
                              options: MakeOptions, parallel: bool = True, compilation_db_name: "Optional[str]" = None):
        assert options is not None
        assert make_command is not None
        options = options.copy()
        if compilation_db_name is not None and self.config.create_compilation_db and self.compile_db_requires_bear:
            assert self._compiledb_tool is not None
            compdb_extra_args = []
            if self._compiledb_tool == "bear":
                compdb_extra_args = ["--output", self.build_dir / compilation_db_name, "--append", "--", make_command]
            elif self._compiledb_tool == "compiledb":
                compdb_extra_args = ["--output", self.build_dir / compilation_db_name, "make", "--cmd", make_command]
            else:
                self.fatal("Invalid tool")
            tool_path = shutil.which(self._compiledb_tool)
            if not tool_path:
                self.dependency_error(
                    "Cannot find '" + self._compiledb_tool + "' which is needed to create a compilation DB")
                tool_path = self._compiledb_tool
            options.set_command(tool_path, can_pass_j_flag=options.can_pass_jflag, early_args=compdb_extra_args)
            # Ensure that recursive make invocations reuse the compilation DB tool
            options.set(MAKE=commandline_to_str([options.command, *compdb_extra_args]))
            make_command = options.command

        all_args = [make_command, *options.get_commandline_args(
            targets=[make_target] if isinstance(make_target, str) and make_target else make_target,
            jobs=self.config.make_jobs if parallel else None, config=self.config, verbose=self.config.verbose,
            continue_on_error=self.config.pass_dash_k_to_make)]
        if not self.config.make_without_nice:
            all_args = ["nice", *all_args]
        return all_args

    def get_make_commandline(self, make_target: "Union[str, list[str]]", make_command: "Optional[str]" = None,
                             options: "Optional[MakeOptions]" = None, parallel: bool = True,
                             compilation_db_name: "Optional[str]" = None) -> list:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = self.make_args.command
        return self._get_make_commandline(make_target, make_command, options, parallel, compilation_db_name)

    def run_make(self, make_target: "Optional[Union[str, list[str]]]" = None, *,
                 make_command: "Optional[str]" = None, options: "Optional[MakeOptions]" = None,
                 logfile_name: "Optional[str]" = None, cwd: "Optional[Path]" = None,
                 append_to_logfile=False, compilation_db_name="compile_commands.json", parallel: bool = True,
                 stdout_filter: "Optional[Callable[[bytes], None]]" = _default_stdout_filter) -> None:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = options.command
        all_args = self._get_make_commandline(make_target, make_command, options, parallel=parallel,
                                              compilation_db_name=compilation_db_name)
        if not cwd:
            cwd = self.build_dir
        if not logfile_name:
            logfile_name = Path(make_command).name
            if make_target:
                logfile_name += "." + (make_target if isinstance(make_target, str) else "_".join(make_target))

        starttime = time.time()
        if not self.config.write_logfile and stdout_filter == _default_stdout_filter:
            # if output isatty() (i.e. no logfile) ninja already filters the output -> don't slow this down by
            # adding a redundant filter in python
            if make_command == "ninja" and make_target != "install":
                stdout_filter = None
        if stdout_filter is _default_stdout_filter:
            stdout_filter = self._stdout_filter
        env = options.env_vars
        self.run_with_logfile(all_args, logfile_name=logfile_name, stdout_filter=stdout_filter, cwd=cwd, env=env,
                              append_to_logfile=append_to_logfile)
        # if we create a compilation db, copy it to the source dir:
        if self.config.copy_compilation_db_to_source_dir and (self.build_dir / compilation_db_name).exists():
            self.install_file(self.build_dir / compilation_db_name, self.source_dir / compilation_db_name, force=True)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", make_command, make_target, "took", time.time() - starttime, "seconds")

    def update(self) -> None:
        if not self.repository and not self.skip_update:
            self.fatal("Cannot update", self.target, "as it is missing a repository source",
                       fatal_when_pretending=True)
        self.repository.update(self, src_dir=self.source_dir, base_project_source_dir=self._initial_source_dir,
                               revision=self.git_revision, skip_submodules=self.skip_git_submodules)
        if self.is_large_source_repository and (self.source_dir / ".git").exists():
            # This is a large repository, tell git to do whatever it can to speed up operations (new in 2.24):
            # https://git-scm.com/docs/git-config#Documentation/git-config.txt-featuremanyFiles
            self.run_cmd("git", "config", "--local", "feature.manyFiles", "true", cwd=self.source_dir,
                         print_verbose_only=True)

    _extra_git_clean_excludes: "list[str]" = []

    def _git_clean_source_dir(self, git_dir: "Optional[Path]" = None) -> None:
        if git_dir is None:
            git_dir = self.source_dir
        # just use git clean for cleanup
        self.warning(self.target, "does not support out-of-source builds, using git clean to remove build artifacts.")
        git_clean_cmd = ["git", "clean", "-dfx", "--exclude=.*", *self._extra_git_clean_excludes]
        # Try to keep project files for IDEs and other dotfiles:
        self.run_cmd(git_clean_cmd, cwd=git_dir)

    def clean(self) -> ThreadJoiner:
        assert self.with_clean or self._force_clean
        # TODO: never use the source dir as a build dir (unfortunately mibench and elftoolchain won't work)
        # will have to check how well binutils and qemu work there
        if (self.build_dir / ".git").is_dir():
            if (
                    self.build_dir / "GNUmakefile").is_file() and self.make_kind != MakeCommandKind.BsdMake and \
                    self.target != "elftoolchain":
                run_command(self.make_args.command, "distclean", cwd=self.build_dir)
            else:
                assert self.source_dir == self.build_dir
                self._git_clean_source_dir()
        elif self.build_dir == self.source_dir:
            self.fatal("Cannot clean non-git source directories. Please override")
        else:
            return self.async_clean_directory(self.build_dir, keep_root=True)
        return ThreadJoiner(None)

    def needs_configure(self) -> bool:
        """
        :return: Whether the configure command needs to be run (by default assume yes)
        """
        return True

    def should_run_configure(self) -> bool:
        if self.force_configure or self.config.configure_only:
            return True
        if self.with_clean:
            return True
        return self.needs_configure()

    def add_configure_env_arg(self, arg: str, value: "Union[str,Path]"):
        if value is None:
            return
        assert not isinstance(value, list), ("Wrong type:", type(value))
        assert not isinstance(value, tuple), ("Wrong type:", type(value))
        self.configure_environment[arg] = str(value)

    def set_configure_prog_with_args(self, prog: str, path: Path, args: list) -> None:
        fullpath = str(path)
        if args:
            fullpath += " " + self.commandline_to_str(args)
        self.configure_environment[prog] = fullpath

    def configure(self, cwd: "Optional[Path]" = None, configure_path: "Optional[Path]" = None) -> None:
        if cwd is None:
            cwd = self.build_dir
        if not self.should_run_configure():
            return

        if self.build_via_symlink_farm:
            banned_dirs = {".hg", ".git", ".svn"}
            for root, dirnames, filenames in os.walk(self.source_dir):
                dirnames[:] = [d for d in dirnames if d not in banned_dirs]
                root = Path(root)
                relroot = root.relative_to(self.source_dir)
                for dirname in dirnames:
                    self.makedirs(self.build_dir / relroot / dirname)
                self.create_symlinks(map(lambda x: root / x, filenames), self.build_dir / relroot)

        if configure_path is None:
            configure_path = self.configure_command
        if configure_path is None:
            self.verbose_print("No configure command specified, skippping configure step.")
        else:
            assert configure_path, "configure_command should not be empty!"
            if not Path(configure_path).exists():
                self.fatal("Configure command ", configure_path, "does not exist!")
            self.run_with_logfile([str(configure_path), *self.configure_args], logfile_name="configure", cwd=cwd,
                                  env=self.configure_environment)

    def compile(self, cwd: "Optional[Path]" = None, parallel: bool = True) -> None:
        if cwd is None:
            cwd = self.build_dir
        self.run_make("all", cwd=cwd, parallel=parallel)

    @property
    def make_install_env(self) -> "dict[str, str]":
        if self.destdir:
            env = self.make_args.env_vars.copy()
            if "DESTDIR" not in env:
                env["DESTDIR"] = str(self.destdir)
            return env
        return self.make_args.env_vars

    @property
    def real_install_root_dir(self) -> Path:
        """
        :return: the real install root directory (e.g. if prefix == /usr/local and destdir == /tmp/benchdir it will
         return /tmp/benchdir/usr/local
        """
        if self.destdir is not None:
            assert self._install_prefix and self._install_prefix.parts[0] == "/"
            return Path(self.destdir, *self._install_prefix.parts[1:])
        return self._install_dir

    @property
    def install_dir(self) -> Path:
        assert self._setup_called, "Should be called after base class setup()"
        return self.real_install_root_dir

    @property
    def install_prefix(self) -> Path:
        assert self._setup_called, "Should be called after base class setup()"
        if self._install_prefix is not None:
            return self._install_prefix
        return self._install_dir

    def run_make_install(self, *, options: "Optional[MakeOptions]" = None, _stdout_filter=_default_stdout_filter,
                         cwd: "Optional[Path]" = None, parallel: Optional[bool] = None,
                         target: "Union[str, list[str]]" = "install", make_install_env=None, **kwargs):
        if parallel is None:
            parallel = self.can_run_parallel_install
        if options is None:
            options = self.make_args.copy()
        else:
            options = options.copy()
        if make_install_env is None:
            make_install_env = self.make_install_env
        options.env_vars.update(make_install_env)
        self.run_make(make_target=target, options=options, stdout_filter=_stdout_filter, cwd=cwd,
                      parallel=parallel, **kwargs)

    def install(self, _stdout_filter=_default_stdout_filter) -> None:
        self.run_make_install(_stdout_filter=_stdout_filter)
        if self.compiling_for_cheri() and not (self.real_install_root_dir / "lib64c").exists():
            self.create_symlink(self.real_install_root_dir / "lib", self.real_install_root_dir / "lib64c")

    def _do_generate_cmakelists(self) -> None:
        cmakelists = """
# Do not edit!
# Generated by cheribuild.py
#
cmake_minimum_required(VERSION 3.8)
project({project} LANGUAGES NONE)
set(CLEAR_MAKEENV env -u MAKEFLAGS -u MAKELEVEL -u MAKE -u MAKE_TERMERR -u MAKE_TERMOUT -u MFLAGS)
add_custom_target(cheribuild ALL VERBATIM USES_TERMINAL COMMAND {command} --skip-update --skip-install {target})
add_custom_target(cheribuild-j1 VERBATIM USES_TERMINAL COMMAND {command} --skip-update -j1 {target})
add_custom_target(cheribuild-verbose VERBATIM USES_TERMINAL COMMAND {command} --skip-update -v {target})
add_custom_target(cheribuild-verbose-j1 VERBATIM USES_TERMINAL COMMAND {command} --skip-update -v -j1 {target})

add_custom_target(cheribuild-with-install VERBATIM USES_TERMINAL COMMAND {command} --skip-update {target})
add_custom_target(cheribuild-full VERBATIM USES_TERMINAL COMMAND {command} {target})
""".format(command="${CLEAR_MAKEENV} " + sys.argv[0], project=self.target, target=self.target)
        target_file = self.source_dir / "CMakeLists.txt"
        create = True
        if target_file.exists():
            existing_code = self.read_file(target_file)
            if existing_code == cmakelists:
                create = False
            elif "Generated by cheribuild.py" not in existing_code:
                print("A different CMakeLists.txt already exists. Contents:\n",
                      coloured(AnsiColour.green, existing_code), end="")
                if not self.query_yes_no("Overwrite?", force_result=False):
                    create = False
        if create:
            self.write_file(target_file, cmakelists, overwrite=True)

    @property
    def csetbounds_stats_file(self) -> Path:
        return self.build_dir / "csetbounds-stats.csv"

    def strip_elf_files(self, benchmark_dir) -> None:
        """
        Strip all ELF binaries to reduce the size of the benchmark directory
        :param benchmark_dir: The directory containing multiple ELF binaries
        """
        self.info("Stripping all ELF files in", benchmark_dir)
        self.run_cmd("du", "-sh", benchmark_dir)
        for root, dirnames, filenames in os.walk(str(benchmark_dir)):
            for filename in filenames:
                file = Path(root, filename)
                if file.suffix == ".dump":
                    # TODO: make this an error since we should have deleted them
                    self.warning("Will copy a .dump file to the FPGA:", file)
                # Try to reduce the amount of copied data
                self.maybe_strip_elf_file(file)
        self.run_cmd("du", "-sh", benchmark_dir)

    # @cached_property is important to only compute it once since we encode seconds in the file name:
    @cached_property
    def default_statcounters_csv_name(self) -> str:
        assert isinstance(self, Project)
        suffix = self.build_configuration_suffix()
        if self.config.benchmark_statcounters_suffix:
            user_suffix = self.config.benchmark_statcounters_suffix
            if not user_suffix.startswith("-"):
                user_suffix = "-" + user_suffix
            suffix += user_suffix
        else:
            # If we explicitly override the linkage model, encode it in the statcounters file
            if self.force_static_linkage:
                suffix += "-static"
            elif self.force_dynamic_linkage:
                suffix += "-dynamic"
            if self.config.benchmark_lazy_binding:
                suffix += "-lazybinding"
        return self.target + "-statcounters{}-{}.csv".format(
            suffix, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))

    def copy_asan_dependencies(self, dest_libdir) -> None:
        # ASAN depends on libraries that are not included in the benchmark image by default:
        assert self.compiling_for_mips(include_purecap=False) and self.use_asan
        self.info("Adding ASAN library dependencies to", dest_libdir)
        self.makedirs(dest_libdir)
        for lib in ("usr/lib/librt.so.1", "usr/lib/libexecinfo.so.1", "lib/libgcc_s.so.1", "lib/libelf.so.2"):
            self.install_file(self.sdk_sysroot / lib, dest_libdir / Path(lib).name, force=True,
                              print_verbose_only=False)

    _check_install_dir_conflict: bool = True

    def _last_build_kind_path(self) -> Path:
        return Path(self.build_dir, ".cheribuild_last_build_kind")

    def _last_clean_counter_path(self) -> Path:
        return Path(self.build_dir, ".cheribuild_last_clean_counter")

    def _parse_require_clean_build_counter(self) -> Optional[int]:
        require_clean_path = Path(self.source_dir, ".require_clean_build")
        if not require_clean_path.exists():
            return None
        with require_clean_path.open("r") as f:
            latest_counter: Optional[int] = None
            for i, line in enumerate(f.readlines()):
                # Remove comments
                while "#" in line:
                    line = line[:line.index('#')]
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = int(line)
                    if latest_counter is not None and parsed < latest_counter:
                        self.warning(require_clean_path, ":", i + 1, ": parsed counter ", parsed,
                                     " is smaller than previous one: ", latest_counter, sep="")
                    else:
                        latest_counter = parsed
                except ValueError as e:
                    self.warning(require_clean_path, ":", i + 1, ": could not parse line (", line, "): ", e, sep="")
                    continue
            if latest_counter is None:
                self.warning("Could not find latest counter in", require_clean_path)
            return latest_counter

    def process(self) -> None:
        if self.generate_cmakelists:
            self._do_generate_cmakelists()
        if self.config.verbose:
            print(self.target, " directories: source=", self.source_dir, " build=", self.build_dir, " install=",
                  self.install_dir, sep="")

        if self.use_asan and self.compiling_for_mips(include_purecap=False):
            # copy the ASAN lib into the right directory:
            resource_dir = self.get_compiler_info(self.CC).get_resource_dir()
            status_update("Copying ASAN libs to", resource_dir)
            expected_path = resource_dir / "lib/freebsd/"
            asan_libdir_candidates = list((self.sdk_sysroot / "usr/lib/clang").glob("*"))
            versions = [a.name for a in asan_libdir_candidates]
            # Find the newest ASAN runtime library versions from the FreeBSD sysroot
            found_asan_lib = None
            from distutils.version import StrictVersion
            libname = "libclang_rt.asan-mips64.a"
            for version in reversed(sorted(versions, key=StrictVersion)):
                asan_libs = self.sdk_sysroot / "usr/lib/clang" / version / "lib/freebsd"
                if (asan_libs / libname).exists():
                    found_asan_lib = asan_libs / libname
                    break
            if not found_asan_lib:
                self.fatal("Cannot find", libname, "library in sysroot dirs", asan_libdir_candidates,
                           "-- Compilation will fail!")
                found_asan_lib = Path("/some/invalid/path/to/lib")
            self.makedirs(expected_path)
            run_command("cp", "-av", found_asan_lib.parent, expected_path.parent)
            # For some reason they are 644 so we can't overwrite for the next build unless we chmod first
            run_command("chmod", "-R", "u+w", expected_path.parent)
            if not (expected_path / libname).exists():
                self.fatal("Cannot find", libname, "library in compiler dir", expected_path,
                           "-- Compilation will fail!")
        install_dir_kind = self.get_default_install_dir_kind()
        if install_dir_kind != DefaultInstallDir.DO_NOT_INSTALL and self._check_install_dir_conflict:
            xtarget: CrossCompileTarget = self._xtarget
            # If the conflicting target is also in supported_architectures, check for conficts:
            if xtarget.check_conflict_with is not None and xtarget.check_conflict_with in self.supported_architectures:
                # Check that we are not installing to the same directory as MIPS to avoid conflicts
                base = getattr(self, "synthetic_base", None)
                assert base is not None
                assert issubclass(base, SimpleProject)
                other_instance = base.get_instance_for_cross_target(xtarget.check_conflict_with, self.config,
                                                                    caller=self)
                if self.config.verbose:
                    self.info(self.target, "install dir for", xtarget.name, "is", self.install_dir)
                    other_xtarget = other_instance.crosscompile_target
                    self.info(self.target, "install dir for", other_xtarget.name, "is", self.install_dir)
                assert other_instance.install_dir != self.install_dir, \
                    other_instance.target + " reuses the same install prefix! This will cause conflicts: " + str(
                        other_instance.install_dir)

        if self.skip_update:
            # When --skip-update is set (or we don't have working internet) only check that the repository exists
            if self.repository:
                self.repository.ensure_cloned(self, src_dir=self.source_dir,
                                              base_project_source_dir=self._initial_source_dir,
                                              skip_submodules=self.skip_git_submodules)
        else:
            self.update()
        if not self._system_deps_checked:
            self.check_system_dependencies()
        assert self._system_deps_checked, "self._system_deps_checked must be set by now!"

        last_build_file = self._last_build_kind_path()
        if self.build_in_source_dir and not self.with_clean:
            if not last_build_file.exists():
                self._force_clean = True  # could be an old build prior to adding this check
            else:
                last_build_kind = self.read_file(last_build_file)
                if last_build_kind != self.build_configuration_suffix():
                    if not self.query_yes_no("Last build was for configuration" + last_build_kind +
                                             " but currently building" + self.build_configuration_suffix() +
                                             ". Will clean before build. Continue?", force_result=True,
                                             default_result=True):
                        self.fatal("Cannot continue")
                        return
                    self._force_clean = True

        required_clean_counter = self._parse_require_clean_build_counter()
        clean_counter_in_build_dir: Optional[int] = None
        last_clean_counter_path = self._last_clean_counter_path()
        if required_clean_counter is not None:
            # Check if the last clean build had a smaller counter than the current required on and if so perform a clean
            # build and increment the value in the build directory.
            if not last_clean_counter_path.is_file():
                self.verbose_print("Forcing full rebuild since clean counter", last_clean_counter_path,
                                   "does not exist yet")
                self._force_clean = True
            else:
                try:
                    clean_counter_in_build_dir = int(last_clean_counter_path.read_text().strip())
                    if clean_counter_in_build_dir < required_clean_counter:
                        self.info("Forcing full rebuild since clean counter in build dir (", clean_counter_in_build_dir,
                                  ") is less than required minimum ", required_clean_counter, sep="")
                        self._force_clean = True
                    else:
                        self.verbose_print("Not forcing clean build since clean counter in build dir",
                                           clean_counter_in_build_dir, "is >= required minimum", required_clean_counter)
                except Exception as e:
                    self.warning("Could not parse", last_clean_counter_path, "-> assuming clean build is required.", e)
                    self._force_clean = True

        # run the rm -rf <build dir> in the background
        cleaning_task = self.clean() if (self._force_clean or self.with_clean) else ThreadJoiner(None)
        if cleaning_task is None:
            cleaning_task = ThreadJoiner(None)
        assert isinstance(cleaning_task, ThreadJoiner), ""
        with cleaning_task:
            if not self.build_dir.is_dir():
                self.makedirs(self.build_dir)

            # Clean has been performed -> write the last clean counter now (if needed).
            if required_clean_counter is not None and clean_counter_in_build_dir != required_clean_counter:
                self.write_file(last_clean_counter_path, str(required_clean_counter), overwrite=True)
            # Update the last build kind file if we are building in the source dir;
            if self.build_in_source_dir:
                self.write_file(last_build_file, self.build_configuration_suffix(), overwrite=True)
            # Clean completed

            # Configure step
            if (not self.config.skip_configure or self.config.configure_only) and self.should_run_configure():
                status_update("Configuring", self.display_name, "... ")
                self.configure()
            if self.config.configure_only:
                return

            # Build step
            if not self.config.skip_build:
                if self.config.csetbounds_stats and (self.csetbounds_stats_file.exists() or self.config.pretend):
                    self.move_file(self.csetbounds_stats_file,
                                   self.csetbounds_stats_file.with_suffix(".from-configure.csv"),
                                   force=True)
                    # move any csetbounds stats from configuration (since they are not useful)
                status_update("Building", self.display_name, "... ")
                self.compile()

            # Install step
            if not self.config.skip_install:
                status_update("Installing", self.display_name, "... ")
                if install_dir_kind == DefaultInstallDir.DO_NOT_INSTALL:
                    self.info("Not installing", self.target, "since install dir is set to DO_NOT_INSTALL")
                else:
                    self.install()


# Shared between meson and CMake
class _CMakeAndMesonSharedLogic(Project):
    do_not_add_to_targets: bool = True
    tests_need_full_disk_image: bool = False
    _minimum_cmake_or_meson_version: "Optional[tuple[int, ...]]" = None
    _configure_tool_name: str
    _toolchain_template: str
    _toolchain_file: Path

    class CommandLineArgs:
        """Simple wrapper to distinguish CMake (space-separated string) from Meson (python-style list)"""

        def __init__(self, args: list) -> None:
            self.args = args

        def __str__(self) -> str:
            return str(self.args)

        def __repr__(self) -> str:
            return str(self)

    class EnvVarPathList:
        """Simple wrapper to distinguish CMake (:-separated string) from Meson (python-style list)"""

        def __init__(self, paths: list) -> None:
            self.paths = paths

        def __str__(self) -> str:
            return str(self.paths)

        def __repr__(self) -> str:
            return str(self)

    def _toolchain_file_list_to_str(self, value: list) -> str:
        raise NotImplementedError()

    def _toolchain_file_command_args_to_str(self, value: CommandLineArgs) -> str:
        return self._toolchain_file_list_to_str(value.args)

    def _toolchain_file_env_var_path_list_to_str(self, value: EnvVarPathList) -> str:
        return self._toolchain_file_list_to_str(value.paths)

    def _bool_to_str(self, value: bool) -> str:
        raise NotImplementedError()

    def _replace_value(self, template: str, required: bool, key: str, value: str) -> str:
        if isinstance(value, bool):
            strval = self._bool_to_str(value)
        elif isinstance(value, _CMakeAndMesonSharedLogic.CommandLineArgs):
            # The CMake toolchain file generated by Meson uses a CMake list for compiler args, but that results in
            # CMake calling `clang -target;foo;--sysroot=...". We have to use a space-separated list instead, so we
            # also expand @{KEY}_STR@ (but don't make it an error if it doesn't exist in the toolchain file).
            # Feature request: https://github.com/mesonbuild/meson/issues/8534
            template = self._replace_value(template, required=False,
                                           key=key + '_STR', value=commandline_to_str(value.args))
            strval = self._toolchain_file_command_args_to_str(value)
        elif isinstance(value, _CMakeAndMesonSharedLogic.EnvVarPathList):
            strval = self._toolchain_file_env_var_path_list_to_str(value)
        elif isinstance(value, list):
            strval = self._toolchain_file_list_to_str(value)
        else:
            if not isinstance(value, (str, Path, int)):
                self.fatal(f"Unexpected value type {type(value)} for {key}: {value}", fatal_when_pretending=True)
            strval = str(value)
        result = template.replace("@" + key + "@", strval)
        if required and result == template:
            raise ValueError(key + " not used in toolchain file")
        return result

    @property
    def cmake_prefix_paths(self):
        return remove_duplicates(self.target_info.cmake_prefix_paths(self.config) + self.dependency_install_prefixes)

    def _replace_values_in_toolchain_file(self, template: str, file: Path, **kwargs) -> None:
        result = template
        for key, value in kwargs.items():
            if value is None:
                continue
            result = self._replace_value(result, required=True, key=key, value=value)
        not_substituted = re.search(r"@[\w_\d]+@", result)
        if not_substituted:
            self.fatal("Did not replace all keys, found", not_substituted.group(0), "at offset", not_substituted.span(),
                       fatal_when_pretending=True)
        self.write_file(contents=result, file=file, overwrite=True)

    def _prepare_toolchain_file_common(self, output_file: "Optional[Path]" = None, **kwargs) -> None:
        if output_file is None:
            output_file = self._toolchain_file
        assert self._toolchain_template is not None
        # XXX: We currently use CHERI LLVM tools for native builds
        sdk_bindir = self.sdk_bindir if not self.compiling_for_host() else self.config.cheri_sdk_bindir
        cmdline = _CMakeAndMesonSharedLogic.CommandLineArgs
        system_name = self.target_info.cmake_system_name if not self.compiling_for_host() else sys.platform
        if self._configure_tool_name == "Meson":
            # Meson expects lower-case system names:
            # https://mesonbuild.com/Reference-tables.html#operating-system-names
            system_name = system_name.lower()
        self._replace_values_in_toolchain_file(
            self._toolchain_template, output_file,
            TOOLCHAIN_SDK_BINDIR=sdk_bindir,
            TOOLCHAIN_COMPILER_BINDIR=self.CC.parent,
            TOOLCHAIN_TARGET_TRIPLE=self.target_info.target_triple,
            TOOLCHAIN_COMMON_FLAGS=cmdline(self.default_compiler_flags),
            TOOLCHAIN_C_FLAGS=cmdline(self.CFLAGS),
            TOOLCHAIN_LINKER_FLAGS=cmdline(self.default_ldflags + self.LDFLAGS),
            TOOLCHAIN_CXX_FLAGS=cmdline(self.CXXFLAGS),
            TOOLCHAIN_ASM_FLAGS=cmdline(self.ASMFLAGS),
            TOOLCHAIN_C_COMPILER=self.CC,
            TOOLCHAIN_CXX_COMPILER=self.CXX,
            TOOLCHAIN_AR=self.target_info.ar,
            TOOLCHAIN_RANLIB=self.target_info.ranlib,
            TOOLCHAIN_NM=self.target_info.nm,
            TOOLCHAIN_STRIP=self.target_info.strip_tool,
            TOOLCHAIN_SYSROOT=self.sdk_sysroot if self.needs_sysroot else "",
            TOOLCHAIN_SYSTEM_PROCESSOR=self.target_info.cmake_processor_id,
            TOOLCHAIN_SYSTEM_NAME=system_name,
            TOOLCHAIN_SYSTEM_VERSION=self.target_info.toolchain_system_version or "",
            TOOLCHAIN_CMAKE_PREFIX_PATH=self.cmake_prefix_paths,
            TOOLCHAIN_PKGCONFIG_DIRS=_CMakeAndMesonSharedLogic.EnvVarPathList(self.pkgconfig_dirs),
            COMMENT_IF_NATIVE="#" if self.compiling_for_host() else "",
            **kwargs)

    def _add_configure_options(self, *, _include_empty_vars=False, _replace=True, _config_file_options: "list[str]",
                               **kwargs) -> None:
        for option, value in kwargs.items():
            existing_option = next((x for x in self.configure_args if x.startswith("-D" + option + "=")), None)
            if any(x.startswith("-D" + option) for x in _config_file_options):
                self.info("Not using default value of '", value, "' for configure option '", option,
                          "' since it is explicitly overwritten in the configuration", sep="")
                continue
            if existing_option is not None:
                if _replace:
                    self.configure_args.remove(existing_option)
                else:
                    self.warning("Not replacing ", option, "since it is already set.")
                    continue
            if isinstance(value, bool):
                value = self._bool_to_str(value)
            if (not str(value) or not value) and not _include_empty_vars:
                continue
            # Only allow a known list of types to be converted to strings:
            if not isinstance(value, (str, Path, int)):
                raise TypeError(f"Unsupported type {type(value)}: {value}")
            assert value is not None
            self.configure_args.append("-D" + option + "=" + str(value))

    def _get_configure_tool_version(self) -> "tuple[int, ...]":
        cmd = Path(self.configure_command)
        assert self.configure_command is not None
        if not cmd.is_absolute() or not Path(self.configure_command).exists():
            self.fatal("Could not find", self._configure_tool_name, "binary:", self.configure_command)
            return 0, 0, 0
        assert cmd.is_absolute()
        return get_program_version(cmd, config=self.config, **self._get_version_args)

    @property
    def _get_version_args(self) -> dict:
        raise NotImplementedError()

    def _configure_tool_install_instructions(self) -> InstallInstructions:
        raise NotImplementedError()

    def setup(self):
        super().setup()
        assert self.configure_command is not None
        if not Path(self.configure_command).is_absolute():
            abspath = shutil.which(self.configure_command)
            if abspath:
                self.configure_command = abspath

    def process(self) -> None:
        super().process()
        if self._minimum_cmake_or_meson_version is not None:
            version_components = self._get_configure_tool_version()
            if version_components < self._minimum_cmake_or_meson_version:
                version_str = ".".join(map(str, version_components))
                expected_str = ".".join(map(str, self._minimum_cmake_or_meson_version))
                tool = self._configure_tool_name
                install_instrs = self._configure_tool_install_instructions()
                self.dependency_error(tool, "version", version_str, "is too old (need at least", expected_str + ")",
                                      install_instructions=install_instrs,
                                      cheribuild_target=install_instrs.cheribuild_target,
                                      cheribuild_xtarget=BasicCompilationTargets.NATIVE)


class AutotoolsProject(Project):
    do_not_add_to_targets: bool = True
    _configure_supports_prefix: bool = True
    make_kind: MakeCommandKind = MakeCommandKind.GnuMake
    add_host_target_build_config_options: bool = True

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        super().setup_config_options(**kwargs)
        cls.extra_configure_flags = cls.add_list_option("configure-options", metavar="OPTIONS",
                                                        help="Additional command line options to pass to configure")

    """
    Like Project but automatically sets up the defaults for autotools like projects
    Sets configure command to ./configure, adds --prefix=installdir
    """
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.configure_command = self.source_dir / "configure"

    def setup(self) -> None:
        super().setup()
        buildhost = self.get_host_triple()
        if self.add_host_target_build_config_options:
            if not self.compiling_for_host():
                autotools_triple = self.target_info.target_triple
                # Most scripts don't like the final -purecap component:
                autotools_triple = autotools_triple.replace("-purecap", "")
                # TODO: do we have to remove these too?
                # autotools_triple = autotools_triple.replace("mips64c128-", "cheri-")
                self.configure_args.extend(["--host=" + autotools_triple, "--target=" + autotools_triple,
                                            "--build=" + buildhost])
            elif self.crosscompile_target.is_hybrid_or_purecap_cheri():
                # When compiling natively on CheriBSD, most autotools projects don't like the inferred config.guess
                # value of aarch64c-unknown-freebsd14.0. Override it to make this work in most cases.
                self.configure_args.extend(["--build=" + buildhost])
        if self.config.verbose:
            # Most autotools-base projects enable verbose output by setting V=1
            self.make_args.set_env(V=1)

    def configure(self, **kwargs) -> None:
        if self._configure_supports_prefix:
            if self.install_prefix != self.install_dir:
                assert self.destdir, "custom install prefix requires DESTDIR being set!"
                self.configure_args.append("--prefix=" + str(self.install_prefix))
            else:
                self.configure_args.append("--prefix=" + str(self.install_dir))
        if self.make_args.kind != MakeCommandKind.DefaultMake:
            self.add_configure_env_arg("MAKE", self.make_args.command)
        if self.extra_configure_flags:
            self.configure_args.extend(self.extra_configure_flags)
        # If there is no ./configure script but ./autogen.sh exists, try running that first
        if not self.configure_command.exists() and (self.configure_command.parent / "autogen.sh").is_file():
            # We need to pass NOCONFIGURE=1, to avoid invoking the configure script directly plus any environment
            # variables that might affect the autoconf lookup (e.g. ACLOCAL_PATH).
            self.run_cmd(self.configure_command.parent / "autogen.sh", cwd=self.configure_command.parent,
                         env={**dict(NOCONFIGURE=1), **self.configure_environment})
        super().configure(**kwargs)

    def needs_configure(self) -> bool:
        # Most autotools projects use makefiles, but we also use this class for the CMake
        # bootstrap build which ends up generating a build.ninja file instead of a Makefile.
        build_file = "build.ninja" if self.make_args.kind == MakeCommandKind.Ninja else "Makefile"
        return not (self.build_dir / build_file).exists()

    def set_lto_binutils(self, ar, ranlib, nm, ld) -> None:
        kwargs = {"NM": nm, "AR": ar, "RANLIB": ranlib}
        if ld:
            kwargs["LD"] = ld
        self.configure_environment.update(**kwargs)
        # self.make_args.env_vars.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
        self.make_args.set(**kwargs)
        self.make_args.env_vars.update(**kwargs)

    def run_tests(self) -> None:
        # Most autotools projects have a "check" target that we can use.
        try:
            self.run_cmd(self.make_args.command,
                         *self.make_args.get_commandline_args(targets=["-n", "check"], jobs=1, config=self.config),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=self.build_dir)
        except subprocess.CalledProcessError:
            # If make -n check fails, assume there are no tests.
            return super().run_tests()
        self.run_make("check", parallel=False, logfile_name="test")  # Unlikely to be parallel-safe


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
        # Most projects expect that a plain $CC foo.c will work so we include the -target, etc in CC
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

    def set_make_cmd_with_args(self, var, cmd: Path, args: "list[str]") -> None:
        value = str(cmd)
        if args:
            value += " " + self.commandline_to_str(args)
        if self.set_commands_on_cmdline:
            self.make_args.set(**{var: value})
        else:
            self.make_args.set_env(**{var: value})
