# Scripts useful for working with CHERI


##`build_cheribsd_for_qemu.py` (**Requires Python 3.4**)

This script makes it easy to run [CHERIBSD](https://github.com/CTSRD-CHERI/cheribsd) on [QEMU](https://github.com/CTSRD-CHERI/qemu)

Running `build_cheribsd_for_qemu.py all` will clone, build and install all projects, then create a CHERIBSD disk image and launch QEMU with that disk image.

**NOTE**: As this involves building CHERIBSD you will need to run this script on a FreeBSD system.
If you want to run this script on a remote FreeBSD host you can use the `py3-run-remote.sh` script that is included in this repository:

`py3-run-remote my.freebsd.server ./build_cheribsd_for_qemu.py all` will build and run CHERIBSD on `my.freebsd.server`

The following targets are available:

- `binutils` build and install [CTSRD-CHERI/binutils](https://github.com/CTSRD-CHERI/binutils)
- `qemu` build and install [CTSRD-CHERI/qemu](https://github.com/CTSRD-CHERI/qemu)
- `llvm` build and install [CTSRD-CHERI/llvm](https://github.com/CTSRD-CHERI/llvm) and [CTSRD-CHERI/clang](https://github.com/CTSRD-CHERI/clang)
- `cheribsd` build and install [CTSRD-CHERI/cheribsd](https://github.com/CTSRD-CHERI/cheribsd)
- `disk-image` creates a CHERIBSD disk-image
- `run` launch QEMU with the CHERIBSD disk image
- `all` execute all of the above targets

### Output of `--help`:

```
usage: build_cheribsd_for_qemu.py [-h] [--make-jobs MAKE_JOBS] [--clean] [--pretend] [--quiet] [--list-targets] [--skip-update] [--skip-configure]
                                  [--source-root SOURCE_ROOT] [--output-root OUTPUT_ROOT] [--disk-image-path DISK_IMAGE_PATH]
                                  [TARGET [TARGET ...]]

positional arguments:
  TARGET                The targets to build

optional arguments:
  -h, --help            show this help message and exit
  --make-jobs MAKE_JOBS, -j MAKE_JOBS
                        Number of jobs to use for compiling (default: 8)
  --clean               Remove the build directory before build
  --pretend, -p         Print the commands that would be run instead of executing them
  --quiet, -q           Don't show stdout of the commands that are executed
  --list-targets        List all available targets and exit
  --skip-update         Skip the git pull step
  --skip-configure      Skip the configure step
  --source-root SOURCE_ROOT
                        The directory to store all sources (default: '$HOME/cheri')
  --output-root OUTPUT_ROOT
                        The directory to store all output (default: '<SOURCE_ROOT>/output')
  --disk-image-path DISK_IMAGE_PATH
                        The output path for the QEMU disk image (default: '<OUTPUT_ROOT>/disk.img')

```


