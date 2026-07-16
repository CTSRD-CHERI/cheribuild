#
# Copyright (c) 2025-2026 Hesham Almatary
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
from dataclasses import dataclass
from pathlib import Path

from .busybox import BuildAllianceBusyBox, BuildMochaBusyBox
from .opensbi import BuildAllianceOpenSBI
from ...config.chericonfig import CheriConfig, RiscvCheriISA
from ...config.compilation_targets import CheriLinuxTargetInfo, CompilationTargets
from ...config.target_info import CPUArchitecture


@dataclass
class FitImage:
    name: str
    path: Path
    description: str
    type: str
    arch: str = "riscv"
    os: str | None = None
    compression: str | None = None
    load: int | None = None
    entry: int | None = None
    hash_algo: str = "sha256"


@dataclass
class FitConfiguration:
    name: str
    description: str
    kernel: str | None = None
    fdt: str | None = None
    ramdisk: str | None = None


class PackageCVA6FitImages:
    def _generate_fit_image_node(self, image: FitImage) -> str:
        lines = [
            f"        {image.name} {{",
            f'            description = "{image.description}";',
            f'            data = /incbin/("{image.path}");',
            f'            type = "{image.type}";',
            f'            arch = "{image.arch}";',
        ]

        if image.os is not None:
            lines.append(f'            os = "{image.os}";')

        if image.compression is not None:
            lines.append(f'            compression = "{image.compression}";')

        if image.load is not None:
            lines.append(f"            load = <0x{image.load:x}>;")

        if image.entry is not None:
            lines.append(f"            entry = <0x{image.entry:x}>;")

        lines.extend(
            [
                "            hash-1 {",
                f'                algo = "{image.hash_algo}";',
                "            };",
                "        };",
            ]
        )

        return "\n".join(lines)

    def _generate_fit_configuration_node(
        self,
        configuration: FitConfiguration,
    ) -> str:
        lines = [
            f"        {configuration.name} {{",
            f'            description = "{configuration.description}";',
        ]

        if configuration.kernel is not None:
            lines.append(f'            kernel = "{configuration.kernel}";')

        if configuration.fdt is not None:
            lines.append(f'            fdt = "{configuration.fdt}";')

        if configuration.ramdisk is not None:
            lines.append(f'            ramdisk = "{configuration.ramdisk}";')

        lines.append("        };")

        return "\n".join(lines)

    def _generate_fit_its(
        self,
        images: list[FitImage],
        configurations: list[FitConfiguration],
        default_configuration: str | None = None,
    ) -> str:
        if not images:
            raise ValueError("FIT must contain at least one image")

        if not configurations:
            raise ValueError("FIT must contain at least one configuration")

        configuration_names = {configuration.name for configuration in configurations}

        if default_configuration is None:
            default_configuration = configurations[0].name

        if default_configuration not in configuration_names:
            raise ValueError(f"Default configuration {default_configuration!r} does not exist")

        image_nodes = "\n".join(self._generate_fit_image_node(image) for image in images)

        configuration_nodes = "\n".join(
            self._generate_fit_configuration_node(configuration) for configuration in configurations
        )

        return f"""\
/dts-v1/;

/ {{
    description = "FIT Image";
    #address-cells = <1>;

    images {{
{image_nodes}
    }};

    configurations {{
        default = "{default_configuration}";

{configuration_nodes}
    }};
}};
"""

    def generate_fit(
        self,
        *,
        mkimage: Path,
        its_path: Path,
        itb_path: Path,
        images: list[FitImage],
        configurations: list[FitConfiguration],
    ) -> None:
        its_file = self._generate_fit_its(
            images=images,
            configurations=configurations,
        )

        print(its_file)

        if self.config.pretend:
            return

        its_path.write_text(its_file)

        subprocess.run(
            [
                str(mkimage),
                "-f",
                str(its_path),
                str(itb_path),
            ],
            check=True,
        )


class PackageCVA6SDCardImages:
    def next_power_of_two(self, size: int) -> int:
        """Return the smallest power of two >= size."""
        if size <= 1:
            return 1
        return 1 << (size - 1).bit_length()

    def round_up(self, size: int, alignment: int) -> int:
        return ((size + alignment - 1) // alignment) * alignment

    def gen_sdcard_image(
        self,
        os_name,
        *,
        fw_payload=None,
        fit_image=None,
    ):
        mib = 1024 * 1024
        sector_size = 512
        gpt_start_sector = 2048

        if self.config.pretend:
            return

        # ------------------------------------------------------------
        # Paths
        # ------------------------------------------------------------
        img = Path(self.root_dir) / f"boot/cva6_cheri_{os_name}_bootable.img"

        if fw_payload is None:
            fw_payload = self.opensbi
        else:
            fw_payload = Path(fw_payload)

        if fit_image is None:
            fit_image = Path(self.root_dir) / "boot/fitImage.itb"
        else:
            fit_image = Path(fit_image)

        img.parent.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------
        # Calculate partition sizes
        # ------------------------------------------------------------
        part1_mib = (
            self.round_up(
                fw_payload.stat().st_size,
                mib,
            )
            // mib
        )

        part2_mib = (
            self.round_up(
                fit_image.stat().st_size,
                mib,
            )
            // mib
        )

        # Leave enough space for the GPT and both partitions.
        img_mib = self.next_power_of_two(part1_mib + part2_mib)

        # ------------------------------------------------------------
        # 1. Create empty disk image
        # ------------------------------------------------------------
        subprocess.run(
            [
                "dd",
                "if=/dev/zero",
                f"of={img}",
                "bs=1M",
                f"count={img_mib}",
            ],
            check=True,
        )

        # ------------------------------------------------------------
        # 2. Create GPT partition table
        # ------------------------------------------------------------
        subprocess.run(
            ["sgdisk", "-Z", str(img)],
            check=True,
        )

        subprocess.run(
            [
                "sgdisk",
                "--clear",
                "-g",
                # Partition 1: OpenSBI
                "-n",
                f"1:{gpt_start_sector}:+{part1_mib}M",
                "-c",
                "1:OpenSBI",
                "-t",
                "1:3000",
                # Partition 2: FIT
                "-n",
                f"2:0:+{part2_mib}M",
                "-c",
                "2:FIT",
                "-t",
                "2:EF00",
                str(img),
            ],
            check=True,
        )

        # ------------------------------------------------------------
        # 3. Write OpenSBI payload directly to partition 1
        # ------------------------------------------------------------
        subprocess.run(
            [
                "dd",
                f"if={fw_payload}",
                f"of={img}",
                "bs=512",
                f"seek={gpt_start_sector}",
                "conv=notrunc",
                "status=progress",
                "oflag=sync",
            ],
            check=True,
        )

        # ------------------------------------------------------------
        # 4. Force kernel to re-read partition layout
        # ------------------------------------------------------------
        subprocess.run(
            ["partprobe", str(img)],
            check=True,
        )

        # ------------------------------------------------------------
        # 5. Format partition 2 as FAT
        # ------------------------------------------------------------
        part2_offset = gpt_start_sector * sector_size + part1_mib * mib

        subprocess.run(
            [
                "mformat",
                "-i",
                f"{img}@@{part2_offset}",
                "::",
            ],
            check=True,
        )

        # ------------------------------------------------------------
        # 6. Copy FIT image into FAT partition
        # ------------------------------------------------------------
        subprocess.run(
            [
                "mcopy",
                "-i",
                f"{img}@@{part2_offset}",
                str(fit_image),
                "::fitImage.itb",
            ],
            check=True,
        )

        print(f"""
\033[1;32mGenerated SD card image:\033[0m

  \033[36m{img}\033[0m

\033[1;32mSD card boot:\033[0m

  Replace \033[36m/dev/<sd-card-device>\033[0m with the correct SD card device
  and run:

  \033[36msudo dd if={img} of=/dev/<sd-card-device> bs=4M status=progress conv=fsync\033[0m

\033[1;32mTFTP boot:\033[0m

  Copy:

  \033[36m{fit_image}\033[0m

  to the root directory of your TFTP server as:

  \033[36mfitImage.itb\033[0m

  For example:

  \033[36mcp {fit_image} <tftp-root>/fitImage.itb\033[0m

  U-Boot expects the FIT image to be available as
  \033[36mfitImage.itb\033[0m in the root of the TFTP server.

\033[1;33mNOTE:\033[0m The lowRISC Ethernet driver is broken and disabled by default.
If the driver is enabled and working, the TFTP boot instructions above
can be used to boot the system over the network.
""")


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


class PackageCVA6CheriLinux(BuildAllianceBusyBox, PackageCVA6FitImages, PackageCVA6SDCardImages):
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

    def gen_fit(self):
        self.root_dir = self.cross_sysroot_path

        kernel = self.root_dir / "boot" / "Image.gz"
        initramfs = self.root_dir / "boot" / "initramfs.cpio.gz"

        uboot_mkimage = Path(self.config.cheri_alliance_sdk_dir) / "u-boot" / "mkimage"

        purecap_suffix = "-purecap" if self.crosscompile_target.is_cheri_purecap([CPUArchitecture.RISCV64]) else ""

        uboot_dtb = (
            Path(self.config.cheri_alliance_sdk_dir)
            / "u-boot"
            / f"riscv64{purecap_suffix}"
            / "cv64a6_imafdc_zcheri_sv39.dtb"
        )

        opensbi_install = BuildAllianceOpenSBI.get_install_dir(
            self,
            cross_target=CompilationTargets.FREESTANDING_RISCV64_PURECAP_093,
        )

        self.opensbi = opensbi_install / "share" / "opensbi" / "l64pc128" / "generic" / "firmware" / "fw_payload.bin"

        its_path = self.install_dir / "fitImage.its"
        self.itb_path = self.install_dir / "fitImage.itb"

        self.generate_fit(
            mkimage=uboot_mkimage,
            its_path=its_path,
            itb_path=self.itb_path,
            images=[
                FitImage(
                    name="kernel-1",
                    path=kernel,
                    description="CHERI Linux Kernel",
                    type="kernel",
                    os="linux",
                    compression="gzip",
                    load=0x90000000,
                    entry=0x90000000,
                ),
                FitImage(
                    name="ramdisk-1",
                    path=initramfs,
                    description="Initial BusyBox ramdisk",
                    type="ramdisk",
                    os="linux",
                ),
                FitImage(
                    name="fdt-1",
                    path=uboot_dtb,
                    description="CVA6-CHERI DTB",
                    type="flat_dt",
                    compression="none",
                ),
            ],
            configurations=[
                FitConfiguration(
                    name="standard",
                    description="Standard Boot",
                    kernel="kernel-1",
                    fdt="fdt-1",
                    ramdisk="ramdisk-1",
                ),
            ],
        )

    def install(self, **kwargs):
        super().install()
        self.gen_fit()
        self.install_file(self.itb_path, self.install_dir / "boot/fitImage.itb")
        self.gen_sdcard_image("linux")
