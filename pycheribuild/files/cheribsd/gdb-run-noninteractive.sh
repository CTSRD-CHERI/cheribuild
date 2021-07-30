#!/bin/sh
# This is the same as gdb-run.sh but non-interative (so can be used when running tests/benchmarks, etc.)
GDB="${GDB:-gdb} --quiet --batch --return-child-result" gdb-run.sh "$@"
