#
# Copyright (c) 2018 Jessica Clarke
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

from typing import Optional

from .cheribsd import ConfigPlatform
from .crosscompileproject import CompilationTargets, CrossCompileAutotoolsProject
from ..build_qemu import BuildQEMU
from ..project import (
    BuildType,
    CheriConfig,
    ComputedDefaultValue,
    CrossCompileTarget,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
    Project,
)
from ...qemu_utils import QemuOptions


class BuildBBLBase(CrossCompileAutotoolsProject):
    do_not_add_to_targets = True
    repository = GitRepository("https://github.com/CTSRD-CHERI/riscv-pk",
                               force_branch=True, default_branch="cheri_purecap",
                               # Compilation fixes for clang and support for CHERI
                               old_urls=[b"https://github.com/jrtc27/riscv-pk.git"])
    make_kind = MakeCommandKind.GnuMake
    _always_add_suffixed_targets = True
    is_sdk_target = False
    needs_sysroot = False  # Should be buildable without a sysroot
    kernel_class = None
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    without_payload = False
    enable_zero_bss = False
    custom_payload: Optional[str] = None
    mem_start = "0x80000000"

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        result = super().dependencies(config)
        if cls.kernel_class:
            result += (cls.kernel_class.get_class_for_target(cls.get_crosscompile_target()).target, )
        return result

    def setup(self):
        self.COMMON_LDFLAGS.extend(["-nostartfiles", "-nostdlib", "-static"])
        self.CFLAGS.extend(["-nostartfiles", "-nostdlib", "-static", "-ffreestanding"])
        self.COMMON_FLAGS.append("-nostdlib")
        super().setup()
        self.common_warning_flags.append("-Werror=undef")
        self.common_warning_flags.append("-Werror=return-type")
        self.common_warning_flags.append("-Wall")

        if self.crosscompile_target.is_hybrid_or_purecap_cheri():
            # We have to build a purecap if we want to support CHERI
            self.configure_args.append("--with-abi=l64pc128")
            # Enable CHERI extensions
            self.configure_args.append("--with-arch=rv64imafdcxcheri")
        else:
            self.configure_args.append("--with-abi=lp64")
            self.configure_args.append("--with-arch=rv64imafdc")

        self.configure_args.append("--with-mem-start=" + self.mem_start)

        if self.build_type == BuildType.DEBUG:
            self.configure_args.append("--enable-logo")  # For debugging

        self.configure_args.append("--disable-fp-emulation")  # Should not be needed

        # BBL build uses weird objcopy flags and therefore requires GNU objcopy if you want to build everything
        # Fortunetaly we don't need this when building only BBL.
        self.add_configure_and_make_env_arg("OBJCOPY", self.sdk_bindir / "llvm-objcopy")
        self.add_configure_and_make_env_arg("READELF", self.sdk_bindir / "llvm-readelf")
        self.add_configure_and_make_env_arg("RANLIB", self.target_info.ranlib)
        self.add_configure_and_make_env_arg("AR", self.target_info.ar)

        if self.without_payload:
            # Build an OpenSBI fw_jump style BBL
            assert self.kernel_class is None
            self.configure_args.append("--without-payload")
        elif self.custom_payload:
            self.configure_args.append("--with-payload=" + str(self.custom_payload))
        else:
            # Add the kernel as a payload:
            assert self.kernel_class is not None
            kernel_project = self.kernel_class.get_instance(self)
            kernel_config = kernel_project.default_kernel_config(ConfigPlatform.QEMU)
            kernel_path = kernel_project.get_kernel_install_path(kernel_config)
            self.configure_args.append("--with-payload=" + str(kernel_path))

        # XXX: Should this explicitly disable once updated bbl is widespread?
        if self.enable_zero_bss:
            self.configure_args.append("--enable-zero-bss")

    def compile(self, **kwargs):
        self.run_make("bbl")

    def install(self, **kwargs):
        self.install_file(self.build_dir / "bbl", self.real_install_root_dir / "bbl")

    @classmethod
    def get_installed_kernel_path(cls, caller, config: "Optional[CheriConfig]" = None,
                                  cross_target: "Optional[CrossCompileTarget]" = None):
        return cls.get_instance(caller, config=config, cross_target=cross_target).real_install_root_dir / "bbl"


def _bbl_install_dir(config: CheriConfig, project: Project):
    dir_name = project.crosscompile_target.generic_arch_suffix.replace("baremetal-", "")
    return config.cheri_sdk_dir / ("bbl" + project.build_dir_suffix) / dir_name


class BuildBBLTestPayload(BuildBBLBase):
    target = "bbl-test-payload"
    default_directory_basename = "bbl"  # reuse same source dir
    build_dir_suffix = "-test-payload"  # but not the build dir
    cross_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    supported_architectures = (CompilationTargets.FREESTANDING_RISCV64_PURECAP, CompilationTargets.FREESTANDING_RISCV64)
    custom_payload = "dummy_payload"

    def setup(self):
        super().setup()
        self.configure_args.append("--enable-logo")
        self.configure_args.append("--enable-print-device-tree")

    def run_tests(self) -> None:
        options = QemuOptions(self.crosscompile_target)
        self.run_cmd(options.get_commandline(
            qemu_command=BuildQEMU.qemu_binary(self), add_network_device=False, bios_args=["-bios", "none"],
            kernel_file=self.build_dir / "bbl"),
            give_tty_control=True, cwd="/")


# Build BBL without an embedded payload
class BuildBBLNoPayload(BuildBBLBase):
    target = "bbl"
    default_directory_basename = "bbl"
    without_payload = True
    cross_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR
    supported_architectures = (CompilationTargets.FREESTANDING_RISCV64_PURECAP, CompilationTargets.FREESTANDING_RISCV64)
    _default_install_dir_fn = ComputedDefaultValue(function=_bbl_install_dir,
                                                   as_string="$SDK_ROOT/bbl/riscv{32,64}{,-purecap}")

    def install(self):
        super().install()
        # Only install BuildBBLNoPayload as the QEMU bios and not the GFE version by checking build_dir_suffix
        if self.crosscompile_target.is_cheri_purecap() and not self.build_dir_suffix:
            # Install into the QEMU firware directory so that `-bios default` works
            qemu_fw_dir = BuildQEMU.get_firmware_dir(self, cross_target=CompilationTargets.NATIVE)
            self.makedirs(qemu_fw_dir)
            self.run_cmd(self.sdk_bindir / "llvm-objcopy", "-S", "-O", "binary",
                         self.get_installed_kernel_path(self), qemu_fw_dir / "bbl-riscv64cheri-virt-fw_jump.bin")


class BuildBBLNoPayloadGFE(BuildBBLNoPayload):
    mem_start = "0xc0000000"
    target = "bbl-gfe"
    default_directory_basename = "bbl"  # reuse same source dir
    build_dir_suffix = "-gfe"  # but not the build dir
    enable_zero_bss = True

    _default_install_dir_fn = ComputedDefaultValue(function=_bbl_install_dir,
                                                   as_string="$SDK_ROOT/bbl-gfe/riscv{32,64}{,-purecap}")
