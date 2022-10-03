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
import builtins
import json
import os
import shutil
import sys
import typing
from typing import Optional, Union, Any, Callable

try:
    import argcomplete
except ImportError:
    argcomplete: Optional[Any] = None

from .computed_default_value import ComputedDefaultValue
from .config_loader_base import ConfigLoaderBase, ConfigOptionBase, DefaultValueOnlyConfigOption, _LoadedConfigValue
from ..utils import fatal_error, status_update, warning_message, error_message, ConfigBase
from ..colour import AnsiColour, coloured
from pathlib import Path
from enum import Enum

T = typing.TypeVar('T')
EnumTy = typing.TypeVar('EnumTy', bound=Enum)


# From https://bugs.python.org/issue25061
class _EnumArgparseType(typing.Generic[EnumTy]):
    """Factory for creating enum object types
    """

    def __init__(self, enumclass: "type[EnumTy]"):
        self.enums: "type[EnumTy]" = enumclass
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

    def __call__(self, astring: "Union[str, list[str], EnumTy]") -> "Union[EnumTy, list[EnumTy]]":
        if isinstance(astring, list):
            return [self.__call__(a) for a in astring]
        if isinstance(astring, self.enums):
            return typing.cast(EnumTy, astring)  # Allow passing an enum instance
        name = self.enums.__name__
        try:
            # convert the passed value to the enum name
            enum_value_name: str = astring.upper()
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

    def __repr__(self) -> str:
        astr = ', '.join([t.name.lower() for t in self.enums])
        return '%s(%s)' % (self.enums.__name__, astr)


# custom encoder to handle pathlib.Path and _LoadedConfigValue objects
class MyJsonEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs) -> None:
        # noinspection PyArgumentList
        super().__init__(*args, **kwargs)

    def default(self, o) -> Any:
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, _LoadedConfigValue):
            return o.value
        if isinstance(o, Enum):
            if isinstance(o.value, str):
                return o.value
            return o.name.replace("_", "-")
        return super().default(o)


# When tab-completing, argparse spends 100ms printing the help message for all available targets
# Avoid this by providing a no-op help formatter
class NoOpHelpFormatter(argparse.HelpFormatter):
    def format_help(self) -> str:
        return "TAB-COMPLETING, THIS STRING SHOULD NOT BE VISIBLE"


def get_argcomplete_prefix() -> str:
    if "_ARGCOMPLETE_BENCHMARK" in os.environ:
        os.environ["_ARGCOMPLETE_IFS"] = "\n"
        # os.environ["COMP_LINE"] = "cheribuild.py " # return all targets
        if "COMP_LINE" not in os.environ:
            # return all options starting with --sq
            os.environ["COMP_LINE"] = "cheribuild.py foo --enable-hybrid-for-purecap-rootfs-targets --sq"
        os.environ["COMP_POINT"] = str(len(os.environ["COMP_LINE"]))
    assert argcomplete is not None
    comp_line = os.environ["COMP_LINE"]
    result = argcomplete.split_line(comp_line, int(os.environ["COMP_POINT"]))[1]
    if "_ARGCOMPLETE_BENCHMARK" in os.environ:
        print("argcomplete_prefix =", result, file=sys.stderr)
    return result


# Based on Python 3.9 BooleanOptionalAction, but places the "no" after the first /
class BooleanNegatableAction(argparse.Action):
    # noinspection PyShadowingBuiltins
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

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        if option_string in self.option_strings:
            setattr(namespace, self.dest, option_string not in self._negated_option_strings)

    def format_usage(self) -> str:
        return ' | '.join(self.option_strings[:self.displayed_option_count])


# argparse._StoreAction but with a possible list of aliases
class StoreActionWithPossibleAliases(argparse.Action):
    # noinspection PyShadowingBuiltins
    def __init__(self, option_strings: "list[str]", dest, nargs=None, default=None, type=None, choices=None,
                 required=False, help=None, metavar=None, alias_names=None):
        if nargs == 1:
            raise ValueError("nargs for store actions must be 1")
        self.displayed_option_count = len(option_strings)
        if alias_names is not None:
            option_strings = option_strings + alias_names
        super().__init__(option_strings=option_strings, dest=dest, nargs=nargs, default=default, type=type,
                         choices=choices, required=required, help=help, metavar=metavar)

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        setattr(namespace, self.dest, values)

    def format_usage(self) -> str:
        return ' | '.join(self.option_strings[:self.displayed_option_count])


class CommandLineConfigOption(ConfigOptionBase[T]):
    _loader: "JsonAndCommandLineConfigLoader"

    # noinspection PyProtectedMember,PyUnresolvedReferences
    def __init__(self, name: str, shortname: "Optional[str]", default,
                 value_type: "Union[type[T], Callable[[Any], T]]", _owning_class, *,
                 _loader: "JsonAndCommandLineConfigLoader", help_hidden: bool,
                 group: "Optional[argparse._ArgumentGroup]", _fallback_names: "list[str]" = None,
                 _legacy_alias_names: "list[str]" = None, **kwargs):
        super().__init__(name, shortname, default, value_type, _owning_class, _loader=_loader,
                         _fallback_names=_fallback_names, _legacy_alias_names=_legacy_alias_names)
        # hide obscure options unless --help-hidden/--help/all is passed
        if help_hidden and not self._loader.show_all_help:
            kwargs["help"] = argparse.SUPPRESS
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
        # _legacy_alias_names are ignored for command line options (since they only exist for backwards compat)
        self.action = self._add_argparse_action(name, shortname, group, **kwargs)

    def _add_argparse_action(self, name, shortname, group, **kwargs) -> "argparse.Action":
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

    def _load_option_impl(self, config: ConfigBase, target_option_name: str) -> "Optional[_LoadedConfigValue]":
        from_cmdline = self._load_from_commandline()
        return from_cmdline

    # noinspection PyProtectedMember
    def _load_from_commandline(self) -> "Optional[_LoadedConfigValue]":
        assert self._loader._parsed_args  # load() must have been called before using this object
        # FIXME: check the fallback name here
        assert hasattr(self._loader._parsed_args, self.action.dest)
        result = getattr(self._loader._parsed_args, self.action.dest)  # from command line
        if result is None:
            return None
        return _LoadedConfigValue(result, None)


# noinspection PyProtectedMember
class JsonAndCommandLineConfigOption(CommandLineConfigOption[T]):
    def __init__(self, *args, **kwargs) -> None:
        # Note: we ignore _legacy_alias_names for command line options and only load them from the JSON
        alias_names = kwargs.pop("_legacy_alias_names", tuple())
        super().__init__(*args, **kwargs)
        self.alias_names = alias_names

    def _load_option_impl(self, config: ConfigBase, target_option_name: str):
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

    def _lookup_key_in_json(self, full_option_name: str) -> "Optional[_LoadedConfigValue]":
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

    def _load_from_json(self, full_option_name: str) -> "Optional[_LoadedConfigValue]":
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
    def __init__(self) -> None:
        super().__init__(option_cls=DefaultValueOnlyConfigOption,
                         command_line_only_options_cls=DefaultValueOnlyConfigOption)
        # Ignore options stored in other classes
        self.options = dict()

    def load(self) -> None:
        pass

    def targets(self) -> "list[str]":
        return []

    def add_argument_group(self, description: str) -> None:
        return None

    def add_mutually_exclusive_group(self) -> None:
        return None


# https://stackoverflow.com/a/14902564/894271
def dict_raise_on_duplicates_and_store_src(ordered_pairs, src_file) -> "dict[Any, _LoadedConfigValue]":
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
    def __call__(self, parser, namespace, values, option_string=None) -> None:
        setattr(namespace, self.dest, values)
        setattr(namespace, self.dest + '_given', True)


class CommandLineConfigLoader(ConfigLoaderBase):
    _parsed_args: argparse.Namespace

    show_all_help: bool = any(
        s in sys.argv for s in ("--help-all", "--help-hidden")) or ConfigLoaderBase.is_completing_arguments
    _argcomplete_prefix: "Optional[str]" = (
        get_argcomplete_prefix() if ConfigLoaderBase.is_completing_arguments else None)
    _argcomplete_prefix_includes_slash: bool = "/" in _argcomplete_prefix if _argcomplete_prefix else False

    def __init__(self, argparser_class: "type[argparse.ArgumentParser]" = argparse.ArgumentParser, *,
                 option_cls=CommandLineConfigOption, command_line_only_options_cls=CommandLineConfigOption):
        if self.is_completing_arguments or self.is_running_unit_tests:
            self._parser = argparser_class(formatter_class=NoOpHelpFormatter)
        else:
            terminal_width = shutil.get_terminal_size(fallback=(120, 24))[0]
            self._parser = argparser_class(
                formatter_class=lambda prog: argparse.HelpFormatter(prog, width=terminal_width))
        super().__init__(option_cls=option_cls, command_line_only_options_cls=command_line_only_options_cls)
        self._parser.add_argument("--help-all", "--help-hidden", action="help", help="Show all help options, including"
                                                                                     " the target-specific ones.")

    # noinspection PyShadowingBuiltins
    def add_option(self, name: str, shortname=None, *, type: "Union[type[T], Callable[[str], T]]" = str,
                   default: "Union[ComputedDefaultValue[T], T]" = None, group=None, help_hidden=False, **kwargs) -> T:
        if not self.is_needed_for_completion(name, shortname, type):
            # We are autocompleting and there is a prefix that won't match this option, so we just return the
            # default value since it won't be displayed anyway. This should noticeably speed up tab-completion.
            return default  # pytype: disable=bad-return-type
        if isinstance(type, builtins.type) and issubclass(type, Enum):
            # Handle enums as the argparse type
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
        return super().add_option(name, shortname, default=default, type=type, group=group, help_hidden=help_hidden,
                                  **kwargs)

    def debug_msg(self, *args, sep=" ", **kwargs) -> None:
        if self._parsed_args and self._parsed_args.verbose is True:
            print(coloured(AnsiColour.cyan, *args, sep=sep), file=sys.stderr, **kwargs)

    def _load_command_line_args(self) -> None:
        if argcomplete and self.is_completing_arguments:
            if "_ARGCOMPLETE_BENCHMARK" in os.environ:
                # Argcomplete < 2.0 needs the file in binary mode, >= 2.0 needs it in text mode.
                output_mode = "wb" if hasattr(argcomplete, "ensure_str") else "w"
                with open(os.getenv("_ARGCOMPLETE_OUTPUT_PATH", os.devnull), output_mode) as output:
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
                all_options = getattr(self._parser, "_option_string_actions", {}).keys()
                if not all_options:
                    error_message("Internal argparse API change, cannot detect available command line options.")
                    all_options = ["--" + opt for opt in self.options.keys()]
                # Suggesting the correct config option is quite expensive (currently we have to scan over ~64K
                # options), so we only do this when not running tests.
                suggestions = None
                if not self.is_running_unit_tests:
                    suggestions = difflib.get_close_matches(x, all_options)
                errmsg = "unknown argument '" + x + "'"
                if suggestions:
                    errmsg += ". Did you mean " + " or ".join(suggestions) + "?"
                self._parser.error(errmsg)
        self._parsed_args.targets += trailing

    def targets(self) -> "list[str]":
        return self._parsed_args.targets

    # noinspection PyUnresolvedReferences,PyProtectedMember
    def add_argument_group(self, description: str) -> "argparse._ArgumentGroup":
        return self._parser.add_argument_group(description)

    # noinspection PyUnresolvedReferences,PyProtectedMember
    def add_mutually_exclusive_group(self) -> "argparse._MutuallyExclusiveGroup":
        return self._parser.add_mutually_exclusive_group()

    def is_needed_for_completion(self, name: str, shortname: str, option_type) -> bool:
        comp_prefix = self._argcomplete_prefix
        if comp_prefix is None:
            return True
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
        # self.debug_msg("Skipping option", name)
        return False

    def load(self) -> None:
        self._load_command_line_args()


class JsonAndCommandLineConfigLoader(CommandLineConfigLoader):
    def __init__(self, argparser_class: "type[argparse.ArgumentParser]" = argparse.ArgumentParser, *,
                 option_cls=JsonAndCommandLineConfigOption, command_line_only_options_cls=CommandLineConfigOption):
        super().__init__(argparser_class, option_cls=option_cls,
                         command_line_only_options_cls=command_line_only_options_cls)
        self._config_path: "Optional[Path]" = None
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

    @staticmethod
    def get_config_prefix() -> str:
        program = Path(sys.argv[0]).name
        suffixes = ["cheribuild", "cheribuild.py"]
        for suffix in suffixes:
            if program.endswith(suffix):
                return program[0:-len(suffix)]
        return ""

    def __load_json_with_comments(self, config_path: Path) -> "dict[str, Any]":
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
    def merge_dict_recursive(self, a: "dict[str, _LoadedConfigValue]", b: "dict[str, _LoadedConfigValue]",
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
            print("Checking", Path(configdir, self._config_path.name), "since", self._config_path, "doesn't exist",
                  file=sys.stderr)
            self._config_path = Path(configdir, self._config_path.name)
            if self._inferred_config_prefix:
                print(coloured(AnsiColour.green, "Note: Configuration file path inferred as"),
                      coloured(AnsiColour.blue, self._config_path),
                      coloured(AnsiColour.green, "based on command name"),
                      file=sys.stderr)
            if self._config_path.exists():
                self._json = self.__load_json_with_includes(self._config_path)
            else:
                if self._inferred_config_prefix:
                    # If the user invoked foo-cheribuild.py but foo-cheribuild.json does not exist that is almost
                    # certainly an error. Report it as such and don't
                    print(coloured(AnsiColour.green, "Note: Configuration file path inferred as"),
                          coloured(AnsiColour.blue, self._config_path),
                          coloured(AnsiColour.green, "based on command name"),
                          file=sys.stderr)
                    fatal_error("Configuration file ", self._config_path, "matching prefixed command was not found.",
                                "If this is intended pass an explicit `--config-file=/dev/null` argument.",
                                pretend=False)
                    raise FileNotFoundError(self._parsed_args.config_file)
                print(coloured(AnsiColour.green, "Note: Configuration file", self._config_path,
                               "does not exist, using only command line arguments."),
                      file=sys.stderr)

    def load(self) -> None:
        super().load()
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

    def _validate_config_file(self) -> None:
        for k, v in self._json.items():
            self.__validate("", k, v)

    def reset(self) -> None:
        super().reset()
        self._load_json_config_file()
