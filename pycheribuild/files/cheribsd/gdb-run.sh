#!/bin/sh
# Start GDB, send a "r" command, and on the next prompt a backtrace
# This automates waiting for minutes until GDB has loaded symbols and then pressing r to actually start the program
"${GDB:-gdb}" -ex=r -ex=sharedlibrary -ex=bt --args "$@"
