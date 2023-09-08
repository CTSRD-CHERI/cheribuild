#
# Copyright (c) 2016 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
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

import io
import os
import shutil
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Optional

from .cross.cheribsd import BuildCHERIBSD, BuildFreeBSD, BuildFreeBSDWithDefaultOptions
from .cross.gdb import BuildGDB, BuildKGDB
from .project import (
    AutotoolsProject,
    CheriConfig,
    ComputedDefaultValue,
    CPUArchitecture,
    CrossCompileTarget,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
    Project,
)
from .simple_project import SimpleProject
from ..config.compilation_targets import CompilationTargets
from ..mtree import MtreeFile
from ..utils import AnsiColour, cached_property, classproperty, coloured, include_local_file

# Notes:
# Mount the filesystem of a BSD VM: guestmount -a /foo/bar.qcow2 -m /dev/sda1:/:ufstype=ufs2:ufs --ro /mnt/foo
# ufstype=ufs2 is required as the Linux kernel can't automatically determine which UFS filesystem is being used
# Same thing is possible with qemu-nbd, but needs root (might be faster)


class BuildMtools(AutotoolsProject):
    repository = GitRepository(url="https://github.com/vapier/mtools.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    make_kind = MakeCommandKind.GnuMake
    build_in_source_dir = True

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("autoreconf", default="autoconf")
        self.check_required_system_tool("aclocal", default="automake")

    def setup(self):
        super().setup()
        # Manpages won't build:
        self.make_args.set(MAN1="", MAN5="")

    def process(self):
        super().process()

    def configure(self, **kwargs):
        if not (self.source_dir / "configure").exists():
            self.run_cmd("autoreconf", "-ivf", cwd=self.source_dir)
        if not (self.source_dir / "mtools.tmpl.1").exists():
            self.run_cmd("bash", "-e", "./mkmanpages", cwd=self.source_dir)
        if self.target_info.is_macos():
            self.add_configure_env_arg("LIBS", "-liconv")

        super().configure(**kwargs)


# noinspection PyMethodMayBeStatic
class _AdditionalFileTemplates:
    def get_fstab_template(self):
        return include_local_file("files/cheribsd/fstab.in")

    def get_rc_conf_template(self):
        return include_local_file("files/cheribsd/rc.conf.in")

    def get_cshrc_template(self):
        return include_local_file("files/cheribsd/csh.cshrc.in")

    def get_dot_bashrc_template(self):
        return include_local_file("files/cheribsd/dot.bashrc.in")

    def get_dot_bash_profile_template(self):
        return include_local_file("files/cheribsd/dot.bash_profile.in")


def _default_disk_image_name(_: CheriConfig, directory: Path, project: "BuildDiskImageBase"):
    if project.use_qcow2:
        suffix = "qcow2"
    else:
        suffix = "img"
    # Don't add the os_prefix to the disk image name since it should already be encoded in project.disk_image_prefix)
    return directory / (project.disk_image_prefix + project.build_configuration_suffix() + "." + suffix)


def _default_disk_image_hostname(prefix: str) -> "ComputedDefaultValue[str]":
    # noinspection PyProtectedMember
    return ComputedDefaultValue(
        function=lambda conf, proj: prefix + proj.build_configuration_suffix(),
        as_string=prefix + "-<ARCHITECTURE>")


class FileSystemType(Enum):
    UFS = "ufs"
    ZFS = "zfs"


class BuildDiskImageBase(SimpleProject):
    do_not_add_to_targets = True
    disk_image_path: Path = None
    _source_class: "Optional[type[Project]]" = None
    strip_binaries = False  # True by default for minimal disk-image
    is_minimal = False  # To allow building a much smaller image
    disk_image_prefix: str = None
    default_disk_image_path = ComputedDefaultValue(
        function=lambda conf, proj: _default_disk_image_name(conf, conf.output_root, proj),
        as_string=lambda cls: "$OUTPUT_ROOT/" + cls.disk_image_prefix + "-<TARGET>-disk.img depending on architecture")

    @classproperty
    def default_architecture(self) -> CrossCompileTarget:
        return self._source_class.default_architecture

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return self._source_class.supported_architectures

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        return (cls._source_class.get_class_for_target(cls.get_crosscompile_target()).target,)

    @classmethod
    def setup_config_options(cls, *, default_hostname, extra_files_suffix="", **kwargs):
        super().setup_config_options()
        cls.extra_files_dir = cls.add_path_option("extra-files", show_help=True,
                                                  default=lambda config, project: (
                                                          config.source_root / ("extra-files" + extra_files_suffix)),
                                                  help="A directory with additional files that will be added to the "
                                                       "image (default: "
                                                       "'$SOURCE_ROOT/extra-files" + extra_files_suffix + "')",
                                                  metavar="DIR")
        cls.hostname = cls.add_config_option("hostname", show_help=False, default=default_hostname, metavar="HOSTNAME",
                                             help="The hostname to use for the disk image")
        if "use_qcow2" not in cls.__dict__:
            cls.use_qcow2 = cls.add_bool_option("use-qcow2",
                                                help="Convert the disk image to QCOW2 format instead of raw")
        cls.rootfs_type = cls.add_config_option("rootfs-type", show_help=True,
                                                kind=FileSystemType, default=FileSystemType.UFS,
                                                enum_choices=[FileSystemType.UFS, FileSystemType.ZFS],
                                                help="Select the type of the root file system image.")
        cls.remote_path = cls.add_config_option("remote-path", show_help=False, metavar="PATH",
                                                help="When set rsync will be used to update the image from "
                                                     "the remote server instead of building it locally.")
        cls.wget_via_tmp = cls.add_bool_option("wget-via-tmp",
                                               help="Use a directory in /tmp for recursive wget operations;"
                                                    "of interest in rare cases, like extra-files on smbfs.")
        cls.include_gdb = cls.add_bool_option("include-gdb", default=True,
                                              help="Include GDB in the disk image (if it exists)")
        cls.include_kgdb = cls.add_bool_option("include-kgdb", default=False,
                                               help="Include KGDB in the disk image (if it exists)")
        assert cls.default_disk_image_path is not None
        cls.disk_image_path = cls.add_path_option("path", default=cls.default_disk_image_path, metavar="IMGPATH",
                                                  help="The output path for the disk image", show_help=True)
        cls.force_overwrite = cls.add_bool_option("force-overwrite", default=True,
                                                  help="Overwrite an existing disk image without prompting")
        cls.no_autoboot = cls.add_bool_option("no-autoboot", default=False,
                                              help="Disable autoboot and boot menu for targets that use loader(8)")

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("ssh-keygen", apt="openssh-client", zypper="openssh-clients")

    def __init__(self, *args, **kwargs) -> None:
        # TODO: different extra-files directory
        super().__init__(*args, **kwargs)
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifest_file: Optional[Path] = None
        self.extra_files: "list[Path]" = []
        self.auto_prefixes = ["usr/local/", "opt/", "extra/", "bin/bash"]
        self.makefs_cmd: Optional[Path] = None
        self.mkimg_cmd: Optional[Path] = None
        self.minimum_image_size = "1g"  # minimum image size = 1GB
        self.mtree = MtreeFile(verbose=self.config.verbose)
        self.input_metalogs = []
        # used during process to generated files
        self.tmpdir: Optional[Path] = None
        self.file_templates = _AdditionalFileTemplates()
        self.hostname = os.path.expandvars(self.hostname)  # Expand env vars in hostname to allow $CHERI_BITS
        # MIPS needs big-endian disk images
        self.big_endian = self.compiling_for_mips(include_purecap=True)

    @cached_property
    def source_project(self) -> BuildFreeBSD:
        source_class = self._source_class.get_class_for_target(self._get_source_class_target())
        assert issubclass(source_class, BuildFreeBSD), source_class
        return source_class.get_instance(self)

    @property
    def rootfs_dir(self) -> Path:
        return self.source_project.install_dir

    @property
    def user_group_db_dir(self) -> Path:
        return self.rootfs_dir / "etc"

    def setup(self) -> None:
        super().setup()
        self.input_metalogs = [self.rootfs_dir / "METALOG.world", self.rootfs_dir / "METALOG.kernel"]

    def _get_source_class_target(self):
        return self.crosscompile_target

    def add_file_to_image(self, file: Path, *, base_directory: "Optional[Path]" = None, user="root", group="wheel",
                          mode=None, path_in_target=None, strip_binaries: "Optional[bool]" = None):
        if path_in_target is None:
            assert base_directory is not None, "Either base_directory or path_in_target must be set!"
            path_in_target = os.path.relpath(str(file), str(base_directory))
        assert not str(path_in_target).startswith(".."), path_in_target

        if strip_binaries is None:
            strip_binaries = self.strip_binaries
        if strip_binaries:
            # Try to shrink the size by stripping all elf binaries
            stripped_path = self.tmpdir / path_in_target
            if self.maybe_strip_elf_file(file, output_path=stripped_path):
                self.verbose_print("Stripped ELF binary", file)
                file = stripped_path

        if not self.config.quiet:
            self.info(file, " -> /", path_in_target, sep="")

        # This also adds all the parent directories to METALOG
        self.mtree.add_file(file, path_in_target, mode=mode, uname=user, gname=group, print_status=self.config.verbose)
        if file in self.extra_files:
            self.extra_files.remove(file)  # remove it from extra_files so we don't install it twice

    def create_file_for_image(self, path_in_image: str, *, contents: str = "\n", show_contents_non_verbose=False,
                              mode=None):
        if path_in_image.startswith("/"):
            path_in_image = path_in_image[1:]
        assert not path_in_image.startswith("/")
        user_provided = self.extra_files_dir / path_in_image
        if user_provided.is_file():
            self.verbose_print("Using user provided /", path_in_image, " instead of generating default", sep="")
            self.extra_files.remove(user_provided)
            target_file = user_provided
            base_dir = self.extra_files_dir
        else:
            assert user_provided not in self.extra_files
            target_file = self.tmpdir / path_in_image
            base_dir = self.tmpdir
            if self.config.verbose or (show_contents_non_verbose and not self.config.quiet):
                print("Generating /", path_in_image, " with the following contents:\n",
                      coloured(AnsiColour.green, contents), sep="", end="")
            self.write_file(target_file, contents, never_print_cmd=True, overwrite=False, mode=mode)
        self.add_file_to_image(target_file, base_directory=base_dir)

    def prepare_rootfs(self):
        assert self.tmpdir is not None
        assert self.manifest_file is not None
        # skip parsing the metalog in the git push hook since it takes a long time and isn't that useful
        for metalog in self.input_metalogs:
            if metalog.exists() and not os.getenv("_TEST_SKIP_METALOG"):
                self.mtree.load(metalog, append=True)
            else:
                self.fatal("Could not find required input mtree file", metalog)

        # We need to add /etc/fstab and /etc/rc.conf and the SSH host keys to the disk-image.
        # If they do not exist in the extra-files directory yet we generate a default one and use that
        # Additionally, all other files in the extra-files directory will be added to the disk image

        if self.extra_files_dir.exists():
            self.add_all_files_in_dir(self.extra_files_dir)

        # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
        # Overlay extra-files over additional stuff over cheribsd rootfs dir

        fstab_contents = ""
        if self.rootfs_type == FileSystemType.UFS:
            fstab_contents += "/dev/ufs/root / ufs rw,noatime 1 1\n"
        if self.include_swap_partition:
            fstab_contents += "/dev/gpt/swap none swap sw 0 0\n"
        fstab_contents += self.file_templates.get_fstab_template()
        self.create_file_for_image("/etc/fstab", contents=fstab_contents, show_contents_non_verbose=True)

        # enable ssh and set hostname
        # TODO: use separate file in /etc/rc.conf.d/ ?
        rc_conf_contents = self.file_templates.get_rc_conf_template().format(hostname=self.hostname)
        self.create_file_for_image("/etc/rc.conf", contents=rc_conf_contents, show_contents_non_verbose=False)

        cshrc_contents = self.file_templates.get_cshrc_template().format(SRCPATH=self.config.source_root,
                                                                         ROOTFS_DIR=self.rootfs_dir)
        self.create_file_for_image("/etc/csh.cshrc", contents=cshrc_contents)

        # Basic .bashrc/.bash_profile template
        dot_bashrc_contents = self.file_templates.get_dot_bashrc_template().format(SRCPATH=self.config.source_root,
                                                                                   ROOTFS_DIR=self.rootfs_dir)
        self.create_file_for_image("/root/.bashrc", contents=dot_bashrc_contents)
        self.create_file_for_image("/usr/share/skel/dot.bashrc", contents=dot_bashrc_contents)
        dot_bash_profile_contents = self.file_templates.get_dot_bash_profile_template().format(
            SRCPATH=self.config.source_root,
            ROOTFS_DIR=self.rootfs_dir)
        self.create_file_for_image("/root/.bash_profile", contents=dot_bash_profile_contents)
        self.create_file_for_image("/usr/share/skel/dot.bash_profile", contents=dot_bash_profile_contents)

        # Add the mount-source/mount-rootfs/do-reroot scripts (even in the minimal image)
        # TODO: should we omit this from the minimal image?
        non_cheri_dirname = "non-cheri-rootfs-not-found"
        hybrid_cheri_dirname = "hybrid-cheri-rootfs-not-found"
        purecap_cheri_dirname = "purecap-cheri-rootfs-not-found"

        def path_relative_to_outputroot(xtarget) -> Path:
            if xtarget not in self.supported_architectures:
                return Path("/target/not/supported")
            install_dir = self._source_class.get_install_dir(self, cross_target=xtarget)
            try:
                return install_dir.relative_to(self.config.output_root)
            except ValueError:
                self.info(install_dir, "is not relative to", self.config.output_root,
                          "-- qemu-mount-rootfs.sh may not mount it")
                return Path("/invalid/path")

        if self.crosscompile_target.is_hybrid_or_purecap_cheri():
            non_cheri_dirname = path_relative_to_outputroot(self.crosscompile_target.get_non_cheri_target())
            hybrid_cheri_dirname = path_relative_to_outputroot(self.crosscompile_target.get_cheri_hybrid_target())
            purecap_cheri_dirname = path_relative_to_outputroot(self.crosscompile_target.get_cheri_purecap_target())
        mount_rootfs_script = include_local_file("files/cheribsd/qemu-mount-rootfs.sh.in").format(
            SRCPATH=self.config.source_root, ROOTFS_DIR=self.rootfs_dir,
            NOCHERI_ROOTFS_DIRNAME=non_cheri_dirname, HYBRID_ROOTFS_DIRNAME=hybrid_cheri_dirname,
            PURECAP_ROOTFS_DIRNAME=purecap_cheri_dirname)
        self.create_file_for_image("/sbin/qemu-mount-rootfs.sh", contents=mount_rootfs_script,
                                   mode=0o755, show_contents_non_verbose=False)
        mount_sources_script = include_local_file("files/cheribsd/qemu-mount-sources.sh.in").format(
            SRCPATH=self.config.source_root, ROOTFS_DIR=self.rootfs_dir)
        self.create_file_for_image("/sbin/qemu-mount-sources.sh", contents=mount_sources_script,
                                   mode=0o755, show_contents_non_verbose=False)
        do_reroot_script = include_local_file("files/cheribsd/qemu-do-reroot.sh.in").format(
            SRCPATH=self.config.source_root, ROOTFS_DIR=self.rootfs_dir)
        self.create_file_for_image("/sbin/qemu-do-reroot.sh", contents=do_reroot_script,
                                   mode=0o755, show_contents_non_verbose=False)
        self.create_file_for_image("/sbin/startup-benchmark.sh", mode=0o755, show_contents_non_verbose=False,
                                   contents=include_local_file("files/cheribsd/startup-benchmark.sh"))
        # Add a script to launch gdb, run a program and get a backtrace:
        self.create_file_for_image("/usr/bin/gdb-run.sh", contents=include_local_file("files/cheribsd/gdb-run.sh"),
                                   mode=0o755, show_contents_non_verbose=False)
        # And another one for non-interactive use:
        self.create_file_for_image("/usr/bin/gdb-run-noninteractive.sh",
                                   contents=include_local_file("files/cheribsd/gdb-run-noninteractive.sh"),
                                   mode=0o755, show_contents_non_verbose=False)

        # Add a script to turn of network and stop running services:
        self.create_file_for_image("/usr/bin/prepare-benchmark-environment.sh",
                                   contents=include_local_file("files/cheribsd/prepare-benchmark-environment.sh"),
                                   mode=0o755, show_contents_non_verbose=False)

        # Update test suite config to skip tests disabled in FreeBSD CI and skip slow tests by default.
        # For example, the mkimg tests take almost 6 hours out of 22 total on RISCV purecap.
        kyua_config_path = "etc/kyua/kyua.conf"
        kyua_config = self.rootfs_dir / kyua_config_path
        if not kyua_config.exists():
            self.info("SSHD not installed, not changing sshd_config")
        else:
            self.info("Adding kyua configuration variables for CI to", kyua_config.relative_to(self.rootfs_dir))
            # make sure we can login as root with pubkey auth:
            new_kyua_config_contents = self.read_file(kyua_config)
            new_kyua_config_contents += include_local_file("files/cheribsd/kyua.conf.append")
            self.create_file_for_image("/" + kyua_config_path, contents=new_kyua_config_contents,
                                       show_contents_non_verbose=False)

        # make sure that the disk image always has the same SSH host keys
        # If they don't exist the system will generate one on first boot and we have to accept them every time
        self.generate_ssh_host_keys()

        sshd_config = self.rootfs_dir / "etc/ssh/sshd_config"
        if not sshd_config.exists():
            self.info("SSHD not installed, not changing sshd_config")
        else:
            self.info("Adding 'PermitRootLogin without-password\nUseDNS no' to /etc/ssh/sshd_config")
            # make sure we can login as root with pubkey auth:
            new_sshd_config_contents = self.read_file(sshd_config)
            new_sshd_config_contents += "\n# Allow root login with pubkey auth:\nPermitRootLogin without-password\n"
            new_sshd_config_contents += "\n# Major speedup to SSH performance:\n UseDNS no\n"
            self.create_file_for_image("/etc/ssh/sshd_config", contents=new_sshd_config_contents,
                                       show_contents_non_verbose=False)
        # now try adding the right ~/.ssh/authorized_keys
        authorized_keys = self.extra_files_dir / "root/.ssh/authorized_keys"
        if not authorized_keys.is_file():
            ssh_keys = list(Path(os.path.expanduser("~/.ssh/")).glob("*.pub"))
            if len(ssh_keys) > 0:
                print("Found the following ssh keys:", list(map(str, ssh_keys)))
                if self.query_yes_no("Would you like to add them to /root/.ssh/authorized_keys in the image?",
                                     default_result=True):
                    contents = ""
                    for pubkey in ssh_keys:
                        contents += self.read_file(pubkey)
                    self.create_file_for_image("/root/.ssh/authorized_keys", contents=contents, mode=0o600)
                    if self.query_yes_no("Should this authorized_keys file be used by default? "
                                         "(You can always change them by editing/deleting '" +
                                         str(authorized_keys) + "')?"):
                        self.install_file(self.tmpdir / "root/.ssh/authorized_keys", authorized_keys)
                        # SSHD complains and rejects all connections if /root or /root/.ssh is not 0700
                        self.run_cmd("chmod", "0700", authorized_keys.parent.parent, authorized_keys.parent)
                        self.run_cmd("chmod", "0600", authorized_keys)

        loader_conf_contents = ""
        if self.is_x86:
            loader_conf_contents += "console=\"comconsole\"\nautoboot_delay=0\n"
        if self.no_autoboot:
            if self.crosscompile_target.is_aarch64(include_purecap=True) or self.is_x86:
                loader_conf_contents += "autoboot_delay=\"NO\"\nbeastie_disable=\"YES\"\n"
            else:
                self.warning("--no-autoboot is not supported for this target, ignoring.")
        if self.rootfs_type == FileSystemType.ZFS:
            loader_conf_contents += "zfs_load=\"YES\"\n"
        self.create_file_for_image("/boot/loader.conf", contents=loader_conf_contents, mode=0o644)

        # Avoid long boot time on first start due to missing entropy:
        # for i in ("boot/entropy", "entropy"):
        # We need at least three 4KB entropy files for dhclient to not block on the first arc4random():
        var_db_entrop_files = ["var/db/entropy/entropy." + str(i) for i in range(2)]
        for i in ["boot/entropy", *var_db_entrop_files]:
            # "dd if=/dev/random of="$i" bs=4096 count=1"
            entropy_file = self.tmpdir / i
            self.makedirs(entropy_file.parent)
            if not self.config.pretend:
                with entropy_file.open("wb") as f:
                    random_data = os.urandom(4096)
                    f.write(random_data)
            self.add_file_to_image(entropy_file, base_directory=self.tmpdir)

    def add_gdb(self):
        if not self.include_gdb and not self.include_kgdb:
            return
        # FIXME: if /usr/local/bin/gdb is in the image make /usr/bin/gdb a symlink
        cross_target = self.source_project.crosscompile_target
        if cross_target.is_cheri_purecap():
            cross_target = cross_target.get_cheri_hybrid_for_purecap_rootfs_target()
        if cross_target not in BuildGDB.supported_architectures:
            self.warning("GDB cannot be built for architecture ", cross_target, " -> not addding it")
            return
        if self.include_kgdb:
            gdb_instance = BuildKGDB.get_instance_for_cross_target(cross_target, self.config)
        else:
            gdb_instance = BuildGDB.get_instance_for_cross_target(cross_target, self.config)
        # If we already added GDB in /usr/local/bin (for full disk images), create a symlink to usr/bin/gdb instead of
        # adding another copy.
        if "usr/local/bin/gdb" in self.mtree:
            assert "usr/bin/gdb" not in self.mtree, "GDB already added?"
            self.mtree.add_symlink(path_in_image="usr/bin/gdb", symlink_dest=Path("/usr/local/bin/gdb"))
        else:
            gdb_binary = gdb_instance.real_install_root_dir / "bin/gdb"
            if not gdb_binary.exists():
                # try to add GDB from the build directory
                gdb_binary = gdb_instance.build_dir / "gdb/gdb"
                # self.info("Adding GDB binary from GDB build directory to image")
            if gdb_binary.exists():
                self.info("Adding GDB binary", gdb_binary, "to disk image")
                self.add_file_to_image(gdb_binary, mode=0o755, path_in_target="usr/bin/gdb")
        if self.include_kgdb:
            # If KGDB was already installed
            if "usr/local/bin/kgdb" in self.mtree:
                assert "usr/bin/kgdb" not in self.mtree, "KGDB already added?"
                self.mtree.add_symlink(path_in_image="usr/bin/kgdb", symlink_dest=Path("/usr/local/bin/kgdb"))
            else:
                kgdb_binary = gdb_instance.real_install_root_dir / "bin/kgdb"
                if not kgdb_binary.exists():
                    # try to add KGDB from the build directory
                    kgdb_binary = gdb_instance.build_dir / "gdb/kgdb"
                if kgdb_binary.exists():
                    self.info("Adding KGDB binary", kgdb_binary, "to disk image")
                    self.add_file_to_image(kgdb_binary, mode=0o755, path_in_target="usr/bin/kgdb")

    def add_all_files_in_dir(self, root_dir: Path):
        for root, dirnames, filenames in os.walk(str(root_dir)):
            for ignored_dirname in ('.svn', '.git', '.idea'):
                if ignored_dirname in dirnames:
                    dirnames.remove(ignored_dirname)
            # Symlinks that point to directories are included in dirnames as a
            # historical wart that can't be fixed without risking breakage...
            for filename in filenames + [d for d in dirnames if os.path.islink(Path(root, d))]:
                new_file = Path(root, filename)
                if root_dir == self.extra_files_dir:
                    self.extra_files.append(new_file)
                else:
                    self.add_file_to_image(new_file, base_directory=root_dir)

    @property
    def is_x86(self):
        return self.crosscompile_target.is_any_x86()

    def run_mkimg(self, cmd: list, **kwargs):
        if not self.mkimg_cmd or not self.mkimg_cmd.exists():
            self.fatal(f"Missing mkimg command ('{self.mkimg_cmd}')! Should be found in FreeBSD build dir.",
                       fixit_hint="Pass an explicit path to mkimg by setting the MKIMG_CMD environment variable")
        self.run_cmd([self.mkimg_cmd, *cmd], **kwargs)

    @property
    def include_efi_partition(self):
        if self.crosscompile_target.is_mips(include_purecap=True):
            return False
        # TODO: Make this unconditional once all branches support EFI
        if self.crosscompile_target.is_riscv(include_purecap=True):
            return (self.rootfs_dir / "boot/loader.efi").exists()
        return True

    @property
    def include_swap_partition(self):
        return self.crosscompile_target.is_riscv(include_purecap=True)

    @property
    def rootfs_only(self):
        if self.crosscompile_target.is_mips(include_purecap=True):
            return True
        # Upstream's QEMU config wants /dev/vtbd0 (ours also looks for
        # /dev/ufs/root), so until we boot with UEFI we have to use that
        if not self.target_info.is_cheribsd() and self.crosscompile_target.is_riscv():
            return True
        return False

    def make_x86_disk_image(self, out_img: Path):
        assert self.is_x86
        root_partition = out_img.with_suffix(".root.img")
        try:
            self.make_rootfs_image(root_partition)

            if self.rootfs_type == FileSystemType.ZFS:
                mkimg_bootfs_args = ["-p", "freebsd-boot:=" + str(self.rootfs_dir / "boot/gptzfsboot")]
                mkimg_rootfs_args = ["-p", "freebsd-zfs:=" + str(root_partition)]
            elif self.rootfs_type == FileSystemType.UFS:
                mkimg_bootfs_args = ["-p", "freebsd-boot:=" + str(self.rootfs_dir / "boot/gptboot")]
                mkimg_rootfs_args = ["-p", "freebsd-ufs:=" + str(root_partition)]
            else:
                raise ValueError("Invalid FileSystemType")

            # See mk_nogeli_gpt_ufs_legacy in tools/boot/rootgen.sh in FreeBSD
            self.run_mkimg(["-s", "gpt",  # use GUID Partition Table (GPT)
                            # "-f", "raw",  # raw disk image instead of qcow2
                            "-b", self.rootfs_dir / "boot/pmbr",  # bootload (MBR)
                            *mkimg_bootfs_args,
                            *mkimg_rootfs_args,
                            "-o", out_img,  # output file
                            ], cwd=self.rootfs_dir)
        finally:
            self.delete_file(root_partition)  # no need to keep the partition now that we have built the full image

    def make_gpt_disk_image(self, out_img: Path):
        root_partition = out_img.with_suffix(".root.img")

        if self.include_efi_partition:
            efi_partition = out_img.with_suffix(".efi.img")
        else:
            efi_partition = None

        try:
            if efi_partition is not None:
                self.make_efi_partition(efi_partition)
                if not efi_partition.exists() and not self.config.pretend:
                    self.fatal("Failed to create the EFI partition", efi_partition)
                mkimg_efi_args = ["-p", "efi:=" + str(efi_partition)]
            else:
                mkimg_efi_args = []

            if self.include_swap_partition:
                mkimg_swap_args = ["-p", "freebsd-swap/swap::2G"]
            else:
                mkimg_swap_args = []

            mkimg_rootfs_args = ["-p", f"freebsd-{self.rootfs_type.value}:={root_partition}"]
            self.make_rootfs_image(root_partition)
            self.run_mkimg(["-s", "gpt",  # use GUID Partition Table (GPT)
                            # "-f", "raw",  # raw disk image instead of qcow2
                            *mkimg_efi_args,
                            *mkimg_rootfs_args,
                            *mkimg_swap_args,
                            "-o", out_img,  # output file
                            ], cwd=self.rootfs_dir)
        finally:
            self.delete_file(root_partition)  # no need to keep the partition now that we have built the full image
            if efi_partition is not None:
                self.delete_file(efi_partition)  # no need to keep the partition now that we have built the full image

    def make_efi_partition(self, efi_partition: Path):
        # See Table 15. UEFI Image Types, UEFI spec v2.8 (Errata B)
        efi_machine_type_short_names = {
            CPUArchitecture.I386: "IA32",
            CPUArchitecture.X86_64: "x64",
            CPUArchitecture.ARM32: "ARM",
            CPUArchitecture.AARCH64: "AA64",
            CPUArchitecture.RISCV64: "RISCV64",
        }
        efi_machine_type_short_name = efi_machine_type_short_names[self.crosscompile_target.cpu_architecture]
        efi_file = "BOOT" + efi_machine_type_short_name + ".EFI"
        # Use loader_simp for minimal images as it's smaller and doesn't require any additional files
        loader_file = "loader_simp.efi" if self.is_minimal else "loader.efi"

        with tempfile.NamedTemporaryFile(mode="w+") as tmp_mtree:
            use_makefs = True
            mtools = BuildMtools.get_instance(self, cross_target=CompilationTargets.NATIVE)
            mtools_bin = mtools.install_dir / "bin"

            if use_makefs:
                # Makefs doesn't handle contents= right now
                efi_mtree = MtreeFile(verbose=self.config.verbose)
                efi_mtree.add_file(self.rootfs_dir / "boot" / loader_file, path_in_image="efi/boot/" + efi_file.lower(),
                                   mode=0o644)
                efi_mtree.write(tmp_mtree, pretend=self.config.pretend)
                tmp_mtree.flush()  # ensure the file is actually written
                self.run_cmd("cat", tmp_mtree.name)
                # Note: it appears msdosfs makefs only works if you pass a fixed size, so use 2m which is large
                # enough to fit either loader_simp.efi or loader.efi.
                self.run_cmd([self.makefs_cmd, "-t", "msdos", "-s", "2m",
                              # "-d", "0x2fffffff",  # super verbose output
                              # "-d", "0x20000000",  # MSDOSFS debug output
                              "-B", "le",  # byte order little endian
                              "-N", self.user_group_db_dir,
                              str(efi_partition), str(tmp_mtree.name)], cwd=self.rootfs_dir)
            else:
                # Use this (and mtools) instead: https://wiki.osdev.org/UEFI_Bare_Bones#Creating_the_FAT_image
                if not (mtools_bin / "mformat").exists():
                    self.fatal("Build mtools first: `cheribuild.py mtools`")
                self.run_cmd("dd", "if=/dev/zero", "of=" + str(efi_partition), "bs=1k", "count=2048")
                self.run_cmd(mtools_bin / "mformat", "-i", efi_partition, "-f", "2048", "::")
                self.run_cmd(mtools_bin / "mmd", "-i", efi_partition, "::/EFI")
                self.run_cmd(mtools_bin / "mmd", "-i", efi_partition, "::/EFI/BOOT")
                self.run_cmd(mtools_bin / "mcopy", "-i", efi_partition,
                             self.rootfs_dir / "boot" / loader_file, "::/EFI/BOOT/" + efi_file.upper())
            if (mtools_bin / "minfo").exists():
                # Get some information about the created image information:
                self.run_cmd(mtools_bin / "minfo", "-i", efi_partition)
                self.run_cmd(mtools_bin / "mdir", "-i", efi_partition)
                self.run_cmd(mtools_bin / "mdir", "-i", efi_partition, "-/", "::")
                # self.run_cmd(mtools_bin / "mdu", "-i", efi_partition, "-a", "::")

    def make_rootfs_image(self, rootfs_img: Path):
        # write out the manifest file:
        self.mtree.write(self.manifest_file, pretend=self.config.pretend)
        # print(self.manifest_file.read_text())

        makefs_flags = []
        if self.rootfs_type == FileSystemType.ZFS:
            makefs_flags = [
                "-t", "zfs",
                "-o", "poolname=zroot,rootpath=/,bootfs=zroot",
                "-s", "5g",
            ]
        elif self.rootfs_type == FileSystemType.UFS:
            debug_options = []
            if self.config.debug_output:
                debug_options = ["-d", "0x90000"]  # trace POPULATE and WRITE_FILE events
            # For the minimal image 2m of free space and 1k inodes should be enough
            # For the larger images we need a lot more space (llvm-{cheri,morello} needs more than 1g)
            if self.is_minimal:
                free_blocks = "2m"
            else:
                free_blocks = "2g"

            extra_flags = []
            if self.is_x86:
                # x86: -t ffs -f 200000 -s 8g -o version=2,bsize=32768,fsize=4096
                extra_flags = ["-o", "bsize=32768,fsize=4096,label=root"]
            makefs_flags = debug_options + extra_flags + [
                "-t", "ffs",  # BSD fast file system
                "-o", "version=2,label=root",  # UFS2
                "-o", "softupdates=1",  # Enable soft updates journaling
                "-Z",  # sparse file output
                "-b", free_blocks,
                "-f", "1k" if self.is_minimal else "200k",
                # minimum 1024 free inodes for minimal, otherwise at least 1M
                "-R", "4m",  # round up size to the next 4m multiple
                "-M", self.minimum_image_size,
                "-B", "be" if self.big_endian else "le",  # byte order
            ]
        try:
            self.run_cmd([self.makefs_cmd, *makefs_flags,
                          "-N", self.user_group_db_dir,
                          # use master.passwd from the cheribsd source not the current systems passwd file
                          # which makes sure that the numeric UID values are correct
                          rootfs_img,  # output file
                          self.manifest_file,  # use METALOG as the manifest for the disk image
                          ], cwd=self.rootfs_dir)
        except Exception:
            self.warning("makefs failed, if it reports an issue with METALOG report a bug (could be either cheribuild"
                         " or cheribsd) and attach the METALOG file.")
            self.query_yes_no("About to delete the temporary directory. Copy any files you need before pressing enter.",
                              yes_no_str="")
            raise

    def make_disk_image(self):
        # check that qemu-img exists before starting the potentially long-running makefs command
        qemu_img_command = self.config.qemu_bindir / "qemu-img"
        if not qemu_img_command.is_file():
            system_qemu_img = shutil.which("qemu-img")
            if system_qemu_img:
                self.info("qemu-img from CHERI SDK not found, falling back to system qemu-img")
                qemu_img_command = Path(system_qemu_img)
            else:
                self.info("qemu-img command was not found. Will not be able to create QCOW2 images")

        if self.use_qcow2:
            # If we're going to generate a qcow2 image, avoid clobbering the non-qcow2 image and don't generate a .qcow2
            # file that isn't acqually qcow2.
            raw_img = self.disk_image_path.with_suffix(".raw")
        else:
            raw_img = self.disk_image_path

        if self.rootfs_only:
            self.make_rootfs_image(raw_img)
        elif self.is_x86:
            # X86 currently requires special handling
            # TODO: Switch to normal UEFI booting
            self.make_x86_disk_image(raw_img)
        else:
            self.make_gpt_disk_image(raw_img)

        # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
        if not self.config.quiet and qemu_img_command.exists():
            self.run_cmd(qemu_img_command, "info", raw_img)
        if self.use_qcow2:
            if not qemu_img_command.exists():
                self.fatal("Cannot create QCOW2 image without qemu-img command!")
            # create a qcow2 version from the raw image:
            self.run_cmd(qemu_img_command, "convert",
                         "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
                         "-O", "qcow2",  # convert to qcow2 format
                         raw_img,  # input file
                         self.disk_image_path)  # output file
            self.delete_file(raw_img, print_verbose_only=True)
            if self.config.verbose:
                self.run_cmd(qemu_img_command, "info", self.disk_image_path)

    def copy_from_remote_host(self):
        self.info("Copying disk image instead of building it.")
        rsync_path = os.path.expandvars(self.remote_path)
        self.info("Will copy the disk-image from ", rsync_path, sep="")
        if not self.query_yes_no("Continue?"):
            return
        self.copy_remote_file(rsync_path, self.disk_image_path)

    def process(self):
        self.__process()

    @staticmethod
    def path_from_env(var, default=None) -> Optional[Path]:
        s = os.getenv(var)
        if s:
            return Path(s)
        return default

    def __process(self):
        if self.disk_image_path.is_dir():
            # Given a directory, derive the default file name inside it
            self.disk_image_path = _default_disk_image_name(self.config, self.disk_image_path, self)

        if self.disk_image_path.is_file():
            # only show prompt if we can actually input something to stdin
            if not self.with_clean and not self.force_overwrite:
                # with --clean always delete the image
                opt = self.get_config_option_name("force_overwrite")
                self.info("An image already exists (" + str(self.disk_image_path) + "). ", end="")
                self.info("Note: Pass", coloured(AnsiColour.yellow, "--" + opt),
                          coloured(AnsiColour.cyan, "to skip this prompt or add"),
                          coloured(AnsiColour.yellow, "\"" + opt + "\": true"),
                          coloured(AnsiColour.cyan, "to", self.config.loader.config_file_path))
                if not self.query_yes_no("Overwrite?", default_result=True):
                    return  # we are done here
            self.delete_file(self.disk_image_path)

        # we can only build disk images on FreeBSD, so copy the file if we aren't
        if self.remote_path is not None:
            self.copy_from_remote_host()
            return

        self.makefs_cmd = self.path_from_env("MAKEFS_CMD")
        self.mkimg_cmd = self.path_from_env("MKIMG_CMD")

        # Try to find makefs and install in the freebsd build dir
        freebsd_builddir = self.source_project.objdir
        if not self.makefs_cmd:
            makefs_xtool = freebsd_builddir / "tmp/usr/sbin/makefs"
            if makefs_xtool.exists():
                self.makefs_cmd = makefs_xtool
        if not self.mkimg_cmd:
            mkimg_xtool = freebsd_builddir / "tmp/usr/bin/mkimg"
            if mkimg_xtool.exists():
                self.mkimg_cmd = mkimg_xtool

        if not self.makefs_cmd or not self.makefs_cmd.exists():
            self.fatal("Missing makefs command ('{}')! Should be found in FreeBSD build dir ({})".format(
                self.makefs_cmd, freebsd_builddir),
                fixit_hint="Pass an explicit path to makefs by setting the MAKEFS_CMD environment variable")
        self.info("Disk image will be saved to", self.disk_image_path)
        self.info("Disk image root fs is", self.rootfs_dir)
        self.info("Extra files for the disk image will be copied from", self.extra_files_dir)

        for metalog in self.input_metalogs:
            if not metalog.is_file():
                self.fatal("mtree manifest", metalog, "is missing")
        if not (self.user_group_db_dir / "master.passwd").is_file():
            self.fatal("master.passwd does not exist in ", self.user_group_db_dir)

        with tempfile.TemporaryDirectory(prefix="cheribuild-" + self.target + "-") as tmp:
            self.tmpdir = Path(tmp)
            self.manifest_file = self.tmpdir / "METALOG"
            self.prepare_rootfs()
            # now add all the user provided files to the image:
            # we have to make a copy as we modify self.extra_files in self.add_file_to_image()
            for p in self.extra_files.copy():
                path_in_image = p.relative_to(self.extra_files_dir)
                self.verbose_print("Adding user provided file /", path_in_image, " to disk image.", sep="")
                self.add_file_to_image(p, base_directory=self.extra_files_dir)

            # then walk the rootfs to see if any additional files should be added:
            if not os.getenv("_TEST_SKIP_METALOG"):
                # skip adding to the metalog in the git push hook since it takes a long time and isn't that useful
                self.add_unlisted_files_to_metalog()
            # Add/symlink GDB (if requested).
            self.add_gdb()
            # finally create the disk image
            self.make_disk_image()
        self.tmpdir = None
        self.manifest_file = None

    def add_unlisted_files_to_metalog(self):
        unlisted_files = []
        rootfs_str = str(self.rootfs_dir)  # compat with python < 3.6
        for root, dirnames, filenames in os.walk(rootfs_str):
            for filename in filenames:
                full_path = Path(root, filename)
                target_path = os.path.relpath(str(full_path), rootfs_str)
                added = False
                for prefix in self.auto_prefixes:
                    if target_path.startswith(prefix):
                        self.mtree.add_file(full_path, target_path, print_status=self.config.verbose)
                        added = True
                        break
                if added:
                    continue
                elif target_path not in self.mtree:
                    # METALOG is not added to the disk image
                    if target_path not in ("METALOG", "METALOG.kernel", "METALOG.world"):
                        unlisted_files.append((full_path, target_path))
        if unlisted_files:
            print("Found the following files in the rootfs that are not listed in METALOG:")
            for i in unlisted_files:
                print("\t", i[1])
            if self.query_yes_no("Should these files also be added to the image?", default_result=True,
                                 force_result=True):
                for i in unlisted_files:
                    self.mtree.add_file(i[0], i[1], print_status=self.config.verbose)

    def generate_ssh_host_keys(self):
        # do the same as "ssh-keygen -A" just with a different output directory as it does not allow customizing that
        ssh_dir = self.extra_files_dir / "etc/ssh"
        self.makedirs(ssh_dir)
        # -t type Specifies the type of key to create.  The possible values are "rsa1" for protocol version 1
        #  and "dsa", "ecdsa","ed25519", or "rsa" for protocol version 2.

        for key_type in ("rsa", "dsa", "ecdsa", "ed25519"):
            # SSH1 protocol uses just /etc/ssh/ssh_host_key without the type
            private_key_name = "ssh_host_key" if key_type == "rsa1" else "ssh_host_" + key_type + "_key"
            private_key = ssh_dir / private_key_name
            public_key = ssh_dir / (private_key_name + ".pub")
            if not private_key.is_file():
                self.run_cmd("ssh-keygen", "-t", key_type,
                             "-N", "",  # no passphrase
                             "-f", str(private_key))
            self.add_file_to_image(private_key, base_directory=self.extra_files_dir, mode="0600")
            self.add_file_to_image(public_key, base_directory=self.extra_files_dir, mode="0644")


class BuildMinimalCheriBSDDiskImage(BuildDiskImageBase):
    target = "disk-image-minimal"
    _source_class = BuildCHERIBSD
    disk_image_prefix = "cheribsd-minimal"
    include_boot_kernel = True
    include_boot_files = True

    class _MinimalFileTemplates(_AdditionalFileTemplates):
        def get_rc_conf_template(self):
            return include_local_file("files/minimal-image/etc/rc.conf.in")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(default_hostname=_default_disk_image_hostname("cheribsd-minimal"),
                                     extra_files_suffix="-minimal", **kwargs)
        cls.strip_binaries = cls.add_bool_option(
            "strip", default=True, help="strip ELF files to reduce size of generated image")
        cls.include_cheribsdtest = cls.add_bool_option(
            "include-cheribsdtest", default=True, help="Also add static cheribsdtest base variants to the disk image")
        cls.kernels = cls.add_list_option("kernel-names", default=[""],
                                          help="Kernel(s) to include in the image; empty string or '/' for "
                                               "/boot/kernel/, X for /boot/kernel.X/")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.minimum_image_size = "20m"  # let's try to shrink the image size
        # The base input is only cheribsdbox and all the symlinks
        self.file_templates = BuildMinimalCheriBSDDiskImage._MinimalFileTemplates()
        self.is_minimal = True

    def setup(self):
        super().setup()
        self.input_metalogs = [self.rootfs_dir / "cheribsdbox.mtree"]

    @property
    def include_swap_partition(self):
        return False

    @staticmethod
    def _have_cplusplus_support(_: "list[str]"):
        # C++ runtime was not available for RISC-V purecap due to https://github.com/CTSRD-CHERI/llvm-project/issues/379
        # This has now been fixed, but could be an issue again in the future so keep this function around.
        return True

    def process_files_list(self, files_list):
        for line in io.StringIO(files_list).readlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            assert not line.startswith("/")
            # Otherwise find the file in the rootfs
            file_path: Path = self.rootfs_dir / line
            if not file_path.exists():
                self.fatal("Required file", line, "missing from rootfs")
            if file_path.is_dir():
                self.mtree.add_dir(line, reference_dir=file_path, print_status=self.config.verbose)
            else:
                self.add_file_to_image(file_path, base_directory=self.rootfs_dir)

    def add_unlisted_files_to_metalog(self):
        # Now add all the files from *.files to the image:
        self.verbose_print("Adding files from rootfs to minimal image:")
        files_to_add = [include_local_file("files/minimal-image/base.files"),
                        include_local_file("files/minimal-image/etc.files")]
        if self._have_cplusplus_support(["lib", "usr/lib"]):
            files_to_add.append(include_local_file("files/minimal-image/need-cplusplus.files"))
        if self.include_boot_kernel:
            for k in self.kernels:
                kernel_dir = "kernel" if k in ("", "/") else f"kernel.{k}"
                files_to_add.append(f"boot/{kernel_dir}/kernel")
        elif self.kernels is not None:
            self.warning("This disk image is not installing kernels, yet kernel names given.")

        for files_list in files_to_add:
            self.process_files_list(files_list)

        # At least one runtime linker must be present - they will be included in
        # METALOG so we don't need to add manually
        ld_elf_path = self.rootfs_dir / "libexec/ld-elf.so.1"
        if ld_elf_path.exists():
            self.add_file_to_image(ld_elf_path, base_directory=self.rootfs_dir)
        else:
            self.warning("default ABI runtime linker not present in rootfs at", ld_elf_path)
            self.ask_for_confirmation("Are you sure you want to continue?")
        # Add all compat ABI runtime linkers that we find in the rootfs:
        for rtld_abi in ("elf32", "elf64", "elf64c", "elf64cb"):
            rtld_path = self.rootfs_dir / "libexec" / f"ld-{rtld_abi}.so.1"
            if rtld_path.exists():
                self.add_file_to_image(rtld_path, base_directory=self.rootfs_dir)

        self.add_required_libraries(["lib", "usr/lib"])
        # Add compat libraries (may not exist if it was built with -DWITHOUT_LIB64, etc.)
        for libcompat_dir in ("lib32", "lib64", "lib64c", "lib64cb"):
            fullpath = self.rootfs_dir / "usr" / libcompat_dir
            if fullpath.is_symlink():
                # add the libcompat symlinks to ensure that we can always use lib64/lib64c in test scripts
                self.mtree.add_symlink(src_symlink=self.rootfs_dir / "usr" / libcompat_dir,
                                       path_in_image="usr/" + libcompat_dir)
                if (self.rootfs_dir / libcompat_dir).is_symlink():
                    self.mtree.add_symlink(src_symlink=self.rootfs_dir / libcompat_dir, path_in_image=libcompat_dir)
            elif (fullpath / "libc.so").exists():
                self.add_required_libraries(["usr/" + libcompat_dir])

        if self.include_cheribsdtest:
            for test_binary in (self.rootfs_dir / "bin").glob("cheribsdtest-*"):
                self.add_file_to_image(test_binary, base_directory=self.rootfs_dir)

        # These dirs seem to be needed
        self.mtree.add_dir("var/db", print_status=self.config.verbose)
        self.mtree.add_dir("var/empty", print_status=self.config.verbose)

        if self.include_boot_files and (self.is_x86 or self.compiling_for_aarch64(include_purecap=True)):
            # When booting minimal disk images, we need the files in /boot (kernel+loader), but we omit modules.
            extra_files = []
            for root, dirnames, filenames in os.walk(str(self.rootfs_dir / "boot")):
                for filename in filenames:
                    new_file = Path(root, filename)
                    # Don't add kernel modules
                    if new_file.suffix == ".ko":
                        # Except for those needed to run tests
                        if new_file.name not in ("tmpfs.ko", "smbfs.ko", "libiconv.ko", "libmchain.ko", "if_vtnet.ko"):
                            continue
                    # Also don't add the kernel with debug info
                    if new_file.suffix == ".full" and new_file.name.startswith("kernel"):
                        continue
                    extra_files.append(new_file)
                    # Stripping kernel modules makes them unloadable:
                    # kldload: /boot/kernel/smbfs.ko: file must have exactly one symbol table
                    self.add_file_to_image(new_file, base_directory=self.rootfs_dir, strip_binaries=False)
            self.verbose_print("Boot files:\n\t", "\n\t".join(map(str, sorted(extra_files))))
        self.verbose_print("Not adding unlisted files to METALOG since we are building a minimal image")

    def add_required_libraries(self, libdirs: "list[str]"):
        optional_libs = []
        required_libs = [
            "libc.so.7",
            "libcrypt.so.5",
            "libm.so.5",
            "libthr.so.3",
            "libutil.so.9",
            "libz.so.6",
            # Commonly used (and tiny)
            "libdl.so.1",
            "libncursesw.so.9",
            "libxo.so.0",
            "libz.so.6",
        ]
        # required, but versions were bumped with changes to ncurses
        optional_libs += [
            # needed by /bin/sh & /bin/csh (if we included the purecap sh/csh)
            "libedit.so.7", "libedit.so.8",
        ]
        # required, but versions were bumped for OpenSSL 3
        optional_libs += [
            # cheribsdbox depends on SSL
            "libcrypto.so.111", "libcrypto.so.30",
            "libssl.so.111", "libssl.so.30",
        ]
        # additional cheribsdbox dependencies (PAM+SSL+BSM)
        # We don't know what ABI cheribsdbox is built for so let's just add the libraries for all ABIs
        required_libs += [
            "libbsm.so.3",
            "libpam.so.6",
            "libypclnt.so.4",  # needed by pam_unix.so.6
            # cheribsdbox links these three dynamically since they are needed by other programs too
            "libprocstat.so.1",
            "libkvm.so.7",
            "libelf.so.2",
            # Needed for backtrace() (required by CTest)
            "libexecinfo.so.1",  # depends on libelf.so
        ]
        # Add the required PAM libraries for su(1)/login(1)
        for i in ("permit", "rootok", "self", "unix", "nologin", "securetty", "lastlog", "login_access"):
            required_libs += ["pam_" + i + ".so", "pam_" + i + ".so.6"]

        # Libraries to include if they exist
        optional_libs += [
            # Needed for most benchmarks, but not supported on all architectures
            "libstatcounters.so.3",
        ]

        if self._have_cplusplus_support(libdirs):
            required_libs += ["libc++.so.1", "libcxxrt.so.1", "libgcc_s.so.1"]

        for libs, required in [(required_libs, True), (optional_libs, False)]:
            for library_basename in libs:
                full_lib_path = None
                for library_dir in libdirs:
                    guess = self.rootfs_dir / library_dir / library_basename
                    if guess.exists():
                        full_lib_path = guess
                if full_lib_path is None:
                    if len(libdirs) == 1:
                        prefix = libdirs[0] + "/"
                    else:
                        prefix = "{" + ",".join(libdirs) + "}/"
                    if required:
                        self.fatal("Could not find required library '", prefix + library_basename, "' in rootfs ",
                                   self.rootfs_dir, sep="")
                    else:
                        self.info("Could not find optional library '", prefix + library_basename, "' in rootfs ",
                                  self.rootfs_dir, sep="")
                    continue
                self.add_file_to_image(full_lib_path, base_directory=self.rootfs_dir)

    def prepare_rootfs(self):
        super().prepare_rootfs()
        # Add the additional sysctl configs
        self.create_file_for_image("/etc/pam.d/system", show_contents_non_verbose=False,
                                   contents=include_local_file("files/minimal-image/pam.d/system"))
        # disable coredumps (since there is almost no space on the image)
        self.create_file_for_image("/etc/sysctl.conf", show_contents_non_verbose=False,
                                   contents=include_local_file("files/minimal-image/etc/sysctl.conf"))
        # The actual minimal startup file:
        self.create_file_for_image("/etc/rc", show_contents_non_verbose=False,
                                   contents=include_local_file("files/minimal-image/etc/rc"))

    def make_rootfs_image(self, rootfs_img: Path):
        # update cheribsdbox link in case we stripped it:
        # noinspection PyProtectedMember
        cheribsdbox_entry = self.mtree._mtree.get("./bin/cheribsdbox")
        if not cheribsdbox_entry:
            self.fatal("Could not find cheribsdbox entry in mtree file!")
        else:
            cheribsdbox_path = cheribsdbox_entry.attributes["contents"]
            # create at least one hardlink to cheribsdbox so that mtree can detect that the files are all the same
            dummy_hardlink = Path(cheribsdbox_path).with_suffix(".dummy_hardlink")
            if not self.config.pretend:
                self.delete_file(dummy_hardlink)
                os.link(str(cheribsdbox_path), str(dummy_hardlink))
                if Path(cheribsdbox_path).stat().st_nlink < 2:
                    self.fatal("Need at least one hardlink to cheribsdbox so that makefs can detect deduplicate. "
                               "This should have been created by cheribuild but something must have gone wrong")
            print("Relocating mtree path ./bin/cheribsdbox to use", cheribsdbox_path)
            # noinspection PyProtectedMember
            for i in self.mtree._mtree.values():
                if i.attributes.get("contents", None) == "./bin/cheribsdbox":
                    i.attributes["contents"] = cheribsdbox_path

        # self.run_cmd(["sh", "-c", "du -ah " + shlex.quote(str(self.tmpdir)) + " | sort -h"])
        if self.config.debug_output:
            self.mtree.write(sys.stderr, pretend=self.config.pretend)
        if self.config.verbose:
            self.run_cmd("du", "-ah", self.tmpdir)
            self.run_cmd("sh", "-c", f"du -ah '{self.tmpdir}' | sort -h")
        super().make_rootfs_image(rootfs_img)


class BuildMfsRootCheriBSDDiskImage(BuildMinimalCheriBSDDiskImage):
    target = "disk-image-mfs-root"
    disk_image_prefix = "cheribsd-mfs-root"
    include_boot_kernel = False
    include_boot_files = False

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.include_boot_kernel = cls.add_bool_option("include-kernel", help="Include /boot/kernel/kernel in MFS")

    @property
    def rootfs_only(self):
        return True

    @property
    def cheribsd_class(self):
        return self._source_class


class BuildCheriBSDDiskImage(BuildDiskImageBase):
    target = "disk-image"
    _source_class = BuildCHERIBSD
    disk_image_prefix = "cheribsd"

    @classmethod
    def dependencies(cls, config) -> "tuple[str, ...]":
        result = super().dependencies(config)
        # GDB is not strictly a dependency, but having it in the disk image makes life a lot easier
        xtarget = cls.get_crosscompile_target()
        gdb_xtarget = xtarget.get_cheri_hybrid_for_purecap_rootfs_target() if xtarget.is_cheri_purecap() else xtarget
        result += (BuildGDB.get_class_for_target(gdb_xtarget).target,)
        return result

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(default_hostname=_default_disk_image_hostname("cheribsd"), **kwargs)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.minimum_image_size = "256m"  # let's try to shrink the image size


def _default_tar_name(_: CheriConfig, directory: Path, project: "BuildDiskImageBase"):
    xtarget = project.crosscompile_target
    return directory / (project.disk_image_prefix + project.build_configuration_suffix(xtarget) + ".tar.xz")


class BuildCheriBSDTarball(BuildCheriBSDDiskImage):
    target = "rootfs-tarball"

    default_disk_image_path = ComputedDefaultValue(
        function=lambda conf, proj: _default_tar_name(conf, conf.output_root, proj),
        as_string=lambda cls: "$OUTPUT_ROOT/" + cls.disk_image_prefix + "-<TARGET>.tar.xz depending on architecture")

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("bsdtar", cheribuild_target="bsdtar", apt="libarchive-tools")

    def make_disk_image(self):
        # write out the manifest file:
        self.mtree.write(self.manifest_file, pretend=self.config.pretend)
        bsdtar_path = shutil.which("bsdtar")
        if not bsdtar_path:
            if not self.config.pretend:
                raise LookupError("Could not find bsdtar command in PATH")
            bsdtar_path = "bsdtar"
        try:
            self.run_cmd([bsdtar_path, "acf", self.disk_image_path, "@" + str(self.manifest_file)], cwd=self.rootfs_dir)
        except Exception:
            self.warning("bsdtar failed, if it reports an issue with METALOG report a bug (could be either cheribuild"
                         " or cheribsd) and attach the METALOG file.")
            self.query_yes_no("About to delete the temporary directory. Copy any files you need before pressing enter.",
                              yes_no_str="")
            raise


class BuildFreeBSDImage(BuildDiskImageBase):
    target = "disk-image-freebsd"
    include_os_in_target_suffix = False
    _source_class = BuildFreeBSD
    disk_image_prefix = "freebsd"

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(default_hostname=_default_disk_image_hostname("freebsd"), **kwargs)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # TODO: different extra-files directory
        self.minimum_image_size = "256m"


class BuildFreeBSDWithDefaultOptionsDiskImage(BuildFreeBSDImage):
    target = "disk-image-freebsd-with-default-options"
    _source_class = BuildFreeBSDWithDefaultOptions
    hide_options_from_help = True
