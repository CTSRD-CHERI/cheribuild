# Scripts useful for working with CHERI


##`build_cheribsd_for_qemu.py` (**Requires Python 3.4**)

This script makes it easy to run [CHERIBSD](https://github.com/CTSRD-CHERI/cheribsd) on [QEMU](https://github.com/CTSRD-CHERI/qemu)

If all the required repositories are checked out `build_cheribsd_for_qemu.py all` will build all project,
create a CHERIBSD disk image and launch QEMU with that disk image.

**NOTE**: As this involves building CHERIBSD you will need to run this script on a FreeBSD system.

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
usage: build_cheribsd_for_qemu.py [-h] [--clone] [--make-jobs MAKE_JOBS]
                                  [--clean] [--pretend] [--quiet]
                                  [--list-targets] [--skip-update]
                                  [--skip-configure]
                                  [--disk-image-path DISK_IMAGE_PATH]
                                  [TARGET [TARGET ...]]

positional arguments:
  TARGET                The targets to build

optional arguments:
  -h, --help            show this help message and exit
  --make-jobs MAKE_JOBS, -j MAKE_JOBS
                        Number of jobs to use for compiling
  --clean               Do a clean build
  --pretend, -p         Print the commands that would be run instead of
                        executing them
  --quiet               Don't show stdout of the commands that are executed
  --list-targets        List all available targets
  --skip-update         Skip the git pull step
  --skip-configure      Don't run the configure step
  --disk-image-path DISK_IMAGE_PATH
                        The disk image path (defaults to output/disk.img)
```


