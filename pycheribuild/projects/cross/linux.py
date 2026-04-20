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

from abc import ABC
from pathlib import Path

from .crosscompileproject import CrossCompileAutotoolsProject
from ..project import (
    ComputedDefaultValue,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
)
from ..run_qemu import LaunchQEMUBase
from ...config.chericonfig import CheriConfig, RiscvCheriISA
from ...config.compilation_targets import CompilationTargets, LinuxGccTargetInfo
from ...config.target_info import CPUArchitecture
from ...processutils import get_compiler_info
from ...utils import classproperty


class BuildLinux(CrossCompileAutotoolsProject):
    target = "upstream-linux-kernel"
    repository = GitRepository("https://github.com/torvalds/linux.git")
    _needs_sysroot = False
    is_sdk_target = False
    is_rootfs_target = True
    _supported_architectures = (
        *CompilationTargets.ALL_UPSTREAM_LINUX_TARGETS,
        CompilationTargets.LINUX_KERNEL_RISCV64_GCC,
        CompilationTargets.LINUX_KERNEL_AARCH64_GCC,
    )
    _always_add_suffixed_targets = True
    include_os_in_target_suffix = False  # Avoid adding -linux- as we are building linux-kernel here
    make_kind = MakeCommandKind.GnuMake

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("dtc", apt="device-tree-compiler", homebrew="dtc")

    def _set_config(self, option, value: str = "y"):
        """Update config values in .config. You must call make oldconfig afterwards"""
        self.run_cmd(self.source_dir / "scripts/config", "--set-val", option, value, cwd=self.build_dir)
        # Also handle auto-detected config value which would overwrite our manual setting above
        # This happens e.g. with CONFIG_CC_HAS_ASM_GOTO_OUTPUT.
        auto_config_args = (self.source_dir / "scripts/config", "--file", self.build_dir / "include/config/auto.conf")
        auto_value = self.run_cmd(*auto_config_args, "--state", option, capture_output=True)
        if auto_value.stdout != b"undef":
            self.run_cmd(*auto_config_args, "--set-val", option, value)

    @property
    def linux_arch(self) -> str:
        if self.crosscompile_target.is_riscv(include_purecap=True):
            return "riscv"
        elif self.crosscompile_target.is_aarch64(include_purecap=True):
            return "arm64"
        raise LookupError()

    def setup(self) -> None:
        super().setup()
        self.make_args.add_flags("-f", self.source_dir / "Makefile")

        compiler_info = get_compiler_info(self.CC, config=self.config)

        if compiler_info.is_gcc():
            assert isinstance(self.target_info, LinuxGccTargetInfo)
            self.make_args.set(CROSS_COMPILE=self.target_info._cross_compile_prefix)
        else:
            self.make_args.set(
                CROSS_COMPILE=str(self.CC.parent) + "/",
                LLVM=str(self.CC.parent) + "/",
            )
            # We only support building the kernel with LLVM/Clang
            self.make_args.set(HOSTCC=self.host_CC)
            self.make_args.set(HOSTCXX=self.host_CXX)

        self.make_args.set(KBUILD_ABS_SRCTREE=self.source_dir.absolute())
        self.make_args.set(ARCH=self.linux_arch)
        self.make_args.set(O=self.build_dir)

        # Install kernel headers at rootfs (and sysroot)'s path
        self.make_args.set(INSTALL_HDR_PATH=self.install_dir / "usr")

        # Don't overwrite our manually edited .config file with default values
        self.make_args.set_env(KCONFIG_NOSILENTUPDATE=1)

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        super().setup_config_options(**kwargs)

        cls.defconfig = cls.add_config_option(
            "defconfig",
            default=ComputedDefaultValue(
                function=lambda _, p: (p.default_defconfig()),
                as_string="platform-dependent, usually defconfig",
            ),
            help="The Linux kernel's defconfig to use",
        )

    def default_defconfig(self) -> str:
        return "defconfig"

    def _apply_build_patches(self):
        # Placeholder for future patches that might be required to be applied here
        pass

    def compile(self, **kwargs):
        self._apply_build_patches()
        self.run_make()

    def _apply_patch_from_url(self, patch_output_path: Path, patch_url: str):
        self.download_file(patch_output_path, patch_url)
        # Check if the patch can be applied in reverse. If this command fails, the patch is not yet applied.
        already_applied = self.run_cmd(
            ["git", "apply", "--check", "--reverse", patch_output_path],
            cwd=self.source_dir,
            allow_unexpected_returncode=True,
            print_verbose_only=True,
        )
        if already_applied.returncode != 0:
            self.info(f"Applying patch from {patch_url}")
            self.run_cmd("git", "apply", patch_output_path, cwd=self.source_dir)
        else:
            self.info(f"Patch from {patch_url} already applied, skipping.")

    def configure(self, **kwargs):
        assert self.defconfig is not None
        self.run_make(self.defconfig, cwd=self.source_dir, parallel=False)

    def install(self, **kwargs):
        self.install_file(self.build_dir / "vmlinux", self.install_dir / "boot/vmlinux")
        self.install_file(self.build_dir / "System.map", self.install_dir / "boot/System.map")
        self.install_file(self.build_dir / f"arch/{self.linux_arch}/boot/Image", self.install_dir / "boot/Image")
        self.install_file(self.build_dir / f"arch/{self.linux_arch}/boot/Image.gz", self.install_dir / "boot/Image.gz")
        self.run_make("headers_install", cwd=self.source_dir)


class BuildCheriAllianceLinux(BuildLinux):
    target = "linux-kernel"
    repository = GitRepository("https://github.com/CHERI-Alliance/linux.git", default_branch="codasip-cheri-riscv-6.18")
    _supported_architectures = (
        *CompilationTargets.ALL_CHERI_LINUX_TARGETS,
        CompilationTargets.LINUX_KERNEL_RISCV64_GCC,
        CompilationTargets.LINUX_KERNEL_AARCH64_GCC,
    )
    supported_riscv_cheri_standard = RiscvCheriISA.EXPERIMENTAL_STD093
    _default_architecture = CompilationTargets.CHERI_LINUX_RISCV64_PURECAP_093

    # Override default defconfig for CHERI-enabled kernels
    def default_defconfig(self) -> str:
        if self.crosscompile_target.is_cheri_purecap([CPUArchitecture.RISCV64]):
            return "qemu_riscv64cheripc_defconfig"
        elif self.crosscompile_target.is_cheri_purecap([CPUArchitecture.AARCH64]):
            return "morello_pcuabi_defconfig"
        else:
            return "defconfig"

    def configure(self, **kwargs):
        super().configure(**kwargs)
        linux_config = self.read_file(self.build_dir / ".config")
        if self.compiling_for_cheri():
            valid_config = False
            if self.config.pretend and not linux_config:
                valid_config = True  # Avoid false-positive error with --pretend
            if self.compiling_for_riscv(include_purecap=True):
                valid_config = "RISCV_CHERI=y\n" in linux_config
            elif self.compiling_for_aarch64(include_purecap=True):
                valid_config = "CONFIG_CHERI_PURECAP_UABI=y\n" in linux_config
            if not valid_config:
                self.fatal("Invalid configuration selected? CHERI support not enabled!")


class BuildMorelloLinux(BuildLinux):
    target = "morello-linux-kernel"
    repository = GitRepository(
        "https://git.morello-project.org/morello/kernel/linux.git", default_branch="morello/next"
    )
    # Morello Linux is actually built hybrid (at the moment), but in the future it will be purecap.
    # To avoid workarounds and long target names, mark it as LINUX_MORELLO_PURECAP here but it will
    # still be built as a hybrid kernel.
    _supported_architectures = CompilationTargets.ALL_MORELLO_LINUX_TARGETS
    _default_architecture = CompilationTargets.MORELLO_LINUX_MORELLO_PURECAP

    # Override default defconfig for CHERI-enabled kernels
    def default_defconfig(self) -> str:
        if self.crosscompile_target.is_cheri_purecap([CPUArchitecture.AARCH64]):
            return "morello_pcuabi_defconfig"
        else:
            return "defconfig"

    def configure(self, **kwargs) -> None:
        super().configure()
        # Default config only has VIRTIO_NET, not PCI_NET. This is to make
        # it work out of the box with cheribuild's QEMU with networking that
        # uses PCI.
        self._set_config("CONFIG_VIRTIO_PCI")
        self._set_config("CONFIG_VIRTIO_PCI_LEGACY")
        self.run_make("oldconfig")  # regen dependencies


class LaunchLinuxBase(LaunchQEMUBase, ABC):
    do_not_add_to_targets = True
    forward_ssh_port = False
    qemu_user_networking = True
    _uses_disk_image = False
    _enable_smbfs_support = False
    _add_virtio_rng = True

    def setup(self):
        super().setup()
        root_dir = self.cross_sysroot_path
        kernel = f"{root_dir}/boot/Image"
        initramfs = f"{root_dir}/boot/initramfs.cpio.gz"
        self._project_specific_options += ["-append", "init=/init", "-initrd", initramfs]
        # This is not enabled by default for AArch64
        self.qemu_options.can_boot_kernel_directly = True
        self.current_kernel = Path(kernel)


class LaunchUpstreamLinux(LaunchLinuxBase):
    target = "run-minimal-upstream"
    _supported_architectures = CompilationTargets.ALL_UPSTREAM_LINUX_TARGETS

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        return *super().dependencies(config), "upstream-linux-kernel", "upstream-busybox"


class LaunchCheriAllianceLinux(LaunchLinuxBase):
    target = "run-minimal-cheri-linux"
    _supported_architectures = CompilationTargets.ALL_CHERI_LINUX_TARGETS
    include_os_in_target_suffix = False  # Avoid adding -linux- as we are running cheri-linux

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        result = super().dependencies(config)
        if cls.get_crosscompile_target().is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            result += ("cheri-std093-opensbi-baremetal-riscv64-purecap",)
        return *result, "linux-kernel", "busybox"


class LaunchMorelloLinux(LaunchLinuxBase):
    target = "run-minimal-morello"
    _supported_architectures = CompilationTargets.ALL_MORELLO_LINUX_TARGETS

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        return *super().dependencies(config), "morello-linux-kernel", "morello-busybox"
