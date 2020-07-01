#!/bin/sh

# An example hook script to verify what is about to be pushed.  Called by "git
# push" after it has checked the remote status, but before anything has been
# pushed.  If this script exits with a non-zero status nothing will be pushed.
#
# This hook is called with the following parameters:
#
# $1 -- Name of the remote to which the push is being done
# $2 -- URL to which the push is being done
#
# If pushing without using a named remote those arguments will be equal.
#
# Information about the commits which are being pushed is supplied as lines to
# the standard input in the form:
#
#   <local ref> <local sha1> <remote ref> <remote sha1>
#
# This sample shows how to prevent push of commits where the log message starts
# with "WIP" (work in progress).

remote="$1"
url="$2"

z40=0000000000000000000000000000000000000000

try_run() {
    if [ -n "$VERBOSE" ]; then
        set -x
        if ! $@ ; then
            echo "Failed to run $@, don't push this!"
            exit 1
        fi
    else
        if ! $@ 2>/dev/null >/dev/null; then
            echo "Failed to run $@, don't push this!"
            exit 1
        fi
    fi
}

# skip expensive metalog checks in pre-push hook
export _TEST_SKIP_METALOG=1
# Also skip `git status`, etc. invocations
export _TEST_SKIP_GIT_COMMANDS=1
export CHERIBUILD_DEBUG=1


while read -r local_ref local_sha remote_ref remote_sha
do
	if [ "$local_sha" = $z40 ]
	then
		# Handle delete
		:
	else
		if [ "$remote_sha" = $z40 ]
		then
			# New branch, examine all commits
			range="$local_sha"
		else
			# Update to existing branch, examine new commits
			range="$remote_sha..$local_sha"
		fi
		# check for errors that would fail the GitHub CI:
		flake8 pycheribuild/ --count --max-line-length=127 --show-source --statistics

		# check that there are no obvious mistakes:
		try_run ./cheribuild.py -p __run_everything__ --freebsd/crossbuild --clean
		try_run ./cheribuild.py -p __run_everything__ --freebsd/crossbuild --test
		try_run ./cheribuild.py -p __run_everything__ --freebsd/crossbuild --benchmark
		# Regression for --benchmark-clean-boot:
		try_run ./cheribuild.py mibench-mips-nocheri --benchmark --benchmark-clean-boot -p
		try_run ./cheribuild.py mibench-mips-hybrid --benchmark --benchmark-clean-boot -p
		try_run ./cheribuild.py --help
		try_run ./jenkins-cheri-build.py --help
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --cpu=default -p cheribsd
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --cpu=cheri128 -p libcxx
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --test --cpu=cheri128 -p postgres
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --cpu=cheri128 -p llvm-native
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --cpu=cheri128 -p run-purecap
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --tarball --cpu=cheri128 -p llvm-native
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --cpu=cheri128 --test run-minimal --keep-install-dir --install-prefix=/rootfs --cheribsd/build-fpga-kernels --no-clean -p

		# Run python tests before pushing
		if [ -e pytest.ini ]; then
			python3 -m pytest -q . >&2 || exit 1
		fi

		# Check for WIP commit
		commit=$(git rev-list -n 1 --grep '^WIP' "$range")
		if [ -n "$commit" ]
		then
			echo >&2 "Found WIP commit in $local_ref, not pushing"
			exit 1
		fi
		# Check for rebase commit
		commit=$(git rev-list -n 1 --grep '^rebase' "$range")
		if [ -n "$commit" ]
		then
			echo >&2 "Found rebase commit $commit in $local_ref, not pushing"
			exit 1
		fi
	fi
done

exit 0
