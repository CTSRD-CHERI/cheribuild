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

from .cheribsd import BuildCheriBSDFett
from .crosscompileproject import (CheriConfig, CompilationTargets, CrossCompileProject, DefaultInstallDir,
                                  FettProjectMixin, GitRepository, MakeCommandKind)
from .kcgi import BuildFettKCGI
from .nginx import BuildFettNginx
from .openssh import BuildFettOpenSSH
from .sqlbox import BuildFettSQLbox
from ..disk_image import BuildCheriBSDDiskImage
from ..run_qemu import LaunchCheriBSD
from ...config.target_info import CrossCompileTarget
from ...mtree import MtreeFile
from ...utils import classproperty


class BuildFettConfig(FettProjectMixin, CrossCompileProject):
    target = "fett-config"
    repository = GitRepository("git@github.com:CTSRD-CHERI/SSITH-FETT-Target.git", default_branch="cheri")
    skip_git_submodules = True
    supported_architectures = CompilationTargets.FETT_SUPPORTED_ARCHITECTURES
    dependencies = ["fett-nginx", "fett-openssh", "fett-sqlite", "fett-voting"]

    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE

    def __init__(self, config):
        super().__init__(config)
        self.METALOG = self.destdir / "METALOG.world"

    def compile(self, **kwargs):
        print("Nothing to build for " + self.target)

    def install(self, **kwargs):
        if os.getenv("_TEST_SKIP_METALOG"):
            return
        if not self.METALOG.exists():
            self.fatal("METALOG " + str(self.METALOG) + "does not exist")
            return

        mtree = MtreeFile(file=self.METALOG, verbose=self.config.verbose)
        src = self.source_dir

        # general config
        mtree.add_file(src / "build/freebsd/malloc.conf", "etc/malloc.conf")
        mtree.add_file(src / "build/freebsd/motd.template", "etc/motd.template")
        mtree.add_file(src / "build/freebsd/exports", "etc/exports")

        # nginx bits
        nginx_src = src / "build/webserver"
        nginx_prefix = BuildFettNginx.get_instance(self)._install_prefix.relative_to('/')
        mtree.add_file(nginx_src / "common/conf/nginx.conf",
                       nginx_prefix / "conf/nginx.conf")
        mtree.add_file(nginx_src / "common/conf/mime.types",
                       nginx_prefix / "conf/mime.types")
        mtree.add_dir(nginx_prefix / "conf/sites")
        mtree.add_dir(nginx_prefix / "logs")
        # XXX: make private key dir 700?
        mtree.add_file(nginx_src / "common/keys/fett-webserver.key",
                       nginx_prefix / "etc/ssl/private/fett-webserver.key", mode="0600")
        mtree.add_file(nginx_src / "common/certs/fett-webserver.crt",
                       nginx_prefix / "etc/ssl/certs/fett-webserver.crt")
        mtree.add_file(src / "build/webserver/FreeBSD/nginx.sh",
                       "etc/rc.d/fett_nginx", mode="0555")
        mtree.add_dir(nginx_prefix / "post", uname="www", gname="www")
        html_files = [
            "favicon.ico",
            "index.html",
            "private/secret.html",
            "stanford.png",
            "static.html",
            "test.txt",
        ]
        for file in html_files:
            mtree.add_file(src / "build/webserver/common/html" / file,
                           nginx_prefix / "html" / file)

        # sshd bits
        ssh_prefix = BuildFettOpenSSH.get_instance(self)._install_prefix.relative_to('/')
        keyfiles = ["ssh_host_dsa_key", "ssh_host_ecdsa_key", "ssh_host_ed25519_key", "ssh_host_rsa_key"]
        for keyfile in keyfiles:
            mtree.add_symlink(path_in_image=ssh_prefix / "etc" / keyfile, symlink_dest=Path("/etc/ssh", keyfile))
        mtree.add_file(src / "build/ssh/FreeBSD/fett_sshd", "etc/rc.d/fett_sshd", mode="0555")

        # sqlite bits
        # XXX-TODO: install a smoketest?

        # voting app
        voting_src = src / "build/voting"
        voting_prefix = Path("fett/var/www")
        mtree.add_file(voting_src / "common/conf/sites/voting-mime.types",
                       nginx_prefix / "conf/sites/voting-mime.types")
        # /fett/var/www/(cgi-bin|bvrs) added implicitly in fett-voting
        # mtree.add_dir(voting_prefix)
        # mtree.add_dir(voting_prefix / "cgi-bin")
        mtree.add_dir(voting_prefix / "bvrs")
        mtree.add_dir(voting_prefix / "bvrs/bvrs")
        mtree.add_file(voting_src / "common/static/index.html",
                       voting_prefix / "bvrs/index.html")
        mtree.add_file(src / "build/webserver/common/html/favicon.ico",
                       voting_prefix / "bvrs/favicon.ico")
        html_files = [
            "election_official_home.html",
            "election_official_login.html",
            "election_official_new_voter_registration.html",
            "election_official_query.html",
            "index.html",
            "index.js",
            "jquery-3.5.1.min.js",
            "pure-min.css",
            "registration_verification.js",
            "style.css",
            "voter_home.html",
            "voter_registration.html",
            "voter_registration_confirmation.html",
            "voter_registration_update_login.html",
            "voter_registration_verification.html",
        ]
        for file in html_files:
            mtree.add_file(voting_src / "common/static/bvrs" / file,
                           voting_prefix / "bvrs/bvrs" / file)
        mtree.add_dir(voting_prefix / "data", uname="www", gname="www", mode="0770")
        mtree.add_dir(voting_prefix / "run")
        mtree.add_file(voting_src / "common/conf/fastcgi.conf",
                       nginx_prefix / "conf/fastcgi.conf")
        mtree.add_file(voting_src / "common/conf/sites/voting.conf",
                       nginx_prefix / "conf/sites/voting.conf")
        mtree.add_file(nginx_src / "common/keys/fett-voting.key",
                       nginx_prefix / "etc/ssl/private/fett-voting.key", mode="0600")
        mtree.add_file(nginx_src / "common/certs/fett-voting.crt",
                       nginx_prefix / "etc/ssl/certs/fett-voting.crt")
        mtree.add_file(voting_src / "FreeBSD/bvrs.sh",
                       "etc/rc.d/fett_bvrs", mode="0555")

        mtree.write(self.METALOG, pretend=self.config.pretend)


class BuildFettVoting(FettProjectMixin, CrossCompileProject):
    target = "fett-voting"
    repository = GitRepository("git@github.com:CTSRD-CHERI/SSITH-FETT-Voting.git", default_branch="cheri")
    supported_architectures = CompilationTargets.FETT_SUPPORTED_ARCHITECTURES + [CompilationTargets.NATIVE]

    dependencies = ["fett-kcgi", "fett-sqlbox", "fett-sqlite", "fett-zlib", "openradtool"]

    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE

    make_kind = MakeCommandKind.GnuMake
    build_in_source_dir = True

    def setup(self):
        super().setup()
        # XXX: The buid system appends -Werror at the end so we can't use -Wno-error=xxx instead of -Wno-xxx
        self.common_warning_flags.append("-Wno-unused-function")
        self.common_warning_flags.append("-Wno-unused-variable")
        self.COMMON_FLAGS.append("-I" + str(BuildFettKCGI.get_install_dir(self) / "include"))
        self.COMMON_LDFLAGS.append("-L" + str(BuildFettKCGI.get_install_dir(self) / "lib"))
        self.COMMON_FLAGS.append("-I" + str(BuildFettSQLbox.get_install_dir(self) / "include"))
        self.COMMON_LDFLAGS.append("-L" + str(BuildFettSQLbox.get_install_dir(self) / "lib"))
        if self.target_info.is_freebsd():
            self.COMMON_LDFLAGS.append("-lmd")  # kcgi requires libmd
        self.make_args.set_env(
            CC=str(self.CC),
            LDFLAGS=self.commandline_to_str(self.default_ldflags),
            CFLAGS=self.commandline_to_str(self.default_compiler_flags),
            BVRS_OS="freebsd"
        )
        # Note: We must set these variables on the command line since the Makefile assigns to them with =
        self.make_args.set(PREFIX=self.real_install_root_dir, ORT_PREFIX=self.config.cheri_sdk_bindir / "ort")

    def compile(self, **kwargs):
        self.run_make("bvrs", cwd=self.source_dir / "source/src", parallel=True)
        self.run_make("bvrs.sql", cwd=self.source_dir / "source/src", parallel=True)

    def install(self, **kwargs):
        if not self.compiling_for_host():
            self.install_file(self.build_dir / "source/src/bvrs", self.real_install_root_dir / "var/www/cgi-bin/bvrs")
            self.install_file(self.build_dir / "source/src/bvrs.sql", self.real_install_root_dir / "share/bvrs.sql")


class BuildFettDiskImage(FettProjectMixin, BuildCheriBSDDiskImage):
    target = "disk-image-fett"
    dependencies = ["fett-bash", "fett-config"]
    disk_image_prefix = "fett-cheribsd"
    _source_class = BuildCheriBSDFett
    supported_architectures = CompilationTargets.FETT_SUPPORTED_ARCHITECTURES
    hide_options_from_help = True

    @classproperty
    def default_architecture(self):
        return CompilationTargets.FETT_DEFAULT_ARCHITECTURE

    @property
    def _gdb_xtarget(self):
        # Workaround for FETT (we use the normal GDB target to avoid duplicating yet another project)
        for xtarget in BuildCheriBSDDiskImage.supported_architectures:
            assert isinstance(xtarget, CrossCompileTarget)
            if xtarget.generic_suffix == self.crosscompile_target.generic_suffix:
                return xtarget
        raise ValueError("Can't find GDB arch")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.minimum_image_size = "10g"
        self.auto_prefixes.append("fett/")
        # Manpage indexs are being generated and not added to METALOG
        # this is a bug in whatever is calling makewhatis.
        self.auto_prefixes.append("usr/share/openssl/man/mandoc.db")
        self.auto_prefixes.append("usr/share/man/mandoc.db")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.hostname = "cheri-fett"
        cls.include_kgdb = True


class LaunchFett(FettProjectMixin, LaunchCheriBSD):
    target = "run-fett"
    _source_class = BuildFettDiskImage
    supported_architectures = CompilationTargets.FETT_SUPPORTED_ARCHITECTURES
    hide_options_from_help = True

    @classmethod
    def get_cross_target_index(cls):
        return super().get_cross_target_index(xtarget=cls._xtarget)
