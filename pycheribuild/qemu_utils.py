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
import subprocess
from pathlib import Path

from .config.target_info import CrossCompileTarget
from .utils import runCmd


class QemuOptions:
    def __init__(self, xtarget: CrossCompileTarget):
        self.xtarget = xtarget
        self.virtio_disk = True
        self._has_pci = True
        self.can_boot_kernel_directly = False
        if xtarget.is_mips(include_purecap=True):
            # Note: we always use the CHERI QEMU
            self.qemu_arch_sufffix = "cheri128"
            self.machine_flags = ["-M", "malta"]
            self.virtio_disk = False  # broken for MIPS?
            self.can_boot_kernel_directly = True
        elif xtarget.is_riscv(include_purecap=True):
            # Note: we always use the CHERI QEMU
            self.qemu_arch_sufffix = "riscv64cheri"
            self._has_pci = False
            self.machine_flags = ["-M", "virt"]
            self.can_boot_kernel_directly = True
        elif xtarget.is_any_x86():
            self.qemu_arch_sufffix = "x86_64" if xtarget.is_x86_64() else "i386"
            self.can_boot_kernel_directly = False  # boot from disk
            self.machine_flags = []  # default CPU (and NOT -M virt!)
        elif xtarget.is_aarch64():
            self.qemu_arch_sufffix = "aarch64"
            self.can_boot_kernel_directly = False  # boot from disk
            self.machine_flags += ["-M", "virt"]
        else:
            raise ValueError("Unknown target " + str(xtarget))

    def disk_image_args(self, image) -> list:
        if self.virtio_disk:
            return ["-drive", "if=none,file=" + str(image) + ",id=drv,format=raw",
                    "-device", "virtio-blk-device,drive=drv"]
        else:
            return ["-drive", "file=" + str(image) + ",format=raw,index=0,media=disk"]

    def user_network_args(self, extra_options):
        # We'd like to use virtio everwhere, but FreeBSD doesn't like it on BE mips.
        if self.xtarget.is_mips(include_purecap=True):
            return ["-net", "nic", "-net", "user,id=net0,ipv6=off" + extra_options]
        else:
            if self.xtarget.is_any_x86():  # TODO: aarch64?
                virtio_device_kind = "virtio-net-pci"
            else:
                virtio_device_kind = "virtio-net-device"
            return ["-device", virtio_device_kind + ",netdev=net0", "-netdev", "user,id=net0,ipv6=off" + extra_options]


@functools.lru_cache(maxsize=20)
def qemu_supports_9pfs(qemu: Path) -> bool:
    if not qemu.is_file():
        return False
    prog = runCmd([str(qemu), "-virtfs", "?"], stdin=subprocess.DEVNULL, captureOutput=True, captureError=True,
        runInPretendMode=True, expected_exit_code=1, print_verbose_only=True)
    return b"-virtfs ?: Usage: -virtfs" in prog.stderr
