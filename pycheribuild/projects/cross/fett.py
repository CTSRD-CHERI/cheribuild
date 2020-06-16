#
# Copyright (c) 2020 SRI International
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
import os
from pathlib import Path

from .crosscompileproject import (CheriConfig, CompilationTargets, CrossCompileProject, DefaultInstallDir,
                                  FettProjectMixin, GitRepository, MakeCommandKind)
from .kcgi import BuildFettKCGI
from .nginx import BuildFettNginx
from .openssh import BuildFettOpenSSH
from .sqlbox import BuildFettSQLbox
from ..disk_image import _default_disk_image_name, BuildCheriBSDDiskImage
from ..run_qemu import LaunchCheriBSD
from ...config.loader import ComputedDefaultValue
from ...mtree import MtreeFile
from ...utils import commandline_to_str, classproperty


class BuildFettConfig(CrossCompileProject):
    project_name = "fett-config"
    repository = GitRepository("git@github.com:CTSRD-CHERI/SSITH-FETT-Target.git", default_branch="cheri")
    skipGitSubmodules = True
    supported_architectures = CompilationTargets.FETT_SUPPORTED_ARCHITECTURES

    dependencies = ["fett-nginx", "fett-openssh", "fett-sqlite", "fett-voting"]

    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    cross_install_dir = DefaultInstallDir.ROOTFS

    def __init__(self, config):
        super().__init__(config)
        self.mtree = MtreeFile()
        self.METALOG = self.destdir / "METALOG"

    def compile(self, **kwargs):
        print("Nothing to build for " + self.project_name)

    def install(self, **kwargs):
        if os.getenv("_TEST_SKIP_METALOG"):
            return
        if not self.METALOG.exists():
            self.fatal("METALOG " + str(self.METALOG) + "does not exist")
            return

        self.mtree.load(self.METALOG)
        src = self.sourceDir

        # nginx bits
        nginx_src = src / "build/webserver"
        nginx_prefix = BuildFettNginx.get_instance(self)._installPrefix.relative_to('/')
        self.mtree.add_file(nginx_src / "common/conf/nginx.conf",
                            nginx_prefix / "conf/nginx.conf")
        self.mtree.add_dir(nginx_prefix / "conf/sites")
        self.mtree.add_dir(nginx_prefix / "logs")
        # XXX: make private key dir 700?
        self.mtree.add_file(nginx_src / "common/keys/private-selfsigned.key",
                            nginx_prefix / "etc/ssl/private/private-selfsigned.key", mode="0600")
        self.mtree.add_file(nginx_src / "common/certs/selfsigned.crt",
                            nginx_prefix / "etc/ssl/certs/selfsigned.crt")
        self.mtree.add_file(src / "build/webserver/FreeBSD/rcfile",
            "etc/rc.d/fett_nginx", mode="0555")
        self.mtree.add_dir(nginx_prefix / "post", uname="www", gname="www")
        html_files = [
            "index.html",
            "private/secret.html",
            "stanford.png",
            "static.html",
            "test.txt",
            ]
        for file in html_files:
            self.mtree.add_file(src / "build/webserver/common/html" / file,
                                nginx_prefix / "html" / file)

        # sshd bits
        ssh_prefix = BuildFettOpenSSH.get_instance(self)._installPrefix.relative_to('/')
        keyfiles = ["ssh_host_dsa_key", "ssh_host_ecdsa_key", "ssh_host_ed25519_key", "ssh_host_rsa_key"]
        for keyfile in keyfiles:
            self.mtree.add_file(Path("/etc/ssh", keyfile), ssh_prefix / "etc/" / keyfile, symlink=True)
        self.mtree.add_file(src / "build/ssh/FreeBSD/fett_sshd", "etc/rc.d/fett_sshd", mode="0555")

        # sqlite bits
        # XXX-TODO: install a smoketest?

        # voting app
        voting_src = src / "build/voting"
        # /fett/var/www/cgi-bin added implicitly in fett-voting
        #self.mtree.add_dir("fett/var/www")
        #self.mtree.add_dir("fett/var/www/cgi-bin")
        self.mtree.add_dir("fett/var/www/data", uname="www", gname="www", mode="0770")
        self.mtree.add_dir("fett/var/www/html")
        self.mtree.add_dir("fett/var/www/run")
        self.mtree.add_file(voting_src / "common/conf/fastcgi.conf",
                            nginx_prefix / "conf/fastcgi.conf")
        self.mtree.add_file(voting_src / "common/conf/sites/voting.conf",
                            nginx_prefix / "conf/sites/voting.conf")
        self.mtree.add_file(voting_src / "freebsd/fett_bvrs.sh",
                            "etc/rc.d/fett_bvrs", mode="0555")

        self.mtree.write(self.METALOG)


class BuildFettVoting(FettProjectMixin, CrossCompileProject):
    project_name = "fett-voting"
    repository = GitRepository("git@github.com:CTSRD-CHERI/SSITH-FETT-Voting.git", default_branch="cheri")
    supported_architectures = CompilationTargets.FETT_SUPPORTED_ARCHITECTURES + [CompilationTargets.NATIVE]

    dependencies = ["fett-kcgi", "fett-sqlbox", "fett-sqlite", "fett-zlib", "openradtool"]

    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS

    make_kind = MakeCommandKind.GnuMake
    build_in_source_dir = True

    def setup(self):
        super().setup()
        # XXX: The buid system appends -Werror at the end so we can't use -Wno-error=xxx instead of -Wno-xxx
        self.common_warning_flags.append("-Wno-unused-function")
        self.common_warning_flags.append("-Wno-unused-variable")
        self.COMMON_FLAGS.append("-I" + str(BuildFettKCGI.getInstallDir(self) / "include"))
        self.COMMON_LDFLAGS.append("-L" + str(BuildFettKCGI.getInstallDir(self) / "lib"))
        self.COMMON_FLAGS.append("-I" + str(BuildFettSQLbox.getInstallDir(self) / "include"))
        self.COMMON_LDFLAGS.append("-L" + str(BuildFettSQLbox.getInstallDir(self) / "lib"))
        if self.target_info.is_freebsd():
            self.COMMON_LDFLAGS.append("-lmd")  # kcgi requires libmd
        self.make_args.set_env(
            CC=str(self.CC),
            LDFLAGS=commandline_to_str(self.default_ldflags),
            CFLAGS=commandline_to_str(self.default_compiler_flags),
            BVRS_OS="freebsd"
            )
        # Note: We must set these variables on the command line since the Makefile assigns to them with =
        self.make_args.set(PREFIX=self.real_install_root_dir, ORT_PREFIX=self.config.cheri_sdk_bindir / "ort")

    def compile(self, **kwargs):
        self.run_make("bvrs", cwd=self.sourceDir / "source/src", parallel=True)
        self.run_make("bvrs.sql", cwd=self.sourceDir / "source/src", parallel=True)

    def install(self, **kwargs):
        if not self.compiling_for_host():
            self.install_file(self.buildDir / "source/src/bvrs", self.real_install_root_dir / "var/www/cgi-bin/bvrs")
            self.install_file(self.buildDir / "source/src/bvrs.sql", self.real_install_root_dir / "share/bvrs.sql")


class BuildFettDiskImage(BuildCheriBSDDiskImage):
    project_name = "disk-image-fett"
    dependencies = ["fett-config"]
    supported_architectures = CompilationTargets.FETT_SUPPORTED_ARCHITECTURES

    @classproperty
    def default_architecture(cls):
        return CompilationTargets.CHERIBSD_RISCV_PURECAP

    default_disk_image_path = ComputedDefaultValue(
        function=lambda conf, proj: _default_disk_image_name(conf, conf.outputRoot, proj, "fett-cheribsd-"),
        as_string="$OUTPUT_ROOT/fett-$arch_prefix-disk.img.")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.autoPrefixes.append("fett/")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.hostname = "cheri-fett"


class LaunchFett(LaunchCheriBSD):
    project_name = "run-fett"
    _source_class = BuildFettDiskImage
    supported_architectures = CompilationTargets.FETT_SUPPORTED_ARCHITECTURES
