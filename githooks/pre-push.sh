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

try_run_verbose() {
    echo "Running: $*"
    if ! "$@" ; then
        echo >&2 "Failed to run $*, don't push this!"
        exit 1
    fi
}

try_run() {
    if [ -n "$VERBOSE" ]; then
        try_run_verbose "$@"
    else
        echo "Running: $*"
        if ! "$@" 2>/dev/null >/dev/null; then
            echo >&2 "Failed to run $*, don't push this!"
            exit 1
        fi
    fi
}

check_bad_commit_msg() {
    range=$1
    pattern=$2
    msg=$3
    # Don't scan commits already in origin/master when creating new branches
    # for pull requests: At least fbb3f6dad35497ed5dcea7e96d0a228b3e383741 and
    # 4063bcdfe4fcd33b9dd43bf39bd8be3771274ced have a bad commit message.
    bad_commit=$(git rev-list --grep "$pattern" "$range" ^origin/master)
    if [ -n "$bad_commit" ]
    then
        echo >&2 "Found $msg commit $bad_commit, not pushing"
        exit 1
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
		# Check for WIP commit
		check_bad_commit_msg "$range" '^WIP' "work-in-progress"
		# Check for rebase/fixup commit
		check_bad_commit_msg "$range" '^rebase' "rebase/fixup"
		check_bad_commit_msg "$range" '^fixup' "rebase/fixup"
		# Check for DNM (do-not-merge) commit
		check_bad_commit_msg "$range" '^DNM' "do-not-merge"

		set -e
		# check for errors that would fail the GitHub CI:
		try_run_verbose flake8

		# check that there are no obvious mistakes:
		try_run ./cheribuild.py --help
		try_run ./jenkins-cheri-build.py --help
		try_run ./cheribuild.py --get-config-option llvm/source-directory
		try_run ./cheribuild.py --get-config-option output-root
		try_run ./cheribuild.py --dump-config
		try_run ./cheribuild.py -p __run_everything__ --clean --build --test --benchmark
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --allow-more-than-one-target --build --test --cpu=default -p __run_everything__
		# Regression for --benchmark-clean-boot:
		# TODO: try_run ./cheribuild.py mibench-new-riscv64 --benchmark --benchmark-clean-boot -p
		# TODO: try_run ./cheribuild.py mibench-new-riscv64-purecap --benchmark --benchmark-clean-boot -p
		# Various jenkins things that have failed in the past
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --test --tarball -p libcxx-riscv64-purecap
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --cpu=cheri128 -p run-morello-purecap
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --test --tarball --cpu=cheri128 -p llvm-native
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --test --tarball --cpu=cheri128 -p llvm-native --without-sdk
		try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --cpu=cheri128 --test run-minimal-riscv64-purecap --keep-install-dir --install-prefix=/rootfs --cheribsd/build-fpga-kernels --no-clean -p

		# Run python tests before pushing
		if [ -e pytest.ini ]; then
			try_run_verbose python3 -m pytest -q . >&2
		fi
	fi
done

exit 0
