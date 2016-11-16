import argparse
import json
import os
import shlex
import shutil
import sys
import collections.abc
from collections import OrderedDict
from pathlib import Path
from .utils import coloured, AnsiColour


class ConfigLoader(object):
    # will be set later...
    _cheriConfig = None  # type: CheriConfig

    _parser = argparse.ArgumentParser(formatter_class=
                                      lambda prog: argparse.HelpFormatter(prog, width=shutil.get_terminal_size()[0]))
    _parser.add_argument("--help-all", "--help-hidden", action="help", help="Show all help options, including"
                                                                            "the target-specific ones.")
    options = []
    _parsedArgs = None
    _JSON = {}  # type: dict
    values = OrderedDict()
    # argument groups:
    revisionGroup = _parser.add_argument_group("Specifying git revisions", "Useful if the current HEAD of a repository "
                                               "does not work but an older one did.")
    remoteBuilderGroup = _parser.add_argument_group("Specifying a remote FreeBSD build server",
                                                    "Useful if you want to create a CHERI SDK on a Linux or OS X host"
                                                    " to allow cross compilation to a CHERI target.")
    deprecatedOptionsGroup = _parser.add_argument_group("Old deprecated options", "These should not be used any more")

    cheriBitsGroup = _parser.add_mutually_exclusive_group()

    showAllHelp = any(s in sys.argv for s in ("--help-all", "--help-hidden"))

    @classmethod
    def loadTargets(cls, availableTargets: list) -> list:
        """
        Loads the configuration from the command line and the JSON file
        :return The targets to build
        """
        cls._parser.add_argument("targets", metavar="TARGET", type=str, nargs=argparse.ONE_OR_MORE,
                                 help="The targets to build", default=["all"], choices=availableTargets)
        configdir = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        defaultConfigPath = Path(configdir, "cheribuild.json")
        cls._parser.add_argument("--config-file", metavar="FILE", type=str, default=str(defaultConfigPath),
                                 help="The config file that is used to load the default settings (default: '" +
                                      str(defaultConfigPath) + "')")
        try:
            # noinspection PyUnresolvedReferences
            import argcomplete
            argcomplete.autocomplete(cls._parser)
        except ImportError:
            pass
        cls._parsedArgs = cls._parser.parse_args()
        try:
            cls._configPath = Path(os.path.expanduser(cls._parsedArgs.config_file)).absolute()
            if cls._configPath.exists():
                with cls._configPath.open("r") as f:
                    cls._JSON = json.load(f, encoding="utf-8")
            else:
                print("Configuration file", cls._configPath, "does not exist, using only command line arguments.")
        except Exception as e:
            print(coloured(AnsiColour.red, "Could not load config file", cls._configPath, "-", e))
        return cls._parsedArgs.targets

    @classmethod
    def addOption(cls, name: str, shortname=None, default=None, type=None, group=None, **kwargs):
        # add the default string to help if it is not lambda and help != argparse.SUPPRESS
        if default and not callable(default) and "help" in kwargs:
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
        result = cls(action, default, type)
        cls.options.append(result)
        return result

    @classmethod
    def addBoolOption(cls, name: str, shortname=None, **kwargs) -> bool:
        return cls.addOption(name, shortname, default=False, action="store_true", type=bool, **kwargs)

    @classmethod
    def addPathOption(cls, name: str, shortname=None, **kwargs) -> Path:
        # we have to make sure we resolve this to an absolute path because otherwise steps where CWD is different fail!
        return cls.addOption(name, shortname, type=lambda s: Path(s).absolute(), **kwargs)

    def __init__(self, action: argparse.Action, default, valueType):
        self.action = action
        self.default = default
        self.valueType = valueType
        self._cached = None
        pass

    def _loadOption(self, config: "CheriConfig"):
        fullOptionName = self.action.option_strings[0][2:]  # strip the initial "--"
        result = self._loadOptionImpl(fullOptionName, config)
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
            result = self.valueType(result)  # make sure it has the right type (e.g. Path, int, bool, str)
        # print("Loaded option", self.action, "->", result)
        # import traceback
        # traceback.print_stack()
        ConfigLoader.values[fullOptionName] = self._cached  # just for debugging
        return result

    def _loadOptionImpl(self, fullOptionName: str, config: "CheriConfig"):
        assert self._parsedArgs  # load() must have been called before using this object
        assert hasattr(self._parsedArgs, self.action.dest)
        assert self.action.option_strings[0].startswith("--")

        # First check the value specified on the command line, then load JSON and then fallback to the default
        fromCmdLine = getattr(self._parsedArgs, self.action.dest)  # from command line
        # print(fullOptionName, "from cmdline:", fromCmdLine)
        if fromCmdLine is not None:
            if fromCmdLine != self.action.default:
                return fromCmdLine
            # print("From command line == default:", fromCmdLine, self.action.default, "-> trying JSON")
        # try loading it from the JSON file:
        fromJson = self._loadFromJson(fullOptionName)
        # print(fullOptionName, "from JSON:", fromJson)
        if fromJson is not None:
            print(coloured(AnsiColour.blue, "Overriding default value for", fullOptionName,
                           "with value from JSON:", fromJson))
            return fromJson
        # load the default value (which could be a lambda)
        if callable(self.default):
            return self.default(config)
        else:
            return self.default

    def _loadFromJson(self, fullOptionName: str):
        # if there are any / characters treat these as an object reference
        jsonPath = fullOptionName.split(sep="/")
        jsonKey = jsonPath[-1]  # last item is the key (e.g. llvm/build-type -> build-type)
        jsonPath = jsonPath[:-1]  # all but the last item is the path (e.g. llvm/build-type -> llvm)
        jsonObject = self._JSON
        for objRef in jsonPath:
            # Return an empty dict if it is not found
            jsonObject = jsonObject.get(objRef, {})

        result = jsonObject.get(jsonKey, None)
        if result is None:
            # also check action.dest (as a fallback so I don't have to update all my config files right now)
            result = self._JSON.get(self.action.dest, None)
            if result is not None:
                print(coloured(AnsiColour.cyan, "Old JSON key", self.action.dest, "used, please use",
                               jsonKey, "instead"))
        return result

    def __get__(self, instance, owner):
        if self._cached is None:
            self._cached = self._loadOption(self._cheriConfig)
        return self._cached
