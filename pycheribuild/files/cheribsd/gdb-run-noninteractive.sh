#!/bin/sh
# Start GDB, send a "r" command, and on the next prompt a backtrace
# This automates waiting for minutes until GDB has loaded symbols and then pressing r to actually start the program
# Use "thread apply all bt" instead of "bt" to ensure a successful exit doesn't
# result in GDB returning a non-zero exit code due to missing stack
# This is the same as gdb-run.sh but non-interative (so can be used when running tests/benchmarks, etc.)
"${GDB:-gdb}" --quiet --batch --return-child-result -ex=r -ex=sharedlibrary "-ex=thread apply all bt" --args "$@"
