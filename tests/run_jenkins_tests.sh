#!/usr/bin/env bash

set -e
set -x

case $1 in
  baseline|latest|ubuntu-baseline|ubuntu-latest)
    test_prefix=$1
    ;;
  *)
    echo "INVALID TARGET $1"
    exit 1
    ;;
esac

test_results="$PWD/../$test_prefix-results.xml"

if [ "${HOME:-/}" = "/" ]; then
  export HOME="$PWD/home"
  mkdir "$HOME"
fi

export CHERIBUILD_DEBUG=1

# Copy cheribuild to a temporary director
_srcdir=../src
if command -v git >/dev/null && [[ -z "$FORCE_RUN" ]]; then
    echo "GIT IS INSTALLED, copying to tempdir to avoid chowning files to root"
    if [[ -e ".git" ]]; then
        echo ".git already exists, cannot continue!"; exit 1
    fi
    git clone "$_srcdir" "src" < /dev/null
    cd src
else
    cd "$_srcdir"
fi

if ! PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip install --user -r requirements.txt; then
  echo "FATAL: could not install requirements"
  exit 1
fi

pytest_binary="python3 -m pytest"

# Run unit tests
rm -f "$test_results"
$pytest_binary -v --junit-xml "$test_results" tests || echo "Some tests failed"
if [ ! -e "$test_results" ]; then
  echo "FATAL: could not find test results xml"
  exit 1
fi
# env | sort
# Run all targets
env VERBOSE=1 ./tests/run_basic_tests.sh
