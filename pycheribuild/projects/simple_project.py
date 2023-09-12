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
import errno
import functools
import inspect
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import typing
from abc import ABCMeta, abstractmethod
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Optional, Union

from ..config.chericonfig import CheriConfig, ComputedDefaultValue
from ..config.config_loader_base import ConfigLoaderBase, ConfigOptionBase, DefaultValueOnlyConfigOption
from ..config.target_info import (
    AbstractProject,
    AutoVarInit,
    BasicCompilationTargets,
    CPUArchitecture,
    CrossCompileTarget,
    TargetInfo,
)
from ..processutils import (
    check_call_handle_noexec,
    commandline_to_str,
    keep_terminal_sane,
    popen_handle_noexec,
    print_command,
    run_command,
    set_env,
)
from ..targets import MultiArchTarget, MultiArchTargetAlias, Target, target_manager
from ..utils import (
    InstallInstructions,
    OSInfo,
    classproperty,
    fatal_error,
    is_jenkins_build,
    query_yes_no,
    replace_one,
    status_update,
)

__all__ = [  # no-combine
    "_cached_get_homebrew_prefix", "_clear_line_sequence", "_default_stdout_filter",  # no-combine
    "flush_stdio", "SimpleProject", "TargetAlias", "TargetAliasWithDependencies", "BoolConfigOption",  # no-combine
]  # no-combine

T = typing.TypeVar("T")


def flush_stdio(stream) -> None:
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


def _default_stdout_filter(_: bytes) -> "typing.NoReturn":
    raise NotImplementedError("Should never be called, this is a dummy")


# noinspection PyProtectedMember
class ProjectSubclassDefinitionHook(ABCMeta):
    def __new__(cls, name, bases, dct):
        # We have to set _local_config_options to a new dict here, as this is the first hook that runs before
        # the __set_name__ function on class members is called (__init_subclass__ is too late).
        for base in bases:
            old = getattr(base, "_local_config_options", None)
            if old is not None:
                # Create a copy of the dictionary so that modifying it does not change the value in the base class.
                dct = dict(dct)
                dct["_local_config_options"] = dict(old)
        return super().__new__(cls, name, bases, dct)

    def __init__(cls, name: str, bases, clsdict) -> None:
        super().__init__(name, bases, clsdict)
        if typing.TYPE_CHECKING:
            assert issubclass(cls, SimpleProject)
        if clsdict.get("do_not_add_to_targets") is not None:
            if clsdict.get("do_not_add_to_targets") is True:
                return  # if do_not_add_to_targets is defined within the class we skip it
        elif name.endswith("Base"):
            fatal_error("Found class name ending in Base (", name, ") but do_not_add_to_targets was not defined",
                        sep="", pretend=False)

        def die(msg):
            sys.exit(inspect.getfile(cls) + ":" + str(inspect.findsource(cls)[1] + 1) + ": error: " + msg)
        # load "target" field first then use that to infer the default source/build/install dir names
        target_name = None
        if "target" in clsdict:
            target_name = clsdict["target"]
        elif name.startswith("Build"):
            target_name = name[len("Build"):].replace("_", "-").lower()
            cls.target = target_name
        if not target_name:
            die("target name is not set and cannot infer from class " + name +
                " -- set target= or do_not_add_to_targets=True")
        # Make the local config options dictionary read-only
        cls._local_config_options = MappingProxyType(cls._local_config_options)

        # The default source/build/install directory name defaults to the target unless explicitly overwritten.
        if "default_directory_basename" not in clsdict and not cls.inherit_default_directory_basename:
            cls.default_directory_basename = target_name

        if "project_name" in clsdict:
            die("project_name should no longer be used, change the definition of class " + name +
                " to include target and/or default_directory_basename")

        if cls.__dict__.get("dependencies_must_be_built") and not cls.dependencies:
            sys.exit("PseudoTarget with no dependencies should not exist!! Target name = " + target_name)
        supported_archs = cls.supported_architectures
        assert supported_archs, "Must not be empty: " + str(supported_archs)
        assert isinstance(supported_archs, tuple)
        assert len(set(supported_archs)) == len(
            supported_archs), "Duplicates in supported archs for " + cls.__name__ + ": " + str(supported_archs)
        # TODO: if len(cls.supported_architectures) > 1:
        if cls._always_add_suffixed_targets or len(supported_archs) > 1:
            # Add a the target for the default architecture
            base_target = MultiArchTargetAlias(target_name, cls)
            # Add aliases for targets that support multiple architectures and have a clear default value.
            # E.g. llvm -> llvm-native, but not cheribsd since it's not clear which variant should be built there.
            if cls.default_architecture is not None:
                target_manager.add_target(base_target)
            else:
                target_manager.add_target_for_config_options_only(base_target)

            assert cls._xtarget is None, "Should not be set!"
            # assert cls._should_not_be_instantiated, "multiarch base classes should not be instantiated"
            for arch in supported_archs:
                assert isinstance(arch, CrossCompileTarget)
                # create a new class to ensure different build dirs and config name strings
                if cls.custom_target_name is not None:
                    custom_target_cb = cls.custom_target_name
                    new_name = custom_target_cb(target_name, arch)
                else:
                    if cls.include_os_in_target_suffix:
                        new_name = target_name + "-" + arch.generic_target_suffix
                    else:
                        # Don't add the OS name to the target suffixed when building the OS: we want the target
                        # to be called freebsd-amd64 and not freebsd-freebsd-amd64.
                        new_name = target_name + "-" + arch.base_target_suffix
                new_dict = cls.__dict__.copy()
                new_dict["_xtarget"] = arch
                new_dict["_should_not_be_instantiated"] = False  # unlike the subclass we can instantiate these
                new_dict["do_not_add_to_targets"] = True  # We are already adding it here
                new_dict["target"] = new_name
                new_dict["synthetic_base"] = cls  # We are already adding it here
                # noinspection PyTypeChecker
                new_cls = type(cls.__name__ + "_" + arch.name, (cls, *cls.__bases__), new_dict)
                assert issubclass(new_cls, SimpleProject)
                target_manager.add_target(MultiArchTarget(new_name, new_cls, arch, base_target))
                # Handle old names for FreeBSD/CheriBSD targets in the config file:
                if arch.target_info_cls.is_freebsd() and not arch.target_info_cls.is_native():
                    if arch.target_info_cls.is_cheribsd():
                        if arch.is_hybrid_or_purecap_cheri([CPUArchitecture.MIPS64]):
                            new_cls._config_file_aliases += (replace_one(new_name, "-mips64-", "-mips-"),)
                        elif arch.is_mips(include_purecap=False):
                            new_cls._config_file_aliases += (replace_one(new_name, "-mips64", "-mips-nocheri"),)
                    else:
                        # FreeBSD target suffixes have also changed over time
                        if arch.is_mips(include_purecap=False):
                            new_cls._config_file_aliases += (replace_one(new_name, "-mips64", "-mips"),)
                        elif arch.is_x86_64(include_purecap=False):
                            new_cls._config_file_aliases += (replace_one(new_name, "-amd64", "-x86"),
                                                             replace_one(new_name, "-amd64", "-x86_64"))
                if len(set(new_cls._config_file_aliases)) != len(new_cls._config_file_aliases):
                    raise ValueError(f"Duplicate aliases for {new_name}: {new_cls._config_file_aliases}")
        else:
            assert len(supported_archs) == 1
            # Only one target is supported:
            cls._xtarget = supported_archs[0]
            cls._should_not_be_instantiated = False  # can be instantiated
            target_manager.add_target(Target(target_name, cls))
        # print("Adding target", target_name, "with deps:", cls.dependencies)


class PerProjectConfigOption:
    def __init__(self, name: str, help: str, default: "typing.Any", **kwargs):
        self._name = name
        self._default = default
        self._help = help
        self._kwargs = kwargs

    def register_config_option(self, owner: "type[SimpleProject]") -> ConfigOptionBase:
        raise NotImplementedError()

    # noinspection PyProtectedMember
    def __set_name__(self, owner: "type[SimpleProject]", name: str):
        owner._local_config_options[name] = self

    def __get__(self, instance: "SimpleProject", owner: "type[SimpleProject]"):
        return ValueError("Should have been replaced!")


if typing.TYPE_CHECKING:
    # noinspection PyPep8Naming
    def BoolConfigOption(name: str, help: str,  # noqa: N802
                         default: "typing.Union[bool, ComputedDefaultValue[bool]]" = False, **kwargs) -> bool:
        return typing.cast(bool, default)

    # noinspection PyPep8Naming
    def IntConfigOption(name: str, help: str,  # noqa: N802
                        default: "typing.Union[int, ComputedDefaultValue[int]]", **kwargs) -> int:
        return typing.cast(int, default)

    # noinspection PyPep8Naming
    def OptionalIntConfigOption(name: str, help: str,  # noqa: N802
                                default: "typing.Union[Optional[int], ComputedDefaultValue[Optional[int]]]" = None,
                                **kwargs) -> "Optional[int]":
        return typing.cast(Optional[int], default)
else:
    class BoolConfigOption(PerProjectConfigOption):
        def __init__(self, name: str, help: str, default: "typing.Union[bool, ComputedDefaultValue[bool]]" = False,
                     **kwargs):
            super().__init__(name, help, default, **kwargs)

        def register_config_option(self, owner: "type[SimpleProject]") -> ConfigOptionBase:
            return typing.cast(ConfigOptionBase,
                               owner.add_bool_option(self._name, default=self._default, help=self._help,
                                                     **self._kwargs))

    class IntConfigOption(PerProjectConfigOption):
        def __init__(self, name: str, help: str, default: "typing.Union[int, ComputedDefaultValue[int]]", **kwargs):
            super().__init__(name, help, default, **kwargs)

        def register_config_option(self, owner: "type[SimpleProject]") -> ConfigOptionBase:
            return typing.cast(ConfigOptionBase,
                               owner.add_config_option(self._name, default=self._default, help=self._help, kind=int,
                                                       **self._kwargs))

    class OptionalIntConfigOption(PerProjectConfigOption):
        def __init__(self, name: str, help: str,
                     default: "typing.Union[Optional[int], ComputedDefaultValue[Optional[int]]]" = None, **kwargs):
            super().__init__(name, help, default, **kwargs)

        def register_config_option(self, owner: "type[SimpleProject]") -> ConfigOptionBase:
            return typing.cast(ConfigOptionBase,
                               owner.add_config_option(self._name, default=self._default, help=self._help, kind=int,
                                                       **self._kwargs))


@functools.lru_cache(maxsize=20)
def _cached_get_homebrew_prefix(package: "Optional[str]", config: CheriConfig):
    assert OSInfo.IS_MAC, "Should only be called on macos"
    command = ["brew", "--prefix"]
    if package:
        command.append(package)
    prefix = None
    try:
        prefix_str = run_command(command, capture_output=True, run_in_pretend_mode=True,
                                 print_verbose_only=False, config=config).stdout.decode("utf-8").strip()
        prefix = Path(prefix_str)
        if not prefix.exists():
            prefix = None
    except subprocess.CalledProcessError:
        pass
    return prefix


# ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
# However, if the output is just a plain text file don't attempt to do any line clearing
_clear_line_sequence: bytes = b"\x1b[2K\r" if sys.stdout.isatty() else b"\n"


class SimpleProject(AbstractProject, metaclass=ABCMeta if typing.TYPE_CHECKING else ProjectSubclassDefinitionHook):
    _commandline_option_group: typing.Any = None
    _config_loader: ConfigLoaderBase = None

    # The source dir/build dir names will be inferred from the target name unless default_directory_basename is set.
    # Note that this is not inherited by default unless you set inherit_default_directory_basename (which itself is
    # inherited as normal, so can be set in a base class).
    default_directory_basename: Optional[str] = None
    inherit_default_directory_basename: bool = False
    _local_config_options: "typing.ClassVar[dict[str, PerProjectConfigOption]]" = dict()
    # Old names in the config file (per-architecture) for backwards compat
    _config_file_aliases: "tuple[str, ...]" = tuple()
    dependencies: "tuple[str, ...]" = tuple()
    dependencies_must_be_built: bool = False
    direct_dependencies_only: bool = False
    # skip_toolchain_dependencies can be set to true for target aliases to skip the toolchain dependecies by default.
    # For example, when running "cheribuild.py morello-firmware --clean" we don't want to also do a clean build of LLVM.
    skip_toolchain_dependencies: bool = False
    _cached_full_deps: "Optional[list[Target]]" = None
    _cached_filtered_deps: "Optional[list[Target]]" = None
    is_alias: bool = False
    is_sdk_target: bool = False  # for --skip-sdk
    # Set to true to omit the extra -<os> suffix in target names (otherwise we would end up with targets such as
    # freebsd-freebsd-amd64, etc.)
    include_os_in_target_suffix: bool = True
    source_dir: Optional[Path] = None
    build_dir: Optional[Path] = None
    install_dir: Optional[Path] = None
    build_dir_suffix: str = ""  # add a suffix to the build dir (e.g. for freebsd-with-bootstrap-clang)
    use_asan: bool = False
    add_build_dir_suffix_for_native: bool = False  # Whether to add -native to the native build dir
    build_in_source_dir: bool = False  # For projects that can't build in the source dir
    build_via_symlink_farm: bool = False  # Create source symlink farm to work around lack of out-of-tree build support
    # For target_info.py. Real value is only set for Project subclasses, since SimpleProject subclasses should not
    # include C/C++ compilation (there is no source+build dir)
    auto_var_init: AutoVarInit = AutoVarInit.NONE
    # Whether to hide the options from the default --help output (only add to --help-hidden)
    hide_options_from_help: bool = False
    # Project subclasses will automatically have a target based on their name generated unless they add this:
    do_not_add_to_targets: bool = True
    # Default to NATIVE only
    supported_architectures: "typing.ClassVar[tuple[CrossCompileTarget, ...]]" = (BasicCompilationTargets.NATIVE,)
    # The architecture to build for the unsuffixed target name (defaults to supported_architectures[0] if no match)
    _default_architecture: "Optional[CrossCompileTarget]" = None

    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    # To check that we don't create an crosscompile targets without a fixed target
    _should_not_be_instantiated: bool = True
    # To prevent non-suffixed targets in case the only target is not NATIVE
    _always_add_suffixed_targets: bool = False  # add a suffixed target only if more than one variant is supported

    # List of system tools/headers/pkg-config files that have been checked so far (to avoid duplicate work)
    __checked_system_tools: "dict[str, InstallInstructions]" = {}
    __checked_system_headers: "dict[str, InstallInstructions]" = {}
    __checked_pkg_config: "dict[str, InstallInstructions]" = {}

    custom_target_name: "Optional[typing.Callable[[str, CrossCompileTarget], str]]" = None

    @classmethod
    def is_toolchain_target(cls) -> bool:
        return False

    @property
    def _no_overwrite_allowed(self) -> "tuple[str]":
        return ("_xtarget", )

    @classmethod
    def all_dependency_names(cls, config: CheriConfig) -> "list[str]":
        assert cls._xtarget is not None
        if cls.__dict__.get("_cached_full_deps", None) is None:
            cls._cache_full_dependencies(config)
        return [t.name for t in cls.cached_full_dependencies()]

    # noinspection PyCallingNonCallable
    @classmethod
    def _direct_dependencies(cls, config: CheriConfig, *, include_toolchain_dependencies: bool,
                             include_sdk_dependencies: bool,
                             explicit_dependencies_only: bool) -> "typing.Iterator[Target]":
        if not include_sdk_dependencies:
            include_toolchain_dependencies = False  # --skip-sdk means skip toolchain and skip sysroot
        assert cls._xtarget is not None
        dependencies = cls.dependencies
        expected_build_arch = cls.get_crosscompile_target()
        assert expected_build_arch is not None
        assert cls._xtarget is not None
        if expected_build_arch is None or cls._xtarget is None:
            raise ValueError("Cannot call _direct_dependencies() on a target alias")
        if callable(dependencies):
            if inspect.ismethod(dependencies):
                dependencies = dependencies(config)
            else:
                # noinspection PyCallingNonCallable  (false positive, we used if callable() above)
                dependencies = dependencies(cls, config)
        assert isinstance(dependencies, tuple), "Expected a list and not " + str(type(dependencies))
        dependencies = list(dependencies)  # mutable copy to append transitive dependencies
        # Also add the toolchain targets (e.g. llvm-native) and sysroot targets if needed:
        if not explicit_dependencies_only:
            if include_toolchain_dependencies:
                dependencies.extend(cls._xtarget.target_info_cls.toolchain_targets(cls._xtarget, config))
            if include_sdk_dependencies and cls.needs_sysroot:
                # For A-for-B-rootfs targets the sysroot should use B targets,
                # not A-for-B-rootfs.
                for dep_name in cls._xtarget.target_info_cls.base_sysroot_targets(cls._xtarget, config):
                    try:
                        dep_target = target_manager.get_target(
                            dep_name,
                            arch_for_unqualified_targets=expected_build_arch.get_rootfs_target(),
                            config=config,
                            caller=cls.target,
                        )
                        dependencies.append(dep_target.name)
                    except KeyError:
                        fatal_error("Could not find sysroot target '", dep_name, "' for ", cls.__name__, sep="",
                                    pretend=config.pretend, fatal_when_pretending=True)
                        raise
        # Try to resovle the target names to actual targets and potentially add recursive depdencies
        for dep_name in dependencies:
            try:
                dep_target = target_manager.get_target(
                    dep_name, arch_for_unqualified_targets=expected_build_arch, config=config, caller=cls.target,
                )
            except KeyError:
                fatal_error("Could not find target '", dep_name, "' for ", cls.__name__, sep="",
                            pretend=config.pretend, fatal_when_pretending=True)
                raise
            # Handle --include-dependencies when --skip-sdk/--no-include-toolchain-dependencies is passed
            if explicit_dependencies_only:
                pass  # add all explicit direct dependencies
            elif not include_sdk_dependencies and dep_target.project_class.is_sdk_target:
                if config.verbose:
                    status_update("Not adding ", cls.target, "dependency", dep_target.name,
                                  "since it is an SDK target.")
                continue
            elif not include_toolchain_dependencies and dep_target.project_class.is_toolchain_target():
                if config.verbose:
                    status_update("Not adding ", cls.target, "dependency", dep_target.name,
                                  "since it is a toolchain target.")
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

    def is_exact_instance(self, class_type: "type[typing.Any]") -> bool:
        if self.__class__ == class_type or getattr(self, "synthetic_base", object) == class_type:
            self.verbose_print(self, "is exact instance of", class_type)
            return True
        else:
            self.verbose_print(self, "is not exact instance of", class_type)
            return False

    @classmethod
    def recursive_dependencies(cls, config: CheriConfig) -> "list[Target]":
        """
        Returns the list of recursive depdencies. If filtered is False this returns all dependencies, if True the result
        is filtered based on various parameters such as config.include_dependencies.
        """
        # look only in __dict__ to avoid parent class lookup
        result: "Optional[list[Target]]" = cls.__dict__.get("_cached_filtered_deps", None)
        if result is None:
            with_toolchain_deps = config.include_toolchain_dependencies and not cls.skip_toolchain_dependencies
            with_sdk_deps = not config.skip_sdk
            result = cls._recursive_dependencies_impl(
                config, include_dependencies=config.include_dependencies or cls.dependencies_must_be_built,
                include_toolchain_dependencies=with_toolchain_deps, include_sdk_dependencies=with_sdk_deps)
            cls._cached_filtered_deps = result
        return result

    @classmethod
    def _recursive_dependencies_impl(cls, config: CheriConfig, *, include_dependencies: bool,
                                     include_toolchain_dependencies: bool,
                                     dependency_chain: "Optional[list[type[SimpleProject]]]" = None,
                                     include_sdk_dependencies: bool) -> "list[Target]":
        assert cls._xtarget is not None, cls
        if not include_dependencies:
            return []
        if dependency_chain:
            new_dependency_chain = [*dependency_chain, cls]
            if cls in dependency_chain:
                cycle = new_dependency_chain[new_dependency_chain.index(cls):]
                fatal_error("Cyclic dependency found:", " -> ".join(map(lambda c: c.target, cycle)), pretend=False)
        else:
            new_dependency_chain = [cls]
        # look only in __dict__ to avoid parent class lookup
        cache_lookup_args = (include_dependencies, include_toolchain_dependencies, include_sdk_dependencies)
        # noinspection PyProtectedMember
        cached_result = config._cached_deps.get(cls.target, dict()).get(cache_lookup_args, None)
        if cached_result is not None:
            return cached_result
        result = []
        for target in cls._direct_dependencies(config, include_toolchain_dependencies=include_toolchain_dependencies,
                                               include_sdk_dependencies=include_sdk_dependencies,
                                               explicit_dependencies_only=cls.direct_dependencies_only):
            if config.should_skip_dependency(target.name, cls.target):
                continue

            if target not in result:
                result.append(target)
            if cls.direct_dependencies_only:
                continue  # don't add recursive dependencies for e.g. "build-and-run"
            # now recursively add the other deps:
            recursive_deps = target.project_class._recursive_dependencies_impl(
                config, include_dependencies=include_dependencies,
                include_toolchain_dependencies=include_toolchain_dependencies,
                include_sdk_dependencies=include_sdk_dependencies,
                dependency_chain=new_dependency_chain)
            for r in recursive_deps:
                if r not in result:
                    result.append(r)
        # save the result to avoid recomputing it lots of times
        # noinspection PyProtectedMember
        config._cached_deps[cls.target][cache_lookup_args] = result
        return result

    @classmethod
    def cached_full_dependencies(cls) -> "list[Target]":
        # look only in __dict__ to avoid parent class lookup
        _cached: "Optional[list[Target]]" = cls.__dict__.get("_cached_full_deps", None)
        if _cached is None:
            raise ValueError("cached_full_dependencies called before value was cached")
        return _cached

    @classmethod
    def _cache_full_dependencies(cls, config, *, allow_already_cached=False) -> None:
        assert allow_already_cached or cls.__dict__.get("_cached_full_deps", None) is None, "Already cached??"
        cls._cached_full_deps = cls._recursive_dependencies_impl(config, include_dependencies=True,
                                                                 include_toolchain_dependencies=True,
                                                                 include_sdk_dependencies=True)

    @classmethod
    def get_instance(cls: "type[T]", caller: "Optional[AbstractProject]", config: "Optional[CheriConfig]" = None,
                     cross_target: Optional[CrossCompileTarget] = None) -> T:
        # TODO: assert that target manager has been initialized
        if caller is not None:
            if config is None:
                config = caller.config
            if cross_target is None:
                cross_target = caller.get_crosscompile_target()
        else:
            if cross_target is None:
                cross_target = cls.get_crosscompile_target()
            assert config is not None, "Need either caller or config argument!"
        return cls.get_instance_for_cross_target(cross_target, config, caller=caller)

    @classmethod
    def _get_instance_no_setup(cls: "type[T]", caller: AbstractProject,
                               cross_target: Optional[CrossCompileTarget] = None) -> T:
        if cross_target is None:
            cross_target = caller.crosscompile_target
        target_name = cls.target
        if cross_target is not None and isinstance(caller, cls):
            # When called as self.get_* we have to ensure that we use the "generic" target since cls.target includes
            # the -<arch> suffix and querying the target manager for foo-<arch> with a mismatched target is an error
            target_name = getattr(cls, "synthetic_base", cls).target
        target = target_manager.get_target(target_name, required_arch=cross_target, config=caller.config, caller=caller)
        # noinspection PyProtectedMember
        result = target._get_or_create_project_no_setup(cross_target, caller.config, caller=caller)
        assert isinstance(result, SimpleProject)
        return result

    @classmethod
    def get_instance_for_cross_target(cls: "type[T]", cross_target: CrossCompileTarget, config: CheriConfig,
                                      caller: "Optional[AbstractProject]" = None) -> T:
        # Also need to handle calling self.get_instance_for_cross_target() on a target-specific instance
        # In that case cls.target returns e.g. foo-mips, etc. and target_manager will always return the MIPS version
        if caller is not None:
            assert caller._init_called, "Cannot call this inside __init__()"
        root_class = getattr(cls, "synthetic_base", cls)
        target = target_manager.get_target(root_class.target, required_arch=cross_target, config=config, caller=caller)
        result = target.get_or_create_project(cross_target, config, caller=caller)
        assert isinstance(result, SimpleProject)
        found_target = result.get_crosscompile_target()
        # XXX: FIXME: add cross target to every call
        assert cross_target is not None
        assert found_target is cross_target, "Didn't find right instance of " + str(cls) + ": " + str(
            found_target) + " vs. " + str(cross_target) + ", caller was " + repr(caller)
        return result

    @classproperty
    def default_architecture(self) -> "Optional[CrossCompileTarget]":
        return self._default_architecture

    def get_host_triple(self) -> str:
        compiler = self.get_compiler_info(self.host_CC)
        return compiler.default_target

    # noinspection PyPep8Naming
    @property
    def CC(self) -> Path:  # noqa: N802
        return self.target_info.c_compiler

    # noinspection PyPep8Naming
    @property
    def CXX(self) -> Path:  # noqa: N802
        return self.target_info.cxx_compiler

    # noinspection PyPep8Naming
    @property
    def CPP(self) -> Path:  # noqa: N802
        return self.target_info.c_preprocessor

    # noinspection PyPep8Naming
    @property
    def host_CC(self) -> Path:  # noqa: N802
        return TargetInfo.host_c_compiler(self.config)

    # noinspection PyPep8Naming
    @property
    def host_CXX(self) -> Path:  # noqa: N802
        return TargetInfo.host_cxx_compiler(self.config)

    # noinspection PyPep8Naming
    @property
    def host_CPP(self) -> Path:  # noqa: N802
        return TargetInfo.host_c_preprocessor(self.config)

    @property
    def essential_compiler_and_linker_flags(self):
        # This property exists so that gdb can override the target flags to build the -purecap targets as hybrid.
        return self.target_info.get_essential_compiler_and_linker_flags()

    @classproperty
    def needs_sysroot(self) -> bool:
        return not self._xtarget.is_native()  # Most projects need a sysroot (but not native)

    def compiling_for_mips(self, include_purecap: bool) -> bool:
        return self.crosscompile_target.is_mips(include_purecap=include_purecap)

    def compiling_for_cheri(self, valid_cpu_archs: "Optional[list[CPUArchitecture]]" = None) -> bool:
        return self.crosscompile_target.is_cheri_purecap(valid_cpu_archs)

    def compiling_for_cheri_hybrid(self, valid_cpu_archs: "Optional[list[CPUArchitecture]]" = None) -> bool:
        return self.crosscompile_target.is_cheri_hybrid(valid_cpu_archs)

    def compiling_for_host(self) -> bool:
        return self.crosscompile_target.is_native()

    def compiling_for_riscv(self, include_purecap: bool) -> bool:
        return self.crosscompile_target.is_riscv(include_purecap=include_purecap)

    def compiling_for_aarch64(self, include_purecap: bool) -> bool:
        return self.crosscompile_target.is_aarch64(include_purecap=include_purecap)

    def build_configuration_suffix(self, target: Optional[CrossCompileTarget] = None) -> str:
        """
        :param target: the target to use
        :return: a string such as -128/-native-asan that identifies the build configuration
        """
        config = self.config
        if target is None:
            target = self.get_crosscompile_target()
        result = ""
        if self.build_dir_suffix:
            result += self.build_dir_suffix
        if self.use_asan:
            result += "-asan"
        if self.auto_var_init != AutoVarInit.NONE:
            result += "-init-" + str(self.auto_var_init.value)
        # targets that only support native might not need a suffix
        if not target.is_native() or self.add_build_dir_suffix_for_native:
            result += target.build_suffix(config, include_os=self.include_os_in_target_suffix)
        return result

    @property
    def triple_arch(self) -> str:
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
    def display_name(self) -> str:
        if self._xtarget is None:
            return self.target + " (target alias)"
        return self.target + " (" + self._xtarget.build_suffix(self.config,
                                                               include_os=self.include_os_in_target_suffix) + ")"

    @classmethod
    def get_class_for_target(cls: "type[T]", arch: CrossCompileTarget) -> "type[T]":
        target = target_manager.get_target_raw(cls.target)
        if isinstance(target, MultiArchTarget):
            # check for exact match
            if target.target_arch is arch:
                assert issubclass(target.project_class, cls)
                return target.project_class
            # Otherwise fall back to the target alias and find the matching one
            target = target.base_target
        if isinstance(target, MultiArchTargetAlias):
            for t in target.derived_targets:
                if t.target_arch is arch:
                    assert issubclass(t.project_class, target.project_class)
                    return t.project_class
        elif isinstance(target, Target):
            # single architecture target
            result = target.project_class
            if arch is None or result._xtarget is arch:
                assert issubclass(result, cls)
                return result
        raise LookupError("Invalid arch " + str(arch) + " for class " + str(cls))

    @property
    def cross_sysroot_path(self) -> Path:
        assert self.target_info is not None, "called from invalid class " + str(self.__class__)
        return self.target_info.sysroot_dir

    # Duplicate all arguments instead of using **kwargs to get sensible code completion
    # noinspection PyShadowingBuiltins
    def run_cmd(self, *args, capture_output=False, capture_error=False, input: "Optional[Union[str, bytes]]" = None,
                timeout=None, print_verbose_only=False, run_in_pretend_mode=False, raise_in_pretend_mode=False,
                no_print=False, replace_env=False, give_tty_control=False,
                **kwargs) -> "subprocess.CompletedProcess[bytes]":
        return run_command(*args, capture_output=capture_output, capture_error=capture_error, input=input,
                           timeout=timeout, config=self.config, print_verbose_only=print_verbose_only,
                           run_in_pretend_mode=run_in_pretend_mode, raise_in_pretend_mode=raise_in_pretend_mode,
                           no_print=no_print, replace_env=replace_env, give_tty_control=give_tty_control, **kwargs)

    def set_env(self, *, print_verbose_only=True, **environ) -> "typing.ContextManager":
        return set_env(print_verbose_only=print_verbose_only, config=self.config, **environ)

    @staticmethod
    def commandline_to_str(args: "typing.Iterable[Union[str,Path]]") -> str:
        return commandline_to_str(args)

    @classmethod
    def get_config_option_name(cls, option: str) -> str:
        option = inspect.getattr_static(cls, option)
        assert isinstance(option, ConfigOptionBase)
        return option.full_option_name

    @classmethod
    def add_config_option(cls, name: str, *, show_help=False, altname: "Optional[str]" = None,
                          kind: "Union[type[T], Callable[[str], T]]" = str,
                          default: "Union[ComputedDefaultValue[T], Callable[[CheriConfig, SimpleProject], T], T, None]"
                          = None, only_add_for_targets: "Optional[tuple[CrossCompileTarget, ...]]" = None,
                          extra_fallback_config_names: "Optional[list[str]]" = None, _allow_unknown_targets=False,
                          use_default_fallback_config_names=True, **kwargs) -> Optional[T]:
        fullname = cls.target + "/" + name
        # We abuse shortname to implement altname
        if altname is not None:
            shortname = "-" + cls.target + "/" + altname
        else:
            shortname = None

        if not cls._config_loader.is_needed_for_completion(fullname, shortname, kind):
            # We are autocompleting and there is a prefix that won't match this option, so we just return the
            # default value since it won't be displayed anyway. This should noticeably speed up tab-completion.
            return default  # pytype: disable=bad-return-type
        # Hide stuff like --foo/install-directory from --help
        help_hidden = not show_help

        # check that the group was defined in the current class not a superclass
        if "_commandline_option_group" not in cls.__dict__:
            # If we are parsing command line arguments add a group for argparse
            if hasattr(cls._config_loader, "_parser"):  # XXX:  Use hasattr instead of isinstance to avoid imports.
                # noinspection PyProtectedMember
                cls._commandline_option_group = cls._config_loader._parser.add_argument_group(
                    "Options for target '" + cls.target + "'")
            else:
                cls._commandline_option_group = None
        if cls.hide_options_from_help:
            help_hidden = True
        synthetic_base = getattr(cls, "synthetic_base", None)
        fallback_name_base = None
        if synthetic_base is not None:
            # Don't show the help options for qtbase-mips64/qtbase-native/qtbase-riscv64 in default --help output, the
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
            if target is not None and target not in only_add_for_targets and not typing.TYPE_CHECKING:
                kwargs["option_cls"] = DefaultValueOnlyConfigOption

        # We don't want to inherit certain options from the non-target specific class since they should always be
        # set directly for that target. Currently the only such option is build-directory since sharing that would
        # break the build in most cases.
        # Important: Only look in the current class, not in parent classes to avoid duplicate names!
        fallback_config_names = []
        # For targets such as cheribsd-mfs-root-kernel to fall back to checking the value of the option for cheribsd
        extra_fallback_class = getattr(cls, "_config_inherits_from", None)
        if use_default_fallback_config_names and (fallback_name_base or extra_fallback_class is not None):
            if fallback_name_base:
                fallback_config_names.append(fallback_name_base + "/" + name)
            if extra_fallback_class is not None:
                assert name not in ["build-directory"]
                # Next add both cheribsd-<suffix> and cheribsd (in that order) so that cheribsd-mfs-root-kernel/...
                # overrides the cheribsd defaults.
                assert issubclass(extra_fallback_class, SimpleProject), extra_fallback_class
                if cls._xtarget is not None:
                    suffixed_target_class = extra_fallback_class.get_class_for_target(cls._xtarget)
                    fallback_config_names.append(suffixed_target_class.target + "/" + name)
                fallback_config_names.append(extra_fallback_class.target + "/" + name)
        if extra_fallback_config_names:
            fallback_config_names.extend(extra_fallback_config_names)
        legacy_alias_target_names = [tgt + "/" + name for tgt in cls.__dict__.get("_config_file_aliases", tuple())]
        return cls._config_loader.add_option(fullname, shortname, default=default, type=kind,
                                             _owning_class=cls, group=cls._commandline_option_group,
                                             help_hidden=help_hidden, _fallback_names=fallback_config_names,
                                             _legacy_alias_names=legacy_alias_target_names, **kwargs)

    @classmethod
    def add_bool_option(cls, name: str, *, altname=None,
                        only_add_for_targets: "Optional[tuple[CrossCompileTarget, ...]]" = None,
                        default: "Union[bool, ComputedDefaultValue[bool]]" = False, **kwargs) -> bool:
        return typing.cast(bool, cls.add_config_option(name, default=default, kind=bool, altname=altname,
                                                       only_add_for_targets=only_add_for_targets, **kwargs))

    @classmethod
    def add_list_option(cls, name: str, *, default=None, **kwargs) -> "list[str]":
        return typing.cast(typing.List[str],
                           cls.add_config_option(name, kind=list, default=[] if default is None else default, **kwargs))

    @classmethod
    def add_optional_path_option(cls, name: str, **kwargs) -> Optional[Path]:
        return cls.add_config_option(name, kind=Path, **kwargs)

    @classmethod
    def add_path_option(
        cls, name: str, *,
        default: "Union[ComputedDefaultValue[Path], Callable[[CheriConfig, SimpleProject], Path], Path]", **kwargs,
    ) -> Path:
        return typing.cast(Path, cls.add_config_option(name, kind=Path, default=default, **kwargs))

    __config_options_set: "dict[type[SimpleProject], bool]" = dict()
    with_clean = BoolConfigOption(
        "clean",
        default=ComputedDefaultValue(lambda config, proj: config.clean, "the value of the global --clean option"),
        help="Override --clean/--no-clean for this target only",
    )

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        # assert cls not in cls.__config_options_set, "Setup called twice?"
        cls.__config_options_set[cls] = True
        for k, v in cls._local_config_options.items():
            # If the option has been overwritten to be a constant in a subclass we should not register it - check the
            # type of the ClassVar to determine if this is actually needed.
            option = inspect.getattr_static(cls, k)
            if isinstance(option, PerProjectConfigOption):
                setattr(cls, k, v.register_config_option(cls))

    def __init__(self, config: CheriConfig, *, crosscompile_target: CrossCompileTarget) -> None:
        assert self._xtarget is not None, "Placeholder class should not be instantiated: " + repr(self)
        self.target_info = self._xtarget.create_target_info(self)
        super().__init__(config)
        self.crosscompile_target = crosscompile_target
        assert self._xtarget == crosscompile_target, "Failed to update all callers?"
        assert not self._should_not_be_instantiated, "Should not have instantiated " + self.__class__.__name__
        assert self.__class__ in self.__config_options_set, "Forgot to call super().setup_config_options()? " + str(
            self.__class__)
        self._system_deps_checked = False
        self._setup_called = False
        self._setup_late_called = False
        self._last_stdout_line_can_be_overwritten = False
        assert not hasattr(self, "gitBranch"), "gitBranch must not be used: " + self.__class__.__name__

    def setup(self) -> None:
        """
        Class setup that is run just before process()/run_tests/run_benchmarks. This ensures that all dependent targets
        have been built before and therefore querying e.g. the target compiler will work correctly.
        """
        assert not self._setup_called, "Should only be called once"
        self._setup_called = True

    def setup_late(self) -> None:
        """
        Like setup(), but called after setup() has been executed for all child classes.
        This can be used for example when adding configure arguments that depend on state modifications in setup().
        """
        assert not self._setup_late_called, "Should only be called once"
        self._setup_late_called = True

    def _validate_cheribuild_target_for_system_deps(self, cheribuild_target: "Optional[str]"):
        if not cheribuild_target:
            return
        # Check that the target actually exists
        tgt = target_manager.get_target(cheribuild_target, config=self.config, caller=self)
        # And check that it's a native target:
        if not tgt.project_class.get_crosscompile_target().is_native():
            self.fatal("add_required_*() should use a native cheribuild target and not ", cheribuild_target,
                       "- found while processing", self.target, fatal_when_pretending=True)

    def check_required_system_tool(self, executable: str, instructions: "Optional[InstallInstructions]" = None,
                                   default: "Optional[str]" = None, freebsd: "Optional[str]" = None,
                                   apt: "Optional[str]" = None, zypper: "Optional[str]" = None,
                                   homebrew: "Optional[str]" = None, cheribuild_target: "Optional[str]" = None,
                                   alternative_instructions: "Optional[str]" = None,
                                   compat_abi: "Optional[bool]" = None):
        if instructions is None:
            if compat_abi is None:
                compat_abi = self.compiling_for_host() and self.compiling_for_cheri_hybrid()
            instructions = OSInfo.install_instructions(
                executable, is_lib=False, default=default, freebsd=freebsd, zypper=zypper, apt=apt, homebrew=homebrew,
                cheribuild_target=cheribuild_target, alternative=alternative_instructions, compat_abi=compat_abi)
        if executable in self.__checked_system_tools:
            # If we already checked for this tool, the install instructions should match
            assert instructions.fixit_hint() == self.__checked_system_tools[executable].fixit_hint(), executable
            return  # already checked
        assert instructions.cheribuild_target == cheribuild_target
        assert instructions.alternative == alternative_instructions
        self._validate_cheribuild_target_for_system_deps(instructions.cheribuild_target)
        if not shutil.which(str(executable)):
            self.dependency_error("Required program", executable, "is missing!", install_instructions=instructions,
                                  cheribuild_target=instructions.cheribuild_target,
                                  cheribuild_xtarget=BasicCompilationTargets.NATIVE)
        self.__checked_system_tools[executable] = instructions

    def check_required_pkg_config(self, package: str, instructions: "Optional[InstallInstructions]" = None,
                                  default: "Optional[str]" = None, freebsd: "Optional[str]" = None,
                                  apt: "Optional[str]" = None, zypper: "Optional[str]" = None,
                                  homebrew: "Optional[str]" = None, cheribuild_target: "Optional[str]" = None,
                                  alternative_instructions: "Optional[str]" = None) -> None:
        if "pkg-config" not in self.__checked_system_tools:
            self.check_required_system_tool("pkg-config", freebsd="pkgconf", homebrew="pkg-config", apt="pkg-config")
        if instructions is None:
            instructions = OSInfo.install_instructions(
                package, is_lib=False, default=default, freebsd=freebsd, zypper=zypper, apt=apt, homebrew=homebrew,
                cheribuild_target=cheribuild_target, alternative=alternative_instructions)
        if package in self.__checked_pkg_config:
            # If we already checked for this pkg-config .pc file, the install instructions should match
            assert instructions.fixit_hint() == self.__checked_pkg_config[package].fixit_hint(), package
            return  # already checked
        self._validate_cheribuild_target_for_system_deps(instructions.cheribuild_target)
        try:
            env = {}
            # Support keg-only homebrew formulae, like libarchive
            if OSInfo.IS_MAC:
                brew_prefix = self.get_homebrew_prefix(homebrew if homebrew is not None else package, optional=True)
                if brew_prefix is not None:
                    env["PKG_CONFIG_PATH"] = os.getenv("PKG_CONFIG_PATH", "") + ":" + str(brew_prefix / "lib/pkgconfig")
            with self.set_env(**env):
                self.run_cmd(["pkg-config", "--modversion", package], capture_output=True)
        except subprocess.CalledProcessError as e:
            self.dependency_error("Required pkg-config file for", package, "is missing:", e,
                                  install_instructions=instructions,
                                  cheribuild_target=instructions.cheribuild_target,
                                  cheribuild_xtarget=BasicCompilationTargets.NATIVE)
        self.__checked_pkg_config[package] = instructions

    def check_required_system_header(self, header: str, instructions: "Optional[InstallInstructions]" = None,
                                     default: "Optional[str]" = None, freebsd: "Optional[str]" = None,
                                     apt: "Optional[str]" = None, zypper: "Optional[str]" = None,
                                     homebrew: "Optional[str]" = None, cheribuild_target: "Optional[str]" = None,
                                     alternative_instructions: "Optional[str]" = None) -> None:
        if instructions is None:
            instructions = OSInfo.install_instructions(
                header, is_lib=False, default=default, freebsd=freebsd, zypper=zypper, apt=apt, homebrew=homebrew,
                cheribuild_target=cheribuild_target, alternative=alternative_instructions)
        if header in self.__checked_system_headers:
            # If we already checked for this header file, the install instructions should match
            assert instructions.fixit_hint() == self.__checked_system_headers[header].fixit_hint(), header
            return  # already checked
        self._validate_cheribuild_target_for_system_deps(instructions.cheribuild_target)
        include_dirs = self.get_compiler_info(self.CC).get_include_dirs(self.essential_compiler_and_linker_flags)
        if not any(Path(d, header).exists() for d in include_dirs):
            self.dependency_error("Required C header", header, "is missing!", install_instructions=instructions,
                                  cheribuild_target=instructions.cheribuild_target,
                                  cheribuild_xtarget=BasicCompilationTargets.NATIVE)
        self.__checked_system_headers[header] = instructions

    def query_yes_no(self, message: str = "", *, default_result=False, force_result=True,
                     yes_no_str: "Optional[str]" = None) -> bool:
        return query_yes_no(self.config, message, default_result=default_result, force_result=force_result,
                            yes_no_str=yes_no_str)

    def ask_for_confirmation(self, message: str, error_message="Cannot continue.", default_result=True,
                             **kwargs) -> None:
        if not self.query_yes_no(message, default_result=default_result, **kwargs):
            self.fatal(error_message)

    @staticmethod
    def _handle_stderr(outfile, stream, file_lock, project: "SimpleProject"):
        for err_line in stream:
            with file_lock:
                try:
                    # noinspection PyProtectedMember
                    if project._last_stdout_line_can_be_overwritten:
                        sys.stdout.buffer.write(b"\n")
                        flush_stdio(sys.stdout)
                        project._last_stdout_line_can_be_overwritten = False
                    sys.stderr.buffer.write(err_line)
                    flush_stdio(sys.stderr)
                    if project.config.write_logfile:
                        outfile.write(err_line)
                except ValueError:
                    # Don't print a backtrace on ctrl+C (since that will exit the main thread and close the file)
                    # ValueError: write to closed file
                    continue

    def _line_not_important_stdout_filter(self, line: bytes) -> None:
        # by default we don't keep any line persistent, just have updating output
        if self._last_stdout_line_can_be_overwritten:
            sys.stdout.buffer.write(_clear_line_sequence)
        sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
        sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
        flush_stdio(sys.stdout)
        self._last_stdout_line_can_be_overwritten = True

    def _show_line_stdout_filter(self, line: bytes) -> None:
        if self._last_stdout_line_can_be_overwritten:
            sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.write(line)
        flush_stdio(sys.stdout)
        self._last_stdout_line_can_be_overwritten = False

    _stdout_filter: Optional[Callable[[bytes], None]] = None  # don't filter output by default

    # Subclasses can add the following:
    # def _stdout_filter(self, line: bytes):
    #     self._line_not_important_stdout_filter(line)

    def run_with_logfile(self, args: "typing.Sequence[str]", logfile_name: str, *, stdout_filter=None,
                         cwd: "Optional[Path]" = None, env: "Optional[dict[str, Optional[str]]]" = None,
                         append_to_logfile=False, stdin=subprocess.DEVNULL) -> None:
        """
        Runs make and logs the output
        config.quiet doesn't display anything, normal only status updates and config.verbose everything
        :param append_to_logfile: whether to append to the logfile if it exists
        :param args: the command to run (e.g. ["make", "-j32"])
        :param logfile_name: the name of the logfile (e.g. "build.log")
        :param cwd the directory to run make in (defaults to self.build_dir)
        :param stdout_filter a filter to use for standard output (a function that takes a single bytes argument)
        :param env the environment to pass to make
        :param stdin defaults to /dev/null, set to None to pass the current stdin.
        """
        print_command(args, cwd=cwd, env=env, config=self.config)
        # make sure that env is either None or a os.environ with the updated entries entries
        new_env: "Optional[dict[str, str]]" = None
        if env:
            new_env = os.environ.copy()
            env = {k: str(v) for k, v in env.items()}  # make sure everything is a string
            new_env.update(env)
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

        if not self.config.write_logfile:
            if stdout_filter is None:
                # just run the process connected to the current stdout/stdin
                check_call_handle_noexec(args, cwd=str(cwd), env=new_env)
            else:
                with keep_terminal_sane(command=args):
                    make = popen_handle_noexec(args, cwd=str(cwd), stdout=subprocess.PIPE, env=new_env)
                    self.__run_process_with_filtered_output(make, None, stdout_filter, args)
            return

        # open file in append mode
        with logfile_path.open("ab") as logfile:
            # print the command and then the logfile
            if append_to_logfile:
                logfile.write(b"\n\n")
            if cwd:
                logfile.write(("cd " + shlex.quote(str(cwd)) + " && ").encode("utf-8"))
            logfile.write(self.commandline_to_str(args).encode("utf-8") + b"\n\n")
            if self.config.quiet:
                # a lot more efficient than filtering every line
                check_call_handle_noexec(args, cwd=str(cwd), stdout=logfile, stderr=logfile, stdin=stdin, env=new_env)
                return
            with keep_terminal_sane(command=args):
                make = popen_handle_noexec(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           stdin=stdin, env=new_env)
                self.__run_process_with_filtered_output(make, logfile, stdout_filter, args)

    def __run_process_with_filtered_output(self, proc: subprocess.Popen, logfile: "Optional[typing.IO]",
                                           stdout_filter: "Callable[[bytes], None]",
                                           args: "list[str]"):
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
            message = ("See " + logfile.name + " for details.").encode("utf-8") if logfile else None
            raise subprocess.CalledProcessError(retcode, args, None, stderr=message)

    def maybe_strip_elf_file(self, file: Path, *, output_path: "Optional[Path]" = None,
                             print_verbose_only=True) -> bool:
        """Runs llvm-strip on the file if it is an ELF file and it is safe to do so."""
        if not file.is_file():
            return False
        try:
            with file.open("rb") as f:
                if f.read(4) == b"\x7fELF" and self.should_strip_elf_file(file):
                    self.verbose_print("Stripping ELF binary", file)
                    cmd = [self.target_info.strip_tool, file]
                    if output_path:
                        self.makedirs(output_path.parent)
                        cmd += ["-o", output_path]
                    self.run_cmd(cmd, print_verbose_only=print_verbose_only)
                    return True
        except OSError as e:
            self.warning("Failed to detect file type for", file, e)
        return False

    def should_strip_elf_file(self, f: Path) -> bool:
        if f.suffix == ".o":
            # We musn't strip crt1.o, etc. sice if we do the linker can't find essential symbols such as __start
            # __programe or environ.
            self.verbose_print("Not stripping", f, "since the symbol table is probably required!")
            return False
        return True

    def prepare_install_dir_for_archiving(self) -> None:
        """Perform cleanup to reduce the size of the tarball that jenkins creates"""
        self.info("No project-specific cleanup for", self.target)

    def _cleanup_old_files(self, *old_paths: Path, default_delete=True) -> None:
        for p in old_paths:
            if not p.exists():
                continue
            filetype = "directory" if p.is_dir() else "file"
            self.warning(f"Found old {filetype} {p} that is now obsolete.")
            if self.query_yes_no(f"Would you like to remove the old {filetype} {p}", default_result=default_delete):
                if p.is_dir():
                    self._delete_directories(p)
                else:
                    self.delete_file(p)

    def _cleanup_renamed_files(self, current_path: Path, current_suffix: str, old_suffixes: "list[str]"):
        """Remove old build directories/disk-images, etc. to avoid wasted disk space after renaming targets"""
        if not old_suffixes:
            return
        if current_suffix not in current_path.name:
            self.info("Warning:", current_path.name, "does not include expected suffix", current_suffix,
                      "-- either it was set in the config file or this is a logic error.")
            # raise ValueError((current_path, current_suffix))
        for old_suffix in old_suffixes:
            old_name = current_path.name.replace(current_suffix, old_suffix)
            if old_name != current_path.name:
                old_path = current_path.with_name(old_name)
                self.verbose_print("Checking for presence of old build dir", old_path)
                if old_path.is_file() or old_path.is_symlink():
                    self.warning("Found old file", old_name, "that has since been renamed to", current_path.name)
                    if self.query_yes_no("Would you like to remove the old file " + str(old_path)):
                        self.delete_file(old_path)
                elif old_path.is_dir():
                    self.warning("Found old directory", old_name, "that has since been renamed to", current_path.name)
                    if self.query_yes_no("Would you like to remove the old directory " + str(old_path)):
                        self._delete_directories(old_path)

    def _dependency_message(self, *args, problem="missing",
                            install_instructions: "Optional[InstallInstructions]" = None,
                            cheribuild_target: "Optional[str]" = None,
                            cheribuild_xtarget: "Optional[CrossCompileTarget]" = None,
                            cheribuild_action: str = "install", fatal: bool):
        self._system_deps_checked = True  # make sure this is always set
        if install_instructions is not None and isinstance(install_instructions, InstallInstructions):
            install_instructions = install_instructions.fixit_hint()
        if cheribuild_target:
            self.warning("Dependency for", self.target, problem + ":", *args, fixit_hint=install_instructions)
            if not self._setup_late_called:
                # TODO: make this a fatal error
                self.warning("TODO: Should not call dependency_error() with a cheribuild target fixit before "
                             "setup() has completed. Move the call to process() instead.")
                self.fatal("Dependency for", self.target, problem + ":", *args, fixit_hint=install_instructions)
                return
            if self.query_yes_no("Would you like to " + cheribuild_action + " the dependency (" + cheribuild_target +
                                 ") using cheribuild?", force_result=False if is_jenkins_build() else True):
                xtarget = cheribuild_xtarget if cheribuild_xtarget is not None else self.crosscompile_target
                dep_target = target_manager.get_target(cheribuild_target, required_arch=xtarget, config=self.config,
                                                       caller=self)
                dep_target.check_system_deps(self.config)
                assert dep_target.get_or_create_project(None, self.config, caller=self).crosscompile_target == xtarget
                dep_target.execute(self.config)
                return  # should be installed now
        if fatal:
            self.fatal("Dependency for", self.target, problem + ":", *args, fixit_hint=install_instructions)

    def dependency_error(self, *args, problem="missing", install_instructions: "Optional[InstallInstructions]" = None,
                         cheribuild_target: "Optional[str]" = None,
                         cheribuild_xtarget: "Optional[CrossCompileTarget]" = None, cheribuild_action: str = "install"):
        self._dependency_message(*args, problem=problem, install_instructions=install_instructions,
                                 cheribuild_target=cheribuild_target, cheribuild_action=cheribuild_action,
                                 cheribuild_xtarget=cheribuild_xtarget, fatal=True)

    def dependency_warning(self, *args, problem="missing",
                           install_instructions: "Optional[InstallInstructions]" = None,
                           cheribuild_target: "Optional[str]" = None,
                           cheribuild_xtarget: "Optional[CrossCompileTarget]" = None,
                           cheribuild_action: str = "install"):
        self._dependency_message(*args, problem=problem, install_instructions=install_instructions,
                                 cheribuild_target=cheribuild_target, cheribuild_xtarget=cheribuild_xtarget,
                                 cheribuild_action=cheribuild_action, fatal=False)

    def check_system_dependencies(self) -> None:
        """
        Checks that all the system dependencies (required tool, etc) are available
        :return: Throws an error if dependencies are missing
        """
        self._system_deps_checked = True

    def get_homebrew_prefix(self, package: "Optional[str]" = None, optional: bool = False) -> Path:
        prefix = _cached_get_homebrew_prefix(package, self.config)
        if not prefix and not optional:
            prefix = Path("/fake/homebrew/prefix/when/pretending")
            if package:
                self.dependency_error("Could not find homebrew package", package,
                                      install_instructions=InstallInstructions(f"Try running `brew install {package}`"))
                prefix = prefix / "opt" / package
            else:
                self.dependency_error("Could not find homebrew")
        return prefix

    @abstractmethod
    def process(self) -> None:
        ...

    def run_tests(self) -> None:
        # for the --test option
        status_update("No tests defined for target", self.target)

    def run_benchmarks(self) -> None:
        # for the --benchmark option
        status_update("No benchmarks defined for target", self.target)

    @staticmethod
    def get_test_script_path(script_name: str) -> Path:
        # noinspection PyUnusedLocal
        script_dir = Path("/this/will/not/work/when/using/remote-cheribuild.py")
        # generate a sensible error when using remote-cheribuild.py by omitting this line:
        script_dir = Path(__file__).parent.parent.parent / "test-scripts"  # no-combine
        return script_dir / script_name

    def run_shell_script(self, script, shell="sh", **kwargs) -> "subprocess.CompletedProcess[bytes]":
        print_args = dict(**kwargs)
        # Remove kwargs not supported by print_command
        print_args.pop("capture_output", None)
        print_args.pop("give_tty_control", None)
        print_command(shell, "-xe" if self.config.verbose else "-e", "-c", script, config=self.config, **print_args)
        kwargs["no_print"] = True
        return run_command(shell, "-xe" if self.config.verbose else "-e", "-c", script, config=self.config, **kwargs)

    def ensure_file_exists(self, what, path, fixit_hint=None) -> Path:
        if not path.exists():
            self.fatal(what, "not found:", path, fixit_hint=fixit_hint)
        return path

    def download_file(self, dest: Path, url: str, sha256: "Optional[str]" = None) -> bool:
        """
        :return: True if a new file was downloaded, false otherwise.
        """
        should_download = False
        if not dest.is_file():
            should_download = True
        elif sha256 is not None:
            existing_sha256 = self.sha256sum(dest)
            self.verbose_print("Downloaded", url, "with SHA256", existing_sha256)
            if sha256 and existing_sha256 != sha256:
                self.warning("SHA256 for", dest, "(" + existing_sha256 + ") does not match expected SHA256", sha256)
                if self.query_yes_no("Continue with unexpected file?", default_result=False, force_result=False):
                    self.info("Using file with unexpected SHA256 hash", existing_sha256)
                else:
                    self.info("Will try to download again.")
                    should_download = True
        elif self.with_clean:
            # Always download when using --clean and the SHA256 is not specified.
            should_download = True
        if should_download:
            self.makedirs(dest.parent)
            if shutil.which("wget"):
                self.run_cmd("wget", url, "-O", dest)
            elif shutil.which("curl"):
                self.run_cmd("curl", "--location", "-o", dest, url)  # --location needed to handle redirects
            elif shutil.which("fetch"):  # pre-installed on FreeBSD/CheriBSD
                self.run_cmd("fetch", "-o", dest, url)
            else:
                self.dependency_error("Cannot find a tool to download target URL.",
                                      install_instructions=InstallInstructions("Please install wget or curl"))
            downloaded_sha256 = self.sha256sum(dest)
            self.verbose_print("Downloaded", url, "with SHA256 hash", downloaded_sha256)
            if sha256 is not None and downloaded_sha256 != sha256:
                self.warning("Downloaded file SHA256 hash", downloaded_sha256, "does not match expected SHA256", sha256)
                self.ask_for_confirmation("Continue with unexpected file?", default_result=False)
        return should_download

    def print(self, *args, **kwargs) -> None:
        if not self.config.quiet:
            print(*args, **kwargs)

    def verbose_print(self, *args, **kwargs) -> None:
        if self.config.verbose:
            print(*args, **kwargs)

    @classmethod
    def targets_reset(cls) -> None:
        # For unit tests to get a fresh instance
        cls._cached_full_deps = None
        cls._cached_filtered_deps = None


# A target that is just an alias for at least one other targets but does not force building of dependencies
class TargetAlias(SimpleProject):
    do_not_add_to_targets: bool = True
    dependencies_must_be_built: bool = False
    is_alias: bool = True

    def process(self) -> None:
        dependencies = self.dependencies
        if callable(self.dependencies):
            dependencies = self.dependencies(self.config)
        assert any(True for _ in dependencies), "Expected non-empty dependencies for " + self.target


# A target that does nothing (used for e.g. the "all" target)
class TargetAliasWithDependencies(TargetAlias):
    do_not_add_to_targets: bool = True
    dependencies_must_be_built: bool = True
