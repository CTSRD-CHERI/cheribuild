#
# Copyright (c) 2020 Jessica Clarke
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

from ..build_qemu import BuildQEMU
from ..project import (BuildType, CheriConfig, ComputedDefaultValue, CrossCompileTarget, DefaultInstallDir,
                       GitRepository, MakeCommandKind, Project)
from ...config.compilation_targets import CompilationTargets


def uboot_install_dir(config: CheriConfig, project: "BuildUBoot") -> Path:
    return config.cheri_sdk_dir / ("u-boot" + project.build_dir_suffix) / project.uboot_suffix


class BuildUBoot(Project):
    target = "u-boot"
    repository = GitRepository("https://github.com/CTSRD-CHERI/u-boot",
                               default_branch="cheri")
    dependencies = ["compiler-rt-builtins"]
    needs_sysroot = False  # We don't need a complete sysroot
    default_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR
    default_build_type = BuildType.RELWITHDEBINFO
    supported_architectures = [
        CompilationTargets.BAREMETAL_NEWLIB_RISCV64_HYBRID,
        CompilationTargets.BAREMETAL_NEWLIB_RISCV64,
        # Won't compile yet: CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP
        ]
    make_kind = MakeCommandKind.GnuMake
    _always_add_suffixed_targets = True
    _default_install_dir_fn: ComputedDefaultValue[Path] = \
        ComputedDefaultValue(function=uboot_install_dir,
                             as_string="$SDK_ROOT/u-boot/riscv{32,64}{,-hybrid,-purecap}")

    def __init__(self, config) -> None:
        super().__init__(config)
        self.add_required_system_tool("dtc", apt="device-tree-compiler", homebrew="dtc")
        self.kconfig_overrides = dict()

    def setup(self) -> None:
        super().setup()
        compflags = " " + self.commandline_to_str(self.essential_compiler_and_linker_flags)
        compflags += " -Qunused-arguments"
        self.make_args.set(
            O=self.build_dir,  # output dir
            I=self.install_dir,  # install dir
            # No AS= as needs to be gas if used, but only used to query
            # binutils version in arch/arm to work around an old bug.
            LD=self.target_info.linker,
            CC=str(self.CC) + compflags,
            CPP=str(self.CPP) + compflags,
            AR=self.sdk_bindir / "llvm-ar",
            NM=self.sdk_bindir / "llvm-nm",
            STRIP=self.sdk_bindir / "llvm-strip",
            # Wants to remove .dynstr in .so's; this is ok as they don't have
            # any relocations (and BFD lets it do this).
            OBJCOPY=str(self.sdk_bindir / "llvm-objcopy") + " --allow-broken-links",
            OBJDUMP=self.sdk_bindir / "llvm-objdump",
            )

        self.kconfig_overrides = {
            "CONFIG_SREC": False,
            "CONFIG_GAP_FILL": False,
        }

        if self.config.verbose:
            self.make_args.set(V=True)

    @property
    def platform(self) -> str:
        if self.crosscompile_target.is_riscv():
            return "qemu-riscv64_smode"
        assert False, "unhandled target"

    @property
    def uboot_suffix(self) -> str:
        return self.crosscompile_target.generic_arch_suffix.replace("baremetal-", "")

    @property
    def firmware_path(self) -> Path:
        # Prefer install path in QEMU for the QEMU firmware
        if not self.build_dir_suffix:
            qemu_fw_dir = BuildQEMU.get_firmware_dir(self, cross_target=CompilationTargets.NATIVE)
            return qemu_fw_dir / ("u-boot-" + self.uboot_suffix)
        return self.install_dir / "u-boot"

    @classmethod
    def get_firmware_path(cls, caller, config: CheriConfig = None, cross_target: CrossCompileTarget = None):
        return cls.get_instance(caller, config=config, cross_target=cross_target).firmware_path

    def configure(self, **kwargs):
        self.run_make(self.platform + "_defconfig")

        def override_config(old):
            new = []
            for line in old:
                for key, value in self.kconfig_overrides.items():
                    if line.startswith(key + "=") or line.startswith("# " + key + " is not set"):
                        if isinstance(value, bool):
                            value = "y" if value else "n"
                        line = key + "=" + value
                        break
                new.append(line)
            return new

        self.rewrite_file(self.build_dir / ".config", override_config)

    def install(self, **kwargs):
        self.install_file(self.build_dir / "u-boot", self.install_dir / "u-boot")
        self.install_file(self.build_dir / "u-boot.bin", self.install_dir / "u-boot.bin")
        # Only install BuildUBoot as the QEMU firmware and not any other derived version by checking build_dir_suffix
        if not self.build_dir_suffix:
            # Install into the QEMU firware directory so that `-bios default` works
            qemu_fw_path = self.firmware_path
            assert qemu_fw_path != self.install_dir / "u-boot"
            self.install_file(self.build_dir / "u-boot", qemu_fw_path)

    def run_make(self, *args, **kwargs):
        if 'cwd' in kwargs:
            assert kwargs['cwd'] == self.build_dir
            del kwargs['cwd']
        super().run_make(*args, **kwargs, cwd=self.source_dir)
