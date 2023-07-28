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
import collections.abc
import os
import shlex
import sys
import typing
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional, Union

from .computed_default_value import ComputedDefaultValue
from ..utils import ConfigBase, fatal_error, warning_message

T = typing.TypeVar('T')

if typing.TYPE_CHECKING:
    import argparse


class _LoadedConfigValue:
    """A simple class to hold the loaded value as well as the source (to handle relative paths correctly)"""

    def __init__(self, value, loaded_from: "Optional[Path]", used_key: "Optional[str]" = None):
        # assert value is not None, used_key + " is None"
        self.value = value
        self.loaded_from = loaded_from
        self.used_key = used_key

    def is_nested_dict(self) -> bool:
        return isinstance(self.value, dict)

    def __repr__(self) -> str:
        return repr(self.value)


class ConfigLoaderBase(ABC):
    # will be set later...
    _cheri_config: ConfigBase

    options: "typing.ClassVar[dict[str, ConfigOptionBase]]" = {}
    _json: "typing.ClassVar[dict[str, _LoadedConfigValue]]" = {}
    is_completing_arguments: bool = "_ARGCOMPLETE" in os.environ
    is_generating_readme: bool = "_GENERATING_README" in os.environ
    is_running_unit_tests: bool = False

    # argparse groups used in the command line loader

    def __init__(self, *, option_cls: "type[ConfigOptionBase]",
                 command_line_only_options_cls: "type[ConfigOptionBase]"):
        self.__option_cls: "type[ConfigOptionBase]" = option_cls
        self.__command_line_only_options_cls: "type[ConfigOptionBase]" = command_line_only_options_cls
        self.unknown_config_option_is_error = False
        self.completion_excludes = []
        # Add argparse groups
        self.action_group = self.add_argument_group("Actions to be performed")
        self.dependencies_group = self.add_argument_group("Selecting which dependencies are built")
        self.path_group = self.add_argument_group("Configuration of default paths")
        self.cross_compile_options_group = self.add_argument_group(
            "Adjust flags used when compiling MIPS/CHERI projects")
        self.tests_group = self.add_argument_group("Configuration for running tests")
        self.benchmark_group = self.add_argument_group("Configuration for running benchmarks")
        self.run_group = self.add_argument_group("Configuration for launching QEMU (and other simulators)")
        # put these right at the end since they are is not that useful
        self.freebsd_group = self.add_argument_group("FreeBSD and CheriBSD build configuration")
        self.docker_group = self.add_argument_group("Options controlling the use of docker for building")

    # noinspection PyShadowingBuiltins
    def add_commandline_only_option(self, *args, type: "Callable[[str], T]" = str, **kwargs) -> T:
        """
        :return: A config option that is always loaded from the command line no matter what the default is
        """
        return self.add_option(*args, type=type, option_cls=self.__command_line_only_options_cls, **kwargs)

    def add_commandline_only_bool_option(self, *args, default=False, **kwargs) -> bool:
        assert default is False or kwargs.get("negatable") is True
        return self.add_option(*args, option_cls=self.__command_line_only_options_cls, default=default,
                               negatable=kwargs.pop("negatable", False), type=bool, **kwargs)

    # noinspection PyShadowingBuiltins
    def add_option(self, name: str, shortname=None, *, type: "Union[type[T], Callable[[str], T]]" = str,
                   default: "Union[ComputedDefaultValue[T], Optional[T], Callable[[ConfigBase, typing.Any], T]]" = None,
                   _owning_class: "Optional[type]" = None, _fallback_names: "Optional[list[str]]" = None,
                   option_cls: "Optional[type[ConfigOptionBase[T]]]" = None, **kwargs) -> T:
        if option_cls is None:
            option_cls = self.__option_cls

        # If there is a option this one inherits the value from (e.g. cheribsd-riscv64-purecap/foo -> cheribsd/foo),
        # we register the fallback option when we first encounter a usage.
        if _fallback_names:
            for fallback_name in _fallback_names:
                fallback_option = self.options.get(fallback_name)
                if fallback_option is None:
                    # Do not assign an owning class or a default value to this implicitly added fallback option.
                    fallback_option = self.add_option(fallback_name, type=type, option_cls=option_cls)
        result = option_cls(name, shortname, default, type, _owning_class, _loader=self,
                            _fallback_names=_fallback_names, **kwargs)
        assert name not in self.options  # make sure we don't add duplicate options
        self.options[name] = result
        # noinspection PyTypeChecker
        return result  # pytype: disable=bad-return-type

    def add_bool_option(self, name: str, shortname=None, default=False, **kwargs) -> bool:
        # noinspection PyTypeChecker
        return self.add_option(name, shortname, default=default, type=bool, **kwargs)

    def add_path_option(self, name: str, *,
                        default: "Union[ComputedDefaultValue[Path], Path, Callable[[ConfigBase, typing.Any], Path]]",
                        shortname=None, **kwargs) -> Path:
        # we have to make sure we resolve this to an absolute path because otherwise steps where CWD is different fail!
        return typing.cast(Path, self.add_option(name, shortname, type=Path, default=default, **kwargs))

    def add_optional_path_option(self, name: str, *, default: "Optional[Path]" = None, shortname=None,
                                 **kwargs) -> Path:
        # we have to make sure we resolve this to an absolute path because otherwise steps where CWD is different fail!
        return self.add_option(name, shortname, type=Path, default=default, **kwargs)

    @abstractmethod
    def load(self) -> None:
        ...

    def finalize_options(self, available_targets, **kwargs) -> None:
        pass

    def reload(self) -> None:
        """
        Clear all loaded values and force reloading them (useful for tests)
        """
        self.reset()
        self.load()

    def reset(self) -> None:
        for option in self.options.values():
            option._cached = None
            option._is_default_value = False

    def debug_msg(self, *args, sep=" ", **kwargs) -> None:
        pass

    def is_needed_for_completion(self, name: str, shortname: str, option_type) -> bool:
        return True

    # noinspection PyUnresolvedReferences,PyProtectedMember
    @abstractmethod
    def add_argument_group(self, description: str) -> "Optional[argparse._ArgumentGroup]":
        ...

    # noinspection PyUnresolvedReferences,PyProtectedMember
    @abstractmethod
    def add_mutually_exclusive_group(self) -> "Optional[argparse._MutuallyExclusiveGroup]":
        ...

    @abstractmethod
    def targets(self) -> "list[str]":
        ...


class ConfigOptionBase(typing.Generic[T]):
    def __init__(self, name: str, shortname: Optional[str], default,
                 value_type: "Union[type[T], Callable[[typing.Any], T]]", _owning_class=None, *,
                 _loader: "Optional[ConfigLoaderBase]" = None, _fallback_names: "Optional[list[str]]" = None,
                 _legacy_alias_names: "Optional[list[str]]" = None):
        self.name = name
        self.shortname = shortname
        self.default = default
        self.value_type = value_type
        self._cached: "Optional[T]" = None
        self._loader = _loader
        # if none it means the global CheriConfig is the class containing this option
        self._owning_class = _owning_class
        if _fallback_names:
            assert _loader is not None
            for name in _fallback_names:
                assert _loader.options.get(name) is not None or _loader.is_completing_arguments
        self._fallback_names = _fallback_names  # for targets such as gdb-mips, etc
        self.alias_names = _legacy_alias_names  # for targets such as gdb-mips, etc
        self._is_default_value = False

    def load_option(self, config: "ConfigBase", instance: "Optional[object]", _: type,
                    return_none_if_default=False) -> T:
        result = self._load_option_impl(config, self.full_option_name)
        # fall back from --qtbase-mips/foo to --qtbase/foo
        # Try aliases first:
        if result is None and self.alias_names is not None:
            for alias_name in self.alias_names:
                result = self._load_option_impl(config, alias_name)
                if result is not None:
                    self.debug_msg("Using alias config option value", alias_name, "for", self.name, "->", result)
                    assert isinstance(result, _LoadedConfigValue)
                    break
        if result is None and self._fallback_names is not None:
            for fallback_name in self._fallback_names:
                fallback_option = self._loader.options.get(fallback_name)
                assert fallback_option is not None
                result = fallback_option._load_option_impl(config, fallback_name)
                if result is not None:
                    self.debug_msg("Using fallback config option value", fallback_name, "for", self.name, "->", result)
                    assert isinstance(result, _LoadedConfigValue)
                    break

        if result is None:  # If no option is set fall back to the default
            if return_none_if_default:
                return None  # Used in jenkins to avoid updating install directory for explicit options on commandline
            result = self._get_default_value(config, instance)
            if result is not None:
                result = _LoadedConfigValue(result, None)
            self._is_default_value = True
        # Now convert it to the right type
        try:
            result = self._convert_type(result)
        except ValueError as e:
            fatal_error("Invalid value for option '", self.full_option_name,
                        "': could not convert '", result, "': ", str(e), sep="", pretend=config.pretend)
            sys.exit()
        return result

    def _load_option_impl(self, config: "ConfigBase", target_option_name) -> "Optional[_LoadedConfigValue]":
        # target_option_name may not be the same as self.full_option_name if we are loading the fallback value
        raise NotImplementedError()

    def debug_msg(self, *args, **kwargs) -> None:
        self._loader.debug_msg(*args, **kwargs)

    @property
    def full_option_name(self) -> str:
        return self.name

    @property
    def is_default_value(self) -> bool:
        assert self._cached is not None, "Must load value before calling is_default_value()"
        return self._is_default_value

    def __get__(self, instance, owner) -> T:
        assert instance is not None or not callable(self.default), \
            f"Tried to access read config option {self.full_option_name} without an object instance. " \
            f"Config options using computed defaults can only be used with an object instance. Owner = {owner}"

        # TODO: would be nice if this was possible (but too much depends on accessing values without instances)
        # if instance is None:
        #     return self
        assert not self._owning_class or issubclass(owner, self._owning_class)
        if self._cached is None:
            # noinspection PyProtectedMember
            self._cached = self.load_option(self._loader._cheri_config, instance, owner)
        return self._cached

    def _get_default_value(self, config: "ConfigBase", instance: "Optional[object]" = None) -> _LoadedConfigValue:
        if callable(self.default):
            return self.default(config, instance)
        else:
            return self.default

    def _convert_type(self, loaded_result: _LoadedConfigValue) -> "Optional[T]":
        # check for None to make sure we don't call str(None) which would result in "None"
        if loaded_result is None:
            return None
        result = loaded_result.value
        # self.debug_msg("Converting", result, "to", self.value_type)
        # if the requested type is list, tuple, etc. use shlex.split() to convert strings to lists
        if self.value_type != str and isinstance(result, str):
            if isinstance(self.value_type, type) and issubclass(self.value_type, collections.abc.Sequence):
                string_value = result
                result = shlex.split(string_value)
                warning_message("Config option ", self.full_option_name, " (", string_value, ") should be a list, ",
                                "got a string instead -> assuming the correct value is ", result, sep="")
        if isinstance(self.value_type, type) and issubclass(self.value_type, Path):
            expanded = os.path.expanduser(os.path.expandvars(str(result)))
            while expanded.startswith("//"):
                expanded = expanded[1:]  # normpath doesn't remove multiple '/' characters at the start
            # self.debug_msg("Expanding env vars in", result, "->", expanded, os.environ)
            if loaded_result.loaded_from is not None:
                assert loaded_result.loaded_from.is_absolute()
                # Make paths relative to the config file
                result = Path(os.path.normpath(str(loaded_result.loaded_from.parent / expanded)))
            else:
                # Note: os.path.abspath also performs the normpath changes
                result = Path(os.path.abspath(expanded))  # relative to CWD if it was not loaded from the config file
            assert result.is_absolute(), result
            assert not str(result).startswith("//"), result
        else:
            result = self.value_type(result)  # make sure it has the right type (e.g. Path, int, bool, str)
        return result

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.name}) type={self.value_type} cached={self._cached}>"


class DefaultValueOnlyConfigOption(ConfigOptionBase[T]):
    # noinspection PyUnusedLocal
    def __init__(self, *args, _loader, **kwargs) -> None:
        super().__init__(*args, _loader=_loader)

    def _load_option_impl(self, config: "ConfigBase", target_option_name):
        return None  # always use the default value
