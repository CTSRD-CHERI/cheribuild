#!/bin/sh
if [ $# -lt "2" ]; then
    echo "usage py3-run-remote host python-script [script-args]..."
    exit 1
fi
host="$1"
script="$2"
shift 2
# Unfortunately the following line doesn't work propertly, ctrl+C won't kill the script
# it will only the ssh process as there is no tty (because stdin is a file)
# `ssh -t "$host" python3 - < "$script" "$@"`
# and even if we force a tty with -tt it won't work because python3 will start in interpreter mode...
# `ssh -tt "$host" python3 - < "$script" "$@"`
# so the only solution seems to be scp script to host and run it there
scp "$script" "${host}:~/.remote-py3-script.py" > /dev/null && ssh -tt "$host" python3 '$HOME/.remote-py3-script.py' "$@"
