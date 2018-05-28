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
import stat
import tempfile

from .cross.cheribsd import _BuildFreeBSD
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


class _BuildDiskImageBase(SimpleProject):
    doNotAddToTargets = True
    diskImagePath = None  # type: Path
    needs_special_pkg_repo = False  # True for CheriBSD

    @classmethod
    def setupConfigOptions(cls, *, defaultHostname, extraFilesShortname=None, **kwargs):
        super().setupConfigOptions()
        cls.extraFilesDir = cls.addPathOption("extra-files", shortname=extraFilesShortname, showHelp=True,
                                              default=lambda config, project: (config.sourceRoot / "extra-files"),
                                              help="A directory with additional files that will be added to the image "
                                                   "(default: '$SOURCE_ROOT/extra-files')", metavar="DIR")
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

    def __init__(self, config, source_class: "typing.Type[_BuildFreeBSD]"):
        super().__init__(config)
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifestFile = None  # type: Path
        self.extraFiles = []  # type: typing.List[Path]
        self._addRequiredSystemTool("ssh-keygen")
        if IS_FREEBSD:
            self._addRequiredSystemTool("makefs")
        else:
            self._addRequiredSystemTool("freebsd-makefs", cheribuild_target="freebsd-bootstrap-tools")
            self._addRequiredSystemTool("freebsd-install", cheribuild_target="freebsd-bootstrap-tools")

        self.makefs_cmd = None
        self.install_cmd = None
        self.source_project = source_class.get_instance(config)
        assert isinstance(self.source_project, _BuildFreeBSD)
        self.rootfsDir = self.source_project.installDir
        assert self.rootfsDir is not None
        self.userGroupDbDir = self.source_project.sourceDir / "etc"
        self.crossBuildImage = self.source_project.crossbuild
        self.minimumImageSize = "1g",  # minimum image size = 1GB
        self.mtree = MtreeFile()
        if self.needs_special_pkg_repo:
            self._addRequiredSystemTool("wget")  # Needed to recursively fetch the pkg repo

    def getModeString(self, path: Path):
        try:
            self.verbose_print(path, path.stat())
            result = "0{0:o}".format(stat.S_IMODE(path.stat().st_mode))  # format as octal with leading 0 prefix
        except Exception as e:
            warningMessage("Failed to stat", path, "assuming mode 0644: ", e)
            result = "0644"
        # make sure that the .ssh config files are installed with the right permissions
        if path.name == ".ssh" and result != "0700":
            warningMessage("Wrong file mode", result, "for", path, " --  it should be 0700, fixing it for image")
            return "0700"
        if path.parent.name == ".ssh" and not path.name.endswith(".pub") and result != "0600" :
            warningMessage("Wrong file mode", result, "for", path, " --  it should be 0600, fixing it for image")
        return result

    def addFileToImage(self, file: Path, *, baseDirectory: Path, user="root", group="wheel", mode=None):
        pathInTarget = file.relative_to(baseDirectory)
        assert not str(pathInTarget).startswith(".."), pathInTarget
        if not self.config.quiet:
            statusUpdate(file, " -> /", pathInTarget, sep="")
        if mode is None:
            mode = self.getModeString(file)

        # This also adds all the parent directories to METALOG
        self.mtree.add_file(file, pathInTarget, mode=mode, uname=user, gname=group)
        if file in self.extraFiles:
            self.extraFiles.remove(file)  # remove it from extraFiles so we don't install it twice

    def createFileForImage(self, outDir: Path, pathInImage: str, *, contents: str="\n", showContentsByDefault=True,
                           mode=None):
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
            targetFile = outDir / pathInImage
            baseDir = outDir
            if self.config.verbose or (showContentsByDefault and not self.config.quiet):
                print("Generating /", pathInImage, " with the following contents:\n",
                      coloured(AnsiColour.green, contents), sep="", end="")
            self.writeFile(targetFile, contents, noCommandPrint=True, overwrite=False, mode=mode)
        self.addFileToImage(targetFile, baseDirectory=baseDir)

    def _wget_fetch(self, what, where):
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

    def prepareRootfs(self, outDir: Path):
        self.manifestFile = outDir / "METALOG"
        originalMetalog = self.rootfsDir / "METALOG"
        if originalMetalog.exists():
            self.mtree.load(originalMetalog)

        # Add the files needed to install kyua (make sure to download before calculating the list of extra files!)
        if self.needs_special_pkg_repo:
            self.createFileForImage(outDir, "/etc/local-kyua-pkg/repos/kyua-pkg-cache.conf", mode=0o644,
                                    showContentsByDefault=False,
                                    contents=includeLocalFile("files/cheribsd/kyua-pkg-cache.repo.conf"))
            self.createFileForImage(outDir, "/etc/local-kyua-pkg/config/pkg.conf", mode=0o644,
                                    showContentsByDefault=False,
                                    contents=includeLocalFile("files/cheribsd/kyua-pkg-cache.options.conf"))
            # Add a script to install from these config files:
            self.createFileForImage(outDir, "/bin/prepare-testsuite.sh", mode=0o755, showContentsByDefault=False,
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

        for root, dirnames, filenames in os.walk(Path(self.rootfsDir,"usr/local")):
            for filename in filenames:
                fp = Path(root,filename)
                tp = fp.relative_to(self.rootfsDir)
                self.mtree.add_file(fp, tp)

        # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
        # Overlay extra-files over additional stuff over cheribsd rootfs dir

        fstabContents = includeLocalFile("files/cheribsd/fstab.in")

        if self.disableTMPFS:
            fstabContents = fstabContents.format_map(dict(tmpfsrem="#"))
        else:
            fstabContents = fstabContents.format_map(dict(tmpfsrem=""))

        self.createFileForImage(outDir, "/etc/fstab", contents=fstabContents)

        # enable ssh and set hostname
        # TODO: use separate file in /etc/rc.conf.d/ ?
        self.hostname = os.path.expandvars(self.hostname)   # Expand env vars in hostname to allow $CHERI_BITS
        rcConfContents = includeLocalFile("files/cheribsd/rc.conf.in").format(hostname=self.hostname)
        self.createFileForImage(outDir, "/etc/rc.conf", contents=rcConfContents)

        cshrcContents = includeLocalFile("files/cheribsd/csh.cshrc.in").format(
            SRCPATH=self.config.sourceRoot, ROOTFS_DIR=self.rootfsDir)
        self.createFileForImage(outDir, "/etc/csh.cshrc", contents=cshrcContents)

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
            self.createFileForImage(outDir, "/etc/ssh/sshd_config", contents=newSshdConfigContents,
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
                    self.createFileForImage(outDir, "/root/.ssh/authorized_keys", contents=contents, mode=0o600)
                    if self.queryYesNo("Should this authorized_keys file be used by default? "
                                       "(You can always change them by editing/deleting '" +
                                       str(authorizedKeys) + "')?", defaultResult=False):
                        self.installFile(outDir / "root/.ssh/authorized_keys", authorizedKeys)
                        runCmd("chmod", "0700", authorizedKeys.parent)
                        runCmd("chmod", "0600", authorizedKeys)

    def makeImage(self):
        # check that qemu-img exists before starting the potentially long-running makefs command
        qemuImgCommand = self.config.sdkDir / "bin/qemu-img"
        if not qemuImgCommand.is_file():
            systemQemuImg = shutil.which("qemu-img")
            if systemQemuImg:
                print("qemu-img from CHERI SDK not found, falling back to system qemu-img")
                qemuImgCommand = Path(systemQemuImg)
            else:
                fatalError("qemu-img command was not found!", fixitHint="Make sure to build target qemu first")

        # write out the manifest file:
        self.mtree.write(self.manifestFile)
        # print(self.manifestFile.read_text())

        debug_options = []
        if self.config.verbose:
            debug_options = ["-d", "0x90000"]  # trace POPULATE and WRITE_FILE events
        runCmd([self.makefs_cmd] + debug_options + [
            "-Z",  # sparse file output
            "-b", "30%",  # minimum 30% free blocks
            "-f", "30%",  # minimum 30% free inodes
            "-R", "128m",  # round up size to the next 16m multiple
            "-M", self.minimumImageSize,
            "-B", "be",  # big endian byte order
            "-N", self.userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file
            # which makes sure that the numeric UID values are correct
            self.diskImagePath,  # output file
            self.manifestFile,  # use METALOG as the manifest for the disk image
            # extra directories:
            # self.rootfsDir  # directory tree to use for the image
        ], cwd=self.rootfsDir)

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
            fatalError("Path to the remote disk image is not set, option '--", self.target, "/", "remote-path' must "
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
        self.makefs_cmd = shutil.which("freebsd-makefs")
        self.install_cmd = shutil.which("freebsd-install")
        # On FreeBSD we can use /usr/bin/makefs and /usr/bin/install
        if IS_FREEBSD:
            if not self.install_cmd:
                self.install_cmd = shutil.which("install")
            if not self.makefs_cmd:
                self.makefs_cmd = shutil.which("makefs")
        if not self.makefs_cmd or not self.install_cmd:
            fatalError("Missing freebsd-install or freebsd-makefs command!")
        statusUpdate("Disk image will saved to", self.diskImagePath)
        statusUpdate("Extra files for the disk image will be copied from", self.extraFilesDir)

        if self.diskImagePath.is_dir():
            # Given a directory, derive the default file name inside it
            self.diskImagePath = _defaultDiskImagePathFn(self.config.cheriBits, self.diskImagePath)

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

        if not (self.rootfsDir / "METALOG").is_file():
            fatalError("mtree manifest", self.rootfsDir / "METALOG", "is missing")
        if not (self.userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in ", self.userGroupDbDir)

        with tempfile.TemporaryDirectory() as outDir:
            self.prepareRootfs(Path(outDir))
            # now add all the user provided files to the image:
            # we have to make a copy as we modify self.extraFiles in self.addFileToImage()
            for p in self.extraFiles.copy():
                pathInImage = p.relative_to(self.extraFilesDir)
                self.print("Adding user provided file /", pathInImage, " to disk image.", sep="")
                self.addFileToImage(p, baseDirectory=self.extraFilesDir)
            # finally create the disk image
            self.makeImage()

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


def _defaultDiskImagePathFn(bits, pfx):
    if bits == 128:
        return pfx / "cheri128-disk.img"
    return pfx / "cheri256-disk.img"


def _defaultDiskImagePath(conf: "CheriConfig", cls):
    return _defaultDiskImagePathFn(conf.cheriBits, conf.outputRoot)


class BuildCheriBSDDiskImage(_BuildDiskImageBase):
    projectName = "disk-image"
    dependencies = ["qemu", "cheribsd", "gdb-mips"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        hostUsername = CheriConfig.get_user_name()
        defaultHostname = ComputedDefaultValue(
            function=lambda conf, unused: "qemu-cheri" + conf.cheriBitsStr + "-" + hostUsername,
            asString="qemu-cheri${CHERI_BITS}-" + hostUsername)
        super().setupConfigOptions(extraFilesShortname="-extra-files", defaultHostname=defaultHostname, **kwargs)
        defaultDiskImagePath = ComputedDefaultValue(
                function=_defaultDiskImagePath, asString="$OUTPUT_ROOT/cheri256-disk.img or "
                                                         "$OUTPUT_ROOT/cheri128-disk.img depending on --cheri-bits.")
        cls.diskImagePath = cls.addPathOption("path", shortname="-disk-image-path", default=defaultDiskImagePath,
                                              metavar="IMGPATH", help="The output path for the QEMU disk image",
                                              showHelp=True)
        cls.disableTMPFS = cls.addBoolOption("disable-tmpfs", shortname="-disable-tmpfs",
                                             help="Don't make /tmp a TMPFS mount in the CHERIBSD system image."
                                                  " This is a workaround in case TMPFS is not working correctly")

    def __init__(self, config: CheriConfig):
        super().__init__(config, source_class=BuildCHERIBSD)
        self.minimumImageSize = "256m"  # let's try to shrink the image size
        # TODO: only fetch pkg from https://people.freebsd.org/~brooks/packages/cheribsd-mips-20170403-brooks-20170609/
        # if we are building the cheribsd tests?
        # self.needs_special_pkg_repo = self.source_project.buildTests
        self.needs_special_pkg_repo = True


class _BuildFreeBSDImageBase(_BuildDiskImageBase):
    doNotAddToTargets = True
    _freebsd_suffix = None
    _freebsd_build_class = None

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


class BuildFreeBSDDiskImageMIPS(_BuildFreeBSDImageBase):
    projectName = "disk-image-freebsd-mips"
    dependencies = ["qemu", "freebsd-mips"]
    _freebsd_build_class = BuildFreeBSDForMIPS
    _freebsd_suffix = "mips"


class BuildFreeBSDDiskImageX86(_BuildFreeBSDImageBase):
    projectName = "disk-image-freebsd-x86"
    dependencies = ["qemu", "freebsd-x86"]
    _freebsd_build_class = BuildFreeBSDForX86
    _freebsd_suffix = "x86"
