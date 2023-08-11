#
# Copyright (c) 2019 Alfredo Mazzinghi
# All rights reserved.
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

import json

from .build_qemu import BuildQEMU
from .cross.cheribsd import BuildCHERIBSD, CheriBSDConfigTable, ConfigPlatform
from .cross.crosscompileproject import CompilationTargets, CrossCompileProject
from .disk_image import BuildCheriBSDDiskImage
from .go import BuildGo
from .project import DefaultInstallDir, GitRepository, MakeCommandKind
from .simple_project import BoolConfigOption, SimpleProject
from ..config.computed_default_value import ComputedDefaultValue
from ..config.target_info import CPUArchitecture
from ..processutils import commandline_to_str
from ..qemu_utils import QemuOptions
from ..utils import OSInfo, ThreadJoiner


class BuildSyzkaller(CrossCompileProject):
    dependencies = ("go",)
    target = "cheri-syzkaller"
    repository = GitRepository("https://github.com/CTSRD-CHERI/cheri-syzkaller.git", force_branch=True,
                               default_branch="morello-syzkaller")
    # no_default_sysroot = None // probably useless??
    # skip_cheri_symlinks = True // llvm target only, useless here
    make_kind = MakeCommandKind.GnuMake

    # is_sdk_target = True
    supported_architectures = (
        CompilationTargets.CHERIBSD_MORELLO_HYBRID_FOR_PURECAP_ROOTFS,
        CompilationTargets.CHERIBSD_RISCV_HYBRID_FOR_PURECAP_ROOTFS,
    )
    default_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, project: config.cheri_sdk_dir,
        as_string="$CHERI_SDK_DIR",
    )

    if OSInfo.IS_FREEBSD:
        sysgen = BoolConfigOption(
            "run-sysgen",
            show_help=True,
            help="Rerun syz-extract and syz-sysgen to rebuild generated Go syscall descriptions.",
        )
    else:
        sysgen = False

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("go", apt="golang")

    def __init__(self, config, *args, **kwargs):
        super().__init__(config, *args, **kwargs)

    @staticmethod
    def _arch_to_syzstring(arch: CPUArchitecture):
        if arch == CPUArchitecture.X86_64:
            return "amd64"
        elif arch == CPUArchitecture.AARCH64:
            return "arm64"
        return arch.value

    @property
    def syz_arch(self):
        return self._arch_to_syzstring(self.crosscompile_target.cpu_architecture)

    def setup(self):
        super().setup()
        goroot = BuildGo.get_instance(self, cross_target=CompilationTargets.NATIVE).goroot_dir
        self.make_args.set_env(
            HOSTARCH=self._arch_to_syzstring(CompilationTargets.NATIVE.cpu_architecture),
            TARGETARCH=self.syz_arch,
            TARGETOS="freebsd",
            GOPATH=self.build_dir,
            GOROOT=goroot,
            CC=self.commandline_to_str([self.CC, *self.essential_compiler_and_linker_flags]),
            CXX=self.commandline_to_str([self.CXX, *self.essential_compiler_and_linker_flags]),
            ADDCFLAGS=self.commandline_to_str(self.default_compiler_flags + self.default_ldflags),
        )
        cflags = self.default_compiler_flags + self.default_ldflags
        self.make_args.set_env(CFLAGS=" ".join(cflags))
        self.make_args.set_env(PATH=f'{goroot / "bin"}:{self.config.dollar_path_with_other_tools}')

    def syzkaller_install_path(self):
        return self.real_install_root_dir / "bin"

    def syzkaller_binary(self):
        return self.syzkaller_install_path() / "syz-manager"

    def needs_configure(self) -> bool:
        return False

    def compile(self, **kwargs):
        if self.sysgen:
            self.generate()
        self.run_make(parallel=False, cwd=self.source_dir)

    def generate(self):
        cheribsd_target = self.crosscompile_target.get_rootfs_target()
        cheribsd_dir = BuildCHERIBSD.get_source_dir(self, cross_target=cheribsd_target)
        if not cheribsd_dir.exists():
            self.dependency_error("Missing CheriBSD source directory")
        with self.set_env(SOURCEDIR=cheribsd_dir):
            self.run_make("extract", parallel=False, cwd=self.source_dir)
            self.run_make("generate", parallel=False, cwd=self.source_dir)

    def install(self, **kwargs):
        native_build = self.source_dir / "bin"
        target_build = native_build / f"freebsd_{self.syz_arch}"
        syz_remote_install = self.syzkaller_install_path() / f"freebsd_{self.syz_arch}"

        self.makedirs(syz_remote_install)

        self.install_file(native_build / "syz-manager", self.syzkaller_binary(), mode=0o755)

        if not self.config.pretend:
            for fpath in target_build.iterdir():
                if fpath.is_file():
                    self.install_file(fpath, syz_remote_install / fpath.name, mode=0o755)

    def clean(self) -> ThreadJoiner:
        self.run_cmd(["chmod", "-R", "u+w", self.build_dir])
        self.run_make("clean", parallel=False, cwd=self.source_dir)
        joiner = super().clean()
        return joiner


class RunSyzkaller(SimpleProject):
    target = "run-syzkaller"
    supported_architectures = BuildSyzkaller.supported_architectures

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.syz_config = cls.add_optional_path_option(
            "syz-config", help="Path to the syzkaller configuration file to use.", show_help=True)
        cls.syz_ssh_key = cls.add_path_option(
            "ssh-privkey",
            show_help=True,
            default=lambda config, project: (config.source_root / "extra-files" / "syzkaller_id_rsa"),
            help=(
                "A directory with additional files that will be added to the image "
                "(default: '$SOURCE_ROOT/extra-files/syzkaller_id_rsa')"
            ),
            metavar="syzkaller_id_rsa",
        )
        cls.syz_workdir = cls.add_path_option(
            "workdir",
            show_help=True,
            default=lambda config, project: (config.output_root / "syzkaller-workdir"),
            help="Working directory for syzkaller output.",
            metavar="DIR",
        )
        cls.syz_debug = cls.add_bool_option(
            "debug",
            help="Run syz-manager in debug mode, requires manual startup of the VM.",
        )

    def syzkaller_config(self, syzkaller: BuildSyzkaller):
        """ Get path of syzkaller configuration file to use. """
        if self.syz_config:
            return self.syz_config
        else:
            xtarget = syzkaller.crosscompile_target.get_cheri_purecap_target()
            qemu_binary = BuildQEMU.qemu_binary(self, xtarget=xtarget)
            kernel_project = BuildCHERIBSD.get_instance(self, cross_target=xtarget)
            kernel_config = CheriBSDConfigTable.get_configs(xtarget, platform=ConfigPlatform.QEMU,
                                                            kernel_abi=kernel_project.get_default_kernel_abi(),
                                                            fuzzing=True)
            if len(kernel_config) == 0:
                self.fatal("No kcov kernel configuration found")
                return
            kernel_path = kernel_project.get_kernel_install_path(kernel_config[0].kernconf)

            kernel_src_path = kernel_project.source_dir
            kernel_build_path = kernel_project.build_dir
            disk_image = BuildCheriBSDDiskImage.get_instance(self, cross_target=xtarget).disk_image_path

            self.makedirs(self.syz_workdir)
            syz_config = self.syz_workdir / "syzkaller-config.json"
            vm_type = "qemu"
            if self.syz_debug:
                # Run in debug mode
                vm_type = "none"

            qemu_opts = QemuOptions(self.crosscompile_target)
            qemu_args = [*qemu_opts.machine_flags, "-device", "virtio-rng-pci", "-D", "syz-trace.log"]
            template = {
                "name": "cheribsd-n64",
                "target": "freebsd/" + str(self.crosscompile_target.cpu_architecture.value),
                "http": ":10000",
                "rpc": ":10001",
                "workdir": str(self.syz_workdir),
                "syzkaller": str(syzkaller.syzkaller_install_path().parent),
                "sshkey": str(self.syz_ssh_key),
                # (used for report symbolization and coverage reports, optional).
                "kernel_obj": str(kernel_path),
                # Kernel source directory (if not set defaults to KernelObj)
                "kernel_src": str(kernel_src_path),
                # Location of the driectory where the kernel was built (if not set defaults to KernelSrc)
                "kernel_build_src": str(kernel_build_path),
                "sandbox": "none",
                "procs": 1,
                "image": str(disk_image),
                "type": vm_type,
                "vm": {
                    "qemu": str(qemu_binary),
                    "qemu_args": commandline_to_str(qemu_args),
                    "kernel": str(kernel_path),
                    "image_device": "drive index=0,media=disk,format=raw,file=",
                    "count": 1,
                    "cpu": 1,
                    "mem": 2048,
                    "timeout": 60,
                },
            }
            self.verbose_print("Using syzkaller configuration", template)
            if not self.config.pretend:
                with syz_config.open("w+") as fp:
                    print(f"Emit syzkaller configuration to {syz_config}")
                    json.dump(template, fp, indent=4)

            return syz_config

    def process(self):
        syzkaller = BuildSyzkaller.get_instance(self)
        syz_args = [syzkaller.syzkaller_binary(), "-config", self.syzkaller_config(syzkaller)]
        if self.config.verbose:
            syz_args += ["-debug"]
        self.run_cmd(*syz_args)
