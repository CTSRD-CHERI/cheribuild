# `cheribuild.py` - A script to build CHERI-related software (**Requires Python 3.5+**)

This script automates all the steps required to build various [CHERI](http://www.chericpu.com)-related software.
For example `cheribuild.py [options] sdk` will create a SDK that can be
used to compile software for the CHERI CPU and `cheribuild.py [options] run`
will start an instance of [CheriBSD](https://github.com/CTSRD-CHERI/cheribsd) in [QEMU](https://github.com/CTSRD-CHERI/qemu).

It has been tested and should work on FreeBSD 10, 11 and 12.
On Linux Ubuntu 16.04 and OpenSUSE Tubleweed are supported. Ubuntu 14.04 may also work but is no longer tested.
MacOS 10.13 is also supported.

# TL;DR

If you want to start up a QEMU VM running CheriBSD run `cheribuild.py run -d` (-d means build all dependencies).
This will build the CHERI compiler, QEMU, CheriBSD, create a disk image and boot that in QEMU.
By default this builds the 256-bit version of CheriBSD. If you would like to use the 128-bit
compressed capabilities, run `cheribuild.py run -d --128`.

By default `cheribuild.py` will clone all projects in `~/cheri`, use `~/cheri/build` for build directories
and install into `~/cheri/output`. However, these directories are all configurable (see below for details).



If you would like to see what the script would do run it with the `--pretend` or `-p` option.
For even more detail you can also pass `--verbose` or `-v`.

**NOTE**: Currently, you will need to run this script on a FreeBSD system if you would like to build CheriBSD.
All the other steps work on other operating systems but you will have to copy the CheriBSD files from a FreeBSD system.
It is also possible to run this script on a remote FreeBSD host by using the `remote-cheribuild.py` script that is included in this repository:
`remote-cheribuild.py my.freebsd.server [options] <targets...>` will run this script on `my.freebsd.server`.


# Usage

`cheribuild.py [options...] targets...`

Example: to build and run a 128-bit CheriBSD: `cheribuild.py --include-dependencies --128 run` and
for a clean verbose build of 256-bit CheriBSD `cheribuild.py -v --clean --include-dependencies --256 run`

## Building the compiler and QEMU

In order to run CheriBSD you will first need to compile QEMU (`cheribuild.py qemu`).
This will build versions of QEMU both for 128 and 256-bit CHERI. You will also need to build
LLVM (this includes a compiler and linker suitable for CHERI) using `cheribuild.py llvm`.
It is now possible to target both 128 and 256-bit CHERI using the same clang binary by specifying
`-mcpu=cheri128` or `-mcpu=256`. However, if you use cheribuild.py for building you won't have to care
about this since the `--128` or `--256` (the default) flag for cheribuild.py will ensure the right
flags are passed.

All binaries will by default be installed to `~/cheri/sdk/bin`.


## Building and running CheriBSD

To build CheriBSD (currently only possible on FreeBSD hosts) run `cheribuild.py cheribsd`.

If you would like to build all binaries in CheriBSD as pure capability programs you will need to pass
the `-DWITH_CHERI_PURE` flag to make. This can either be set in the environment or passed as an option
to cheribuild: `cheribuild.py cheribsd --cheribsd/build-options=-DWITH_CHERI_PURE`.
The current default is to build the normal userspace binaries as MIPS binaries since this speeds up
the boot under QEMU (because QEMU has to emulate the bounds checks instead of doing them in hardware).


### Disk image

The disk image is created by the `cheribuild.py disk-image` target and can then be used as a boot disk by QEMU.
In order to customize the disk image it will add all files under (by default) `~/cheri/extra-files/`
to the resulting image. When building the image cheribuild will ask you whether it should add your
SSH public keys to the `/root/.ssh/authorized_keys` file in the CheriBSD image. It will also
generate SSH host keys for the image so that those don't change everytime the image is rebuilt.
A suitable `/etc/rc.conf` and `/etc/fstab` will also be added to this directory and can then be customized.

The default path for the disk image is `~/cheri/output/cheri256-disk.img` for 256-bit CHERI and
 `~/cheri/output/cheri128-disk.img` for 128-bit.

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
    }
}
```

### Speeding up SSH connections
Connecting to CheriBSD via ssh can take a few seconds. Further connections after the first can
be sped up by using the openssh ControlMaster setting:
```
Host cheribsd
  User root
  Port 12374
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

## Available Targets

When selecting a target you can also build all the targets that it depends on by passing the `--include-dependencies` or `-d` option.
However, some targets (e.g. `all`, `sdk`) will always build their dependencies because running them without building the dependencies does not make sense (see the list of targets for details).

**TODO: Possibly restore the previous behaviour of dependencies included by default and opt out? It is probably be the more logical behaviour? I changed it because I often want to build only cheribsd without changing the compiler but forget to pass the `--skip-dependencies` flag**

#### The following main targets are available

- `qemu` builds and installs [CTSRD-CHERI/qemu](https://github.com/CTSRD-CHERI/qemu)
- `llvm` builds and installs [CTSRD-CHERI/llvm](https://github.com/CTSRD-CHERI/llvm) and [CTSRD-CHERI/clang](https://github.com/CTSRD-CHERI/clang) and [CTSRD-CHERI/lld](https://github.com/CTSRD-CHERI/lld)
- `cheribsd` builds and installs [CTSRD-CHERI/cheribsd](https://github.com/CTSRD-CHERI/cheribsd) (**NOTE:** Only works on FreeBSD systems)
- `disk-image` creates a CHERIBSD disk-image
- `elftoolchain` builds the binutils such as ar, readelf, etc. This will probably be replaced by the tools from LLVM soon
- `run` launches QEMU with the CHERIBSD disk image
- `cheribsd-sysroot` creates a CheriBSD sysroot. When running this script on a non-FreeBSD system the files will need to be copied from a build server
- `freestanding-sdk` builds everything required to build and run `-ffreestanding` binaries: compiler, binutils and qemu
- `cheribsd-sdk` builds everything required to compile binaries for CheriBSD: `freestanding-sdk` and `cheribsd-sysroot`
- `sdk` is an alias for `cheribsd-sdk` when building on FreeBSD, otherwise builds `freestanding-sdk`
- `all`: runs all the targets listed so far (`run` comes last so you can then interact with QEMU)

#### Other targets
- `gnu-binutils` (deprecated) builds and installs [CTSRD-CHERI/binutils](https://github.com/CTSRD-CHERI/binutils). This is only useful if you want to use GNU objdump instead of llvm-objdump since it builds the ancient 2.17 version of binutils
- `cmake` builds and installs latest [CMake](https://github.com/Kitware/CMake)
- `binutils` builds and installs [CTSRD-CHERI/binutils](https://github.com/CTSRD-CHERI/binutils)
- `brandelf` builds and installs `brandelf` from [elftoolchain](https://github.com/Richardson/elftoolchain/) (needed for SDK on non-FreeBSD systems)
- `awk` builds and installs BSD AWK (if you need it on Linux)
- `cherios` builds and installs [CTSRD-CHERI/cherios](https://github.com/CTSRD-CHERI/cherios)
- `cheritrace` builds and installs [CTSRD-CHERI/cheritrace](https://github.com/CTSRD-CHERI/cheritrace)
- `cherivis` builds and installs [CTSRD-CHERI/cherivis](https://github.com/CTSRD-CHERI/cherivis)


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
usage: cheribuild.py [-h] [--config-file FILE] [--help-all] [--pretend] [--clang-path CLANG_PATH]
                     [--clang++-path CLANG++_PATH] [--pass-k-to-make] [--with-libstatcounters] [--skip-buildworld]
                     [--buildenv] [--libcheri-buildenv] [--print-targets-only] [--no-unified-sdk]
                     [--no-clang-colour-diags] [--use-sdk-clang-for-native-xbuild] [--configure-only] [--skip-install]
                     [--docker] [--docker-container DOCKER_CONTAINER] [--docker-reuse-container] [--quiet] [--verbose]
                     [--clean] [--force] [--no-logfile] [--skip-update] [--force-update] --skip-configure |
                     --reconfigure] [--list-targets] [--dump-configuration] [--get-config-option KEY]
                     [--include-dependencies] --cheri-128 | --cheri-256 | --cheri-bits {128,256}] [--compilation-db]
                     --cross-compile-for-mips | --cross-compile-for-host] [--make-without-nice] [--make-jobs MAKE_JOBS]
                     [--source-root SOURCE_ROOT] [--output-root OUTPUT_ROOT] [--build-root BUILD_ROOT]
                     [--disk-image-freebsd-mips/extra-files DIR] [--disk-image-freebsd-mips/hostname HOSTNAME]
                     [--disk-image-freebsd-mips/remote-path PATH] [--disk-image-freebsd-mips/path IMGPATH]
                     [--qtbase/build-tests] [--qtbase/build-examples] [--qtbase/minimal] [--qtbase/optmized-debug-build]
                     [--run-freebsd-x86/monitor-over-telnet PORT] [--run-freebsd-x86/ssh-forwarding-port PORT]
                     [--run-freebsd-x86/remote-kernel-path RUN_FREEBSD_X86/REMOTE_KERNEL_PATH]
                     [--run-freebsd-x86/skip-kernel-update] [--cheribsd-sysroot/remote-sdk-path PATH]
                     [--run-freebsd-mips/monitor-over-telnet PORT] [--run-freebsd-mips/ssh-forwarding-port PORT]
                     [--run-freebsd-mips/remote-kernel-path RUN_FREEBSD_MIPS/REMOTE_KERNEL_PATH]
                     [--run-freebsd-mips/skip-kernel-update] [--disk-image/extra-files DIR]
                     [--disk-image/hostname HOSTNAME] [--disk-image/remote-path PATH] [--disk-image/path IMGPATH]
                     [--run/monitor-over-telnet PORT] [--run/ssh-forwarding-port PORT]
                     [--run/remote-kernel-path RUN/REMOTE_KERNEL_PATH] [--run/skip-kernel-update]
                     [--freebsd-x86/subdir-with-deps DIR] [--freebsd-x86/subdir SUBDIRS]
                     [--freebsd-x86/build-options OPTIONS] [--freebsd-x86/no-use-external-toolchain-for-kernel]
                     [--freebsd-x86/no-use-external-toolchain-for-world] [--freebsd-x86/no-debug-info]
                     [--freebsd-x86/build-tests] [--freebsd-x86/no-auto-obj] [--freebsd-x86/minimal]
                     [--freebsd-x86/fast] [--cheribsd/subdir-with-deps DIR] [--cheribsd/subdir SUBDIRS]
                     [--cheribsd/build-options OPTIONS] [--cheribsd/no-use-external-toolchain-for-kernel]
                     [--cheribsd/no-use-external-toolchain-for-world] [--cheribsd/no-debug-info]
                     [--cheribsd/build-tests] [--cheribsd/no-auto-obj] [--cheribsd/minimal] [--cheribsd/fast]
                     [--cheribsd/kernel-config CONFIG] [--cheribsd/build-fpga-kernels] [--cheribsd/pure-cap-kernel]
                     [--run-cherios/monitor-over-telnet PORT] [--qt5/build-tests] [--qt5/build-examples] [--qt5/minimal]
                     [--qt5/optmized-debug-build] [--qt5/all-modules] [--freebsd-mips/subdir-with-deps DIR]
                     [--freebsd-mips/subdir SUBDIRS] [--freebsd-mips/build-options OPTIONS]
                     [--freebsd-mips/no-use-external-toolchain-for-kernel]
                     [--freebsd-mips/no-use-external-toolchain-for-world] [--freebsd-mips/no-debug-info]
                     [--freebsd-mips/build-tests] [--freebsd-mips/no-auto-obj] [--freebsd-mips/minimal]
                     [--freebsd-mips/fast] [--disk-image-freebsd-x86/extra-files DIR]
                     [--disk-image-freebsd-x86/hostname HOSTNAME] [--disk-image-freebsd-x86/remote-path PATH]
                     [--disk-image-freebsd-x86/path IMGPATH]
                     [TARGET [TARGET ...]]

positional arguments:
  TARGET                The targets to build

optional arguments:
  -h, --help            show this help message and exit
  --config-file FILE    The config file that is used to load the default settings (default:
                        '/home/alr48/.config/cheribuild.json')
  --help-all, --help-hidden
                        Show all help options, including the target-specific ones.
  --pretend, -p         Only print the commands instead of running them (default: 'False')
  --clang-path CLANG_PATH
                        The Clang C compiler to use for compiling LLVM+Clang (must be at least version 3.7) (default:
                        '/usr/bin/clang-6.0')
  --clang++-path CLANG++_PATH
                        The Clang C++ compiler to use for compiling LLVM+Clang (must be at least version 3.7) (default:
                        '/usr/local/bin/clang++')
  --pass-k-to-make, -k  Pass the -k flag to make to continue after the first error (default: 'False')
  --with-libstatcounters
                        Link cross compiled CHERI project with libstatcounters. This is only useful when targetting FPGA
                        (default: 'False')
  --skip-buildworld     Skip the buildworld step when buildingFreeBSD or CheriBSD (default: 'False')
  --buildenv            Open a shell with the right environmentfor building the project. Currently onlyworks for
                        FreeBSD/CheriBSD (default: 'False')
  --libcheri-buildenv   Open a shell with the right environment for building CHERI libraries. Currently only works for
                        CheriBSD (default: 'False')
  --print-targets-only  Don't run the build but instead only print the targets that would be executed (default: 'False')
  --no-unified-sdk      Do not build a single SDK instead of separate 128 and 256 bits ones
  --no-clang-colour-diags
                        Do not force CHERI clang to emit coloured diagnostics
  --use-sdk-clang-for-native-xbuild
                        Compile cross-compile project with CHERI clang from the SDK instead of host compiler (default:
                        'False')
  --configure-only      Only run the configure step (skip build and install) (default: 'False')
  --skip-install        Skip the install step (only do the build) (default: 'False')
  --docker              Run the build inside a docker container (default: 'False')
  --docker-container DOCKER_CONTAINER
                        Name of the docker container to use (default: 'cheribuild-test')
  --docker-reuse-container
                        Attach to the same container again (note: docker-container option must be an id rather than a
                        container name (default: 'False')
  --quiet, -q           Don't show stdout of the commands that are executed (default: 'False')
  --verbose, -v         Print all commmands that are executed (default: 'False')
  --clean, -c           Remove the build directory before build (default: 'False')
  --force, -f           Don't prompt for user input but use the default action (default: 'False')
  --no-logfile          Don't write a logfile for the build steps (default: 'False')
  --skip-update         Skip the git pull step (default: 'False')
  --force-update        Always update (with autostash) even if there are uncommitted changes (default: 'False')
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
                        The directory to store all sources (default: '/home/alr48/cheri')
  --output-root OUTPUT_ROOT
                        The directory to store all output (default: '<SOURCE_ROOT>/output')
  --build-root BUILD_ROOT
                        The directory for all the builds (default: '<SOURCE_ROOT>/build')

Options for target 'disk-image-freebsd-mips':
  --disk-image-freebsd-mips/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image-freebsd-mips/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-mips-alr48')
  --disk-image-freebsd-mips/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image-freebsd-mips/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/freebsd-mips .img')

Options for target 'qtbase':
  --qtbase/build-tests  build the Qt unit tests (default: 'False')
  --qtbase/build-examples
                        build the Qt examples (default: 'False')
  --qtbase/minimal      Don't build QtWidgets or QtGui, etc (default: 'False')
  --qtbase/optmized-debug-build
                        Don't build with -Os instead of -O0 for debug info builds (default: 'False')

Options for target 'run-freebsd-x86':
  --run-freebsd-x86/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C
  --run-freebsd-x86/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` connect to the VM (default: '12380')
  --run-freebsd-x86/remote-kernel-path RUN_FREEBSD_X86/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run-freebsd-x86/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'cheribsd-sysroot':
  --cheribsd-sysroot/remote-sdk-path PATH
                        The path to the CHERI SDK on the remote FreeBSD machine (e.g. vica:~foo/cheri/output/sdk256)

Options for target 'run-freebsd-mips':
  --run-freebsd-mips/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C
  --run-freebsd-mips/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` connect to the VM (default: '12376')
  --run-freebsd-mips/remote-kernel-path RUN_FREEBSD_MIPS/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run-freebsd-mips/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'disk-image':
  --disk-image/extra-files DIR, --extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-cheri${CHERI_BITS}-alr48')
  --disk-image/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image/path IMGPATH, --disk-image-path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/cheri256-disk.img or
                        $OUTPUT_ROOT/cheri128-disk.img depending on --cheri-bits.')

Options for target 'run':
  --run/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C
  --run/ssh-forwarding-port PORT, --ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` connect to the VM (default: '12374')
  --run/remote-kernel-path RUN/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'freebsd-x86':
  --freebsd-x86/subdir-with-deps DIR
                        Only build subdir DIR instead of the full tree.#This uses the SUBDIR_OVERRIDE mechanism so will
                        build much morethan just that directory
  --freebsd-x86/subdir SUBDIRS
                        Only build subdirs SUBDIRS instead of the full tree. Useful for quickly rebuilding an individual
                        programs/libraries. If more than one dir is passed they will be processed in order. Note: This
                        will break if not all dependencies have been built.
  --freebsd-x86/build-options OPTIONS
                        Additional make options to be passed to make when building CHERIBSD. See `man src.conf` for more
                        info. (default: '[]')
  --freebsd-x86/no-use-external-toolchain-for-kernel
                        Do not build the kernel with the external toolchain
  --freebsd-x86/no-use-external-toolchain-for-world
                        Do not build world with the external toolchain
  --freebsd-x86/no-debug-info
                        Do not pass make flags for building with debug info
  --freebsd-x86/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')
  --freebsd-x86/no-auto-obj
                        Do not use -DWITH_AUTO_OBJ (experimental)
  --freebsd-x86/minimal
                        Don't build all of FreeBSD, just what is needed for running most CHERI tests/benchmarks
                        (default: 'False')
  --freebsd-x86/fast    Skip some (usually) unnecessary build steps to speed up rebuilds (default: 'False')

Options for target 'cheribsd':
  --cheribsd/subdir-with-deps DIR
                        Only build subdir DIR instead of the full tree.#This uses the SUBDIR_OVERRIDE mechanism so will
                        build much morethan just that directory
  --cheribsd/subdir SUBDIRS
                        Only build subdirs SUBDIRS instead of the full tree. Useful for quickly rebuilding an individual
                        programs/libraries. If more than one dir is passed they will be processed in order. Note: This
                        will break if not all dependencies have been built.
  --cheribsd/build-options OPTIONS
                        Additional make options to be passed to make when building CHERIBSD. See `man src.conf` for more
                        info. (default: '[]')
  --cheribsd/no-use-external-toolchain-for-kernel
                        Do not build the kernel with the external toolchain
  --cheribsd/no-use-external-toolchain-for-world
                        Do not build world with the external toolchain
  --cheribsd/no-debug-info
                        Do not pass make flags for building with debug info
  --cheribsd/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')
  --cheribsd/no-auto-obj
                        Do not use -DWITH_AUTO_OBJ (experimental)
  --cheribsd/minimal    Don't build all of FreeBSD, just what is needed for running most CHERI tests/benchmarks
                        (default: 'False')
  --cheribsd/fast       Skip some (usually) unnecessary build steps to speed up rebuilds (default: 'False')
  --cheribsd/kernel-config CONFIG, --kernconf CONFIG
                        The kernel configuration to use for `make buildkernel` (default: CHERI_MALTA64 or
                        CHERI128_MALTA64 depending on --cheri-bits)
  --cheribsd/build-fpga-kernels
                        Also build kernels for the FPGA. They will not be installed so you need to copy them from the
                        build directory. (default: 'False')
  --cheribsd/pure-cap-kernel
                        Build kernel with pure capability ABI (probably won't work!) (default: 'False')

Options for target 'run-cherios':
  --run-cherios/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhostat $PORT via telnet instead
                        of using CTRL+A,C

Options for target 'qt5':
  --qt5/build-tests     build the Qt unit tests (default: 'False')
  --qt5/build-examples  build the Qt examples (default: 'False')
  --qt5/minimal         Don't build QtWidgets or QtGui, etc (default: 'False')
  --qt5/optmized-debug-build
                        Don't build with -Os instead of -O0 for debug info builds (default: 'False')
  --qt5/all-modules     Build all modules (even those that don't make sense for CHERI) (default: 'False')

Options for target 'freebsd-mips':
  --freebsd-mips/subdir-with-deps DIR
                        Only build subdir DIR instead of the full tree.#This uses the SUBDIR_OVERRIDE mechanism so will
                        build much morethan just that directory
  --freebsd-mips/subdir SUBDIRS
                        Only build subdirs SUBDIRS instead of the full tree. Useful for quickly rebuilding an individual
                        programs/libraries. If more than one dir is passed they will be processed in order. Note: This
                        will break if not all dependencies have been built.
  --freebsd-mips/build-options OPTIONS
                        Additional make options to be passed to make when building CHERIBSD. See `man src.conf` for more
                        info. (default: '[]')
  --freebsd-mips/no-use-external-toolchain-for-kernel
                        Do not build the kernel with the external toolchain
  --freebsd-mips/no-use-external-toolchain-for-world
                        Do not build world with the external toolchain
  --freebsd-mips/no-debug-info
                        Do not pass make flags for building with debug info
  --freebsd-mips/build-tests
                        Build the tests too (-DWITH_TESTS) (default: 'False')
  --freebsd-mips/no-auto-obj
                        Do not use -DWITH_AUTO_OBJ (experimental)
  --freebsd-mips/minimal
                        Don't build all of FreeBSD, just what is needed for running most CHERI tests/benchmarks
                        (default: 'False')
  --freebsd-mips/fast   Skip some (usually) unnecessary build steps to speed up rebuilds (default: 'False')

Options for target 'disk-image-freebsd-x86':
  --disk-image-freebsd-x86/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image-freebsd-x86/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-x86-alr48')
  --disk-image-freebsd-x86/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image-freebsd-x86/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/freebsd-x86 .img')
```
