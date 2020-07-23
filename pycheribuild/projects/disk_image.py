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
import typing
from pathlib import Path

from .cross.cheribsd import (BuildCHERIBSD, BuildFreeBSD, BuildFreeBSDDeviceModel, BuildFreeBSDGFE,
                             BuildFreeBSDWithDefaultOptions)
from .cross.gdb import BuildGDB
from .project import (AutotoolsProject, CheriConfig, ComputedDefaultValue, CPUArchitecture, CrossCompileTarget,
                      DefaultInstallDir, GitRepository, MakeCommandKind, SimpleProject)
from ..config.compilation_targets import CompilationTargets
from ..mtree import MtreeFile
from ..targets import target_manager
from ..utils import (AnsiColour, classproperty, coloured, include_local_file, OSInfo, set_env)


# Notes:
# Mount the filesystem of a BSD VM: guestmount -a /foo/bar.qcow2 -m /dev/sda1:/:ufstype=ufs2:ufs --ro /mnt/foo
# ufstype=ufs2 is required as the Linux kernel can't automatically determine which UFS filesystem is being used
# Same thing is possible with qemu-nbd, but needs root (might be faster)


class BuildMtools(AutotoolsProject):
    repository = GitRepository(url="https://github.com/vapier/mtools.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config):
        super().__init__(config)
        self.add_required_system_tool("autoreconf", homebrew="autoconf")
        self.add_required_system_tool("aclocal", homebrew="automake")
        # Manpages won't build:
        self.make_args.set(MAN1="", MAN5="")

    def process(self):
        if not (self.source_dir / "mtools.tmpl.1").exists():
            self.run_cmd("bash", "-xe", "./mkmanpages", cwd=self.source_dir)
        super().process()

    def configure(self, **kwargs):
        if not (self.source_dir / "configure").exists():
            self.run_cmd("autoreconf", "-ivf", cwd=self.source_dir)

        super().configure(**kwargs)


# noinspection PyMethodMayBeStatic
class _AdditionalFileTemplates(object):
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


class _BuildDiskImageBase(SimpleProject):
    do_not_add_to_targets = True
    disk_image_path = None  # type: Path
    _freebsd_build_class = None
    strip_binaries = False  # True by default for minimal disk-image
    is_minimal = False  # To allow building a much smaller image
    default_disk_image_path = None

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
        cls.hostname = cls.add_config_option("hostname", show_help=True, default=default_hostname, metavar="HOSTNAME",
                                             help="The hostname to use for the QEMU image")
        if "use_qcow2" not in cls.__dict__:
            cls.use_qcow2 = cls.add_bool_option("use-qcow2",
                                                help="Convert the disk image to QCOW2 format instead of raw")
        if not OSInfo.IS_FREEBSD:
            cls.remote_path = cls.add_config_option("remote-path", show_help=True, metavar="PATH",
                                                    help="The path on the "
                                                         "remote FreeBSD machine from where to copy the disk image")
        cls.wget_via_tmp = cls.add_bool_option("wget-via-tmp",
                                               help="Use a directory in /tmp for recursive wget operations;"
                                                    "of interest in rare cases, like extra-files on smbfs.")
        cls.include_gdb = cls.add_bool_option("include-gdb", default=True,
                                              help="Include GDB in the disk image (if it exists)")
        assert cls.default_disk_image_path is not None
        cls.disk_image_path = cls.add_path_option("path", default=cls.default_disk_image_path, metavar="IMGPATH",
                                                  help="The output path for the QEMU disk image", show_help=True)
        cls.force_overwrite = cls.add_bool_option("force-overwrite", default=False,
                                                  help="Overwrite an existing disk image without prompting")

    def __init__(self, config, source_class: "typing.Type[BuildFreeBSD]"):
        super().__init__(config)
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifest_file = None  # type: typing.Optional[Path]
        self.extra_files = []  # type: typing.List[Path]
        self.auto_prefixes = ["usr/local/", "opt/", "extra/"]
        self.add_required_system_tool("ssh-keygen")

        self.makefs_cmd = None  # type: typing.Optional[Path]
        self.mkimg_cmd = None  # type: typing.Optional[Path]
        self.source_project = source_class.get_instance(self)
        assert isinstance(self.source_project, BuildFreeBSD)
        self.rootfs_dir = self.source_project.get_install_dir(self)
        assert self.rootfs_dir is not None
        self.user_group_db_dir = self.rootfs_dir / "etc"
        self.cross_build_image = self.source_project.crossbuild
        self.minimum_image_size = "1g"  # minimum image size = 1GB
        self.mtree = MtreeFile()
        self.input_metalog = self.rootfs_dir / "METALOG"
        self.input_metalog_required = True
        # used during process to generated files
        self.tmpdir = None  # type: typing.Optional[Path]
        self.file_templates = _AdditionalFileTemplates()
        self.hostname = os.path.expandvars(self.hostname)  # Expand env vars in hostname to allow $CHERI_BITS
        # MIPS needs big-endian disk images
        self.big_endian = self.compiling_for_mips(include_purecap=True)

    def add_file_to_image(self, file: Path, *, base_directory: Path = None, user="root", group="wheel", mode=None,
                          path_in_target=None):
        if path_in_target is None:
            assert base_directory is not None, "Either base_directory or path_in_target must be set!"
            path_in_target = os.path.relpath(str(file), str(base_directory))
        assert not str(path_in_target).startswith(".."), path_in_target

        if self.strip_binaries and file.exists():
            # Try to shrink the size by stripping all elf binaries
            with file.open("rb") as f:
                if f.read(4) == b"\x7fELF":
                    self.verbose_print("Stripping ELF binary", file)
                    stripped_path = self.tmpdir / path_in_target
                    self.makedirs(stripped_path.parent)
                    self.run_cmd(self.sdk_bindir / "llvm-strip", file, "-o", stripped_path, print_verbose_only=True)
                    # self.run_cmd("file", stripped_path)
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

    def _wget_fetch(self, what, where):
        # https://apple.stackexchange.com/a/100573/251654
        # https://www.gnu.org/software/wget/manual/html_node/Directory-Options.html
        wget_cmd = ["wget", "--no-host-directories", "--cut-dirs=3",  # strip prefix
                    "--timestamping", "-r", "--level", "inf", "--no-parent",  # recursive but ignore parents
                    "--convert-links", "--execute=robots = off",
                    # ignore robots.txt files, don't download robots.txt files"
                    "--no-verbose",
                    ]
        self.run_cmd(wget_cmd + what, cwd=where)

    def _wget_fetch_dir(self, what, where):
        if self.wget_via_tmp:
            with tempfile.TemporaryDirectory(prefix="cheribuild-wget-") as td:
                # Speed things up by using whatever we've got locally, too
                self.run_cmd("rsync", "-avvP", str(where) + "/.", td + "/.")
                self._wget_fetch(what, td)
                self.run_cmd("rsync", "-avvP", "--no-times", "--delete", td + "/.", str(where) + "/.")
        else:
            self._wget_fetch(what, where)

    def prepare_rootfs(self):
        assert self.tmpdir is not None
        assert self.manifest_file is not None
        # skip parsing the metalog in the git push hook since it takes a long time and isn't that useful
        if self.input_metalog.exists() and not os.getenv("_TEST_SKIP_METALOG"):
            self.mtree.load(self.input_metalog)
        elif self.input_metalog_required:
            self.fatal("Could not find required input mtree file", self.input_metalog)

        # We need to add /etc/fstab and /etc/rc.conf and the SSH host keys to the disk-image.
        # If they do not exist in the extra-files directory yet we generate a default one and use that
        # Additionally all other files in the extra-files directory will be added to the disk image

        if self.extra_files_dir.exists():
            self.add_all_files_in_dir(self.extra_files_dir)

        # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
        # Overlay extra-files over additional stuff over cheribsd rootfs dir

        fstab_contents = self.file_templates.get_fstab_template()
        fstab_contents = fstab_contents.format_map(dict(tmpfsrem=""))

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
            install_dir = self.source_project.get_install_dir(self, cross_target=xtarget)
            try:
                return install_dir.relative_to(self.config.output_root)
            except ValueError:
                self.info(install_dir, "is not relative to", self.config.output_root,
                          "-- qemu-mount-rootfs.sh may not mount it")

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
                if self.query_yes_no("Should they be added to /root/.ssh/authorized_keys?", default_result=True):
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

        if self.include_gdb:
            cross_target = self.source_project.get_crosscompile_target(self.config)
            # We always want to include the MIPS GDB for CHERI targets (purecap doesn't work and would be slower):
            if cross_target.is_cheri_purecap():
                cross_target = cross_target.get_cheri_hybrid_target()
            if not any(x is cross_target for x in BuildGDB.supported_architectures):
                self.warning("GDB cannot be built for architecture ", cross_target, " -> not addding it")
            else:
                gdb_instance = BuildGDB.get_instance_for_cross_target(cross_target, self.config)  # type: BuildGDB
                gdb_path = gdb_instance.real_install_root_dir
                gdb_binary = gdb_path / "bin/gdb"
                if not gdb_binary.exists():
                    # try to add GDB from the build directory
                    gdb_binary = gdb_instance.build_dir / "gdb/gdb"
                    # self.info("Adding GDB binary from GDB build directory to image")
                if gdb_binary.exists():
                    self.info("Adding GDB binary", gdb_binary, "to disk image")
                    self.add_file_to_image(gdb_binary, mode=0o755, path_in_target="usr/bin/gdb")

        loader_conf_contents = "beastie_disable=\"yes\"\n"
        if self.is_x86:
            loader_conf_contents += "console=\"comconsole\"\n"
        self.create_file_for_image("/boot/loader.conf", contents=loader_conf_contents, mode=0o644)

        # Avoid long boot time on first start due to missing entropy:
        # for i in ("boot/entropy", "entropy"):
        # We need at least three 4KB entropy files for dhclient to not block on the first arc4random():
        var_db_entrop_files = ["var/db/entropy/entropy." + str(i) for i in range(2)]
        for i in ["boot/entropy"] + var_db_entrop_files:
            # "dd if=/dev/random of="$i" bs=4096 count=1"
            entropy_file = self.tmpdir / i
            self.makedirs(entropy_file.parent)
            if not self.config.pretend:
                with entropy_file.open("wb") as f:
                    random_data = os.urandom(4096)
                    f.write(random_data)
            self.add_file_to_image(entropy_file, base_directory=self.tmpdir)

    def add_all_files_in_dir(self, root_dir: Path):
        for root, dirnames, filenames in os.walk(str(root_dir)):
            for ignored_dirname in ('.svn', '.git', '.idea'):
                if ignored_dirname in dirnames:
                    dirnames.remove(ignored_dirname)
            for filename in filenames:
                new_file = Path(root, filename)
                if root_dir == self.extra_files_dir:
                    self.extra_files.append(new_file)
                else:
                    self.add_file_to_image(new_file, base_directory=root_dir)

    @property
    def is_x86(self):
        return False

    def run_mkimg(self, cmd: list, **kwargs):
        if not self.mkimg_cmd or not self.mkimg_cmd.exists():
            self.fatal("Missing mkimg command! Should be found in FreeBSD build dir (or set $MKIMG_CMD)")
        self.run_cmd([self.mkimg_cmd] + cmd, **kwargs)

    def build_mbr_image(self, root_partition: Path):  # FIXME: doesn't actually work
        assert self.is_x86
        # See mk_nogeli_mbr_ufs_legacy in tools/boot/rootgen.sh in FreeBSD
        # cat > ${src}/etc/fstab <<EOF
        # /dev/ada0s1a	/		ufs	rw	1	1
        # EOF
        # makefs -t ffs -B little -s 200m ${img}.s1a ${src}
        # mkimg -s bsd -b ${src}/boot/boot -p freebsd-ufs:=${img}.s1a -o ${img}.s1
        # mkimg -a 1 -s mbr -b ${src}/boot/boot0sio -p freebsd:=${img}.s1 -o ${img}
        # rm -f ${src}/etc/fstab
        s1_path = self.disk_image_path.with_suffix(".s1.img")
        self.run_mkimg(["-s", "bsd",
                        "-f", "raw",  # raw disk image instead of qcow2
                        "-b", self.rootfs_dir / "boot/boot",  # bootload (MBR)
                        "-p", "freebsd-ufs:=" + str(root_partition),  # rootfs
                        "-o", s1_path  # output file
                        ], cwd=self.rootfs_dir)
        self.run_mkimg(["-a", "1", "-s", "mbr",
                        "-f", "raw",  # raw disk image instead of qcow2
                        "-b", self.rootfs_dir / "boot/boot0sio",  # bootload (MBR)
                        "-p", "freebsd:=" + str(s1_path),  # rootfs
                        "-o", self.disk_image_path  # output file
                        ], cwd=self.rootfs_dir)
        self.delete_file(root_partition)  # no need to keep the partition now that we have built the full image
        self.delete_file(s1_path)  # no need to keep the partition now that we have built the full image

    def build_gpt_image(self, root_partition: Path):
        assert self.is_x86
        # See mk_nogeli_gpt_ufs_legacy in tools/boot/rootgen.sh in FreeBSD
        self.run_mkimg(["-s", "gpt",  # use GUID Partition Table (GPT)
                        # "-f", "raw",  # raw disk image instead of qcow2
                        "-b", self.rootfs_dir / "boot/pmbr",  # bootload (MBR)
                        "-p", "freebsd-boot:=" + str(self.rootfs_dir / "boot/gptboot"),  # gpt boot partition
                        "-p", "freebsd-ufs:=" + str(root_partition),  # rootfs
                        "-o", self.disk_image_path  # output file
                        ], cwd=self.rootfs_dir)
        self.delete_file(root_partition)  # no need to keep the partition now that we have built the full image

    def build_riscv_gpt_image(self, root_partition: Path):
        assert self.crosscompile_target.is_riscv(include_purecap=True)
        # See mk_nogeli_gpt_ufs_legacy in tools/boot/rootgen.sh in FreeBSD
        self.run_mkimg(["-s", "gpt",  # use GUID Partition Table (GPT)
                        # "-f", "raw",  # raw disk image instead of qcow2
                        "-p", "freebsd-ufs:=" + str(root_partition),  # rootfs
                        "-p", "freebsd-swap/swap::2G",
                        "-o", self.disk_image_path  # output file
                        ], cwd=self.rootfs_dir)
        self.delete_file(root_partition)  # no need to keep the partition now that we have built the full image

    def make_aarch64_disk_image(self):
        assert self.crosscompile_target.is_aarch64(include_purecap=True)
        root_partition = self.disk_image_path.with_suffix(".partition.img")
        efi_partition = self.disk_image_path.with_suffix(".efi.img")
        try:
            self.make_efi_partition(efi_partition)
            self.make_rootfs_image(root_partition)
            self.run_mkimg(["-s", "mbr",  # use MBR Partition Table
                            "-p", "efi:=" + str(efi_partition),  # EFI boot partition
                            "-p", "freebsd:=" + str(root_partition),  # rootfs
                            "-o", self.disk_image_path  # output file
                            ], cwd=self.rootfs_dir)
        finally:
            self.delete_file(root_partition)  # no need to keep the partition now that we have built the full image
            self.delete_file(efi_partition)  # no need to keep the partition now that we have built the full image

    def make_efi_partition(self, efi_partition: Path):
        with tempfile.NamedTemporaryFile(mode="w+") as tmp_mtree:
            use_makefs = True
            mtools = BuildMtools.get_instance(self, cross_target=CompilationTargets.NATIVE)
            mtools_bin = mtools.install_dir / "bin"
            if use_makefs:
                # Makefs doesn't handle contents= right now
                efi_mtree = MtreeFile()
                efi_mtree.add_file(self.rootfs_dir / "boot/boot1.efi", path_in_image="efi/boot/bootaa64.efi",
                                   mode=0o644)
                efi_mtree.write(tmp_mtree)
                tmp_mtree.flush()  # ensure the file is actually written
                mtree_contents = self.read_file(Path(tmp_mtree.name))
                assert mtree_contents, "Didn't write file??"
                print(mtree_contents)
                self.run_cmd("cat", tmp_mtree.name)
                self.run_cmd([self.makefs_cmd, "-t", "msdos", "-s", "1m",  # 1 MB
                              # "-d", "0x2fffffff",  # super verbose output
                              "-d", "0x20000000",  # MSDOSFS debug output
                              "-B", "le",  # byte order little endian
                              "-N", self.user_group_db_dir,
                              str(efi_partition), str(tmp_mtree.name)], cwd=self.rootfs_dir)
            else:
                # Use this (and mtools) instead: https://wiki.osdev.org/UEFI_Bare_Bones#Creating_the_FAT_image
                if not (mtools_bin / "mformat").exists():
                    self.fatal("Build mtools first: `cheribuild.py mtools`")
                self.run_cmd("dd", "if=/dev/zero", "of=" + str(efi_partition), "bs=1k", "count=1440")
                self.run_cmd(mtools_bin / "mformat", "-i", efi_partition, "-f", "1440", "::")
                self.run_cmd(mtools_bin / "mmd", "-i", efi_partition, "::/EFI")
                self.run_cmd(mtools_bin / "mmd", "-i", efi_partition, "::/EFI/BOOT")
                self.run_cmd(mtools_bin / "mcopy", "-i", efi_partition,
                             self.rootfs_dir / "boot/boot1.efi", "::/EFI/BOOT/BOOTAA64.EFI")
            if (mtools_bin / "minfo").exists():
                # Get some information about the created image information:
                self.run_cmd(mtools_bin / "minfo", "-i", efi_partition)
                self.run_cmd(mtools_bin / "mdir", "-i", efi_partition)
                self.run_cmd(mtools_bin / "mdir", "-i", efi_partition, "-/", "::")
                # self.run_cmd(mtools_bin / "mdu", "-i", efi_partition, "-a", "::")

    def make_rootfs_image(self, rootfs_img: Path):
        # write out the manifest file:
        self.mtree.write(self.manifest_file)
        # print(self.manifest_file.read_text())
        debug_options = []
        if self.config.debug_output:
            debug_options = ["-d", "0x90000"]  # trace POPULATE and WRITE_FILE events
        try:
            extra_flags = []
            if self.is_x86:
                # x86: -t ffs -f 200000 -s 8g -o version=2,bsize=32768,fsize=4096
                extra_flags = ["-o", "bsize=32768,fsize=4096,label=root"]
            self.run_cmd([self.makefs_cmd] + debug_options + extra_flags + [
                "-t", "ffs",  # BSD fast file system
                "-o", "version=2,label=root",  # UFS2
                "-o", "softupdates=1",  # Enable soft updates journaling
                "-Z",  # sparse file output
                # For the minimal image 2mb of free space and 1k inodes should be enough
                # For the larger images we need a lot more space (kyua needs around 400MB and the test might create
                # big files)
                "-b", "2m" if self.is_minimal else "1g",  # kyua needs a lot of space -> at least 1g
                "-f", "1k" if self.is_minimal else "200k",
                # minimum 1024 free inodes for minimal, otherwise at least 1M
                "-R", "4m",  # round up size to the next 4m multiple
                "-M", self.minimum_image_size,
                "-B", "be" if self.big_endian else "le",  # byte order
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
                self.warning("qemu-img command was not found! Make sure to build target qemu first.")
        # AArch64 and x86 require different disk images:
        if self.crosscompile_target.is_aarch64(include_purecap=True):
            self.make_aarch64_disk_image()
        elif self.is_x86:
            root_partition = self.disk_image_path.with_suffix(".partition.img")
            self.make_rootfs_image(root_partition)
            self.build_gpt_image(root_partition)
            self.delete_file(root_partition)  # no need to keep the partition now that we have built the full image
        elif self.crosscompile_target.is_riscv(
                include_purecap=True) and self.target_info.is_cheribsd() and not self.is_minimal:
            root_partition = self.disk_image_path.with_suffix(".partition.img")
            self.make_rootfs_image(root_partition)
            self.build_riscv_gpt_image(root_partition)
            self.delete_file(root_partition)  # no need to keep the partition now that we have built the full image
        else:
            self.make_rootfs_image(self.disk_image_path)

        # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
        if not self.config.quiet and qemu_img_command.exists():
            self.run_cmd(qemu_img_command, "info", self.disk_image_path)
        if self.use_qcow2:
            if not qemu_img_command.exists():
                self.fatal("Cannot create QCOW2 image without qemu-img command!")
            # create a qcow2 version from the raw image:
            raw_img = self.disk_image_path.with_suffix(".raw")
            self.run_cmd("mv", "-f", self.disk_image_path, raw_img)
            self.run_cmd(qemu_img_command, "convert",
                         "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
                         "-O", "qcow2",  # convert to qcow2 format
                         raw_img,  # input file
                         self.disk_image_path)  # output file
            self.delete_file(raw_img, print_verbose_only=True)
            if self.config.verbose:
                self.run_cmd(qemu_img_command, "info", self.disk_image_path)

    def copy_from_remote_host(self):
        self.info("Cannot build disk image on non-FreeBSD systems, will attempt to copy instead.")
        if not self.remote_path:
            self.fatal("Path to the remote disk image is not set, option '--", self.target,
                       "/remote-path' must be set to a path that scp understands (e.g. vica:/foo/bar/disk.img)", sep="")
            return
        # noinspection PyAttributeOutsideInit
        self.remote_path = os.path.expandvars(self.remote_path)
        self.info("Will copy the disk-image from ", self.remote_path, sep="")
        if not self.query_yes_no("Continue?"):
            return

        self.copy_remote_file(self.remote_path, self.disk_image_path)

    def process(self):
        if not OSInfo.IS_FREEBSD and self.cross_build_image:
            with set_env(PATH=str(self.config.output_root / "freebsd-cross/bin") + ":" + os.getenv("PATH")):
                self.__process()
        else:
            self.__process()

    @staticmethod
    def path_from_env(var, default=None) -> typing.Optional[Path]:
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
            if not self.config.clean and not self.force_overwrite:
                # with --clean always delete the image
                print("An image already exists (" + str(self.disk_image_path) + "). ", end="")
                if not self.query_yes_no("Overwrite?", default_result=True):
                    return  # we are done here
            self.delete_file(self.disk_image_path)

        # we can only build disk images on FreeBSD, so copy the file if we aren't
        if not OSInfo.IS_FREEBSD and not self.cross_build_image:
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
            self.fatal(
                "Missing makefs command ('{}')! Should be found in FreeBSD build dir (or set $MAKEFS_CMD)".format(
                    self.makefs_cmd))
        self.info("Disk image will be saved to", self.disk_image_path)
        self.info("Disk image root fs is", self.rootfs_dir)
        self.info("Extra files for the disk image will be copied from", self.extra_files_dir)

        if not self.input_metalog.is_file():
            self.fatal("mtree manifest", self.input_metalog, "is missing")
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
                    if target_path != "METALOG":  # METALOG is not added to METALOG
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

        for keyType in ("rsa", "dsa", "ecdsa", "ed25519"):
            # SSH1 protocol uses just /etc/ssh/ssh_host_key without the type
            private_key_name = "ssh_host_key" if keyType == "rsa1" else "ssh_host_" + keyType + "_key"
            private_key = ssh_dir / private_key_name
            public_key = ssh_dir / (private_key_name + ".pub")
            if not private_key.is_file():
                self.run_cmd("ssh-keygen", "-t", keyType,
                             "-N", "",  # no passphrase
                             "-f", str(private_key))
            self.add_file_to_image(private_key, base_directory=self.extra_files_dir, mode="0600")
            self.add_file_to_image(public_key, base_directory=self.extra_files_dir, mode="0644")


def _default_disk_image_name(config: CheriConfig, directory: Path, project: SimpleProject, img_prefix=""):
    # old name for cheribsd:
    xtarget = project.get_crosscompile_target(config)
    if xtarget.is_mips(include_purecap=True):
        # Backwards compat (different prefix for hybrid+purecap images):
        if xtarget.is_cheri_hybrid():
            return directory / (img_prefix + "mips-hybrid" + project.cheri_config_suffix + ".img")
        if xtarget.is_cheri_purecap():
            return directory / (img_prefix + "mips-purecap" + project.cheri_config_suffix + ".img")
    suffix = (xtarget.generic_suffix if xtarget else "<TARGET>") + project.cheri_config_suffix
    return config.output_root / (img_prefix + suffix + ".img")


def _default_freebsd_disk_image_name(config: CheriConfig, project: SimpleProject):
    return _default_disk_image_name(config, config.output_root, project, "freebsd-")


class BuildMinimalCheriBSDDiskImage(_BuildDiskImageBase):
    project_name = "disk-image-minimal"
    dependencies = ["qemu", "cheribsd"]  # TODO: include gdb?
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_HYBRID, CompilationTargets.CHERIBSD_MIPS_NO_CHERI,
                               CompilationTargets.CHERIBSD_MIPS_PURECAP,
                               CompilationTargets.CHERIBSD_RISCV_PURECAP, CompilationTargets.CHERIBSD_RISCV_HYBRID,
                               CompilationTargets.CHERIBSD_RISCV_NO_CHERI,
                               ]

    class _MinimalFileTemplates(_AdditionalFileTemplates):
        def get_fstab_template(self):
            return include_local_file("files/minimal-image/etc/fstab.in")

        def get_rc_conf_template(self):
            return include_local_file("files/minimal-image/etc/rc.conf.in")

    default_disk_image_path = ComputedDefaultValue(
        function=lambda conf, proj: _default_disk_image_name(conf, conf.output_root, proj, "cheribsd-minimal-"),
        as_string="$OUTPUT_ROOT/minimal-<TARGET>-disk.img depending on architecture")

    @classmethod
    def setup_config_options(cls, **kwargs):
        host_username = CheriConfig.get_user_name()
        default_hostname = ComputedDefaultValue(
            function=lambda conf, proj: "qemu-cheri" + proj.cheri_config_suffix + "-" + host_username,
            as_string="qemu-cheri${ABI}-" + host_username)

        super().setup_config_options(default_hostname=default_hostname, extra_files_suffix="-minimal", **kwargs)
        cls.strip_binaries = cls.add_bool_option("strip", default=True,
                                                 help="strip ELF files to reduce size of generated image")
        cls.include_cheritest = cls.add_bool_option("include-cheritest", default=True,
                                                    help="Also add cheritest/cheriabitest to the disk image")
        cls.use_cheribsd_purecap_rootfs = cls.add_bool_option("use-cheribsd-purecap-rootfs", default=False,
                                                              help="Use the rootfs built by cheribsd-purecap instead")

    def __init__(self, config: CheriConfig):
        self.rootfs_xtarget = self.get_crosscompile_target(config)
        if self.rootfs_xtarget.is_cheri_hybrid([CPUArchitecture.MIPS64]) and self.use_cheribsd_purecap_rootfs:
            self.rootfs_xtarget = CompilationTargets.CHERIBSD_MIPS_PURECAP
        if self.rootfs_xtarget.is_cheri_hybrid([CPUArchitecture.RISCV64]) and self.use_cheribsd_purecap_rootfs:
            self.rootfs_xtarget = CompilationTargets.CHERIBSD_RISCV_HYBRID
        self.cheribsd_class = BuildCHERIBSD.get_class_for_target(
            self.rootfs_xtarget)  # type: typing.Type[BuildCHERIBSD]
        assert self.cheribsd_class.get_crosscompile_target(config) == self.rootfs_xtarget
        super().__init__(config, source_class=self.cheribsd_class)
        self.minimum_image_size = "20m"  # let's try to shrink the image size
        # The base input is only cheribsdbox and all the symlinks
        self.input_metalog = self.rootfs_dir / "cheribsdbox.mtree"
        self.file_templates = BuildMinimalCheriBSDDiskImage._MinimalFileTemplates()
        self.is_minimal = True

    def _have_cplusplus_support(self, libdirs: "typing.List[str]"):
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
            file_path = self.rootfs_dir / line  # type: Path
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
        for rtld_basename in ("ld-elf32.so.1", "ld-elf64.so.1", "ld-cheri-elf.so.1"):
            rtld_path = self.rootfs_dir / "libexec" / rtld_basename
            if rtld_path.exists():
                self.add_file_to_image(rtld_path, base_directory=self.rootfs_dir)

        self.add_required_libraries(["lib", "usr/lib"])
        # Add compat libraries (may not exist if it was built with -DWITHOUT_LIB64, etc.)
        for libcompat_dir in ("libcheri", "lib64", "lib32"):
            fullpath = self.rootfs_dir / "usr" / libcompat_dir
            if fullpath.is_symlink():
                # add the libcompat symlinks to ensure that we can always use lib64/libcheri in test scripts
                self.mtree.add_symlink(src_symlink=self.rootfs_dir / "usr" / libcompat_dir,
                                       path_in_image="usr/" + libcompat_dir)
                if (self.rootfs_dir / libcompat_dir).is_symlink():
                    self.mtree.add_symlink(src_symlink=self.rootfs_dir / libcompat_dir, path_in_image=libcompat_dir)
            elif (fullpath / "libc.so").exists():
                self.add_required_libraries(["usr/" + libcompat_dir])

        if self.include_cheritest:
            for i in ("cheritest", "cheriabitest"):
                test_binary = self.rootfs_dir / "bin" / i  # type: Path
                if test_binary.exists():
                    self.add_file_to_image(test_binary, base_directory=self.rootfs_dir)

        # These dirs seem to be needed
        self.mtree.add_dir("var/db", print_status=self.config.verbose)
        self.mtree.add_dir("var/empty", print_status=self.config.verbose)

        self.verbose_print("Not adding unlisted files to METALOG since we are building a minimal image")

    def add_required_libraries(self, libdirs: "typing.List[str]"):
        required_libs = [
            "libc.so.7",
            "libcrypt.so.5",
            "libm.so.5",
            "libthr.so.3",
            "libutil.so.9",
            "libz.so.6",
            # Commonly used (and tiny)
            "libdl.so.1",
            # needed by /bin/sh & /bin/csh (if we included the purecap sh/csh)
            "libedit.so.7",
            "libncursesw.so.9",
            "libxo.so.0",
            "libz.so.6",
            ]
        # additional cheribsdbox dependencies (PAM+SSL+BSM)
        # We don't know what ABI cheribsdbox is built for so let's just add the libraries for all ABIs
        required_libs += [
            "libbsm.so.3",
            "libcrypto.so.111",
            "libssl.so.111",
            # PAM libraries (we should only need pam_permit/pam_rootok)
            "libpam.so.6",
            "pam_permit.so",
            "pam_permit.so.6",
            "pam_rootok.so",
            "pam_rootok.so.6",
            ]
        if self.rootfs_xtarget.is_mips(include_purecap=True):
            # Needed for most benchmarks (MIPS-only):
            required_libs.append("libstatcounters.so.3")

        if self._have_cplusplus_support(libdirs):
            required_libs += ["libc++.so.1", "libcxxrt.so.1", "libgcc_s.so.1"]

        for library_basename in required_libs:
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
                self.fatal("Could not find required library '", prefix + library_basename, "' in rootfs ",
                           self.rootfs_dir, sep="")
                continue
            self.add_file_to_image(full_lib_path, base_directory=self.rootfs_dir)

    def prepare_rootfs(self):
        super().prepare_rootfs()
        # Add the additional sysctl configs
        self.create_file_for_image("/etc/pam.d/su", show_contents_non_verbose=False,
                                   contents=include_local_file("files/minimal-image/pam.d/su"))
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
            self.mtree.write(sys.stderr)
        if self.config.verbose:
            self.run_cmd("du", "-ah", self.tmpdir)
            self.run_cmd("sh", "-c", "du -ah '{}' | sort -h".format(self.tmpdir))
        super().make_rootfs_image(rootfs_img)


class _RISCVFileTemplates(_AdditionalFileTemplates):
    def get_fstab_template(self):
        return include_local_file("files/riscv/fstab.in")


class _X86FileTemplates(_AdditionalFileTemplates):
    def get_fstab_template(self):
        return include_local_file("files/x86/fstab.in")


class _AArch64FileTemplates(_AdditionalFileTemplates):
    def get_fstab_template(self):
        return """
/dev/ufs/root	/	ufs	rw,noatime,async	1	1
{tmpfsrem}tmpfs /tmp tmpfs rw,failok 0 0
"""


class BuildMultiArchDiskImage(_BuildDiskImageBase):
    do_not_add_to_targets = True
    _source_class = None  # type: typing.Type[SimpleProject]

    @classproperty
    def default_architecture(self) -> CrossCompileTarget:
        return self._source_class.default_architecture

    @classproperty
    def supported_architectures(self):
        return self._source_class.supported_architectures

    @classmethod
    def dependencies(cls, config: CheriConfig):
        return ["qemu", cls._source_class.get_class_for_target(cls.get_crosscompile_target(config)).target]

    @property
    def is_x86(self):
        return self.crosscompile_target.is_any_x86()

    def __init__(self, config: CheriConfig):
        # TODO: different extra-files directory
        src_class = self._source_class.get_class_for_target(self.get_crosscompile_target(config))
        assert issubclass(src_class, BuildFreeBSD)
        super().__init__(config, source_class=src_class)
        if self.get_crosscompile_target(config).is_riscv(include_purecap=True):
            self.file_templates = _RISCVFileTemplates()
        elif self.is_x86:
            self.file_templates = _X86FileTemplates()
        elif self.crosscompile_target.is_aarch64(include_purecap=True):
            self.file_templates = _AArch64FileTemplates()


class BuildCheriBSDDiskImage(BuildMultiArchDiskImage):
    project_name = "disk-image"
    _source_class = BuildCHERIBSD
    _always_add_suffixed_targets = True  # preparation for future multi-target support

    default_disk_image_path = ComputedDefaultValue(
        function=lambda conf, proj: _default_disk_image_name(conf, conf.output_root, proj, "cheribsd-"),
        as_string="$OUTPUT_ROOT/$arch_prefix-disk.img.")

    @classmethod
    def dependencies(cls, config):
        xtarget = cls.get_crosscompile_target(config)
        result = super().dependencies(config)
        # RISCV needs BBL to run:
        if xtarget.is_riscv(include_purecap=True):
            if xtarget.is_hybrid_or_purecap_cheri():
                result.append("gdb-riscv64-hybrid")
            else:
                result.append("gdb-riscv64")
        if xtarget.is_mips(include_purecap=True):
            if xtarget.is_hybrid_or_purecap_cheri():
                result.append("gdb-mips-hybrid")
            else:
                result.append("gdb-mips-nocheri")
        return result

    @classmethod
    def setup_config_options(cls, **kwargs):
        host_username = CheriConfig.get_user_name()
        default_hostname = ComputedDefaultValue(
            function=lambda conf, proj: "qemu-cheri" + proj.cheri_config_suffix + "-" + host_username,
            as_string="qemu-cheri${ABI}-" + host_username)

        super().setup_config_options(default_hostname=default_hostname, **kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.minimum_image_size = "256m"  # let's try to shrink the image size


class BuildFreeBSDImage(BuildMultiArchDiskImage):
    target = "disk-image-freebsd"
    _source_class = BuildFreeBSD

    default_disk_image_path = ComputedDefaultValue(function=_default_freebsd_disk_image_name,
                                                   as_string="$OUTPUT_ROOT/freebsd-$SUFFIX.img")

    @classmethod
    def setup_config_options(cls, **kwargs):
        host_username = CheriConfig.get_user_name()
        suffix = cls._xtarget.generic_suffix if cls._xtarget else "<TARGET>"
        super().setup_config_options(default_hostname="qemu-" + suffix + "-" + host_username, **kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # TODO: different extra-files directory
        self.minimum_image_size = "256m"


class BuildFreeBSDWithDefaultOptionsDiskImage(BuildFreeBSDImage):
    project_name = "disk-image-freebsd-with-default-options"
    _source_class = BuildFreeBSDWithDefaultOptions
    hide_options_from_help = True


class BuildFreeBSDGFEDiskImage(BuildFreeBSDImage):
    project_name = "disk-image-freebsd-gfe"
    _source_class = BuildFreeBSDGFE
    hide_options_from_help = True


class BuildFreeBSDDeviceModelDiskImage(BuildFreeBSDWithDefaultOptionsDiskImage):
    project_name = "disk-image-freebsd-device-model"
    _source_class = BuildFreeBSDDeviceModel
    hide_options_from_help = True


# Backwards compatibility:
target_manager.add_target_alias("disk-image-purecap", "disk-image-mips-purecap", deprecated=True)
target_manager.add_target_alias("disk-image-minimal-purecap", "disk-image-minimal-mips-purecap", deprecated=True)
target_manager.add_target_alias("disk-image-native", "disk-image-amd64", deprecated=True)
target_manager.add_target_alias("disk-image-x86_64", "disk-image-amd64", deprecated=True)
