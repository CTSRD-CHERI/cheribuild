#!/bin/sh
set -e

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/.."

try_run_verbose() {
    echo "Running: $*"
    if ! "$@" ; then
        echo >&2 "Failed to run $*, don't push this!"
        exit 1
    fi
}

# check for errors that would fail the GitHub CI:
try_run_verbose python3 -m flake8

# check that there are no obvious mistakes:
sh "$SCRIPT_DIR/run_smoke_tests.sh"

# Run python tests
try_run_verbose python3 -m pytest -q . >&2
