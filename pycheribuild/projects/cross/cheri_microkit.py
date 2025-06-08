#
# SPDX-License-Identifier: BSD-2-Clause
#
# Author: Hesham Almatary <Hesham.Almatary@cl.cam.ac.uk>
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
import threading
import typing
from pathlib import Path

from .crosscompileproject import (
    CompilationTargets,
    CrossCompileAutotoolsProject,
    CrossCompileProject,
    DefaultInstallDir,
    GitRepository,
)
from ..build_qemu import BuildCheriAllianceQEMU, BuildQEMU
from ..project import CheriConfig, CPUArchitecture
from ..run_qemu import LaunchQEMUBase
from ...config.chericonfig import RiscvCheriISA


class BuildCheriseL4(CrossCompileProject):
    target = "cheri-sel4"
    repository = GitRepository(
        "https://github.com/CHERI-Alliance/CHERI-seL4.git",
        default_branch="cheri-microkit",
        force_branch=True,
    )
    _supported_architectures = (
        CompilationTargets.FREESTANDING_RISCV64,
        CompilationTargets.FREESTANDING_RISCV64_PURECAP,
        CompilationTargets.FREESTANDING_MORELLO_NO_CHERI,
        CompilationTargets.FREESTANDING_MORELLO_PURECAP,
    )
    supported_riscv_cheri_standard = RiscvCheriISA.EXPERIMENTAL_STD093

    def compile(self, **kwargs):
        pass

    def install(self, **kwargs):
        pass


class BuildCheriMicrokit(CrossCompileAutotoolsProject):
    repository = GitRepository(
        "https://github.com/CHERI-Alliance/CHERI-Microkit.git",
        default_branch="cheri",
        force_branch=True,
    )
    target = "cheri-microkit"
    release_version = "2.0.1-dev"
    dependencies = ("cheri-sel4",)
    native_install_dir = DefaultInstallDir.CHERI_ALLIANCE_SDK
    is_sdk_target = False
    _needs_sysroot = False
    _supported_architectures = (
        CompilationTargets.FREESTANDING_RISCV64,
        CompilationTargets.FREESTANDING_RISCV64_PURECAP,
        CompilationTargets.FREESTANDING_MORELLO_NO_CHERI,
        CompilationTargets.FREESTANDING_MORELLO_PURECAP,
    )
    supported_riscv_cheri_standard = RiscvCheriISA.EXPERIMENTAL_STD093

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.configs: str = typing.cast(
            str,
            cls.add_config_option(
                "configs",
                metavar="CONFIGS",
                show_help=True,
                default="cheri",
                help="CHERI-Microkit comma-separated configs (release, debug, benchmark, and cheri).",
            ),
        )
        cls.boards: str = typing.cast(
            str,
            cls.add_config_option(
                "boards",
                metavar="BOARDS",
                show_help=True,
                default=None,
                help="CHERI-Microkit comma-separated list of boards to build for.",
            ),
        )
        cls.example: str = typing.cast(
            str,
            cls.add_config_option(
                "example",
                metavar="EXAMPLE",
                show_help=True,
                default="hello,hierarchy,passive_server,rust",
                help="CHERI-Microkit example to build.",
            ),
        )
        cls.build_all: str = typing.cast(
            str,
            cls.add_bool_option(
                "build_all",
                show_help=True,
                default=False,
                help="Build all Microkit configs, targets, boards, etc.",
            ),
        )

    def compile(self, **kwargs):
        cmdline = [
            "./pyenv/bin/python",
            "build_sdk.py",
            "--sel4",
            BuildCheriseL4.get_source_dir(self),
            "--skip-docs",
            "--skip-tar",
            "--llvm",
        ]

        if self.boards is None:
            if self.crosscompile_target.is_riscv(include_purecap=True):
                self.boards = "qemu_virt_riscv64"
            elif self.crosscompile_target.is_aarch64(include_purecap=True):
                self.boards = "morello_qemu"

        if not self.build_all:
            cmdline += ["--configs", self.configs]

            if self.boards:
                cmdline += ["--boards", self.boards]

        self.run_cmd(cmdline, cwd=self.source_dir)

        if self.example:
            for config in self.configs.split(","):
                if self.target_info.target.is_cheri_purecap() and config != "cheri":
                    self.fatal("Can't build purecap userspace on non-CHERI seL4")

                for ex in self.example.split(","):
                    for board in self.boards.split(","):
                        cmdline = [
                            "./pyenv/bin/python",
                            "dev_build.py",
                            "--board",
                            board,
                            "--example",
                            ex,
                            "--rebuild",
                            "--llvm",
                        ]

                        if self.target_info.target.is_cheri_purecap() and config == "cheri":
                            cmdline += ["--cheri"]

                        cmdline += ["--config", config]
                        self.run_cmd(cmdline, cwd=self.source_dir)
                        self.move_file(
                            self.source_dir / "tmp_build/loader.img",
                            self.install_dir / f"{ex}-cheri-sel4-microkit-{board}-{config}.img",
                        )

    def install(self, **kwargs):
        self.clean_directory(self.install_dir / f"microkit-sdk-{self.release_version}")
        self.move_file(
            self.source_dir / f"release/microkit-sdk-{self.release_version}",
            self.install_dir,
            force=True,
        )

    def run_tests(self):
        expected_output = {
            "hierarchy": "hello, world",
            "hello": "hello, world",
            "passive_server": "running on client",
            "rust": "hello, world from Rust!",
        }

        if self.config.pretend:
            return

        board = "qemu_virt_riscv64"
        qemu_cmd = []

        if self.compiling_for_aarch64(include_purecap=True):
            qemu = BuildQEMU.qemu_binary(self, xtarget=self.crosscompile_target)
            board = "morello_qemu"
            qemu_cmd += [
                qemu,
                "-cpu",
                "morello",
                "-m",
                "2G",
                "-smp",
                "1",
                "-machine",
                "virt,gic-version=2,virtualization=on",
                "-net",
                "none",
                "-nographic",
            ]
        elif (
            self.compiling_for_riscv(include_purecap=True)
            and self.config.riscv_cheri_isa == RiscvCheriISA.EXPERIMENTAL_STD093
        ):
            qemu = BuildCheriAllianceQEMU.qemu_binary(self, xtarget=self.crosscompile_target)
            bios_args = LaunchQEMUBase.riscv_bios_arguments(self.crosscompile_target, self)
            qemu_cmd += [
                qemu,
                "-cpu",
                "codasip-a730,cheri_pte=on,cheri_levels=2",
                "-m",
                "2G",
                "-smp",
                "1",
                "-machine",
                "virt",
                "-net",
                "none",
                "-nographic",
                *bios_args,
            ]

        for ex in self.example.split(","):
            qemu_command = list(qemu_cmd)

            kernel_file = str(self.install_dir / f"{ex}-cheri-sel4-microkit-{board}-{self.configs}.img")

            qemu_command += ["-kernel", kernel_file]

            if self.compiling_for_aarch64(include_purecap=True):
                qemu_command += ["-device", "loader,file=" + kernel_file + ",addr=0x70000000,cpu-num=0"]

            print(qemu_command)
            proc = subprocess.Popen(
                qemu_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            found = False

            def monitor_output():
                nonlocal found
                for line in proc.stdout:
                    print(line, end="")
                    if expected_output[ex] in line:
                        found = True
                        proc.terminate()
                        break

            monitor_thread = threading.Thread(target=monitor_output)
            monitor_thread.start()
            monitor_thread.join(timeout=30)

            if monitor_thread.is_alive():
                proc.terminate()
                monitor_thread.join()

            proc.wait(timeout=2)

            if found:
                print(f"✅ CHERI-Microkit's {ex} example succeeded.")
            else:
                raise RuntimeError(f"❌ CHERI-Microkit's {ex} example failed.")

    def clean(self):
        self.clean_directory(self.source_dir / "release")
        self.clean_directory(self.source_dir / "build")
        self.clean_directory(self.source_dir / "tmp_build")
        self.clean_directory(self.install_dir / f"microkit-sdk-{self.release_version}")
        return super().clean()

    def is_virtualenv_ready(self, venv_path, requirements_file):
        python_bin = venv_path / "bin" / "python"
        pip_check_cmd = [str(python_bin), "-m", "pip", "check"]
        try:
            # Ensure the venv exists
            if not python_bin.exists():
                return False
            subprocess.run(pip_check_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            installed = subprocess.check_output([str(python_bin), "-m", "pip", "freeze"]).decode()
            with open(requirements_file) as f:
                for line in f:
                    if line.strip() and not line.startswith("#"):
                        if not any(line.split("==")[0] in pkg for pkg in installed.splitlines()):
                            return False
            return True
        except Exception:
            return False

    def configure(self):
        venv_path = Path(self.source_dir) / "pyenv"
        req_file = Path(self.source_dir) / "requirements.txt"
        if not self.is_virtualenv_ready(venv_path, req_file):
            self.run_cmd(["python3", "-m", "venv", "pyenv"], cwd=self.source_dir)
            self.run_cmd(
                ["./pyenv/bin/pip", "install", "--upgrade", "pip", "setuptools", "wheel"],
                cwd=self.source_dir,
            )
            self.run_cmd(
                ["./pyenv/bin/pip", "install", "-r", "requirements.txt"],
                cwd=self.source_dir,
            )

    def needs_configure(self):
        return True


class LaunchCheriMicrokitQEMU(LaunchQEMUBase):
    target = "run-cheri-microkit"
    _supported_architectures = (
        CompilationTargets.FREESTANDING_MORELLO_PURECAP,
        CompilationTargets.FREESTANDING_RISCV64_PURECAP,
    )
    supported_riscv_cheri_standard = RiscvCheriISA.EXPERIMENTAL_STD093
    forward_ssh_port = False
    qemu_user_networking = False
    _uses_disk_image = False
    _enable_smbfs_support = False
    _add_virtio_rng = False
    board = "qemu_virt_riscv64"

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        result = tuple()
        result += ("cheri-microkit",)
        if cls.get_crosscompile_target().is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            result += ("cheri-std093-llvm",)
            result += ("cheri-std093-opensbi",)
            result += ("cheri-std093-gdb-native",)
            result += ("cheri-std093-qemu",)
        elif cls.get_crosscompile_target().is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            result += ("morello-llvm-native",)
            result += ("gdb-native",)
            result += ("qemu",)
        return result

    def setup(self):
        super().setup()
        cheri_microkit = BuildCheriMicrokit.get_instance(self, self.config)

        if self.crosscompile_target.is_aarch64(include_purecap=True):
            self.board = "morello_qemu"

        bootable_img = f"{cheri_microkit.install_dir}/hierarchy-cheri-sel4-microkit-{self.board}-cheri.img"

        if self.crosscompile_target.is_aarch64(include_purecap=True):
            self.qemu_options.machine_flags = [
                "-M",
                "virt,gic-version=2,virtualization=on",
                "-cpu",
                "morello",
                "-smp",
                1,
            ]
            self.qemu_options.machine_flags += ["-device", "loader,file=" + bootable_img + ",addr=0x70000000,cpu-num=0"]

        self.current_kernel = Path(bootable_img)
