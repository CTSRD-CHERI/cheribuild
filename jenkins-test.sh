#!/usr/bin/env bash

JENKINS_TEST_DIR=${JENKINS_TEST_DIR:-/local/scratch/$USER/jenkins-test}

export WORKSPACE=${JENKINS_TEST_DIR}

CURDIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"

"$CURDIR/jenkins-cheri-build.py" "$@"
