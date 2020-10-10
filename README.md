# `cheribuild.py` - A script to build CHERI-related software (**requires Python 3.5.2+**)

This script automates all the steps required to build various [CHERI](http://www.chericpu.com)-related software.
For example `cheribuild.py [options] sdk` will create a SDK that can be
used to compile software for the CHERI CPU and `cheribuild.py [options] run-riscv64-purecap`
will start an instance of [CheriBSD](https://github.com/CTSRD-CHERI/cheribsd) built for RISC-V in [QEMU](https://github.com/CTSRD-CHERI/qemu).

`cheribuild.py` also allows building software Arm's adaption of CHERI, [the Morello platform](https://developer.arm.com/architectures/cpu-architecture/a-profile/morello), however not all targets are supported yet.

## Supported operating systems
`cheribuild.py` has been tested and should work on FreeBSD 11 and 12.
On Linux, Ubuntu 16.04, Ubuntu 18.04 and OpenSUSE Tumbleweed are supported. Ubuntu 14.04 may also work but is no longer tested.
macOS 10.14 and newer is also supported.

# Pre-Build Setup

If you are building CHERI on a Debian/Ubuntu-based machine, please install the following packages:

```shell
apt-get install libtool pkg-config clang bison cmake ninja-build samba flex texinfo libglib2.0-dev libpixman-1-dev libarchive-dev libarchive-tools libbz2-dev libattr1-dev libcap-ng-dev
```

Older versions of Ubuntu may report errors when trying to install `libarchive-tools`. In this case try using `apt-get install bsdtar` instead.

If you are building CHERI on a RHEL/Fedora-based machine, please install the following packages:

```shell
dnf install libtool clang-devel bison cmake ninja-build samba flex texinfo glib2-devel pixman-devel libarchive-devel bsdtar bzip2-devel libattr-devel libcap-ng-devel expat-devel
```

# Basic usage

If you want to start up a QEMU VM running CheriBSD run `cheribuild.py run-riscv64-purecap -d` (-d means build all dependencies).
If you would like the VM to have all userspace binaries to be built as plain RISC-V binaries instead of CHERI pure-capability ones use `cheribuild.py run-riscv64-hybrid -d`.
This will build the CHERI compiler, QEMU, CheriBSD, create a disk image and boot that in QEMU.

By default `cheribuild.py` will clone all projects in `~/cheri`, use `~/cheri/build` for build directories
and install into `~/cheri/output`. However, these directories are all configurable (see below for details).
When building for the first time, `cheribuild.py` will request user input multiple times, but the `--force`/`-f` flag can be used to accept the default.


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
- `llvm` builds and installs the [CTSRD-CHERI/llvm-project](https://github.com/CTSRD-CHERI/llvm-project) toolchain (includes compiler, linker, and all required binutils).
- `cheribsd-<architecture>` builds and installs [CTSRD-CHERI/cheribsd](https://github.com/CTSRD-CHERI/cheribsd) and creates a sysroot for cross-compilation.
- `disk-image-<architecture>` creates a CheriBSD disk-image.
- `run-<architecture>` launches QEMU with the CheriBSD disk image.
- `freestanding-sdk` builds everything required to build and run `-ffreestanding` binaries: compiler, linker and qemu
- `cheribsd-sdk-<architecture>` builds everything required to compile binaries for CheriBSD: `freestanding-sdk` and `cheribsd-sysroot`
- `sdk-<architecture>` is an alias for `cheribsd-sdk-<architecture>`
- `all-<architecture>`: runs all the targets listed so far (`run-<architecture>` comes last so you can interact with QEMU)

##### Supported architectures
- `riscv64`: RISC-V without CHERI support
- `riscv64-hybrid`: RISC-V with CHERI support: pointers are integers by default but can be annotated with `__capability` to use CHERI capabilities.
- `riscv64-purecap`: [pure-capability](https://www.cl.cam.ac.uk/techreports/UCAM-CL-TR-947.html) RISC-V: all pointers are CHERI capabilities.
- `mips64`: MIPS without CHERI support
- `mips64-hybrid`: MIPS with CHERI support: pointers are integers by default but can be annotated with `__capability` to use CHERI capabilities.
- `mips64-purecap`: [pure-capability](https://www.cl.cam.ac.uk/techreports/UCAM-CL-TR-947.html) MIPS: all pointers are CHERI capabilities.
- `aarch64`: AArch64 without CHERI support
- `morello-hybrid`: AArch64 with CHERI (Morello) support: pointers are integers by default but can be annotated with `__capability` to use CHERI capabilities.
- `morello-purecap`: [pure-capability](https://www.cl.cam.ac.uk/techreports/UCAM-CL-TR-947.html) AArch64 (Morello): all pointers are CHERI capabilities.
- `amd64`: 64-bit Intel x86.

Most projects (the ones that don't build a full OS, but just a program or library) also support `-native` configuration
  that builds for the host. This can be useful to verify that changes made for CHERI have not broken the native builds.

For the `cheribsd`, `disk-image` and `run` targets the hybrid vs purecap distinction applies means that the userspace space (see [below for more details](#building-and-running-cheribsd)).

#### Other targets
- `cmake` builds and installs latest [CMake](https://github.com/Kitware/CMake)
- `cherios` builds and installs [CTSRD-CHERI/cherios](https://github.com/CTSRD-CHERI/cherios)
- `cheritrace` builds and installs [CTSRD-CHERI/cheritrace](https://github.com/CTSRD-CHERI/cheritrace)
- `sqlite-<architecture>` builds and installs [CTSRD-CHERI/sqlite](https://github.com/CTSRD-CHERI/sqlite)
- `nginx-<architecture>` builds and installs [CTSRD-CHERI/nginx](https://github.com/CTSRD-CHERI/nginx)
- `postgres-<architecture>` builds and installs [CTSRD-CHERI/postgres](https://github.com/CTSRD-CHERI/postgres)

## Building the compiler and QEMU

In order to run CheriBSD you will first need to compile QEMU (`cheribuild.py qemu`).
You will also need to build LLVM (this includes a compiler and linker suitable for CHERI) using `cheribuild.py llvm`.
The compiler can generate CHERI code for MIPS (64-bit only) and RISCV (32 and 64-bit).
All binaries will by default be installed to `~/cheri/sdk/bin`.

## Building and running CheriBSD

To build CheriBSD run `cheribuild.py cheribsd-<architecture>`, with architecture being one of
- `riscv64`: Kernel and userspace are RISC-V without CHERI support.
- `riscv64-hybrid`: Kernel is RISC-V with CHERI support (hybrid), but most programs built as plain RISC-V.
- `riscv64-purecap`: Kernel is RISC-V with CHERI support (hybrid), and all userspace programs built as pure-capability CHERI binaries.
- `mips64`: Kernel and userspace are MIPS without CHERI support.
- `mips64-hybrid`: Kernel is MIPS with CHERI support (hybrid), but most programs built as plain RISC-V.
- `mips64-purecap`: Kernel is MIPS with CHERI support (hybrid), and all userspace programs built as pure-capability CHERI binaries.
- `aarch64`: Kernel and userspace are AArch64 without CHERI support.
- `morello-hybrid`: Kernel is AArch64 with CHERI (Morello) support (hybrid), but most programs built as plain AArch64.
- `morello-purecap`: Kernel is AArch64 with CHERI (Morello) support (hybrid), and all userspace programs built as pure-capability CHERI binaries.
- `amd64`: Kernel and userspace are 64-bit Intel x86.

### Disk image

The disk image is created by the `cheribuild.py disk-image-<architecture>` target and can then be used as a boot disk by QEMU.

In order to customize the disk image it will add all files under (by default) `~/cheri/extra-files/`
to the resulting image. When building the image cheribuild will ask you whether it should add your
SSH public keys to the `/root/.ssh/authorized_keys` file in the CheriBSD image. It will also
generate SSH host keys for the image so that those don't change everytime the image is rebuilt.
A suitable `/etc/rc.conf` and `/etc/fstab` will also be added to this directory and can then be customized.

The default path for the disk image is `~/cheri/output/cheribsd-<architecture>.img`, i.e.
`cheribsd-riscv64-purecap.img` for pure-capability RISC-V or `cheribsd-mips64.img` for MIPS without CHERI support.

### CheriBSD SSH ports

Since cheribuild.py was designed to be run by multiple users on a shared build system it will tell QEMU
to listen on a port on localhost that depends on the user ID to avoid conflicts.
It will print a message such as `Listening for SSH connections on localhost:12374`, i.e. you will need
to use `ssh -p 12374 root@localhost` to connect to CheriBSD.
This can be changed using `cheribuild.py --run/ssh-forwarding-port <portno> run-<architecture>` or be made persistent
with the following configuration file (see below for more details on the config file format and path):
```json
{
    "run-riscv64-hybrid": {
        "ssh-forwarding-port": 12345
    }, "run-riscv64-purecap": {
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

You can also build a [version of GDB that understands CHERI capabilities](https://github.com/bsdjhb/gdb/tree/mips_cheri-8.0.1)
either as a binary for the host (`cheribuild.py gdb-native`) to debug coredumps or as a gues binary to use
for live debugging in CheriBSD (`cheribuild.py gdb-mips64-hybrid` for MIPS and `cheribuild.py gdb-riscv64-hybrid` for RISC-V).
The guest binary will be installed in `usr/local/bin/gdb` under your CheriBSD rootfs and will be included
when you build a new disk image (`cheribuild.py disk-image-<arch>`).
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
A full list of the available options with descriptions can be found [towards the end of this document](#list-of-options---help-output).

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
                     [--freebsd-subdir SUBDIRS] [--no-install-subdir-to-sysroot] [--buildenv] [--libcompat-buildenv]
                     [--debug-output] [--mips-float-abi {soft,hard}] [--cross-compile-linkage {dynamic,static}]
                     [--subobject-bounds {conservative,subobject-safe,aggressive,very-aggressive,everywhere-unsafe}]
                     [--no-subobject-debug] [--no-clang-colour-diags] [--use-sdk-clang-for-native-xbuild]
                     [--configure-only] [--skip-install] [--skip-build] [--skip-sdk] [--trap-on-unrepresentable]
                     [--qemu-gdb-break-on-cheri-trap]
                     [--qemu-gdb-debug-userspace-program QEMU_GDB_DEBUG_USERSPACE_PROGRAM] [--docker]
                     [--docker-container DOCKER_CONTAINER] [--docker-reuse-container] [--compilation-db]
                     [--wait-for-debugger] [--debugger-in-tmux-pane] [--no-gdb-random-port] [--run-under-gdb]
                     [--test-ssh-key TEST_SSH_KEY] [--use-minimal-benchmark-kernel] [--test-extra-args ARGS]
                     [--interact-after-tests] [--test-environment-only] [--test-ld-preload TEST_LD_PRELOAD]
                     [--benchmark-fpga-extra-args ARGS] [--benchmark-clean-boot] [--benchmark-extra-args ARGS]
                     [--benchmark-ssh-host BENCHMARK_SSH_HOST] [--benchmark-csv-suffix BENCHMARK_CSV_SUFFIX]
                     [--benchmark-ld-preload BENCHMARK_LD_PRELOAD] [--benchmark-with-debug-kernel]
                     [--benchmark-lazy-binding] [--benchmark-iterations BENCHMARK_ITERATIONS] [--benchmark-with-qemu]
                     [--no-shallow-clone] [--beri-fpga-env-setup-script BERI_FPGA_ENV_SETUP_SCRIPT]
                     [--get-config-option KEY] [--quiet] [--verbose] [--clean] [--force] [--logfile] [--skip-update]
                     [--force-update  --skip-configure | --reconfigure] [--include-dependencies]
                     [--no-include-toolchain-dependencies] [--compilation-db-in-source-dir] [--generate-cmakelists]
                     [--make-without-nice] [--make-jobs MAKE_JOBS] [--source-root SOURCE_ROOT]
                     [--output-root OUTPUT_ROOT] [--build-root BUILD_ROOT] [--tools-root TOOLS_ROOT]
                     [--morello-sdk-root MORELLO_SDK_ROOT] [--sysroot-install-dir SYSROOT_INSTALL_DIR] [--qemu/gui]
                     [--qemu/targets QEMU/TARGETS] [--qemu/unaligned] [--qemu/statistics]
                     [--freebsd-universe/build-options OPTIONS] [--freebsd-universe/minimal]
                     [--freebsd-universe/no-build-tests] [--go/bootstrap-toolchain GO/BOOTSTRAP_TOOLCHAIN]
                     [--run-rtems/monitor-over-telnet PORT] [--sail-cheri-mips/trace-support]
                     [--sail-riscv/trace-support] [--sail-cheri-riscv/trace-support] [--cheri-syzkaller/run-sysgen]
                     [--run-syzkaller/syz-config RUN_SYZKALLER/SYZ_CONFIG]
                     [--run-syzkaller/ssh-privkey syzkaller_id_rsa] [--run-syzkaller/workdir DIR]
                     [--cheribsd/build-options OPTIONS] [--cheribsd/minimal] [--cheribsd/no-build-tests]
                     [--cheribsd/subdir SUBDIRS] [--cheribsd/kernel-config CONFIG] [--cheribsd/no-debug-info]
                     [--cheribsd/no-auto-obj] [--cheribsd/sysroot-only] [--cheribsd/build-fpga-kernels]
                     [--cheribsd/build-fett-kernels] [--cheribsd/pure-cap-kernel] [--cheribsd/caprevoke-kernel]
                     [--cheribsd-mfs-root-kernel/no-build-fpga-kernels] [--cheribsd-sysroot/remote-sdk-path PATH]
                     [--disk-image-minimal/extra-files DIR] [--disk-image-minimal/hostname HOSTNAME]
                     [--disk-image-minimal/remote-path PATH] [--disk-image-minimal/path IMGPATH]
                     [--disk-image/extra-files DIR] [--disk-image/hostname HOSTNAME] [--disk-image/remote-path PATH]
                     [--disk-image/path IMGPATH] [--disk-image-freebsd/extra-files DIR]
                     [--disk-image-freebsd/hostname HOSTNAME] [--disk-image-freebsd/remote-path PATH]
                     [--disk-image-freebsd/path IMGPATH] [--freertos/demo DEMO] [--freertos/prog PROG]
                     [--freertos/bsp BSP] [--run/monitor-over-telnet PORT] [--run/ssh-forwarding-port PORT]
                     [--run/remote-kernel-path RUN/REMOTE_KERNEL_PATH] [--run/skip-kernel-update]
                     [--run-freertos/monitor-over-telnet PORT] [--run-freertos/demo DEMO] [--run-freertos/prog PROG]
                     [--run-freertos/bsp BSP] [--run-minimal/monitor-over-telnet PORT]
                     [--run-minimal/ssh-forwarding-port PORT]
                     [--run-minimal/remote-kernel-path RUN_MINIMAL/REMOTE_KERNEL_PATH]
                     [--run-minimal/skip-kernel-update] [--bash/set-as-root-shell] [--qtbase-dev/no-build-tests]
                     [--qtbase-dev/build-examples] [--qtbase-dev/no-assertions] [--qtbase-dev/no-minimal]
                     [--qtwebkit/build-jsc-only]
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
  --beri-fpga-env-setup-script BERI_FPGA_ENV_SETUP_SCRIPT
                        Custom script to source to setup PATH and quartus, default to using cheri-cpu/cheri/setup.sh
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
                        Also build the dependencies of targets passed on the command line. Targets passed on the command
                        line will be reordered and processed in an order that ensures dependencies are built before the
                        real target. (run --list-targets for more information). By default this does not build toolchain
                        targets such as LLVM. Pass --include-toolchain-dependencies to also build those. (default:
                        'False')
  --no-include-toolchain-dependencies
                        Do not include toolchain targets such as LLVM and QEMU when --include-dependencies is set.
  --compilation-db-in-source-dir
                        Generate a compile_commands.json and also copy it to the source directory (default: 'False')
  --generate-cmakelists
                        Generate a CMakeLists.txt that just calls cheribuild. Useful for IDEs that only support CMake
                        (default: 'False')
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
                        '/usr/bin/clang')
  --clang++-path CLANG++_PATH, --c++-path CLANG++_PATH
                        The C++ compiler to use for host binaries (must be compatible with Clang >= 3.7) (default:
                        '/usr/bin/clang++')
  --clang-cpp-path CLANG_CPP_PATH, --cpp-path CLANG_CPP_PATH
                        The C preprocessor to use for host binaries (must be compatible with Clang >= 3.7) (default:
                        '/usr/bin/cpp')
  --source-root SOURCE_ROOT
                        The directory to store all sources (default: '/Users/alex/cheri')
  --output-root OUTPUT_ROOT
                        The directory to store all output (default: '<SOURCE_ROOT>/output')
  --build-root BUILD_ROOT
                        The directory for all the builds (default: '<SOURCE_ROOT>/build')
  --tools-root TOOLS_ROOT
                        The directory to find sdk and bootstrap tools (default: '<OUTPUT_ROOT>')
  --morello-sdk-root MORELLO_SDK_ROOT
                        The directory to find/install the Morello SDK (default: ''<OUTPUT_ROOT>/morello-sdk'')
  --sysroot-install-dir SYSROOT_INSTALL_DIR
                        Sysroot prefix (default: '<TOOLS_ROOT>')

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
  --debugger-in-tmux-pane
                        Start Qemu and gdb in another tmux split (default: 'False')
  --no-gdb-random-port  Do not wait for gdb using a random port
  --run-under-gdb       Run tests/benchmarks under GDB. Note: currently most targets ignore this flag. (default:
                        'False')

FreeBSD and CheriBSD build configuration:
  --skip-buildworld, --skip-world
                        Skip the buildworld step when building FreeBSD or CheriBSD (default: 'False')
  --freebsd-subdir SUBDIRS, --subdir SUBDIRS
                        Only build subdirs SUBDIRS of FreeBSD/CheriBSD instead of the full tree. Useful for quickly
                        rebuilding an individual programs/libraries. If more than one dir is passed they will be
                        processed in order. Note: This will break if not all dependencies have been built.
  --no-install-subdir-to-sysroot
                        Do not when using the --subdir option for CheriBSD targets also install the built libraries into
                        the sysroot.
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

Options for target 'qemu':
  --qemu/gui            Build a the graphical UI bits for QEMU (SDL,VNC) (default: 'False')
  --qemu/targets QEMU/TARGETS
                        Build QEMU for the following targets (default:
                        'cheri128-softmmu,mips64-softmmu,riscv64-softmmu,riscv64cheri-
                        softmmu,riscv32-softmmu,x86_64-softmmu,aarch64-softmmu')
  --qemu/unaligned      Permit un-aligned loads/stores (default: 'False')
  --qemu/statistics     Collect statistics on out-of-bounds capability creation. (default: 'False')

Options for target 'freebsd-universe':
  --freebsd-universe/build-options OPTIONS
                        Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf`
                        for more info. (default: '[]')
  --freebsd-universe/minimal
                        Don't build all of FreeBSD, just what is needed for running most CHERI tests/benchmarks
                        (default: 'False')
  --freebsd-universe/no-build-tests
                        Do not build the tests too (-DWITH_TESTS)

Options for target 'go':
  --go/bootstrap-toolchain GO/BOOTSTRAP_TOOLCHAIN
                        Path to alternate go bootstrap toolchain.

Options for target 'run-rtems':
  --run-rtems/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhost at $PORT via telnet
                        instead of using CTRL+A,C

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

Options for target 'cheribsd':
  --cheribsd/build-options OPTIONS
                        Additional make options to be passed to make when building FreeBSD/CheriBSD. See `man src.conf`
                        for more info. (default: '[]')
  --cheribsd/minimal    Don't build all of FreeBSD, just what is needed for running most CHERI tests/benchmarks
                        (default: 'False')
  --cheribsd/no-build-tests
                        Do not build the tests too (-DWITH_TESTS)
  --cheribsd/subdir SUBDIRS
                        Only build subdirs SUBDIRS instead of the full tree. Useful for quickly rebuilding an individual
                        programs/libraries. If more than one dir is passed, they will be processed in order. Note: This
                        will break if not all dependencies have been built. (default: 'the value of the global
                        --freebsd-subdir options')
  --cheribsd/kernel-config CONFIG
                        The kernel configuration to use for `make buildkernel` (default: CHERI_MALTA64) (default:
                        'target-dependent default')
  --cheribsd/no-debug-info
                        Do not pass make flags for building with debug info
  --cheribsd/no-auto-obj
                        Do not use -DWITH_AUTO_OBJ (experimental)
  --cheribsd/sysroot-only
                        Only build a sysroot instead of the full system. This will only build the libraries and skip all
                        binaries (default: 'False')
  --cheribsd/build-fpga-kernels
                        Also build kernels for the FPGA. (default: 'False')
  --cheribsd/build-fett-kernels
                        Also build kernels for FETT. (default: 'False')
  --cheribsd/pure-cap-kernel
                        Build kernel with pure capability ABI (experimental) (default: 'False')
  --cheribsd/caprevoke-kernel
                        Build kernel with caprevoke support (experimental) (default: 'False')

Options for target 'cheribsd-mfs-root-kernel':
  --cheribsd-mfs-root-kernel/no-build-fpga-kernels
                        Do not also build kernels for the FPGA.

Options for target 'cheribsd-sysroot':
  --cheribsd-sysroot/remote-sdk-path PATH
                        The path to the CHERI SDK on the remote FreeBSD machine (e.g. vica:~foo/cheri/output/sdk)

Options for target 'disk-image-minimal':
  --disk-image-minimal/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files-minimal')
  --disk-image-minimal/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-cheri${ABI}-alex')
  --disk-image-minimal/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image-minimal/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/cheribsd-
                        minimal-<TARGET>-disk.img depending on architecture')

Options for target 'disk-image':
  --disk-image/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-cheri${ABI}-alex')
  --disk-image/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/cheribsd-<TARGET>-disk.img
                        depending on architecture')

Options for target 'disk-image-freebsd':
  --disk-image-freebsd/extra-files DIR
                        A directory with additional files that will be added to the image (default: '$SOURCE_ROOT/extra-
                        files')
  --disk-image-freebsd/hostname HOSTNAME
                        The hostname to use for the QEMU image (default: 'qemu-<TARGET>-alex')
  --disk-image-freebsd/remote-path PATH
                        The path on the remote FreeBSD machine from where to copy the disk image
  --disk-image-freebsd/path IMGPATH
                        The output path for the QEMU disk image (default: '$OUTPUT_ROOT/freebsd-<TARGET>-disk.img
                        depending on architecture')

Options for target 'freertos':
  --freertos/demo DEMO  The FreeRTOS Demo build. (default: 'RISC-V-Generic')
  --freertos/prog PROG  The FreeRTOS program to build. (default: 'main_blinky')
  --freertos/bsp BSP    The FreeRTOS BSP to build. This is only valid for the paramterized RISC-V-Generic. The BSP
                        option chooses platform, RISC-V arch and RISC-V abi in the $platform-$arch-$abi format. See
                        RISC-V-Generic/README for more details (default: 'target-dependent default')

Options for target 'run':
  --run/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhost at $PORT via telnet
                        instead of using CTRL+A,C
  --run/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` to connect to the VM (default: '19500')
  --run/remote-kernel-path RUN/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'run-freertos':
  --run-freertos/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhost at $PORT via telnet
                        instead of using CTRL+A,C
  --run-freertos/demo DEMO
                        The FreeRTOS Demo to run. (default: 'RISC-V-Generic')
  --run-freertos/prog PROG
                        The FreeRTOS program to run. (default: 'main_blinky')
  --run-freertos/bsp BSP
                        The FreeRTOS BSP to run. This is only valid for the paramterized RISC-V-Generic. The BSP option
                        chooses platform, RISC-V arch and RISC-V abi in the $platform-$arch-$abi format. See RISC-V-
                        Generic/README for more details (default: 'target-dependent default')

Options for target 'run-minimal':
  --run-minimal/monitor-over-telnet PORT
                        If set, the QEMU monitor will be reachable by connecting to localhost at $PORT via telnet
                        instead of using CTRL+A,C
  --run-minimal/ssh-forwarding-port PORT
                        The port on localhost to forward to the QEMU ssh port. You can then use `ssh root@localhost -p
                        $PORT` to connect to the VM (default: '19519')
  --run-minimal/remote-kernel-path RUN_MINIMAL/REMOTE_KERNEL_PATH
                        Path to the FreeBSD kernel image on a remote host. Needed because FreeBSD cannot be cross-
                        compiled.
  --run-minimal/skip-kernel-update
                        Don't update the kernel from the remote host (default: 'False')

Options for target 'bash':
  --bash/set-as-root-shell
                        Set root's shell to bash (default: 'False')

Options for target 'qtbase-dev':
  --qtbase-dev/no-build-tests
                        Do not build the Qt unit tests
  --qtbase-dev/build-examples
                        build the Qt examples (default: 'False')
  --qtbase-dev/no-assertions
                        Do not include assertions
  --qtbase-dev/no-minimal
                        Do not don't build QtWidgets or QtGui, etc

Options for target 'qtwebkit':
  --qtwebkit/build-jsc-only
                        only build the JavaScript interpreter executable (default: 'False')
```
