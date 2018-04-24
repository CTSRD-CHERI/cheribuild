#!/usr/bin/env bash

pytest_binary="pytest"
pytest_installed="no"

if [[ "$1" = "3.4.0" ]]; then
    test_prefix="3.4.0"
elif [[ "$1" = "3.5.0" ]]; then
    test_prefix="3.5.0"
elif [[ "$1" = "3.6" ]]; then
    test_prefix="3.6"
elif [[ "$1" = "rc" ]]; then
    test_prefix="rc"
elif [[ "$1" = "ubuntu" ]]; then
    test_prefix="ubuntu"
    pytest_binary="py.test-3"
    pytest_installed="yes"
else
    echo "INVALID TARGET $1"
    exit 1
fi

_srcdir=../src
set -e
set -x

# Copy cheribuild to a temporary director
if command -v git >/dev/null && [[ -z "FORCE_RUN" ]]; then
    echo "GIT IS INSTALLED, copying to tempdir to avoid chowning files to root"
    if [[ -e ".git" ]]; then
        echo ".git already exists, cannot continue!"; exit 1
    fi
    git clone "$_srcdir" "." < /dev/null
else
    cd "$_srcdir"
fi

# env | sort
./cheribuild.py -p __run_everything__ --cheribsd/crossbuild > /dev/null
./cheribuild.py --help > /dev/null
./cheribuild.py --help-all > /dev/null
if [[ "$pytest_installed" = "no" ]]; then
    pip install pytest
fi
$pytest_binary -v --junit-xml "../$test_prefix-results.xml" tests || echo "Some tests failed"
# Remove all debug messages (contains ansi escape sequences and the Available targets message:)
targets=$(./cheribuild.py --list-targets | grep -v Available | grep -v $(printf "\x1b"))
# echo "targets=$targets"
for i in $targets; do
  WORKSPACE=/tmp ./jenkins-cheri-build.py --cpu=cheri128 -p $i > /dev/null;
done
