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
from pathlib import Path

from .build_qemu import BuildQEMU
from .cross.cheribsd import BuildCHERIBSD, ConfigPlatform, CheriBSDConfigTable
from .cross.crosscompileproject import CompilationTargets, CrossCompileProject
from .disk_image import BuildCheriBSDDiskImage
from .project import DefaultInstallDir, GitRepository, MakeCommandKind
from .simple_project import SimpleProject
from ..processutils import commandline_to_str
from ..qemu_utils import QemuOptions
from ..utils import ThreadJoiner


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

    def __init__(self, config, *args, **kwargs):
        self._install_prefix = config.cheri_sdk_dir
        self._install_dir = config.cheri_sdk_dir
        self.destdir = Path("")
        super().__init__(config, *args, **kwargs)

        # self.gopath = source_base / gohome
        self.goroot = config.cheri_sdk_dir / "go"

        # repo_url = urlparse(self.repository.url)
        # repo_path = repo_url.path.split(".")[0]
        # parts = ["src", repo_url.netloc] + repo_path.split("/")
        self.gopath = self.build_dir
        self.gosrc = self.source_dir

        self._new_path = (str(self.config.cheri_sdk_dir / "bin") + ":" +
                          str(self.config.dollar_path_with_other_tools))

        cheribsd_target = self.crosscompile_target.get_rootfs_target()
        self.cheribsd_dir = BuildCHERIBSD.get_source_dir(self, cross_target=cheribsd_target)

    def syzkaller_install_path(self):
        return self.config.cheri_sdk_bindir

    def syzkaller_binary(self):
        return self.config.cheri_sdk_bindir / "syz-manager"

    def needs_configure(self) -> bool:
        return False

    def compile(self, **kwargs):
        cflags = self.default_compiler_flags + self.default_ldflags

        self.make_args.set_env(
            HOSTARCH="amd64",
            TARGETARCH=self.crosscompile_target.cpu_architecture.value,
            TARGETOS="freebsd",
            GOROOT=self.goroot.expanduser(),
            GOPATH=self.gopath.expanduser(),
            CC=self.CC, CXX=self.CXX,
            PATH=self._new_path)
        if self.sysgen:
            self.generate()

        self.make_args.set_env(CFLAGS=" ".join(cflags))
        self.run_make(parallel=False, cwd=self.gosrc)

    def generate(self):
        with self.set_env(PATH=self._new_path, SOURCEDIR=self.cheribsd_dir):
            self.run_make("extract", parallel=False, cwd=self.gosrc)
            self.run_make("generate", parallel=False, cwd=self.gosrc)

    def install(self, **kwargs):
        # XXX-AM: should have a propert install dir configuration
        native_build = self.source_dir / "bin"
        mips64_build = native_build / "freebsd_mips64"
        syz_remote_install = self.syzkaller_install_path() / "freebsd_mips64"

        self.makedirs(syz_remote_install)

        self.install_file(native_build / "syz-manager", self.syzkaller_binary(), mode=0o755)

        if not self.config.pretend:
            # mips64_build does not exist if we preted, so skip
            for fname in os.listdir(str(mips64_build)):
                fpath = mips64_build / fname
                if os.path.isfile(fpath):
                    self.install_file(fpath, syz_remote_install / fname, mode=0o755)

    def clean(self) -> ThreadJoiner:
        self.run_cmd(["chmod", "-R", "u+w", self.build_dir])
        self.make_args.set_env(
            HOSTARCH="amd64",
            TARGETARCH=self.crosscompile_target.cpu_architecture.value,
            TARGETOS="freebsd",
            GOROOT=self.goroot.expanduser(),
            GOPATH=self.gopath.expanduser(),
            CC=self.CC, CXX=self.CXX,
            PATH=self._new_path)

        self.run_make("clean", parallel=False, cwd=self.gosrc)
        joiner = super().clean()
        return joiner


class RunSyzkaller(SimpleProject):
    target = "run-syzkaller"
    supported_architectures = [CompilationTargets.CHERIBSD_MORELLO_HYBRID_FOR_PURECAP_ROOTFS]

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.syz_config = cls.add_path_option("syz-config", default=None,
                                             help="Path to the syzkaller configuration file to use.",
                                             show_help=True)
        cls.syz_ssh_key = cls.add_path_option("ssh-privkey", show_help=True,
                                              default=lambda config, project: (
                                                      config.source_root / "extra-files" / "syzkaller_id_rsa"),
                                              help="A directory with additional files that will be added to the image "
                                                   "(default: '$SOURCE_ROOT/extra-files/syzkaller_id_rsa')",
                                              metavar="syzkaller_id_rsa")
        cls.syz_workdir = cls.add_path_option("workdir", show_help=True,
                                              default=lambda config, project: (
                                                      config.output_root / "syzkaller-workdir"),
                                              help="Working directory for syzkaller output.", metavar="DIR")
        cls.syz_debug = cls.add_bool_option("debug",
                                            help="Run syz-manager in debug mode, requires manual startup of the VM.")

    def syzkaller_config(self, syzkaller: BuildSyzkaller):
        """ Get path of syzkaller configuration file to use. """
        if self.syz_config:
            return self.syz_config
        else:
            xtarget = syzkaller.crosscompile_target.get_cheri_purecap_target()
            qemu_binary = BuildQEMU.qemu_binary(self, xtarget=xtarget)
            kernel_project = BuildCHERIBSD.get_instance(self, cross_target=xtarget)
            kernel_config = CheriBSDConfigTable.get_configs(xtarget, platform=ConfigPlatform.QEMU,
                                                            kABI=kernel_project.get_default_kernel_abi(),
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
                    "qemu_args": commandline_to_str(qemu_opts.machine_flags +
                                                    ["-device", "virtio-rng-pci",
                                                     "-D", "syz-trace.log"]),
                    "kernel": str(kernel_path),
                    "image_device": "drive index=0,media=disk,format=raw,file=",
                    "count": 1,
                    "cpu": 1,
                    "mem": 2048,
                    "timeout": 60
                    }
                }
            self.verbose_print("Using syzkaller configuration", template)
            if not self.config.pretend:
                with syz_config.open("w+") as fp:
                    print("Emit syzkaller configuration to {}".format(syz_config))
                    json.dump(template, fp, indent=4)

            return syz_config

    def process(self):
        syzkaller = BuildSyzkaller.get_instance(self)
        syz_args = [syzkaller.syzkaller_binary(), "-config", self.syzkaller_config(syzkaller)]
        if self.config.verbose:
            syz_args += ["-debug"]
        self.run_cmd(*syz_args)
