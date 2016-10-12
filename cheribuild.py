#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
import os
import subprocess
import sys
from pathlib import Path

env = os.environ.copy()
scriptDir = Path(__file__).resolve().parent  # type: Path
env["PYTHONPATH"] = str(scriptDir)

try:
    # just run the module and return the corresponding exit code
    sys.exit(subprocess.call(["python3", "-b", "-Wall", "-m", "pycheribuild"] + sys.argv[1:], env=env))
except KeyboardInterrupt:
    sys.exit("Exiting due to Ctrl+C")
