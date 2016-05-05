import argparse
import json
import os
import shutil
from collections import OrderedDict
from pathlib import Path
from .utils import coloured, AnsiColour


class ConfigLoader(object):
    _parser = argparse.ArgumentParser(formatter_class=
                                      lambda prog: argparse.HelpFormatter(prog, width=shutil.get_terminal_size()[0]))
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

    cheriBitsGroup = _parser.add_mutually_exclusive_group()

    @classmethod
    def loadTargets(cls) -> list:
        """
        Loads the configuration from the command line and the JSON file
        :return The targets to build
        """
        cls._parser.add_argument("targets", metavar="TARGET", type=str, nargs="*",
                                 help="The targets to build", default=["all"])
        configdir = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        defaultConfigPath = Path(configdir, "cheribuild.json")
        cls._parser.add_argument("--config-file", metavar="FILE", type=str, default=str(defaultConfigPath),
                                 help="The config file that is used to load the default settings (default: '" +
                                      str(defaultConfigPath) + "')")
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
        if default and not callable(default) and "help" in kwargs:
            # only add the default string if it is not lambda
            kwargs["help"] = kwargs["help"] + " (default: \'" + str(default) + "\')"
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
        kwargs["default"] = False
        return cls.addOption(name, shortname, action="store_true", type=bool, **kwargs)

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
        assert self._parsedArgs  # load() must have been called before using this object
        assert hasattr(self._parsedArgs, self.action.dest)
        isDefault = False
        result = getattr(self._parsedArgs, self.action.dest)
        if not result:
            isDefault = True
            # allow lambdas as default values
            if callable(self.default):
                result = self.default(config)
            else:
                result = self.default
        # override default options from the JSON file
        assert self.action.option_strings[0].startswith("--")
        jsonKey = self.action.option_strings[0][2:]  # strip the initial --
        fromJSON = self._JSON.get(jsonKey, None)
        if not fromJSON:
            # also check action.dest (as a fallback so I don't have to update all my config files right now)
            fromJSON = self._JSON.get(self.action.dest, None)
            if fromJSON:
                print(coloured(AnsiColour.cyan, "Old JSON key", self.action.dest, "used, please use",
                               jsonKey, "instead"))
        if fromJSON and isDefault:
            print(coloured(AnsiColour.blue, "Overriding default value for", jsonKey,
                           "with value from JSON:", fromJSON))
            result = fromJSON
        if result:
            # make sure we don't call str(None) which would result in "None"
            result = self.valueType(result)  # make sure it has the right type (e.g. Path, int, bool, str)

        ConfigLoader.values[jsonKey] = result  # just for debugging
        return result

    def __get__(self, instance: "CheriConfig", owner):
        if not self._cached:
            self._cached = self._loadOption(instance)
        return self._cached
