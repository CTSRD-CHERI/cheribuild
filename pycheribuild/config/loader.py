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
import argparse
import collections.abc
import json
import os
import shlex
import shutil
import sys
import typing

try:
    import argcomplete
except ImportError:
    argcomplete = None

from ..utils import fatal_error, status_update, warning_message, error_message
from ..colour import AnsiColour, coloured
from pathlib import Path
from enum import Enum

if typing.TYPE_CHECKING:  # no-combine
    from .chericonfig import CheriConfig  # no-combine
    from ..projects.project import SimpleProject  # no-combine

T = typing.TypeVar('T')
AnyProjectSubclass = typing.TypeVar('AnyProjectSubclass', bound='SimpleProject')


class ComputedDefaultValue(typing.Generic[T]):
    def __init__(self, function: "typing.Callable[[CheriConfig, AnyProjectSubclass], T]",
                 as_string: "typing.Union[str, typing.Callable[[typing.Any], str]]",
                 as_readme_string: "typing.Union[str, typing.Callable[[typing.Any], str]]" = None):
        self.function = function
        self.as_string = as_string
        self.as_readme_string = as_readme_string

    def __call__(self, config: "CheriConfig", obj: "SimpleProject") -> T:
        return self.function(config, obj)

    def __repr__(self):
        return "{ComputedDefault:" + str(self.as_string) + "}"


# From https://bugs.python.org/issue25061
class _EnumArgparseType(object):
    """Factory for creating enum object types
    """

    def __init__(self, enumclass: "typing.Type[Enum]"):
        self.enums = enumclass
        # Validate that all enum keys match the expected format
        for member in enumclass:
            # only upppercase letters, numbers and _ allowed
            for c in member.name:
                if c.isdigit() or c == "_":
                    continue
                if c.isalpha() and c.isupper():
                    continue
                raise RuntimeError("Invalid character '" + c + "' found in enum " + str(enumclass) +
                                   " member " + member.name + ": must all be upper case letters or _ or digits.")
        # self.action = action

    def __call__(self, astring):
        if isinstance(astring, list):
            return [self.__call__(a) for a in astring]
        if isinstance(astring, self.enums):
            return astring  # Allow passing an enum instance
        name = self.enums.__name__
        try:
            # convert the passed value to the enum name
            enum_value_name = astring.upper()  # type: str
            enum_value_name = enum_value_name.replace("-", "_")
            for e in self.enums:
                if e.value == astring:
                    return e
            v = self.enums[enum_value_name]
        except KeyError:
            msg = ', '.join([t.name.lower() for t in self.enums])
            msg = '%s: use one of {%s}' % (name, msg)
            raise argparse.ArgumentTypeError(msg)
        #       else:
        #           self.action.choices = None  # hugly hack to prevent post validation from choices
        return v

    def __repr__(self):
        astr = ', '.join([t.name.lower() for t in self.enums])
        return '%s(%s)' % (self.enums.__name__, astr)


class _LoadedConfigValue:
    """A simple class to hold the loaded value as well as the source (to handle relative paths correctly)"""

    def __init__(self, value, loaded_from: "typing.Optional[Path]", used_key: str = None):
        # assert value is not None, used_key + " is None"
        self.value = value
        self.loaded_from = loaded_from
        self.used_key = used_key

    def is_nested_dict(self):
        return isinstance(self.value, dict)

    def __repr__(self):
        return repr(self.value)


# custom encoder to handle pathlib.Path and _LoadedConfigValue objects
class MyJsonEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        # noinspection PyArgumentList
        super().__init__(*args, **kwargs)

    def default(self, o):
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, _LoadedConfigValue):
            return o.value
        return super().default(o)


# When tab-completing, argparse spends 100ms printing the help message for all available targets
# Avoid this by providing a no-op help formatter
class NoOpHelpFormatter(argparse.HelpFormatter):
    def format_help(self):
        return "TAB-COMPLETING, THIS STRING SHOULD NOT BE VISIBLE"


def get_argcomplete_prefix():
    if "_ARGCOMPLETE_BENCHMARK" in os.environ:
        os.environ["_ARGCOMPLETE_IFS"] = "\n"
        # os.environ["COMP_LINE"] = "cheribuild.py " # return all targets
        if "COMP_LINE" not in os.environ:
            os.environ["COMP_LINE"] = "cheribuild.py foo --sq"  # return all options starting with --sq
        # os.environ["COMP_LINE"] = "cheribuild.py foo --no-s"  # return all options
        os.environ["COMP_POINT"] = str(len(os.environ["COMP_LINE"]))
    comp_line = argcomplete.ensure_str(os.environ["COMP_LINE"])
    result = argcomplete.split_line(comp_line, int(os.environ["COMP_POINT"]))[1]
    if "_ARGCOMPLETE_BENCHMARK" in os.environ:
        print("argcomplete_prefix =", result, file=sys.stderr)
    return result


class ConfigLoaderBase(object):
    # will be set later...
    _cheri_config = None  # type: CheriConfig

    options = dict()  # type: typing.Dict[str, "ConfigOptionBase"]
    _parsed_args = None
    _json = {}  # type: typing.Dict[str, _LoadedConfigValue]
    is_completing_arguments = "_ARGCOMPLETE" in os.environ
    is_generating_readme = "_GENERATING_README" in os.environ
    _argcomplete_prefix = get_argcomplete_prefix() if is_completing_arguments else None
    _argcomplete_prefix_includes_slash = "/" in _argcomplete_prefix if _argcomplete_prefix else None

    show_all_help = any(s in sys.argv for s in ("--help-all", "--help-hidden")) or is_completing_arguments

    def __init__(self, option_cls, argparser_class: "typing.Type[argparse.ArgumentParser]" = argparse.ArgumentParser):
        self.__option_cls = option_cls
        if self.is_completing_arguments:
            self._parser = argparser_class(formatter_class=NoOpHelpFormatter)
        else:
            terminal_width = shutil.get_terminal_size(fallback=(120, 24))[0]
            # noinspection PyTypeChecker
            self._parser = argparser_class(
                formatter_class=lambda prog: argparse.HelpFormatter(prog, width=terminal_width))

        self.action_group = self._parser.add_argument_group("Actions to be performed")
        self.dependencies_group = self._parser.add_argument_group("Selecting which dependencies are built")
        self.path_group = self._parser.add_argument_group("Configuration of default paths")
        self.cross_compile_options_group = self._parser.add_argument_group(
            "Adjust flags used when compiling MIPS/CHERI projects")
        self.tests_group = self._parser.add_argument_group("Configuration for running tests")
        self.benchmark_group = self._parser.add_argument_group("Configuration for running benchmarks")
        self.run_group = self._parser.add_argument_group("Configuration for launching QEMU (and other simulators)")

        # put this one right at the end since it is not that useful
        self.freebsd_group = self._parser.add_argument_group("FreeBSD and CheriBSD build configuration")
        self.docker_group = self._parser.add_argument_group("Options controlling the use of docker for building")
        self.unknown_config_option_is_error = False
        self.completion_excludes = []

    def _load_command_line_args(self):
        if argcomplete and self.is_completing_arguments:
            if "_ARGCOMPLETE_BENCHMARK" in os.environ:
                with open(os.getenv("_ARGCOMPLETE_OUTPUT_PATH", os.devnull), "wb") as output:
                    # with open("/dev/stdout", "wb") as output:
                    # sys.stdout.buffer
                    argcomplete.autocomplete(
                        self._parser,
                        always_complete_options=None,  # don't print -/-- by default
                        exclude=self.completion_excludes,  # hide these options from the output
                        print_suppressed=True,  # also include target-specific options
                        output_stream=output,
                        exit_method=sys.exit)  # ensure that cprofile data is written
            else:
                argcomplete.autocomplete(
                    self._parser,
                    always_complete_options=None,  # don't print -/-- by default
                    exclude=self.completion_excludes,  # hide these options from the output
                    print_suppressed=True,  # also include target-specific options
                )
        # Handle cases such as cheribuild.py target1 --arg target2
        # Ideally we would use parse_intermixed_args() but that requires python3.7
        # so we work around it using parse_known_args().
        self._parsed_args, trailing = self._parser.parse_known_args()
        # TODO: python 3.7 self._parsed_args = self._parser.parse_intermixed_args()
        # print(self._parsed_args, trailingTargets, file=sys.stderr)
        for x in trailing:
            # filter out unknown options (like -b)
            # exit with error
            if x.startswith('-'):
                import difflib
                # There is no officially supported API to get back all option strings, but fortunately we store
                # all the actions here anyway
                all_options = getattr(self._parser, "_option_string_actionss", {}).keys()
                if not all_options:
                    error_message("Internal argparse API change, cannot detect available command line options.")
                    all_options = ["--" + opt for opt in self.options.keys()]
                suggestions = difflib.get_close_matches(x, all_options)
                errmsg = "unknown argument '" + x + "'"
                if suggestions:
                    errmsg += ". Did you mean " + " or ".join(suggestions) + "?"
                self._parser.error(errmsg)
        self._parsed_args.targets += trailing

    def add_commandline_only_option(self, *args, **kwargs):
        """
        :return: A config option that is always loaded from the command line no matter what the default is
        """
        return self.add_option(*args, option_cls=CommandLineConfigOption, **kwargs)

    def add_commandline_only_bool_option(self, *args, default=False, **kwargs) -> bool:
        # noinspection PyTypeChecker
        assert default is False or kwargs.get("negatable") is True
        return self.add_option(*args, option_cls=CommandLineConfigOption, default=default,
                               negatable=kwargs.pop("negatable", False), type=bool, **kwargs)

    @staticmethod
    def __is_enum_type(value_type):
        return isinstance(value_type, type) and issubclass(value_type, Enum)

    def is_needed_for_completion(self, name: str, shortname: str, option_type):
        comp_prefix = self._argcomplete_prefix
        while comp_prefix is not None:  # fake loop to allow early return
            if comp_prefix.startswith("--") and name.startswith(comp_prefix[2:]):
                # self.debug_msg("comp_prefix '", comp_prefix, "' matches name: ", name, sep="")
                return True  # Okay, prefix matches long name
            elif shortname is not None and comp_prefix.startswith("-") and shortname.startswith(comp_prefix[1:]):
                # self.debug_msg("comp_prefix '", comp_prefix, "' matches shortname: ", shortname, sep="")
                return True  # Okay, prefix matches shortname
            elif option_type is bool and (comp_prefix.startswith("--no-") or self._argcomplete_prefix_includes_slash):
                slash_index = name.rfind("/")
                negated_name = name[:slash_index + 1] + "no-" + name[slash_index + 1:]
                if negated_name.startswith(comp_prefix[2:]):
                    # self.debug_msg("comp_prefix '", comp_prefix, "' matches negated option: ", negated_name, sep="")
                    return True  # Okay, prefix matches negated long name
            # We are autocompleting and there is a prefix that won't match this option, so we just return the
            # default value since it won't be displayed anyway. This should noticeably speed up tab-completion.
            # self.debug_msg("Skipping option", name)
            return False
        return True

    # noinspection PyShadowingBuiltins
    def add_option(self, name: str, shortname=None, default=None,
                   type: "typing.Union[typing.Type[T], typing.Callable[[str], T]]" = str, group=None, help_hidden=False,
                   _owning_class: "typing.Type" = None, _fallback_names: "typing.List[str]" = None,
                   option_cls: "typing.Type[ConfigOptionBase]" = None, **kwargs) -> T:
        if not self.is_needed_for_completion(name, shortname, type):
            # We are autocompleting and there is a prefix that won't match this option, so we just return the
            # default value since it won't be displayed anyway. This should noticeably speed up tab-completion.
            return default
        if option_cls is None:
            option_cls = self.__option_cls

        if self.__is_enum_type(type):
            assert "action" not in kwargs or kwargs[
                "action"] == "append", "action should be none or append for Enum options"
            assert "choices" not in kwargs, "for enum options choices are the enum names (or set enum_choices)!"
            if "enum_choices" in kwargs:
                kwargs["choices"] = tuple(t.name.lower().replace("_", "-") for t in kwargs["enum_choices"])
                del kwargs["enum_choices"]
            elif "enum_choice_strings" in kwargs:
                # noinspection PyTypeChecker
                assert len(kwargs["enum_choice_strings"]) == len(list(x for x in type))
                kwargs["choices"] = kwargs["enum_choice_strings"]
                del kwargs["enum_choice_strings"]
            else:
                # noinspection PyTypeChecker
                kwargs["choices"] = tuple(t.name.lower() for t in type)
            type = _EnumArgparseType(type)

        # noinspection PyArgumentList
        result = option_cls(name, shortname, default, type, _owning_class, _loader=self, group=group,
                            help_hidden=help_hidden, _fallback_names=_fallback_names, **kwargs)
        assert name not in self.options  # make sure we don't add duplicate options
        self.options[name] = result
        # noinspection PyTypeChecker
        return result

    def add_bool_option(self, name: str, shortname=None, default=False, **kwargs) -> bool:
        # noinspection PyTypeChecker
        return self.add_option(name, shortname, default=default, type=bool, **kwargs)

    def add_path_option(self, name: str, shortname=None, **kwargs) -> Path:
        # we have to make sure we resolve this to an absolute path because otherwise steps where CWD is different fail!
        return self.add_option(name, shortname, type=Path, **kwargs)

    def load(self):
        raise NotImplementedError()

    def finalize_options(self, available_targets, **kwargs):
        raise NotImplementedError()

    def reload(self) -> None:
        """
        Clear all loaded values and force reloading them (useful for tests)
        """
        self.reset()
        self.load()

    def reset(self):
        for option in self.options.values():
            option._cached = None

    def debug_msg(self, *args, sep=" ", **kwargs):
        if self._parsed_args and self._parsed_args.verbose is True:
            print(coloured(AnsiColour.cyan, *args, sep=sep), file=sys.stderr, **kwargs)

    @property
    def targets(self) -> "typing.List[str]":
        return self._parsed_args.targets


class ConfigOptionBase(object):
    def __init__(self, name: str, shortname: typing.Optional[str], default, value_type: "typing.Type",
                 _owning_class=None, _loader: ConfigLoaderBase = None, _fallback_names: "typing.List[str]" = None,
                 _legacy_alias_names: "typing.List[str]" = None):
        self.name = name
        self.shortname = shortname
        self.default = default
        self.value_type = value_type
        if isinstance(default, ComputedDefaultValue):
            if _loader.is_generating_readme and default.as_readme_string is not None:
                as_string = default.as_readme_string
            else:
                as_string = default.as_string
            if callable(as_string):
                self.default_str = as_string(_owning_class)
            else:
                self.default_str = str(as_string)
        elif default is not None:
            if isinstance(default, Enum) or isinstance(value_type, _EnumArgparseType):
                # allow append
                if isinstance(default, list) and not default:
                    self.default_str = "[]"
                else:
                    assert isinstance(value_type, _EnumArgparseType), "default is enum but value type isn't: " + str(
                        value_type)
                    assert isinstance(default, Enum), "Should use enum constant for default and not " + str(default)
                    self.default_str = default.name.lower()
            else:
                self.default_str = str(default)
        self._cached = None
        self._loader = _loader
        # if none it means the global CheriConfig is the class containing this option
        self._owning_class = _owning_class
        self._fallback_names = _fallback_names  # for targets such as gdb-mips, etc
        self.alias_names = _legacy_alias_names  # for targets such as gdb-mips, etc
        self._is_default_value = False

    # noinspection PyUnusedLocal
    def load_option(self, config: "CheriConfig", instance: "typing.Optional[SimpleProject]", owner: "typing.Type",
                    return_none_if_default=False):
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
                        "': could not convert '", result, "': ", str(e), sep="")
            sys.exit()
        return result

    def _load_option_impl(self, config: "CheriConfig", target_option_name) -> "typing.Optional[_LoadedConfigValue]":
        # target_option_name may not be the same as self.full_option_name if we are loading the fallback value
        raise NotImplementedError()

    def debug_msg(self, *args, **kwargs):
        self._loader.debug_msg(*args, **kwargs)

    @property
    def full_option_name(self):
        return self.name

    @property
    def is_default_value(self):
        assert self._cached is not None, "Must load value before calling is_default_value()"
        return self._is_default_value

    def __get__(self, instance, owner):
        assert instance is not None, "This attribute needs an object instance. Owner = " + str(owner)

        # TODO: would be nice if this was possible (but too much depends on accessing values without instances)
        # if instance is None:
        #     return self
        assert not self._owning_class or issubclass(owner, self._owning_class)
        if self._cached is None:
            # allow getting the value when used on a class as well:
            if instance is None:
                instance = owner
            # noinspection PyProtectedMember
            self._cached = self.load_option(self._loader._cheri_config, instance, owner)
        return self._cached

    def _get_default_value(self, config: "CheriConfig",
                           instance: "typing.Optional[SimpleProject]" = None) -> _LoadedConfigValue:
        if callable(self.default):
            return self.default(config, instance)
        else:
            return self.default

    def _convert_type(self, loaded_result: _LoadedConfigValue):
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

    def __repr__(self):
        return "<{}({}) type={} cached={}>".format(self.__class__.__name__, self.name, self.value_type, self._cached)


class DefaultValueOnlyConfigOption(ConfigOptionBase):
    # noinspection PyUnusedLocal
    def __init__(self, *args, _loader, **kwargs):
        super().__init__(*args, _loader=_loader)

    def _load_option_impl(self, config: "CheriConfig", target_option_name):
        return None  # always use the default value


# Based on Python 3.9 BooleanOptionalAction, but places the "no" after the first /
class BooleanNegatableAction(argparse.Action):
    def __init__(self, option_strings: "list[str]", dest, default=None, type=None, choices=None, required=False,
                 help=None, metavar=None, alias_names=None):
        # Add the negated option, placing the "no" after the / instead of the start -> --cheribsd/no-build-tests
        def collect_option_strings(original_strings):
            for opt in original_strings:
                all_option_strings.append(opt)
                if opt.startswith('--'):
                    slash_index = opt.rfind("/")
                    if slash_index == -1:
                        negated_opt = "--no-" + opt[2:]
                    else:
                        negated_opt = opt[:slash_index + 1] + "no-" + opt[slash_index + 1:]
                    all_option_strings.append(negated_opt)
                    self._negated_option_strings.append(negated_opt)
        all_option_strings = []
        self._negated_option_strings = []
        collect_option_strings(option_strings)
        # Don't show the alias options in --help output
        self.displayed_option_count = len(all_option_strings)
        if alias_names is not None:
            collect_option_strings(alias_names)
        super().__init__(option_strings=all_option_strings, dest=dest, nargs=0,
                         default=default, type=type, choices=choices, required=required, help=help, metavar=metavar)

    def __call__(self, parser, namespace, values, option_string=None):
        if option_string in self.option_strings:
            setattr(namespace, self.dest, option_string not in self._negated_option_strings)

    def format_usage(self):
        return ' | '.join(self.option_strings[:self.displayed_option_count])


# argparse._StoreAction but with a possible list of aliases
class StoreActionWithPossibleAliases(argparse.Action):
    def __init__(self, option_strings: "list[str]", dest, nargs=None, default=None, type=None, choices=None,
                 required=False, help=None, metavar=None, alias_names=None):
        if nargs == 1:
            raise ValueError("nargs for store actions must be 1")
        self.displayed_option_count = len(option_strings)
        if alias_names is not None:
            option_strings = option_strings + alias_names
        super().__init__(option_strings=option_strings, dest=dest, nargs=nargs, default=default, type=type,
                         choices=choices, required=required, help=help, metavar=metavar)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)

    def format_usage(self):
        return ' | '.join(self.option_strings[:self.displayed_option_count])


class CommandLineConfigOption(ConfigOptionBase):
    # noinspection PyProtectedMember,PyUnresolvedReferences
    def __init__(self, name: str, shortname: str, default, value_type: "typing.Type", _owning_class,
                 _loader: ConfigLoaderBase, help_hidden: bool, group: "argparse._ArgumentGroup",
                 _fallback_names: "typing.List[str]" = None, _legacy_alias_names: "typing.List[str]" = None, **kwargs):
        super().__init__(name, shortname, default, value_type, _owning_class, _loader, _fallback_names,
                         _legacy_alias_names)
        # hide obscure options unless --help-hidden/--help/all is passed
        if help_hidden and not self._loader.show_all_help:
            kwargs["help"] = argparse.SUPPRESS
        # _legacy_alias_names are ignored for command line options (since they only exist for backwards compat)
        self.action = self._add_argparse_action(name, shortname, default, group, kwargs)

    def _add_argparse_action(self, name, shortname, default, group, kwargs):
        # add the default string to help if it is not lambda and help != argparse.SUPPRESS
        has_default_help_text = isinstance(self.default, ComputedDefaultValue) or not callable(self.default)
        assert "default" not in kwargs  # Should be handled manually
        # noinspection PyProtectedMember
        parser_obj = group if group else self._loader._parser
        kwargs["dest"] = name
        if self.value_type is bool:
            if kwargs.pop("negatable", None) is False:
                kwargs["action"] = "store_true"
            else:
                assert "action" not in kwargs
                kwargs["action"] = BooleanNegatableAction
        else:
            action_kind = kwargs.get("action", None)
            if action_kind is None:
                kwargs["action"] = StoreActionWithPossibleAliases
            else:
                assert action_kind == "append", "Unhandled action " + action_kind
        # TODO: instantiate the actions and call parser_obj._add_action() to skip some slow argparse code
        # TODO: but need to investigate if that API is stable across versions
        if shortname:
            action = parser_obj.add_argument("--" + name, "-" + shortname, **kwargs)
        else:
            action = parser_obj.add_argument("--" + name, **kwargs)
        if self.default is not None and action.help is not None and has_default_help_text:
            if action.help != argparse.SUPPRESS:
                action.help = action.help + " (default: \'" + self.default_str + "\')"
        action.default = None  # we don't want argparse default values!
        assert not action.type  # we handle the type of the value manually
        return action

    def _load_option_impl(self, config: "CheriConfig",
                          target_option_name: str) -> "typing.Optional[_LoadedConfigValue]":
        from_cmdline = self._load_from_commandline()
        return from_cmdline

    # noinspection PyProtectedMember
    def _load_from_commandline(self) -> "typing.Optional[_LoadedConfigValue]":
        assert self._loader._parsed_args  # load() must have been called before using this object
        # FIXME: check the fallback name here
        assert hasattr(self._loader._parsed_args, self.action.dest)
        result = getattr(self._loader._parsed_args, self.action.dest)  # from command line
        if result is None:
            return None
        return _LoadedConfigValue(result, None)


# noinspection PyProtectedMember
class JsonAndCommandLineConfigOption(CommandLineConfigOption):
    def __init__(self, *args, **kwargs):
        # Note: we ignore _legacy_alias_names for command line options and only load them from the JSON
        alias_names = kwargs.pop("_legacy_alias_names", tuple())
        super().__init__(*args, **kwargs)
        self.alias_names = alias_names

    def _load_option_impl(self, config: "CheriConfig", target_option_name: str):
        # First check the value specified on the command line, then load JSON and then fallback to the default
        from_cmd_line = self._load_from_commandline()
        # config_debug(full_option_name, "from cmdline:", from_cmd_line)
        if from_cmd_line is not None:
            if from_cmd_line != self.action.default:
                return from_cmd_line
            # config_debug("Command line == default:", from_cmd_line, self.action.default, "-> trying JSON")
        # try loading it from the JSON file:
        from_json = self._load_from_json(target_option_name)
        # self.debug_msg(full_option_name, "from JSON:", from_json)
        if from_json is not None:
            status_update("Overriding default value for", target_option_name, "with value from JSON key",
                          from_json.used_key, "->", from_json.value, file=sys.stderr)
            return from_json
        return None  # not found -> fall back to default

    def _lookup_key_in_json(self, full_option_name: str) -> "typing.Optional[_LoadedConfigValue]":
        if full_option_name in self._loader._json:
            return self._loader._json[full_option_name]
        # if there are any / characters treat these as an object reference
        json_path = full_option_name.split(sep="/")
        json_key = json_path[-1]  # last item is the key (e.g. llvm/build-type -> build-type)
        json_path = json_path[:-1]  # all but the last item is the path (e.g. llvm/build-type -> llvm)
        json_object = self._loader._json
        for objRef in json_path:
            # Return an empty dict if it is not found
            json_object = json_object.get(objRef, None)
            if json_object is None:
                return None
            json_object = json_object.value
        return json_object.get(json_key, None)

    def _load_from_json(self, full_option_name: str) -> "typing.Optional[_LoadedConfigValue]":
        result = self._lookup_key_in_json(full_option_name)
        # See if any of the other long option names is a valid key name:
        if result is None:
            for optionName in self.action.option_strings:
                if optionName.startswith("--"):
                    json_key = optionName[2:]
                    result = self._lookup_key_in_json(json_key)
                    if result is not None:
                        warning_message("Old JSON key", json_key, "used, please use", full_option_name, "instead")
                        break
        # FIXME: it's about time I removed this code
        if result is None:
            # also check action.dest (as a fallback so I don't have to update all my config files right now)
            result = self._loader._json.get(self.action.dest, None)
            if result is not None:
                warning_message("Old JSON key", result.used_key, "used, please use", full_option_name, "instead")
        return result


class DefaultValueOnlyConfigLoader(ConfigLoaderBase):
    def __init__(self):
        super().__init__(DefaultValueOnlyConfigOption)
        # Ignore options stored in other classes
        self.options = dict()

    def finalize_options(self, available_targets: list, **kwargs):
        pass

    def load(self):
        pass


# https://stackoverflow.com/a/14902564/894271
def dict_raise_on_duplicates_and_store_src(ordered_pairs, src_file):
    """Reject duplicate keys."""
    d = {}
    for k, v in ordered_pairs:
        if k in d:
            raise SyntaxError("duplicate key: %r" % (k,))
        else:
            # Ensure all values store the source file
            d[k] = _LoadedConfigValue(v, src_file, used_key=k)
    return d


# https://stackoverflow.com/a/50936474
class ArgparseSetGivenAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, self.dest + '_given', True)


class JsonAndCommandLineConfigLoader(ConfigLoaderBase):
    def __init__(self, argparser_class: "typing.Type[argparse.ArgumentParser]" = argparse.ArgumentParser):
        super().__init__(JsonAndCommandLineConfigOption, argparser_class)
        self._config_path = None  # type: typing.Optional[Path]
        # Choose the default config file based on argv[0]
        # This allows me to have symlinks for e.g. stable-cheribuild.py release-cheribuild.py debug-cheribuild.py
        # that pick up the right config file in ~/.config or the cheribuild directory
        cheribuild_rootdir = Path(__file__).absolute().parent.parent.parent
        self._inferred_config_prefix = self.get_config_prefix()
        self.default_config_path = Path(cheribuild_rootdir, self._inferred_config_prefix + "cheribuild.json")
        self.path_group.add_argument("--config-file", metavar="FILE", type=str, default=str(self.default_config_path),
                                     action=ArgparseSetGivenAction,
                                     help="The config file that is used to load the default settings (default: '" +
                                          str(self.default_config_path) + "')")
        self._parser.add_argument("--help-all", "--help-hidden", action="help", help="Show all help options, including"
                                                                                     " the target-specific ones.")
        self.configure_group = self._parser.add_mutually_exclusive_group()

    @staticmethod
    def get_config_prefix():
        program = Path(sys.argv[0]).name
        suffixes = ["cheribuild", "cheribuild.py"]
        for suffix in suffixes:
            if program.endswith(suffix):
                return program[0:-len(suffix)]
        return ""

    def finalize_options(self, available_targets: list, **kwargs):
        target_option = self._parser.add_argument("targets", metavar="TARGET", nargs=argparse.ZERO_OR_MORE,
                                                  help="The targets to build")
        if argcomplete and self.is_completing_arguments:
            # if OSInfo.IS_FREEBSD: # FIXME: for some reason this won't work
            self.completion_excludes = ["-t", "--skip-dependencies"]
            if sys.platform.startswith("freebsd"):
                self.completion_excludes += ["--freebsd-builder-copy-only", "--freebsd-builder-hostname",
                                             "--freebsd-builder-output-path"]

            visible_targets = available_targets.copy()
            visible_targets.remove("__run_everything__")
            target_completer = argcomplete.completers.ChoicesCompleter(visible_targets)
            target_option.completer = target_completer
            # make sure we get target completion for the unparsed args too by adding another zero_or more options
            # not sure why this works but it's a nice hack
            unparsed = self._parser.add_argument("targets", metavar="TARGET", type=list, nargs=argparse.ZERO_OR_MORE,
                                                 help=argparse.SUPPRESS, choices=available_targets)
            unparsed.completer = target_completer

    def __load_json_with_comments(self, config_path: Path) -> "typing.Dict[str, typing.Any]":
        """
        Loads a JSON file ignoring any lines that start with '#' or '//'
        :param config_path: path to the json file
        :return: a parsed json dict
        """
        with config_path.open("r", encoding="utf-8") as f:
            json_lines = []
            for line in f.readlines():
                stripped = line.strip()
                if not stripped.startswith("#") and not stripped.startswith("//"):
                    json_lines.append(line)
            if not json_lines:
                result = dict()
                status_update("JSON config file", config_path, "was empty.")
            else:
                result = json.loads("".join(json_lines),
                                    object_pairs_hook=lambda o: dict_raise_on_duplicates_and_store_src(o, config_path))
            self.debug_msg("Parsed", config_path, "as",
                           coloured(AnsiColour.cyan, json.dumps(result, cls=MyJsonEncoder)))
            return result

    # Based on https://stackoverflow.com/a/7205107/894271
    def merge_dict_recursive(self, a: "typing.Dict[str, _LoadedConfigValue]", b: "typing.Dict[str, _LoadedConfigValue]",
                             included_file: Path, base_file: Path, path=None) -> dict:
        """merges b into a"""
        if path is None:
            path = []
        for key in b:
            if key == "#include":
                continue
            if key in a:
                if a[key].is_nested_dict() and b[key].is_nested_dict():
                    self.merge_dict_recursive(a[key].value, b[key].value, included_file, base_file, path + [str(key)])
                elif a[key] != b[key]:
                    if self._parsed_args:
                        self.debug_msg("Overriding '" + '.'.join(path + [str(key)]) + "' value", b[key], " from",
                                       included_file, "with value ", a[key], "from", base_file)
                else:
                    pass  # same leaf value
            else:
                a[key] = b[key]
        return a

    def __load_json_with_includes(self, config_path: Path):
        result = dict()
        try:
            result = self.__load_json_with_comments(config_path)
        except Exception as e:
            error_message("Could not load config file ", config_path, ": ", e, sep="")
            if not sys.__stdin__.isatty() or not input("Invalid config file " + str(config_path) +
                                                       ". Continue? y/[N]").lower().startswith("y"):
                raise
        include_value = result.get("#include")
        if include_value:
            included_path = config_path.parent / include_value.value
            included_json = self.__load_json_with_includes(included_path)
            del result["#include"]
            result = self.merge_dict_recursive(result, included_json, included_path, config_path)
            self.debug_msg(coloured(AnsiColour.cyan, "Merging JSON config file", included_path))
            self.debug_msg("New result is", coloured(AnsiColour.cyan, json.dumps(result, cls=MyJsonEncoder)))

        return result

    @property
    def config_file_path(self) -> Path:
        assert self._config_path is not None
        return self._config_path

    def _load_json_config_file(self) -> None:
        self._json = {}
        if not self._config_path:
            self._config_path = Path(os.path.expanduser(self._parsed_args.config_file)).absolute()
        if self._config_path.exists():
            self._json = self.__load_json_with_includes(self._config_path)
        elif hasattr(self._parsed_args, "config_file_given"):
            error_message("Configuration file", self._config_path, "does not exist, using only command line arguments.")
            raise FileNotFoundError(self._parsed_args.config_file)
        else:
            # No config file bundled with cheribuild, look in ~/.config
            # XXX: Ideally we would always load this file and merge the two if
            # both exist, with the bundled config file setting new defaults.
            configdir = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
            print("Checking", Path(configdir, self._config_path.name), "since", self._config_path, "doesn't exist")
            self._config_path = Path(configdir, self._config_path.name)
            if self._inferred_config_prefix:
                print(coloured(AnsiColour.green, "Note: Configuration file path inferred as"),
                      coloured(AnsiColour.blue, self._config_path),
                      coloured(AnsiColour.green, "based on command name"))
            if self._config_path.exists():
                self._json = self.__load_json_with_includes(self._config_path)
            else:
                if self._inferred_config_prefix:
                    # If the user invoked foo-cheribuild.py but foo-cheribuild.json does not exist that is almost
                    # certainly an error. Report it as such and don't
                    print(coloured(AnsiColour.green, "Note: Configuration file path inferred as"),
                          coloured(AnsiColour.blue, self._config_path),
                          coloured(AnsiColour.green, "based on command name"))
                    fatal_error("Configuration file ", self._config_path, "matching prefixed command was not found.",
                                "If this is intended pass an explicit `--config-file=/dev/null` argument.",
                                pretend=False)
                    raise FileNotFoundError(self._parsed_args.config_file)
                print(coloured(AnsiColour.green, "Note: Configuration file", self._config_path,
                               "does not exist, using only command line arguments."))

    def load(self):
        self._load_command_line_args()

        self._load_json_config_file()
        # Now validate the config file
        self._validate_config_file()

    def __validate(self, prefix: str, key: str, lcv: _LoadedConfigValue) -> bool:
        fullname = prefix + key
        if isinstance(lcv.value, dict):
            for k, v in lcv.value.items():
                self.__validate(fullname + "/", k, v)
            return True

        if fullname == "#include":
            return True

        found_option = self.options.get(fullname)
        # see if it is one of the alternate names is valid
        if found_option is None:
            for option in self.options.values():
                # only handle alternate names that aren't one character long
                if option.shortname and len(option.shortname) > 1:
                    alternate_name = option.shortname.lstrip("-")
                    if fullname == alternate_name:
                        found_option = option  # fine
                        break
                if option.alias_names:
                    if fullname in option.alias_names:
                        found_option = option  # fine
                        break

        if found_option is not None:
            # Found an option, now verify that it's not a command-line only option
            if not isinstance(found_option, JsonAndCommandLineConfigOption):
                errmsg = "Option '" + fullname + "' cannot be used in the config file"
                error_message(errmsg)
                raise ValueError(errmsg)
            return True
        error_message("Unknown config option '", fullname, "' in ", self._config_path, sep="")
        if self.unknown_config_option_is_error:
            raise ValueError("Unknown config option '" + fullname + "'")
        return False

    def _validate_config_file(self):
        for k, v in self._json.items():
            self.__validate("", k, v)

    def reset(self) -> None:
        super().reset()
        self._load_json_config_file()
