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
    CheriConfig,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
)
from ..run_qemu import LaunchQEMUBase
from ...config.chericonfig import RiscvCheriISA
from ...config.compilation_targets import CompilationTargets
from ...config.target_info import CPUArchitecture
from ...utils import classproperty


class BuildLinux(CrossCompileAutotoolsProject):
    target = "linux-kernel"
    repository = GitRepository("https://github.com/torvalds/linux.git")
    needs_sysroot = False
    is_sdk_target = False
    supported_architectures = (
        CompilationTargets.LINUX_RISCV64,
        CompilationTargets.LINUX_AARCH64,
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
        self.run_cmd(self.source_dir / "scripts/config", "--set-val", option, value, cwd=self.build_dir)
        # Also handle auto-detected config value which would overwrite our manual setting above
        # This happens e.g. with CONFIG_CC_HAS_ASM_GOTO_OUTPUT.
        auto_config_args = (self.source_dir / "scripts/config", "--file", self.build_dir / "include/config/auto.conf")
        auto_value = self.run_cmd(*auto_config_args, "--state", option, capture_output=True)
        if auto_value.stdout != b"undef":
            self.run_cmd(*auto_config_args, "--set-val", option, value)

    def setup(self) -> None:
        super().setup()
        self.make_args.set(CROSS_COMPILE=str(self.CC.parent) + "/", LLVM=str(self.CC.parent) + "/")
        if self.crosscompile_target.is_riscv(include_purecap=True):
            self.linux_arch = "riscv"
        elif self.crosscompile_target.is_aarch64(include_purecap=True):
            self.linux_arch = "arm64"

        self.make_args.set(ARCH=self.linux_arch)
        self.make_args.set(O=self.build_dir)

        # We only support building the kernel with LLVM/Clang
        self.make_args.set(HOSTCC=self.host_CC)
        self.make_args.set(HOSTCXX=self.host_CXX)
        # Install kernel headers at rootfs (and sysroot)'s path
        self.make_args.set(INSTALL_HDR_PATH=self.install_dir / "usr")

        # Don't overwrite our manually edited .config file with default values
        self.make_args.set_env(KCONFIG_NOSILENTUPDATE=1)

        if self.config.verbose:
            self.make_args.set(V=True)

    @property
    def defconfig(self) -> str:
        return "defconfig"

    def compile(self, **kwargs):
        if self.compiling_for_riscv(include_purecap=True):
            # Work around https://github.com/ClangBuiltLinux/linux/issues/2092
            ccinfo = self.get_compiler_info(self.CC)
            # FIXME: apparently this value is always overwritten by the build system with the default value no
            # matter what I do, just print a warning for now
            if ccinfo.is_clang and False:
                self.info("Working around https://github.com/ClangBuiltLinux/linux/issues/2092")
                self._set_config("CONFIG_CC_HAS_ASM_GOTO_OUTPUT", "n")
                # self.run_make("savedefconfig", parallel=False)
                # self.run_make("oldconfig", parallel=False)
                self.run_cmd(
                    self.source_dir / "scripts/config", "--state", "CONFIG_CC_HAS_ASM_GOTO_OUTPUT", cwd=self.build_dir
                )
            else:
                self.warning("Need to working around https://github.com/ClangBuiltLinux/linux/issues/2092")
                self.warning(
                    "See patch in https://lore.kernel.org/all/20250811-riscv-wa-llvm-asm-goto-outputs-"
                    "assertion-failure-v1-1-7bb8c9cbb92b@kernel.org/"
                )
        self.run_make()

    def configure(self, **kwargs):
        self.run_make(self.defconfig, cwd=self.source_dir, parallel=False)

    def install(self, **kwargs):
        self.install_file(self.build_dir / "vmlinux", self.install_dir / "boot/vmlinux")
        self.install_file(self.build_dir / "System.map", self.install_dir / "boot/System.map")
        self.install_file(self.build_dir / f"arch/{self.linux_arch}/boot/Image", self.install_dir / "boot/Image")
        self.install_file(self.build_dir / f"arch/{self.linux_arch}/boot/Image.gz", self.install_dir / "boot/Image.gz")
        self.run_make("headers_install", cwd=self.source_dir)


class BuildCheriAllianceLinux(BuildLinux):
    target = "cheri-std093-linux-kernel"
    repository = GitRepository("https://github.com/CHERI-Alliance/linux.git", default_branch="codasip-cheri-riscv")
    supported_architectures = (CompilationTargets.LINUX_RISCV64_PURECAP,)
    supported_riscv_cheri_standard = RiscvCheriISA.EXPERIMENTAL_STD093

    @property
    def defconfig(self) -> str:
        if self.crosscompile_target.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            return "cheri_full_defconfig"
        else:
            return "defconfig"

    def configure(self, **kwargs):
        super().configure(**kwargs)
        config = self.read_file(self.build_dir / ".config")
        if "RISCV_CHERI=y\n" not in config:
            self.fatal("Invalid configuration selected? CHERI support not enabled!")


class BuildMorelloLinux(BuildLinux):
    target = "morello-linux-kernel"
    repository = GitRepository(
        "https://git.morello-project.org/morello/kernel/linux.git", default_branch="morello/next"
    )
    # Morello Linux is actually built hybrid (at the moment), but in the future it will be purecap.
    # To avoid workarounds and long target names, mark it as LINUX_MORELLO_PURECAP here but it will
    # still be built as a hybrid kernel.
    supported_architectures = (CompilationTargets.LINUX_MORELLO_PURECAP,)

    @property
    def defconfig(self) -> str:
        if self.crosscompile_target.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            return "morello_transitional_pcuabi_defconfig"
        else:
            return "defconfig"

    def configure(self, **kwargs) -> None:
        super().configure()
        # Default config only has VIRTIO_NET, not PCI_NET. This is to make
        # it work out of the box with cheribuild's QEMU with networking that
        # uses PCI.
        self._set_config("CONFIG_VIRTIO_PCI")
        self._set_config("CONFIG_VIRTIO_PCI_LEGACY")


class LaunchCheriLinux(LaunchQEMUBase):
    target = "run-minimal"
    supported_architectures = (
        CompilationTargets.LINUX_MORELLO_PURECAP,
        CompilationTargets.LINUX_RISCV64_PURECAP,
        CompilationTargets.LINUX_RISCV64,
        CompilationTargets.LINUX_AARCH64,
    )
    forward_ssh_port = False
    qemu_user_networking = True
    _uses_disk_image = False
    _enable_smbfs_support = False
    _add_virtio_rng = True

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        result = super().dependencies(config)
        if cls.get_crosscompile_target().is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            result += ("cheri-std093-linux-kernel",)
            result += ("cheri-std093-opensbi-baremetal-riscv64-purecap",)
            # TODO: Add more projects (eg busybox and muslc once released and is public)
        elif cls.get_crosscompile_target().is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            result += ("morello-linux-kernel",)
            result += ("morello-muslc",)
            result += ("morello-compiler-rt-builtins",)
            result += ("morello-busybox",)
        else:
            result += ("linux-kernel",)
            result += ("muslc",)
            result += ("compiler-rt-builtins",)
            result += ("busybox",)

        return result

    def setup(self):
        super().setup()

        if self.crosscompile_target.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            linux_project = BuildMorelloLinux.get_instance(self, self.config)
        elif self.crosscompile_target.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            linux_project = BuildCheriAllianceLinux.get_instance(self, self.config)
        else:
            linux_project = BuildLinux.get_instance(self, self.config)

        kernel = f"{linux_project.install_dir}/boot/Image"
        initramfs = f"{linux_project.install_dir}/boot/initramfs.cpio.gz"

        if self.crosscompile_target.is_aarch64(include_purecap=True):
            if self.crosscompile_target.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
                cpu = "morello"
            else:
                cpu = "cortex-a53"
            self.qemu_options.machine_flags = [
                "-M",
                "virt",
                "-cpu",
                cpu,
                "-smp",
                1,
                "-kernel",
                kernel,
                "-initrd",
                initramfs,
                "-append",
                "init=/init",
            ]

        self.current_kernel = Path(kernel)
