#!/usr/bin/env bash

pytest_binary="python3 -m pytest"


case  $1  in
  3.6.0|3.7.0|3.8.0|3.9.0|rc|ubuntu)
    test_prefix=$1
    ;;
  *)
    echo "INVALID TARGET $1"
    exit 1
    ;;
esac

_srcdir=../src
set -e
set -x

export CHERIBUILD_DEBUG=1

# Copy cheribuild to a temporary director
if command -v git >/dev/null && [[ -z "$FORCE_RUN" ]]; then
    echo "GIT IS INSTALLED, copying to tempdir to avoid chowning files to root"
    if [[ -e ".git" ]]; then
        echo ".git already exists, cannot continue!"; exit 1
    fi
    git clone "$_srcdir" "." < /dev/null
else
    cd "$_srcdir"
fi

# Run unit tests
rm -f "../$test_prefix-results.xml"
$pytest_binary -v --junit-xml "../$test_prefix-results.xml" tests || echo "Some tests failed"
if [ ! -e "../$test_prefix-results.xml" ]; then
  echo "FATAL: could not find test results xml"
  exit 1
fi
# env | sort
# Run all targets
env VERBOSE=1 ./tests/run_basic_tests.sh
