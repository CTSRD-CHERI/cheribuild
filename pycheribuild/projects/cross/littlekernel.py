#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright 2021 Alex Richardson
# Copyright 2021 Google UK
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
from pathlib import Path

from .compiler_rt import BuildCompilerRtBuiltins
from .crosscompileproject import CompilationTargets, CrossCompileMakefileProject, GitRepository
from ..project import DefaultInstallDir
from ..run_qemu import LaunchQEMUBase
from ..simple_project import BoolConfigOption
from ...config.target_info import CrossCompileTarget
from ...qemu_utils import riscv_bios_arguments
from ...utils import classproperty


class BuildLittleKernel(CrossCompileMakefileProject):
    target = "littlekernel"
    default_directory_basename = "lk"
    supported_architectures = (
        CompilationTargets.FREESTANDING_MORELLO_NO_CHERI,
        CompilationTargets.FREESTANDING_MORELLO_PURECAP,
        CompilationTargets.FREESTANDING_RISCV64,
        CompilationTargets.FREESTANDING_RISCV64_PURECAP,
    )
    repository = GitRepository(
        "https://github.com/littlekernel/lk",
        temporary_url_override="https://github.com/arichardson/lk.git",
        url_override_reason="Fixes to allow building with Clang",
    )
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    set_pkg_config_path = False
    needs_sysroot = False
    build_in_source_dir = False
    # We have to override CC, etc. on the command line rather than in the environment:
    set_commands_on_cmdline = True
    include_os_in_target_suffix = False  # Avoid adding -baremetal
    use_mmu = BoolConfigOption("use-mmu", help="Compile with MMU support", default=False)

    @classmethod
    def needs_compiler_rt(cls):
        return cls.get_crosscompile_target().cpu_architecture.is_32bit()

    @classmethod
    def dependencies(cls, _) -> "tuple[str, ...]":
        return ("compiler-rt-builtins",) if cls.needs_compiler_rt() else tuple()

    @property
    def build_dir_suffix(self):
        if self.use_mmu:
            return "-mmu"
        return ""

    def compiler_rt_builtins_path(self) -> Path:
        path = BuildCompilerRtBuiltins.get_build_dir(self) / f"lib/baremetal/libclang_rt.builtins-{self.triple_arch}.a"
        if not path.exists():
            self.dependency_error(
                "Compiler builtins library", path, "does not exist", cheribuild_target="compiler-rt-builtins"
            )
        return path

    @property
    def essential_compiler_and_linker_flags(self) -> "list[str]":
        if self.compiling_for_cheri():
            return [
                *self.target_info.get_essential_compiler_and_linker_flags(softfloat=False),
                "-Werror=cheri-capability-misuse",
                "-Werror=shorten-cap-to-int",
            ]
        return ["--target=" + self.target_info.target_triple]

    def setup(self):
        super().setup()
        self.make_args.remove_var("CCLD")
        self.make_args.remove_var("CXXLD")
        self.make_args.set(BUILDROOT=self.build_dir)
        if self.config.verbose:
            self.make_args.set(NOECHO="")
        for var in ["CFLAGS", "CPPFLAGS", "CXXFLAGS", "LDFLAGS"]:
            del self.make_args.env_vars[var]
        toolchain_prefix = str(self.sdk_bindir) + "/"
        if self.compiling_for_riscv(include_purecap=True):
            # Use hardfloat to avoid libgcc deps
            self.make_args.set(RISCV_FPU=True)
        if self.compiling_for_cheri():
            self.make_args.set(ARCH_COMPILEFLAGS="")  # dont' override the default -mabi=

        self.set_make_cmd_with_args("LD", self.target_info.linker, ["--unresolved-symbols=report-all"])
        if self.crosscompile_target.is_riscv(include_purecap=True) and self.use_mmu:
            self.make_args.set(RISCV_MMU="sv39", RISCV_MODE="supervisor")
        self.make_args.set(
            TOOLCHAIN_PREFIX=toolchain_prefix,
            ARCH_arm64_TOOLCHAIN_PREFIX=toolchain_prefix,
            ARCH_riscv64_TOOLCHAIN_PREFIX=toolchain_prefix,
        )

    def setup_late(self) -> None:
        super().setup_late()
        self.make_args.set(LIBGCC=str(self.compiler_rt_builtins_path()) if self.needs_compiler_rt() else "")

    @property
    def kernel_path(self) -> Path:
        if self.compiling_for_aarch64(include_purecap=True):
            return self.build_dir / "build-qemu-virt-arm64-test/lk.elf"
        elif self.compiling_for_riscv(include_purecap=True):
            return self.build_dir / "build-qemu-virt-riscv64-test/lk.elf"
        else:
            raise ValueError("Unsupported arch")

    def run_tests(self):
        if self.compiling_for_aarch64(include_purecap=True):
            cmd = [
                self.config.qemu_bindir / "qemu-system-aarch64",
                "-cpu",
                "cortex-a53",
                "-m",
                "512",
                "-smp",
                "1",
                "-machine",
                "virt",
                "-net",
                "none",
                "-nographic",
                "-kernel",
                self.kernel_path,
            ]
        elif self.compiling_for_riscv(include_purecap=True):
            bios_args = ["-bios", "none"]
            if self.use_mmu:
                bios_args = riscv_bios_arguments(self.crosscompile_target, self)
            cmd = [
                self.config.qemu_bindir / "qemu-system-riscv64cheri",
                "-cpu",
                "rv64",
                "-m",
                "512",
                "-smp",
                "1",
                "-machine",
                "virt",
                "-net",
                "none",
                "-nographic",
                "-kernel",
                self.kernel_path,
                *bios_args,
            ]
        else:
            return self.fatal("Unsupported arch")
        self.run_cmd(cmd, cwd=self.build_dir, give_tty_control=True)

    def compile(self, **kwargs):
        if self.compiling_for_aarch64(include_purecap=True):
            self.run_make("qemu-virt-arm64-test", cwd=self.source_dir, parallel=True)
        elif self.compiling_for_riscv(include_purecap=True):
            self.run_make("qemu-virt-riscv64-test", cwd=self.source_dir, parallel=True)
        else:
            return self.fatal("Unsupported arch")


class LaunchLittlekernelQEMU(LaunchQEMUBase):
    target = "run-littlekernel"
    dependencies = ("littlekernel",)
    forward_ssh_port = False
    qemu_user_networking = False
    _enable_smbfs_support = False
    _add_virtio_rng = False
    _uses_disk_image = False

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return BuildLittleKernel.supported_architectures

    def setup(self):
        super().setup()
        lk_instance = BuildLittleKernel.get_instance(self)
        self.current_kernel = lk_instance.kernel_path
        # We boot directly even for aarch64
        self.qemu_options.can_boot_kernel_directly = True
        if self.compiling_for_aarch64(include_purecap=True):
            # FIXME: GICv3 support not included in lk, have to force version 2
            self.qemu_options.machine_flags = ["-M", "virt,gic-version=2", "-cpu", "cortex-a53"]

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(defaultSshPort=None, **kwargs)

    def get_riscv_bios_args(self) -> "list[str]":
        # Currently we run in machine mode
        lk_instance = BuildLittleKernel.get_instance(self)
        if not lk_instance.use_mmu:
            return ["-bios", "none"]
        return super().get_riscv_bios_args()

    def process(self):
        super().process()
