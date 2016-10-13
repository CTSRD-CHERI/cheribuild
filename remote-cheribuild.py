#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path

scriptDir = Path(__file__).resolve().parent  # type: Path
host = sys.argv[1]
cheribuildArgs = sys.argv[2:]

with tempfile.NamedTemporaryFile(prefix="cheribuild-", suffix=".py") as tmp:
    combineScript = scriptDir / "combine-files.py"
    assert combineScript.is_file()
    subprocess.check_call([sys.executable, str(combineScript)], stdout=tmp)
    print("About to run cheribuild on host '" + host + "' with the following arguments:", cheribuildArgs)
    print("Note: file that will be run is located at", tmp.name)
    input("Press enter to continue...")
    subprocess.check_call([str(scriptDir / "py3-run-remote.sh"), host, tmp.name] + cheribuildArgs)
