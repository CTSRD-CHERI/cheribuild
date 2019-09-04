# `cheribuild.py` - A script to build CHERI-related software (**Requires Python 3.5+**)

This script automates all the steps required to build various [CHERI](http://www.chericpu.com)-related software.
For example `cheribuild.py [options] sdk` will create a SDK that can be
used to compile software for the CHERI CPU and `cheribuild.py [options] run`
will start an instance of [CheriBSD](https://github.com/CTSRD-CHERI/cheribsd) in [QEMU](https://github.com/CTSRD-CHERI/qemu).

It has been tested and should work on FreeBSD 10, 11 and 12.
On Linux Ubuntu 16.04 and OpenSUSE Tubleweed are supported. Ubuntu 14.04 may also work but is no longer tested.
MacOS 10.13 is also supported.

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
It is now possible to target both 128 and 256-bit CHERI using the same clang binary by specifying
`-mcpu=cheri128` or `-mcpu=256`. However, if you use cheribuild.py for building you won't have to care
about this since the `--128` (the default) or `--256` flag for cheribuild.py will ensure the right
flags are passed.

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
  "cheri-bits": 128,
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
usage: cheribuild.py [-h] [--config-file FILE] [--help-all] [--pretend]
                     [--build] [--test] [--build-and-test] [--list-targets]
                     [--print-chosen-targets] [--dump-configuration]
                     [--print-targets-only] [--clang-path CLANG_PATH]
                     [--clang++-path CLANG++_PATH] [--pass-k-to-make]
                     [--with-libstatcounters] [--skip-buildworld]
                     [--freebsd-subdir SUBDIRS] [--buildenv]
                     [--libcheri-buildenv] [--mips-float-abi {soft,hard}]
                     [--cross-compile-linkage {dynamic,static}]
                     [--no-unified-sdk] [--no-clang-colour-diags]
                     [--use-sdk-clang-for-native-xbuild] [--configure-only]
                     [--skip-install] [--skip-sdk] [--docker]
                     [--docker-container DOCKER_CONTAINER]
                     [--docker-reuse-container] [--compilation-db]
                     [--test-ssh-key TEST_SSH_KEY] [--get-config-option KEY]
                     [--quiet] [--verbose] [--clean] [--force] [--no-logfile]
                     [--skip-update] [--force-update] --skip-configure |
                     --reconfigure] [--include-dependencies] --cheri-128 |
                     --cheri-256 | --cheri-bits {128,256}]
                     [--compilation-db-in-source-dir] --cross-compile-for-mips |
                     --cross-compile-for-host] [--make-without-nice]
                     [--make-jobs MAKE_JOBS] [--source-root SOURCE_ROOT]
                     [--output-root OUTPUT_ROOT] [--build-root BUILD_ROOT]
                     [--freebsd-universe/build-options OPTIONS]
                     [--freebsd-universe/minimal]
                     [--freebsd-universe/build-tests]
                     [--cheribsd/build-options OPTIONS] [--cheribsd/minimal]
                     [--cheribsd/build-tests] [--cheribsd/subdir SUBDIRS]
                     [--cheribsd/compile-with-cheribuild-upstream-llvm]
                     [--cheribsd/no-use-external-toolchain-for-kernel]
                     [--cheribsd/no-use-external-toolchain-for-world]
                     [--cheribsd/no-debug-info] [--cheribsd/no-auto-obj]
                     [--cheribsd/kernel-config CONFIG] [--cheribsd/sysroot-only]
                     [--cheribsd/build-fpga-kernels]
                     [--cheribsd/pure-cap-kernel]
                     [--cheribsd-purecap/build-options OPTIONS]
                     [--cheribsd-purecap/minimal]
                     [--cheribsd-purecap/build-tests]
                     [--cheribsd-purecap/subdir SUBDIRS]
                     [--cheribsd-purecap/compile-with-cheribuild-upstream-llvm]
                     [--cheribsd-purecap/no-use-external-toolchain-for-kernel]
                     [--cheribsd-purecap/no-use-external-toolchain-for-world]
                     [--cheribsd-purecap/no-debug-info]
                     [--cheribsd-purecap/no-auto-obj]
                     [--cheribsd-purecap/kernel-config CONFIG]
                     [--cheribsd-purecap/sysroot-only]
                     [--cheribsd-purecap/build-fpga-kernels]
                     [--cheribsd-purecap/pure-cap-kernel] [--qemu/no-unaligned]
                     [--qemu/statistics] [--qemu/gui] [--qemu/no-use-lto]
                     [--disk-image-minimal/extra-files DIR]
                     [--disk-image-minimal/hostname HOSTNAME]
                     [--disk-image-minimal/path IMGPATH]
                     [--disk-image/extra-files DIR]
                     [--disk-image/hostname HOSTNAME]
                     [--disk-image/path IMGPATH]
                     [--disk-image-purecap/extra-files DIR]
                     [--disk-image-purecap/hostname HOSTNAME]
                     [--disk-image-purecap/path IMGPATH]
                     [--run/monitor-over-telnet PORT]
                     [--run/ssh-forwarding-port PORT]
                     [--run-purecap/monitor-over-telnet PORT]
                     [--run-purecap/ssh-forwarding-port PORT]
                     [--run-minimal/monitor-over-telnet PORT]
                     [--run-minimal/ssh-forwarding-port PORT]
                     [--sail-cheri-mips/trace-support]
                     [--qtwebkit/build-jsc-only]
                     [TARGET [TARGET ...]]

positional arguments:
  TARGET                The targets to build

optional arguments:
  -h, --help            show this help message and exit
  --help-all, --help-hidden
                        Show all help options, including the target-specific
                        ones.
  --pretend, -p         Only print the commands instead of running them
                        (default: 'False')
  --pass-k-to-make, -k  Pass the -k flag to make to continue after the first
                        error (default: 'False')
  --no-unified-sdk      Do not build a single SDK instead of separate 128 and
                        256 bits ones
  --no-clang-colour-diags
                        Do not force CHERI clang to emit coloured diagnostics
  --configure-only      Only run the configure step (skip build and install)
                        (default: 'False')
  --skip-install        Skip the install step (only do the build) (default:
                        'False')
  --skip-sdk            When building with --include-dependencies ignore the
                        CHERI sdk dependencies. Saves a lot of time when
                        building libc++, etc. with dependencies but the sdk is
                        already up-to-date (default: 'False')
  --compilation-db, --cdb
                        Create a compile_commands.json file in the build dir
                        (requires Bear for non-CMake projects) (default:
                        'False')
  --quiet, -q           Don't show stdout of the commands that are executed
                        (default: 'False')
  --verbose, -v         Print all commmands that are executed (default: 'False')
  --clean, -c           Remove the build directory before build (default:
                        'False')
  --force, -f           Don't prompt for user input but use the default action
                        (default: 'False')
  --no-logfile          Don't write a logfile for the build steps (default:
                        'False')
  --skip-update         Skip the git pull step (default: 'False')
  --force-update        Always update (with autostash) even if there are
                        uncommitted changes (default: 'False')
  --skip-configure      Skip the configure step (default: 'False')
  --reconfigure, --force-configure
                        Always run the configure step, even for CMake projects
                        with a valid cache. (default: 'False')
  --include-dependencies, -d
                        Also build the dependencies of targets passed on the
                        command line. Targets passed on thecommand line will be
                        reordered and processed in an order that ensures
                        dependencies are built before the real target. (run with
                        --list-targets for more information) (default: 'False')
  --cheri-128, --128    Shortcut for --cheri-bits=128
  --cheri-256, --256    Shortcut for --cheri-bits=256
  --cheri-bits {128,256}
                        Whether to build the whole software stack for 128 or 256
                        bit CHERI. The output directories will be suffixed with
                        the number of bits to make sure the right binaries are
                        being used. (default: '128')
  --compilation-db-in-source-dir
                        Generate a compile_commands.json and also copy it to the
                        source directory (default: 'False')
  --cross-compile-for-mips, --xmips
                        Make cross compile projects target MIPS hybrid ABI
                        instead of CheriABI (default: 'False')
  --cross-compile-for-host, --xhost
                        Make cross compile projects target the host system and
                        use cheri clang to compile (tests that we didn't break
                        x86) (default: 'False')
  --make-without-nice   Run make/ninja without nice(1) (default: 'False')
  --make-jobs MAKE_JOBS, -j MAKE_JOBS
                        Number of jobs to use for compiling (default: '8')

Actions to be performed:
  --build               Run (usually build+install) chosen targets (default)
  --test, --run-tests   Run tests for the passed targets instead of building
                        them
  --build-and-test      Run chosen targets and then run any tests afterwards
  --list-targets        List all available targets and exit
  --print-chosen-targets
                        List all the targets that would be built
  --dump-configuration  Print the current configuration as JSON. This can be
                        saved to ~/.config/cheribuild.json to make it persistent
  --print-targets-only  Don't run the build but instead only print the targets
                        that would be executed (default: 'False')
  --get-config-option KEY
                        Print the value of config option KEY and exit

Configuration of default paths:
  --config-file FILE    The config file that is used to load the default
                        settings (default: '~/.config/cheribuild.json')
  --clang-path CLANG_PATH
                        The Clang C compiler to use for compiling LLVM+Clang
                        (must be at least version 3.7) (default:
                        '/usr/bin/clang')
  --clang++-path CLANG++_PATH
                        The Clang C++ compiler to use for compiling LLVM+Clang
                        (must be at least version 3.7) (default:
                        '/usr/bin/clang++')
  --source-root SOURCE_ROOT
                        The directory to store all sources (default:
                        '~/cheri')
  --output-root OUTPUT_ROOT
                        The directory to store all output (default:
                        '<SOURCE_ROOT>/output')
  --build-root BUILD_ROOT
                        The directory for all the builds (default:
                        '<SOURCE_ROOT>/build')

Adjust flags used when compiling MIPS/CHERI projects:
  --with-libstatcounters
                        Link cross compiled CHERI project with libstatcounters.
                        This is only useful when targetting FPGA (default:
                        'False')
  --mips-float-abi {soft,hard}
                        The floating point ABI to use for building MIPS+CHERI
                        programs (default: 'soft')
  --cross-compile-linkage {dynamic,static}
                        Whether to link cross-compile projects static or dynamic
                        by default (default: 'dynamic')
  --use-sdk-clang-for-native-xbuild
                        Compile cross-compile project with CHERI clang from the
                        SDK instead of host compiler (default: 'False')

Configuration for running tests:
  --test-ssh-key TEST_SSH_KEY
                        The SSH key to used to connect to the QEMU instance when
                        running tests on CheriBSD (default:
                        '~/.ssh/id_ed25519.pub')

FreeBSD and CheriBSD build configuration:
  --skip-buildworld     Skip the buildworld step when building FreeBSD or
                        CheriBSD (default: 'False')
  --freebsd-subdir SUBDIRS
                        Only build subdirs SUBDIRS of FreeBSD/CheriBSD instead
                        of the full tree. Useful for quickly rebuilding an
                        individual programs/libraries. If more than one dir is
                        passed they will be processed in order. Note: This will
                        break if not all dependencies have been built.
  --buildenv            Open a shell with the right environment for building the
                        project. Currently only works for FreeBSD/CheriBSD
                        (default: 'False')
  --libcheri-buildenv   Open a shell with the right environment for building
                        CHERI libraries. Currently only works for CheriBSD
                        (default: 'False')

Options controlling the use of docker for building:
  --docker              Run the build inside a docker container (default:
                        'False')
  --docker-container DOCKER_CONTAINER
                        Name of the docker container to use (default:
                        'cheribuild-test')
  --docker-reuse-container
                        Attach to the same container again (note: docker-
                        container option must be an id rather than a container
                        name (default: 'False')

Options for target 'freebsd-universe':
  --freebsd-universe/build-options OPTIONS
                        Additional make options to be passed to make when
                        building FreeBSD/CheriBSD. See `man src.conf` for more
                        info. (default: '[]')
  --freebsd-universe/minimal
                        Don't build all of FreeBSD, just what is needed for
                        running most CHERI tests/benchmarks (default: 'False')
  --freebsd-universe/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')

Options for target 'cheribsd':
  --cheribsd/build-options OPTIONS
                        Additional make options to be passed to make when
                        building FreeBSD/CheriBSD. See `man src.conf` for more
                        info. (default: '[]')
  --cheribsd/minimal    Don't build all of FreeBSD, just what is needed for
                        running most CHERI tests/benchmarks (default: 'False')
  --cheribsd/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')
  --cheribsd/subdir SUBDIRS
                        Only build subdirs SUBDIRS instead of the full tree.
                        Useful for quickly rebuilding an individual
                        programs/libraries. If more than one dir is passed they
                        will be processed in order. Note: This will break if not
                        all dependencies have been built. (default: 'the value
                        of the global --freebsd-subdir options')
  --cheribsd/compile-with-cheribuild-upstream-llvm
                        Compile with the Clang version built by the
                        `cheribuild.py upstream-llvm` target (default: 'False')
  --cheribsd/no-use-external-toolchain-for-kernel
                        Do not build the kernel with the external toolchain
  --cheribsd/no-use-external-toolchain-for-world
                        Do not build world with the external toolchain
  --cheribsd/no-debug-info
                        Do not pass make flags for building with debug info
  --cheribsd/no-auto-obj
                        Do not use -DWITH_AUTO_OBJ (experimental)
  --cheribsd/kernel-config CONFIG
                        The kernel configuration to use for `make buildkernel`
                        (default: CHERI_MALTA64 or CHERI128_MALTA64 depending on
                        --cheri-bits)
  --cheribsd/sysroot-only
                        Only build a sysroot instead of the full system. This
                        will only build the libraries and skip all binaries
                        (default: 'False')
  --cheribsd/build-fpga-kernels
                        Also build kernels for the FPGA. (default: 'False')
  --cheribsd/pure-cap-kernel
                        Build kernel with pure capability ABI (probably won't
                        work!) (default: 'False')

Options for target 'cheribsd-purecap':
  --cheribsd-purecap/build-options OPTIONS
                        Additional make options to be passed to make when
                        building FreeBSD/CheriBSD. See `man src.conf` for more
                        info. (default: '[]')
  --cheribsd-purecap/minimal
                        Don't build all of FreeBSD, just what is needed for
                        running most CHERI tests/benchmarks (default: 'False')
  --cheribsd-purecap/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')
  --cheribsd-purecap/subdir SUBDIRS
                        Only build subdirs SUBDIRS instead of the full tree.
                        Useful for quickly rebuilding an individual
                        programs/libraries. If more than one dir is passed they
                        will be processed in order. Note: This will break if not
                        all dependencies have been built. (default: 'the value
                        of the global --freebsd-subdir options')
  --cheribsd-purecap/compile-with-cheribuild-upstream-llvm
                        Compile with the Clang version built by the
                        `cheribuild.py upstream-llvm` target (default: 'False')
  --cheribsd-purecap/no-use-external-toolchain-for-kernel
                        Do not build the kernel with the external toolchain
  --cheribsd-purecap/no-use-external-toolchain-for-world
                        Do not build world with the external toolchain
  --cheribsd-purecap/no-debug-info
                        Do not pass make flags for building with debug info
  --cheribsd-purecap/no-auto-obj
                        Do not use -DWITH_AUTO_OBJ (experimental)
  --cheribsd-purecap/kernel-config CONFIG
                        The kernel configuration to use for `make buildkernel`
                        (default: CHERI_MALTA64 or CHERI128_MALTA64 depending on
                        --cheri-bits)
  --cheribsd-purecap/sysroot-only
                        Only build a sysroot instead of the full system. This
                        will only build the libraries and skip all binaries
                        (default: 'False')
  --cheribsd-purecap/build-fpga-kernels
                        Also build kernels for the FPGA. (default: 'False')
  --cheribsd-purecap/pure-cap-kernel
                        Build kernel with pure capability ABI (probably won't
                        work!) (default: 'False')

Options for target 'qemu':
  --qemu/no-unaligned   Do not permit un-aligned loads/stores
  --qemu/statistics     Collect statistics on out-of-bounds capability creation.
                        (default: 'False')
  --qemu/gui            Build a the graphical UI bits for QEMU (SDL,VNC)
                        (default: 'False')
  --qemu/no-use-lto     Do not try to build QEMU with link-time optimization if
                        possible

Options for target 'disk-image-minimal':
  --disk-image-minimal/extra-files DIR
                        A directory with additional files that will be added to
                        the image (default: '$SOURCE_ROOT/extra-files-minimal')
  --disk-image-minimal/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-
                        cheri${CHERI_BITS}-def')
  --disk-image-minimal/path IMGPATH
                        The output path for the QEMU disk image (default:
                        '$OUTPUT_ROOT/minimal-cheri256-disk.img or
                        $OUTPUT_ROOT/minimal-cheri128-disk.img depending on
                        --cheri-bits.')

Options for target 'disk-image':
  --disk-image/extra-files DIR, --extra-files DIR
                        A directory with additional files that will be added to
                        the image (default: '$SOURCE_ROOT/extra-files')
  --disk-image/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-
                        cheri${CHERI_BITS}-def')
  --disk-image/path IMGPATH, --disk-image-path IMGPATH
                        The output path for the QEMU disk image (default:
                        '$OUTPUT_ROOT/cheri256-disk.img or
                        $OUTPUT_ROOT/cheri128-disk.img depending on --cheri-
                        bits.')

Options for target 'disk-image-purecap':
  --disk-image-purecap/extra-files DIR
                        A directory with additional files that will be added to
                        the image (default: '$SOURCE_ROOT/extra-files')
  --disk-image-purecap/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-
                        purecap${CHERI_BITS}-def')
  --disk-image-purecap/path IMGPATH
                        The output path for the QEMU disk image (default:
                        '$OUTPUT_ROOT/purecap-cheri256-disk.img or
                        $OUTPUT_ROOT/purecap-cheri128-disk.img depending on
                        --cheri-bits.')

Options for target 'run':
  --run/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting
                        to localhostat $PORT via telnet instead of using
                        CTRL+A,C
  --run/ssh-forwarding-port PORT, --ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port.
                        You can then use `ssh root@localhost -p $PORT` connect
                        to the VM (default: '10000')

Options for target 'run-purecap':
  --run-purecap/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting
                        to localhostat $PORT via telnet instead of using
                        CTRL+A,C
  --run-purecap/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port.
                        You can then use `ssh root@localhost -p $PORT` connect
                        to the VM (default: '10001')

Options for target 'run-minimal':
  --run-minimal/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting
                        to localhostat $PORT via telnet instead of using
                        CTRL+A,C
  --run-minimal/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port.
                        You can then use `ssh root@localhost -p $PORT` connect
                        to the VM (default: '10008')

Options for target 'sail-cheri-mips':
  --sail-cheri-mips/trace-support
                        Build sail-cheri-mips simulators with tracing support
                        (they will be slow butthe traces are useful to debug
                        failing tests) (default: 'False')

Options for target 'qtwebkit':
  --qtwebkit/build-jsc-only
                        only build the JavaScript interpreter executable
                        (default: 'False')
```
