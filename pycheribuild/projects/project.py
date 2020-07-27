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
import copy
import datetime
import errno
import inspect
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import typing
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Callable, Tuple, Union

from ..config.chericonfig import BuildType, CheriConfig
from ..config.loader import (ComputedDefaultValue, ConfigLoaderBase, ConfigOptionBase, DefaultValueOnlyConfigOption)
from ..config.target_info import (AutoVarInit, BasicCompilationTargets, CPUArchitecture, CrossCompileTarget, Linkage,
                                  TargetInfo)
from ..filesystemutils import FileSystemUtils
from ..targets import MultiArchTarget, MultiArchTargetAlias, Target, target_manager
from ..utils import (AnsiColour, check_call_handle_noexec, classproperty, coloured, commandline_to_str, CompilerInfo,
                     fatal_error, get_compiler_info, get_program_version,
                     get_version_output, include_local_file, is_jenkins_build, OSInfo, popen_handle_noexec,
                     print_command, run_command, status_update, ThreadJoiner, warning_message)

__all__ = ["Project", "CMakeProject", "AutotoolsProject", "TargetAlias", "TargetAliasWithDependencies",  # no-combine
           "SimpleProject", "CheriConfig", "flush_stdio", "MakeOptions", "MakeCommandKind",  # no-combine
           "CrossCompileTarget", "CPUArchitecture", "GitRepository", "ComputedDefaultValue", "TargetInfo",  # no-combine
           "commandline_to_str", "ReuseOtherProjectRepository", "ExternallyManagedSourceRepository",  # no-combine
           "ReuseOtherProjectDefaultTargetRepository",  # no-combine
           "TargetBranchInfo", "Linkage", "BasicCompilationTargets", "DefaultInstallDir", "BuildType"]  # no-combine

Type_T = typing.TypeVar("Type_T")


def flush_stdio(stream):
    while True:
        try:
            # can lead to EWOULDBLOCK if stream cannot be flushed immediately
            stream.flush()
            break
        except BlockingIOError as e:
            if e.errno != errno.EWOULDBLOCK:
                raise
            else:
                time.sleep(0.1)


def _default_stdout_filter(_: bytes):
    raise NotImplementedError("Should never be called, this is a dummy")


class ProjectSubclassDefinitionHook(type):
    def __init__(cls, name: str, bases, clsdict):
        super().__init__(name, bases, clsdict)
        if typing.TYPE_CHECKING:  # no-combine
            assert issubclass(cls, SimpleProject)  # no-combine
        if clsdict.get("do_not_add_to_targets") is not None:
            if clsdict.get("do_not_add_to_targets") is True:
                return  # if do_not_add_to_targets is defined within the class we skip it
        elif name.endswith("Base"):
            fatal_error("Found class name ending in Base (", name, ") but do_not_add_to_targets was not defined",
                        sep="")

        project_name = None
        if "project_name" in clsdict:
            project_name = clsdict["project_name"]
        else:
            # fall back to name of target then infer from class name
            # if target_name:
            #     project_name = target_name
            if name.startswith("Build"):
                project_name = name[len("Build"):].replace("_", "-")
            cls.project_name = project_name

        # load "target" field first then check project name (as that might default to target)
        target_name = None
        if "target" in clsdict:
            target_name = clsdict["target"]
        elif project_name:
            target_name = project_name.lower()
            cls.target = target_name

        if not target_name:
            sys.exit("target name is not set and cannot infer from class " + name +
                     " -- set project_name=, target= or do_not_add_to_targets=True")
        if cls.__dict__.get("dependencies_must_be_built"):
            if not cls.dependencies:
                sys.exit("PseudoTarget with no dependencies should not exist!! Target name = " + target_name)
        supported_archs = cls.supported_architectures
        assert supported_archs, "Must not be empty: " + str(supported_archs)
        assert isinstance(supported_archs, list)
        assert len(set(supported_archs)) == len(
            supported_archs), "Duplicates in supported archs for " + cls.__name__ + ": " + str(supported_archs)
        # TODO: if len(cls.supported_architectures) > 1:
        if cls._always_add_suffixed_targets or len(supported_archs) > 1:
            # Add a the target for the default architecture
            base_target = MultiArchTargetAlias(target_name, cls)
            target_manager.add_target(base_target)
            assert cls._xtarget is None, "Should not be set!"
            # assert cls._should_not_be_instantiated, "multiarch base classes should not be instantiated"
            for arch in supported_archs:
                assert isinstance(arch, CrossCompileTarget)
                # create a new class to ensure different build dirs and config name strings
                if hasattr(cls, "custom_target_name"):
                    assert callable(cls.custom_target_name)
                    new_name = cls.custom_target_name(target_name, arch)
                else:
                    new_name = target_name + "-" + arch.generic_suffix
                new_dict = cls.__dict__.copy()
                new_dict["_xtarget"] = arch
                new_dict["_should_not_be_instantiated"] = False  # unlike the subclass we can instantiate these
                new_dict["do_not_add_to_targets"] = True  # We are already adding it here
                new_dict["target"] = new_name
                new_dict["synthetic_base"] = cls  # We are already adding it here
                # noinspection PyTypeChecker
                new_type = type(cls.__name__ + "_" + arch.name, (cls,) + cls.__bases__, new_dict)
                target_manager.add_target(MultiArchTarget(new_name, new_type, arch, base_target))
        else:
            assert len(supported_archs) == 1
            # Only one target is supported:
            cls._xtarget = supported_archs[0]
            cls._should_not_be_instantiated = False  # can be instantiated
            target_manager.add_target(Target(target_name, cls))
        # print("Adding target", target_name, "with deps:", cls.dependencies)


class SimpleProject(FileSystemUtils, metaclass=ProjectSubclassDefinitionHook):
    _config_loader = None  # type: ConfigLoaderBase

    # These two class variables can be defined in subclasses to customize dependency ordering of targets
    target = ""  # type: str
    project_name = None
    dependencies = []  # type: typing.List[str]
    dependencies_must_be_built = False
    is_alias = False
    is_sdk_target = False  # for --skip-sdk
    source_dir = None
    build_dir = None
    build_in_source_dir = False  # For projects that can't build in the source dir
    install_dir = None
    # For target_info.py. Real value is only set for Project subclasses, since SimpleProject subclasses should not
    # include C/C++ compilation (there is no source+build dir)
    auto_var_init = AutoVarInit.NONE
    # Whether to hide the options from the default --help output (only add to --help-hidden)
    hide_options_from_help = False
    # Project subclasses will automatically have a target based on their name generated unless they add this:
    do_not_add_to_targets = True
    # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
    # However, if the output is just a plain text file don't attempt to do any line clearing
    _clear_line_sequence = b"\x1b[2K\r" if sys.stdout.isatty() else b"\n"
    # Default to NATIVE only
    supported_architectures = [BasicCompilationTargets.NATIVE]
    # The architecture to build for the unsuffixed target name (defaults to supported_architectures[0] if no match)
    _default_architecture = None

    _xtarget = None  # type: typing.Optional[CrossCompileTarget]
    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    # To check that we don't create an crosscompile targets without a fixed target
    _should_not_be_instantiated = True
    # To prevent non-suffixed targets in case the only target is not NATIVE
    _always_add_suffixed_targets = False  # add a suffixed target only if more than one variant is supported

    @classmethod
    def is_toolchain_target(cls):
        return False

    @property
    def _no_overwrite_allowed(self) -> "typing.Tuple[str]":
        return "_xtarget",

    __cached_deps = None  # type: typing.List[Target]

    @classmethod
    def all_dependency_names(cls, config: CheriConfig) -> "typing.List[str]":
        assert cls._xtarget is not None
        return [t.name for t in cls.recursive_dependencies(config)]

    @classmethod
    def direct_dependencies(cls, config: CheriConfig) -> "typing.Generator[Target]":
        assert cls._xtarget is not None
        dependencies = cls.dependencies
        expected_build_arch = cls.get_crosscompile_target(config)
        assert expected_build_arch is not None
        assert cls._xtarget is not None
        if expected_build_arch is None or cls._xtarget is None:
            raise ValueError("Cannot call direct_dependencies() on a target alias")
        if callable(dependencies):
            if inspect.ismethod(dependencies):
                # noinspection PyCallingNonCallable
                dependencies = cls.dependencies(config)
            else:
                dependencies = dependencies(cls, config)
        assert isinstance(dependencies, list), "Expected a list and not " + str(type(dependencies))
        for dep_name in dependencies:
            if callable(dep_name):
                dep_name = dep_name(cls, config)
            try:
                dep_target = target_manager.get_target(dep_name, arch=expected_build_arch, config=config, caller=cls)
            except KeyError:
                fatal_error("Could not find target '", dep_name, "' for ", cls.__name__, sep="")
                raise
            # Handle --include-dependencies with --skip-sdk is passed
            if config.skip_sdk and dep_target.project_class.is_sdk_target:
                if config.verbose:
                    status_update("Not adding ", cls.target, "dependency", dep_target.name,
                                  "since it is an SDK target and --skip-sdk was passed.")
                continue
            if config.include_dependencies and (
                    not config.include_toolchain_dependencies and dep_target.project_class.is_toolchain_target()):
                if config.verbose:
                    status_update("Not adding ", cls.target, "dependency", dep_target.name,
                                  "since it is a toolchain target and --include-toolchain-dependencies was not passed.")
                continue
            # Now find the actual crosscompile targets for target aliases:
            if isinstance(dep_target, MultiArchTargetAlias):
                # Find the correct dependency (e.g. libcxx-native should depend on libcxxrt-native)
                # try to find a better match:
                for tgt in dep_target.derived_targets:
                    if tgt.target_arch is expected_build_arch:
                        dep_target = tgt
                        # print("Overriding with", tgt.name)
                        break
            assert not isinstance(dep_target, MultiArchTargetAlias), "All targets should be fully resolved but got " \
                                                                     + str(dep_target) + " in " + cls.__name__
            if dep_target.project_class is cls:
                # assert False, "Found self as dependency:" + str(cls)
                continue
            yield dep_target

    def is_exact_instance(self, class_type: "typing.Type[typing.Any]") -> bool:
        if self.__class__ == class_type or getattr(self, "synthetic_base", object) == class_type:
            self.verbose_print(self, "is exact instance of", class_type)
            return True
        else:
            self.verbose_print(self, "is not exact instance of", class_type)
            return False

    @classmethod
    def recursive_dependencies(cls, config: CheriConfig) -> "typing.List[Target]":
        # look only in __dict__ to avoid parent class lookup
        _cached = cls.__dict__.get("_cached_deps", None)
        if _cached is not None:
            return _cached
        result = []  # type: typing.List[Target]
        assert cls._xtarget is not None, cls
        for target in cls.direct_dependencies(config):
            if target not in result:
                result.append(target)
            # now recursively add the other deps:
            recursive_deps = target.project_class.recursive_dependencies(config)
            for r in recursive_deps:
                if r not in result:
                    result.append(r)
        cls._cached_deps = result
        return result

    @classmethod
    def _cached_dependencies(cls) -> "typing.List[Target]":
        # look only in __dict__ to avoid parent class lookup
        _cached = cls.__dict__.get("_cached_deps", None)
        if _cached is None:
            raise ValueError("_cached_dependencies called before all_dependency_names()")
        return _cached

    @classmethod
    def get_instance(cls: typing.Type[Type_T], caller: "typing.Optional[SimpleProject]",
                     config: CheriConfig = None, cross_target: typing.Optional[CrossCompileTarget] = None) -> Type_T:
        # TODO: assert that target manager has been initialized
        if caller is not None:
            if config is None:
                config = caller.config
            if cross_target is None:
                cross_target = caller.get_crosscompile_target(config)
        else:
            if cross_target is None:
                cross_target = cls.get_crosscompile_target(config)
            assert config is not None, "Need either caller or config argument!"
        return cls.get_instance_for_cross_target(cross_target, config, caller=caller)

    @classmethod
    def get_instance_for_cross_target(cls: typing.Type[Type_T], cross_target: CrossCompileTarget,
                                      config: CheriConfig, caller: "SimpleProject" = None) -> Type_T:
        # Also need to handle calling self.get_instance_for_cross_target() on a target-specific instance
        # In that case cls.target returns e.g. foo-mips, etc and target_manager will always return the MIPS version
        root_class = getattr(cls, "synthetic_base", cls)
        target = target_manager.get_target(root_class.target, cross_target, config, caller=caller)
        result = target.get_or_create_project(cross_target, config)
        assert isinstance(result, SimpleProject)
        found_target = result.get_crosscompile_target(config)
        # XXX: FIXME: add cross target to every call
        assert cross_target is not None
        if cross_target is not None:
            assert found_target is cross_target, "Didn't find right instance of " + str(cls) + ": " + str(
                found_target) + " vs. " + str(cross_target) + ", caller was " + repr(caller)
        return result

    @classmethod
    def get_crosscompile_target(cls, config: CheriConfig) -> CrossCompileTarget:
        target = cls._xtarget
        if target is not None:
            return target
        # Find the best match based on config.preferred_xtarget
        default_target = config.preferred_xtarget
        assert cls.supported_architectures, "Must not be empty"
        # if we can build the default target (--xmips/--xhost) chose that
        if default_target in cls.supported_architectures:
            assert default_target is not None
            return default_target
        # otherwise fall back to the default specified in the class
        result = cls.default_architecture
        assert result is not None
        return result

    @classproperty
    def default_architecture(self) -> CrossCompileTarget:
        result = self._default_architecture
        if result is not None:
            return result
        # otherwise pick the first supported arch:
        return self.supported_architectures[0]

    @property
    def crosscompile_target(self):
        return self.get_crosscompile_target(self.config)

    def get_host_triple(self):
        compiler = get_compiler_info(self.host_CC)
        return compiler.default_target

    # noinspection PyPep8Naming
    @property
    def CC(self):
        return self.target_info.c_compiler

    # noinspection PyPep8Naming
    @property
    def CXX(self):
        return self.target_info.cxx_compiler

    # noinspection PyPep8Naming
    @property
    def CPP(self):
        return self.target_info.c_preprocessor

    # noinspection PyPep8Naming
    @property
    def host_CC(self):
        return TargetInfo.host_c_compiler(self.config)

    # noinspection PyPep8Naming
    @property
    def host_CXX(self):
        return TargetInfo.host_cxx_compiler(self.config)

    # noinspection PyPep8Naming
    @property
    def host_CPP(self):
        return TargetInfo.host_c_preprocessor(self.config)

    @classproperty
    def needs_sysroot(self):
        return not self._xtarget.is_native()  # Most projects need a sysroot (but not native)

    def compiling_for_mips(self, include_purecap: bool):
        return self.crosscompile_target.is_mips(include_purecap=include_purecap)

    def compiling_for_cheri(self):
        return self.crosscompile_target.is_cheri_purecap()

    def compiling_for_cheri_hybrid(self):
        return self.crosscompile_target.is_cheri_hybrid()

    def compiling_for_host(self):
        return self.crosscompile_target.is_native()

    def compiling_for_riscv(self, include_purecap: bool):
        return self.crosscompile_target.is_riscv(include_purecap=include_purecap)

    @property
    def triple_arch(self):
        target_triple = self.target_info.target_triple
        return target_triple[:target_triple.find("-")]

    @property
    def sdk_sysroot(self) -> Path:
        return self.target_info.sysroot_dir

    @property
    def cheri_config_suffix(self):
        return self.crosscompile_target.cheri_config_suffix(self.config)

    @property
    def sdk_bindir(self) -> Path:
        return self.target_info.sdk_root_dir / "bin"

    @property
    def display_name(self):
        if self._xtarget is None:
            return self.project_name + " (target alias)"
        return self.project_name + " (" + self._xtarget.build_suffix(self.config) + ")"

    @classmethod
    def get_class_for_target(cls: "typing.Type[Type_T]", arch: CrossCompileTarget) -> "typing.Type[Type_T]":
        target = target_manager.get_target_raw(cls.target)
        if isinstance(target, MultiArchTarget):
            # check for exact match
            if target.target_arch is arch:
                return target.project_class
            # Otherwise fall back to the target alias and find the matching one
            target = target.base_target
        if isinstance(target, MultiArchTargetAlias):
            for t in target.derived_targets:
                if t.target_arch is arch:
                    return t.project_class
        elif isinstance(target, Target):
            # single architecture target
            result = target.project_class
            if arch is None or result._xtarget is arch:
                return result
        raise LookupError("Invalid arch " + str(arch) + " for class " + str(cls))

    @property
    def cross_sysroot_path(self):
        assert self.target_info is not None, "called from invalid class " + str(self.__class__)
        return self.target_info.sysroot_dir

    # Duplicate all arguments instead of using **kwargs to get sensible code completion
    # noinspection PyShadowingBuiltins
    @staticmethod
    def run_cmd(*args, capture_output=False, capture_error=False, input: typing.Union[str, bytes] = None, timeout=None,
                print_verbose_only=False, run_in_pretend_mode=False, raise_in_pretend_mode=False, no_print=False,
                replace_env=False, **kwargs):
        return run_command(*args, capture_output=capture_output, capture_error=capture_error, input=input,
                           timeout=timeout,
                           print_verbose_only=print_verbose_only, run_in_pretend_mode=run_in_pretend_mode,
                           raise_in_pretend_mode=raise_in_pretend_mode, no_print=no_print, replace_env=replace_env,
                           **kwargs)

    @classmethod
    def add_config_option(cls, name: str, *, show_help=False, shortname=None, _no_fallback_config_name: bool = False,
                          kind: "Union[typing.Type[Type_T], Callable[[str], Type_T]]" = str,
                          default: "Union[ComputedDefaultValue[Type_T], Type_T, Callable[[], Type_T]]" = None,
                          only_add_for_targets: "typing.List[CrossCompileTarget]" = None,
                          extra_fallback_config_names: "typing.List[str]" = None,
                          _allow_unknown_targets=False, **kwargs) -> Type_T:
        # Need a string annotation for kind to avoid https://github.com/python/typing/issues/266 which seems to affect
        # the version of python in Ubuntu 16.04
        config_option_key = cls.target
        # if cls.target != cls.project_name.lower():
        #    self.fatal("Target name does not match project name:", cls.target, "vs", cls.project_name.lower())

        # Hide stuff like --foo/install-directory from --help
        help_hidden = not show_help

        # check that the group was defined in the current class not a superclass
        if "_commandline_option_group" not in cls.__dict__:
            # noinspection PyProtectedMember
            # has to be a single underscore otherwise the name gets mangled to _Foo__commandlineOptionGroup
            cls._commandline_option_group = cls._config_loader._parser.add_argument_group(
                "Options for target '" + cls.target + "'")
        # For targets such as qtbase-mips we want to fall back to checking the value of the option for qtbase
        fallback_name_base = getattr(cls, "_config_inherits_from", None)
        synthetic_base = getattr(cls, "synthetic_base", None)
        if cls.hide_options_from_help:
            help_hidden = True
        if synthetic_base is not None:
            # Don't show the help options for qtbase-mips/qtbase-native/qtbase-cheri in default --help output, the
            # base version is enough. They will still be included in --help-all
            help_hidden = True
            fallback_name_base = synthetic_base.target

        if only_add_for_targets is not None:
            # Some config options only apply to certain targets -> add them to those targets and the generic one
            target = cls._xtarget
            # If we are adding to the base class or the target is not in the list, emit a warning
            if not _allow_unknown_targets:
                for t in only_add_for_targets:
                    assert t in cls.supported_architectures, \
                        cls.__name__ + ": some of " + str(only_add_for_targets) + " not in " + str(
                            cls.supported_architectures)
            if target is not None and target not in only_add_for_targets:
                kwargs["option_cls"] = DefaultValueOnlyConfigOption

        # We don't want to inherit certain options from the non-target specific class since they should always be
        # set directly for that target. Currently the only such option is build-directory since sharing that would
        # break the build in most cases.
        # Important: Only look in the current class, not in parent classes to avoid duplicate names!
        fallback_config_names = []
        if not _no_fallback_config_name and fallback_name_base:
            if name not in ["build-directory"]:
                fallback_config_names.append(fallback_name_base + "/" + name)
            elif synthetic_base is not None:
                assert name == "build-directory"
                assert issubclass(cls, SimpleProject), cls
                # build-directory should only be inherited for the default target (e.g. cheribsd-cheri -> cheribsd):
                if cls.default_architecture is not None and cls.default_architecture is cls._xtarget:
                    # Don't allow cheribsd-purecap/build-directory to fall back to cheribsd/build-directory
                    # but if the project_name is the same we can assume it's the same class:
                    if cls.project_name == synthetic_base.project_name:
                        fallback_config_names.append(fallback_name_base + "/" + name)
        if extra_fallback_config_names:
            fallback_config_names.extend(extra_fallback_config_names)
        alias_target_names = [prefix + "/" + name for prefix in cls.__dict__.get("_alias_target_names", tuple())]
        return cls._config_loader.add_option(config_option_key + "/" + name, shortname, default=default, type=kind,
                                             _owning_class=cls, group=cls._commandline_option_group,
                                             help_hidden=help_hidden,
                                             _fallback_names=fallback_config_names, _alias_names=alias_target_names,
                                             **kwargs)

    @classmethod
    def add_bool_option(cls, name: str, *, shortname=None, only_add_for_targets: list = None,
                        default: "typing.Union[bool, ComputedDefaultValue[bool]]" = False, **kwargs) -> bool:
        # noinspection PyTypeChecker
        return cls.add_config_option(name, default=default, kind=bool, shortname=shortname, action="store_true",
                                     only_add_for_targets=only_add_for_targets, **kwargs)

    @classmethod
    def add_path_option(cls, name: str, *, shortname=None, only_add_for_targets: list = None, **kwargs) -> Path:
        return cls.add_config_option(name, kind=Path, shortname=shortname, only_add_for_targets=only_add_for_targets,
                                     **kwargs)

    __config_options_set = dict()  # typing.Dict[Type, bool]

    @classmethod
    def setup_config_options(cls, **kwargs):
        # assert cls not in cls.__config_options_set, "Setup called twice?"
        cls.__config_options_set[cls] = True

    def __init__(self, config: CheriConfig):
        self.target_info = self._xtarget.create_target_info(self)
        super().__init__(config)
        assert self._xtarget is not None, "Placeholder class should not be instantiated: " + repr(self)
        assert not self._should_not_be_instantiated, "Should not have instantiated " + self.__class__.__name__
        assert self.__class__ in self.__config_options_set, "Forgot to call super().setup_config_options()? " + str(
            self.__class__)
        self.__required_system_tools = {}  # type: typing.Dict[str, typing.Any]
        self.__required_system_headers = {}  # type: typing.Dict[str, typing.Any]
        self.__required_pkg_config = {}  # type: typing.Dict[str, typing.Any]
        self._system_deps_checked = False
        self._setup_called = False
        assert not hasattr(self, "gitBranch"), "gitBranch must not be used: " + self.__class__.__name__

    def setup(self):
        """
        Class setup that is run just before process()/run_tests/run_benchmarks. This ensures that all dependent targets
        have been built before and therefore querying e.g. the target compiler will work correctly.
        """
        assert not self._setup_called, "Should only be called once"
        self._setup_called = True

    def add_required_system_tool(self, executable: str, install_instructions=None, freebsd: str = None, apt: str = None,
                                 zypper: str = None, homebrew: str = None, cheribuild_target: str = None):
        if not install_instructions:
            install_instructions = OSInfo.install_instructions(executable, False, freebsd=freebsd, zypper=zypper,
                                                               apt=apt,
                                                               homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__required_system_tools[executable] = install_instructions

    def add_required_pkg_config(self, package: str, install_instructions=None, freebsd: str = None, apt: str = None,
                                zypper: str = None, homebrew: str = None, cheribuild_target: str = None):
        self.add_required_system_tool("pkg-config", freebsd="pkgconf", homebrew="pkg-config", apt="pkg-config")
        if not install_instructions:
            install_instructions = OSInfo.install_instructions(package, True, freebsd=freebsd, zypper=zypper, apt=apt,
                                                               homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__required_pkg_config[package] = install_instructions

    def add_required_system_header(self, header: str, install_instructions=None, freebsd: str = None, apt: str = None,
                                   zypper: str = None, homebrew: str = None, cheribuild_target: str = None):
        if not install_instructions:
            install_instructions = OSInfo.install_instructions(header, True, freebsd=freebsd, zypper=zypper, apt=apt,
                                                               homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__required_system_headers[header] = install_instructions

    @staticmethod
    def _query_yes_no(config: CheriConfig, message: str = "", *, default_result=False, force_result=True,
                      yes_no_str: str = None) -> bool:
        if yes_no_str is None:
            yes_no_str = " [Y]/n " if default_result else " y/[N] "
        if config.pretend:
            print(message + yes_no_str, coloured(AnsiColour.green, "y" if force_result else "n"), sep="", flush=True)
            return force_result  # in pretend mode we always return true
        if config.force:
            # in force mode we always return the forced result without prompting the user
            print(message + yes_no_str, coloured(AnsiColour.green, "y" if force_result else "n"), sep="", flush=True)
            return force_result
        if not sys.__stdin__.isatty():
            return default_result  # can't get any input -> return the default
        result = input(message + yes_no_str)
        if default_result:
            return not result.startswith("n")  # if default is yes accept anything other than strings starting with "n"
        return str(result).lower().startswith("y")  # anything but y will be treated as false

    def query_yes_no(self, message: str = "", *, default_result=False, force_result=True,
                     yes_no_str: str = None) -> bool:
        return self._query_yes_no(self.config, message, default_result=default_result, force_result=force_result,
                                  yes_no_str=yes_no_str)

    def ask_for_confirmation(self, message: str, error_message="Cannot continue.", default_result=True, **kwargs):
        if not self.query_yes_no(message, default_result=default_result, **kwargs):
            self.fatal(error_message)

    @staticmethod
    def _handle_stderr(outfile, stream, file_lock, project: "Project"):
        for errLine in stream:
            with file_lock:
                try:
                    # noinspection PyProtectedMember
                    if project._last_stdout_line_can_be_overwritten:
                        sys.stdout.buffer.write(b"\n")
                        flush_stdio(sys.stdout)
                        project._last_stdout_line_can_be_overwritten = False
                    sys.stderr.buffer.write(errLine)
                    flush_stdio(sys.stderr)
                    if project.config.write_logfile:
                        outfile.write(errLine)
                except ValueError:
                    # Don't print a backtrace on ctrl+C (since that will exit the main thread and close the file)
                    # ValueError: write to closed file
                    continue

    def _line_not_important_stdout_filter(self, line: bytes):
        # by default we don't keep any line persistent, just have updating output
        if self._last_stdout_line_can_be_overwritten:
            sys.stdout.buffer.write(Project._clear_line_sequence)
        sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
        sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
        flush_stdio(sys.stdout)
        self._last_stdout_line_can_be_overwritten = True

    def _show_line_stdout_filter(self, line: bytes):
        if self._last_stdout_line_can_be_overwritten:
            sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.write(line)
        flush_stdio(sys.stdout)
        self._last_stdout_line_can_be_overwritten = False

    def _stdout_filter(self, line: bytes):
        self._line_not_important_stdout_filter(line)

    def run_with_logfile(self, args: "typing.Sequence[str]", logfile_name: str, *, stdout_filter=None, cwd: Path = None,
                         env: dict = None, append_to_logfile=False) -> None:
        """
        Runs make and logs the output
        config.quiet doesn't display anything, normal only status updates and config.verbose everything
        :param append_to_logfile: whether to append to the logfile if it exists
        :param args: the command to run (e.g. ["make", "-j32"])
        :param logfile_name: the name of the logfile (e.g. "build.log")
        :param cwd the directory to run make in (defaults to self.build_dir)
        :param stdout_filter a filter to use for standard output (a function that takes a single bytes argument)
        :param env the environment to pass to make
        """
        print_command(args, cwd=cwd, env=env)
        # make sure that env is either None or a os.environ with the updated entries entries
        if env:
            new_env = os.environ.copy()
            env = {k: str(v) for k, v in env.items()}  # make sure everything is a string
            new_env.update(env)
        else:
            new_env = None
        assert not logfile_name.startswith("/")
        if self.config.write_logfile:
            logfile_path = self.build_dir / (logfile_name + ".log")
            print("Saving build log to", logfile_path)
        else:
            logfile_path = Path(os.devnull)
        if self.config.pretend:
            return
        if self.config.verbose:
            stdout_filter = None

        if self.config.write_logfile and logfile_path.is_file() and not append_to_logfile:
            logfile_path.unlink()  # remove old logfile
        args = list(map(str, args))  # make sure all arguments are strings
        cmd_str = commandline_to_str(args)

        if not self.config.write_logfile:
            if stdout_filter is None:
                # just run the process connected to the current stdout/stdin
                check_call_handle_noexec(args, cwd=str(cwd), env=new_env)
            else:
                make = popen_handle_noexec(args, cwd=str(cwd), stdout=subprocess.PIPE, env=new_env)
                self.__run_process_with_filtered_output(make, None, stdout_filter, cmd_str)
            return

        # open file in append mode
        with logfile_path.open("ab") as logfile:
            # print the command and then the logfile
            if append_to_logfile:
                logfile.write(b"\n\n")
            if cwd:
                logfile.write(("cd " + shlex.quote(str(cwd)) + " && ").encode("utf-8"))
            logfile.write(cmd_str.encode("utf-8") + b"\n\n")
            if self.config.quiet:
                # a lot more efficient than filtering every line
                check_call_handle_noexec(args, cwd=str(cwd), stdout=logfile, stderr=logfile, env=new_env)
                return
            make = popen_handle_noexec(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=new_env)
            self.__run_process_with_filtered_output(make, logfile, stdout_filter, cmd_str)

    def __run_process_with_filtered_output(self, proc: subprocess.Popen, logfile: "typing.Optional[typing.IO]",
                                           stdout_filter: "typing.Callable[[bytes], None]", cmd_str: str):
        logfile_lock = threading.Lock()  # we need a mutex so the logfile line buffer doesn't get messed up
        stderr_thread = None
        if logfile:
            # use a thread to print stderr output and write it to logfile (not using a thread would block)
            stderr_thread = threading.Thread(target=self._handle_stderr,
                                             args=(logfile, proc.stderr, logfile_lock, self))
            stderr_thread.start()
        for line in proc.stdout:
            with logfile_lock:  # make sure we don't interleave stdout and stderr lines
                if logfile:
                    logfile.write(line)
                if stdout_filter:
                    stdout_filter(line)
                else:
                    sys.stdout.buffer.write(line)
                    flush_stdio(sys.stdout)
        retcode = proc.wait()
        if stderr_thread:
            stderr_thread.join()
        # Not sure if the remaining call is needed
        remaining_err, remaining_out = proc.communicate()
        if remaining_err:
            print("Process had remaining stderr:", remaining_err)
            sys.stderr.buffer.write(remaining_err)
            if logfile:
                logfile.write(remaining_out)
        if remaining_out:
            print("Process had remaining stdout:", remaining_out)
            sys.stdout.buffer.write(remaining_out)
            if logfile:
                logfile.write(remaining_err)
        if stdout_filter and self._last_stdout_line_can_be_overwritten:
            # add the final new line after the filtering
            sys.stdout.buffer.write(b"\n")
        if retcode:
            message = "Command \"%s\" failed with exit code %d.\n" % (cmd_str, retcode)
            if logfile:
                message += "See " + logfile.name + " for details."
            raise SystemExit(message)

    def dependency_error(self, *args, install_instructions: str = None):
        self._system_deps_checked = True  # make sure this is always set
        if callable(install_instructions):
            install_instructions = install_instructions()
        self.fatal("Dependency for", self.target, "missing:", *args, fixit_hint=install_instructions)

    def check_system_dependencies(self) -> None:
        """
        Checks that all the system dependencies (required tool, etc) are available
        :return: Throws an error if dependencies are missing
        """
        for (tool, install_instructions) in self.__required_system_tools.items():
            if not shutil.which(str(tool)):
                if install_instructions is None or install_instructions == "":
                    install_instructions = "Try installing `" + tool + "` using your system package manager."
                self.dependency_error("Required program", tool, "is missing!",
                                      install_instructions=install_instructions)
        for (package, instructions) in self.__required_pkg_config.items():
            if not shutil.which("pkg-config"):
                # error should already have printed above
                break
            check_cmd = ["pkg-config", "--exists", package]
            print_command(check_cmd, print_verbose_only=True)
            exit_code = subprocess.call(check_cmd)
            if exit_code != 0:
                self.dependency_error("Required library", package, "is missing!", install_instructions=instructions)
        for (header, instructions) in self.__required_system_headers.items():
            if not Path("/usr/include", header).exists() and not Path("/usr/local/include", header).exists():
                self.dependency_error("Required C header", header, "is missing!", install_instructions=instructions)
        self._system_deps_checked = True

    def process(self):
        raise NotImplementedError()

    def run_tests(self):
        # for the --test option
        status_update("No tests defined for target", self.target)

    def run_benchmarks(self):
        # for the --benchmark option
        status_update("No benchmarks defined for target", self.target)

    @staticmethod
    def get_test_script_path(script_name: str) -> Path:
        # noinspection PyUnusedLocal
        script_dir = Path("/this/will/not/work/when/using/remote-cheribuild.py")
        # generate a sensible error when using remote-cheribuild.py by omitting this line:
        script_dir = Path(__file__).parent.parent.parent / "test-scripts"  # no-combine
        return script_dir / script_name

    def run_shell_script(self, script, shell="sh", **kwargs):
        print_args = dict(**kwargs)
        if "capture_output" in print_args:
            del print_args["capture_output"]
        print_command(shell, "-xe" if self.config.verbose else "-e", "-c", script, **print_args)
        kwargs["no_print"] = True
        return run_command(shell, "-xe" if self.config.verbose else "-e", "-c", script, **kwargs)

    def print(self, *args, **kwargs):
        if not self.config.quiet:
            print(*args, **kwargs)

    def verbose_print(self, *args, **kwargs):
        if self.config.verbose:
            print(*args, **kwargs)

    @staticmethod
    def info(*args, **kwargs):
        # TODO: move all those methods here
        status_update(*args, **kwargs)

    @staticmethod
    def warning(*args, **kwargs):
        warning_message(*args, **kwargs)

    @staticmethod
    def fatal(*args, sep=" ", fixit_hint=None, fatal_when_pretending=False):
        fatal_error(*args, sep=sep, fixit_hint=fixit_hint, fatal_when_pretending=fatal_when_pretending)


def install_dir_not_specified(_: CheriConfig, project: "Project"):
    raise RuntimeError("install_dir_not_specified! dummy impl must not be called: " + str(project))


def _default_build_dir(config: CheriConfig, project: "SimpleProject"):
    assert isinstance(project, Project)
    target = project.get_crosscompile_target(config)
    return project.build_dir_for_target(target)


class MakeCommandKind(Enum):
    DefaultMake = "system default make"
    GnuMake = "GNU make"
    BsdMake = "BSD make"
    Ninja = "ninja"
    CustomMakeTool = "custom make tool"


class MakeOptions(object):
    def __init__(self, kind: MakeCommandKind, project: SimpleProject, **kwargs):
        self.__project = project
        self._vars = OrderedDict()
        # Used by e.g. FreeBSD:
        self._with_options = OrderedDict()  # type: typing.Dict[str, bool]
        self._flags = list()
        self.env_vars = {}
        self.set(**kwargs)
        self.kind = kind
        self.__can_pass_j_flag = None  # type: typing.Optional[bool]
        self.__command = None  # type: typing.Optional[str]
        self.__command_args = []  # type: typing.List[str]

    def __deepcopy__(self, memo):
        assert False, "Should not be called!"
        pass

    @staticmethod
    def __do_set(target_dict: "typing.Dict[str, str]", **kwargs):
        for k, v in kwargs.items():
            if isinstance(v, bool):
                v = "1" if v else "0"
            if isinstance(v, (Path, int)):
                v = str(v)
            assert isinstance(v, str), "Should only pass int/bool/str/Path here!"
            target_dict[k] = v

    def set(self, **kwargs):
        self.__do_set(self._vars, **kwargs)

    def set_env(self, **kwargs):
        self.__do_set(self.env_vars, **kwargs)

    def set_with_options(self, **kwargs):
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
                # Prefer homebrew-installed gmake if it is available
                self.__project.add_required_system_tool("gmake", homebrew="make")
                return "gmake"
            else:
                self.__project.add_required_system_tool("make")
                return "make"
        elif self.kind == MakeCommandKind.GnuMake:
            if OSInfo.IS_LINUX and not shutil.which("gmake"):
                status_update("Could not find `gmake` command, assuming `make` is GNU make")
                self.__project.add_required_system_tool("make")
                return "make"
            else:
                self.__project.add_required_system_tool("gmake", homebrew="make")
                return "gmake"
        elif self.kind == MakeCommandKind.BsdMake:
            if OSInfo.IS_FREEBSD:
                return "make"
            else:
                self.__project.add_required_system_tool("bmake", homebrew="bmake", cheribuild_target="bmake")
                return "bmake"
        elif self.kind == MakeCommandKind.Ninja:
            self.__project.add_required_system_tool("ninja", homebrew="ninja", apt="ninja-build")
            return "ninja"
        else:
            if self.__command is not None:
                return self.__command
            self.__project.fatal("Cannot infer path from CustomMakeTool. Set self.make_args.set_command(\"tool\")")
            raise RuntimeError()

    def set_command(self, value, can_pass_j_flag=True, early_args: "typing.List[str]" = None, *,
                    install_instructions=None):
        self.__command = str(value)
        if early_args is None:
            early_args = []
        self.__command_args = early_args
        assert isinstance(self.__command_args, list)
        # noinspection PyProtectedMember
        if not Path(value).is_absolute():
            self.__project.add_required_system_tool(value, install_instructions=install_instructions)
        self.__can_pass_j_flag = can_pass_j_flag

    @property
    def all_commandline_args(self) -> list:
        assert self.kind
        result = list(self.__command_args)
        # First all the variables
        for k, v in self._vars.items():
            assert isinstance(v, str)
            if v == "1":
                result.append(self._get_defined_var(k))
            else:
                result.append(k + "=" + v)
        # then the WITH/WITHOUT variables
        for k, v in self._with_options.items():
            result.append(self._get_defined_var("WITH_" if v else "WITHOUT_") + k)
        # and finally the command line flags like -k
        result.extend(self._flags)
        return result

    def remove_var(self, variable):
        if variable in self._vars:
            del self._vars[variable]
        if variable in self._with_options:
            del self._with_options[variable]
        for flag in self._flags.copy():
            if flag.strip() == "-D" + variable or flag.startswith(variable + "="):
                self._flags.remove(flag)

    def get_var(self, variable, default=None):
        return self._vars.get(variable, default)

    def remove_flag(self, flag: str):
        if flag in self._flags:
            self._flags.remove(flag)

    def remove_all(self, predicate: "typing.Callable[[str], bool]"):
        keys = list(self._vars.keys())
        for k in keys:
            if predicate(k):
                del self._vars[k]

    def copy(self):
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


class SourceRepository(object):
    def ensure_cloned(self, current_project: "Project", *, src_dir: Path, default_src_dir: Path,
                      skip_submodules=False) -> None:
        raise NotImplementedError

    def update(self, current_project: "Project", *, src_dir: Path, default_src_dir: Path = None, revision=None,
               skip_submodules=False) -> None:
        raise NotImplementedError

    def get_real_source_dir(self, caller: SimpleProject, default_src_dir: Path) -> Path:
        return default_src_dir


class ExternallyManagedSourceRepository(SourceRepository):
    def ensure_cloned(self, current_project: "Project", src_dir: Path, **kwargs):
        current_project.info("Not cloning repositiory since it is externally managed")

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        current_project.info("Not updating", src_dir, "since it is externally managed")


class ReuseOtherProjectRepository(SourceRepository):
    def __init__(self, source_project: "typing.Type[Project]", *, subdirectory=".",
                 repo_for_target: CrossCompileTarget = None, do_update=False):
        self.source_project = source_project
        self.subdirectory = subdirectory
        self.repo_for_target = repo_for_target
        self.do_update = do_update

    def ensure_cloned(self, current_project: "Project", **kwargs) -> None:
        # noinspection PyProtectedMember
        src = self.get_real_source_dir(current_project, current_project._initial_source_dir)
        if not src.exists():
            current_project.fatal("Source repository for target", current_project.target, "does not exist.",
                                  fixit_hint="This project uses the sources from the " + self.source_project.target +
                                             "target so you will have to clone that first. Try running:\n\t`" +
                                             "cheribuild.py " + self.source_project.target +
                                             "--no-skip-update --skip-configure --skip-build --skip-install`")

    def get_real_source_dir(self, caller: SimpleProject, default_src_dir: typing.Optional[Path]) -> Path:
        return self.source_project.get_source_dir(caller, caller.config,
                                                  cross_target=self.repo_for_target) / self.subdirectory

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        if self.do_update:
            src_proj = self.source_project.get_instance(current_project, cross_target=self.repo_for_target)
            src_proj.update()
        else:
            current_project.info("Not updating", src_dir, "since it reuses the repository for ",
                                 self.source_project.target)


class ReuseOtherProjectDefaultTargetRepository(ReuseOtherProjectRepository):
    def __init__(self, source_project: "typing.Type[Project]", *, subdirectory="."):
        super().__init__(source_project, subdirectory=subdirectory,
                         repo_for_target=source_project.supported_architectures[0])


# Use git-worktree to handle per-target branches:
class TargetBranchInfo(object):
    def __init__(self, branch: str, directory_name: str, url: str = None):
        self.branch = branch
        self.directory_name = directory_name
        self.url = url


_PRETEND_RUN_GIT_COMMANDS = os.getenv("_TEST_SKIP_GIT_COMMANDS") is None


class GitRepository(SourceRepository):
    def __init__(self, url, *, old_urls: typing.List[bytes] = None, default_branch: str = None,
                 force_branch: bool = False,
                 per_target_branches: typing.Dict[CrossCompileTarget, TargetBranchInfo] = None):
        self.url = url
        self.old_urls = old_urls
        self.default_branch = default_branch
        self.force_branch = force_branch
        if per_target_branches is None:
            per_target_branches = dict()
        self.per_target_branches = per_target_branches

    def ensure_cloned(self, current_project: "Project", *, src_dir: Path, default_src_dir: Path,
                      skip_submodules=False) -> None:
        if default_src_dir is None:
            default_src_dir = src_dir
        # git-worktree creates a .git file instead of a .git directory so we can't use .is_dir()
        if not (default_src_dir / ".git").exists():
            if current_project.config.skip_clone:
                current_project.fatal("Sources for", str(default_src_dir), " missing!")
            assert isinstance(self.url, str), self.url
            assert not self.url.startswith("<"), "Invalid URL " + self.url
            if not current_project.query_yes_no(
                    str(default_src_dir) + " is not a git repository. Clone it from '" + self.url + "'?"):
                current_project.fatal("Sources for", str(default_src_dir), " missing!")
            clone_cmd = ["git", "clone"]
            if current_project.config.shallow_clone:
                # Note: we pass --no-single-branch since otherwise git fetch will not work with branches and
                # the solution of running  `git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"`
                # is not very intuitive. This increases the amount of data fetched but increases usability
                clone_cmd.extend(["--depth", "1", "--no-single-branch"])
            if not skip_submodules:
                clone_cmd.append("--recurse-submodules")
            if self.default_branch:
                clone_cmd += ["--branch", self.default_branch]
            current_project.run_cmd(clone_cmd + [self.url, default_src_dir], cwd="/")
            # Could also do this but it seems to fetch more data than --no-single-branch
            # if self.config.shallow_clone:
            #    current_project.run_cmd(["git", "config", "remote.origin.fetch",
            #                             "+refs/heads/*:refs/remotes/origin/*"], cwd=src_dir)

        if src_dir == default_src_dir:
            return  # Nothing else to do

        # Handle per-target overrides by adding a new git-worktree git-worktree
        target_override = self.per_target_branches.get(current_project.crosscompile_target, None)
        assert target_override is not None, "Default src != src -> must have a per-target override"
        if (src_dir / ".git").exists():
            return
        current_project.info("Creating git-worktree checkout of", default_src_dir, "with branch",
                             target_override.branch, "for", src_dir)

        # Find the first valid remote
        per_target_url = target_override.url if target_override.url else self.url
        remote_name = "origin"
        remotes = run_command(["git", "-C", default_src_dir, "remote", "-v"], capture_output=True).stdout.decode(
            "utf-8")  # type: str
        for r in remotes.splitlines():
            if per_target_url in r:
                remote_name = r.split()[0].strip()
        while True:
            try:
                url = run_command(["git", "-C", default_src_dir, "remote", "get-url", remote_name],
                                  capture_output=True).stdout.decode("utf-8").strip()
            except subprocess.CalledProcessError as e:
                current_project.warning("Could not determine URL for remote", remote_name, str(e))
                url = None
            if url == self.url:
                break
            current_project.info("URL '", url, "' for remote ", remote_name, " does not match expected url '",
                                 self.url, "'", sep="")
            if current_project.query_yes_no("Use this remote?"):
                break
            remote_name = input("Please enter the correct remote: ")
        run_command(["git", "worktree", "add", "--track", "-b", target_override.branch, src_dir,
                     remote_name + "/" + target_override.branch], cwd=default_src_dir)

    def get_real_source_dir(self, caller: SimpleProject, default_src_dir: Path) -> Path:
        target_override = self.per_target_branches.get(caller.crosscompile_target, None)
        if target_override is None:
            return default_src_dir
        return default_src_dir.with_name(target_override.directory_name)

    def update(self, current_project: "Project", *, src_dir: Path, default_src_dir: Path = None, revision=None,
               skip_submodules=False):
        self.ensure_cloned(current_project, src_dir=src_dir, default_src_dir=default_src_dir,
                           skip_submodules=skip_submodules)
        if current_project.skip_update:
            return
        if not src_dir.exists():
            return

        # handle repositories that have moved
        if src_dir.exists() and self.old_urls:
            # Update from the old url:
            for old_url in self.old_urls:
                assert isinstance(old_url, bytes)
                remote_url = run_command("git", "remote", "get-url", "origin", capture_output=True,
                                         cwd=src_dir).stdout.strip()
                if remote_url == old_url:
                    current_project.warning(current_project.project_name, "still points to old repository", remote_url)
                    if current_project.query_yes_no("Update to correct URL?"):
                        run_command("git", "remote", "set-url", "origin", self.url,
                                    run_in_pretend_mode=_PRETEND_RUN_GIT_COMMANDS, cwd=src_dir)

        # First fetch all the current upstream branch to see if we need to autostash/pull.
        # Note: "git fetch" without other arguments will fetch from the currently configured upstream.
        # If there is no upstream, it will just return immediately.
        run_command("git", "fetch", cwd=src_dir)

        # Handle forced branches now that we have fetched the latest changes
        if src_dir.exists() and self.force_branch:
            assert self.default_branch, "default_branch must be set if force_branch is true!"
            # TODO: move this to Project so it can also be used for other targets
            status = run_command("git", "status", "-b", "-s", "--porcelain", "-u", "no",
                                 capture_output=True, print_verbose_only=True, cwd=src_dir,
                                 run_in_pretend_mode=_PRETEND_RUN_GIT_COMMANDS)
            if status.stdout.startswith(b"## ") and not status.stdout.startswith(
                    b"## " + self.default_branch.encode("utf-8") + b"..."):
                current_branch = status.stdout[3:status.stdout.find(b"...")].strip()
                current_project.warning("You are trying to build the", current_branch.decode("utf-8"),
                                        "branch. You should be using", self.default_branch)
                if current_project.query_yes_no("Would you like to change to the " + self.default_branch + " branch?"):
                    run_command("git", "checkout", self.default_branch, cwd=src_dir)
                else:
                    current_project.ask_for_confirmation("Are you sure you want to continue?", force_result=False,
                                                         error_message="Wrong branch: " + current_branch.decode(
                                                             "utf-8"))

        # We don't need to update if the upstream commit is an ancestor of the current HEAD.
        # This check ensures that we avoid a rebase if the current branch is a few commits ahead of upstream.
        # Note: merge-base --is-ancestor exits with code 0/1 instead of printing output so we need a try/catch
        is_ancestor = run_command("git", "merge-base", "--is-ancestor", "@{upstream}", "HEAD", cwd=src_dir,
                                  print_verbose_only=True, capture_error=True, allow_unexpected_returncode=True,
                                  run_in_pretend_mode=_PRETEND_RUN_GIT_COMMANDS, raise_in_pretend_mode=True)
        if is_ancestor.returncode == 0:
            current_project.verbose_print(coloured(AnsiColour.blue, "Current HEAD is up-to-date or ahead of upstream."))
            return
        elif is_ancestor.returncode == 128 or (is_ancestor.stderr and "no upstream configured" in is_ancestor.stderr):
            current_project.info("No upstream configured to update from")
            return
        elif is_ancestor.returncode == 1:
            current_project.verbose_print(coloured(AnsiColour.blue, "Current HEAD is behind upstream."))
        else:
            current_project.warning("Unknown return code", is_ancestor)
            # some other error -> raise so that I can see what went wrong
            raise subprocess.CalledProcessError(is_ancestor.retcode, is_ancestor.args, output=is_ancestor.stdout,
                                                stderr=is_ancestor.stderr)

        # make sure we run git stash if we discover any local changes
        has_changes = len(run_command("git", "diff", "--stat", "--ignore-submodules",
                                      capture_output=True, cwd=src_dir, print_verbose_only=True).stdout) > 1

        pull_cmd = ["git", "pull"]
        has_autostash = False
        git_version = get_program_version(Path(shutil.which("git"))) if shutil.which("git") else (0, 0, 0)
        # Use the autostash flag for Git >= 2.14 (https://stackoverflow.com/a/30209750/894271)
        if git_version >= (2, 14):
            has_autostash = True
            pull_cmd.append("--autostash")

        if has_changes:
            print(coloured(AnsiColour.green, "Local changes detected in", src_dir))
            # TODO: add a config option to skip this query?
            if current_project.config.force_update:
                status_update("Updating", src_dir, "with autostash due to --force-update")
            elif not current_project.query_yes_no("Stash the changes, update and reapply?", default_result=True,
                                                  force_result=True):
                status_update("Skipping update of", src_dir)
                return
            if not has_autostash:
                # TODO: ask if we should continue?
                stash_result = run_command("git", "stash", "save", "Automatic stash by cheribuild.py",
                                           capture_output=True, cwd=src_dir, print_verbose_only=True).stdout
                # print("stash_result =", stash_result)
                if "No local changes to save" in stash_result.decode("utf-8"):
                    # print("NO REAL CHANGES")
                    has_changes = False  # probably git diff showed something from a submodule

        if not skip_submodules:
            pull_cmd.append("--recurse-submodules")
        rebase_flag = "--rebase=merges" if git_version >= (2, 18) else "--rebase=preserve"
        run_command(pull_cmd + [rebase_flag], cwd=src_dir, print_verbose_only=True)
        if not skip_submodules:
            run_command("git", "submodule", "update", "--init", "--recursive", cwd=src_dir, print_verbose_only=True)
        if has_changes and not has_autostash:
            run_command("git", "stash", "pop", cwd=src_dir, print_verbose_only=True)
        if revision:
            run_command("git", "checkout", revision, cwd=src_dir, print_verbose_only=True)


class DefaultInstallDir(Enum):
    DO_NOT_INSTALL = "Should not be installed"
    IN_BUILD_DIRECTORY = "$BUILD_DIR/test-install-prefix"
    ROOTFS = "The rootfs for this target"
    COMPILER_RESOURCE_DIR = "The compiler resource directory"
    SYSROOT = "The sysroot for this target"
    SYSROOT_AND_ROOTFS = "The sysroot for this target and the sysroot"
    CHERI_SDK = "The CHERI SDK directory"
    BOOTSTRAP_TOOLS = "The bootstap tools directory"
    CUSTOM_INSTALL_DIR = "Custom install directory"
    SYSROOT_FOR_BAREMETAL_ROOTFS_OTHERWISE = "Sysroot for baremetal projects, rootfs otherwise"


_INVALID_INSTALL_DIR = Path("/this/dir/should/be/overwritten/and/not/used/!!!!")
_DO_NOT_INSTALL_PATH = Path("/this/project/should/not/be/installed!!!!")


# noinspection PyProtectedMember
def _default_install_dir_handler(config: CheriConfig, project: "Project") -> Path:
    install_dir = project.get_default_install_dir_kind()
    if install_dir == DefaultInstallDir.DO_NOT_INSTALL:
        return _DO_NOT_INSTALL_PATH
    elif install_dir == DefaultInstallDir.IN_BUILD_DIRECTORY:
        return project.build_dir / "test-install-prefix"
    elif install_dir == DefaultInstallDir.ROOTFS:
        assert not project.compiling_for_host(), "Should not use DefaultInstallDir.ROOTFS for native builds!"
        rootfs_target = project.target_info.get_rootfs_project()
        if hasattr(project, "path_in_rootfs"):
            assert project.path_in_rootfs.startswith("/"), project.path_in_rootfs
            return rootfs_target.install_dir / project.path_in_rootfs[1:]
        return Path(
            rootfs_target.install_dir / "opt" / project.target_info.install_prefix_dirname /
            project._rootfs_install_dir_name)
    elif install_dir == DefaultInstallDir.COMPILER_RESOURCE_DIR:
        compiler_for_resource_dir = project.CC
        # For the NATIVE variant we want to install to CHERI clang:
        if project.compiling_for_host():
            compiler_for_resource_dir = config.cheri_sdk_bindir / "clang"
        return get_compiler_info(compiler_for_resource_dir).get_resource_dir()
    elif install_dir == DefaultInstallDir.SYSROOT or install_dir == DefaultInstallDir.SYSROOT_AND_ROOTFS:
        return project.sdk_sysroot
    elif install_dir == DefaultInstallDir.CHERI_SDK:
        assert project.compiling_for_host(), "CHERI_SDK is only a valid install dir for native, " \
                                             "use SYSROOT/ROOTFS for cross"
        return config.cheri_sdk_dir
    elif install_dir == DefaultInstallDir.BOOTSTRAP_TOOLS:
        assert project.compiling_for_host(), "BOOTSTRAP_TOOLS is only a valid install dir for native, " \
                                             "use SYSROOT/ROOTS for cross"
        return config.other_tools_dir
    elif install_dir == DefaultInstallDir.CUSTOM_INSTALL_DIR:
        return _INVALID_INSTALL_DIR
    project.fatal("Unknown install dir for", project.project_name)


def _default_install_dir_str(project: "Project") -> str:
    install_dir = project.get_default_install_dir_kind()
    return str(install_dir.value)
    # fatal_error("Unknown install dir for", project.project_name)


class Project(SimpleProject):
    repository = None  # type: SourceRepository
    # is_large_source_repository can be set to true to set some git config options to speed up operations:
    # Ideally this would be a flag in GitRepository, but that will not work with inheritance (since some
    # subclasses use different repositories and they would all have to set that flag again). Annoying for LLVM/FreeBSD
    is_large_source_repository = False
    git_revision = None
    skip_git_submodules = False
    compile_db_requires_bear = True
    do_not_add_to_targets = True

    build_dir_suffix = ""  # add a suffix to the build dir (e.g. for freebsd-with-bootstrap-clang)
    add_build_dir_suffix_for_native = False  # Whether to add -native to the native build dir

    default_source_dir = ComputedDefaultValue(
        function=lambda config, project: Path(config.source_root / project.project_name.lower()),
        as_string=lambda cls: "$SOURCE_ROOT/" + cls.project_name.lower())

    @classmethod
    def dependencies(cls, config: CheriConfig):
        # TODO: can I avoid instantiating all cross-compile targets here? The hack below might work
        target = cls.get_crosscompile_target(config)  # type: CrossCompileTarget
        result = target.target_info_cls.toolchain_targets(target, config)
        if cls.needs_sysroot:
            result += target.target_info_cls.base_sysroot_targets(target, config)
        return result

    @classmethod
    def project_build_dir_help(cls):
        result = "$BUILD_ROOT/" + cls.project_name.lower()
        if cls._xtarget is not BasicCompilationTargets.NATIVE or cls.add_build_dir_suffix_for_native:
            result += "-$TARGET"
        result += "-build"
        return result

    default_build_dir = ComputedDefaultValue(
        function=_default_build_dir, as_string=lambda cls: cls.project_build_dir_help())

    make_kind = MakeCommandKind.DefaultMake
    """
    The kind of too that is used for building and installing (defaults to using "make")
    Set this to MakeCommandKind.GnuMake if the build system needs GNU make features or BsdMake if it needs bmake
    """

    # A per-project config option to generate a CMakeLists.txt that just has a custom taget that calls cheribuild.py
    generate_cmakelists = None

    # TODO: remove these three
    @classmethod
    def get_source_dir(cls, caller: "SimpleProject", config: CheriConfig = None,
                       cross_target: CrossCompileTarget = None):
        return cls.get_instance(caller, config, cross_target).source_dir

    @classmethod
    def get_build_dir(cls, caller: "SimpleProject", config: CheriConfig = None,
                      cross_target: CrossCompileTarget = None):
        return cls.get_instance(caller, config, cross_target).build_dir

    @classmethod
    def get_install_dir(cls, caller: "SimpleProject", config: CheriConfig = None,
                        cross_target: CrossCompileTarget = None):
        return cls.get_instance(caller, config, cross_target).real_install_root_dir

    def build_configuration_suffix(self, target: typing.Optional[CrossCompileTarget] = None) -> str:
        """
        :param target: the target to use
        :return: a string such as -128/-native-asan that identifies the build configuration
        """
        config = self.config
        if target is None:
            target = self.get_crosscompile_target(config)
        result = ""
        if self.build_dir_suffix:
            result += self.build_dir_suffix
        if self.use_asan:
            result += "-asan"
        if self.auto_var_init != AutoVarInit.NONE:
            result += "-init-" + str(self.auto_var_init.value)
        # targets that only support native might not need a suffix
        if not target.is_native() or self.add_build_dir_suffix_for_native:
            result += target.build_suffix(config)
        return result

    def build_dir_for_target(self, target: CrossCompileTarget):
        return self.config.build_root / (self.project_name.lower() + self.build_configuration_suffix(target) + "-build")

    default_use_asan = False

    @classproperty
    def can_build_with_asan(self):
        return self._xtarget is None or not self._xtarget.is_cheri_purecap()

    @classmethod
    def get_default_install_dir_kind(cls) -> DefaultInstallDir:
        if cls.default_install_dir is not None:
            assert cls.native_install_dir is None, "default_install_dir and native_install_dir are mutually " \
                                                   "exclusive"
            assert cls.cross_install_dir is None, "default_install_dir and cross_install_dir are mutually exclusive"
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
                install_dir = DefaultInstallDir.SYSROOT
            else:
                install_dir = DefaultInstallDir.ROOTFS
        return install_dir

    default_install_dir = None  # type: typing.Optional[DefaultInstallDir]
    # To provoide different install locations when cross-compiling and when native
    native_install_dir = None  # type: typing.Optional[DefaultInstallDir]
    cross_install_dir = None  # type: typing.Optional[DefaultInstallDir]
    # For more precise control over the install dir it is possible to provide a callback function
    _default_install_dir_fn = ComputedDefaultValue(function=_default_install_dir_handler,
                                                   as_string=_default_install_dir_str)
    """ The default installation directory """

    @property
    def _rootfs_install_dir_name(self):
        return self.project_name.lower()

    # useful for cross compile projects that use a prefix and DESTDIR
    _install_prefix = None
    destdir = None

    __can_use_lld_map = dict()  # type: typing.Dict[Path, bool]

    @classmethod
    def can_use_lld(cls, compiler: Path):
        if OSInfo.IS_MAC:
            return False  # lld does not work on MacOS
        if compiler not in cls.__can_use_lld_map:
            try:
                run_command([compiler, "-fuse-ld=lld", "-xc", "-o", "/dev/null", "-"], run_in_pretend_mode=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, raise_in_pretend_mode=True,
                            input="int main() { return 0; }\n", print_verbose_only=True)
                status_update(compiler, "supports -fuse-ld=lld, linking should be much faster!")
                cls.__can_use_lld_map[compiler] = True
            except subprocess.CalledProcessError:
                status_update(compiler, "does not support -fuse-ld=lld, using slower bfd instead")
                cls.__can_use_lld_map[compiler] = False
        return cls.__can_use_lld_map[compiler]

    @classmethod
    def can_use_lto(cls, ccinfo: CompilerInfo):
        if ccinfo.compiler == "apple-clang":
            return True
        elif ccinfo.compiler == "clang" and ccinfo.version >= (4, 0, 0) and cls.can_use_lld(ccinfo.path):
            return True
        else:
            return False

    def check_system_dependencies(self):
        # Check that the make command exists (this will also add it to the required system tools)
        if self.make_args.command is None:
            self.fatal("Make command not set!")
        super().check_system_dependencies()

    lto_by_default = False  # Don't default to LTO
    prefer_full_lto_over_thin_lto = False  # If LTO is enabled, use LLVM's ThinLTO by default
    lto_set_ld = True
    default_build_type = BuildType.DEFAULT
    default_auto_var_init = AutoVarInit.NONE

    @classmethod
    def setup_config_options(cls, install_directory_help="", **kwargs):
        super().setup_config_options(**kwargs)
        cls._initial_source_dir = cls.add_path_option("source-directory", metavar="DIR", default=cls.default_source_dir,
                                                      help="Override default source directory for " + cls.project_name)
        cls.build_dir = cls.add_path_option("build-directory", metavar="DIR", default=cls.default_build_dir,
                                            help="Override default source directory for " + cls.project_name)
        if cls.can_build_with_asan:
            asan_default = ComputedDefaultValue(
                function=lambda config, proj: False if proj.get_crosscompile_target(
                    config).is_cheri_purecap() else proj.default_use_asan,
                as_string=str(cls.default_use_asan))
            cls.use_asan = cls.add_bool_option("use-asan", default=asan_default,
                                               help="Build with AddressSanitizer enabled")
        else:
            cls.use_asan = False
        cls.auto_var_init = cls.add_config_option("auto-var-init", kind=AutoVarInit,
                                                  default=ComputedDefaultValue(
                                                      lambda config, proj: proj.default_auto_var_init,
                                                      lambda c: (
                                                              "the value of the global --skip-update option ("
                                                              "defaults to \"" +
                                                              c.default_auto_var_init.value + "\")")),
                                                  help="Whether to initialize all local variables (currently only "
                                                       "supported when compiling with clang)")
        cls.skip_update = cls.add_bool_option("skip-update",
                                              default=ComputedDefaultValue(lambda config, proj: config.skip_update,
                                                                           "the value of the global --skip-update "
                                                                           "option"),
                                              help="Override --skip-update/--no-skip-update for this target only ")

        if not install_directory_help:
            install_directory_help = "Override default install directory for " + cls.project_name
        cls._install_dir = cls.add_path_option("install-directory", metavar="DIR", help=install_directory_help,
                                               default=cls._default_install_dir_fn)
        if "repository" in cls.__dict__ and isinstance(cls.repository, GitRepository):
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
        if "generate_cmakelists" not in cls.__dict__:
            # Make sure not to dereference a parent class descriptor here -> use getattr_static
            option = inspect.getattr_static(cls, "generate_cmakelists")
            # If option is not a fixed bool then we need a command line option:
            if not isinstance(option, bool):
                assert option is None or isinstance(option, ConfigOptionBase)
                assert not issubclass(cls,
                                      CMakeProject), "generate_cmakelists option needed -> should not be a CMakeProject"
                cls.generate_cmakelists = cls.add_bool_option("generate-cmakelists",
                                                              help="Generate a CMakeLists.txt that just calls "
                                                                   "cheribuild. "
                                                                   "Useful for IDEs that only support CMake")
            else:
                assert issubclass(cls, CMakeProject), "Should be a CMakeProject: " + cls.__name__

        cls.use_lto = cls.add_bool_option("use-lto", help="Build with link-time optimization (LTO)",
                                          default=cls.lto_by_default)
        cls.use_cfi = False  # doesn't work yet
        cls._linkage = cls.add_config_option("linkage", default=Linkage.DEFAULT, kind=Linkage,
                                             help="Build static or dynamic (or use the project default)")

        cls.build_type = cls.add_config_option("build-type",
                                               help="Optimization+debuginfo defaults (supports the same values as "
                                                    "CMake (as well as 'DEFAULT' which"
                                                    " does not pass any additional flags to the configure command).",
                                               default=cls.default_build_type, kind=BuildType,
                                               enum_choice_strings=[t.value for t in BuildType])  # type: BuildType

    def linkage(self):
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

    _force_debug_info = None  # Override the debug info setting from --build-type

    @property
    def should_include_debug_info(self) -> bool:
        if self._force_debug_info is not None:
            return self._force_debug_info
        return self.build_type.should_include_debug_info

    def should_use_extra_c_compat_flags(self):
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
            return ["-O0"]
        elif cbt in (BuildType.RELEASE, BuildType.RELWITHDEBINFO):
            return ["-O2"]
        elif cbt in (BuildType.MINSIZEREL, BuildType.MINSIZERELWITHDEBINFO):
            return ["-Os"]

    needs_mxcaptable_static = False  # E.g. for postgres which is just over the limit:
    needs_mxcaptable_dynamic = False  # This might be true for Qt/QtWebkit

    @property
    def compiler_warning_flags(self):
        if self.compiling_for_host():
            return self.common_warning_flags + self.host_warning_flags
        else:
            return self.common_warning_flags + self.cross_warning_flags

    @property
    def default_compiler_flags(self):
        assert self._setup_called
        result = []
        if self.use_lto:
            result.extend(self._lto_compiler_flags)
        if self.use_cfi:
            if not self.use_lto:
                self.fatal("Cannot use CFI without LTO!")
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
            result.append("-fvisibility=hidden")
        if self.compiling_for_host():
            return result + self.COMMON_FLAGS + self.compiler_warning_flags + self.optimization_flags
        result += self.target_info.essential_compiler_and_linker_flags + self.optimization_flags
        result += self.COMMON_FLAGS + self.compiler_warning_flags
        if self.config.csetbounds_stats:
            result.extend(["-mllvm", "-collect-csetbounds-output=" + str(self.csetbounds_stats_file),
                           "-mllvm", "-collect-csetbounds-stats=csv",
                           # "-Xclang", "-cheri-bounds=everywhere-unsafe"])
                           ])
        # Add mxcaptable for projects that need it
        if self.compiling_for_mips(include_purecap=True):
            if self.crosscompile_target.is_cheri_purecap():
                if self.force_static_linkage and self.needs_mxcaptable_static:
                    result.append("-mxcaptable")
                if self.force_dynamic_linkage and self.needs_mxcaptable_dynamic:
                    result.append("-mxcaptable")
            # Do the same for MIPS to get even performance comparisons
            else:
                if self.force_static_linkage and self.needs_mxcaptable_static:
                    result.extend(["-mxgot", "-mllvm", "-mxmxgot"])
                if self.force_dynamic_linkage and self.needs_mxcaptable_dynamic:
                    result.extend(["-mxgot", "-mllvm", "-mxmxgot"])
        return result

    @property
    def default_ldflags(self):
        result = list(self.COMMON_LDFLAGS)
        if self.use_lto:
            result.extend(self._lto_linker_flags)
        if self.force_static_linkage:
            result.append("-static")
        if self.use_cfi:
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
        if self.compiling_for_host():
            return result

        # Should work fine without linker emulation (the linker should infer it from input files)
        # if self.compiling_for_cheri():
        #     emulation = "elf64btsmip_cheri_fbsd" if not self.target_info.is_baremetal() else "elf64btsmip_cheri"
        # elif self.compiling_for_mips(include_purecap=False):
        #     emulation = "elf64btsmip_fbsd" if not self.target_info.is_baremetal() else "elf64btsmip"
        # result.append("-Wl,-m" + emulation)
        result += self.target_info.essential_compiler_and_linker_flags + [
            "-fuse-ld=" + str(self.target_info.linker),
            # Should no longer be needed now that I added a hack for .eh_frame
            # "-Wl,-z,notext",  # needed so that LLD allows text relocations
            ]
        if self.should_include_debug_info and ".bfd" not in self.target_info.linker.name:
            # Add a gdb_index to massively speed up running GDB on CHERIBSD:
            result.append("-Wl,--gdb-index")
        if self.target_info.is_cheribsd() and self.config.with_libstatcounters:
            # We need to include the constructor even if there is no reference to libstatcounters:
            # TODO: always include the .a file?
            result += ["-Wl,--whole-archive", "-lstatcounters", "-Wl,--no-whole-archive"]
        return result

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # set up the install/build/source directories (allowing overrides from config file)
        assert isinstance(self.repository, SourceRepository), self.target + " repository member is wrong!"
        if hasattr(self, "_repository_url"):
            # TODO: remove this and use a custom argparse.Action subclass
            assert isinstance(self.repository, GitRepository)
            self.repository.url = self._repository_url
        if isinstance(self.repository, ReuseOtherProjectRepository):
            # HACK: override the source directory (ignoring the setting from the JSON)
            # This should be done using a decorator that also changes default_source_dir so that we can
            # take the JSON into account
            self._initial_source_dir = self.repository.get_real_source_dir(self, self._initial_source_dir)
            self.info("Overriding source directory for", self.target, "since it reuses the sources of",
                      self.repository.source_project.target, "->", self._initial_source_dir)
        self.source_dir = self.repository.get_real_source_dir(self, self._initial_source_dir)

        if self.build_in_source_dir:
            self.verbose_print("Cannot build", self.project_name, "in a separate build dir, will build in",
                               self.source_dir)
            self.build_dir = self.source_dir

        self.configure_command = ""
        # non-assignable variables:
        self.configure_args = []  # type: typing.List[str]
        self.configure_environment = {}  # type: typing.Dict[str,str]
        self._last_stdout_line_can_be_overwritten = False
        self.make_args = MakeOptions(self.make_kind, self)
        if self.config.create_compilation_db and self.compile_db_requires_bear:
            # CompileDB seems to generate broken compile_commands,json
            if self.make_args.is_gnu_make and False:
                # use compiledb instead of bear for gnu make
                # https://blog.jetbrains.com/clion/2018/08/working-with-makefiles-in-clion-using-compilation-db/
                self.add_required_system_tool("compiledb", install_instructions="Run `pip2 install --user compiledb``")
                self._compiledb_tool = "compiledb"
            else:
                self.add_required_system_tool("bear", install_instructions="Run `cheribuild.py bear`")
                self._compiledb_tool = "bear"
        self._force_clean = False
        self._prevent_assign = True

        # Setup destdir and installprefix:
        if not self.compiling_for_host():
            install_dir_kind = self.get_default_install_dir_kind()
            # Install to SDK if CHERIBSD_ROOTFS is the install dir but we are not building for CheriBSD
            if install_dir_kind == DefaultInstallDir.SYSROOT or install_dir_kind == \
                    DefaultInstallDir.SYSROOT_AND_ROOTFS:
                if self.target_info.is_baremetal():
                    self.destdir = self.sdk_sysroot.parent
                    self._install_prefix = Path("/", self.target_info.target_triple)
                elif self.target_info.is_rtems():
                    self.destdir = self.sdk_sysroot.parent
                    self._install_prefix = Path("/", self.target_info.target_triple)
                else:
                    self._install_prefix = Path("/", self.target_info.sysroot_install_prefix_relative)
                    self.destdir = self._install_dir
                if install_dir_kind == DefaultInstallDir.SYSROOT_AND_ROOTFS:
                    self.rootfs_path = self.target_info.get_rootfs_project().install_dir
            elif install_dir_kind == DefaultInstallDir.ROOTFS:
                self.rootfs_path = self.target_info.get_rootfs_project().install_dir
                relative_to_rootfs = os.path.relpath(str(self._install_dir), str(self.rootfs_path))
                if relative_to_rootfs.startswith(os.path.pardir):
                    self.verbose_print("Custom install dir", self._install_dir, "-> using / as install prefix")
                    self._install_prefix = Path("/")
                    self.destdir = self._install_dir
                else:
                    self._install_prefix = Path("/", relative_to_rootfs)
                    self.destdir = self.rootfs_path
            elif install_dir_kind in (None, DefaultInstallDir.DO_NOT_INSTALL, DefaultInstallDir.COMPILER_RESOURCE_DIR,
                                      DefaultInstallDir.IN_BUILD_DIRECTORY, DefaultInstallDir.CUSTOM_INSTALL_DIR):
                self._install_prefix = self._install_dir
                self.destdir = None
            else:
                assert self._install_prefix and self.destdir is not None, "both must be set!"

        # convert the tuples into mutable lists (this is needed to avoid modifying class variables)
        # See https://github.com/CTSRD-CHERI/cheribuild/issues/33
        self.cross_warning_flags = ["-Werror=cheri-capability-misuse", "-Werror=implicit-function-declaration",
                                    "-Werror=format", "-Werror=undefined-internal",
                                    "-Werror=incompatible-pointer-types",
                                    "-Werror=cheri-prototypes", "-Werror=cheri-bitwise-operations"]
        # Make underaligned capability loads/stores an error and require an explicit cast:
        self.cross_warning_flags.append("-Werror=pass-failed")
        self.host_warning_flags = []
        self.common_warning_flags = []
        target_arch = self.crosscompile_target
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

        assert self.install_dir, "must be set"
        self.verbose_print(self.target, "INSTALLDIR = ", self._install_dir, "INSTALL_PREFIX=", self._install_prefix,
                           "DESTDIR=", self.destdir)

        if self.should_include_debug_info:
            if not self.target_info.is_macos():
                self.COMMON_FLAGS.append("-ggdb")
        self.CFLAGS = []
        self.CXXFLAGS = []
        self.ASMFLAGS = []
        self.LDFLAGS = self.target_info.required_link_flags()
        self.COMMON_LDFLAGS = []
        # Don't build CHERI with ASAN since that doesn't work or make much sense
        if self.use_asan and not self.compiling_for_cheri():
            self.COMMON_FLAGS.append("-fsanitize=address")
            self.COMMON_LDFLAGS.append("-fsanitize=address")

        self._lto_linker_flags = []
        self._lto_compiler_flags = []

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            # We need to set the PKG_CONFIG variables both when configuring and when running make since some projects
            # (e.g. GDB) run the configure scripts lazily during the make all stage. If we don't set PKG_CONFIG_*
            # these configure steps will find the libraries on the host instead and cause the build to fail
            pkg_config_args = dict(
                PKG_CONFIG_PATH=self.target_info.pkgconfig_dirs,
                PKG_CONFIG_LIBDIR=self.target_info.pkgconfig_dirs,
                PKG_CONFIG_SYSROOT_DIR=self.target_info.sysroot_dir
                )
            self.configure_environment.update(pkg_config_args)
            self.make_args.set_env(**pkg_config_args)
        if self.use_lto:
            self.add_lto_build_options(get_compiler_info(self.CC))

    def set_lto_binutils(self, ar, ranlib, nm, ld):
        self.fatal("Building", self.project_name, "with LTO is not supported (yet).")
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
            self._lto_linker_flags.append("-fuse-ld=" + shlex.quote(str(lld)))
            if not llvm_ar or not llvm_ranlib or not llvm_nm:
                self.warning("Could not find llvm-{ar,ranlib,nm}" + version_suffix,
                             "-> disabling LTO (resulting binary will be a bit slower)")
                return False
            ld = lld if self.lto_set_ld else None
            self.set_lto_binutils(ar=llvm_ar, ranlib=llvm_ranlib, nm=llvm_nm, ld=ld)
        if self.prefer_full_lto_over_thin_lto:
            self._lto_compiler_flags.append("-flto")
            self._lto_linker_flags.append("-flto")
        else:
            self._lto_compiler_flags.append("-flto=thin")
            self._lto_linker_flags.append("-flto=thin")
            if self.can_use_lld(ccinfo.path):
                thinlto_cache_flag = "--thinlto-cache-dir="
            else:
                # Apple ld uses a different flag for the thinlto cache dir
                assert ccinfo.compiler == "apple-clang"
                thinlto_cache_flag = "-cache_path_lto,"
            self._lto_linker_flags.append("-Wl," + thinlto_cache_flag + str(self.build_dir / "thinlto-cache"))
        self.info("Building with LTO")
        return True

    @property
    def rootfs_dir(self):
        assert self.get_default_install_dir_kind() == DefaultInstallDir.ROOTFS
        return self.rootfs_path

    @property
    def _no_overwrite_allowed(self) -> "typing.Iterable[str]":
        return super()._no_overwrite_allowed + ("configure_args", "configure_environment", "make_args")

    # Make sure that API is used properly
    def __setattr__(self, name, value):
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

    def _get_make_commandline(self, make_target: "typing.Union[str, typing.List[str]]", make_command,
                              options: MakeOptions, parallel: bool = True, compilation_db_name: str = None):
        assert options is not None
        assert make_command is not None
        options = options.copy()
        if self.config.create_compilation_db and self.compile_db_requires_bear:
            compdb_extra_args = []
            if self._compiledb_tool == "bear":
                compdb_extra_args = ["--cdb", self.build_dir / compilation_db_name, "--append", make_command]
            elif self._compiledb_tool == "compiledb":
                compdb_extra_args = ["--output", self.build_dir / compilation_db_name, make_command]
            else:
                self.fatal("Invalid tool")
            options.set_command(shutil.which(self._compiledb_tool), can_pass_j_flag=options.can_pass_jflag,
                                early_args=compdb_extra_args)
            # Ensure that recursive make invocations reuse the compilation DB tool
            options.set(MAKE=commandline_to_str([options.command] + compdb_extra_args))
            make_command = options.command

        if make_target:
            all_args = [make_command] + options.all_commandline_args
            if isinstance(make_target, str):
                all_args.append(make_target)
            else:
                all_args.extend(make_target)
        else:
            all_args = [make_command] + options.all_commandline_args
        if parallel and options.can_pass_jflag:
            all_args.append(self.config.make_j_flag)
        if not self.config.make_without_nice:
            all_args = ["nice"] + all_args
        if self.config.debug_output and options.kind == MakeCommandKind.Ninja:
            all_args.append("-v")
        if self.config.pass_dash_k_to_make:
            all_args.append("-k")
            if options.kind == MakeCommandKind.Ninja:
                # ninja needs the maximum number of failed jobs as an argument
                all_args.append("50")
        return all_args

    def get_make_commandline(self, make_target: "typing.Union[str, typing.List[str]]", make_command: str = None,
                             options: MakeOptions = None, parallel: bool = True,
                             compilation_db_name: str = None) -> list:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = self.make_args.command
        return self._get_make_commandline(make_target, make_command, options, parallel, compilation_db_name)

    def run_make(self, make_target: "typing.Union[str, typing.List[str]]" = "", *, make_command: str = None,
                 options: MakeOptions = None, logfile_name: str = None, cwd: Path = None, append_to_logfile=False,
                 compilation_db_name="compile_commands.json", parallel: bool = True,
                 stdout_filter: "typing.Optional[typing.Callable[[bytes], None]]" = _default_stdout_filter) -> None:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = self.make_args.command
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

    def update(self):
        if not self.repository and not self.config.skip_update:
            self.fatal("Cannot update", self.project_name, "as it is missing a repository source",
                       fatal_when_pretending=True)
        self.repository.update(self, src_dir=self.source_dir, default_src_dir=self._initial_source_dir,
                               revision=self.git_revision, skip_submodules=self.skip_git_submodules)
        if self.is_large_source_repository and (self.source_dir / ".git").exists():
            # This is a large repository, tell git to do whatever it can to speed up operations (new in 2.24):
            # https://git-scm.com/docs/git-config#Documentation/git-config.txt-featuremanyFiles
            self.run_cmd("git", "config", "--local", "feature.manyFiles", "true", cwd=self.source_dir,
                         print_verbose_only=True)

    _extra_git_clean_excludes = []

    def _git_clean_source_dir(self):
        # just use git clean for cleanup
        self.warning(self.project_name, "does not support out-of-source builds, using git clean to remove "
                                        "build artifacts.")
        git_clean_cmd = ["git", "clean", "-dfx", "--exclude=.*"] + self._extra_git_clean_excludes
        # Try to keep project files for IDEs and other dotfiles:
        self.run_cmd(git_clean_cmd, cwd=self.source_dir)

    def clean(self) -> ThreadJoiner:
        assert self.config.clean or self._force_clean
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

    def should_run_configure(self):
        if self.config.force_configure or self.config.configure_only:
            return True
        if self.config.clean:
            return True
        return self.needs_configure()

    def add_configure_env_arg(self, arg: str, value: "typing.Union[str,Path]"):
        if value is None:
            return
        assert not isinstance(value, list), ("Wrong type:", type(value))
        assert not isinstance(value, tuple), ("Wrong type:", type(value))
        self.configure_environment[arg] = str(value)

    def set_configure_prog_with_args(self, prog: str, path: Path, args: list):
        fullpath = str(path)
        if args:
            fullpath += " " + commandline_to_str(args)
        self.configure_environment[prog] = fullpath

    def configure(self, cwd: Path = None, configure_path: Path = None):
        if cwd is None:
            cwd = self.build_dir
        if not self.should_run_configure():
            return

        _configure_path = self.configure_command
        if configure_path:
            _configure_path = configure_path
        if not Path(_configure_path).exists():
            self.fatal("Configure command ", _configure_path, "does not exist!")
        if _configure_path:
            self.run_with_logfile([_configure_path] + self.configure_args, logfile_name="configure", cwd=cwd,
                                  env=self.configure_environment)

    def compile(self, cwd: Path = None, parallel: bool = True):
        if cwd is None:
            cwd = self.build_dir
        self.run_make("all", cwd=cwd, parallel=parallel)

    @property
    def make_install_env(self):
        if self.destdir:
            env = self.make_args.env_vars.copy()
            if "DESTDIR" not in env:
                env["DESTDIR"] = str(self.destdir)
            return env
        return self.make_args.env_vars

    @property
    def real_install_root_dir(self):
        """
        :return: the real install root directory (e.g. if prefix == /usr/local and destdir == /tmp/benchdir it will
         return /tmp/benchdir/usr/local
        """
        if self.destdir is not None:
            assert self._install_prefix
            return self.destdir / Path(self._install_prefix).relative_to(Path("/"))
        return self._install_dir

    @property
    def install_dir(self):
        return self.real_install_root_dir

    @property
    def install_prefix(self) -> Path:
        if self._install_prefix is not None:
            return self._install_prefix
        return self._install_dir

    def run_make_install(self, *, options: MakeOptions = None, _stdout_filter=_default_stdout_filter, cwd=None,
                         parallel=False, target: "typing.Union[str, typing.List[str]]" = "install",
                         make_install_env=None, **kwargs):
        if options is None:
            options = self.make_args.copy()
        else:
            options = options.copy()
        if make_install_env is None:
            make_install_env = self.make_install_env
        options.env_vars.update(make_install_env)
        self.run_make(make_target=target, options=options, stdout_filter=_stdout_filter, cwd=cwd,
                      parallel=parallel, **kwargs)

    def install(self, _stdout_filter=_default_stdout_filter):
        self.run_make_install(_stdout_filter=_stdout_filter)
        if self.get_default_install_dir_kind() == DefaultInstallDir.SYSROOT_AND_ROOTFS:
            # Also install to the rootfs:
            make_install_env = self.make_install_env.copy()
            assert "DESTDIR" in make_install_env, "DESTDIR must be set in install env for SYSROOT_AND_ROOTFS to work!"
            make_install_env["DESTDIR"] = self.rootfs_path
            self.run_make_install(_stdout_filter=_stdout_filter, make_install_env=make_install_env)

    def _do_generate_cmakelists(self):
        assert not isinstance(self, CMakeProject), self
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
""".format(command="${CLEAR_MAKEENV} " + sys.argv[0], project=self.project_name, target=self.target)
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

    def strip_elf_files(self, benchmark_dir):
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
                with file.open("rb") as f:
                    if f.read(4) == b"\x7fELF":
                        self.verbose_print("Stripping ELF binary", file)
                        run_command(self.sdk_bindir / "llvm-strip", file)
        self.run_cmd("du", "-sh", benchmark_dir)

    @property
    def default_statcounters_csv_name(self) -> str:
        assert isinstance(self, Project)
        # Only compute it once since we encode seconds in the file name:
        if hasattr(self, "_statcounters_csv"):
            return self._statcounters_csv
        else:
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
            # noinspection PyAttributeOutsideInit
            self._statcounters_csv = self.target + "-statcounters{}-{}.csv".format(
                suffix, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
            return self._statcounters_csv

    def copy_asan_dependencies(self, dest_libdir):
        # ASAN depends on libraries that are not included in the benchmark image by default:
        assert self.compiling_for_mips(include_purecap=False) and self.use_asan
        self.info("Adding ASAN library depedencies to", dest_libdir)
        self.makedirs(dest_libdir)
        for lib in ("usr/lib/librt.so.1", "usr/lib/libexecinfo.so.1", "lib/libgcc_s.so.1", "lib/libelf.so.2"):
            self.install_file(self.sdk_sysroot / lib, dest_libdir / Path(lib).name, force=True,
                              print_verbose_only=False)

    _check_install_dir_conflict = True

    def _last_build_kind_path(self):
        return Path(self.build_dir, ".cheribuild_last_build_kind")

    def _last_clean_counter_path(self):
        return Path(self.build_dir, ".cheribuild_last_clean_counter")

    def _parse_require_clean_build_counter(self) -> typing.Optional[int]:
        require_clean_path = Path(self.source_dir, ".require_clean_build")
        if not require_clean_path.exists():
            return None
        with require_clean_path.open("r") as f:
            latest_counter = None  # type: typing.Optional[int]
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

    def prepare_install_dir_for_archiving(self):
        """Perform cleanup to reduce the size of the tarball that jenkins creates"""
        self.info("No project-specific cleanup for", self.target)

    def process(self):
        if self.generate_cmakelists:
            self._do_generate_cmakelists()
        if self.config.verbose:
            print(self.project_name, "directories: source=%s, build=%s, install=%s" %
                  (self.source_dir, self.build_dir, self.install_dir))

        if self.use_asan and self.compiling_for_mips(include_purecap=False):
            # copy the ASAN lib into the right directory:
            resource_dir = get_compiler_info(self.CC).get_resource_dir()
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
            xtarget = self._xtarget  # type: CrossCompileTarget
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
                    other_xtarget = other_instance.get_crosscompile_target(self.config)
                    self.info(self.target, "install dir for", other_xtarget.name, "is", self.install_dir)
                assert other_instance.install_dir != self.install_dir, \
                    other_instance.target + " reuses the same install prefix! This will cause conflicts: " + str(
                        other_instance.install_dir)

        if self.config.skip_update:
            # When --skip-update is set (or we don't have working internet) only check that the repository exists
            if self.repository:
                self.repository.ensure_cloned(self, src_dir=self.source_dir, default_src_dir=self._initial_source_dir,
                                              skip_submodules=self.skip_git_submodules)
        else:
            self.update()
        if not self._system_deps_checked:
            self.check_system_dependencies()
        assert self._system_deps_checked, "self._system_deps_checked must be set by now!"

        last_build_file = self._last_build_kind_path()
        if self.build_in_source_dir and not self.config.clean:
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
        clean_counter_in_build_dir = None  # type: typing.Optional[int]
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
        cleaning_task = self.clean() if (self._force_clean or self.config.clean) else ThreadJoiner(None)
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
            if not self.config.skip_configure or self.config.configure_only:
                if self.should_run_configure():
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
                if is_jenkins_build():
                    self.prepare_install_dir_for_archiving()


class CMakeProject(Project):
    """
    Like Project but automatically sets up the defaults for CMake projects
    Sets configure command to CMake, adds -DCMAKE_INSTALL_PREFIX=installdir
    and checks that CMake is installed
    """
    __minimum_cmake_version = None  # type: Tuple[int, int, int]
    do_not_add_to_targets = True
    compile_db_requires_bear = False  # cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON does it
    generate_cmakelists = False  # There is already a CMakeLists.txt

    class Generator(Enum):
        Default = 0
        Ninja = 1
        Makefiles = 2

    default_build_type = BuildType.RELWITHDEBINFO

    @property
    def _build_type_basic_compiler_flags(self):
        # No need to add any flags here, cmake does it for us already
        return []

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.cmake_options = cls.add_config_option("cmake-options", default=[], kind=list, metavar="OPTIONS",
                                                  help="Additional command line options to pass to CMake")

    def __init__(self, config, generator=Generator.Ninja):
        super().__init__(config)
        self.configure_command = os.getenv("CMAKE_COMMAND", "cmake")
        self.add_required_system_tool("cmake", homebrew="cmake", zypper="cmake", apt="cmake", freebsd="cmake")
        # allow a -G flag in cmake-options to override the default generator (e.g. use makefiles for CLion)
        custom_generator = next((x for x in self.cmake_options if x.startswith("-G")), None)
        if custom_generator:
            if "Unix Makefiles" in custom_generator:
                generator = CMakeProject.Generator.Makefiles
            elif "Ninja" in custom_generator:
                generator = CMakeProject.Generator.Ninja
            else:
                # TODO: add support for cmake --build <dir> --target <tgt> -- <args>
                self.fatal("Unknown CMake Generator", custom_generator, "-> don't know which build command to run")
        self.generator = generator
        self.configure_args.append(str(self.source_dir))  # TODO: use undocumented -H and -B options?
        if self.generator == CMakeProject.Generator.Ninja:
            if not custom_generator:
                self.configure_args.append("-GNinja")
            self.make_args.kind = MakeCommandKind.Ninja
        if self.generator == CMakeProject.Generator.Makefiles:
            if not custom_generator:
                self.configure_args.append("-GUnix Makefiles")
            self.make_args.kind = MakeCommandKind.DefaultMake

        if self.build_type != BuildType.DEFAULT:
            # no CMake equivalent for MinSizeRelWithDebInfo -> set minsizerel and force debug info
            if self.build_type == BuildType.MINSIZERELWITHDEBINFO:
                self.build_type = BuildType.MINSIZEREL
                self._force_debug_info = True

        self.configure_args.append("-DCMAKE_BUILD_TYPE=" + str(self.build_type.value))
        # TODO: always generate it?
        if self.config.create_compilation_db:
            self.configure_args.append("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
            # Don't add the user provided options here, add them in configure() so that they are put last
        # This must come first:
        if not self.compiling_for_host():
            # Despite the name it should also work for baremetal newlib
            assert self.target_info.is_cheribsd() or self.target_info.is_baremetal() or self.target_info.is_rtems()
            self._cmake_template = include_local_file("files/CrossToolchain.cmake.in")
            self.toolchain_file = self.build_dir / "CrossToolchain.cmake"
            self.add_cmake_options(CMAKE_TOOLCHAIN_FILE=self.toolchain_file)
        # The toolchain files need at least CMake 3.7
        self.set_minimum_cmake_version(3, 7)

    def _prepare_toolchain_file(self, file: Path, **kwargs):
        configured_template = self._cmake_template
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
        self.write_file(contents=configured_template, file=file, overwrite=True)

    def add_cmake_options(self, *, _include_empty_vars=False, _replace=True, **kwargs):
        for option, value in kwargs.items():
            if not _replace and any(x.startswith("-D" + option + "=") for x in self.configure_args):
                self.verbose_print("Not replacing ", option, "since it is already set.")
                return
            if any(x.startswith("-D" + option) for x in self.cmake_options):
                self.info("Not using default value of '", value, "' for CMake option '", option,
                          "' since it is explicitly overwritten in the configuration", sep="")
                continue
            if isinstance(value, bool):
                value = "ON" if value else "OFF"
            if not str(value) and not _include_empty_vars:
                continue
            assert value is not None
            self.configure_args.append("-D" + option + "=" + str(value))

    def set_minimum_cmake_version(self, major: int, minor: int, patch: int = 0):
        self.__minimum_cmake_version = (major, minor, patch)

    def _cmake_install_stdout_filter(self, line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        self._show_line_stdout_filter(line)

    def set_lto_binutils(self, ar, ranlib, nm, ld):
        # LD is never invoked directly, so the -fuse-ld= flag is sufficient
        self.add_cmake_options(CMAKE_AR=ar, CMAKE_RANLIB=ranlib)

    def needs_configure(self) -> bool:
        if self.config.pretend and (self.config.force_configure or self.config.clean):
            return True
        # CMake is smart enough to detect when it must be reconfigured -> skip configure if cache exists
        cmake_cache = self.build_dir / "CMakeCache.txt"
        build_file = "build.ninja" if self.generator == CMakeProject.Generator.Ninja else "Makefile"
        return not cmake_cache.exists() or not (self.build_dir / build_file).exists()

    def generate_cmake_toolchain_file(self, file: Path):
        # CMAKE_CROSSCOMPILING will be set when we change CMAKE_SYSTEM_NAME:
        # This means we may not need the toolchain file at all
        # https://cmake.org/cmake/help/latest/variable/CMAKE_CROSSCOMPILING.html
        # TODO: avoid the toolchain file and set all flags on the command line
        if self.crosscompile_target.is_cheri_purecap() and self.target_info.is_cheribsd():
            if self._get_cmake_version() < (3, 9, 0):
                self.fatal("CMake 3.9 or newer is required to cross-compile for CheriBSD")
            add_lib_suffix = """
# cheri libraries are found in /usr/libcheri:
set(CMAKE_FIND_LIBRARY_CUSTOM_LIB_SUFFIX "cheri")
# set(LIB_SUFFIX "cheri" CACHE INTERNAL "")
"""
        else:
            add_lib_suffix = "# no lib suffix needed for non-purecap"
        self._prepare_toolchain_file(
            file=file,
            TOOLCHAIN_SDK_BINDIR=self.sdk_bindir if not self.compiling_for_host() else
            self.config.cheri_sdk_bindir,
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
            TOOLCHAIN_SYSTEM_NAME=self.target_info.cmake_system_name,
            TOOLCHAIN_PKGCONFIG_DIRS=self.target_info.pkgconfig_dirs,
            TOOLCHAIN_PREFIX_PATHS=";".join(map(str, self.target_info.cmake_prefix_paths)),
            TOOLCHAIN_FORCE_STATIC=self.force_static_linkage,
            )

    def configure(self, **kwargs):
        if self.install_prefix != self.install_dir:
            assert self.destdir, "custom install prefix requires DESTDIR being set!"
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.install_prefix)
        else:
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.install_dir)
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
        if not self.compiling_for_host():
            # TODO: set CMAKE_STRIP, CMAKE_NM, CMAKE_OBJDUMP, CMAKE_READELF, CMAKE_DLLTOOL, CMAKE_DLLTOOL,
            #  CMAKE_ADDR2LINE
            self.generate_cmake_toolchain_file(self.toolchain_file)
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
        if not self.compiling_for_host() and self.generator == CMakeProject.Generator.Ninja:
            # Ninja can't change the RPATH when installing: https://gitlab.kitware.com/cmake/cmake/issues/13934
            # TODO: remove once it has been fixed
            self.add_cmake_options(CMAKE_BUILD_WITH_INSTALL_RPATH=True)
        # TODO: BUILD_SHARED_LIBS=OFF?

        # Add the options from the config file:
        self.configure_args.extend(self.cmake_options)
        # make sure we get a completely fresh cache when --reconfigure is passed:
        cmake_cache = self.build_dir / "CMakeCache.txt"
        if self.config.force_configure:
            self.delete_file(cmake_cache)
        super().configure(**kwargs)
        if self.config.copy_compilation_db_to_source_dir and (self.build_dir / "compile_commands.json").exists():
            self.install_file(self.build_dir / "compile_commands.json", self.source_dir / "compile_commands.json",
                              force=True)

    def install(self, _stdout_filter="__DEFAULT__"):
        if _stdout_filter == "__DEFAULT__":
            _stdout_filter = self._cmake_install_stdout_filter
        super().install(_stdout_filter=_stdout_filter)

    def _get_cmake_version(self):
        cmd = Path(self.configure_command)
        assert self.configure_command is not None
        if not cmd.is_absolute() or not Path(self.configure_command).exists():
            self.fatal("Could not find cmake binary:", self.configure_command)
            return 0, 0, 0
        assert cmd.is_absolute()
        return get_program_version(cmd, program_name=b"cmake")

    def check_system_dependencies(self):
        if not Path(self.configure_command).is_absolute():
            abspath = shutil.which(self.configure_command)
            if abspath:
                self.configure_command = abspath
        super().check_system_dependencies()
        if self.__minimum_cmake_version:
            version_components = self._get_cmake_version()
            # noinspection PyTypeChecker
            if version_components < self.__minimum_cmake_version:
                version_str = ".".join(map(str, version_components))
                expected_str = ".".join(map(str, self.__minimum_cmake_version))
                instrs = "Use your package manager to install CMake > " + expected_str + \
                         " or run `cheribuild.py cmake` to install the latest version locally"
                self.dependency_error("CMake version", version_str, "is too old (need at least", expected_str + ")",
                                      install_instructions=instrs)

    @staticmethod
    def find_package(name: str) -> bool:
        try:
            cmd = "cmake --find-package -DCOMPILER_ID=Clang -DLANGUAGE=CXX -DMODE=EXIST -DQUIET=TRUE".split()
            cmd.append("-DNAME=" + name)
            return run_command(cmd).returncode == 0
        except subprocess.CalledProcessError:
            return False


class AutotoolsProject(Project):
    do_not_add_to_targets = True
    _configure_supports_prefix = True
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.extra_configure_flags = cls.add_config_option("configure-options", default=[], kind=list, metavar="OPTIONS",
                                                          help="Additional command line options to pass to configure")

    """
    Like Project but automatically sets up the defaults for autotools like projects
    Sets configure command to ./configure, adds --prefix=installdir
    """

    def __init__(self, config, configure_script="configure"):
        super().__init__(config)
        self.configure_command = self.source_dir / configure_script

    def configure(self, **kwargs):
        if self._configure_supports_prefix:
            if self.install_prefix != self.install_dir:
                assert self.destdir, "custom install prefix requires DESTDIR being set!"
                self.configure_args.append("--prefix=" + str(self.install_prefix))
            else:
                self.configure_args.append("--prefix=" + str(self.install_dir))
        if self.extra_configure_flags:
            self.configure_args.extend(self.extra_configure_flags)
        super().configure(**kwargs)

    def needs_configure(self):
        return not (self.build_dir / "Makefile").exists()

    def set_lto_binutils(self, ar, ranlib, nm, ld):
        kwargs = {"NM": nm, "AR": ar, "RANLIB": ranlib}
        if ld:
            kwargs["LD"] = ld
        self.configure_environment.update(**kwargs)
        # self.make_args.env_vars.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
        self.make_args.set(**kwargs)
        self.make_args.env_vars.update(**kwargs)


# A target that is just an alias for at least one other targets but does not force building of dependencies
class TargetAlias(SimpleProject):
    do_not_add_to_targets = True
    dependencies_must_be_built = False
    hasSourceFiles = False
    is_alias = True

    def process(self):
        assert len(self.dependencies) > 0


# A target that does nothing (used for e.g. the "all" target)
class TargetAliasWithDependencies(TargetAlias):
    do_not_add_to_targets = True
    dependencies_must_be_built = True
    hasSourceFiles = False


class BuildAll(TargetAliasWithDependencies):
    dependencies = ["qemu", "sdk", "disk-image-mips-hybrid", "run-mips-hybrid"]
