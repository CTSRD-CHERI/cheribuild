#!/bin/sh

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR/.." || exit 1

try_run() {
    if [ -n "$VERBOSE" ]; then
        try_run_verbose "$@"
    else
        echo "Running: $*"
        if ! "$@" 2>/dev/null >/dev/null; then
            echo >&2 "Failed to run $*, don't push this!"
            # Run it again with stderr/stdout available:
            "$@"; exit 1
        fi
    fi
}

# skip expensive metalog checks in pre-push hook
export _TEST_SKIP_METALOG=1
# Also skip `git status`, etc. invocations
export _TEST_SKIP_GIT_COMMANDS=1
export CHERIBUILD_DEBUG=1

# check that there are no obvious mistakes:
try_run ./cheribuild.py --help
try_run ./jenkins-cheri-build.py --help
try_run ./cheribuild.py --get-config-option llvm/source-directory
try_run ./cheribuild.py --get-config-option output-root
try_run ./cheribuild.py --dump-config
try_run ./cheribuild.py -p __run_everything__ --clean --build --test --benchmark
# Also check that we can run --pretend mode with all tools missing.
try_run env PATH=/does/not/exist "$(command -v python3)" ./cheribuild.py -p __run_everything__ --clean --build --test --benchmark
try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --allow-more-than-one-target --build --test --cpu=default -p __run_everything__

# Various jenkins things that have failed in the past
try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --test --tarball -p libcxx-riscv64-purecap
try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --cpu=cheri128 -p run-morello-purecap
try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --test --tarball --cpu=cheri128 -p llvm-native
try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --build --test --tarball --cpu=cheri128 -p llvm-native --without-sdk
try_run env WORKSPACE=/tmp ./jenkins-cheri-build.py --cpu=cheri128 --test run-minimal-riscv64-purecap --keep-install-dir --install-prefix=/rootfs --cheribsd/build-fpga-kernels --no-clean -p
