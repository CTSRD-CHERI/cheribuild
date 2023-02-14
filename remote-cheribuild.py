#!/usr/bin/env python3
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
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

scriptDir = Path(__file__).resolve().parent
host = sys.argv[1]
cheribuildArgs = sys.argv[2:]
cheribuildArgs = list(map(shlex.quote, cheribuildArgs))

with tempfile.NamedTemporaryFile(prefix="cheribuild-", suffix=".py") as tmp:
    combineScript = scriptDir / "combine-files.py"
    assert combineScript.is_file()
    subprocess.check_call([sys.executable, str(combineScript)], stdout=tmp)
    print("About to run cheribuild on host '" + host + "' with the following arguments:", cheribuildArgs)
    print("Note: file that will be run is located at", tmp.name)
    tty_option = ["-tt"] if sys.__stdin__.isatty() else []
    if "-f" not in sys.argv:
        input("Press enter to continue...")
    # the bash script:
    """
# Unfortunately the following line doesn't work propertly, ctrl+C won't kill the script
# it will only the ssh process as there is no tty (because stdin is a file)
# `ssh -t "$host" python3 - < "$script" "$@"`
# and even if we force a tty with -tt it won't work because python3 will start in interpreter mode...
# `ssh -tt "$host" python3 - < "$script" "$@"`
# so the only solution seems to be scp script to host and run it there
scp "$script" "${host}:~/.remote-py3-script.py" > /dev/null && \
    ssh -tt "$host" python3 '$HOME/.remote-py3-script.py' "$@"
"""
    remoteFile = "$HOME/.remote-py3-script.py"
    subprocess.check_call(["scp", tmp.name, host + ":" + remoteFile])
    # call execvp so that we get "^CExiting due to Ctrl+C" instead of a CalledProcessError
    os.execvp("ssh", ["ssh"] + tty_option + [host, "--", "python3", remoteFile] + cheribuildArgs)
