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
import os
from enum import Enum

from .build_qemu import BuildQEMU
from .cross.cheribsd import BuildCHERIBSD, ConfigPlatform, CheriBSDConfigTable
from .cross.crosscompileproject import CompilationTargets, CrossCompileProject
from .disk_image import BuildCheriBSDDiskImage
from .project import DefaultInstallDir, GitRepository, MakeCommandKind, SimpleProject
from ..processutils import commandline_to_str
from ..qemu_utils import QemuOptions
from ..utils import ThreadJoiner


class GoType(Enum):
    UPSTREAM = "upstream"
    SYSTEM = "system"


class BuildSyzkaller(CrossCompileProject):
    dependencies = ["go"]
    target = "cheri-syzkaller"
    github_base_url = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(github_base_url + "cheri-syzkaller.git")
    # no_default_sysroot = None // probably useless??
    # skip_cheri_symlinks = True // llvm target only, useless here
    make_kind = MakeCommandKind.GnuMake

    # is_sdk_target = True
    supported_architectures = [CompilationTargets.CHERIBSD_MORELLO_HYBRID_FOR_PURECAP_ROOTFS]
    default_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.sysgen = cls.add_bool_option(
            "run-sysgen", show_help=True,
            help="Rerun syz-extract and syz-sysgen to rebuild generated Go "
                 "syscall descriptions.")
        cls.use_go = cls.add_config_option("use-go", kind=GoType, default=GoType.SYSTEM,
                                           enum_choice_strings=[t.value for t in GoType],
                                           help="The Go type to run with.")

    def get_path(self):
        return (str(self.get_sdk_bindir()) + ":" +
                str(self.config.dollar_path_with_other_tools))

    def get_sdk_dir(self):
        return self.config.cheri_sdk_dir

    def get_sdk_bindir(self):
        return self.config.cheri_sdk_bindir

    def __init__(self, config):
        super().__init__(config)
        self._install_prefix = self.get_sdk_dir()
        self._install_dir = self.get_sdk_dir()
        self.destdir = ""

        # self.gopath = source_base / gohome
        self.goroot = self.get_sdk_dir() / "go"

        # repo_url = urlparse(self.repository.url)
        # repo_path = repo_url.path.split(".")[0]
        # parts = ["src", repo_url.netloc] + repo_path.split("/")
        self.gopath = self.build_dir
        self.gosrc = self.source_dir

        self.targetarch = self.crosscompile_target.cpu_architecture.value
        if self.targetarch == "aarch64":
            self.targetarch = "arm64"

        cheribsd_target = self.get_crosscompile_target(config).get_rootfs_target()
        self.cheribsd_dir = BuildCHERIBSD.get_source_dir(self, cross_target=cheribsd_target)

    def syzkaller_install_path(self):
        return self.get_sdk_dir() / "syzkaller" / "bin"

    def syzkaller_binary(self):
        return self.get_sdk_bindir() / "syz-manager"

    def needs_configure(self) -> bool:
        return False

    def setup(self):
        super().setup()
        args = {
            "HOSTARCH": "amd64",
            "TARGETARCH": self.targetarch,
            "TARGETOS": "freebsd",
            "CC": self.CC,
            "CXX": self.CXX,
            "PATH": self.get_path()
        }
        if self.use_go == GoType.UPSTREAM:
            args["GOROOT"] = self.goroot.expanduser()
            args["GOPATH"] = self.gopath.expanduser()
        self.make_args.set_env(**args)

    def compile(self, **kwargs):
        cflags = self.default_compiler_flags + self.default_ldflags

        if self.sysgen:
            self.generate()

        self.make_args.set_env(CFLAGS=" ".join(cflags))
        self.run_make(parallel=False, cwd=self.gosrc)

    def generate(self):
        with self.set_env(PATH=self.get_path(), SOURCEDIR=self.cheribsd_dir):
            self.run_make("extract", parallel=False, cwd=self.gosrc)
            self.run_make("generate", parallel=False, cwd=self.gosrc)

    def get_install_files(self, dir):
        file_paths = []
        for path in dir.iterdir():
            if path.is_dir():
                sub_files = self.get_install_files(path)
                file_paths.extend(sub_files)
            else:
                file_paths.append(path)
        return file_paths

    def install(self, **kwargs):
        # XXX-AM: should have a propert install dir configuration
        build_output_path = self.source_dir / "bin"
        sdk_install_path = self.syzkaller_install_path()

        self.makedirs(sdk_install_path)

        self.install_file(build_output_path / "syz-manager", self.syzkaller_binary(), mode=0o755)

        if not self.config.pretend:
            # build does not exist if we preted, so skip
            for fpath in self.get_install_files(build_output_path):
                if os.path.isfile(fpath):
                    self.install_file(fpath, sdk_install_path / fpath.relative_to(build_output_path),
                                      mode=0o755)

    def clean(self) -> ThreadJoiner:
        self.run_cmd(["chmod", "-R", "u+w", self.build_dir])

        self.run_make("clean", parallel=False, cwd=self.gosrc)
        joiner = super().clean()
        return joiner


class BuildMorelloSyzkaller(BuildSyzkaller):
    target = "morello-syzkaller"
    github_base_url = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(github_base_url + "cheri-syzkaller.git", force_branch=True,
                               default_branch="morello-syzkaller")

    def get_sdk_dir(self):
        return self.config.morello_sdk_dir

    def get_sdk_bindir(self):
        return self.config.morello_sdk_bindir


class VMSetting(Enum):
    NONE = {"type": "none",
            "vm": {}
            }
    QEMU = {
            "type": "qemu",
            "vm": {
                "qemu": "",
                "qemu_args": "",
                "kernel": "",
                "image_device": "drive index=0,media=disk,format=raw,file=",
                "count": 1,
                "cpu": 1,
                "mem": 2048,
                    }
            }
    MORELLO = {
            "type": "morello",
            "vm": {
                "reboot_command": "timeout 1m /home/zalan/reboot_morello.sh",
                "mbox_workdir": "/syz/"
                    }
                }


class RunSyzkallerBase(SimpleProject):
    # Generally to generate a new type of config, the new class should
    # override costumize_config and get_syzkaller
    # also set do_not_add_to_targets to false
    do_not_add_to_targets = True
    supported_architectures = [CompilationTargets.CHERIBSD_MORELLO_HYBRID_FOR_PURECAP_ROOTFS]
    vm_type = VMSetting.NONE

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.syz_config = cls.add_path_option("syz-config", default=None,
                                             help="Path to the syzkaller configuration file to use.",
                                             show_help=True)
        cls.syz_ssh_key = cls.add_path_option("ssh-privkey", show_help=True,
                                              default=lambda config, project: (
                                                      config.source_root / "extra-files" / "syzkaller_id_rsa"),
                                              help="Path to the private key used for ssh connections with the VM(s) "
                                                   "(default: '$SOURCE_ROOT/extra-files/syzkaller_id_rsa')",
                                              metavar="syzkaller_id_rsa")
        cls.syz_workdir = cls.add_path_option("workdir", show_help=True,
                                              default=lambda config, project: (
                                                      config.output_root / "syzkaller-workdir"),
                                              help="Working directory for syzkaller output.", metavar="DIR")
        cls.syz_debug = cls.add_bool_option("debug",
                                            help="Run syz-manager in debug mode, requires manual startup of the VM.")

    def syzkaller_config(self):
        """ Get path of syzkaller configuration file to use. """
        if self.syz_config:
            return self.syz_config
        else:
            xtarget = self.syzkaller.crosscompile_target.get_cheri_purecap_target()
            kernel_project = BuildCHERIBSD.get_instance(self, cross_target=xtarget)

            kernel_src_path = kernel_project.source_dir
            kernel_build_path = kernel_project.build_dir
            disk_image = BuildCheriBSDDiskImage.get_instance(self, cross_target=xtarget).disk_image_path

            self.makedirs(self.syz_workdir)
            syz_config = self.syz_workdir / "syzkaller-config.json"

            template = {
                "name": "cheribsd-n64",
                "target": "freebsd/" + self.crosscompile_target.cpu_architecture.value,
                "http": ":10000",
                "rpc": ":10001",
                "workdir": str(self.syz_workdir),
                "syzkaller": str(self.syzkaller.syzkaller_install_path().parent),
                "sshkey": str(self.syz_ssh_key),
                # (used for report symbolization and coverage reports, optional).
                "kernel_obj": "",  # has to be provided by costumize_config
                # Kernel source directory (if not set defaults to KernelObj)
                "kernel_src": str(kernel_src_path),
                # Location of the driectory where the kernel was built (if not set defaults to KernelSrc)
                "kernel_build_src": str(kernel_build_path),
                "sandbox": "none",
                "procs": 1,
                "image": str(disk_image),
                "type": "",
                "vm": {}
                }

            for item in self.vm_type.value.keys():
                template[item] = self.vm_type.value[item]

            template = self.costumize_config(kernel_project, template)
            if self.syz_debug:
                # Run in debug mode
                template["type"] = "none"
            self.verbose_print("Using syzkaller configuration", template)
            if not self.config.pretend:
                with syz_config.open("w+") as fp:
                    print("Emit syzkaller configuration to {}".format(syz_config))
                    json.dump(template, fp, indent=4)

            return syz_config

    def costumize_config(self, kernel_project, template):
        return template

    def set_syzkaller(self):
        self.syzkaller = BuildSyzkaller.get_instance(self)

    def process(self):
        self.set_syzkaller()
        syz_args = [self.syzkaller.syzkaller_binary(), "-config", self.syzkaller_config()]
        if self.config.verbose:
            syz_args += ["-debug"]
        self.run_cmd(*syz_args)


class RunSyzkaller(RunSyzkallerBase):
    target = "run-syzkaller"
    vm_type = VMSetting.QEMU

    def costumize_config(self, kernel_project, template):
        xtarget = self.syzkaller.crosscompile_target.get_cheri_purecap_target()
        qemu_binary = BuildQEMU.qemu_binary(self, xtarget=xtarget)
        kernel_config = CheriBSDConfigTable.get_configs(xtarget, ConfigPlatform.QEMU,
                                                        kernel_project.get_default_kernel_abi(), fuzzing=True)
        if len(kernel_config) == 0:
            self.fatal("No kcov kernel configuration found")
            return
        kernel_path = kernel_project.get_kernel_install_path(kernel_config[0].kernconf)
        qemu_opts = QemuOptions(self.crosscompile_target)

        template["kernel_obj"] = str(kernel_path)
        template["vm"]["qemu"] = str(qemu_binary)
        template["vm"]["qemu_args"] = commandline_to_str(qemu_opts.machine_flags +
                                                         ["-device", "virtio-rng-pci",
                                                          "-D", "syz-trace.log"])
        template["vm"]["kernel"] = str(kernel_path)
        template["vm"]["timeout"] = 60

        return template


class RunMorelloQemuSyzkaller(RunSyzkallerBase):
    target = "run-morello-qemu-syzkaller"
    vm_type = VMSetting.QEMU

    def costumize_config(self, kernel_project, template):
        qemu_binary = BuildQEMU.qemu_binary(self)
        kernel_config = kernel_project.default_kernel_config(ConfigPlatform.QEMU)
        if len(kernel_config) == 0:
            self.fatal("No kcov kernel configuration found")
            return
        kernel_path = kernel_project.get_kernel_install_path(kernel_config)
        qemu_opts = QemuOptions(self.crosscompile_target)

        template["kernel_obj"] = str(kernel_path)
        template["vm"]["qemu"] = str(qemu_binary)
        template["vm"]["qemu_args"] = commandline_to_str(qemu_opts.machine_flags +
                                                         ["-D", "syz-trace.log"])
        template["vm"]["kernel"] = str(kernel_path)

        target_arch = self.crosscompile_target.cpu_architecture.value
        if target_arch == "aarch64":
            target_arch = "arm64"
        template["target"] = "freebsd/" + target_arch

        return template

    def set_syzkaller(self):
        self.syzkaller = BuildMorelloSyzkaller.get_instance(self)


class RunMorelloBaremetalSyzkaller(RunSyzkallerBase):
    target = "run-morello-baremetal-syzkaller"
    vm_type = VMSetting.MORELLO

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        cls.syz_ssh_user = cls.add_config_option("ssh-user", show_help=True,
                                                 help="The username of the default ssh user")
        cls.syz_morellobox_ssh_user = cls.add_config_option("morellobox-ssh-user", show_help=True,
                                                            help="The username of the morellobox ssh user "
                                                                 "(default value: "
                                                                 "--run-morello-baremetal-syzkaller/ssh-user)")
        cls.syz_morellobox_ssh_key = cls.add_path_option("morellobox-ssh-privkey", show_help=True,
                                                         help="Path to the private key used to communicate "
                                                              "with the morellobox")
        cls.syz_morellobox_address = cls.add_config_option("morellobox-address", show_help=True,
                                                           help="The address of the morellobox")
        def_comm = VMSetting.MORELLO.value["vm"]["reboot_command"]
        cls.syz_reboot_command = cls.add_config_option("reboot-command", show_help=True,
                                                       help="The command to restart the morellobox "
                                                            "(default: '" + def_comm + "')")
        def_mbox_wdir = VMSetting.MORELLO.value["vm"]["mbox_workdir"]
        cls.syz_morellobox_workdir = cls.add_path_option("morellobox-workdir", show_help=True,
                                                         help="The directory where syzkaller fuzzing files are"
                                                              "copied to, and where syz-fuzzer executes "
                                                              "(default: '" + def_mbox_wdir + "')")

    def costumize_config(self, kernel_project, template):
        kernel_config = kernel_project.default_kernel_config(ConfigPlatform.QEMU)
        if len(kernel_config) == 0:
            self.fatal("No kcov kernel configuration found")
            return
        if self.syz_ssh_user is None:
            self.fatal("No ssh user name provided, use --run-morello-baremetal-syzkaller/ssh-user USERNAME")
            return
        if self.syz_morellobox_address is None:
            self.fatal("No morello box address provided, use --run-morello-baremetal-syzkaller/"
                       "morellobox-address ADDRESS")
            return
        kernel_path = kernel_project.get_kernel_install_path(kernel_config)

        template["kernel_obj"] = str(kernel_path)
        template["ssh_user"] = str(self.syz_ssh_user)
        template["sshkey"] = str(self.syz_ssh_key)
        template["vm"]["mbox_address"] = str(self.syz_morellobox_address)
        if self.syz_morellobox_ssh_user is not None:
            template["vm"]["mbox_username"] = str(self.syz_morellobox_ssh_user)
        if self.syz_morellobox_ssh_key is not None:
            template["vm"]["mbox_sshkey"] = str(self.syz_morellobox_ssh_key)
        if self.syz_reboot_command is not None:
            template["vm"]["reboot_command"] = str(self.syz_reboot_command)
        if self.syz_morellobox_workdir is not None:
            template["vm"]["mbox_workdir"] = str(self.syz_morellobox_workdir)

        target_arch = self.crosscompile_target.cpu_architecture.value
        if target_arch == "aarch64":
            target_arch = "arm64"
        template["target"] = "freebsd/" + target_arch

        return template

    def set_syzkaller(self):
        self.syzkaller = BuildMorelloSyzkaller.get_instance(self)
