#
# Copyright (c) 2025 Hesham Almatary
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
from pathlib import Path

from .crosscompileproject import CrossCompileAutotoolsProject
from ..project import (
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
)
from ...config.compilation_targets import CompilationTargets
from ...utils import classproperty


class BuildBusyBox(CrossCompileAutotoolsProject):
    target = "busybox"
    repository = GitRepository("https://git.busybox.net/busybox/")
    is_sdk_target = False
    _supported_architectures = (
        CompilationTargets.LINUX_RISCV64,
        CompilationTargets.LINUX_AARCH64,
    )
    make_kind = MakeCommandKind.GnuMake
    _always_add_suffixed_targets = True

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    def setup(self) -> None:
        if self.config.verbose:
            self.make_args.set(V=True)
        super().setup()

        if self.crosscompile_target.is_riscv(include_purecap=True):
            self.busybox_arch = "riscv"
        elif self.crosscompile_target.is_aarch64(include_purecap=True):
            self.busybox_arch = "arm64"

        # Avoid dependency on libgcc_eh
        self.COMMON_LDFLAGS.append("--unwindlib=none")
        self.make_args.set(ARCH=self.busybox_arch, O=self.build_dir)
        self.make_args.set(
            CC=self.commandline_to_str([self.CC, *self.essential_compiler_and_linker_flags]),
            HOSTCC=self.host_CC,
            # Force busybox's Makefile not to use the triple for finding the toolchain
            CROSS_COMPILE="",
            LD=self.target_info.linker,
            # LDFLAGS are passed directly to ld.lld, we need to set CFLAGS_busybox
            CFLAGS_busybox=self.commandline_to_str(self.default_ldflags),
            AR=self.sdk_bindir / "llvm-ar",
            NM=self.sdk_bindir / "llvm-nm",
            STRIP=self.sdk_bindir / "llvm-strip",
            OBJCOPY=self.sdk_bindir / "llvm-objcopy",
            OBJDUMP=self.sdk_bindir / "llvm-objdump",
            # CONFIG_PREFIX is used to define the installation directory
            CONFIG_PREFIX=self.install_dir / "rootfs",
        )

    def write_busybox_init(self, init_path: Path, hostname: str, prompt: str, welcome_message: str):
        """
        Write a BusyBox-compatible /init script to the given path.
        This is derived (and modified) from an init C version located here:
        https://git.morello-project.org/morello/morello-sdk/-/blob/latest/morello/projects/init/init.c
        """
        self.makedirs(init_path.parent)  # ensure rootfs/ exists

        script = f"""#!/bin/sh
# Minimal init script to replace the C init
set -x
echo ">>> /init: starting OK"

PATH=/usr/sbin:/bin:/sbin
export PATH

echo "Hello from BusyBox"

# Ensure required mount points exist
mkdir -p /proc /dev/pts /dev/mqueue /dev/shm /sys /sys/fs/cgroup /etc
ln -sf /proc/mounts /etc/mtab

# Mount essential filesystems
mount -t proc none /proc
mount -t devpts none /dev/pts
mount -t mqueue none /dev/mqueue
mount -t tmpfs none /dev/shm
mount -t sysfs none /sys
mount -t cgroup none /sys/fs/cgroup

# Set hostname
hostname {hostname}

echo
echo "{welcome_message}"
echo "Have a lot of fun!"
echo

# Install udhcpc DHCP helper script
ifconfig eth0 up
udhcpc -i eth0
ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up
route add default gw 10.0.2.2
echo "nameserver 8.8.8.8" > /etc/resolv.conf

# Loop shell forever
while true; do
    echo "[{prompt}]: Starting /bin/sh..."
    /bin/sh
    sleep 1
done
"""
        # Make the script executable
        self.write_file(init_path, contents=script, overwrite=True, mode=0o755)

    def compile(self, **kwargs) -> None:
        # Disable traffic control since it got removed from >= 6.8 Linux kernel
        # and Busybox hasn't fixed that yet
        # https://bugs.busybox.net/show_bug.cgi?id=15931
        # https://lists.busybox.net/pipermail/busybox-cvs/2024-January/041752.html
        self.replace_in_file(self.build_dir / ".config", {"CONFIG_TC=y": "CONFIG_TC=n"})
        self.run_make("busybox")

    def configure(self, **kwargs) -> None:
        self.run_make("defconfig", cwd=self.source_dir)

    def make_initramfs(self, installdir: Path, out_file: Path):
        self.makedirs(installdir)  # ensure path exists
        self.makedirs(out_file.parent)  # <-- ensure boot/ exists
        with (Path("/dev/null") if self.config.pretend else out_file).open("wb") as out:
            self.run_cmd(["find . | cpio --verbose -o --format=newc | gzip"], shell=True, cwd=installdir, stdout=out)
        self.info("Wrote", out_file)

    def install(self, **kwargs) -> None:
        self.run_make_install()
        root = self.install_dir / "rootfs"
        self.write_busybox_init(
            self.install_dir / "rootfs/init",
            hostname="cheribuild-linux",
            welcome_message="Welcome to Linux (busybox)!",
            prompt="Linux",
        )
        self.make_initramfs(root, self.install_dir / "boot/initramfs.cpio.gz")


class BuildMorelloBusyBox(BuildBusyBox):
    target = "morello-busybox"
    repository = GitRepository("https://git.morello-project.org/morello/morello-busybox.git")
    _supported_architectures = (CompilationTargets.LINUX_MORELLO_PURECAP,)

    def setup(self) -> None:
        # Morello Buxybox has its own modified Makefile to work with LLVM/Clang and Morello
        # compiler flags. Skip the parent's setup and just setup Morello's Makefile args.
        CrossCompileAutotoolsProject.setup(self)
        self.make_args.set(CONFIG_PREFIX=self.install_dir / "rootfs")
        self.make_args.set(MUSL_HOME=self.install_dir)
        self.make_args.set(KHEADERS=self.install_dir / "usr/include/")
        self.make_args.set(CLANG_RESOURCE_DIR=self.install_dir / "lib")
        self.add_configure_vars(CC=self.CC)

    def configure(self) -> None:
        self.run_make("morello_busybox_defconfig", cwd=self.source_dir)

    def install(self, **kwargs):
        self.write_busybox_init(
            self.install_dir / "rootfs/init",
            hostname="morello",
            welcome_message="Welcome to Morello PCuABI environment (busybox)!",
            prompt="MORELLO",
        )
        super().install(**kwargs)
