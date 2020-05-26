#
# Copyright (c) 2020 Alex Richardson
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
import functools
import shutil
import subprocess
import typing
from pathlib import Path

from .config.target_info import CrossCompileTarget
from .utils import runCmd


class QemuOptions:
    def __init__(self, xtarget: CrossCompileTarget):
        self.xtarget = xtarget
        self.virtio_disk = True
        self.can_boot_kernel_directly = False
        self.memory_size = "2048"
        if xtarget.is_mips(include_purecap=True):
            # Note: we always use the CHERI QEMU
            self.qemu_arch_sufffix = "cheri128"
            self.machine_flags = ["-M", "malta"]
            self.virtio_disk = False  # broken for MIPS?
            self.can_boot_kernel_directly = True
        elif xtarget.is_riscv(include_purecap=True):
            # Note: we always use the CHERI QEMU
            self.qemu_arch_sufffix = "riscv64cheri"
            self.machine_flags = ["-M", "virt"]
            self.can_boot_kernel_directly = True
        elif xtarget.is_any_x86():
            self.qemu_arch_sufffix = "x86_64" if xtarget.is_x86_64() else "i386"
            self.can_boot_kernel_directly = False  # boot from disk
            self.machine_flags = []  # default CPU (and NOT -M virt!)
        elif xtarget.is_aarch64():
            self.qemu_arch_sufffix = "aarch64"
            self.can_boot_kernel_directly = False  # boot from disk
            self.machine_flags = ["-M", "virt"]
        else:
            raise ValueError("Unknown target " + str(xtarget))

    def disk_image_args(self, image) -> list:
        if self.virtio_disk:
            return ["-drive", "if=none,file=" + str(image) + ",id=drv,format=raw",
                    "-device", "virtio-blk-device,drive=drv"]
        else:
            return ["-drive", "file=" + str(image) + ",format=raw,index=0,media=disk"]

    def can_use_virtio_network(self):
        # We'd like to use virtio everwhere, but FreeBSD doesn't like it on BE mips.
        if self.xtarget.is_mips(include_purecap=True):
            return False
        return True

    def user_network_args(self, extra_options):
        # We'd like to use virtio everwhere, but FreeBSD doesn't like it on BE mips.
        if not self.can_use_virtio_network():
            return ["-net", "nic", "-net", "user,id=net0,ipv6=off" + extra_options]
        if self.xtarget.is_any_x86():  # TODO: aarch64?
            virtio_device_kind = "virtio-net-pci"
        else:
            virtio_device_kind = "virtio-net-device"
        return ["-device", virtio_device_kind + ",netdev=net0", "-netdev", "user,id=net0,ipv6=off" + extra_options]

    def get_qemu_binary(self) -> "typing.Optional[Path]":
        found_in_path = shutil.which("qemu-system-" + self.qemu_arch_sufffix)
        return Path(found_in_path) if found_in_path is not None else None

    def get_commandline(self, *, qemu_command=None, kernel_file: Path = None, disk_image: Path = None,
                        user_network_args: str = "", add_network_device=True, bios_args: "typing.List[str]" = None,
                        trap_on_unrepresentable=False, debugger_on_cheri_trap=False, add_virtio_rng=False,
                        gui_options: "typing.List[str]" = None) -> "typing.List[str]":
        if qemu_command is None:
            qemu_command = self.get_qemu_binary()
        result = [str(qemu_command)]
        result.extend(self.machine_flags)
        result.extend(["-m", self.memory_size])
        if gui_options is None:
            gui_options = ["-nographic"]
        # For debugging generate a trap on unrepresentable instead of detagging:
        if self.xtarget.is_hybrid_or_purecap_cheri():
            if trap_on_unrepresentable:
                result.append("-cheri-c2e-on-unrepresentable")
            if debugger_on_cheri_trap:
                result.append("-cheri-debugger-on-trap")
        result.extend(gui_options)
        if bios_args:
            result.extend(bios_args)
        if kernel_file:
            result.append("-kernel")
            result.append(str(kernel_file))
        if disk_image:
            result.extend(self.disk_image_args(disk_image))
        if add_network_device:
            result.extend(self.user_network_args(user_network_args))
        if add_virtio_rng:
            result.extend(["-device", "virtio-rng-pci"])
        return result


@functools.lru_cache(maxsize=20)
def qemu_supports_9pfs(qemu: Path) -> bool:
    if not qemu.is_file():
        return False
    prog = runCmd([str(qemu), "-virtfs", "?"], stdin=subprocess.DEVNULL, captureOutput=True, captureError=True,
        runInPretendMode=True, expected_exit_code=1, print_verbose_only=True)
    return b"-virtfs ?: Usage: -virtfs" in prog.stderr
