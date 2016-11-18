# `cheribuild.py` - A script to build CHERI-related software (**Requires Python 3.4+**)

This script automates all the steps required to build various [CHERI](http://www.chericpu.com)-related software.
For example `cheribuild.py [options] run` will start an insanstance of [CHERIBSD](https://github.com/CTSRD-CHERI/cheribsd) on [QEMU](https://github.com/CTSRD-CHERI/qemu) and
`cheribuild.py [options] sdk` will create a SDK that can be used to compile software for the CHERI CPU.

**NOTE**: As this involves building CHERIBSD you will need to run this script on a FreeBSD system.
If you want to run this script on a remote FreeBSD host you can use the `remote-cheribuild.py` script that is included in this repository:

`remote-cheribuild.py my.freebsd.server [options] <targets...>` will run this script on `my.freebsd.server`

If you would like to see what the script will do run it with the `--pretend` or `-p` option.
For even more detail you can also pass `--verbose` or `-v`.

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
- `cheribsd` builds and installs [CTSRD-CHERI/cheribsd](https://github.com/CTSRD-CHERI/cheribsd)
- `disk-image` creates a CHERIBSD disk-image
- `run` launches QEMU with the CHERIBSD disk image
- `cheribsd-sysroot` creates a CheriBSD sysroot. When running this script on a non-FreeBSD system the files will need to be copied from a build server
- `freestanding-sdk` builds everything required to build and run `-ffreestanding` binaries: compiler, binutils and qemu
- `cheribsd-sdk` builds everything required to compile binaries for CheriBSD: `freestanding-sdk` and `cheribsd-sysroot`
- `sdk` is an alias for `cheribsd-sdk` when building on FreeBSD, otherwise builds `freestanding-sdk`
- `all`: runs all the targets listed so far (`run` comes last so you can then interact with QEMU)

#### Other targets
- `cmake` builds and installs latest [CMake](https://github.com/Kitware/CMake)
- `brandelf` builds and installs `brandelf` from [elftoolchain](https://github.com/Richardson/elftoolchain/) (needed for SDK on non-FreeBSD systems)
- `awk` builds and installs BSD AWK (if you need it on Linux)
- `cherios` builds and installs [CTSRD-CHERI/cherios](https://github.com/CTSRD-CHERI/cherios)
- `cheritrace` builds and installs [CTSRD-CHERI/cheritrace](https://github.com/CTSRD-CHERI/cheritrace)
- `cherivis` builds and installs [CTSRD-CHERI/cherivis](https://github.com/CTSRD-CHERI/cherivis)

## Full list of options

```
usage: cheribuild.py [-h] [--pretend] [--quiet] [--verbose] [--clean] [--force] [--skip-update]
                     [--skip-configure] [--skip-install] [--skip-buildworld] [--list-targets]
                     [--dump-configuration] [--skip-dependencies] [--include-dependencies]
                     [--disable-tmpfs] [--no-logfile]
                     [--cheri-128 | --cheri-256 | --cheri-bits {128,256}]
                     [--source-root SOURCE_ROOT] [--output-root OUTPUT_ROOT]
                     [--extra-files EXTRA_FILES] [--clang-path CLANG_PATH]
                     [--clang++-path CLANG++_PATH] [--disk-image-path DISK_IMAGE_PATH]
                     [--make-jobs MAKE_JOBS] [--ssh-forwarding-port PORT]
                     [--cheribsd-make-options CHERIBSD_MAKE_OPTIONS]
                     [--cheribsd-revision GIT_COMMIT_ID] [--llvm-revision GIT_COMMIT_ID]
                     [--clang-revision GIT_COMMIT_ID] [--lldb-revision GIT_COMMIT_ID]
                     [--qemu-revision GIT_COMMIT_ID] [--freebsd-builder-hostname SSH_HOSTNAME]
                     [--freebsd-builder-output-path PATH] [--freebsd-builder-copy-only]
                     [--config-file FILE]
                     [TARGET [TARGET ...]]

positional arguments:
  TARGET                The targets to build

optional arguments:
  -h, --help            show this help message and exit
  --pretend, -p         Only print the commands instead of running them
  --quiet, -q           Don't show stdout of the commands that are executed
  --verbose, -v         Print all commmands that are executed
  --clean, -c           Remove the build directory before build
  --force, -f           Don't prompt for user input but use the default action
  --skip-update         Skip the git pull step
  --skip-configure      Skip the configure step
  --skip-install        Skip the install step (only do the build)
  --skip-buildworld     Skip the FreeBSD buildworld step -> only build and install the kernel
  --list-targets        List all available targets and exit
  --dump-configuration  Print the current configuration as JSON. This can be saved to
                        ~/.config/cheribuild.json to make it persistent
  --skip-dependencies, -t
                        This option no longer does anything and is only included toallow running
                        existing command lines
  --include-dependencies, -d
                        Also build the dependencies of targets passed on the command line. Targets
                        passed on thecommand line will be reordered and processed in an order that
                        ensures dependencies are built before the real target. (run with --list-
                        targets for more information)
  --disable-tmpfs       Don't make /tmp a TMPFS mount in the CHERIBSD system image. This is a
                        workaround in case TMPFS is not working correctly
  --no-logfile          Don't write a logfile for the build steps
  --cheri-128, --128    Shortcut for --cheri-bits=128
  --cheri-256, --256    Shortcut for --cheri-bits=256
  --cheri-bits {128,256}
                        Whether to build the whole software stack for 128 or 256 bit CHERI. The
                        output directories will be suffixed with the number of bits to make sure the
                        right binaries are being used. WARNING: 128-bit CHERI is still very
                        unstable. (default: '256')
  --source-root SOURCE_ROOT
                        The directory to store all sources (default: '/home/alr48/cheri')
  --output-root OUTPUT_ROOT
                        The directory to store all output (default: '<SOURCE_ROOT>/output')
  --extra-files EXTRA_FILES
                        A directory with additional files that will be added to the image (default:
                        '<OUTPUT_ROOT>/extra-files')
  --clang-path CLANG_PATH
                        The Clang C compiler to use for compiling LLVM+Clang (must be at least
                        version 3.7) (default: '/usr/bin/clang')
  --clang++-path CLANG++_PATH
                        The Clang C++ compiler to use for compiling LLVM+Clang (must be at least
                        version 3.7) (default: '/usr/bin/clang++')
  --disk-image-path DISK_IMAGE_PATH
                        The output path for the QEMU disk image (default:
                        '<OUTPUT_ROOT>/cheri256-disk.qcow2')
  --make-jobs MAKE_JOBS, -j MAKE_JOBS
                        Number of jobs to use for compiling (default: '4')
  --ssh-forwarding-port PORT, -s PORT
                        The port to use on localhost to forward the QEMU ssh port. You can then use
                        `ssh root@localhost -p $PORT` connect to the VM (default: '12374')
  --cheribsd-make-options CHERIBSD_MAKE_OPTIONS
                        Additional options to be passed to make when building CHERIBSD. See man
                        src.conf for more info (default: '-DWITHOUT_TESTS -DWITHOUT_HTML
                        -DWITHOUT_SENDMAIL -DWITHOUT_MAIL -DWITHOUT_SVNLITE')
  --config-file FILE    The config file that is used to load the default settings (default:
                        '/home/alr48/.config/cheribuild.json')

Specifying git revisions:
  Useful if the current HEAD of a repository does not work but an older one did.

  --cheribsd-revision GIT_COMMIT_ID
                        The git revision or branch of CHERIBSD to check out
  --llvm-revision GIT_COMMIT_ID
                        The git revision or branch of LLVM to check out
  --clang-revision GIT_COMMIT_ID
                        The git revision or branch of clang to check out
  --lldb-revision GIT_COMMIT_ID
                        The git revision or branch of clang to check out
  --qemu-revision GIT_COMMIT_ID
                        The git revision or branch of QEMU to check out

Specifying a remote FreeBSD build server:
  Useful if you want to create a CHERI SDK on a Linux or OS X host to allow cross compilation to a
  CHERI target.

  --freebsd-builder-hostname SSH_HOSTNAME
                        This string will be passed to ssh and be something like user@hostname of a
                        FreeBSD system that can be used to build CHERIBSD. Can also be the name of a
                        host in ~/.ssh/config.
  --freebsd-builder-output-path PATH
                        The path where the cheribuild output is stored on the FreeBSD build server.
  --freebsd-builder-copy-only
                        Only scp the SDK from theFreeBSD build server and don't build the SDK first.

```
