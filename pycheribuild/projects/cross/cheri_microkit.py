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
import os
import typing
import subprocess
from typing import ClassVar
from pathlib import Path

from .compiler_rt import BuildCompilerRtBuiltins
from .crosscompileproject import CompilationTargets, CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository
from ..project import ComputedDefaultValue
from ..run_qemu import LaunchQEMUBase
from ...qemu_utils import QemuOptions
from ..build_qemu import BuildCheriAllianceQEMU
from .opensbi import BuildAllianceOpenSBI

class BuildCheriseL4(CrossCompileAutotoolsProject):
    target = "cheri-sel4"
    repository = GitRepository("git@github.com:CTSRD-CHERI/seL4.git",
        default_branch="std-cheri-riscv-microkit", force_branch=True)
    supported_architectures = (
        CompilationTargets.FREESTANDING_RISCV64_ZPURECAP,
        CompilationTargets.FREESTANDING_RISCV64,
    )
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def configure(self):
        pass

    def needs_configure(self):
        return False

    def compile(self, **kwargs):
        pass

    def install(self, **kwargs):
        pass

    def process(self):
        super().process()

class BuildCheriMicrokit(CrossCompileAutotoolsProject):
    repository = GitRepository(
        "git@github.com:CTSRD-CHERI/CHERI-Microkit.git", force_branch=True, default_branch="std-cheri-riscv"
    )
    target = "cheri-microkit"
    release_version = "2.0.1-dev"
    dependencies = ("cheri-alliance-opensbi", "cheri-sel4", "cheri-alliance-qemu")
    native_install_dir = DefaultInstallDir.CHERI_ALLIANCE_SDK
    is_sdk_target = True
    needs_sysroot = False  # We don't need a complete sysroot
    supported_architectures = (
        CompilationTargets.FREESTANDING_RISCV64_ZPURECAP,
        CompilationTargets.FREESTANDING_RISCV64,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def setup(self):
        super().setup()

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
            ))

        cls.boards: str = typing.cast(
            str,
            cls.add_config_option(
                "boards",
                metavar="BOARDS",
                show_help=True,
                default="qemu_virt_riscv64",
                help="CHERI-Microkit comma-separated list of boards to build for.",
            ))

        cls.example: str = typing.cast(
            str,
            cls.add_config_option(
                "example",
                metavar="EXAMPLE",
                show_help=True,
                default="hierarchy",
                help="CHERI-Microkit example to build (hello, hierarchy, passive_server, etc).",
            ))
        cls.build_all: str = typing.cast(
            str,
            cls.add_bool_option(
                "build_all",
                show_help=True,
                default=False,
                help="Build all Microkit's configs, targets, boards, etc. ",
            ))

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

        if not self.build_all:
            cmdline += ["--configs", "cheri" if self.target_info.target.is_cheri_purecap() else "debug"]

            if self.boards:
                cmdline += ["--boards", self.boards]

        self.run_cmd(cmdline, cwd=self.source_dir)

        if self.example:
            for board in self.boards.split(","):
                cmdline = [
                    "./pyenv/bin/python",
                    "dev_build.py",
                    "--board",
                    board,
                    "--example",
                    self.example,
                    "--rebuild",
                    "--llvm",
                    ]
                if self.target_info.target.is_cheri_purecap():
                    cmdline += ["--cheri"]
                    cmdline += ["--config", "cheri"]
                else:
                    cmdline += ["--config", "debug"]

                self.run_cmd(cmdline, cwd=self.source_dir)

                self.move_file(
                    self.source_dir / str("tmp_build/loader.img"),
                    self.real_install_root_dir / str(self.example + "-cheri-sel4-microkit-" + board + ".img"))

    def install(self, **kwargs):
        self.clean_directory(self.install_dir / str("microkit-sdk-" + self.release_version))
        self.move_file(
            self.source_dir / str("release/microkit-sdk-" + self.release_version),
            self.install_dir,
            force=True)

    def run_tests(self):
        self.opensbi_project = BuildAllianceOpenSBI.get_instance(self)
        options = QemuOptions(self.crosscompile_target)
        options.machine_flags=["-M", "virt", "-cpu", "codasip-a730", "-smp", "1"]
        options.memory_size="3G"
        self.run_cmd(
            options.get_commandline(
                qemu_command=BuildCheriAllianceQEMU.qemu_binary(self),
                add_network_device=False,
                bios_args=["-bios", self.opensbi_project.install_dir / "share/opensbi/l64pc128/generic/firmware//fw_jump.elf"],
                kernel_file=self.install_dir / str(self.example + "-cheri-sel4-microkit-" + self.boards + ".img"),
            ))

    def clean(self):
        self.clean_directory(self.source_dir / "release")
        self.clean_directory(self.source_dir / "build")
        self.clean_directory(self.source_dir / "tmp_build")
        self.clean_directory(self.install_dir / str("microkit-sdk-" + self.release_version))
        return super().clean()

    def is_virtualenv_ready(self, venv_path, requirements_file):
        python_bin = venv_path / "bin" / "python"
        pip_check_cmd = [str(python_bin), "-m", "pip", "check"]
        try:
            # Ensure the venv exists
            if not python_bin.exists():
                return False
            # This checks for dependency conflicts and missing packages
            subprocess.run(pip_check_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Optionally verify if all packages in requirements.txt are installed
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
            self.run_cmd(["./pyenv/bin/pip", "install", "--upgrade", "pip", "setuptools", "wheel"], cwd=self.source_dir)
            self.run_cmd(["./pyenv/bin/pip", "install", "-r", "requirements.txt"], cwd=self.source_dir)

    def needs_configure(self):
        return True

    def process(self):
        super().process()
