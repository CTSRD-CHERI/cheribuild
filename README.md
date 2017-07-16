# `cheribuild.py` - A script to build CHERI-related software (**Requires Python 3.4+**)

This script automates all the steps required to build various [CHERI](http://www.chericpu.com)-related software.
For example `cheribuild.py [options] run` will start an insanstance of [CHERIBSD](https://github.com/CTSRD-CHERI/cheribsd) on [QEMU](https://github.com/CTSRD-CHERI/qemu) and
`cheribuild.py [options] sdk` will create a SDK that can be used to compile software for the CHERI CPU.

It has been tested and should work on FreeBSD 10, 11 and 12.
On Linux Ubuntu 16.04 and OpenSUSE Tubleweed are supported. Ubuntu 14.04 should also work but is no longer tested.

**NOTE**: As this involves building CHERIBSD you will need to run this script on a FreeBSD system.
If you want to run this script on a remote FreeBSD host you can use the `remote-cheribuild.py` script that is included in this repository:

`remote-cheribuild.py my.freebsd.server [options] <targets...>` will run this script on `my.freebsd.server`

If you would like to see what the script will do run it with the `--pretend` or `-p` option.
For even more detail you can also pass `--verbose` or `-v`.

## Getting shell completion

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

## Adapting the build configuration
There are a lot of options to customize the behaviour of this script: e.g. the directory for
the cloned sources can be changed from the default of `$HOME/cheri` using the `--source-root=` option.
A full list of the available options with descriptions can be found [towards the end of this document](#full-list-of-options).

The options can also be made persistent by storing them in a JSON config file (`~/.config/cheribuild.json`).
Options passed on the command line will override those read from the config file.
The key in the JSON config file is the same as the long option name without the intial `--`.
For example if you want cheribuild.py to behave as if you had passed
`--source-root /sources/cheri --output-root /build/cheri --128 -t -j 4`, you can write the following JSON to
`~/.config/cheribuild.json`:

```json
{
  "source-root": "/sources/cheri",
  "output-root": "/build/cheri",
  "cheri-bits": 128,
  "skip-dependencies": true,
  "make-jobs": 4
}
```

## Available Targets

When selecting a target you can also build all the targets that it depends on by passing the `--include-dependencies` or `-d` option.
However, some targets (e.g. `all`, `sdk`) will always build their dependencies because running them without building the dependencies does not make sense (see the list of targets for details).

**TODO: Possibly restore the previous behaviour of dependencies included by default and opt out? It is probably be the more logical behaviour? I changed it because I often want to build only cheribsd without changing the compiler but forget to pass the `--skip-dependencies` flag**

#### The following main targets are available

- `binutils` builds and installs [CTSRD-CHERI/binutils](https://github.com/CTSRD-CHERI/binutils)
- `qemu` builds and installs [CTSRD-CHERI/qemu](https://github.com/CTSRD-CHERI/qemu)
- `llvm` builds and installs [CTSRD-CHERI/llvm](https://github.com/CTSRD-CHERI/llvm) and [CTSRD-CHERI/clang](https://github.com/CTSRD-CHERI/clang)
- `lld` builds and installs the [llvm-mirror/lld](https://github.com/llvm-mirror/lld) linker
- `cheribsd` builds and installs [CTSRD-CHERI/cheribsd](https://github.com/CTSRD-CHERI/cheribsd) (**NOTE:** Only works on FreeBSD systems)
- `disk-image` creates a CHERIBSD disk-image
- `run` launches QEMU with the CHERIBSD disk image
- `cheribsd-sysroot` creates a CheriBSD sysroot. When running this script on a non-FreeBSD system the files will need to be copied from a build server
- `freestanding-sdk` builds everything required to build and run `-ffreestanding` binaries: compiler, binutils and qemu
- `cheribsd-sdk` builds everything required to compile binaries for CheriBSD: `freestanding-sdk` and `cheribsd-sysroot`
- `sdk` is an alias for `cheribsd-sdk` when building on FreeBSD, otherwise builds `freestanding-sdk`
- `all`: runs all the targets listed so far (`run` comes last so you can then interact with QEMU)

#### Other targets
- `cmake` builds and installs latest [CMake](https://github.com/Kitware/CMake)
- `binutils` builds and installs [CTSRD-CHERI/binutils](https://github.com/CTSRD-CHERI/binutils)
- `brandelf` builds and installs `brandelf` from [elftoolchain](https://github.com/Richardson/elftoolchain/) (needed for SDK on non-FreeBSD systems)
- `awk` builds and installs BSD AWK (if you need it on Linux)
- `cherios` builds and installs [CTSRD-CHERI/cherios](https://github.com/CTSRD-CHERI/cherios)
- `cheritrace` builds and installs [CTSRD-CHERI/cheritrace](https://github.com/CTSRD-CHERI/cheritrace)
- `cherivis` builds and installs [CTSRD-CHERI/cherivis](https://github.com/CTSRD-CHERI/cherivis)

## Full list of options

```
usage: cheribuild.py [-h] [--config-file FILE] [--help-all] [--pretend] [--clang-path CLANG_PATH]
                     [--clang++-path CLANG++_PATH] [--pass-k-to-make] [--with-libstatcounters] [--configure-only]
                     [--skip-install] [--quiet] [--verbose] [--clean] [--force] [--no-logfile] [--skip-update]
                     --skip-configure | --reconfigure] [--list-targets] [--dump-configuration] [--get-config-option KEY]
                     [--include-dependencies] --cheri-128 | --cheri-256 | --cheri-bits {128,256}] [--compilation-db]
                     --cross-compile-for-mips | --cross-compile-for-host] [--make-without-nice] [--make-jobs MAKE_JOBS]
                     [--source-root SOURCE_ROOT] [--output-root OUTPUT_ROOT] [--build-root BUILD_ROOT]
                     [--freebsd-mips/subdir DIR] [--freebsd-mips/use-external-toolchain-for-kernel]
                     [--freebsd-mips/use-external-toolchain-for-world] [--freebsd-mips/no-debug-info]
                     [--freebsd-mips/build-tests] [--freebsd-mips/fast] [--cheribsd/subdir DIR]
                     [--cheribsd/no-use-external-toolchain-for-kernel] [--cheribsd/use-external-toolchain-for-world]
                     [--cheribsd/no-debug-info] [--cheribsd/build-tests] [--cheribsd/fast]
                     [--cheribsd/build-options OPTIONS] [--cheribsd/kernel-config CONFIG] [--cheribsd/only-build-kernel]
                     [--cheribsd/build-fpga-kernels] [--cheribsd-sysroot/remote-sdk-path PATH]
                     [--disk-image/extra-files DIR] [--disk-image/hostname HOSTNAME] [--disk-image/remote-path PATH]
                     [--disk-image/path IMGPATH] [--disk-image-freebsd-mips/extra-files DIR]
                     [--disk-image-freebsd-mips/hostname HOSTNAME] [--disk-image-freebsd-mips/remote-path PATH]
                     [--disk-image-freebsd-mips/path IMGPATH] [--run/monitor-over-telnet PORT]
                     [--run/ssh-forwarding-port PORT] [--run/remote-kernel-path RUN/REMOTE_KERNEL_PATH]
                     [--run/skip-kernel-update] [--run-freebsd-mips/monitor-over-telnet PORT]
                     [--run-freebsd-mips/ssh-forwarding-port PORT]
                     [--run-freebsd-mips/remote-kernel-path RUN_FREEBSD_MIPS/REMOTE_KERNEL_PATH]
                     [--run-freebsd-mips/skip-kernel-update] [--run-cherios/monitor-over-telnet PORT]
                     [--qt5/build-tests] [--qt5/build-examples] [--qt5/all-modules] [--qtbase/build-tests]
                     [--qtbase/build-examples]
                     [TARGET [TARGET ...]]

positional arguments:
  TARGET                The targets to build

optional arguments:
  -h, --help            show this help message and exit
  --config-file FILE    The config file that is used to load the default settings (default:
                        '/Users/alex/.config/cheribuild.json')
  --help-all, --help-hidden
                        Show all help options, including the target-specific ones.
  --pretend, -p         Only print the commands instead of running them (default: 'False')
  --clang-path CLANG_PATH
                        The Clang C compiler to use for compiling LLVM+Clang (must be at least version 3.7) (default:
                        '/usr/bin/clang')
  --clang++-path CLANG++_PATH
                        The Clang C++ compiler to use for compiling LLVM+Clang (must be at least version 3.7) (default:
                        '/usr/bin/clang++')
  --pass-k-to-make, -k  Pass the -k flag to make to continue after the first error (default: 'False')
  --with-libstatcounters
                        Link cross compiled CHERI project with libstatcounters. This is only useful when targetting FPGA
                        (default: 'False')
  --configure-only      Only run the configure step (skip build and install) (default: 'False')
  --skip-install        Skip the install step (only do the build) (default: 'False')
  --quiet, -q           Don't show stdout of the commands that are executed (default: 'False')
  --verbose, -v         Print all commmands that are executed (default: 'False')
  --clean, -c           Remove the build directory before build (default: 'False')
  --force, -f           Don't prompt for user input but use the default action (default: 'False')
  --no-logfile          Don't write a logfile for the build steps (default: 'False')
  --skip-update         Skip the git pull step (default: 'False')
  --skip-configure      Skip the configure step (default: 'False')
  --reconfigure, --force-configure
                        Always run the configure step, even for CMake projects with a valid cache. (default: 'False')
  --list-targets        List all available targets and exit (default: 'False')
  --dump-configuration  Print the current configuration as JSON. This can be saved to ~/.config/cheribuild.json to make
                        it persistent (default: 'False')
  --get-config-option KEY
                        Print the value of config option KEY and exit
  --include-dependencies, -d
                        Also build the dependencies of targets passed on the command line. Targets passed on thecommand
                        line will be reordered and processed in an order that ensures dependencies are built before the
                        real target. (run with --list-targets for more information) (default: 'False')
  --cheri-128, --128    Shortcut for --cheri-bits=128
  --cheri-256, --256    Shortcut for --cheri-bits=256
  --cheri-bits {128,256}
                        Whether to build the whole software stack for 128 or 256 bit CHERI. The output directories will
                        be suffixed with the number of bits to make sure the right binaries are being used. (default:
                        '256')
  --compilation-db, --cdb
                        Create a compile_commands.json file in the build dir (requires Bear for non-CMake projects)
                        (default: 'False')
  --cross-compile-for-mips, --xmips
                        Make cross compile projects target MIPS hybrid ABI instead of CheriABI (default: 'False')
  --cross-compile-for-host, --xhost
                        Make cross compile projects target the host system and use cheri clang to compile (tests that we
                        didn't break x86) (default: 'False')
  --make-without-nice   Run make/ninja without nice(1) (default: 'False')
  --make-jobs MAKE_JOBS, -j MAKE_JOBS
                        Number of jobs to use for compiling (default: '8')
  --source-root SOURCE_ROOT
                        The directory to store all sources (default: '/Users/alex/cheri')
  --output-root OUTPUT_ROOT
                        The directory to store all output (default: '<SOURCE_ROOT>/output')
  --build-root BUILD_ROOT
                        The directory for all the builds (default: '<SOURCE_ROOT>/build')

Options for target 'freebsd-mips':
  --freebsd-mips/subdir DIR
                        Only build subdir DIR instead of the full tree. Useful for quickly rebuilding an individual
                        program/library
  --freebsd-mips/use-external-toolchain-for-kernel
                        build the kernel with the external toolchain (default: 'False')
  --freebsd-mips/use-external-toolchain-for-world
                        Build world with the external toolchain (probably won't work!) (default: 'False')
  --freebsd-mips/no-debug-info
                        Do not pass make flags for building debug info
  --freebsd-mips/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')
  --freebsd-mips/fast   Skip some (usually) unnecessary build steps to spped up rebuilds (default: 'False')

Options for target 'cheribsd-without-sysroot':
  --cheribsd/subdir DIR
                        Only build subdir DIR instead of the full tree. Useful for quickly rebuilding an individual
                        program/library
  --cheribsd/no-use-external-toolchain-for-kernel
                        Do not build the kernel with the external toolchain
  --cheribsd/use-external-toolchain-for-world
                        Build world with the external toolchain (probably won't work!) (default: 'False')
  --cheribsd/no-debug-info
                        Do not pass make flags for building debug info
  --cheribsd/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')
  --cheribsd/fast       Skip some (usually) unnecessary build steps to spped up rebuilds (default: 'False')
  --cheribsd/build-options OPTIONS, --cheribsd-make-options OPTIONS
                        Additional make options to be passed to make when building CHERIBSD. See `man src.conf` for more
                        info. (default: '['-DWITHOUT_HTML', '-DWITHOUT_SENDMAIL', '-DWITHOUT_MAIL',
                        '-DWITHOUT_SVNLITE']')
  --cheribsd/kernel-config CONFIG, --kernconf CONFIG
                        The kernel configuration to use for `make buildkernel` (default: CHERI_MALTA64 or
                        CHERI128_MALTA64 depending on --cheri-bits)
  --cheribsd/only-build-kernel, --skip-buildworld
                        Skip the buildworld step -> only build and install the kernel (default: 'False')
  --cheribsd/build-fpga-kernels
                        Also build kernels for the FPGA. They will not be installed so you need to copy them from the
                        build directory. (default: 'False')

Options for target 'cheribsd-sysroot':
  --cheribsd-sysroot/remote-sdk-path PATH
                        The path to the CHERI SDK on the remote FreeBSD machine (e.g. vica:~foo/cheri/output/sdk256)

Options for target 'disk-image':
  --disk-image/extra-files DIR, --extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-cheri${CHERI_BITS}-alex')
  --disk-image/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image/path IMGPATH, --disk-image-path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/cheri256-disk.qcow2 or
                        $OUTPUT_ROOT/cheri128-disk.qcow2 depending on --cheri-bits.')

Options for target 'disk-image-freebsd-mips':
  --disk-image-freebsd-mips/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image-freebsd-mips/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-mips-alex')
  --disk-image-freebsd-mips/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image-freebsd-mips/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/freebsd-mips.qcow2')

Options for target 'run':
  --run/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C
  --run/ssh-forwarding-port PORT, --ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` connect to the VM (default: '19500')
  --run/remote-kernel-path RUN/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'run-freebsd-mips':
  --run-freebsd-mips/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C
  --run-freebsd-mips/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` connect to the VM (default: '19502')
  --run-freebsd-mips/remote-kernel-path RUN_FREEBSD_MIPS/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run-freebsd-mips/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'run-cherios':
  --run-cherios/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C

Options for target 'qt5':
  --qt5/build-tests     build the Qt unit tests (default: 'False')
  --qt5/build-examples  build the Qt examples (default: 'False')
  --qt5/all-modules     Build all modules (even those that don't make sense for CHERI) (default: 'False')

Options for target 'qtbase':
  --qtbase/build-tests  build the Qt unit tests (default: 'False')
  --qtbase/build-examples
                        build the Qt examples (default: 'False')

```
