#!/bin/bash

JENKINS_TEST_DIR=${JENKINS_TEST_DIR:-/local/scratch/$USER/jenkins-test}

export WORKSPACE=${JENKINS_TEST_DIR}
export CPU=${CPU:-cheri128}
export ISA=${ISA:-cap-table-pcrel}

./jenkins-cheri-build.py "$@"
