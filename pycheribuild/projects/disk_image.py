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
import shlex
import stat
import tempfile
from pathlib import Path

from .cheribsd import _BuildFreeBSD
from .cheribsd import *
from ..config.loader import ComputedDefaultValue
from .project import *
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


class _BuildDiskImageBase(SimpleProject):
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
        cls.useQCOW2 = cls.addBoolOption("use-qcow2", help="Convert the disk image to QCOW2 format instead of raw")
        if not IS_FREEBSD:
            cls.remotePath = cls.addConfigOption("remote-path", showHelp=True, metavar="PATH", help="The path on the "
                                                 "remote FreeBSD machine from where to copy the disk image")
        cls.disableTMPFS = None

    def __init__(self, config, sourceClass: type(_BuildFreeBSD)):
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
        self.dirsAddedToManifest = [Path(".")]  # Path().parents always includes a "." entry
        self.rootfsDir = sourceClass.rootfsDir(self.config)
        assert self.rootfsDir is not None
        self.userGroupDbDir = sourceClass.getSourceDir(self.config) / "etc"
        self.crossBuildImage = sourceClass.crossbuild
        self.minimumImageSize = "1g",  # minimum image size = 1GB
        self.dirsInMtree = []

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
            runCmd([self.install_cmd, "-d"] + commonArgs + ["-m", self.getModeString(baseDirectory / parent),
                                                     str(self.rootfsDir / parent)], printVerboseOnly=True)
            self.dirsAddedToManifest.append(parent)

        # need to pass target file and destination dir so that METALOG can be filled correctly
        parentDir = self.rootfsDir / pathInTarget.parent
        runCmd([self.install_cmd] + commonArgs + ["-m", mode, str(file), str(parentDir)], printVerboseOnly=True)
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

        runCmd([
            self.makefs_cmd,
            "-Z", # sparse file output
            "-d", "0x90000", # trace POPULATE and WRITE_FILE events
            "-b", "30%",  # minimum 30% free blocks
            "-f", "30%",  # minimum 30% free inodes
            "-R", "128m",  # round up size to the next 16m multiple
            "-M", self.minimumImageSize,
            "-B", "be",  # big endian byte order
            "-F", self.manifestFile,  # use METALOG as the manifest for the disk image
            "-N", self.userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file
            # which makes sure that the numeric UID values are correct
            self.diskImagePath,  # output file
            self.rootfsDir  # directory tree to use for the image
        ])
        # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
        if self.config.verbose:
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


def _defaultDiskImagePath(conf: "CheriConfig", cls):
    if conf.cheriBits == 128:
        return conf.outputRoot / "cheri128-disk.img"
    return conf.outputRoot / "cheri256-disk.img"


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
        super().__init__(config, sourceClass=BuildCHERIBSD)
        self.minimumImageSize = "256m"  # let's try to shrink the image size
        # TODO: fetch pkg from https://people.freebsd.org/~brooks/packages/cheribsd-mips-20170403-brooks-20170609/


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
        super().__init__(config, sourceClass=self._freebsd_build_class)
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
