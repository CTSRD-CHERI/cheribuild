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
import subprocess
from pathlib import Path

from .busybox import BuildAllianceBusyBox, BuildMochaBusyBox
from .opensbi import BuildAllianceOpenSBI
from ...config.chericonfig import CheriConfig, RiscvCheriISA
from ...config.compilation_targets import CheriLinuxTargetInfo, CompilationTargets
from ...config.target_info import CPUArchitecture


class PackageMochaCheriLinux(BuildMochaBusyBox):
    target = "genimage-mocha-cheri-linux"
    include_os_in_target_suffix = False  # Avoid adding -linux- as we are running cheri-linux
    _supported_architectures = (CompilationTargets.CHERI_LINUX_RISCV64_PURECAP_093,)
    supported_riscv_cheri_standard = RiscvCheriISA.EXPERIMENTAL_STD093
    _default_architecture = CompilationTargets.CHERI_LINUX_RISCV64_PURECAP_093
    CheriLinuxTargetInfo.kernel_target = "mocha-linux-kernel"

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        result = ("mocha-opensbi-u-boot-baremetal-riscv64-purecap",)
        return result

    def gen_rootfs_uimage(self):
        root_dir = self.cross_sysroot_path
        initramfs = f"{root_dir}/boot/initramfs.cpio.gz"
        uboot_mkimage = f"{self.config.cheri_alliance_sdk_dir}/u-boot/mkimage"

        if not self.config.pretend:
            # run mkimage
            subprocess.run(
                [
                    uboot_mkimage,
                    "-A",
                    "riscv",
                    "-T",
                    "ramdisk",
                    "-O",
                    "linux",
                    "-C",
                    "gzip",
                    "-n",
                    "CHERI-Linux rootfs for Mocha",
                    "-d",
                    str(initramfs),
                    f"{root_dir}/boot/rootfs.uimage.gz",
                ],
                check=True,
            )

    def install(self, **kwargs):
        super().install()
        self.gen_rootfs_uimage()


class PackageCVA6CheriLinux(BuildAllianceBusyBox):
    target = "genimage-cva6-cheri-linux"
    include_os_in_target_suffix = False  # Avoid adding -linux- as we are running cheri-linux
    _supported_architectures = (CompilationTargets.CHERI_LINUX_RISCV64_PURECAP_093,)
    supported_riscv_cheri_standard = RiscvCheriISA.EXPERIMENTAL_STD093
    _default_architecture = CompilationTargets.CHERI_LINUX_RISCV64_PURECAP_093
    CheriLinuxTargetInfo.kernel_target = "cva6cheri-linux-kernel"

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        result = ("cva6cheri-opensbi-u-boot-baremetal-riscv64-purecap",)
        return result

    def next_power_of_two(self, size: int) -> int:
        """Return the smallest power of two >= size."""
        if size <= 1:
            return 1
        return 1 << (size - 1).bit_length()

    def round_up(self, size: int, alignment: int) -> int:
        return ((size + alignment - 1) // alignment) * alignment

    def gen_fit(self):
        root_dir = self.cross_sysroot_path
        kernel = f"{root_dir}/boot/Image.gz"
        initramfs = f"{root_dir}/boot/initramfs.cpio.gz"
        uboot_mkimage = f"{self.config.cheri_alliance_sdk_dir}/u-boot/mkimage"
        purecap_suffix = "-purecap" if self.crosscompile_target.is_cheri_purecap([CPUArchitecture.RISCV64]) else ""
        uboot_dtb = f"{self.config.cheri_alliance_sdk_dir}/u-boot/riscv64{purecap_suffix}/cv64a6_imafdc_zcheri_sv39.dtb"
        opensbi_install = BuildAllianceOpenSBI.get_install_dir(
            self, cross_target=CompilationTargets.FREESTANDING_RISCV64_PURECAP_093
        )

        self.opensbi = opensbi_install / "share" / "opensbi" / "l64pc128" / "generic" / "firmware" / "fw_payload.bin"

        its_path = self.build_dir / "fitImage.its"
        self.itb_path = self.build_dir / "fitImage.itb"

        its_file = f"""
/dts-v1/;

/ {{
    description = "CVA6-CHERI-Linux";
    #address-cells = <1>;

    images {{
        kernel-1 {{
            description = "CHERI Linux Kernel";
            data = /incbin/("{kernel}");
            type = "kernel";
            arch = "riscv";
            os = "linux";
            compression = "gzip";
            load = <0x81000000>;
            entry = <0x81000000>;
            hash-1 {{
                algo = "sha256";
            }};
        }};
        ramdisk-1 {{
            description = "Initial BusyBox ramdisk";
            data = /incbin/("{initramfs}");
            type = "ramdisk";
            arch = "riscv";
            os = "linux";
            hash-1 {{
                algo = "sha256";
            }};
        }};
        fdt-1 {{
            description = "CVA6-CHERI DTB";
            data = /incbin/("{uboot_dtb}");
            type = "flat_dt";
            arch = "riscv";
            compression = "none";
            hash-1 {{
                algo = "sha256";
            }};
        }};
    }};

    configurations {{
        default = "standard";
        standard {{
            description = "Standard Boot";
            kernel = "kernel-1";
            fdt = "fdt-1";
            ramdisk = "ramdisk-1";
        }};
    }};
}};
"""

        print(its_file)

        if self.config.pretend:
            return

        # write ITS
        its_path.write_text(its_file)

        # run mkimage
        subprocess.run([uboot_mkimage, "-f", str(its_path), str(self.itb_path)], check=True)

    def gen_sdcard_image(self):
        mib = 1024 * 1024
        sz = 512  # Sector size
        gpt_start_sector = 2048

        img = Path(self.install_dir) / "boot/cva6_cheri_bootable_linux.img"
        img.parent.mkdir(parents=True, exist_ok=True)

        fw_payload = self.opensbi
        fit_image = Path(self.install_dir) / "boot/fitImage.itb"

        if self.config.pretend:
            return

        part1_mib = self.round_up(fw_payload.stat().st_size, mib) // mib
        part2_mib = self.round_up(fit_image.stat().st_size, mib) // mib
        img_mib = self.next_power_of_two(part1_mib + part2_mib)

        # ------------------------------------------------------------
        # 1. Create empty disk image
        # ------------------------------------------------------------
        subprocess.run(
            ["dd", "if=/dev/zero", f"of={img}", "bs=1M", f"count={img_mib}"],
            check=True,
        )

        # ------------------------------------------------------------
        # 2. Create GPT partition table
        # ------------------------------------------------------------
        subprocess.run(["sgdisk", "-Z", str(img)], check=True)

        subprocess.run(
            [
                "sgdisk",
                "--clear",
                "-g",
                "-n",
                f"1:{gpt_start_sector}:+{part1_mib}M",
                "-c",
                "1:OpenSBI",
                "-t",
                "1:3000",
                "-n",
                f"2:0:+{part2_mib}M",
                "-c",
                "2:FIT",
                "-t",
                "2:EF00",  # EFI/FAT
                str(img),
            ],
            check=True,
        )

        subprocess.run(
            [
                "dd",
                f"if={fw_payload}",
                f"of={img}",
                f"bs={sz}",
                f"seek={gpt_start_sector}",
                "conv=notrunc",
                "status=progress",
                "oflag=sync",
            ],
            check=True,
        )

        # ------------------------------------------------------------
        # 3. Force kernel to re-read partition layout
        # ------------------------------------------------------------
        subprocess.run(["partprobe", str(img)], check=True)

        # ------------------------------------------------------------
        # 4. Format partition 2 as FAT and copy FIT image
        # ------------------------------------------------------------
        part2_offset = gpt_start_sector * sz + part1_mib * mib

        # Format FAT filesystem
        subprocess.run(["mformat", "-i", f"{img}@@{part2_offset}", "::"], check=True)

        # Copy FIT image into FAT partition
        subprocess.run(["mcopy", "-i", f"{img}@@{part2_offset}", str(fit_image), "::fitImage.itb"], check=True)

    def install(self, **kwargs):
        super().install()
        self.gen_fit()
        self.install_file(self.itb_path, self.install_dir / "boot/fitImage.itb")
        self.gen_sdcard_image()
