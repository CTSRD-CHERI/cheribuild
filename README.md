# `cheribuild.py` - A script to build CHERI-related software (**requires Python 3.5.2+**)

This script automates all the steps required to build various [CHERI](http://www.chericpu.com)-related software.
For example `cheribuild.py [options] sdk` will create a SDK that can be
used to compile software for the CHERI CPU and `cheribuild.py [options] run`
will start an instance of [CheriBSD](https://github.com/CTSRD-CHERI/cheribsd) in [QEMU](https://github.com/CTSRD-CHERI/qemu).

It has been tested and should work on FreeBSD 10, 11 and 12.
On Linux, Ubuntu 16.04, Ubuntu 18.04 and OpenSUSE Tumbleweed are supported. Ubuntu 14.04 may also work but is no longer tested.
macOS 10.14 is also supported.

# Pre-Build Setup

If you are building CHERI on a Debian/Ubuntu-based machine, please install the following packages:

```shell
apt-get install libtool pkg-config clang bison cmake ninja-build samba flex texinfo libglib2.0-dev libpixman-1-dev libarchive-dev libarchive-tools libbz2-dev libattr1-dev libcap-ng-dev
```

Older versions of Ubuntu may report errors when trying to install `libarchive-tools`. In this case try using `apt-get install bsdtar` instead.

# Basic usage

If you want to start up a QEMU VM running CheriBSD run `cheribuild.py run -d` (-d means build all dependencies).
If you would like the VM to have all userspace binaries to be built for CheriABI use `cheribuild.py run-purecap -d`.
This will build the CHERI compiler, QEMU, CheriBSD, create a disk image and boot that in QEMU.
By default this builds the 128-bit version of CheriBSD.

By default `cheribuild.py` will clone all projects in `~/cheri`, use `~/cheri/build` for build directories
and install into `~/cheri/output`. However, these directories are all configurable (see below for details).


If you would like to see what the script would do run it with the `--pretend` or `-p` option.
For even more detail you can also pass `--verbose` or `-v`.


It is also possible to run this script on a remote host by using the `remote-cheribuild.py` script that is included in this repository:
`remote-cheribuild.py my.remote.server [options] <targets...>` will run this script on `my.remote.server`.


# Usage

`cheribuild.py [options...] targets...`

Example: to build and run a CheriBSD: `cheribuild.py --include-dependencies run` and
for a clean verbose build of LLVM `cheribuild.py -v --clean llvm`

## Available Targets

When selecting a target you can also build all the targets that it depends on by passing the `--include-dependencies` or `-d` option.
However, some targets (e.g. `all`, `sdk`) will always build their dependencies because running them without building the dependencies does not make sense (see the list of targets for details).

#### The following main targets are available

- `qemu` builds and installs [CTSRD-CHERI/qemu](https://github.com/CTSRD-CHERI/qemu)
- `llvm` builds and installs [CTSRD-CHERI/llvm](https://github.com/CTSRD-CHERI/llvm) and [CTSRD-CHERI/clang](https://github.com/CTSRD-CHERI/clang) and [CTSRD-CHERI/lld](https://github.com/CTSRD-CHERI/lld)
- `cheribsd` builds and installs [CTSRD-CHERI/cheribsd](https://github.com/CTSRD-CHERI/cheribsd). **NOTE**: most userspace binaries will be MIPS binaries and not CheriABI.
- `cheribsd-purecap` builds and installs [CTSRD-CHERI/cheribsd](https://github.com/CTSRD-CHERI/cheribsd) with all userspace binaries built for CheriABI.
- `disk-image` creates a CHERIBSD disk-image (MIPS userspace)
- `disk-image-purecap` creates a CHERIBSD disk-image (CheriABI userspace)
- `run` launches QEMU with the CHERIBSD disk image (MIPS userspace)
- `run-purecap` launches QEMU with the CHERIBSD disk image (CheriABI userspace)
- `cheribsd-sysroot` creates a CheriBSD sysroot.
- `freestanding-sdk` builds everything required to build and run `-ffreestanding` binaries: compiler, binutils and qemu
- `cheribsd-sdk` builds everything required to compile binaries for CheriBSD: `freestanding-sdk` and `cheribsd-sysroot`
- `sdk` is an alias for `cheribsd-sdk`
- `all`: runs all the targets listed so far (`run` comes last so you can then interact with QEMU)

#### Other targets
- `cmake` builds and installs latest [CMake](https://github.com/Kitware/CMake)
- `cherios` builds and installs [CTSRD-CHERI/cherios](https://github.com/CTSRD-CHERI/cherios)
- `cheritrace` builds and installs [CTSRD-CHERI/cheritrace](https://github.com/CTSRD-CHERI/cheritrace)
- `cherivis` builds and installs [CTSRD-CHERI/cherivis](https://github.com/CTSRD-CHERI/cherivis)

## Building the compiler and QEMU

In order to run CheriBSD you will first need to compile QEMU (`cheribuild.py qemu`).
You will also need to build LLVM (this includes a compiler and linker suitable for CHERI) using `cheribuild.py llvm`.
The compiler can generate CHERI code for MIPS (64-bit only) and RISCV (32 and 64-bit).
All binaries will by default be installed to `~/cheri/sdk/bin`.

## Building and running CheriBSD

To build CheriBSD run `cheribuild.py cheribsd` or `cheribuild.py cheribsd-purecap`.

If you would like to build all binaries in CheriBSD as pure capability programs can use the `cheribsd-purecap`
target instead.

### Disk image

The disk image is created by the `cheribuild.py disk-image` target and can then be used as a boot disk by QEMU.
To boot the pure-capability userspace you can use `cheribuild.py disk-image-purecap` instead.

In order to customize the disk image it will add all files under (by default) `~/cheri/extra-files/`
to the resulting image. When building the image cheribuild will ask you whether it should add your
SSH public keys to the `/root/.ssh/authorized_keys` file in the CheriBSD image. It will also
generate SSH host keys for the image so that those don't change everytime the image is rebuilt.
A suitable `/etc/rc.conf` and `/etc/fstab` will also be added to this directory and can then be customized.

The default path for the disk image is `~/cheri/output/cheri128-disk.img` for 128-bit.
The pure-capability images will be installed to `~/cheri/output/purecap-cheri128-disk.img`.

### CheriBSD SSH ports

Since cheribuild.py was designed to be run by multiple users on a shared build system it will tell QEMU
to listen on a port on localhost that depends on the user ID to avoid conflicts.
It will print a message such as `Listening for SSH connections on localhost:12374`, i.e. you will need
to use `ssh -p 12374 root@localhost` to connect to CheriBSD.
This can be changed using `cheribuild.py --run/ssh-forwarding-port <portno> run` or be made persistent
with the following configuration file (see below for more details on the config file format and path):
```json
{
    "run": {
        "ssh-forwarding-port": 12345
    }, "run-purecap": {
        "ssh-forwarding-port": 12346
    }
}
```

### Speeding up SSH connections
Connecting to CheriBSD via ssh can take a few seconds. Further connections after the first can
be sped up by using the openssh ControlMaster setting:
```
Host cheribsd
  User root
  Port 12345
  HostName localhost
  ControlPath ~/.ssh/controlmasters/%r@%h:%p
  ControlMaster auto
  StrictHostKeyChecking no
  
Host cheribsd-purecap
  User root
  Port 12346
  HostName localhost
  ControlPath ~/.ssh/controlmasters/%r@%h:%p
  ControlMaster auto
  StrictHostKeyChecking no
```

## Building GDB

You can also build a [version of GDB that understands CHERI capabilities](https://github.com/bsdjhb/gdb/tree/mips_cheri-8.0.1)
either as a binary for the host (`cheribuild.py gdb-native`) to debug coredumps or as a MIPS binary to use
for live debugging in CheriBSD (`cheribuild.py gdb-mips`).
The MIPS binary will be installed in `usr/local/bin/gdb` under your CheriBSD rootfs and will be included
when you build a new disk image (`cheribuild.py disk-image`).
The native GDB will be installed to your SDK binary directory (`~/cheri/sdk/bin` by default).

## Cross-compiling for CheriBSD

In order to cross-compile projects such as NGINX or PostgreSQL for CheriBSD you will first need a full SDK:
`cheribuild.py cheribsd-sdk`. The you can then run `cheribuild.py postgres-cheri` or `cheribuild.py nginx-mips`, etc.
By default these projects will be installed into your CheriBSD rootfs under /opt and will therefore be
automatically included the next time you build a disk image.

See `cheribuild.py --list-targets` for a full list of targets.

## Cross-compiling baremetal MIPS/CHERI

There is currently experimental support to build libcxx as a baremetal library running on top of newlib.
This can be done by running `cheribuild.py libcxx-baremetal -d`.

## Adapting the build configuration
There are a lot of options to customize the behaviour of this script: e.g. the directory for
the cloned sources can be changed from the default of `$HOME/cheri` using the `--source-root=` option.
A full list of the available options with descriptions can be found [towards the end of this document](#full-list-of-options).

The options can also be made persistent by storing them in a JSON config file (`~/.config/cheribuild.json`).
Options passed on the command line will override those read from the config file.
The key in the JSON config file is the same as the long option name without the intial `--`.
For example if you want cheribuild.py to behave as if you had passed
`--source-root /sources/cheri --output-root /build/cheri --128 -j 4 --cheribsd/build-options "-DWITH_CHERI_PURE FOO=bar"`, you can write the following JSON to
`~/.config/cheribuild.json`:

```json
{
  "source-root": "/sources/cheri",
  "output-root": "/build/cheri",
  "make-jobs": 4,
  "cheribsd": {
    "build-options": ["-DWITH_CHERI_PURE", "FOO=bar"]
  }
}
```
### Prefixed cheribuild.py symlinks to select config file

If you invoke cheribuild.py as a prefixed command (e.g. debug-cheribuild.py, stable-cheribuild.py) it will
read the file `~/.config/{prefix}-cheribuild.json` instead. This makes it easy to build
debug and release builds of e.g. LLVM or build CheriBSD with various different flags.

### Including config files

If you have many config files (e.g. cheribsd-purecap, debug, release, etc.) it is now
possible to `#include` a base config file and only write the settings that are different.

For example a `~/.config/purecap-cheribuild.json` could look like this:

```json
{
	"build-root": "/build-purecap",
	"#include": "cheribuild-common.json",
	"cheribsd": {
		"build-options": ["-DWITH_CHERI_PURE"]
	}
}
```

# Getting shell completion

You will need to install python3-argcomplete:
```
pip3 install --user argcomplete

# Or install latest version from git:
git clone https://github.com/kislyuk/argcomplete.git
cd argcomplete
python3 setup.py install --user
```

**NOTE:** On FreeBSD pip and setuptools are not installed by default so you need to run
`python3 -m ensurepip --user` first.


### BASH
```
# NOTE: the next command doesn't seem to work on FreeBSD
~/.local/bin/activate-global-python-argcomplete --user
# On FreeBSD (or if the above work for some other reason) do this:
echo 'eval "$(register-python-argcomplete cheribuild.py)"' >> ~/.bashrc
```

### TCSH:
With tcsh add the following line to `~/.cshrc`:
```
eval "`register-python-argcomplete --shell tcsh cheribuild.py`"
```
Note: `python-argcomplete-tcsh` must be in `$PATH` (should be in `~/.local/bin/`).
I would also suggest using `set autolist` to display all options.


# List of options (--help output)

**NOTE:** Since there are so many per-project options that are identical between all projects they are not all shown when running `--help`. To see the full list of options that can be specified, run `cheribuild.py --help-all`. Since this will generate lots of output it probably makes more sense to run `cheribuild.py --help-all | grep <target_name>`.

```
usage: cheribuild.py [-h] [--config-file FILE] [--help-all] [--pretend] [--build] [--test] [--benchmark]
                     [--build-and-test] [--list-targets] [--print-chosen-targets] [--dump-configuration]
                     [--print-targets-only] [--clang-path CLANG_PATH] [--clang++-path CLANG++_PATH]
                     [--clang-cpp-path CLANG_CPP_PATH] [--pass-k-to-make] [--with-libstatcounters] [--skip-buildworld]
                     [--freebsd-subdir SUBDIRS] [--install-subdir-to-sysroot] [--buildenv] [--libcompat-buildenv]
                     [--debug-output] [--mips-float-abi {soft,hard}] [--cross-compile-linkage {dynamic,static}]
                     [--subobject-bounds {conservative,subobject-safe,aggressive,very-aggressive,everywhere-unsafe}]
                     [--no-subobject-debug] [--no-clang-colour-diags] [--use-sdk-clang-for-native-xbuild]
                     [--configure-only] [--skip-install] [--skip-build] [--skip-sdk] [--trap-on-unrepresentable]
                     [--qemu-gdb-break-on-cheri-trap]
                     [--qemu-gdb-debug-userspace-program QEMU_GDB_DEBUG_USERSPACE_PROGRAM] [--docker]
                     [--docker-container DOCKER_CONTAINER] [--docker-reuse-container] [--compilation-db]
                     [--wait-for-debugger] [--run-under-gdb] [--test-ssh-key TEST_SSH_KEY]
                     [--no-run-mips-tests-with-cheri-image] [--use-minimal-benchmark-kernel] [--test-extra-args ARGS]
                     [--interact-after-tests] [--test-environment-only] [--test-ld-preload TEST_LD_PRELOAD]
                     [--benchmark-fpga-extra-args ARGS] [--benchmark-clean-boot] [--benchmark-extra-args ARGS]
                     [--benchmark-ssh-host BENCHMARK_SSH_HOST] [--benchmark-csv-suffix BENCHMARK_CSV_SUFFIX]
                     [--benchmark-ld-preload BENCHMARK_LD_PRELOAD] [--benchmark-with-debug-kernel]
                     [--benchmark-lazy-binding] [--benchmark-iterations BENCHMARK_ITERATIONS] [--benchmark-with-qemu]
                     [--no-shallow-clone] [--get-config-option KEY] [--quiet] [--verbose] [--clean] [--force]
                     [--logfile] [--skip-update] [--force-update  --skip-configure | --reconfigure]
                     [--include-dependencies]
                     [--compilation-db-in-source-dir  --cross-compile-for-mips | --cross-compile-for-host]
                     [--make-without-nice] [--make-jobs MAKE_JOBS] [--source-root SOURCE_ROOT]
                     [--output-root OUTPUT_ROOT] [--build-root BUILD_ROOT] [--freebsd-universe/build-options OPTIONS]
                     [--freebsd-universe/minimal] [--freebsd-universe/build-tests] [--cheribsd/build-options OPTIONS]
                     [--cheribsd/minimal] [--cheribsd/build-tests] [--cheribsd/subdir SUBDIRS]
                     [--cheribsd/kernel-config CONFIG] [--cheribsd/no-debug-info] [--cheribsd/no-auto-obj]
                     [--cheribsd/sysroot-only] [--cheribsd/build-fpga-kernels] [--cheribsd/pure-cap-kernel]
                     [--cheribsd-mfs-root-kernel/no-build-fpga-kernels] [--cheribsd-sysroot/remote-sdk-path PATH]
                     [--qemu/gui] [--qemu/targets QEMU/TARGETS] [--qemu/unaligned] [--qemu/statistics]
                     [--cherios-qemu/gui] [--cherios-qemu/targets CHERIOS_QEMU/TARGETS] [--cherios-qemu/unaligned]
                     [--cherios-qemu/statistics] [--disk-image-minimal/extra-files DIR]
                     [--disk-image-minimal/hostname HOSTNAME] [--disk-image-minimal/remote-path PATH]
                     [--disk-image-minimal/path IMGPATH] [--disk-image/extra-files DIR] [--disk-image/hostname HOSTNAME]
                     [--disk-image/remote-path PATH] [--disk-image/path IMGPATH] [--disk-image-freebsd/extra-files DIR]
                     [--disk-image-freebsd/hostname HOSTNAME] [--disk-image-freebsd/remote-path PATH]
                     [--disk-image-freebsd/path IMGPATH] [--run/monitor-over-telnet PORT]
                     [--run/ssh-forwarding-port PORT] [--run/remote-kernel-path RUN/REMOTE_KERNEL_PATH]
                     [--run/skip-kernel-update] [--run-minimal/monitor-over-telnet PORT]
                     [--run-minimal/ssh-forwarding-port PORT]
                     [--run-minimal/remote-kernel-path RUN_MINIMAL/REMOTE_KERNEL_PATH]
                     [--run-minimal/skip-kernel-update] [--sail-cheri-mips/trace-support] [--sail-riscv/trace-support]
                     [--sail-cheri-riscv/trace-support] [--cheri-syzkaller/run-sysgen]
                     [--run-syzkaller/syz-config RUN_SYZKALLER/SYZ_CONFIG]
                     [--run-syzkaller/ssh-privkey syzkaller_id_rsa] [--run-syzkaller/workdir DIR]
                     [--go/bootstrap-toolchain GO/BOOTSTRAP_TOOLCHAIN] [--qtwebkit/build-jsc-only]
                     [TARGET [TARGET ...]]

positional arguments:
  TARGET                The targets to build

optional arguments:
  -h, --help            show this help message and exit
  --help-all, --help-hidden
                        Show all help options, including the target-specific ones.
  --pretend, -p         Only print the commands instead of running them (default: 'False')
  --pass-k-to-make, -k  Pass the -k flag to make to continue after the first error (default: 'False')
  --debug-output, -vv   Extremely verbose output (default: 'False')
  --no-clang-colour-diags
                        Do not force CHERI clang to emit coloured diagnostics
  --configure-only      Only run the configure step (skip build and install) (default: 'False')
  --skip-install        Skip the install step (only do the build) (default: 'False')
  --skip-build          Skip the build step (only do the install) (default: 'False')
  --skip-sdk            When building with --include-dependencies ignore the CHERI sdk dependencies. Saves a lot of time
                        when building libc++, etc. with dependencies but the sdk is already up-to-date (default:
                        'False')
  --trap-on-unrepresentable
                        Raise a CHERI exception when capabilities become unreprestable instead of detagging. Useful for
                        debugging, but deviates from the spec, and therefore off by default. (default: 'False')
  --qemu-gdb-break-on-cheri-trap
                        Drop into GDB attached to QEMU when a CHERI exception is triggered (QEMU only). (default:
                        'False')
  --qemu-gdb-debug-userspace-program QEMU_GDB_DEBUG_USERSPACE_PROGRAM
                        Print the command to debug the following userspace program in GDB attaced to QEMU
  --compilation-db, --cdb
                        Create a compile_commands.json file in the build dir (requires Bear for non-CMake projects)
                        (default: 'False')
  --no-shallow-clone    Do not perform a shallow `git clone` when cloning new projects. This can save a lot of time for
                        largerepositories such as FreeBSD or LLVM. Use `git fetch --unshallow` to convert to a non-
                        shallow clone
  --quiet, -q           Don't show stdout of the commands that are executed (default: 'False')
  --verbose, -v         Print all commmands that are executed (default: 'False')
  --clean, -c           Remove the build directory before build (default: 'False')
  --force, -f           Don't prompt for user input but use the default action (default: 'False')
  --logfile             Don't write a logfile for the build steps (default: 'False')
  --skip-update         Skip the git pull step (default: 'False')
  --force-update        Always update (with autostash) even if there are uncommitted changes (default: 'False')
  --skip-configure      Skip the configure step (default: 'False')
  --reconfigure, --force-configure
                        Always run the configure step, even for CMake projects with a valid cache. (default: 'False')
  --include-dependencies, -d
                        Also build the dependencies of targets passed on the command line. Targets passed on thecommand
                        line will be reordered and processed in an order that ensures dependencies are built before the
                        real target. (run with --list-targets for more information) (default: 'False')
  --compilation-db-in-source-dir
                        Generate a compile_commands.json and also copy it to the source directory (default: 'False')
  --make-without-nice   Run make/ninja without nice(1) (default: 'False')
  --make-jobs MAKE_JOBS, -j MAKE_JOBS
                        Number of jobs to use for compiling (default: '8')

Actions to be performed:
  --build               Run (usually build+install) chosen targets (default)
  --test, --run-tests   Run tests for the passed targets instead of building them
  --benchmark           Run tests for the passed targets instead of building them
  --build-and-test      Run chosen targets and then run any tests afterwards
  --list-targets        List all available targets and exit
  --print-chosen-targets
                        List all the targets that would be built
  --dump-configuration  Print the current configuration as JSON. This can be saved to ~/.config/cheribuild.json to make
                        it persistent
  --print-targets-only  Don't run the build but instead only print the targets that would be executed (default: 'False')
  --get-config-option KEY
                        Print the value of config option KEY and exit

Configuration of default paths:
  --config-file FILE    The config file that is used to load the default settings (default:
                        '/Users/alex/.config/cheribuild.json')
  --clang-path CLANG_PATH, --cc-path CLANG_PATH
                        The C compiler to use for host binaries (must be compatible with Clang >= 3.7) (default:
                        '/usr/bin/cc')
  --clang++-path CLANG++_PATH, --c++-path CLANG++_PATH
                        The C++ compiler to use for host binaries (must be compatible with Clang >= 3.7) (default:
                        '/usr/bin/c++')
  --clang-cpp-path CLANG_CPP_PATH, --cpp-path CLANG_CPP_PATH
                        The C preprocessor to use for host binaries (must be compatible with Clang >= 3.7) (default:
                        '/usr/bin/cpp')
  --source-root SOURCE_ROOT
                        The directory to store all sources (default: '/Users/alex/cheri')
  --output-root OUTPUT_ROOT
                        The directory to store all output (default: '<SOURCE_ROOT>/output')
  --build-root BUILD_ROOT
                        The directory for all the builds (default: '<SOURCE_ROOT>/build')

Adjust flags used when compiling MIPS/CHERI projects:
  --with-libstatcounters
                        Link cross compiled CHERI project with libstatcounters. (default: 'False')
  --mips-float-abi {soft,hard}
                        The floating point ABI to use for building MIPS+CHERI programs (default: 'soft')
  --cross-compile-linkage {dynamic,static}
                        Whether to link cross-compile projects static or dynamic by default (default: 'dynamic')
  --subobject-bounds {conservative,subobject-safe,aggressive,very-aggressive,everywhere-unsafe}
                        Whether to add additional CSetBounds to subobject references/&-operator
  --no-subobject-debug  Do not clear software permission bit 2 when subobject bounds reduced size (Note: this should be
                        turned off for benchmarks!)
  --use-sdk-clang-for-native-xbuild
                        Compile cross-compile project with CHERI clang from the SDK instead of host compiler (default:
                        'False')

Configuration for running tests:
  --test-ssh-key TEST_SSH_KEY
                        The SSH key to used to connect to the QEMU instance when running tests on CheriBSD (default:
                        '/Users/alex/.ssh/id_ed25519.pub')
  --no-run-mips-tests-with-cheri-image
                        Do not use a CHERI kernel+image to run plain MIPS CheriBSD tests. This only affects the --test
                        option
  --use-minimal-benchmark-kernel
                        Use a CHERI BENCHMARK version of the cheribsd-mfs-root-kernel (without INVARIATES) for the run-
                        minimal target and for tests. This can speed up longer running tests. This is the default for
                        PostgreSQL and libc++ tests (passing use-minimal-benchmark-kernel can force these tests to use
                        an INVARIANTS kernel). (default: 'False')
  --test-extra-args ARGS
                        Additional flags to pass to the test script in --test
  --interact-after-tests
                        Interact with the CheriBSD instance after running the tests on QEMU (only for --test) (default:
                        'False')
  --test-environment-only
                        Don't actually run the tests. Instead setup a QEMU instance with the right paths set up.
                        (default: 'False')
  --test-ld-preload TEST_LD_PRELOAD
                        Preload the given library before running tests

Configuration for running benchmarks:
  --benchmark-fpga-extra-args ARGS
                        Extra options for beri-fpga-bsd-boot.py
  --benchmark-clean-boot
                        Reboot the FPGA with a new bitfile and kernel before running benchmarks. If not set, assume the
                        FPGA is running. (default: 'False')
  --benchmark-extra-args ARGS
                        Additional flags to pass to the beri-fpga-bsd-boot.py script in --benchmark
  --benchmark-ssh-host BENCHMARK_SSH_HOST
                        The SSH hostname/IP for the benchmark FPGA (default: 'cheri-fpga')
  --benchmark-csv-suffix BENCHMARK_CSV_SUFFIX
                        Add a custom suffix for the statcounters CSV.
  --benchmark-ld-preload BENCHMARK_LD_PRELOAD
                        Preload the given library before running benchmarks
  --benchmark-with-debug-kernel
                        Run the benchmark with a kernel that has assertions enabled. (default: 'False')
  --benchmark-lazy-binding
                        Run the benchmark without setting LD_BIND_NOW. (default: 'False')
  --benchmark-iterations BENCHMARK_ITERATIONS
                        Override the number of iterations for the benchmark. Note: not all benchmarks support this
                        option
  --benchmark-with-qemu
                        Run the benchmarks on QEMU instead of the FPGA (only useful to collect instruction counts or
                        test the benchmarks) (default: 'False')

Configuration for launching QEMU (and other simulators):
  --wait-for-debugger   Start QEMU in the 'wait for a debugger' state whenlaunching CheriBSD,FreeBSD, etc. (default:
                        'False')
  --run-under-gdb       Run tests/benchmarks under GDB. Note: currently most targets ignore this flag. (default:
                        'False')

FreeBSD and CheriBSD build configuration:
  --skip-buildworld, --skip-world
                        Skip the buildworld step when building FreeBSD or CheriBSD (default: 'False')
  --freebsd-subdir SUBDIRS, --subdir SUBDIRS
                        Only build subdirs SUBDIRS of FreeBSD/CheriBSD instead of the full tree. Useful for quickly
                        rebuilding an individual programs/libraries. If more than one dir is passed they will be
                        processed in order. Note: This will break if not all dependencies have been built.
  --install-subdir-to-sysroot
                        When using the --subdir option for CheriBSD targets also install the built libraries into the
                        sysroot. This can also be achived by running the cheribsd-sysroot target afterwards but is
                        faster. (default: 'False')
  --buildenv            Open a shell with the right environment for building the project. Currently only works for
                        FreeBSD/CheriBSD (default: 'False')
  --libcompat-buildenv, --libcheri-buildenv
                        Open a shell with the right environment for building compat libraries. (default: 'False')

Options controlling the use of docker for building:
  --docker              Run the build inside a docker container (default: 'False')
  --docker-container DOCKER_CONTAINER
                        Name of the docker container to use (default: 'cheribuild-test')
  --docker-reuse-container
                        Attach to the same container again (note: docker-container option must be an id rather than a
                        container name (default: 'False')

Options for target 'freebsd-universe':
  --freebsd-universe/build-options OPTIONS
                        Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf`
                        for more info. (default: '[]')
  --freebsd-universe/minimal
                        Don't build all of FreeBSD, just what is needed for running most CHERI tests/benchmarks
                        (default: 'False')
  --freebsd-universe/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')

Options for target 'cheribsd':
  --cheribsd/build-options OPTIONS
                        Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf`
                        for more info. (default: '[]')
  --cheribsd/minimal    Don't build all of FreeBSD, just what is needed for running most CHERI tests/benchmarks
                        (default: 'False')
  --cheribsd/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')
  --cheribsd/subdir SUBDIRS
                        Only build subdirs SUBDIRS instead of the full tree. Useful for quickly rebuilding an individual
                        programs/libraries. If more than one dir is passed, they will be processed in order. Note: This
                        will break if not all dependencies have been built. (default: 'the value of the global
                        --freebsd-subdir options')
  --cheribsd/kernel-config CONFIG
                        The kernel configuration to use for `make buildkernel` (default: 'target-dependent default')
  --cheribsd/no-debug-info
                        Do not pass make flags for building with debug info
  --cheribsd/no-auto-obj
                        Do not use -DWITH_AUTO_OBJ (experimental)
  --cheribsd/sysroot-only
                        Only build a sysroot instead of the full system. This will only build the libraries and skip all
                        binaries (default: 'False')
  --cheribsd/build-fpga-kernels
                        Also build kernels for the FPGA. (default: 'False')
  --cheribsd/pure-cap-kernel
                        Build kernel with pure capability ABI (probably won't work!) (default: 'False')

Options for target 'cheribsd-mfs-root-kernel':
  --cheribsd-mfs-root-kernel/no-build-fpga-kernels
                        Do not also build kernels for the FPGA.

Options for target 'cheribsd-sysroot':
  --cheribsd-sysroot/remote-sdk-path PATH
                        The path to the CHERI SDK on the remote FreeBSD machine (e.g. vica:~foo/cheri/output/sdk)

Options for target 'qemu':
  --qemu/gui            Build a the graphical UI bits for QEMU (SDL,VNC) (default: 'False')
  --qemu/targets QEMU/TARGETS
                        Build QEMU for the following targets (default: 'cheri128-softmmu,cheri128magic-
                        softmmu,mips64-softmmu,riscv64-softmmu,riscv64cheri-softmmu,riscv32-softmmu')
  --qemu/unaligned      Permit un-aligned loads/stores (default: 'False')
  --qemu/statistics     Collect statistics on out-of-bounds capability creation. (default: 'False')

Options for target 'cherios-qemu':
  --cherios-qemu/gui    Build a the graphical UI bits for QEMU (SDL,VNC) (default: 'False')
  --cherios-qemu/targets CHERIOS_QEMU/TARGETS
                        Build QEMU for the following targets (default: 'cheri128-softmmu,cheri128magic-
                        softmmu,mips64-softmmu,riscv64-softmmu,riscv64cheri-softmmu,riscv32-softmmu')
  --cherios-qemu/unaligned
                        Permit un-aligned loads/stores (default: 'False')
  --cherios-qemu/statistics
                        Collect statistics on out-of-bounds capability creation. (default: 'False')

Options for target 'disk-image-minimal':
  --disk-image-minimal/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files-minimal')
  --disk-image-minimal/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-cheri${ABI}-alex')
  --disk-image-minimal/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image-minimal/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/minimal-<TARGET>-disk.img
                        depending on architecture')

Options for target 'disk-image':
  --disk-image/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-cheri${ABI}-alex')
  --disk-image/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/$arch_prefix-disk.img.')

Options for target 'disk-image-freebsd':
  --disk-image-freebsd/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image-freebsd/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-invalid-alex')
  --disk-image-freebsd/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image-freebsd/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/freebsd-$SUFFIX.img')

Options for target 'run':
  --run/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C
  --run/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` connect to the VM (default: '19500')
  --run/remote-kernel-path RUN/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'run-minimal':
  --run-minimal/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C
  --run-minimal/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` connect to the VM (default: '19519')
  --run-minimal/remote-kernel-path RUN_MINIMAL/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run-minimal/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'sail-cheri-mips':
  --sail-cheri-mips/trace-support
                        Build sail-cheri-mips simulators with tracing support (they will be slow but the traces are
                        useful to debug failing tests) (default: 'False')

Options for target 'sail-riscv':
  --sail-riscv/trace-support
                        Build sail-cheri-mips simulators with tracing support (they will be slow butthe traces are
                        useful to debug failing tests) (default: 'False')

Options for target 'sail-cheri-riscv':
  --sail-cheri-riscv/trace-support
                        Build sail-cheri-mips simulators with tracing support (they will be slow but the traces are
                        useful to debug failing tests) (default: 'False')

Options for target 'cheri-syzkaller':
  --cheri-syzkaller/run-sysgen
                        Rerun syz-extract and syz-sysgen to rebuild generated Go syscall descriptions. (default:
                        'False')

Options for target 'run-syzkaller':
  --run-syzkaller/syz-config RUN_SYZKALLER/SYZ_CONFIG
                        Path to the syzkaller configuration file to use.
  --run-syzkaller/ssh-privkey syzkaller_id_rsa
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files/syzkaller_id_rsa')
  --run-syzkaller/workdir DIR
                        Working directory for syzkaller output.

Options for target 'go':
  --go/bootstrap-toolchain GO/BOOTSTRAP_TOOLCHAIN
                        Path to alternate go bootstrap toolchain.

Options for target 'qtwebkit':
  --qtwebkit/build-jsc-only
                        only build the JavaScript interpreter executable (default: 'False')

```
