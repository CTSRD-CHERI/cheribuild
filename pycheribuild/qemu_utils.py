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
from pathlib import Path
from typing import Optional

from .config.target_info import CPUArchitecture, CrossCompileTarget
from .processutils import run_command
from .utils import ConfigBase, OSInfo, warning_message


class QemuOptions:
    def __init__(self, xtarget: CrossCompileTarget, want_debugger=False) -> None:
        self.xtarget = xtarget
        self.virtio_disk = True
        self.force_virtio_blk_device = False
        self.can_boot_kernel_directly = False
        self.memory_size = "2048"
        self.has_default_nic = False
        if xtarget.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            self.qemu_arch_sufffix = "morello"
            self.can_boot_kernel_directly = False  # boot from disk
            # XXX: Use a CHERI-aware firmware. EL3 is disabled by default for
            # virt, so CPTR_EL3 doesn't exist and CheriBSD can enable
            # CPTR_EL2.CEN freely and thus we can get away without CHERI-aware
            # firmware so long as loader(8) is plain AArch64.
            self.machine_flags = ["-M", "virt,gic-version=3", "-cpu", "morello", "-bios", "edk2-aarch64-code.fd"]
        elif xtarget.is_mips(include_purecap=True):
            # Note: we always use the CHERI QEMU
            self.qemu_arch_sufffix = "mips64cheri128"
            self.machine_flags = ["-M", "malta"]
            self.virtio_disk = False  # broken for MIPS?
            self.can_boot_kernel_directly = True
            self.has_default_nic = True  # MALTA board has a default pcnet at 0x0b
        elif xtarget.is_riscv(include_purecap=True):
            # Note: we always use the CHERI QEMU
            self.qemu_arch_sufffix = "riscv32cheri" if xtarget.is_riscv32(include_purecap=True) else "riscv32cheri"
            self.machine_flags = ["-M", "virt"]
            self.can_boot_kernel_directly = True
        elif xtarget.is_any_x86():
            # We boot i386 FreeBSD in a x86_64 QEMU. This avoids having to build another version of QEMU.
            self.qemu_arch_sufffix = "x86_64"
            self.can_boot_kernel_directly = False  # boot from disk
            # Try to use KVM instead of TCG if possible to speed up emulation
            if not want_debugger:
                accel_flag = "accel=hvf:hax:tcg" if OSInfo.IS_MAC else "accel=kvm:xen:hax:tcg"
            else:
                accel_flag = "accel=tcg"  # Have to use TCG if we want to attach GDB
            self.machine_flags = ["-M", accel_flag]
            # Use a more modern CPU than the QEMU default:
            # TCG does not support AVX, so pick the newest pre-AVX Intel CPU (Nehalem)
            # self.machine_flags += ["-cpu", "Nehalem"]
            # FIXME: SSE4.2 is broken in QEMU: https://bugs.launchpad.net/qemu/+bug/1916269
            # We have to use the ancient default instead to avoid kernel panics.
            # See https://bugs.freebsd.org/bugzilla/show_bug.cgi?id=253617 for details.
        elif xtarget.is_aarch64(include_purecap=False):
            self.qemu_arch_sufffix = "aarch64"
            self.can_boot_kernel_directly = False  # boot from disk
            self.machine_flags = ["-M", "virt,gic-version=3", "-cpu", "cortex-a72", "-bios", "edk2-aarch64-code.fd"]
        else:
            raise ValueError("Unknown target " + str(xtarget))

    def disk_image_args(self, image: Path, image_format: str) -> list:
        # Probe the disk image format in case someone has overridden the default image path or format is unspecified
        if not image.exists():
            # Either we're pretending or we'll complain elsewhere.
            if image_format is None:
                image_format = "qcow2" if image.name.endswith(".qcow2") else "raw"
        else:
            with image.open('rb') as imgf:
                magic = imgf.read(4)
                is_qcow2 = magic == b'QFI\xfb'

                if image_format is None:
                    image_format = "qcow2" if is_qcow2 else "raw"
                elif is_qcow2 and image_format != "qcow2":
                    warning_message("Disk image looks like qcow2 but claimed image format is", image_format)
                elif not is_qcow2 and image_format == "qcow2":
                    warning_message("Disk image does not look like claimed image format of qcow2")

        if self.virtio_disk:
            # RISC-V doesn't support virtio-blk-pci, we have to use virtio-blk-device
            if self.xtarget.is_riscv(include_purecap=True) or self.force_virtio_blk_device:
                device_kind = "virtio-blk-device"
            else:
                device_kind = "virtio-blk-pci"
            return ["-drive", "if=none,file=" + str(image) + ",id=drv,format=" + image_format,
                    "-device", device_kind + ",drive=drv"]
        else:
            return ["-drive", "file=" + str(image) + ",format=" + image_format + ",index=0,media=disk"]

    def can_use_virtio_network(self) -> bool:
        # We'd like to use virtio everwhere, but FreeBSD doesn't like it on BE mips.
        if self.xtarget.is_mips(include_purecap=True):
            return False
        return True

    def _qemu_network_config(self) -> "tuple[str, str]":
        if self.has_default_nic:
            assert self.xtarget.is_mips(include_purecap=True)
            return "pcnet", "le0"
        if not self.can_use_virtio_network():
            # Note: providing a "pcnet" net crashes CheriBSD for non-MIPS
            if self.xtarget.is_mips(include_purecap=True):
                return "pcnet", "le0"
            return "e1000", "em0"
        elif self.xtarget.is_riscv(include_purecap=True):  # TODO: aarch64?
            return "virtio-net-device", "vtnet0"
        else:
            return "virtio-net-pci", "em0"  # XXX: is vtnet0 correct?

    def network_interface_name(self) -> str:
        return self._qemu_network_config()[1]

    def user_network_args(self, extra_options) -> "list[str]":
        # We'd like to use virtio everwhere, but FreeBSD doesn't like it on BE mips.
        if self.has_default_nic:
            return ["-nic", "user,id=net0" + extra_options]
        network_device_kind = self._qemu_network_config()[0]
        return ["-device", network_device_kind + ",netdev=net0", "-netdev", "user,id=net0" + extra_options]

    def get_qemu_binary(self) -> "Optional[Path]":
        found_in_path = shutil.which("qemu-system-" + self.qemu_arch_sufffix)
        return Path(found_in_path) if found_in_path is not None else None

    def get_commandline(self, *, qemu_command=None, kernel_file: "Optional[Path]" = None,
                        disk_image: "Optional[Path]" = None, disk_image_format: str = "raw",
                        user_network_args: str = "", add_network_device=True, bios_args: "Optional[list[str]]" = None,
                        trap_on_unrepresentable=False, debugger_on_cheri_trap=False, add_virtio_rng=False,
                        write_disk_image_changes=True, gui_options: "Optional[list[str]]" = None) -> "list[str]":
        if kernel_file is None and disk_image is None:
            raise ValueError("Must pass kernel and/or disk image path when launching QEMU")
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
        if kernel_file and self.can_boot_kernel_directly:
            result.append("-kernel")
            result.append(str(kernel_file))
        if disk_image:
            result.extend(self.disk_image_args(disk_image, disk_image_format))
        if not write_disk_image_changes:
            # All disk writes go to a tempfile: https://qemu.readthedocs.io/en/latest/system/images.html#snapshot-mode
            result.append("-snapshot")
        if add_network_device:
            result.extend(self.user_network_args(user_network_args))
        if add_virtio_rng:
            result.extend(["-device", "virtio-rng-pci"])
        return result


@functools.lru_cache(maxsize=20)
def qemu_supports_9pfs(qemu: Path, *, config: ConfigBase) -> bool:
    if not qemu.is_file():
        return False
    prog = run_command([str(qemu), "-virtfs", "?"], stdin=subprocess.DEVNULL, capture_output=True, capture_error=True,
                       run_in_pretend_mode=True, expected_exit_code=1, print_verbose_only=True, config=config)
    return b"-virtfs ?: Usage: -virtfs" in prog.stderr


def riscv_bios_arguments(xtarget: CrossCompileTarget, _, prefer_bbl=True) -> "list[str]":
    assert xtarget.is_riscv(include_purecap=True)
    if xtarget.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
        # noinspection PyUnreachableCode
        if prefer_bbl:
            # We want a purecap BBL:
            # from .projects.cross.bbl import BuildBBLNoPayload
            # return ["-bios", str(BuildBBLNoPayload.get_installed_kernel_path(caller,
            #         cross_target=CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP))]
            # Explicitly specify the file name while QEMU may still be too old:
            return ["-bios", "bbl-riscv64cheri-virt-fw_jump.bin"]
        else:
            # from .projects.cross.opensbi import BuildOpenSBI
            # return ["-bios", str(BuildOpenSBI.get_cheri_bios(caller))]
            return ["-bios", "opensbi-riscv64cheri-virt-fw_jump.bin"]
    # For non-CHERI we prefer the OpenSBI bios that is bundled with QEMU
    # return BuildOpenSBI.get_nocap_bios(caller)
    return ["-bios", "default"]
