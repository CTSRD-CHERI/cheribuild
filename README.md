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

cheribuild.py will also build all the other target that the given target depends on unless you pass the `-t` flag.


## Available options

Options can be specified on the command or loaded from a JSON config file (`~/.config/cheribuild.json`).
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


```
usage: cheribuild.py [-h] [--pretend] [--quiet] [--clean] [--skip-update] [--skip-configure] [--skip-buildworld]
                     [--list-targets] [--dump-configuration] [--skip-dependencies]
                     [--cheri-128 | --cheri-256 | --cheri-bits {128,256}] [--source-root SOURCE_ROOT]
                     [--output-root OUTPUT_ROOT] [--extra-files EXTRA_FILES] [--disk-image-path DISK_IMAGE_PATH]
                     [--nfs-kernel-path NFS_KERNEL_PATH] [--make-jobs MAKE_JOBS] [--ssh-forwarding-port PORT]
                     [--cheribsd-revision GIT_COMMIT_ID] [--llvm-revision GIT_COMMIT_ID]
                     [--clang-revision GIT_COMMIT_ID] [--lldb-revision GIT_COMMIT_ID] [--qemu-revision GIT_COMMIT_ID]
                     [TARGET [TARGET ...]]

positional arguments:
  TARGET                The targets to build

optional arguments:
  -h, --help            show this help message and exit
  --pretend, -p         Only print the commands instead of running them
  --quiet, -q           Don't show stdout of the commands that are executed
  --clean, -c           Remove the build directory before build
  --skip-update         Skip the git pull step
  --skip-configure      Skip the configure step
  --skip-buildworld     Skip the FreeBSD buildworld step -> only build and install the kernel
  --list-targets        List all available targets and exit
  --dump-configuration  Print the current configuration as JSON. This can be saved to ~/.config/cheribuild.json to make
                        it persistent
  --skip-dependencies, -t
                        Only build the targets that were explicitly passed on the command line
  --cheri-128, --128    Shortcut for --cheri-bits=128
  --cheri-256, --256    Shortcut for --cheri-bits=256
  --cheri-bits {128,256}
                        Whether to build the whole software stack for 128 or 256 bit CHERI. The output directories will
                        be suffixed with the number of bits to make sure the right binaries are being used. WARNING:
                        128-bit CHERI is still very unstable. (default: '256')
  --source-root SOURCE_ROOT
                        The directory to store all sources (default: '/home/alex/cheri')
  --output-root OUTPUT_ROOT
                        The directory to store all output (default: '<SOURCE_ROOT>/output')
  --extra-files EXTRA_FILES
                        A directory with additional files that will be added to the image (default:
                        '<OUTPUT_ROOT>/extra-files')
  --disk-image-path DISK_IMAGE_PATH
                        The output path for the QEMU disk image (default: '<OUTPUT_ROOT>/cheri256-disk.img')
  --nfs-kernel-path NFS_KERNEL_PATH
                        The output path for the CheriBSD kernel that boots over NFS (default:
                        '<OUTPUT_ROOT>/nfs/kernel')
  --make-jobs MAKE_JOBS, -j MAKE_JOBS
                        Number of jobs to use for compiling (default: '8')
  --ssh-forwarding-port PORT, -s PORT
                        The port to use on localhost to forward the QEMU ssh port. You can then use `ssh root@localhost
                        -p $PORT` connect to the VM (default: '9999')

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
```


