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
z40=0000000000000000000000000000000000000000

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

# The PRE_COMMIT_* environment variables are set by pre-commit
if [ -z "$PRE_COMMIT_TO_REF" ]; then
    echo "This script should be run by pre-commit, please run \"pre-commit install -t pre-push -f\""
fi
# Uncomment to debug unexpectedly checked commit
#echo "PRE_COMMIT_FROM_REF=$PRE_COMMIT_FROM_REF"
#echo "PRE_COMMIT_TO_REF=$PRE_COMMIT_TO_REF"
#echo "PRE_COMMIT_REMOTE_NAME=$PRE_COMMIT_REMOTE_NAME"
#echo "PRE_COMMIT_REMOTE_URL=$PRE_COMMIT_REMOTE_URL"
#echo "PRE_COMMIT_REMOTE_BRANCH=$PRE_COMMIT_REMOTE_BRANCH"
#echo "PRE_COMMIT_LOCAL_BRANCH=$PRE_COMMIT_LOCAL_BRANCH"

if [ "$PRE_COMMIT_FROM_REF" = $z40 ]
then
    # New branch, examine all commits
    range="$PRE_COMMIT_TO_REF"
else
    # Update to existing branch, examine new commits
    range="$PRE_COMMIT_FROM_REF..$PRE_COMMIT_TO_REF"
fi

# Check for WIP commit
check_bad_commit_msg "$range" '^WIP' "work-in-progress"
# Check for rebase/fixup commit
check_bad_commit_msg "$range" '^rebase' "rebase/fixup"
check_bad_commit_msg "$range" '^fixup' "rebase/fixup"
# Check for DNM (do-not-merge) commit
check_bad_commit_msg "$range" '^DNM' "do-not-merge"
