import json
import os
import shlex
import subprocess
import sys

from .utils import *
from .targets import targetManager
from .configloader import ConfigLoader
from .projects import *  # make sure all projects are loaded so that targetManager gets populated


# custom encoder to handle pathlib.Path objects
class MyJsonEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def default(self, o):
        if isinstance(o, Path):
            return str(o)
        return super().default(o)


def main():
    cheriConfig = CheriConfig()
    setCheriConfig(cheriConfig)
    # create the required directories
    for d in (cheriConfig.sourceRoot, cheriConfig.outputRoot, cheriConfig.extraFiles):
        if d.exists():
            continue
        if not cheriConfig.pretend:
            if cheriConfig.verbose:
                printCommand("mkdir", "-p", str(d))
            os.makedirs(str(d), exist_ok=True)
    try:
        if cheriConfig.listTargets:
            print("Available targets are:\n ", "\n  ".join(sorted(targetManager.targetNames)))
        elif cheriConfig.dumpConfig:
            print(json.dumps(ConfigLoader.values, sort_keys=True, cls=MyJsonEncoder, indent=4))
        else:
            targetManager.run(cheriConfig)
    except KeyboardInterrupt:
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError as err:
        fatalError("Command ", "`" + " ".join(map(shlex.quote, err.cmd)) + "` failed with non-zero exit code",
                   err.returncode)


if __name__ == "__main__":
    main()
