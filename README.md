# `cheribuild.py` - A script to build CHERI-related software (**requires Python 3.8+**)

This script automates all the steps required to build various [CHERI](http://www.chericpu.com)-related software.
For example `cheribuild.py [options] sdk-riscv64-purecap` will create an SDK that can be
used to compile software as pure-capability code for a CHERI-RISC-V CPU, and `cheribuild.py [options] run-riscv64-purecap`
will start an instance of [CheriBSD](https://github.com/CTSRD-CHERI/cheribsd) built as pure-capability code for CHERI-RISC-V in [QEMU](https://github.com/CTSRD-CHERI/qemu).

`cheribuild.py` also allows building software for Arm's adaption of CHERI, [the Morello platform](https://developer.arm.com/architectures/cpu-architecture/a-profile/morello), however not all targets are supported yet.

## Supported operating systems
`cheribuild.py` has been tested and should work on FreeBSD 12 and 13.
On Linux, Ubuntu 20.04/22.04, Debian 10/11/12 and OpenSUSE Tumbleweed are supported.
macOS 10.14 and newer is also supported.

# Pre-Build Setup

Some projects use meson, which requires Python `setuptools`. Since Python 3.12 removed `distutils`
from the standard library, you may need to install `setuptools` explicitly (e.g., `pip install setuptools`
or via your system package manager) to avoid build failures. Alternatively, use Python 3.11 or earlier.

#### macOS
When building on macOS the following commands will install the packages required for
the most commonly used cheribuild targets:
```shell
brew install cmake ninja libarchive git glib gnu-sed automake autoconf coreutils llvm lld ccache make wget pixman pkg-config xz texinfo mercurial libtool
# Install samba for shared mounts between host and CheriBSD on QEMU
brew install samba
# If you intend to run the morello FVP model you will also need the following:
brew install homebrew/cask/docker homebrew/cask/xquartz socat dtc
```


#### Debian/Ubuntu
If you are building CHERI on a Debian/Ubuntu-based machine, the following command will install the packages required for
the most commonly used cheribuild targets:

```shell
apt install autoconf automake libtool pkg-config clang bison cmake mercurial ninja-build samba flex texinfo time libglib2.0-dev libpixman-1-dev libarchive-dev libarchive-tools libbz2-dev libattr1-dev libcap-ng-dev libexpat1-dev libgmp-dev bc tzdata
```

Older versions of Ubuntu may report errors when trying to install `libarchive-tools`. In this case try using `apt install bsdtar` instead.

#### RHEL/Fedora
If you are building CHERI on a RHEL/Fedora-based machine, the following command will install the packages required for
the most commonly used cheribuild targets:

```shell
dnf install libtool clang-devel bison cmake mercurial ninja-build samba flex texinfo glib2-devel pixman-devel libarchive-devel bsdtar bzip2-devel libattr-devel libcap-ng-devel expat-devel time
```

#### FreeBSD
If you are building CHERI on a FreeBSD machine, the following command will install the packages required for
the most commonly used cheribuild targets:

```shell
pkg install autoconf automake bison cmake expat glib gsed libtool llvm mercurial meson ninja pkgconf pixman samba
```

#### Arch Linux
If you are building CHERI on an Arch Linux machine, the following command will install the packages required for
the most commonly used cheribuild targets:

```shell
pacman -Syu autoconf automake libtool pkgconf clang bison cmake ninja samba flex texinfo time glib2 pixman libarchive bzip2 attr libcap-ng inetutils mercurial expat gmp
```

# Contributing

If you would like to make a change to cheribuild, please submit it as a GitHub pull request to
https://github.com/CTSRD-CHERI/cheribuild.
Cheribuild includes some pre-commit and pre-push hooks (using
[pre-commit](https://github.com/pre-commit/pre-commit)) that can catch issues that will
fail the pull request CI checks. To install the hooks, run the following command
`python3 -m pip install -U --user pre-commit && pre-commit install -t pre-commit -t pre-push`.

# Basic usage

In these examples the `cheribuild.py` program is run from within the
`cheribuild/` directory as `./cheribuild.py` with options following
the main command.  Please note the leading `./` before the command
name.  It is also possible to add the `cheribuild/` directory to your
`PATH` or to link the binary somewhere such as in a `bin/` directory of
your choosing.

If you want to start up a QEMU VM running CheriBSD on top of the CHERI-RISC-V
ISA run the following command:

`./cheribuild.py run-riscv64-purecap -d` (-d means build all dependencies).

This command will build the CHERI compiler, QEMU, CheriBSD, create a
disk image and boot that in QEMU.

If you want to start up a QEMU VM running CheriBSD on top of the
Morello ISA use the following command:

`./cheribuild.py run-morello-purecap -d`

By default `cheribuild.py` will clone all projects in `~/cheri`, use `~/cheri/build` for build directories
and install into `~/cheri/output`. However, these directories are all configurable (see below for details).
When building for the first time, `cheribuild.py` will request user input multiple times, but the `--force`/`-f` flag can be used to accept the default.


If you would like to see what the script would do run it with the `--pretend` or `-p` option.
For even more detail you can also pass `--verbose` or `-v`.


It is also possible to run this script on a remote host by using the `remote-cheribuild.py` script that is included in this repository:
`remote-cheribuild.py my.remote.server [options] <targets...>` will run this script on `my.remote.server`.


# Usage

`cheribuild.py [options...] targets...`

Example: to build and run a pure-capability CheriBSD for RISC-V: `cheribuild.py --include-dependencies run-riscv64-purecap` and
for a clean verbose build of LLVM `cheribuild.py -v --clean llvm`

## Available Targets

When selecting a target you can also build all the targets that it depends on by passing the `--include-dependencies` or `-d` option.
However, some targets (e.g. `all`, `sdk-*`) will always build their dependencies because running them without building the dependencies does not make sense (see the list of targets for details).

#### Overview

There are many targets in cheribuild, and they fall into two categories: single
targets and multiarch targets.
Single targets, such as `qemu`, `sail` and `install-morello-fvp`, are for
targets that only apply natively to the host machine.
Most targets, however, are multiarch targets.
These are always of the form `<name>-<architecture>`, where `<architecture>`
indicates the architecture to compile/run code for, which will be
cross-compiling/emulating for anything other than `native`.
For example, `cheribsd-riscv64-purecap` will cross-compile CheriBSD for
pure-capability CHERI-RISC-V, `disk-image-morello-purecap` will create a
CheriBSD disk image for pure-capability Morello and `gdb-native` will build a
version of CHERI-GDB that runs natively on the host machine and be used to
remote debug CHERI-RISC-V and Morello.

Note that the `<architecture>` in the target says nothing about what it can do,
only where it can do it.
For example, `llvm-native` and `llvm-riscv64` both exist; the former is a
CHERI-LLVM that will run natively on the host and can be used to cross compile
for CHERI-RISC-V, whilst the latter is a CHERI-LLVM built
as a set of RISC-V binaries that will run on CheriBSD itself.

Where there are multiple variants of the source, there are multiple cheribuild
targets with different names which, in the case of multiarch targets, means a
different `<name>`.
For example, `llvm` refers to CHERI-LLVM, whereas `morello-llvm` refers to
Arm's fork of CHERI-LLVM adding Morello support, and `upstream-llvm` refers to
upstream's LLVM.
Thus, a target of `morello-llvm-riscv64` would be Arm's Morello-extended
CHERI-LLVM fork built as a RISC-V binary to run on CheriBSD.

#### The following main targets are available

- `qemu` builds and installs [CTSRD-CHERI/qemu](https://github.com/CTSRD-CHERI/qemu)
- `llvm` builds and installs the [CTSRD-CHERI/llvm-project](https://github.com/CTSRD-CHERI/llvm-project) toolchain (includes a compiler, linker, and all required binutils).
- `cheribsd-<architecture>` builds and installs [CTSRD-CHERI/cheribsd](https://github.com/CTSRD-CHERI/cheribsd) and creates a sysroot for cross-compilation.
- `disk-image-<architecture>` creates a CheriBSD disk-image.
- `run-<architecture>` launches QEMU with the CheriBSD disk image.
- `freestanding-sdk` builds everything required to build and run `-ffreestanding` binaries: compiler, linker and qemu
- `cheribsd-sdk-<architecture>` builds everything required to compile binaries for CheriBSD: `freestanding-sdk` and `cheribsd-sysroot`
- `sdk-<architecture>` is an alias for `cheribsd-sdk-<architecture>`
- `all-<architecture>`: runs all the targets listed so far (`run-<architecture>` comes last so that you can interact with QEMU)

##### Supported architectures
- `riscv64`: RISC-V without CHERI support
- `riscv64-purecap`: [pure-capability](https://www.cl.cam.ac.uk/techreports/UCAM-CL-TR-947.html) RISC-V: all pointers are CHERI capabilities.
- `aarch64`: AArch64 without CHERI support
- `morello-purecap`: [pure-capability](https://www.cl.cam.ac.uk/techreports/UCAM-CL-TR-947.html) AArch64 (Morello): all pointers are CHERI capabilities.
- `amd64`: 64-bit Intel x86.

Most projects (the ones that don't build a full OS, but just a program or library) also support `-native` configuration
  that builds for the host. This can be useful to verify that changes made for CHERI have not broken the native builds.

The following targets are also supported, but discouraged, as they serve little benefit over and above their non-CHERI counterparts, and so should not be used unless absolutely sure.
If you still wish to use them despite that, they can be enabled with `--enable-hybrid-targets`.
- `riscv64-hybrid`: RISC-V with CHERI support: pointers are integers by default but can be annotated with `__capability` to use CHERI capabilities.
- `morello-hybrid`: AArch64 with CHERI (Morello) support: pointers are integers by default but can be annotated with `__capability` to use CHERI capabilities.

For the `cheribsd`, `disk-image` and `run` targets the hybrid vs purecap distinction applies solely to userspace (see [below for more details](#building-and-running-cheribsd)).

#### Other targets
- `freebsd-<architecture>` builds and installs [freebsd/freebsd](https://github.com/freebsd/freebsd).
- `disk-image-freebsd-<architecture>` creates a FreeBSD disk-image.
- `run-freebsd-<architecture>` launches QEMU with the FreeBSD disk image.
- `cmake` builds and installs latest [CMake](https://github.com/Kitware/CMake)
- `cherios` builds and installs [CTSRD-CHERI/cherios](https://github.com/CTSRD-CHERI/cherios)
- `cheritrace` builds and installs [CTSRD-CHERI/cheritrace](https://github.com/CTSRD-CHERI/cheritrace)
- `sqlite-<architecture>` builds and installs [CTSRD-CHERI/sqlite](https://github.com/CTSRD-CHERI/sqlite)
- `nginx-<architecture>` builds and installs [CTSRD-CHERI/nginx](https://github.com/CTSRD-CHERI/nginx)
- `postgres-<architecture>` builds and installs [CTSRD-CHERI/postgres](https://github.com/CTSRD-CHERI/postgres)

## Building the compiler and QEMU

In order to run CheriBSD you will first need to compile QEMU (`cheribuild.py qemu`).
You will also need to build CHERI LLVM (this includes a compiler and linker suitable for CHERI) using `cheribuild.py llvm`.
The compiler can generate CHERI code for RISCV (32 and 64-bit).
All binaries will by default be installed to `~/cheri/sdk/bin`.

For Morello, build Morello LLVM using `cheribuild.py morello-llvm`.
Binaries from this compiler will be installed to `~/cheri/morello-sdk/bin`

## Building and running CheriBSD

To build CheriBSD run `cheribuild.py cheribsd-<architecture>`, with architecture being one of
- `riscv64`: Kernel and userspace are RISC-V without CHERI support.
- `riscv64-purecap`: Kernel is RISC-V with CHERI support (purecap), and all userspace programs built as pure-capability CHERI binaries.
- `aarch64`: Kernel and userspace are AArch64 without CHERI support.
- `morello-purecap`: Kernel is AArch64 with CHERI (Morello) support (purecap), and all userspace programs built as pure-capability CHERI binaries.
- `amd64`: Kernel and userspace are 64-bit Intel x86.

The following targets also exist but are disabled by default and discouraged (see [Supported architectures](#supported-architectures) for more details):
- `riscv64-hybrid`: Kernel is RISC-V with CHERI support (purecap), but most programs built as plain RISC-V.
- `morello-hybrid`: Kernel is AArch64 with CHERI (Morello) support (purecap), but most programs built as plain AArch64.

### Disk image

The disk image is created by the `cheribuild.py disk-image-<architecture>` target and can then be used as a boot disk by QEMU.

In order to customize the disk image it will add all files under (by default) `~/cheri/extra-files/`
to the resulting image. When building the image cheribuild will ask you whether it should add your
SSH public keys to the `/root/.ssh/authorized_keys` file in the CheriBSD image. It will also
generate SSH host keys for the image so that those don't change everytime the image is rebuilt.
A suitable `/etc/rc.conf` and `/etc/fstab` will also be added to this directory and can then be customized.

The default path for the disk image is `~/cheri/output/cheribsd-<architecture>.img`, i.e.
`cheribsd-riscv64-purecap.img` for pure-capability RISC-V or `cheribsd-aarch64.img` for AArch64 without CHERI support.

### CheriBSD SSH ports

Since cheribuild.py was designed to be run by multiple users on a shared build system, it will tell QEMU
to listen on a port on localhost that depends on the user ID to avoid conflicts.
It will print a message such as `Listening for SSH connections on localhost:12374`, i.e. you will need
to use `ssh -p 12374 root@localhost` to connect to CheriBSD.
This can be changed using `cheribuild.py --run/ssh-forwarding-port <portno> run-<architecture>` or be made persistent
with the following configuration file (see below for more details on the config file format and path):
```json
{
    "run-riscv64-hybrid": {
        "ssh-forwarding-port": 12345
    },
    "run-riscv64-purecap": {
        "ssh-forwarding-port": 12346
    }
}
```

### Speeding up SSH connections
Connecting to CheriBSD via ssh can take a few seconds. Further connections after the first can
be sped up by using the openssh ControlMaster setting:
```
Host cheribsd-riscv
  User root
  Port 12345
  HostName localhost
  ControlPath ~/.ssh/controlmasters/%r@%h:%p
  ControlMaster auto
  StrictHostKeyChecking no

Host cheribsd-riscv-purecap
  User root
  Port 12346
  HostName localhost
  ControlPath ~/.ssh/controlmasters/%r@%h:%p
  ControlMaster auto
  StrictHostKeyChecking no
```

## Building GDB

You can also build a [version of GDB that understands CHERI capabilities](https://github.com/CTSRD-CHERI/gdb)
either as a binary for the host (`cheribuild.py gdb-native`) to debug coredumps or as a guest binary to use
for live debugging in CheriBSD (`cheribuild.py gdb-morello-purecap` for Morello and `cheribuild.py gdb-riscv64-purecap` for RISC-V).
The guest binary will be installed in `usr/local/bin/gdb` under your CheriBSD rootfs and will be included
when you build a new disk image (`cheribuild.py disk-image-<arch>`).
The native GDB will be installed to your SDK binary directory (`~/cheri/sdk/bin` by default).

## Cross-compiling for CheriBSD

In order to cross-compile projects such as NGINX or PostgreSQL for CheriBSD you will first need a full SDK:
`cheribuild.py cheribsd-sdk-riscv64-purecap`. Then you can then run `cheribuild.py postgres-riscv64-purecap` or `cheribuild.py nginx-riscv64-purecap`, etc.
By default, these projects will be installed into your CheriBSD rootfs under `/opt` and will therefore be
automatically included the next time you build a disk image.

See `cheribuild.py --list-targets` for a full list of targets.

## Running inside Docker / CI Container

cheribuild includes support for running builds inside a Docker container using the `--docker` option.

For portable builds (e.g. for building portable native host compilers and toolchains), a pre-built, highly compatible Rocky Linux 8 based container image is built automatically by GitHub Actions and pushed to the GitHub Container Registry (GHCR). This image comes pre-installed with static versions of all foundational libraries (such as static `zlib`, `pixman`, `libffi`, `glib`, and `zstd`) to guarantee maximum portability of compiled binaries.

Other projects can easily pull and use this Docker image in their own CI pipelines:

```bash
docker pull ghcr.io/ctsrd-cheri/cheri-portable-build-container:latest
```

## Adapting the build configuration
There are a lot of options to customize the behaviour of this script: e.g. the directory for
the cloned sources can be changed from the default of `$HOME/cheri` using the `--source-root=` option.
A full list of the available options with descriptions can be found [towards the end of this document](#list-of-options---help-output).

The options can also be made persistent by storing them in a JSON config file (`~/.config/cheribuild.json`).
Options passed on the command line will override those read from the config file.
The key in the JSON config file is the same as the long option name without the intial `--`.
For example if you want cheribuild.py to behave as if you had passed
`--source-root /foo/bar/cheri/src --output-root /foo/bar/cheri/output --build-root /build -j 4 --cheribsd/build-options "-DWITHOUT_ZFS FOO=bar"`, you can write the following JSON to
`~/.config/cheribuild.json`:

```jsonc
{
  "source-root": "/foo/bar/cheri/src",
  // build-root defaults to <source-root>/build
  "build-root": "/build",
  // output-root defaults to <source-root>/output
  "output-root": "/foo/bar/cheri/output",
  "make-jobs": 4,
  "cheribsd": {
    "build-options": ["-DWITHOUT_ZFS", "FOO=bar"]
  }
}
```
### Prefixed cheribuild.py symlinks to select config file

If you invoke cheribuild.py as a prefixed command (e.g. debug-cheribuild.py, stable-cheribuild.py) it will
read the file `~/.config/{prefix}-cheribuild.json` instead. This makes it easy to build
debug and release builds of e.g. LLVM or build CheriBSD with various different flags.

### Including config files

If you have many config files (e.g. cheribsd-stable, -debug, -release, etc.) it is also
possible to `#include` a base config file and only write the settings that are different.

For example a `~/.config/stable-cheribuild.json` could look like this:

```json
{
	"build-root": "/build-stable",
	"#include": "cheribuild-common.json",
	"cheribsd": {
		"source-directory": "/my/other/cheribsd/worktree/with/the/stable/branch"
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

**NOTE:** On FreeBSD pip and setuptools are not installed by default, so you need to run
`python3 -m ensurepip --user` first.


### BASH
```
# NOTE: the next command doesn't seem to work on FreeBSD
~/.local/bin/activate-global-python-argcomplete --user
# On FreeBSD (or if the above doesn't work for some other reason) do this:
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

<!-- BEGIN HELP OUTPUT -->
```
usage: cheribuild.py [-h] [--help-all] [--config-file FILE] [--pretend] [--build] [--test] [--benchmark]
                     [--build-and-test] [--list-targets] [--dump-configuration] [--print-targets-only]
                     [--clang-path CLANG-PATH] [--clang++-path CLANG++-PATH] [--clang-cpp-path CLANG-CPP-PATH]
                     [--lld-path LLD-PATH] [--pass-k-to-make] [--with-libstatcounters | --no-with-libstatcounters]
                     [--skip-world | --no-skip-world | --skip-buildworld | --no-skip-buildworld]
                     [--skip-kernel | --no-skip-kernel | --skip-buildkernel | --no-skip-buildkernel]
                     [--freebsd-subdir SUBDIRS] [--buildenv] [--libcompat-buildenv] [--debug-output]
                     [--mips-float-abi {soft,hard}] [--riscv-float-abi {soft,hard}]
                     [--aarch64-fp-and-simd-options {default,nosimd,soft,soft_simd}]
                     [--cross-compile-linkage {default,dynamic,static}]
                     [--subobject-bounds {conservative,subobject-safe,aggressive,very-aggressive,everywhere-unsafe}]
                     [--use-cheri-ubsan | --no-use-cheri-ubsan]
                     [--use-cheri-ubsan-runtime | --no-use-cheri-ubsan-runtime]
                     [--subobject-debug | --no-subobject-debug]
                     [--clang-colour-diags | --no-clang-colour-diags | --clang-color-diags | --no-clang-color-diags]
                     [--use-sdk-clang-for-native-xbuild | --no-use-sdk-clang-for-native-xbuild]
                     [--configure-only | --no-configure-only] [--skip-install | --no-skip-install]
                     [--skip-build | --no-skip-build] [--skip-sdk | --no-skip-sdk] [--skip-dependency-filter REGEX]
                     [--trap-on-unrepresentable | --no-trap-on-unrepresentable]
                     [--qemu-gdb-break-on-cheri-trap | --no-qemu-gdb-break-on-cheri-trap]
                     [--qemu-gdb-debug-userspace-program QEMU-GDB-DEBUG-USERSPACE-PROGRAM]
                     [--only-dependencies | --no-only-dependencies] [--docker | --no-docker]
                     [--docker-container DOCKER-CONTAINER] [--docker-reuse-container | --no-docker-reuse-container]
                     [--portable-build | --no-portable-build]
                     [--compilation-db | --no-compilation-db | --cdb | --no-cdb]
                     [--wait-for-debugger | --no-wait-for-debugger]
                     [--debugger-in-tmux-pane | --no-debugger-in-tmux-pane] [--gdb-random-port | --no-gdb-random-port]
                     [--run-under-gdb | --no-run-under-gdb] [--test-ssh-key TEST-SSH-KEY]
                     [--use-minimal-benchmark-kernel | --no-use-minimal-benchmark-kernel] [--test-extra-args ARGS]
                     [--interact-after-tests] [--test-environment-only] [--test-ld-preload TEST-LD-PRELOAD]
                     [--benchmark-fpga-extra-args ARGS] [--benchmark-clean-boot | --no-benchmark-clean-boot]
                     [--benchmark-extra-args ARGS] [--benchmark-ssh-host BENCHMARK-SSH-HOST]
                     [--benchmark-csv-suffix BENCHMARK-CSV-SUFFIX] [--benchmark-ld-preload BENCHMARK-LD-PRELOAD]
                     [--benchmark-with-debug-kernel | --no-benchmark-with-debug-kernel]
                     [--benchmark-lazy-binding | --no-benchmark-lazy-binding]
                     [--benchmark-iterations BENCHMARK-ITERATIONS] [--benchmark-with-qemu | --no-benchmark-with-qemu]
                     [--shallow-clone | --no-shallow-clone] [--beri-fpga-env-setup-script BERI-FPGA-ENV-SETUP-SCRIPT]
                     [--arm-none-eabi-prefix ARM-NONE-EABI-PREFIX]
                     [--build-morello-firmware-from-source | --no-build-morello-firmware-from-source]
                     [--list-kernels | --no-list-kernels] [--remote-morello-board REMOTE-MORELLO-BOARD]
                     [--get-config-option KEY] [--quiet | --no-quiet | -q] [--verbose | --no-verbose | -v]
                     [--clean | --no-clean | -c] [--force | --no-force | -f] [--logfile | --no-logfile]
                     [--skip-update | --no-skip-update] [--confirm-clone | --no-confirm-clone]
                     [--force-update | --no-force-update] [--presume-connectivity | --no-presume-connectivity]
                     [--skip-configure | --no-skip-configure |
                     --reconfigure | --no-reconfigure | --force-configure | --no-force-configure]
                     [--include-dependencies] [--include-toolchain-dependencies | --no-include-toolchain-dependencies]
                     [--start-with TARGET | --start-after TARGET] [--compilation-db-in-source-dir]
                     [--generate-cmakelists | --no-generate-cmakelists] [--make-without-nice | --no-make-without-nice]
                     [--make-jobs MAKE-JOBS] [--source-root SOURCE-ROOT] [--output-root OUTPUT-ROOT]
                     [--build-root BUILD-ROOT] [--tools-root TOOLS-ROOT] [--morello-sdk-root MORELLO-SDK-ROOT]
                     [--rvy-sdk-root RVY-SDK-ROOT] [--cheri-std093-sdk-root CHERI-STD093-SDK-ROOT]
                     [--sysroot-install-root SYSROOT-INSTALL-ROOT] [--upstream-qemu/targets UPSTREAM-QEMU/TARGETS]
                     [--qemu/targets QEMU/TARGETS] [--qemu/statistics | --qemu/no-statistics]
                     [--cheri-std093-qemu/targets CHERI-STD093-QEMU/TARGETS]
                     [--cheri-std093-qemu/statistics | --cheri-std093-qemu/no-statistics]
                     [--freebsd-universe/build-tests | --freebsd-universe/no-build-tests]
                     [--freebsd-universe/build-options OPTIONS] [--freebsd-universe/extra-env ENV]
                     [--freebsd-universe/debug-info | --freebsd-universe/no-debug-info]
                     [--cheribsd-universe/build-tests | --cheribsd-universe/no-build-tests]
                     [--cheribsd-universe/build-options OPTIONS] [--cheribsd-universe/extra-env ENV]
                     [--cheribsd-universe/debug-info | --cheribsd-universe/no-debug-info]
                     [--freebsd-tinderbox/build-tests | --freebsd-tinderbox/no-build-tests]
                     [--freebsd-tinderbox/build-options OPTIONS] [--freebsd-tinderbox/extra-env ENV]
                     [--freebsd-tinderbox/debug-info | --freebsd-tinderbox/no-debug-info]
                     [--cheribsd-tinderbox/build-tests | --cheribsd-tinderbox/no-build-tests]
                     [--cheribsd-tinderbox/build-options OPTIONS] [--cheribsd-tinderbox/extra-env ENV]
                     [--cheribsd-tinderbox/debug-info | --cheribsd-tinderbox/no-debug-info]
                     [--run-rtems/use-opensbi-fw-payload | --run-rtems/no-use-opensbi-fw-payload]
                     [TARGET ...]

positional arguments:
  TARGET                The targets to build

options:
  -h, --help            show this help message and exit
  --help-all, --help-hidden
                        Show all help options, including the target-specific ones.
  --pretend, -p         Only print the commands instead of running them (default: 'False')
  --pass-k-to-make, -k  Pass the -k flag to make to continue after the first error (default: 'False')
  --debug-output, -vv   Extremely verbose output (default: 'False')
  --clang-colour-diags, --no-clang-colour-diags, --clang-color-diags, --no-clang-color-diags
                        Force CHERI clang to emit coloured diagnostics (default: 'True')
  --configure-only, --no-configure-only
                        Only run the configure step (skip build and install) (default: 'False')
  --skip-install, --no-skip-install
                        Skip the install step (only do the build) (default: 'False')
  --skip-build, --no-skip-build
                        Skip the build step (only do the install) (default: 'False')
  --only-dependencies, --no-only-dependencies
                        Only build dependencies of targets, not the targets themselves (default: 'False')
  --portable-build, --no-portable-build
                        Configure dependencies to be linked statically where possible to build portable host binaries
                        (default: 'False')
  --compilation-db, --no-compilation-db, --cdb, --no-cdb
                        Create a compile_commands.json file in the build dir (requires Bear for non-CMake projects)
                        (default: 'False')
  --shallow-clone, --no-shallow-clone
                        Perform a shallow `git clone` when cloning new projects. This can save a lot of time for
                        largerepositories such as FreeBSD or LLVM. Use `git fetch --unshallow` to convert to a non-
                        shallow clone (default: 'True')
  --build-morello-firmware-from-source, --no-build-morello-firmware-from-source
                        Build the firmware from source instead of downloading the latest release. (default: 'False')
  --remote-morello-board REMOTE-MORELLO-BOARD
                        SSH hostname of a Morello board. When set, some projects will run their test suites on the
                        remote board instead of QEMU.
  --quiet, --no-quiet, -q
                        Don't show stdout of the commands that are executed (default: 'False')
  --verbose, --no-verbose, -v
                        Print all commmands that are executed (default: 'False')
  --clean, --no-clean, -c
                        Remove the build directory before build (default: 'False')
  --force, --no-force, -f
                        Don't prompt for user input but use the default action (default: 'False')
  --logfile, --no-logfile
                        Write a logfile for the build steps (default: 'False')
  --skip-update, --no-skip-update
                        Skip the git pull step (default: 'False')
  --confirm-clone, --no-confirm-clone
                        Ask for confirmation before cloning repositories. (default: 'False')
  --force-update, --no-force-update
                        Always update (with autostash) even if there are uncommitted changes (default: 'False')
  --presume-connectivity, --no-presume-connectivity
                        Do not probe for network connectivity and just assume that we are suitably connected (default:
                        'False')
  --skip-configure, --no-skip-configure
                        Skip the configure step (default: 'False')
  --reconfigure, --no-reconfigure, --force-configure, --no-force-configure
                        Always run the configure step, even for CMake projects with a valid cache. (default: 'False')
  --compilation-db-in-source-dir
                        Generate a compile_commands.json and also copy it to the source directory (default: 'False')
  --generate-cmakelists, --no-generate-cmakelists
                        Generate a CMakeLists.txt that just calls cheribuild. Useful for IDEs that only support CMake
                        (default: 'False')
  --make-without-nice, --no-make-without-nice
                        Run make/ninja without nice(1) (default: 'False')
  --make-jobs, -j MAKE-JOBS
                        Number of jobs to use for compiling (default: '<system-dependent>')

Actions to be performed:
  --build               Run (usually build+install) chosen targets (default)
  --test, --run-tests   Run tests for the passed targets instead of building them
  --benchmark           Run tests for the passed targets instead of building them
  --build-and-test      Run chosen targets and then run any tests afterwards
  --list-targets        List all available targets and exit
  --dump-configuration  Print the current configuration as JSON. This can be saved to ~/.config/cheribuild.json to make
                        it persistent
  --print-targets-only  Don't run the build but instead only print the targets that would be executed (default: 'False')
  --list-kernels, --no-list-kernels
                        List available kernel configs to run and exit (default: 'False')
  --get-config-option KEY
                        Print the value of config option KEY and exit

Selecting which dependencies are built:
  --skip-sdk, --no-skip-sdk
                        When building with --include-dependencies ignore the SDK dependencies. Saves a lot of time when
                        building libc++, etc. with dependencies but the sdk is already up-to-date. This is like --no-
                        include-toolchain-depedencies but also skips the target that builds the sysroot. (default:
                        'False')
  --skip-dependency-filter REGEX
                        A regular expression to match against to target names that should be skipped when using--
                        include-dependency. Can be passed multiple times to add more patterns. (default: '[]')
  --include-dependencies, -d
                        Also build the dependencies of targets passed on the command line. Targets passed on the command
                        line will be reordered and processed in an order that ensures dependencies are built before the
                        real target. (run --list-targets for more information). By default this does not build toolchain
                        targets such as LLVM. Pass --include-toolchain-dependencies to also build those. (default:
                        'False')
  --include-toolchain-dependencies, --no-include-toolchain-dependencies
                        Include toolchain targets such as LLVM and QEMU when --include-dependencies is set. (default:
                        'True')
  --start-with TARGET   Start building at TARGET (useful when resuming an interrupted --include-depedencies build)
  --start-after TARGET  Start building after TARGET (useful when resuming an interrupted --include-depedencies build)

Configuration of default paths:
  --config-file FILE    The config file that is used to load the default settings (default:
                        '$HOME/.config/cheribuild.json')
  --clang-path, --cc-path CLANG-PATH
                        The C compiler to use for host binaries (must be compatible with Clang >= 3.7)
  --clang++-path, --c++-path CLANG++-PATH
                        The C++ compiler to use for host binaries (must be compatible with Clang >= 3.7)
  --clang-cpp-path, --cpp-path CLANG-CPP-PATH
                        The C preprocessor to use for host binaries (must be compatible with Clang >= 3.7)
  --lld-path, --ld-path LLD-PATH
                        The linker to use for host binaries (must be compatible with LLD >= 3.7) (default: 'matching lld
                        or ld')
  --beri-fpga-env-setup-script BERI-FPGA-ENV-SETUP-SCRIPT
                        Custom script to source to setup PATH and quartus, default to using cheri-cpu/cheri/setup.sh
  --arm-none-eabi-prefix ARM-NONE-EABI-PREFIX
                        Prefix for arm-none-eabi-gcc binaries (e.g. /usr/bin/arm-none-eabi-). Available
                        athttps://developer.arm.com/tools-and-software/open-source-software/developer-tools/gnu-
                        toolchain/gnu-rm/downloads (default: '')
  --source-root SOURCE-ROOT
                        The directory to store all sources (default: '$HOME/cheri')
  --output-root OUTPUT-ROOT
                        The directory to store all output (default: '<SOURCE_ROOT>/output')
  --build-root BUILD-ROOT
                        The directory for all the builds (default: '<SOURCE_ROOT>/build')
  --tools-root TOOLS-ROOT
                        The directory to find sdk and bootstrap tools (default: '<OUTPUT_ROOT>')
  --morello-sdk-root MORELLO-SDK-ROOT
                        The directory to find/install the Morello SDK (default: ''<TOOLS_ROOT>/morello-sdk'')
  --rvy-sdk-root RVY-SDK-ROOT
                        The directory to find/install the CHERI RVY SDK (default: ''<TOOLS_ROOT>/rvy-sdk'')
  --cheri-std093-sdk-root CHERI-STD093-SDK-ROOT
                        The directory to find/install the CHERI Alliance SDK (default: ''<TOOLS_ROOT>/cheri-
                        std093-sdk'')
  --sysroot-install-root, --sysroot-install-dir SYSROOT-INSTALL-ROOT
                        Sysroot prefix (default: '<TOOLS_ROOT>')

Adjust flags used when compiling MIPS/CHERI projects:
  --with-libstatcounters, --no-with-libstatcounters
                        Link cross compiled CHERI project with libstatcounters. (default: 'False')
  --mips-float-abi {soft,hard}
                        Whether to use soft or hard float ABIs when targeting MIPS (default: 'soft')
  --riscv-float-abi {soft,hard}
                        Whether to use soft or hard float ABIs when targeting RISC-V (default: 'hard')
  --aarch64-fp-and-simd-options {default,nosimd,soft,soft_simd}
                        The floating point/SIMD mode to use for building AArch64 programs (default: 'default')
  --cross-compile-linkage {default,dynamic,static}
                        Whether to link cross-compile projects static or dynamic by default (default: 'default')
  --subobject-bounds {conservative,subobject-safe,aggressive,very-aggressive,everywhere-unsafe}
                        Whether to add additional CSetBounds to subobject references/&-operator
  --use-cheri-ubsan, --no-use-cheri-ubsan
                        Add compiler flags to detect certain undefined CHERI behaviour at runtime (default: 'False')
  --use-cheri-ubsan-runtime, --no-use-cheri-ubsan-runtime
                        Use the UBSan runtime to provide more detailed information on undefined CHERI behaviour.If false
                        (the default) the compiler will generate a trap instruction instead. (default: 'False')
  --subobject-debug, --no-subobject-debug
                        Clear software permission bit 2 when subobject bounds reduced size (Note: this should be turned
                        off for benchmarks!) (default: 'True')
  --use-sdk-clang-for-native-xbuild, --no-use-sdk-clang-for-native-xbuild
                        Compile cross-compile project with CHERI clang from the SDK instead of host compiler (default:
                        'False')

Configuration for running tests:
  --test-ssh-key TEST-SSH-KEY
                        The SSH key to used to connect to the QEMU instance when running tests on CheriBSD. If not
                        specified a key will be generated in the build-root directory on-demand.
  --use-minimal-benchmark-kernel, --no-use-minimal-benchmark-kernel
                        Use a CHERI BENCHMARK version of the cheribsd-mfs-root-kernel (without INVARIATES) for the run-
                        minimal target and for tests. This can speed up longer running tests. This is the default for
                        PostgreSQL and libc++ tests (passing use-minimal-benchmark-kernel can force these tests to use
                        an INVARIANTS kernel). (default: 'False')
  --test-extra-args ARGS
                        Additional flags to pass to the test script in --test (default: '[]')
  --interact-after-tests
                        Interact with the CheriBSD instance after running the tests on QEMU (only for --test) (default:
                        'False')
  --test-environment-only
                        Don't actually run the tests. Instead setup a QEMU instance with the right paths set up.
                        (default: 'False')
  --test-ld-preload TEST-LD-PRELOAD
                        Preload the given library before running tests

Configuration for running benchmarks:
  --benchmark-fpga-extra-args ARGS
                        Extra options for the FPGA management script (default: '[]')
  --benchmark-clean-boot, --no-benchmark-clean-boot
                        Reboot the FPGA with a new bitfile and kernel before running benchmarks. If not set, assume the
                        FPGA is running. (default: 'False')
  --benchmark-extra-args ARGS
                        Additional flags to pass to the program executed in --benchmark (default: '[]')
  --benchmark-ssh-host BENCHMARK-SSH-HOST
                        The SSH hostname/IP for the benchmark FPGA (default: 'cheri-fpga')
  --benchmark-csv-suffix BENCHMARK-CSV-SUFFIX
                        Add a custom suffix for the statcounters CSV.
  --benchmark-ld-preload BENCHMARK-LD-PRELOAD
                        Preload the given library before running benchmarks
  --benchmark-with-debug-kernel, --no-benchmark-with-debug-kernel
                        Run the benchmark with a kernel that has assertions enabled. (default: 'False')
  --benchmark-lazy-binding, --no-benchmark-lazy-binding
                        Run the benchmark without setting LD_BIND_NOW. (default: 'False')
  --benchmark-iterations BENCHMARK-ITERATIONS
                        Override the number of iterations for the benchmark. Note: not all benchmarks support this
                        option
  --benchmark-with-qemu, --no-benchmark-with-qemu
                        Run the benchmarks on QEMU instead of the FPGA (only useful to collect instruction counts or
                        test the benchmarks) (default: 'False')

Configuration for launching QEMU (and other simulators):
  --trap-on-unrepresentable, --no-trap-on-unrepresentable
                        Raise a CHERI exception when capabilities become unreprestable instead of detagging. Useful for
                        debugging, but deviates from the spec, and therefore off by default. (default: 'False')
  --qemu-gdb-break-on-cheri-trap, --no-qemu-gdb-break-on-cheri-trap
                        Drop into GDB attached to QEMU when a CHERI exception is triggered (QEMU only). (default:
                        'False')
  --qemu-gdb-debug-userspace-program QEMU-GDB-DEBUG-USERSPACE-PROGRAM
                        Print the command to debug the following userspace program in GDB attaced to QEMU
  --wait-for-debugger, --no-wait-for-debugger
                        Start QEMU in the 'wait for a debugger' state whenlaunching CheriBSD,FreeBSD, etc. (default:
                        'False')
  --debugger-in-tmux-pane, --no-debugger-in-tmux-pane
                        Start Qemu and gdb in another tmux split (default: 'False')
  --gdb-random-port, --no-gdb-random-port
                        Wait for gdb using a random port (default: 'True')
  --run-under-gdb, --no-run-under-gdb
                        Run tests/benchmarks under GDB. Note: currently most targets ignore this flag. (default:
                        'False')

FreeBSD and CheriBSD build configuration:
  --skip-world, --no-skip-world, --skip-buildworld, --no-skip-buildworld
                        Skip the buildworld-related steps when building FreeBSD or CheriBSD (default: 'False')
  --skip-kernel, --no-skip-kernel, --skip-buildkernel, --no-skip-buildkernel
                        Skip the buildkernel step when building FreeBSD or CheriBSD (default: 'False')
  --freebsd-subdir, --subdir SUBDIRS
                        Only build subdirs SUBDIRS of FreeBSD/CheriBSD instead of the full tree. Useful for quickly
                        rebuilding individual programs/libraries. If more than one dir is passed they will be processed
                        in order. Note: This will break if not all dependencies have been built. (default: '[]')
  --buildenv            Open a shell with the right environment for building the project. Currently only works for
                        FreeBSD/CheriBSD (default: 'False')
  --libcompat-buildenv, --libcheri-buildenv
                        Open a shell with the right environment for building compat libraries. (default: 'False')

Options controlling the use of docker for building:
  --docker, --no-docker
                        Run the build inside a docker container (default: 'False')
  --docker-container DOCKER-CONTAINER
                        Name of the docker container to use (default: 'ctsrd/cheribuild-docker')
  --docker-reuse-container, --no-docker-reuse-container
                        Attach to the same container again (note: docker-container option must be an id rather than a
                        container name (default: 'False')

Options for target 'upstream-qemu':
  --upstream-qemu/targets UPSTREAM-QEMU/TARGETS
                        Build QEMU for the following targets (default: 'QEMU default targets')

Options for target 'qemu':
  --qemu/targets QEMU/TARGETS
                        Build QEMU for the following targets (default: 'QEMU default targets')
  --qemu/statistics, --qemu/no-statistics
                        Collect statistics on out-of-bounds capability creation. (default: 'False')

Options for target 'cheri-std093-qemu':
  --cheri-std093-qemu/targets CHERI-STD093-QEMU/TARGETS
                        Build QEMU for the following targets (default: 'QEMU default targets')
  --cheri-std093-qemu/statistics, --cheri-std093-qemu/no-statistics
                        Collect statistics on out-of-bounds capability creation. (default: 'False')

Options for target 'freebsd-universe':
  --freebsd-universe/build-tests, --freebsd-universe/no-build-tests
                        Build the tests (default: 'True')
  --freebsd-universe/build-options OPTIONS
                        Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf`
                        for more info. (default: '[]')
  --freebsd-universe/extra-env ENV
                        Additional make env to be passed to make when building FreeBSD/CheriBSD. See `man src-env.conf`
                        for more info. (default: '[]')
  --freebsd-universe/debug-info, --freebsd-universe/no-debug-info
                        pass make flags for building with debug info (default: 'True')

Options for target 'cheribsd-universe':
  --cheribsd-universe/build-tests, --cheribsd-universe/no-build-tests
                        Build the tests (default: 'True')
  --cheribsd-universe/build-options OPTIONS
                        Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf`
                        for more info. (default: '[]')
  --cheribsd-universe/extra-env ENV
                        Additional make env to be passed to make when building FreeBSD/CheriBSD. See `man src-env.conf`
                        for more info. (default: '[]')
  --cheribsd-universe/debug-info, --cheribsd-universe/no-debug-info
                        pass make flags for building with debug info (default: 'True')

Options for target 'freebsd-tinderbox':
  --freebsd-tinderbox/build-tests, --freebsd-tinderbox/no-build-tests
                        Build the tests (default: 'True')
  --freebsd-tinderbox/build-options OPTIONS
                        Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf`
                        for more info. (default: '[]')
  --freebsd-tinderbox/extra-env ENV
                        Additional make env to be passed to make when building FreeBSD/CheriBSD. See `man src-env.conf`
                        for more info. (default: '[]')
  --freebsd-tinderbox/debug-info, --freebsd-tinderbox/no-debug-info
                        pass make flags for building with debug info (default: 'True')

Options for target 'cheribsd-tinderbox':
  --cheribsd-tinderbox/build-tests, --cheribsd-tinderbox/no-build-tests
                        Build the tests (default: 'True')
  --cheribsd-tinderbox/build-options OPTIONS
                        Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf`
                        for more info. (default: '[]')
  --cheribsd-tinderbox/extra-env ENV
                        Additional make env to be passed to make when building FreeBSD/CheriBSD. See `man src-env.conf`
                        for more info. (default: '[]')
  --cheribsd-tinderbox/debug-info, --cheribsd-tinderbox/no-debug-info
                        pass make flags for building with debug info (default: 'True')

Options for target 'run-rtems':
  --run-rtems/use-opensbi-fw-payload, --run-rtems/no-use-opensbi-fw-payload
                        Force QEMU to use OpenSBI's fw_payload variant instead of fw_jump. This is only for RISC-V.
                        (default: 'False')
```
<!-- END HELP OUTPUT -->
