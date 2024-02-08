#
# Copyright (c) 2016 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
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

from .cmake_project import CMakeProject
from .project import BuildType, ComputedDefaultValue, GitRepository
from .run_qemu import LaunchQEMUBase, get_default_ssh_forwarding_port
from .simple_project import BoolConfigOption, IntConfigOption
from ..config.compilation_targets import CompilationTargets
from ..utils import OSInfo


class BuildCheriOS(CMakeProject):
    dependencies = ("cherios-llvm", "makefs-linux")
    default_build_type = BuildType.DEBUG
    repository = GitRepository("https://github.com/CTSRD-CHERI/cherios.git", default_branch="master")
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, p: config.output_root
        / ("cherios" + p.crosscompile_target.build_suffix(config, include_os=False)),
        as_string="$OUTPUT_ROOT/cherios-{mips64,riscv64}",
    )
    needs_sysroot = False
    supported_architectures = (CompilationTargets.CHERIOS_MIPS_PURECAP, CompilationTargets.CHERIOS_RISCV_PURECAP)

    smp_cores = IntConfigOption("smp-cores", default=1, help="Number of cores to use")
    build_net = BoolConfigOption("build-net", default=False, help="Include networking support")

    def setup(self):
        super().setup()
        self.add_cmake_options(CHERI_SDK_DIR=self.target_info.sdk_root_dir)
        self.add_cmake_options(BUILD_FOR_CHERI128=self.config.mips_cheri_bits == 128)
        self.add_cmake_options(BUILD_WITH_NET=self.build_net)
        self.add_cmake_options(SMP_CORES=self.smp_cores)
        self.add_cmake_options(CMAKE_AR=self.sdk_bindir / "llvm-ar")
        self.add_cmake_options(CMAKE_RANLIB=self.sdk_bindir / "llvm-ranlib")
        self.add_cmake_options(PLATFORM=self.crosscompile_target.base_target_suffix)

    def install(self, **kwargs):
        pass  # nothing to install yet


class LaunchCheriOSQEMU(LaunchQEMUBase):
    target = "run-cherios"
    dependencies = ("qemu", "cherios")
    supported_architectures = (CompilationTargets.CHERIOS_MIPS_PURECAP, CompilationTargets.CHERIOS_RISCV_PURECAP)
    forward_ssh_port = False
    qemu_user_networking = False
    hide_options_from_help = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(40), **kwargs)

    @property
    def source_project(self):
        return BuildCheriOS.get_instance(self, self.config)

    def setup(self):
        super().setup()
        # FIXME: these should be config options
        cherios = BuildCheriOS.get_instance(self, self.config)
        self.current_kernel = cherios.build_dir / "boot/cherios.elf"
        self.disk_image = self.config.output_root / "cherios-disk.img"
        self._project_specific_options = ["-no-reboot", "-global", "virtio-mmio.force-legacy=false"]

        if cherios.build_net:
            self._after_disk_options.extend(
                [
                    "-netdev",
                    "tap,id=tap0,ifname=cherios_tap,script=no,downscript=no",
                    "-device",
                    "virtio-net-device,netdev=tap0",
                ]
            )

        if cherios.smp_cores > 1:
            self._project_specific_options.append("-smp")
            self._project_specific_options.append(str(cherios.smp_cores))

        self.qemu_options.virtio_disk = True  # CheriOS needs virtio
        self.qemu_options.force_virtio_blk_device = True
        self.qemu_user_networking = False

    def process(self):
        if not self.disk_image.exists():
            if self.query_yes_no("CheriOS disk image is missing. Would you like to create a zero-filled 1MB image?"):
                size_flag = "bs=128m" if OSInfo.IS_MAC else "bs=128M"
                self.run_cmd("dd", "if=/dev/zero", "of=" + str(self.disk_image), size_flag, "count=1")
        super().process()

    def get_riscv_bios_args(self) -> "list[str]":
        # CheriOS bundles its kernel with its own bootloader
        return ["-bios", "none"]
