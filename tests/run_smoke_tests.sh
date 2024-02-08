#!/bin/sh

set -e
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
srcdir="$SCRIPT_DIR/.."

# Change to a non-writable directory to check we don't write to CWD in --pretend mode
cd /


try_run() {
    echo "Running: $*"
    if [ -n "$VERBOSE" ]; then
        if ! "$@" ; then
            echo >&2 "Failed to run $*, don't push this!"
            exit 1
        fi
    else
        if ! "$@" 2>/dev/null >/dev/null; then
            echo >&2 "Failed to run $*, don't push this!"
            # Run it again with stderr/stdout available:
            "$@"; exit 1
        fi
    fi
}

expect_error() {
    message="$1"
    shift
    echo "Expecting failure: $*"
    if ! "$@" 2>&1 | grep "$message" > /dev/null; then
        echo >&2 "Unexpected error message running $*, don't push this!"
        # Run it again with stderr/stdout available:
        "$@"; exit 1
        exit 1
    fi
}

# skip expensive metalog checks in pre-push hook
export _TEST_SKIP_METALOG=1
# Also skip `git status`, etc. invocations
export _TEST_SKIP_GIT_COMMANDS=1
export CHERIBUILD_DEBUG=1

# check that there are no obvious mistakes:
try_run "${srcdir}/cheribuild.py" --help
try_run "${srcdir}/jenkins-cheri-build.py" --help
try_run "${srcdir}/cheribuild.py" --get-config-option llvm-native/source-directory
# The unprefixed config option should fail:
expect_error "Fatal error: Option 'llvm/source-directory' cannot be queried" "${srcdir}/cheribuild.py" --get-config-option llvm/source-directory
try_run "${srcdir}/cheribuild.py" --get-config-option output-root
try_run "${srcdir}/cheribuild.py" --dump-config
# Check that the concatenated script still works:
try_run sh -c "\"${srcdir}/combine-files.py\" | \"$(command -v python3)\" - --get-config-option output-root"
try_run "${srcdir}/cheribuild.py" -p __run_everything__ --clean --build --test --benchmark
# Also check that we can run --pretend mode with all tools missing.
try_run env PATH=/does/not/exist "$(command -v python3)" "${srcdir}/cheribuild.py" -p __run_everything__ --clean --build --test --benchmark
try_run env WORKSPACE=/tmp "${srcdir}/jenkins-cheri-build.py" --allow-more-than-one-target --build --test --cpu=default -p __run_everything__
# Check that the CheriBSD test script works
try_run "${srcdir}/test-scripts/run_cheribsd_tests.py" -p --architecture morello-purecap --ssh-key path/to/test/ssh_key.pub --qemu-cmd /path/to/sdk/bin/qemu-system-morello --disk-image /path/to/output/cheribsd-morello-purecap.img --test-output-dir=/path/to/build/test-results/run-morello-purecap
try_run "${srcdir}/test-scripts/run_libcxx_tests.py" -p --architecture riscv64 --ssh-key /path/to/build/insecure_test_ssh_key.pub --kernel /path/to/output/kernel-riscv64.QEMU-MFS-ROOT --qemu-cmd /path/to/output/sdk/bin/qemu-system-riscv64cheri --build-dir /path/to/build/upstream-llvm-libs-riscv64-build --sysroot-dir /path/to/output/rootfs-riscv64 --lit-debug-output --ssh-executor-script /path/to/upstream-llvm-project/runtimes/../libcxx/utils/ssh.py --parallel-jobs 2 --multiprocessing-debug
# We were previously hitting an error while argument completing, check that it works now:
if python3 -c 'import argcomplete'; then
    # We previously crashed while completing options that inherited their value from a parent class, but that parent
    # option was not actually registered (an optimization to speed up completion).
    # In this case --run-riscv64/foo inherits from --run/foo but --run/ was not registered since it cannot match the
    # "--run-r" prefix:
    try_run env _ARGCOMPLETE=1 _ARGCOMPLETE_BENCHMARK=1 _ARGCOMPLETE_OUTPUT_PATH=/dev/null \
        _ARGCOMPLETE_BENCHMARK_PREFIX="run-riscv64-purecap --test --run-ri" "${srcdir}/cheribuild.py"
fi


# Various jenkins things that have failed in the past
try_run env WORKSPACE=/tmp "${srcdir}/jenkins-cheri-build.py" --build --test --tarball -p libcxx-riscv64-purecap
try_run env WORKSPACE=/tmp "${srcdir}/jenkins-cheri-build.py" --build --cpu=cheri128 -p run-morello-purecap
try_run env WORKSPACE=/tmp "${srcdir}/jenkins-cheri-build.py" --build --test --tarball --cpu=cheri128 -p llvm-native
try_run env WORKSPACE=/tmp "${srcdir}/jenkins-cheri-build.py" --build --test --tarball --cpu=cheri128 -p llvm-native --without-sdk
try_run env WORKSPACE=/tmp "${srcdir}/jenkins-cheri-build.py" --cpu=cheri128 --test run-minimal-riscv64-purecap --keep-install-dir --install-prefix=/rootfs --cheribsd/build-fpga-kernels --no-clean -p
