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
import shlex
import stat
import io
import tempfile

from .cross.cheribsd import BuildFreeBSD
from .cross.cheribsd import *
from ..config.loader import ComputedDefaultValue
from .project import *
from ..utils import *
from ..mtree import MtreeFile

# Notes:
# Mount the filesystem of a BSD VM: guestmount -a /foo/bar.qcow2 -m /dev/sda1:/:ufstype=ufs2:ufs --ro /mnt/foo
# ufstype=ufs2 is required as the Linux kernel can't automatically determine which UFS filesystem is being used
# Same thing is possible with qemu-nbd, but needs root (might be faster)



PKG_REPO_URL = "https://people.freebsd.org/~brooks/packages/cheribsd-mips-20170403-brooks-20170609/"
# old version of libarchive needed by kyua
OLD_LIBARCHIVE_URL = "https://people.freebsd.org/~arichardson/cheri-files/libarchive.so.6"
# Bump this to redownload all the pkg files
PKG_REPO_NEEDS_UPDATE = datetime.datetime(day=20, month=5, year=2018)


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
    needs_special_pkg_repo = False  # True for CheriBSD
    strip_binaries = False  # True by default for minimal disk-image

    @classmethod
    def setupConfigOptions(cls, *, defaultHostname, extraFilesShortname=None, extraFilesSuffix="", **kwargs):
        super().setupConfigOptions()
        cls.extraFilesDir = cls.addPathOption("extra-files",
            shortname=extraFilesShortname, showHelp=True,
            default=lambda config, project: (config.sourceRoot / ("extra-files" + extraFilesSuffix)),
            help="A directory with additional files that will be added to the image (default: "
                 "'$SOURCE_ROOT/extra-files" + extraFilesSuffix + "')", metavar="DIR")
        cls.hostname = cls.addConfigOption("hostname", showHelp=True, default=defaultHostname, metavar="HOSTNAME",
                                           help="The hostname to use for the QEMU image")
        cls.useQCOW2 = cls.addBoolOption("use-qcow2", help="Convert the disk image to QCOW2 format instead of raw")
        if not IS_FREEBSD:
            cls.remotePath = cls.addConfigOption("remote-path", showHelp=True, metavar="PATH", help="The path on the "
                                                 "remote FreeBSD machine from where to copy the disk image")
        cls.wget_via_tmp = cls.addBoolOption("wget-via-tmp",
                                help="Use a directory in /tmp for recursive wget operations;"
                                      "of interest in rare cases, like extra-files on smbfs.")
        cls.disableTMPFS = None

    def __init__(self, config, source_class: "typing.Type[BuildFreeBSD]"):
        super().__init__(config)
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifestFile = None  # type: Path
        self.extraFiles = []  # type: typing.List[Path]
        self._addRequiredSystemTool("ssh-keygen")

        self.makefs_cmd = None
        self.install_cmd = None
        self.source_project = source_class.get_instance(self, self.config)
        if IS_FREEBSD:
            self._addRequiredSystemTool("makefs")
        elif self.source_project.crossbuild:
            self._addRequiredSystemTool("freebsd-makefs", cheribuild_target="freebsd-bootstrap-tools")
            self._addRequiredSystemTool("freebsd-install", cheribuild_target="freebsd-bootstrap-tools")
        assert isinstance(self.source_project, BuildFreeBSD)
        self.rootfsDir = self.source_project.installDir
        assert self.rootfsDir is not None
        self.userGroupDbDir = self.source_project.sourceDir / "etc"
        self.crossBuildImage = self.source_project.crossbuild
        self.minimumImageSize = "1g",  # minimum image size = 1GB
        self.mtree = MtreeFile()
        self.input_METALOG = self.rootfsDir / "METALOG"
        self.input_METALOG_required = True
        # used during process to generated files
        self.tmpdir = None  # type: Path
        self.file_templates = _AdditionalFileTemplates()
        if self.needs_special_pkg_repo:
            self._addRequiredSystemTool("wget")  # Needed to recursively fetch the pkg repo

    def addFileToImage(self, file: Path, *, baseDirectory: Path, user="root", group="wheel", mode=None):
        pathInTarget = file.relative_to(baseDirectory)
        assert not str(pathInTarget).startswith(".."), pathInTarget

        if self.strip_binaries and file.exists():
            # Try to shrink the size by stripping all elf binaries
            with file.open("rb") as f:
                if f.read(4) == b"\x7fELF":
                    self.verbose_print("Stripping ELF binary", file)
                    stripped_path = self.tmpdir / pathInTarget
                    self.makedirs(stripped_path.parent)
                    runCmd(self.config.sdkBinDir / "llvm-strip", file, "-o", stripped_path)
                    # runCmd("file", stripped_path)
                    file = stripped_path

        if not self.config.quiet:
            statusUpdate(file, " -> /", pathInTarget, sep="")

        # This also adds all the parent directories to METALOG
        self.mtree.add_file(file, pathInTarget, mode=mode, uname=user, gname=group, print_status=self.config.verbose)
        if file in self.extraFiles:
            self.extraFiles.remove(file)  # remove it from extraFiles so we don't install it twice

    def createFileForImage(self, pathInImage: str, *, contents: str="\n", showContentsByDefault=True, mode=None):
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
        self.addFileToImage(targetFile, baseDirectory=baseDir)

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
            with tempfile.TemporaryDirectory(prefix="cheribuild-wget-", dir="/tmp") as td:
                # Speed things up by using whatever we've got locally, too
                runCmd("rsync", "-avvP", str(where) + "/.", td + "/.")
                self._wget_fetch(what, td)
                runCmd("rsync", "-avvP", "--no-times", "--delete", td + "/.", str(where) + "/.")
        else:
            self._wget_fetch(what, where)

    def prepareRootfs(self):
        assert self.tmpdir is not None
        assert self.manifestFile is not None
        if self.input_METALOG.exists():
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
            # Add a script to install from these config files:
            self.createFileForImage("/bin/prepare-testsuite.sh", mode=0o755, showContentsByDefault=False,
                                    contents=includeLocalFile("files/cheribsd/prepare-testsuite.sh"))
            # Download all the kyua pkg files from and put them in /var/db/kyua-pkg-cache
            # But only do that if we really need to update (since the recursive wget is slow)

            download_time_path = (self.extraFilesDir / "var/db/kyua-pkg-cache/.downloaded_time")
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
                pkgcache_dir = self.extraFilesDir / "var/db/kyua-pkg-cache"
                self.makedirs(pkgcache_dir)
                self.makedirs(pkgcache_dir)
                runCmd("find", pkgcache_dir)
                self._wget_fetch_dir(["--accept", "*.txz", # only download .txz files
                                PKG_REPO_URL], pkgcache_dir)
                # fetch old libarchive which is currently needed
                self.makedirs(self.extraFilesDir / "usr/lib")
                self._wget_fetch([OLD_LIBARCHIVE_URL], self.extraFilesDir / "usr/lib")
                self.writeFile(download_time_path, str(datetime.datetime.utcnow().timestamp()), overwrite=True)

        # we need to add /etc/fstab and /etc/rc.conf as well as the SSH host keys to the disk-image
        # If they do not exist in the extra-files directory yet we generate a default one and use that
        # Additionally all other files in the extra-files directory will be added to the disk image
        if self.extraFilesDir.exists():
            for root, dirnames, filenames in os.walk(str(self.extraFilesDir)):
                if '.svn' in dirnames:
                    dirnames.remove('.svn')
                if '.git' in dirnames:
                    dirnames.remove('.git')
                for filename in filenames:
                    self.extraFiles.append(Path(root, filename))

        # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
        # Overlay extra-files over additional stuff over cheribsd rootfs dir

        fstabContents = self.file_templates.get_fstab_template()

        if self.disableTMPFS:
            fstabContents = fstabContents.format_map(dict(tmpfsrem="#"))
        else:
            fstabContents = fstabContents.format_map(dict(tmpfsrem=""))

        self.createFileForImage("/etc/fstab", contents=fstabContents)

        # enable ssh and set hostname
        # TODO: use separate file in /etc/rc.conf.d/ ?
        self.hostname = os.path.expandvars(self.hostname)   # Expand env vars in hostname to allow $CHERI_BITS
        rcConfContents = self.file_templates.get_rc_conf_template().format(hostname=self.hostname)
        self.createFileForImage("/etc/rc.conf", contents=rcConfContents)

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
                if self.queryYesNo("Should they be added to /root/.ssh/authorized_keys?", defaultResult=True):
                    contents = ""
                    for pubkey in sshKeys:
                        contents += self.readFile(pubkey)
                    self.createFileForImage("/root/.ssh/authorized_keys", contents=contents, mode=0o600)
                    if self.queryYesNo("Should this authorized_keys file be used by default? "
                                       "(You can always change them by editing/deleting '" +
                                       str(authorizedKeys) + "')?", defaultResult=False):
                        self.installFile(self.tmpdir / "root/.ssh/authorized_keys", authorizedKeys)
                        runCmd("chmod", "0700", authorizedKeys.parent)
                        runCmd("chmod", "0600", authorizedKeys)

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
            self.addFileToImage(entropy_file, baseDirectory=self.tmpdir)

    def makeImage(self):
        # check that qemu-img exists before starting the potentially long-running makefs command
        qemuImgCommand = self.config.sdkDir / "bin/qemu-img"
        if not qemuImgCommand.is_file():
            systemQemuImg = shutil.which("qemu-img")
            if systemQemuImg:
                print("qemu-img from CHERI SDK not found, falling back to system qemu-img")
                qemuImgCommand = Path(systemQemuImg)
            else:
                self.fatal("qemu-img command was not found!", fixitHint="Make sure to build target qemu first")

        # write out the manifest file:
        self.mtree.write(self.manifestFile)
        # print(self.manifestFile.read_text())
        debug_options = []
        if self.config.verbose:
            debug_options = ["-d", "0x90000"]  # trace POPULATE and WRITE_FILE events
        try:
            runCmd([self.makefs_cmd] + debug_options + [
                "-Z",  # sparse file output
                "-b", "30%",  # minimum 30% free blocks
                "-f", "30%",  # minimum 30% free inodes
                "-R", "4m",  # round up size to the next 1m multiple
                "-M", self.minimumImageSize,
                "-B", "be",  # big endian byte order
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
            self.queryYesNo("About to delete the temporary directory. Copy any files you need before pressing enter.",
                            yesNoStr="")
            raise

        # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
        if not self.config.quiet:
            runCmd(qemuImgCommand, "info", self.diskImagePath)
        if self.useQCOW2:
            # create a qcow2 version from the raw image:
            rawImg = self.diskImagePath.with_suffix(".raw")
            runCmd("mv", "-f", self.diskImagePath, rawImg)
            runCmd(qemuImgCommand, "convert",
                   "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
                   "-O", "qcow2",  # convert to qcow2 format
                   rawImg,  # input file
                   self.diskImagePath)  # output file
            self.deleteFile(rawImg, printVerboseOnly=True)
            if self.config.verbose:
                runCmd(qemuImgCommand, "info", self.diskImagePath)

    def copyFromRemoteHost(self):
        statusUpdate("Cannot build disk image on non-FreeBSD systems, will attempt to copy instead.")
        if not self.remotePath:
            self.fatal("Path to the remote disk image is not set, option '--", self.target, "/", "remote-path' must "
                       "be set to a path that scp understands (e.g. vica:/foo/bar/disk.img)", sep="")
            return
        # noinspection PyAttributeOutsideInit
        self.remotePath = os.path.expandvars(self.remotePath)
        statusUpdate("Will copy the disk-image from ", self.remotePath, sep="")
        if not self.queryYesNo("Continue?"):
            return

        self.copyRemoteFile(self.remotePath, self.diskImagePath)

    def process(self):
        if not IS_FREEBSD and self.crossBuildImage:
            with setEnv(PATH=str(self.config.outputRoot / "freebsd-cross/bin") + ":" + os.getenv("PATH")):
                self.__process()
        else:
            self.__process()

    def __process(self):
        if self.diskImagePath.is_dir():
            # Given a directory, derive the default file name inside it
            self.diskImagePath = _defaultDiskImagePath(self.config.cheriBits, self.diskImagePath)

        if self.diskImagePath.is_file():
            # only show prompt if we can actually input something to stdin
            if not self.config.clean:
                # with --clean always delete the image
                print("An image already exists (" + str(self.diskImagePath) + "). ", end="")
                if not self.queryYesNo("Overwrite?", defaultResult=True):
                    return  # we are done here
            self.deleteFile(self.diskImagePath)

        # we can only build disk images on FreeBSD, so copy the file if we aren't
        if not IS_FREEBSD and not self.crossBuildImage:
            self.copyFromRemoteHost()
            return

        self.makefs_cmd = shutil.which("freebsd-makefs")
        self.install_cmd = shutil.which("freebsd-install")
        # On FreeBSD we can use /usr/bin/makefs and /usr/bin/install
        if IS_FREEBSD:
            if not self.install_cmd:
                self.install_cmd = shutil.which("install")
            if not self.makefs_cmd:
                self.makefs_cmd = shutil.which("makefs")
        if not self.makefs_cmd or not self.install_cmd:
            self.fatal("Missing freebsd-install or freebsd-makefs command!")
        statusUpdate("Disk image will saved to", self.diskImagePath)
        statusUpdate("Extra files for the disk image will be copied from", self.extraFilesDir)

        if not self.input_METALOG.is_file():
            self.fatal("mtree manifest", self.input_METALOG, "is missing")
        if not (self.userGroupDbDir / "master.passwd").is_file():
            self.fatal("master.passwd does not exist in ", self.userGroupDbDir)

        with tempfile.TemporaryDirectory() as tmp:
            self.tmpdir = Path(tmp)
            self.manifestFile = self.tmpdir / "METALOG"
            self.prepareRootfs()
            # now add all the user provided files to the image:
            # we have to make a copy as we modify self.extraFiles in self.addFileToImage()
            for p in self.extraFiles.copy():
                pathInImage = p.relative_to(self.extraFilesDir)
                self.print("Adding user provided file /", pathInImage, " to disk image.", sep="")
                self.addFileToImage(p, baseDirectory=self.extraFilesDir)

            # then walk the rootfs to see if any additional files should be added:
            self.add_unlisted_files_to_metalog()

            # finally create the disk image
            self.makeImage()
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
            if self.queryYesNo("Should these files also be added to the image?", defaultResult=True, forceResult=True):
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
            self.addFileToImage(privateKey, baseDirectory=self.extraFilesDir, mode="0600")
            self.addFileToImage(publicKey, baseDirectory=self.extraFilesDir, mode="0644")


def _defaultDiskImagePath(bits, pfx, img_prefix=""):
    if bits == 128:
        return pfx / (img_prefix + "cheri128-disk.img")
    return pfx / (img_prefix + "cheri256-disk.img")


class BuildMinimalCheriBSDDiskImage(_BuildDiskImageBase):
    projectName = "disk-image-minimal"
    dependencies = ["qemu", "cheribsd-cheri"]  # TODO: include gdb?

    class _MinimalFileTemplates(_AdditionalFileTemplates):
        def get_fstab_template(self):
            return includeLocalFile("files/minimal-image/etc/fstab.in")

        def get_rc_conf_template(self):
            return includeLocalFile("files/minimal-image/etc/rc.conf.in")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        defaultHostname = ComputedDefaultValue(
            function=lambda conf, unused: "qemu-cheri" + conf.cheriBitsStr + "-" + hostUsername,
            asString="qemu-cheri${CHERI_BITS}-" + hostUsername)

        def _defaultMinimalDiskImagePath(conf, proj):
            return _defaultDiskImagePath(conf.cheriBits, conf.outputRoot, "minimal-")

        super().setupConfigOptions(defaultHostname=defaultHostname, extraFilesSuffix="-minimal", **kwargs)
        cls.diskImagePath = cls.addPathOption("path", default=ComputedDefaultValue(
            function=_defaultMinimalDiskImagePath, asString="$OUTPUT_ROOT/minimal-cheri256-disk.img or "
                                                            "$OUTPUT_ROOT/minimal-cheri128-disk.img depending on --cheri-bits."),
                                              metavar="IMGPATH", help="The output path for the QEMU disk image",
                                              showHelp=True)
        cls.strip_binaries = cls.addBoolOption("strip", default=True,
                                               help="strip ELF files to reduce size of generated image")
        cls.include_cheritest = cls.addBoolOption("include-cheritest", default=True,
                                                  help="Also add cheritest/cheriabitest to the disk image")

    def __init__(self, config: CheriConfig):
        super().__init__(config, source_class=BuildCHERIBSD)
        self.minimumImageSize = "20m"  # let's try to shrink the image size
        # The base input is only cheribsdbox and all the symlinks
        self.input_METALOG = self.rootfsDir / "cheribsdbox.mtree"
        self.file_templates = BuildMinimalCheriBSDDiskImage._MinimalFileTemplates()
        self.needs_special_pkg_repo = False

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
                self.addFileToImage(file_path, baseDirectory=self.rootfsDir)

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
                    self.addFileToImage(test_binary, baseDirectory=self.rootfsDir)
        # currently mount_smbfs cannot be used as part of cheribsdbox since
        # the kernel only supports the purecap version
        for i in ("usr/sbin/mount_smbfs", "usr/libcheri/libkiconv.so.4", "usr/libcheri/libsmb.so.4"):
            if (self.rootfsDir / i).exists():
                self.addFileToImage(self.rootfsDir / i, baseDirectory=self.rootfsDir)

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


    def makeImage(self):
        # update cheribsdbox link in case we stripped it:
        cheribsdbox_entry = self.mtree._mtree.get("./bin/cheribsdbox")
        if not cheribsdbox_entry:
            self.fatal("Could not find cheribsdbox entry in mtree file!")
        else:
            cheribsdbox_path = cheribsdbox_entry.attributes["contents"]
            # create at least one hardlink to cheribsdbox so that mtree can detect that the files are all the same
            dummy_hardlink = Path(cheribsdbox_path).with_suffix(".dummy_hardlink")
            if not self.config.pretend:
                self.deleteFile(dummy_hardlink)
                os.link(cheribsdbox_path, dummy_hardlink)
                if Path(cheribsdbox_path).stat().st_nlink < 2:
                    self.fatal("Need at least one hardlink to cheribsdbox so that makefs can detect deduplicate. "
                               "This should have been created by cheribuild but something must have gone wrong")
            print("Relocating mtree path ./bin/cheribsdbox to use", cheribsdbox_path)
            for i in self.mtree._mtree.values():
                if i.attributes.get("contents", None) == "./bin/cheribsdbox":
                    i.attributes["contents"] = cheribsdbox_path

        # runCmd(["sh", "-c", "du -ah " + shlex.quote(str(self.tmpdir)) + " | sort -h"])
        if self.config.verbose:
            self.mtree.write(sys.stderr)
            runCmd("du", "-ah", self.tmpdir)
        super().makeImage()


class BuildCheriBSDDiskImage(_BuildDiskImageBase):
    projectName = "disk-image"
    dependencies = ["qemu", "cheribsd-cheri", "gdb-mips"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        defaultHostname = ComputedDefaultValue(
            function=lambda conf, unused: "qemu-cheri" + conf.cheriBitsStr + "-" + hostUsername,
            asString="qemu-cheri${CHERI_BITS}-" + hostUsername)
        super().setupConfigOptions(extraFilesShortname="-extra-files", defaultHostname=defaultHostname, **kwargs)

        defaultDiskImagePath = ComputedDefaultValue(
            function=lambda conf, proj: _defaultDiskImagePath(conf.cheriBits, conf.outputRoot),
            asString="$OUTPUT_ROOT/cheri256-disk.img or $OUTPUT_ROOT/cheri128-disk.img depending on --cheri-bits.")
        cls.diskImagePath = cls.addPathOption("path", shortname="-disk-image-path", default=defaultDiskImagePath,
                                              metavar="IMGPATH", help="The output path for the QEMU disk image",
                                              showHelp=True)
        cls.disableTMPFS = cls.addBoolOption("disable-tmpfs", shortname="-disable-tmpfs",
                                             help="Don't make /tmp a TMPFS mount in the CHERIBSD system image."
                                                  " This is a workaround in case TMPFS is not working correctly")

    def __init__(self, config: CheriConfig):
        super().__init__(config, source_class=BuildCHERIBSD.get_class_for_target(CrossCompileTarget.CHERI))
        self.minimumImageSize = "256m"  # let's try to shrink the image size
        # self.needs_special_pkg_repo = self.source_project.buildTests
        self.needs_special_pkg_repo = True


class BuildCheriBSDPurecapDiskImage(_BuildDiskImageBase):
    projectName = "disk-image-purecap"
    dependencies = ["qemu", "cheribsd-purecap", "gdb-mips"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        defaultHostname = ComputedDefaultValue(
            function=lambda conf, unused: "qemu-purecap" + conf.cheriBitsStr + "-" + hostUsername,
            asString="qemu-purecap${CHERI_BITS}-" + hostUsername)
        super().setupConfigOptions(defaultHostname=defaultHostname, **kwargs)

        defaultDiskImagePath = ComputedDefaultValue(
            function=lambda conf, proj: _defaultDiskImagePath(conf.cheriBits, conf.outputRoot, "purecap-"),
            asString="$OUTPUT_ROOT/purecap-cheri256-disk.img or $OUTPUT_ROOT/purecap-cheri128-disk.img depending on --cheri-bits.")
        cls.diskImagePath = cls.addPathOption("path", default=defaultDiskImagePath,
                                              metavar="IMGPATH", help="The output path for the QEMU disk image",
                                              showHelp=True)
        cls.disableTMPFS = cls.addBoolOption("disable-tmpfs",
                                             help="Don't make /tmp a TMPFS mount in the CHERIBSD system image."
                                                  " This is a workaround in case TMPFS is not working correctly")

    def __init__(self, config: CheriConfig):
        super().__init__(config, source_class=BuildCHERIBSDPurecap)
        self.minimumImageSize = "256m"  # let's try to shrink the image size
        # self.needs_special_pkg_repo = self.source_project.buildTests
        self.needs_special_pkg_repo = True


class BuildFreeBSDImageBase(_BuildDiskImageBase):
    doNotAddToTargets = True
    _freebsd_suffix = None

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        super().setupConfigOptions(defaultHostname="qemu-" + cls._freebsd_suffix + "-" + hostUsername, **kwargs)
        defaultDiskImagePath = ComputedDefaultValue(
                function=lambda config, project: config.outputRoot / ("freebsd-" + cls._freebsd_suffix + ".img"),
                asString="$OUTPUT_ROOT/freebsd-" + cls._freebsd_suffix + " .img")
        cls.diskImagePath = cls.addPathOption("path", default=defaultDiskImagePath, showHelp=True,
                                              metavar="IMGPATH", help="The output path for the QEMU disk image")
        cls.disableTMPFS = cls._freebsd_suffix == "mips"  # MALTA64 doesn't include tmpfs

    def __init__(self, config: CheriConfig):
        # TODO: different extra-files directory
        super().__init__(config, source_class=self._freebsd_build_class)
        self.minimumImageSize = "256m"


class BuildFreeBSDDiskImageMIPS(BuildFreeBSDImageBase):
    projectName = "disk-image-freebsd-mips"
    dependencies = ["qemu", "freebsd-mips"]
    _freebsd_build_class = BuildFreeBSD.get_class_for_target(CrossCompileTarget.MIPS)
    _freebsd_suffix = "mips"
    hide_options_from_help = True


class BuildFreeBSDDiskImageX86(BuildFreeBSDImageBase):
    projectName = "disk-image-freebsd-x86"
    dependencies = ["qemu", "freebsd-native"]
    _freebsd_build_class = BuildFreeBSD.get_class_for_target(CrossCompileTarget.NATIVE)
    _freebsd_suffix = "x86"
    hide_options_from_help = True
