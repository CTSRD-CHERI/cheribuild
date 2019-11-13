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
import datetime
import io

from .cross.cheribsd import *
from .cross.gdb import BuildGDB
from .project import *
from ..config.chericonfig import MipsFloatAbi
from ..mtree import MtreeFile
from ..utils import *

# Notes:
# Mount the filesystem of a BSD VM: guestmount -a /foo/bar.qcow2 -m /dev/sda1:/:ufstype=ufs2:ufs --ro /mnt/foo
# ufstype=ufs2 is required as the Linux kernel can't automatically determine which UFS filesystem is being used
# Same thing is possible with qemu-nbd, but needs root (might be faster)



PKG_REPO_URL = "https://people.freebsd.org/~brooks/packages/cheribsd-mips-20170403-brooks-20170609/"
# old version of libarchive needed by kyua
OLD_LIBRARIES_BASE_URL = "https://people.freebsd.org/~arichardson/cheri-files/{file}"
# Bump this to redownload all the pkg files
PKG_REPO_NEEDS_UPDATE = datetime.datetime(day=28, month=7, year=2019)


# noinspection PyMethodMayBeStatic
class _AdditionalFileTemplates(object):
    def get_fstab_template(self):
        return includeLocalFile("files/cheribsd/fstab.in")

    def get_rc_conf_template(self):
        return includeLocalFile("files/cheribsd/rc.conf.in")

    def get_cshrc_template(self):
        return includeLocalFile("files/cheribsd/csh.cshrc.in")


class _BuildDiskImageBase(SimpleProject):
    doNotAddToTargets = True
    diskImagePath = None  # type: Path
    _freebsd_build_class = None
    strip_binaries = False  # True by default for minimal disk-image
    bigEndian = True # True for MIPS
    is_minimal = False  # To allow building a much smaller image
    default_disk_image_path = None

    @property
    def needs_special_pkg_repo(self):
        return False  # True for CheriBSD

    @classmethod
    def setup_config_options(cls, *, defaultHostname, extraFilesShortname=None, extraFilesSuffix="", **kwargs):
        super().setup_config_options()
        cls.extraFilesDir = cls.add_path_option("extra-files",
            shortname=extraFilesShortname, show_help=True,
            default=lambda config, project: (config.sourceRoot / ("extra-files" + extraFilesSuffix)),
            help="A directory with additional files that will be added to the image (default: "
                 "'$SOURCE_ROOT/extra-files" + extraFilesSuffix + "')", metavar="DIR")
        cls.hostname = cls.add_config_option("hostname", show_help=True, default=defaultHostname, metavar="HOSTNAME",
                                           help="The hostname to use for the QEMU image")
        if "useQCOW2" not in cls.__dict__:
            cls.useQCOW2 = cls.add_bool_option("use-qcow2", help="Convert the disk image to QCOW2 format instead of raw")
        if not IS_FREEBSD:
            cls.remotePath = cls.add_config_option("remote-path", show_help=True, metavar="PATH", help="The path on the "
                                                 "remote FreeBSD machine from where to copy the disk image")
        cls.wget_via_tmp = cls.add_bool_option("wget-via-tmp",
                                help="Use a directory in /tmp for recursive wget operations;"
                                      "of interest in rare cases, like extra-files on smbfs.")
        cls.include_gdb = cls.add_bool_option("include-gdb", default=True, help="Include GDB in the disk image (if it exists)")
        assert cls.default_disk_image_path is not None
        cls.diskImagePath = cls.add_path_option("path", default=cls.default_disk_image_path, metavar="IMGPATH",
                                              help="The output path for the QEMU disk image", show_help=True)
        cls.disableTMPFS = None

    def __init__(self, config, source_class: "typing.Type[BuildFreeBSD]"):
        super().__init__(config)
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifestFile = None  # type: typing.Optional[Path]
        self.extraFiles = []  # type: typing.List[Path]
        self.addRequiredSystemTool("ssh-keygen")

        self.makefs_cmd = None  # type: typing.Optional[Path]
        self.mkimg_cmd = None  # type: typing.Optional[Path]
        self.source_project = source_class.get_instance(self)
        assert isinstance(self.source_project, BuildFreeBSD)
        self.rootfsDir = self.source_project.getInstallDir(self)
        assert self.rootfsDir is not None
        if (self.source_project.sourceDir / "lib/libc/gen/master.passwd").is_file():
            self.userGroupDbDir = self.source_project.sourceDir / "lib/libc/gen"
        else:
            self.userGroupDbDir = self.source_project.sourceDir / "etc"
        self.crossBuildImage = self.source_project.crossbuild
        self.minimumImageSize = "1g"  # minimum image size = 1GB
        self.mtree = MtreeFile()
        self.input_METALOG = self.rootfsDir / "METALOG"
        self.input_METALOG_required = True
        # used during process to generated files
        self.tmpdir = None  # type: typing.Optional[Path]
        self.file_templates = _AdditionalFileTemplates()
        if self.needs_special_pkg_repo:
            self.addRequiredSystemTool("wget")  # Needed to recursively fetch the pkg repo
        self.hostname = os.path.expandvars(self.hostname)   # Expand env vars in hostname to allow $CHERI_BITS

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
                    runCmd(self.sdk_bindir / "llvm-strip", file, "-o", stripped_path)
                    # runCmd("file", stripped_path)
                    file = stripped_path

        if not self.config.quiet:
            statusUpdate(file, " -> /", path_in_target, sep="")

        # This also adds all the parent directories to METALOG
        self.mtree.add_file(file, path_in_target, mode=mode, uname=user, gname=group, print_status=self.config.verbose)
        if file in self.extraFiles:
            self.extraFiles.remove(file)  # remove it from extraFiles so we don't install it twice

    def createFileForImage(self, pathInImage: str, *, contents: str="\n", showContentsByDefault=False, mode=None):
        if pathInImage.startswith("/"):
            pathInImage = pathInImage[1:]
        assert not pathInImage.startswith("/")
        userProvided = self.extraFilesDir / pathInImage
        if userProvided.is_file():
            self.verbose_print("Using user provided /", pathInImage, " instead of generating default", sep="")
            self.extraFiles.remove(userProvided)
            targetFile = userProvided
            baseDir = self.extraFilesDir
        else:
            assert userProvided not in self.extraFiles
            targetFile = self.tmpdir / pathInImage
            baseDir = self.tmpdir
            if self.config.verbose or (showContentsByDefault and not self.config.quiet):
                print("Generating /", pathInImage, " with the following contents:\n",
                      coloured(AnsiColour.green, contents), sep="", end="")
            self.writeFile(targetFile, contents, noCommandPrint=True, overwrite=False, mode=mode)
        self.add_file_to_image(targetFile, base_directory=baseDir)

    @staticmethod
    def _wget_fetch(what, where):
        # https://apple.stackexchange.com/a/100573/251654
        # https://www.gnu.org/software/wget/manual/html_node/Directory-Options.html
        wget_cmd = ["wget", "--no-host-directories", "--cut-dirs=3",  # strip prefix
                    "--timestamping", "-r", "--level",  "inf", "--no-parent",  # recursive but ignore parents
                    "--convert-links", "--execute=robots = off",  # ignore robots.txt files, don't download robots.txt files"
                    "--no-verbose",
                    ]
        runCmd(wget_cmd + what, cwd=where)

    def _wget_fetch_dir(self, what, where):
        if self.wget_via_tmp:
            with tempfile.TemporaryDirectory(prefix="cheribuild-wget-") as td:
                # Speed things up by using whatever we've got locally, too
                runCmd("rsync", "-avvP", str(where) + "/.", td + "/.")
                self._wget_fetch(what, td)
                runCmd("rsync", "-avvP", "--no-times", "--delete", td + "/.", str(where) + "/.")
        else:
            self._wget_fetch(what, where)

    def prepareRootfs(self):
        assert self.tmpdir is not None
        assert self.manifestFile is not None
        # skip parsing the metalog in the git push hook since it takes a long time and isn't that useful
        if self.input_METALOG.exists() and not os.getenv("_TEST_SKIP_METALOG"):
            self.mtree.load(self.input_METALOG)
        elif self.input_METALOG_required:
            self.fatal("Could not find required input mtree file", self.input_METALOG)

        # Add the files needed to install kyua (make sure to download before calculating the list of extra files!)
        if self.needs_special_pkg_repo:
            self.createFileForImage("/etc/local-kyua-pkg/repos/kyua-pkg-cache.conf", mode=0o644,
                                    showContentsByDefault=False,
                                    contents=includeLocalFile("files/cheribsd/kyua-pkg-cache.repo.conf"))
            self.createFileForImage("/etc/local-kyua-pkg/config/pkg.conf", mode=0o644,
                                    showContentsByDefault=False,
                                    contents=includeLocalFile("files/cheribsd/kyua-pkg-cache.options.conf"))
            self.createFileForImage("/sbin/prepare-testsuite.sh", mode=0o755, showContentsByDefault=False,
                                    contents=includeLocalFile("files/cheribsd/prepare-testsuite.sh"))
            # Download all the kyua pkg files from and put them in /var/db/kyua-pkg-cache
            # But only do that if we really need to update (since the recursive wget is slow)

            custom_pkg_repo_root_dir = self.config.outputRoot / "kyua-cheribsd-pkgcache"
            download_time_path = (custom_pkg_repo_root_dir / "var/db/kyua-pkg-cache/.downloaded_time")
            needs_fresh_download = True
            if download_time_path.exists():
                last_downloaded = datetime.datetime.utcfromtimestamp(float(download_time_path.read_text()))
                self.verbose_print("pkg repo was downloaded", last_downloaded)
                if last_downloaded > PKG_REPO_NEEDS_UPDATE:
                    needs_fresh_download = False
                    statusUpdate("Not fetching pkg repo since download time", last_downloaded,
                                 "is newer than oldest acceptable download time", PKG_REPO_NEEDS_UPDATE,
                                 "\nTo force an update delete the file", str(download_time_path))

            if needs_fresh_download:
                pkgcache_dir = custom_pkg_repo_root_dir / "var/db/kyua-pkg-cache"
                self.makedirs(pkgcache_dir)
                self.makedirs(pkgcache_dir)
                runCmd("find", pkgcache_dir)
                self._wget_fetch_dir(["--accept", "*.txz", # only download .txz files
                                      PKG_REPO_URL], pkgcache_dir)
                # fetch old libarchive which is currently needed
                # We also need old versions of libssl and libcrypto
                self.makedirs(custom_pkg_repo_root_dir / "usr/lib")
                for lib in ("libarchive.so.6", "libcrypto.so.8", "libssl.so.8"):
                    self._wget_fetch([OLD_LIBRARIES_BASE_URL.format(file=lib)], custom_pkg_repo_root_dir / "usr/lib")
                self.writeFile(download_time_path, str(datetime.datetime.utcnow().timestamp()), overwrite=True)
            self.add_all_files_in_dir(custom_pkg_repo_root_dir)
        # we need to add /etc/fstab and /etc/rc.conf as well as the SSH host keys to the disk-image
        # If they do not exist in the extra-files directory yet we generate a default one and use that
        # Additionally all other files in the extra-files directory will be added to the disk image
        if self.extraFilesDir.exists():
            self.add_all_files_in_dir(self.extraFilesDir)

        # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
        # Overlay extra-files over additional stuff over cheribsd rootfs dir

        fstabContents = self.file_templates.get_fstab_template()

        if self.disableTMPFS:
            fstabContents = fstabContents.format_map(dict(tmpfsrem="#"))
        else:
            fstabContents = fstabContents.format_map(dict(tmpfsrem=""))

        self.createFileForImage("/etc/fstab", contents=fstabContents, showContentsByDefault=True)

        # enable ssh and set hostname
        # TODO: use separate file in /etc/rc.conf.d/ ?
        rcConfContents = self.file_templates.get_rc_conf_template().format(hostname=self.hostname)
        self.createFileForImage("/etc/rc.conf", contents=rcConfContents, showContentsByDefault=False)

        cshrcContents = self.file_templates.get_cshrc_template().format(
            SRCPATH=self.config.sourceRoot, ROOTFS_DIR=self.rootfsDir)
        self.createFileForImage("/etc/csh.cshrc", contents=cshrcContents)

        # Add the mount-source/mount-rootfs/do-reroot scripts (even in the minimal image)
        # TODO: should we omit this from the minimal image?
        mount_rootfs_script = includeLocalFile("files/cheribsd/qemu-mount-rootfs.sh.in").format(
            SRCPATH=self.config.sourceRoot, ROOTFS_DIR=self.rootfsDir)
        self.createFileForImage("/sbin/qemu-mount-rootfs.sh", contents=mount_rootfs_script,
                                mode=0o755, showContentsByDefault=False)
        mount_sources_script = includeLocalFile("files/cheribsd/qemu-mount-sources.sh.in").format(
            SRCPATH=self.config.sourceRoot, ROOTFS_DIR=self.rootfsDir)
        self.createFileForImage("/sbin/qemu-mount-sources.sh", contents=mount_sources_script,
                                mode=0o755, showContentsByDefault=False)
        do_reroot_script = includeLocalFile("files/cheribsd/qemu-do-reroot.sh.in").format(
            SRCPATH=self.config.sourceRoot, ROOTFS_DIR=self.rootfsDir)
        self.createFileForImage("/sbin/qemu-do-reroot.sh", contents=do_reroot_script,
                                mode=0o755, showContentsByDefault=False)

        # Add a script to launch gdb, run a program and get a backtrace:
        self.createFileForImage("/usr/bin/gdb-run.sh", contents=includeLocalFile("files/cheribsd/gdb-run.sh"),
                                mode=0o755, showContentsByDefault=False)

        # Add a script to turn of network and stop running services:
        self.createFileForImage("/usr/bin/prepare-benchmark-environment.sh",
                                contents=includeLocalFile("files/cheribsd/prepare-benchmark-environment.sh"),
                                mode=0o755, showContentsByDefault=False)

        # make sure that the disk image always has the same SSH host keys
        # If they don't exist the system will generate one on first boot and we have to accept them every time
        self.generateSshHostKeys()

        sshdConfig = self.rootfsDir / "etc/ssh/sshd_config"
        if not sshdConfig.exists():
            self.info("SSHD not installed, not changing sshd_config")
        else:
            self.info("Adding 'PermitRootLogin without-password\nUseDNS no' to /etc/ssh/sshd_config")
            # make sure we can login as root with pubkey auth:
            newSshdConfigContents = self.readFile(sshdConfig)
            newSshdConfigContents += "\n# Allow root login with pubkey auth:\nPermitRootLogin without-password\n"
            newSshdConfigContents += "\n# Major speedup to SSH performance:\n UseDNS no\n"
            self.createFileForImage("/etc/ssh/sshd_config", contents=newSshdConfigContents,
                                    showContentsByDefault=False)
        # now try adding the right ~/.ssh/authorized_keys
        authorizedKeys = self.extraFilesDir / "root/.ssh/authorized_keys"
        if not authorizedKeys.is_file():
            sshKeys = list(Path(os.path.expanduser("~/.ssh/")).glob("*.pub"))
            if len(sshKeys) > 0:
                print("Found the following ssh keys:", list(map(str, sshKeys)))
                if self.query_yes_no("Should they be added to /root/.ssh/authorized_keys?", default_result=True):
                    contents = ""
                    for pubkey in sshKeys:
                        contents += self.readFile(pubkey)
                    self.createFileForImage("/root/.ssh/authorized_keys", contents=contents, mode=0o600)
                    if self.query_yes_no("Should this authorized_keys file be used by default? "
                                       "(You can always change them by editing/deleting '" +
                                       str(authorizedKeys) + "')?"):
                        self.installFile(self.tmpdir / "root/.ssh/authorized_keys", authorizedKeys)
                        # SSHD complains and rejects all connections if /root or /root/.ssh is not 0700
                        runCmd("chmod", "0700", authorizedKeys.parent.parent, authorizedKeys.parent)
                        runCmd("chmod", "0600", authorizedKeys)

        if self.include_gdb:
            cross_target = self.source_project.get_crosscompile_target(self.config)
            # We always want to include the MIPS GDB for CHERI targets (purecap doesn't work and would be slower):
            if cross_target.is_cheri_purecap([CPUArchitecture.MIPS64]):
                cross_target = CompilationTargets.CHERIBSD_MIPS
            if not any(x is cross_target for x in BuildGDB.supported_architectures):
                warningMessage("GDB cannot be built for architecture ", cross_target, " -> not addding it")
            else:
                gdb_instance = BuildGDB.get_instance_for_cross_target(cross_target, self.config) # type: BuildGDB
                gdb_path = gdb_instance.real_install_root_dir
                gdb_binary = gdb_path / "bin/gdb"
                if not gdb_binary.exists():
                    # try to add GDB from the build directory
                    gdb_binary = gdb_instance.buildDir / "gdb/gdb"
                    # self.info("Adding GDB binary from GDB build directory to image")
                if gdb_binary.exists():
                    self.info("Adding GDB binary", gdb_binary, "to disk image")
                    self.add_file_to_image(gdb_binary, mode=0o755, path_in_target="usr/bin/gdb")

        loader_conf_contents = "beastie_disable=\"yes\"\n"
        if self.is_x86:
            loader_conf_contents += "console=\"comconsole\"\n"
        self.createFileForImage("/boot/loader.conf", contents=loader_conf_contents, mode=0o644)

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
            for blacklisted_dirname in ('.svn', '.git', '.idea'):
                if blacklisted_dirname in dirnames:
                    dirnames.remove(blacklisted_dirname)
            for filename in filenames:
                new_file = Path(root, filename)
                if root_dir == self.extraFilesDir:
                    self.extraFiles.append(new_file)
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
        s1_path = self.diskImagePath.with_suffix(".s1.img")
        self.run_mkimg(["-s", "bsd",
                        "-f", "raw",  # raw disk image instead of qcow2
                        "-b", self.rootfsDir / "boot/boot",  # bootload (MBR)
                        "-p", "freebsd-ufs:=" + str(root_partition),  # rootfs
                        "-o", s1_path  # output file
                        ], cwd=self.rootfsDir)
        self.run_mkimg(["-a", "1", "-s", "mbr",
                        "-f", "raw",  # raw disk image instead of qcow2
                        "-b", self.rootfsDir / "boot/boot0sio",  # bootload (MBR)
                        "-p", "freebsd:=" + str(s1_path),  # rootfs
                        "-o", self.diskImagePath  # output file
                        ], cwd=self.rootfsDir)
        self.deleteFile(root_partition)  # no need to keep the partition now that we have built the full image
        self.deleteFile(s1_path)  # no need to keep the partition now that we have built the full image

    def build_gpt_image(self, root_partition: Path):
        assert self.is_x86
        # See mk_nogeli_gpt_ufs_legacy in tools/boot/rootgen.sh in FreeBSD
        self.run_mkimg(["-s", "gpt",  # use GUID Partition Table (GPT)
                        # "-f", "raw",  # raw disk image instead of qcow2
                        "-b", self.rootfsDir / "boot/pmbr",  # bootload (MBR)
                        "-p", "freebsd-boot:=" + str(self.rootfsDir / "boot/gptboot"),  # gpt boot partition
                        "-p", "freebsd-ufs:=" + str(root_partition),  # rootfs
                        "-o", self.diskImagePath  # output file
                        ], cwd=self.rootfsDir)
        self.deleteFile(root_partition)  # no need to keep the partition now that we have built the full image

    def make_rootfs_image(self, rootfs_img: Path):
        # write out the manifest file:
        self.mtree.write(self.manifestFile)
        # print(self.manifestFile.read_text())
        debug_options = []
        if self.config.debug_output:
            debug_options = ["-d", "0x90000"]  # trace POPULATE and WRITE_FILE events
        try:
            extra_flags = []
            if self.is_x86:
                # x86: -t ffs -f 200000 -s 8g -o version=2,bsize=32768,fsize=4096
                extra_flags = ["-t", "ffs", "-o", "version=2,bsize=32768,fsize=4096"]
            runCmd([self.makefs_cmd] + debug_options + extra_flags + [
                "-Z",  # sparse file output
                # For the minimal image 2mb of free space and 1k inodes should be enough
                # For the larger images we need a lot more space (kyua needs around 400MB and the test might create big files)
                "-b", "2m" if self.is_minimal else "1g",  # kyua needs a lot of space -> at least 1g
                "-f", "1k" if self.is_minimal else "200k",  # minimum 1024 free inodes for minimal, otherwise at least 1M
                "-R", "4m",  # round up size to the next 4m multiple
                "-M", self.minimumImageSize,
                "-B", "be" if self.bigEndian else "le",  # byte order
                "-N", self.userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file
                # which makes sure that the numeric UID values are correct
                self.diskImagePath,  # output file
                self.manifestFile,  # use METALOG as the manifest for the disk image
                # extra directories:
                # self.rootfsDir  # directory tree to use for the image
            ], cwd=self.rootfsDir)
        except:
            warningMessage("makefs failed, if it reports an issue with METALOG report a bug (could be either cheribuild"
                           " or cheribsd) and attach the METALOG file.")
            self.query_yes_no("About to delete the temporary directory. Copy any files you need before pressing enter.",
                            yes_no_str="")
            raise

    def make_disk_image(self) -> Path:
        if self.is_x86:
            if not self.mkimg_cmd:
                self.fatal("Missing freebsd mkimg command! Should be found in FreeBSD build dir")
            root_partition = self.diskImagePath.with_suffix(".partition.img")
            self.make_rootfs_image(root_partition)
            self.build_gpt_image(root_partition)
            self.deleteFile(root_partition)  # no need to keep the partition now that we have built the full image
        else:
            self.make_rootfs_image(self.diskImagePath)
            # check that qemu-img exists before starting the potentially long-running makefs command
            qemu_img_command = self.config.qemu_bindir / "qemu-img"
            if not qemu_img_command.is_file():
                system_qemu_img = shutil.which("qemu-img")
                if system_qemu_img:
                    print("qemu-img from CHERI SDK not found, falling back to system qemu-img")
                    qemu_img_command = Path(system_qemu_img)
                else:
                    self.warning("qemu-img command was not found! Make sure to build target qemu first.")
            # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
            if not self.config.quiet and qemu_img_command.exists():
                runCmd(qemu_img_command, "info", self.diskImagePath)
            if self.useQCOW2:
                if not qemu_img_command.exists():
                    self.fatal("Cannot create QCOW2 image without qemu-img command!")
                # create a qcow2 version from the raw image:
                rawImg = self.diskImagePath.with_suffix(".raw")
                runCmd("mv", "-f", self.diskImagePath, rawImg)
                runCmd(qemu_img_command, "convert",
                       "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
                       "-O", "qcow2",  # convert to qcow2 format
                       rawImg,  # input file
                       self.diskImagePath)  # output file
                self.deleteFile(rawImg, print_verbose_only=True)
                if self.config.verbose:
                    runCmd(qemu_img_command, "info", self.diskImagePath)

    def copyFromRemoteHost(self):
        statusUpdate("Cannot build disk image on non-FreeBSD systems, will attempt to copy instead.")
        if not self.remotePath:
            self.fatal("Path to the remote disk image is not set, option '--", self.target, "/", "remote-path' must "
                       "be set to a path that scp understands (e.g. vica:/foo/bar/disk.img)", sep="")
            return
        # noinspection PyAttributeOutsideInit
        self.remotePath = os.path.expandvars(self.remotePath)
        statusUpdate("Will copy the disk-image from ", self.remotePath, sep="")
        if not self.query_yes_no("Continue?"):
            return

        self.copyRemoteFile(self.remotePath, self.diskImagePath)

    def process(self):
        if not IS_FREEBSD and self.crossBuildImage:
            with setEnv(PATH=str(self.config.outputRoot / "freebsd-cross/bin") + ":" + os.getenv("PATH")):
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
        if self.diskImagePath.is_dir():
            # Given a directory, derive the default file name inside it
            self.diskImagePath = _defaultDiskImagePath(self.config, self.diskImagePath)

        if self.diskImagePath.is_file():
            # only show prompt if we can actually input something to stdin
            if not self.config.clean:
                # with --clean always delete the image
                print("An image already exists (" + str(self.diskImagePath) + "). ", end="")
                if not self.query_yes_no("Overwrite?", default_result=True):
                    return  # we are done here
            self.deleteFile(self.diskImagePath)

        # we can only build disk images on FreeBSD, so copy the file if we aren't
        if not IS_FREEBSD and not self.crossBuildImage:
            self.copyFromRemoteHost()
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
            self.fatal("Missing makefs command! Should be found in FreeBSD build dir (or set $MAKEFS_CMD)")
        statusUpdate("Disk image will be saved to", self.diskImagePath)
        statusUpdate("Disk image root fs is", self.rootfsDir)
        statusUpdate("Extra files for the disk image will be copied from", self.extraFilesDir)

        if not self.input_METALOG.is_file():
            self.fatal("mtree manifest", self.input_METALOG, "is missing")
        if not (self.userGroupDbDir / "master.passwd").is_file():
            self.fatal("master.passwd does not exist in ", self.userGroupDbDir)

        with tempfile.TemporaryDirectory(prefix="cheribuild-" + self.target + "-") as tmp:
            self.tmpdir = Path(tmp)
            self.manifestFile = self.tmpdir / "METALOG"
            self.prepareRootfs()
            # now add all the user provided files to the image:
            # we have to make a copy as we modify self.extraFiles in self.add_file_to_image()
            for p in self.extraFiles.copy():
                pathInImage = p.relative_to(self.extraFilesDir)
                self.verbose_print("Adding user provided file /", pathInImage, " to disk image.", sep="")
                self.add_file_to_image(p, base_directory=self.extraFilesDir)

            # then walk the rootfs to see if any additional files should be added:
            if not os.getenv("_TEST_SKIP_METALOG"):
                # skip adding to the metalog in the git push hook since it takes a long time and isn't that useful
                self.add_unlisted_files_to_metalog()

            # finally create the disk image
            self.make_disk_image()
        self.tmpdir = None
        self.manifestFile = None

    def add_unlisted_files_to_metalog(self):
        unlisted_files = []
        rootfs_str = str(self.rootfsDir)  # compat with python < 3.6
        for root, dirnames, filenames in os.walk(rootfs_str):
            for filename in filenames:
                full_path = Path(root, filename)
                target_path = os.path.relpath(str(full_path), rootfs_str)
                if target_path.startswith("usr/local/") or target_path.startswith("opt/") or target_path.startswith(
                        "extra/"):
                    self.mtree.add_file(full_path, target_path, print_status=self.config.verbose)
                elif target_path not in self.mtree:
                    if target_path != "METALOG":  # METALOG is not added to METALOG
                        unlisted_files.append((full_path, target_path))
        if unlisted_files:
            print("Found the following files in the rootfs that are not listed in METALOG:")
            for i in unlisted_files:
                print("\t", i[1])
            if self.query_yes_no("Should these files also be added to the image?", default_result=True, force_result=True):
                for i in unlisted_files:
                    self.mtree.add_file(i[0], i[1], print_status=self.config.verbose)

    def generateSshHostKeys(self):
        # do the same as "ssh-keygen -A" just with a different output directory as it does not allow customizing that
        sshDir = self.extraFilesDir / "etc/ssh"
        self.makedirs(sshDir)
        # -t type Specifies the type of key to create.  The possible values are "rsa1" for protocol version 1
        #  and "dsa", "ecdsa","ed25519", or "rsa" for protocol version 2.

        for keyType in ("rsa", "dsa", "ecdsa", "ed25519"):
            # SSH1 protocol uses just /etc/ssh/ssh_host_key without the type
            privateKeyName = "ssh_host_key" if keyType == "rsa1" else "ssh_host_" + keyType + "_key"
            privateKey = sshDir / privateKeyName
            publicKey = sshDir / (privateKeyName + ".pub")
            if not privateKey.is_file():
                runCmd("ssh-keygen", "-t", keyType,
                       "-N", "",  # no passphrase
                       "-f", str(privateKey))
            self.add_file_to_image(privateKey, base_directory=self.extraFilesDir, mode="0600")
            self.add_file_to_image(publicKey, base_directory=self.extraFilesDir, mode="0644")


def _defaultDiskImagePath(config: CheriConfig, pfx, img_prefix=""):
    return pfx / (img_prefix + "cheri" + config.cheri_bits_and_abi_str + "-disk.img")


def _defaultMinimalDiskImagePath(conf, proj):
    return _defaultDiskImagePath(conf, conf.outputRoot, "minimal-")


class BuildMinimalCheriBSDDiskImage(_BuildDiskImageBase):
    project_name = "disk-image-minimal"
    dependencies = ["qemu", "cheribsd-cheri"]  # TODO: include gdb?
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_PURECAP]

    class _MinimalFileTemplates(_AdditionalFileTemplates):
        def get_fstab_template(self):
            return includeLocalFile("files/minimal-image/etc/fstab.in")

        def get_rc_conf_template(self):
            return includeLocalFile("files/minimal-image/etc/rc.conf.in")

    default_disk_image_path = ComputedDefaultValue(function=_defaultMinimalDiskImagePath,
        as_string="$OUTPUT_ROOT/minimal-cheri256-disk.img or $OUTPUT_ROOT/minimal-cheri128-disk.img depending "
                  "on --cheri-bits.")

    @classmethod
    def setup_config_options(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        defaultHostname = ComputedDefaultValue(
            function=lambda conf, unused: "qemu-cheri" + conf.cheri_bits_and_abi_str + "-" + hostUsername,
            as_string="qemu-cheri${CHERI_BITS}-" + hostUsername)

        super().setup_config_options(defaultHostname=defaultHostname, extraFilesSuffix="-minimal", **kwargs)
        cls.strip_binaries = cls.add_bool_option("strip", default=True,
                                               help="strip ELF files to reduce size of generated image")
        cls.include_cheritest = cls.add_bool_option("include-cheritest", default=True,
                                                  help="Also add cheritest/cheriabitest to the disk image")
        cls.use_cheribsd_purecap_rootfs = cls.add_bool_option("use-cheribsd-purecap-rootfs", default=False,
                                                            help="Use the rootfs built by cheribsd-purecap instead")

    def __init__(self, config: CheriConfig):
        self.cheribsd_class = BuildCHERIBSD.get_class_for_target(CompilationTargets.CHERIBSD_MIPS_PURECAP)  # type: typing.Type[BuildCHERIBSD]
        if self.use_cheribsd_purecap_rootfs:
            self.cheribsd_class = BuildCHERIBSDPurecap
        super().__init__(config, source_class=self.cheribsd_class)
        self.minimumImageSize = "20m"  # let's try to shrink the image size
        # The base input is only cheribsdbox and all the symlinks
        self.input_METALOG = self.rootfsDir / "cheribsdbox.mtree"
        self.file_templates = BuildMinimalCheriBSDDiskImage._MinimalFileTemplates()
        self.is_minimal = True

    @property
    def needs_special_pkg_repo(self):
        return False

    def process_files_list(self, files_list):
        for line in io.StringIO(files_list).readlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            assert not line.startswith("/")
            # Otherwise find the file in the rootfs
            file_path = self.rootfsDir / line  # type: Path
            if not file_path.exists():
                self.fatal("Required file", line, "missing from rootfs")
            if file_path.is_dir():
                self.mtree.add_dir(line, reference_dir=file_path, print_status=self.config.verbose)
            else:
                self.add_file_to_image(file_path, base_directory=self.rootfsDir)

    def add_unlisted_files_to_metalog(self):
        # Now add all the files from *.files to the image:
        self.verbose_print("Adding files from rootfs to minimal image:")
        files_to_add = [includeLocalFile("files/minimal-image/base.files"),
                        includeLocalFile("files/minimal-image/etc.files")]
        if (self.rootfsDir / "usr/libcheri/libc.so.7").exists():
            files_to_add.append(includeLocalFile("files/minimal-image/purecap-dynamic.files"))

        for files_list in files_to_add:
            self.process_files_list(files_list)

        if self.include_cheritest:
            for i in ("cheritest", "cheriabitest"):
                test_binary = self.rootfsDir / "bin" / i  # type: Path
                if test_binary.exists():
                    self.add_file_to_image(test_binary, base_directory=self.rootfsDir)

        # These dirs seem to be needed
        self.mtree.add_dir("var/db", print_status=self.config.verbose)
        self.mtree.add_dir("var/empty", print_status=self.config.verbose)

        self.verbose_print("Not adding unlisted files to METALOG since we are building a minimal image")

    def prepareRootfs(self):
        super().prepareRootfs()
        # Add the additional sysctl configs
        self.createFileForImage("/etc/pam.d/su", showContentsByDefault=False,
                                contents=includeLocalFile("files/minimal-image/pam.d/su"))
        # disable coredumps (since there is almost no space on the image)
        self.createFileForImage("/etc/sysctl.conf", showContentsByDefault=False,
                                contents=includeLocalFile("files/minimal-image/etc/sysctl.conf"))
        # The actual minimal startup file:
        self.createFileForImage("/etc/rc", showContentsByDefault=False,
                                contents=includeLocalFile("files/minimal-image/etc/rc"))

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
                self.deleteFile(dummy_hardlink)
                os.link(str(cheribsdbox_path), str(dummy_hardlink))
                if Path(cheribsdbox_path).stat().st_nlink < 2:
                    self.fatal("Need at least one hardlink to cheribsdbox so that makefs can detect deduplicate. "
                               "This should have been created by cheribuild but something must have gone wrong")
            print("Relocating mtree path ./bin/cheribsdbox to use", cheribsdbox_path)
            # noinspection PyProtectedMember
            for i in self.mtree._mtree.values():
                if i.attributes.get("contents", None) == "./bin/cheribsdbox":
                    i.attributes["contents"] = cheribsdbox_path

        # runCmd(["sh", "-c", "du -ah " + shlex.quote(str(self.tmpdir)) + " | sort -h"])
        if self.config.debug_output:
            self.mtree.write(sys.stderr)
        if self.config.verbose:
            runCmd("du", "-ah", self.tmpdir)
        super().make_rootfs_image(rootfs_img)


class _RISCVFileTemplates(_AdditionalFileTemplates):
    def get_fstab_template(self):
        return includeLocalFile("files/riscv/fstab.in")


class _X86FileTemplates(_AdditionalFileTemplates):
    def get_fstab_template(self):
        return includeLocalFile("files/x86/fstab.in")


class BuildMultiArchDiskImage(_BuildDiskImageBase):
    doNotAddToTargets = True
    _source_class = None  # type: typing.Type[SimpleProject]
    supported_architectures = [CompilationTargets.NATIVE] + SimpleProject.CAN_TARGET_ALL_CHERIBSD_TARGETS

    @classproperty
    def default_architecture(cls) -> CrossCompileTarget:
        return cls._source_class.default_architecture

    @classproperty
    def supported_architectures(cls):
        return cls._source_class.supported_architectures

    @staticmethod
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
        self.bigEndian = self.compiling_for_mips(include_purecap=True)
        if self.get_crosscompile_target(config).is_riscv():
            self.file_templates = _RISCVFileTemplates()
        elif self.is_x86:
            self.file_templates = _X86FileTemplates()


class BuildCheriBSDDiskImage(BuildMultiArchDiskImage):
    project_name = "disk-image"
    dependencies = ["qemu", "cheribsd-cheri", "gdb-mips"]
    _source_class = BuildCHERIBSD
    _always_add_suffixed_targets = True  # preparation for future multi-target support

    default_disk_image_path = ComputedDefaultValue(
        function=lambda conf, proj: _defaultDiskImagePath(conf, conf.outputRoot),
        as_string="$OUTPUT_ROOT/cheri256-disk.img or $OUTPUT_ROOT/cheri128-disk.img depending on --cheri-bits.")

    @classproperty
    def supported_architectures(cls):
        # FIXME:
        return [CompilationTargets.CHERIBSD_MIPS_PURECAP]
        # return cls._source_class.supported_architectures

    @classmethod
    def setup_config_options(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        defaultHostname = ComputedDefaultValue(
            function=lambda conf, unused: "qemu-cheri" + conf.cheri_bits_and_abi_str + "-" + hostUsername,
            as_string="qemu-cheri${CHERI_BITS}-" + hostUsername)

        tmpfs_shortname = None
        extra_files_shortname = None
        disk_img_shortname = None
        if cls._crossCompileTarget.is_cheri_purecap([CPUArchitecture.MIPS64]):
            tmpfs_shortname = "-disable-tmpfs"
            disk_img_shortname = "-disk-image-path"
            extra_files_shortname = "-extra-files"

        super().setup_config_options(extraFilesShortname=extra_files_shortname, defaultHostname=defaultHostname, **kwargs)
        cls.disableTMPFS = cls.add_bool_option("disable-tmpfs", shortname=tmpfs_shortname,
                                             help="Don't make /tmp a TMPFS mount in the CHERIBSD system image."
                                                  " This is a workaround in case TMPFS is not working correctly")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.minimumImageSize = "256m"  # let's try to shrink the image size
        # self.needs_special_pkg_repo = self.source_project.buildTests

    @property
    def needs_special_pkg_repo(self):
        return self.crosscompile_target.is_mips(include_purecap=True)


class BuildCheriBSDPurecapDiskImage(_BuildDiskImageBase):
    project_name = "disk-image-purecap"
    dependencies = ["qemu", "cheribsd-purecap", "gdb-mips"]
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_PURECAP]
    default_disk_image_path = ComputedDefaultValue(
        function=lambda conf, proj: _defaultDiskImagePath(conf, conf.outputRoot, "purecap-"),
        as_string="$OUTPUT_ROOT/purecap-cheri256-disk.img or $OUTPUT_ROOT/purecap-cheri128-disk.img depending on "
                  "--cheri-bits.")

    @classmethod
    def setup_config_options(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        defaultHostname = ComputedDefaultValue(
            function=lambda conf, unused: "qemu-purecap" + conf.cheri_bits_and_abi_str + "-" + hostUsername,
            as_string="qemu-purecap${CHERI_BITS}-" + hostUsername)
        super().setup_config_options(defaultHostname=defaultHostname, **kwargs)
        cls.disableTMPFS = cls.add_bool_option("disable-tmpfs",
                                             help="Don't make /tmp a TMPFS mount in the CHERIBSD system image."
                                                  " This is a workaround in case TMPFS is not working correctly")

    def __init__(self, config: CheriConfig):
        super().__init__(config, source_class=BuildCHERIBSDPurecap)
        self.minimumImageSize = "256m"  # let's try to shrink the image size
        # self.needs_special_pkg_repo = self.source_project.buildTests

    @property
    def needs_special_pkg_repo(self):
        return True


def _default_freebsd_disk_image_name(config: CheriConfig, project: SimpleProject):
    xtarget = project.get_crosscompile_target(config)
    suffix = xtarget.generic_suffix if xtarget else "<TARGET>"
    if project.compiling_for_mips(include_purecap=False):
        if config.mips_float_abi == MipsFloatAbi.HARD:
            suffix += "-hardfloat"
    return config.outputRoot / ("freebsd-" + suffix + ".img")


class BuildFreeBSDImage(BuildMultiArchDiskImage):
    target = "disk-image-freebsd"
    _source_class = BuildFreeBSD

    default_disk_image_path = ComputedDefaultValue(function=_default_freebsd_disk_image_name,
                                                   as_string="$OUTPUT_ROOT/freebsd-$SUFFIX.img")

    @classmethod
    def setup_config_options(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        suffix = cls._crossCompileTarget.generic_suffix if cls._crossCompileTarget else "<TARGET>"
        super().setup_config_options(defaultHostname="qemu-" + suffix + "-" + hostUsername, **kwargs)
        cls.disableTMPFS = cls._crossCompileTarget.is_mips()  # MALTA64 doesn't include tmpfs

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # TODO: different extra-files directory
        self.minimumImageSize = "256m"


class BuildFreeBSDWithDefaultOptionsDiskImage(BuildFreeBSDImage):
    project_name = "disk-image-freebsd-with-default-options"
    _source_class = BuildFreeBSDWithDefaultOptions
    hide_options_from_help = True

