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
    # the bash script:
    """
# Unfortunately the following line doesn't work propertly, ctrl+C won't kill the script
# it will only the ssh process as there is no tty (because stdin is a file)
# `ssh -t "$host" python3 - < "$script" "$@"`
# and even if we force a tty with -tt it won't work because python3 will start in interpreter mode...
# `ssh -tt "$host" python3 - < "$script" "$@"`
# so the only solution seems to be scp script to host and run it there
scp "$script" "${host}:~/.remote-py3-script.py" > /dev/null && ssh -tt "$host" python3 '$HOME/.remote-py3-script.py' "$@"
    """
    remoteFile = "$HOME/.remote-py3-script.py"
    subprocess.check_call(["scp", tmp.name, host + ":" + remoteFile])
    # call execvp so that we get "^CExiting due to Ctrl+C" instead of a CalledProcessError
    os.execvp("ssh", ["ssh", "-tt", host, "--", "python3", remoteFile] + cheribuildArgs)
