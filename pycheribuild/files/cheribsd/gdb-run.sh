#!/bin/sh
# Start GDB, send a "r" command, and on the next prompt a backtrace
# This automates waiting for minutes until GDB has loaded symbols and then pressing r to actually start the program
# See gdb-run-noninteractive.sh for non-interactive use.
"${GDB:-gdb}" -ex=r -ex=sharedlibrary "-ex=thread apply all bt" --args "$@"
