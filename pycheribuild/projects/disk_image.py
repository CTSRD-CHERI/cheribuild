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
import os
import pwd
import shlex
import shutil
import stat
import tempfile
from pathlib import Path

from .cheribsd import BuildCHERIBSD, BuildFreeBSD
from ..config.loader import ComputedDefaultValue
from ..project import *
from ..utils import *


# Notes:
# Mount the filesystem of a BSD VM: guestmount -a /foo/bar.qcow2 -m /dev/sda1:/:ufstype=ufs2:ufs --ro /mnt/foo
# ufstype=ufs2 is required as the Linux kernel can't automatically determine which UFS filesystem is being used
# Same thing is possible with qemu-nbd, but needs root (might be faster)


class MtreeEntry(object):
    def __init__(self, path: Path, attributes: "typing.Dict[str, str]"):
        self.path = path
        self.attributes = attributes

    @classmethod
    def parse(cls, line: str) -> "MtreeEntry":
        elements = shlex.split(line)
        path = elements[0]
        attrDict = dict(map(lambda s: s.split(sep="=", maxsplit=1), elements[1:]))
        return MtreeEntry(path, attrDict)

    @classmethod
    def parseAllDirsInMtree(cls, mtreeFile: Path) -> "typing.List[MtreeEntry]":
        with mtreeFile.open("r", encoding="utf-8") as f:
            result = []
            for line in f.readlines():
                if " type=dir" in line:
                    try:
                        result.append(MtreeEntry.parse(line))
                    except:
                        warningMessage("Could not parse line", line, "in mtree file", mtreeFile)
            return result


class BuildDiskImageBase(SimpleProject):
    doNotAddToTargets = True
    diskImagePath = None  # type: Path

    @classmethod
    def setupConfigOptions(cls, *, defaultHostname, extraFilesShortname=None, **kwargs):
        super().setupConfigOptions()
        cls.extraFilesDir = cls.addPathOption("extra-files", shortname=extraFilesShortname, showHelp=True,
                                              default=lambda config, project: (config.sourceRoot / "extra-files"),
                                              help="A directory with additional files that will be added to the image "
                                                   "(default: '$SOURCE_ROOT/extra-files')", metavar="DIR")
        cls.hostname = cls.addConfigOption("hostname", showHelp=True, default=defaultHostname, metavar="HOSTNAME",
                                           help="The hostname to use for the QEMU image")
        if not IS_FREEBSD:
            cls.remotePath = cls.addConfigOption("remote-path", showHelp=True, metavar="PATH", help="The path on the "
                                                 "remote FreeBSD machine from where to copy the disk image")
        cls.disableTMPFS = None

    def __init__(self, config, sourceClass: type(BuildFreeBSD)):
        super().__init__(config)
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifestFile = None  # type: Path
        self.extraFiles = []  # type: typing.List[Path]
        if IS_FREEBSD:
            self._addRequiredSystemTool("ssh-keygen")
            self._addRequiredSystemTool("makefs")
        self.dirsAddedToManifest = [Path(".")]  # Path().parents always includes a "." entry
        self.rootfsDir = sourceClass.rootfsDir(self.config)
        self.userGroupDbDir = sourceClass.getSourceDir(self.config) / "etc"
        self.minimumImageSize = "1g",  # minimum image size = 1GB
        self.dirsInMtree = []

    @staticmethod
    def getModeString(path: Path):
        try:
            # print(path, path.stat())
            return "0{0:o}".format(stat.S_IMODE(path.stat().st_mode))  # format as octal with leading 0 prefix
        except Exception as e:
            warningMessage("Failed to stat", path, "assuming mode 0644: ", e)
            return "0644"

    def addFileToImage(self, file: Path, *, baseDirectory: Path, user="root", group="wheel", mode=None):
        pathInTarget = file.relative_to(baseDirectory)
        assert not str(pathInTarget).startswith(".."), pathInTarget
        statusUpdate(file, " -> /", pathInTarget, sep="")
        if mode is None:
            mode = self.getModeString(file)
        # e.g. "install -N /home/alr48/cheri/cheribsd/etc -U -M /home/alr48/cheri/output/rootfs//METALOG
        # -D /home/alr48/cheri/output/rootfs -o root -g wheel -m 444 alarm.3.gz
        # /home/alr48/cheri/output/rootfs/usr/share/man/man3/"
        commonArgs = [
            "-N", str(self.userGroupDbDir),  # Use a custom user/group database text file
            "-U",  # Indicate that install is running unprivileged (do not change uid/gid)
            "-M", str(self.manifestFile),  # the mtree manifest to write the entry to
            "-D", str(self.rootfsDir),  # DESTDIR (will be stripped from the start of the mtree file
            "-o", user, "-g", group,  # uid and gid
        ]
        # install -d: Create directories. Missing parent directories are created as required.
        # If we only create the parent directory if it doesn't exist yet we might break the build if rootfs wasn't
        # cleaned before running disk-image. We get errors like this:
        #   makefs: ./root/.ssh: missing directory in specification
        #   makefs: failed at line 27169 of the specification

        # Add all the parent directories to METALOG
        # we have to reverse the Path().parents as we need to add usr before usr/share
        # also remove the last entry from parents as that is always Path(".")

        # remove the last entry (.) from parents
        dirsToCheck = list(pathInTarget.parents)[:-1]
        # print("dirs to check:", list(dirsToCheck))
        for parent in reversed(dirsToCheck):
            if parent in self.dirsAddedToManifest:
                # print("Dir", parent, "is has already been added")
                continue
            nameInMtree = "./" + str(parent)
            if any(entry.path == nameInMtree for entry in self.dirsInMtree):
                # print("Not adding mtree entry for /" + str(parent), ", it is already in original METALOG")
                self.dirsAddedToManifest.append(parent)
                continue
            # print("Adding dir", str(baseDirectory / parent))
            runCmd(["install", "-d"] + commonArgs + ["-m", self.getModeString(baseDirectory / parent),
                                                     str(self.rootfsDir / parent)], printVerboseOnly=True)
            self.dirsAddedToManifest.append(parent)

        # need to pass target file and destination dir so that METALOG can be filled correctly
        parentDir = self.rootfsDir / pathInTarget.parent
        runCmd(["install"] + commonArgs + ["-m", mode, str(file), str(parentDir)], printVerboseOnly=True)
        if file in self.extraFiles:
            self.extraFiles.remove(file)  # remove it from extraFiles so we don't install it twice

    def createFileForImage(self, outDir: Path, pathInImage: str, *, contents: str="\n", showContentsByDefault=True):
        if pathInImage.startswith("/"):
            pathInImage = pathInImage[1:]
        assert not pathInImage.startswith("/")
        userProvided = self.extraFilesDir / pathInImage
        if userProvided.is_file():
            print("Using user provided /", pathInImage, " instead of generating default", sep="")
            self.extraFiles.remove(userProvided)
            targetFile = userProvided
        else:
            assert userProvided not in self.extraFiles
            targetFile = outDir / pathInImage
            if self.config.verbose or (showContentsByDefault and not self.config.quiet):
                print("Generating /", pathInImage, " with the following contents:\n",
                      coloured(AnsiColour.green, contents), sep="", end="")
            self.writeFile(targetFile, contents, noCommandPrint=True, overwrite=False)
        self.addFileToImage(targetFile, baseDirectory=outDir)

    def prepareRootfs(self, outDir: Path):
        self.manifestFile = outDir / "METALOG"
        originalMetalog = self.rootfsDir / "METALOG"
        self.installFile(originalMetalog, self.manifestFile)
        self.dirsInMtree = MtreeEntry.parseAllDirsInMtree(originalMetalog) if originalMetalog.exists() else []

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

        if self.disableTMPFS:
            self.createFileForImage(outDir, "/etc/fstab", contents="/dev/ada0 / ufs rw,noatime,async 1 1\n")
        else:
            self.createFileForImage(outDir, "/etc/fstab", contents="/dev/ada0 / ufs rw,noatime,async 1 1\n"
                                                                   "tmpfs /tmp tmpfs rw 0 0\n")
        # enable ssh and set hostname
        # TODO: use separate file in /etc/rc.conf.d/ ?
        rcConfContents = """hostname="{hostname}"
ifconfig_le0="DHCP"  # use DHCP on the standard QEMU usermode nic
background_dhclient="YES"  # launch dhclient in the background (hope it doesn't break sshd)
defaultroute_delay=10  # 30 seconds default is a long time
sshd_enable="YES"
sendmail_enable="NONE"  # completely disable sendmail
# disable cron, as this removes errors like: cron[600]: _secure_path: cannot stat /etc/login.conf: Permission denied
# it should also speed up boot a bit
cron_enable="NO"
# devd should also be safe to disable to increase boot speed... Or not ... seems like it breaks network
# devd_enable="NO"
nfs_client_enable="YES"
""".format(hostname=self.hostname)
        self.createFileForImage(outDir, "/etc/rc.conf", contents=rcConfContents)

        # make sure that the disk image always has the same SSH host keys
        # If they don't exist the system will generate one on first boot and we have to accept them every time
        self.generateSshHostKeys()

        print("Adding 'PermitRootLogin without-password\nUseDNS no' to /etc/ssh/sshd_config")
        # make sure we can login as root with pubkey auth:
        sshdConfig = self.rootfsDir / "etc/ssh/sshd_config"
        newSshdConfigContents = self.readFile(sshdConfig)
        newSshdConfigContents += "\n# Allow root login with pubkey auth:\nPermitRootLogin without-password\n"
        newSshdConfigContents += "\n# Major speedup to SSH performance:\n UseDNS no\n"
        self.createFileForImage(outDir, "/etc/ssh/sshd_config", contents=newSshdConfigContents,
                                showContentsByDefault=False)

        # now try adding the right ~/.ssh/authorized_keys
        authorizedKeys = self.extraFilesDir / "root/.ssh/authorized_keys"
        if not authorizedKeys.is_file():
            sshKeys = list(Path(os.path.expanduser("~/.ssh/")).glob("id_*.pub"))
            if len(sshKeys) > 0:
                print("Found the following ssh keys:", list(map(str, sshKeys)))
                if self.queryYesNo("Should they be added to /root/.ssh/authorized_keys?", defaultResult=True):
                    contents = ""
                    for pubkey in sshKeys:
                        contents += self.readFile(pubkey)
                    self.createFileForImage(outDir, "/root/.ssh/authorized_keys", contents=contents)
                    if self.queryYesNo("Should this authorized_keys file be used by default? "
                                       "(You can always change them by editing/deleting '" +
                                       str(authorizedKeys) + "')?", defaultResult=False):
                        self.installFile(outDir / "root/.ssh/authorized_keys", authorizedKeys)

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

        rawDiskImage = self.diskImagePath.with_suffix(".img")
        runCmd([
            "makefs",
            "-b", "30%",  # minimum 30% free blocks
            "-f", "30%",  # minimum 30% free inodes
            "-R", "128m",  # round up size to the next 16m multiple
            "-M", self.minimumImageSize,
            "-B", "be",  # big endian byte order
            "-F", self.manifestFile,  # use METALOG as the manifest for the disk image
            "-N", self.userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file
            # which makes sure that the numeric UID values are correct
            rawDiskImage,  # output file
            self.rootfsDir  # directory tree to use for the image
        ])
        # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
        if self.config.verbose:
            runCmd(qemuImgCommand, "info", rawDiskImage)
        self.deleteFile(self.diskImagePath, printVerboseOnly=True)
        # create a qcow2 version from the raw image:
        runCmd(qemuImgCommand, "convert",
               "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
               "-O", "qcow2",  # convert to qcow2 format
               rawDiskImage,  # input file
               self.diskImagePath)  # output file
        if self.config.verbose:
            runCmd(qemuImgCommand, "info", self.diskImagePath)

    def copyFromRemoteHost(self):
        statusUpdate("Cannot build disk image on non-FreeBSD systems, will attempt to copy instead.")
        if not self.remotePath:
            fatalError("Path to the remote disk image is not set, option '--", self.target, "/", "remote-path' must "
                       "be set to a path that scp understands (e.g. vica:/foo/bar/disk.qcow2)", sep="")
            return
        statusUpdate("Will copy the disk-image from ", self.remotePath, sep="")
        if not self.queryYesNo("Continue?"):
            return

        self.copyRemoteFile(self.remotePath, self.diskImagePath)

    def process(self):
        if str(self.diskImagePath).endswith(".img"):
            self.diskImagePath = self.diskImagePath.with_suffix(".qcow2")

        statusUpdate("Disk image will saved to", self.diskImagePath)
        statusUpdate("Extra files for the disk image will be copied from", self.extraFilesDir)

        if self.diskImagePath.is_file():
            # only show prompt if we can actually input something to stdin
            if not self.config.clean:
                # with --clean always delete the image
                print("An image already exists (" + str(self.diskImagePath) + "). ", end="")
                if not self.queryYesNo("Overwrite?", defaultResult=True):
                    return  # we are done here
            self.deleteFile(self.diskImagePath)

        # we can only build disk images on FreeBSD, so copy the file if we aren't
        if not IS_FREEBSD:
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
                print("Adding user provided file /", pathInImage, " to disk image.", sep="")
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


def _defaultDiskImagePath(conf: "CheriConfig", cls):
    if conf.cheriBits == 128:
        return conf.outputRoot / "cheri128-disk.qcow2"
    return conf.outputRoot / "cheri256-disk.qcow2"


class BuildCheriBSDDiskImage(BuildDiskImageBase):
    projectName = "disk-image"
    dependencies = ["qemu", "cheribsd"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        hostUsername = pwd.getpwuid(os.geteuid())[0]
        defaultHostname = ComputedDefaultValue(
            function=lambda conf, unused: "qemu-cheri" + conf.cheriBitsStr + "-" + hostUsername,
            asString="qemu-cheri${CHERI_BITS}-" + hostUsername)
        super().setupConfigOptions(extraFilesShortname="-extra-files", defaultHostname=defaultHostname, **kwargs)
        defaultDiskImagePath = ComputedDefaultValue(
                function=_defaultDiskImagePath, asString="$OUTPUT_ROOT/cheri256-disk.qcow2 or "
                                                         "$OUTPUT_ROOT/cheri128-disk.qcow2 depending on --cheri-bits.")
        cls.diskImagePath = cls.addPathOption("path", shortname="-disk-image-path", default=defaultDiskImagePath,
                                              metavar="IMGPATH", help="The output path for the QEMU disk image",
                                              showHelp=True)
        cls.disableTMPFS = cls.addBoolOption("disable-tmpfs", shortname="-disable-tmpfs",
                                             help="Don't make /tmp a TMPFS mount in the CHERIBSD system image."
                                                  " This is a workaround in case TMPFS is not working correctly")

    def __init__(self, config: CheriConfig):
        super().__init__(config, sourceClass=BuildCHERIBSD)
        self.minimumImageSize = "256m"  # let's try to shrink the image size


class BuildFreeBSDDiskImage(BuildDiskImageBase):
    projectName = "disk-image-freebsd-mips"
    dependencies = ["qemu", "freebsd-mips"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        hostUsername = pwd.getpwuid(os.geteuid())[0]
        super().setupConfigOptions(defaultHostname="qemu-mips-" + hostUsername, **kwargs)
        defaultDiskImagePath = ComputedDefaultValue(
                function=lambda config, project: config.outputRoot / "freebsd-mips.qcow2",
                asString="$OUTPUT_ROOT/freebsd-mips.qcow2")
        cls.diskImagePath = cls.addPathOption("path", default=defaultDiskImagePath, showHelp=True,
                                              metavar="IMGPATH", help="The output path for the QEMU disk image")
        cls.disableTMPFS = True  # MALTA64 doesn't include tmpfs

    def __init__(self, config: CheriConfig):
        # TODO: different extra-files directory
        super().__init__(config, sourceClass=BuildFreeBSD)
        self.minimumImageSize = "256m"
