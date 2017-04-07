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
import json
import os
import shlex
import shutil
import sys
import collections.abc

try:
    import argcomplete
except ImportError:
    argcomplete = None


from collections import OrderedDict
from pathlib import Path

from .utils import *


class ConfigLoader(object):
    # will be set later...
    _cheriConfig = None  # type: CheriConfig

    _parser = argparse.ArgumentParser(formatter_class=
                                      lambda prog: argparse.HelpFormatter(prog, width=shutil.get_terminal_size()[0]))
    _parser.add_argument("--help-all", "--help-hidden", action="help", help="Show all help options, including"
                                                                            "the target-specific ones.")
    configdir = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    defaultConfigPath = Path(configdir, "cheribuild.json")
    _parser.add_argument("--config-file", metavar="FILE", type=str, default=str(defaultConfigPath),
                         help="The config file that is used to load the default settings (default: '" +
                              str(defaultConfigPath) + "')")
    options = dict()
    _parsedArgs = None
    _JSON = {}  # type: dict
    values = OrderedDict()
    # argument groups:
    # deprecatedOptionsGroup = _parser.add_argument_group("Old deprecated options", "These should not be used any more")
    cheriBitsGroup = _parser.add_mutually_exclusive_group()
    configureGroup = _parser.add_mutually_exclusive_group()

    showAllHelp = any(s in sys.argv for s in ("--help-all", "--help-hidden"))

    @classmethod
    def loadTargets(cls, availableTargets: list) -> list:
        """
        Loads the configuration from the command line and the JSON file
        :return The targets to build
        """
        targetOption = cls._parser.add_argument("targets", metavar="TARGET", nargs=argparse.ZERO_OR_MORE,
                                                help="The targets to build", choices=availableTargets + [[]])
        if argcomplete and "_ARGCOMPLETE" in os.environ:
            # if IS_FREEBSD: # FIXME: for some reason this won't work
            excludes = ["-t", "--skip-dependencies"]
            if sys.platform.startswith("freebsd"):
                excludes += ["--freebsd-builder-copy-only", "--freebsd-builder-hostname",
                             "--freebsd-builder-output-path"]

            visibleTargets = availableTargets.copy()
            visibleTargets.remove("__run_everything__")
            targetCompleter = argcomplete.completers.ChoicesCompleter(visibleTargets)
            targetOption.completer = targetCompleter
            # make sure we get target completion for the unparsed args too by adding another zero_or more options
            # not sure why this works but it's a nice hack
            unparsed = cls._parser.add_argument("targets", metavar="TARGET", type=list, nargs=argparse.ZERO_OR_MORE,
                                                help=argparse.SUPPRESS, choices=availableTargets)
            unparsed.completer = targetCompleter
            argcomplete.autocomplete(
                cls._parser,
                always_complete_options=None,  # don't print -/-- by default
                exclude=excludes,  # hide these options from the output
                print_suppressed=True,  # also include target-specific options
            )
        cls._parsedArgs, trailingTargets = cls._parser.parse_known_args()
        # print(cls._parsedArgs, trailingTargets)
        cls._parsedArgs.targets += trailingTargets
        try:
            cls._configPath = Path(os.path.expanduser(cls._parsedArgs.config_file)).absolute()
            if cls._configPath.exists():
                with cls._configPath.open("r", encoding="utf-8") as f:
                    jsonLines = []
                    for line in f.readlines():
                        stripped = line.strip()
                        if not stripped.startswith("#") and not stripped.startswith("//"):
                            jsonLines.append(line)
                    # print("".join(jsonLines))
                    cls._JSON = json.loads("".join(jsonLines), encoding="utf-8")
            else:
                print("Configuration file", cls._configPath, "does not exist, using only command line arguments.")
        except Exception as e:
            print(coloured(AnsiColour.red, "Could not load config file", cls._configPath, "-", e))
            if not input("Invalid config file " + str(cls._configPath) + ". Continue? y/[N]").lower().startswith("y"):
                raise
        return cls._parsedArgs.targets

    class ComputedDefaultValue(object):
        def __init__(self, function: "typing.Callable[[CheriConfig, typing.Any], typing.Any]",
                     asString: "typing.Union[str, typing.Callable[[typing.Any], str]"):
            self.function = function
            self.asString = asString

        def __call__(self, config: "CheriConfig", cls):
            return self.function(config, cls)

        def __str__(self):
            return self.asString

    @classmethod
    def addOption(cls, name: str, shortname=None, default=None, type: "typing.Callable[[str], Type_T]"=str, group=None,
                  helpHidden=False, _owningClass: "typing.Type"=None, **kwargs) -> "Type_T":
        # add the default string to help if it is not lambda and help != argparse.SUPPRESS

        # hide obscure options unless --help-hidden/--help/all is passed
        if helpHidden and not cls.showAllHelp:
            kwargs["help"] = argparse.SUPPRESS

        hasDefaultHelpText = isinstance(default, ConfigLoader.ComputedDefaultValue) or not callable(default)
        if default and "help" in kwargs and hasDefaultHelpText:
            if kwargs["help"] != argparse.SUPPRESS:
                kwargs["help"] = kwargs["help"] + " (default: \'" + str(default) + "\')"
        assert "default" not in kwargs  # Should be handled manually
        parserObj = group if group else cls._parser
        if shortname:
            action = parserObj.add_argument("--" + name, "-" + shortname, **kwargs)
        else:
            action = parserObj.add_argument("--" + name, **kwargs)
        assert isinstance(action, argparse.Action)
        assert not action.default  # we handle the default value manually
        assert not action.type  # we handle the type of the value manually
        result = ConfigOption(action, default, type, _owningClass, _loader=cls)
        assert name not in cls.options  # make sure we don't add duplicate options
        cls.options[name] = result
        # noinspection PyTypeChecker
        return result

    @classmethod
    def addBoolOption(cls, name: str, shortname=None, **kwargs) -> bool:
        return cls.addOption(name, shortname, default=False, action="store_true", type=bool, **kwargs)

    @classmethod
    def addPathOption(cls, name: str, shortname=None, **kwargs) -> Path:
        # we have to make sure we resolve this to an absolute path because otherwise steps where CWD is different fail!
        return cls.addOption(name, shortname, type=Path, **kwargs)


# noinspection PyProtectedMember
class ConfigOption(object):
    def __init__(self, action: argparse.Action, default, valueType, _owningClass=None,
                 _loader: "typing.Type[ConfigLoader]"=None):
        self.action = action
        self.default = default
        self.valueType = valueType
        self._cached = None
        self._loader = _loader
        self._owningClass = _owningClass  # if none it means the global CheriConfig is the class containing this option

    def _loadOption(self, config: "CheriConfig", ownerClass: "typing.Type"):
        assert self.action.option_strings[0].startswith("--")
        fullOptionName = self.action.option_strings[0][2:]  # strip the initial "--"
        result = self._loadOptionImpl(fullOptionName, config, ownerClass)
        # Now convert it to the right type
        # check for None to make sure we don't call str(None) which would result in "None"
        if result is not None:
            # print("Converting", result, "to", self.valueType)
            # if the requested type is list, tuple, etc. use shlex.split() to convert strings to lists
            if self.valueType != str and isinstance(result, str):
                if isinstance(self.valueType, type) and issubclass(self.valueType, collections.abc.Sequence):
                    stringValue = result
                    result = shlex.split(stringValue)
                    print(coloured(AnsiColour.magenta, "Config option ", fullOptionName, " (", stringValue, ") should "
                          "be a list, got a string instead -> assuming the correct value is ", result, sep=""))
            if self.valueType == Path:
                expanded = os.path.expanduser(os.path.expandvars(str(result)))
                # print("Expanding env vars in", result, "->", expanded, os.environ)
                result = Path(expanded).absolute()
            else:
                result = self.valueType(result)  # make sure it has the right type (e.g. Path, int, bool, str)
        # print("Loaded option", self.action, "->", result)
        # import traceback
        # traceback.print_stack()
        ConfigLoader.values[fullOptionName] = result  # just for debugging
        return result

    def _loadOptionImpl(self, fullOptionName: str, config: "CheriConfig", ownerClass: "typing.Type"):
        assert self._loader._parsedArgs  # load() must have been called before using this object
        assert hasattr(self._loader._parsedArgs, self.action.dest)

        # First check the value specified on the command line, then load JSON and then fallback to the default
        fromCmdLine = getattr(self._loader._parsedArgs, self.action.dest)  # from command line
        # print(fullOptionName, "from cmdline:", fromCmdLine)
        if fromCmdLine is not None:
            if fromCmdLine != self.action.default:
                return fromCmdLine
            # print("From command line == default:", fromCmdLine, self.action.default, "-> trying JSON")
        # try loading it from the JSON file:
        fromJson = self._loadFromJson(fullOptionName)
        # print(fullOptionName, "from JSON:", fromJson)
        if fromJson is not None:
            if config.verbose:
                print(coloured(AnsiColour.blue, "Overriding default value for", fullOptionName,
                               "with value from JSON:", fromJson))
            return fromJson
        # load the default value (which could be a lambda)
        if callable(self.default):
            return self.default(config, ownerClass)
        else:
            return self.default

    def _lookupKeyInJson(self, fullOptionName: str):
        if fullOptionName in self._loader._JSON:
            return self._loader._JSON[fullOptionName]
        # if there are any / characters treat these as an object reference
        jsonPath = fullOptionName.split(sep="/")
        jsonKey = jsonPath[-1]  # last item is the key (e.g. llvm/build-type -> build-type)
        jsonPath = jsonPath[:-1]  # all but the last item is the path (e.g. llvm/build-type -> llvm)
        jsonObject = self._loader._JSON
        for objRef in jsonPath:
            # Return an empty dict if it is not found
            jsonObject = jsonObject.get(objRef, {})
        return jsonObject.get(jsonKey, None)

    def _loadFromJson(self, fullOptionName: str):
        result = self._lookupKeyInJson(fullOptionName)
        # See if any of the other long option names is a valid key name:
        if result is None:
            for optionName in self.action.option_strings:
                if optionName.startswith("--"):
                    jsonKey = optionName[2:]
                    result = self._lookupKeyInJson(jsonKey)
                    if result is not None:
                        print(coloured(AnsiColour.cyan, "Old JSON key", jsonKey, "used, please use",
                                       fullOptionName, "instead"))
                        break
        # FIXME: it's about time I removed this code
        if result is None:
            # also check action.dest (as a fallback so I don't have to update all my config files right now)
            result = self._loader._JSON.get(self.action.dest, None)
            if result is not None:
                print(coloured(AnsiColour.cyan, "Old JSON key", self.action.dest, "used, please use",
                               fullOptionName, "instead"))
        return result

    def __get__(self, instance, owner):
        assert not self._owningClass or issubclass(owner, self._owningClass)
        if self._cached is None:
            self._cached = self._loadOption(self._loader._cheriConfig, owner)
        return self._cached
