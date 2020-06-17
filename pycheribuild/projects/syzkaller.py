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
from urllib.parse import urlparse

from .build_qemu import BuildQEMU
from .cross.cheribsd import BuildCHERIBSD
from .cross.crosscompileproject import CheriConfig, CompilationTargets, CrossCompileProject
from .disk_image import BuildCheriBSDDiskImage
from .project import DefaultInstallDir, GitRepository, MakeCommandKind, SimpleProject
from ..utils import set_env, ThreadJoiner


class BuildSyzkaller(CrossCompileProject):
    dependencies = ["go", "cheribsd"]
    project_name = "cheri-syzkaller"
    githubBaseUrl = "https://github.com/CTSRD-CHERI/"
    repository = GitRepository(githubBaseUrl + "cheri-syzkaller.git")
    # no_default_sysroot = None // probably useless??
    # skip_cheri_symlinks = True // llvm target only, useless here
    make_kind = MakeCommandKind.GnuMake

    # is_sdk_target = True
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_HYBRID]
    default_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.sysgen = cls.add_bool_option(
            "run-sysgen", show_help=True,
            help="Rerun syz-extract and syz-sysgen to rebuild generated Go "
            "syscall descriptions.")

    def __init__(self, config):
        self._installPrefix = config.cheri_sdk_dir
        self._installDir = config.cheri_sdk_dir
        self.destdir = ""
        super().__init__(config)

        # self.gopath = source_base / gohome
        self.goroot = config.cheri_sdk_dir / "go"

        repo_url = urlparse(self.repository.url)
        repo_path = repo_url.path.split(".")[0]
        parts = ["src", repo_url.netloc] + repo_path.split("/")
        self.gopath = self.buildDir
        self.gosrc = self.sourceDir

        self._newPath = (str(self.config.cheri_sdk_dir / "bin") + ":" +
                         str(self.config.dollarPathWithOtherTools))

        self.cheribsd_dir = BuildCHERIBSD.getSourceDir(self)

    def syzkaller_install_path(self):
        return self.config.cheri_sdk_bindir

    def syzkaller_binary(self):
        return self.config.cheri_sdk_bindir / "syz-manager"

    def needsConfigure(self) -> bool:
        return False

    def compile(self, **kwargs):
        cflags = self.default_compiler_flags + self.default_ldflags

        self.make_args.set_env(
            HOSTARCH="amd64",
            TARGETARCH="mips64",
            TARGETOS="freebsd",
            GOROOT=self.goroot.expanduser(),
            GOPATH=self.gopath.expanduser(),
            CC=self.CC, CXX=self.CXX,
            PATH=self._newPath)
        if self.sysgen:
            self.generate()

        self.make_args.set_env(CFLAGS=" ".join(cflags))
        self.run_make(parallel=False, cwd=self.gosrc)

    def generate(self, **kwargs):
        with set_env(PATH=self._newPath, SOURCEDIR=self.cheribsd_dir):
            self.run_make("extract", parallel=False, cwd=self.gosrc)
            self.run_make("generate", parallel=False, cwd=self.gosrc)

    def install(self, **kwargs):
        # XXX-AM: should have a propert install dir configuration
        native_build = self.sourceDir / "bin"
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
        self.run_cmd(["chmod", "-R", "u+w", self.buildDir])
        self.make_args.set_env(
            HOSTARCH="amd64",
            TARGETARCH="mips64",
            TARGETOS="freebsd",
            GOROOT=self.goroot.expanduser(),
            GOPATH=self.gopath.expanduser(),
            CC=self.CC, CXX=self.CXX,
            PATH=self._newPath)

        self.run_make("clean", parallel=False, cwd=self.gosrc)
        joiner = super().clean()
        return joiner


class RunSyzkaller(SimpleProject):
    project_name = "run-syzkaller"

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.syz_config = cls.add_path_option("syz-config", default=None,
                                           help="Path to the syzkaller configuration file to use.",
                                           show_help=True)
        cls.syz_ssh_key = cls.add_path_option("ssh-privkey", show_help=True,
            default=lambda config, project: (config.sourceRoot / "extra-files" / "syzkaller_id_rsa"),
            help="A directory with additional files that will be added to the image (default: "
                 "'$SOURCE_ROOT/extra-files/syzkaller_id_rsa')", metavar="syzkaller_id_rsa")
        cls.syz_workdir = cls.add_path_option("workdir", show_help=True,
            default=lambda config, project: (config.outputRoot / "syzkaller-workdir"),
            help="Working directory for syzkaller output.", metavar="DIR")
        cls.syz_debug = cls.add_bool_option("debug",
            help="Run syz-manager in debug mode, requires manual startup of the VM.")

    def __init__(self, config: CheriConfig):
        super().__init__(config)

        self.qemu_binary = BuildQEMU.qemu_cheri_binary(self, xtarget=CompilationTargets.CHERIBSD_MIPS_HYBRID)
        self.syzkaller_binary = BuildSyzkaller.get_instance(
            self, cross_target=CompilationTargets.CHERIBSD_MIPS_HYBRID).syzkaller_binary()
        self.kernel_path = BuildCHERIBSD.get_installed_kernel_path(
            self, cross_target=CompilationTargets.CHERIBSD_MIPS_PURECAP)
        self.kernel_src_path = BuildCHERIBSD.get_instance(self, cross_target=CompilationTargets.CHERIBSD_MIPS_PURECAP).sourceDir
        self.kernel_build_path = BuildCHERIBSD.get_instance(self, cross_target=CompilationTargets.CHERIBSD_MIPS_PURECAP).buildDir
        self.disk_image = BuildCheriBSDDiskImage.get_instance(
            self, cross_target=CompilationTargets.CHERIBSD_MIPS_PURECAP).disk_image_path

    def syzkaller_config(self):
        """ Get path of syzkaller configuration file to use. """
        if self.syz_config:
            return self.syz_config
        else:
            self.makedirs(self.syz_workdir)
            syz_config = self.syz_workdir / "syzkaller-config.json"
            vm_type = "qemu"
            if self.syz_debug:
                # Run in debug mode
                vm_type = "none"

            template = {
                "name": "cheribsd-n64",
                "target": "freebsd/mips64",
                "http": ":10000",
                "rpc": ":10001",
                "workdir": str(self.syz_workdir),
                "syzkaller": str(BuildSyzkaller.get_instance(
                    self, cross_target=CompilationTargets.CHERIBSD_MIPS_HYBRID)
                                 .syzkaller_install_path().parent),
                "sshkey": str(self.syz_ssh_key),
                # (used for report symbolization and coverage reports, optional).
                "kernel_obj": str(self.kernel_path),
                # Kernel source directory (if not set defaults to KernelObj)
                "kernel_src": str(self.kernel_src_path),
                # Location of the driectory where the kernel was built (if not set defaults to KernelSrc)
                "kernel_build_src": str(self.kernel_build_path),
                "sandbox": "none",
                "procs": 1,
                "image": str(self.disk_image),
                "type": vm_type,
                "vm": {
                    "qemu": str(self.qemu_binary),
                    "qemu_args": "-M malta -device virtio-rng-pci -D syz-trace.log",
                    "kernel": str(self.kernel_path),
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
        syz_args = [self.syzkaller_binary, "-config", self.syzkaller_config()]
        if self.config.verbose:
            syz_args += ["-debug"]
        self.run_cmd(*syz_args)
