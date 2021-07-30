#!/bin/sh
# Start GDB, send a "r" command, and on the next prompt a backtrace
# This automates waiting for minutes until GDB has loaded symbols and then pressing r to actually start the program
# See gdb-run-noninteractive.sh for non-interactive use.
# Note: GDB uses $SHELL to start the target process by default (https://sourceware.org/gdb/onlinedocs/gdb/Environment.html)
# However, this can reset with the environment variables set before starting the debugger.
# For example, on FreeBSD the root shell is csh by default and csh resets $PATH to the default.
# We therefore disable
${GDB:-gdb} -iex="set startup-with-shell off" -ex=r -ex=sharedlibrary -ex="thread apply all bt 5" --args "$@"
