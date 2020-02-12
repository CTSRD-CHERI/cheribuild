#!/usr/bin/env bash

pytest_binary="python3 -m pytest"


case  $1  in
  3.5.0|3.6.0|3.7.0|3.8.0|rc|ubuntu)
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

# env | sort
./cheribuild.py -p __run_everything__ --freebsd/crossbuild > /dev/null
./cheribuild.py --help > /dev/null
./cheribuild.py --help-all > /dev/null
rm -f "../$test_prefix-results.xml"
$pytest_binary -v --junit-xml "../$test_prefix-results.xml" tests || echo "Some tests failed"
if [ ! -e "../$test_prefix-results.xml" ]; then
  echo "FATAL: could not find test results xml"
  exit 1
fi
# Remove all debug messages (contains ansi escape sequences and the Available targets message:)
targets=$(./cheribuild.py --list-targets | grep -v Available | grep -v "$(printf "\x1b")")
# echo "targets=$targets"
for i in $targets; do
  WORKSPACE=/tmp ./jenkins-cheri-build.py --build --cpu=cheri128 -p "$i" > /dev/null;
  WORKSPACE=/tmp ./jenkins-cheri-build.py --test --cpu=cheri128 -p "$i" > /dev/null;
done
