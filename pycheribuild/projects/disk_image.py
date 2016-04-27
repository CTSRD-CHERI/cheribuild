import os
import shutil
import tempfile

from ..project import Project
from ..utils import *
from pathlib import Path


# Notes:
# Mount the filesystem of a BSD VM: guestmount -a /foo/bar.qcow2 -m /dev/sda1:/:ufstype=ufs2:ufs --ro /mnt/foo
# ufstype=ufs2 is required as the Linux kernel can't automatically determine which UFS filesystem is being used
# Same thing is possible with qemu-nbd, but needs root (might be faster)

class BuildDiskImage(Project):
    def __init__(self, config):
        super().__init__(config, projectName="disk-image")
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifestFile = None  # type: Path
        self.userGroupDbDir = self.config.cheribsdSources / "etc"
        self.extraFiles = []  # type: typing.List[Path]
        self.requiredSystemTools = ["ssh-keygen", "makefs"]

    def addFileToImage(self, file: Path, targetDir: str, user="root", group="wheel", mode="0644"):
        assert not targetDir.startswith("/")
        # e.g. "install -N /home/alr48/cheri/cheribsd/etc -U -M /home/alr48/cheri/output/rootfs//METALOG
        # -D /home/alr48/cheri/output/rootfs -o root -g wheel -m 444 alarm.3.gz
        # /home/alr48/cheri/output/rootfs/usr/share/man/man3/"
        parentDir = self.config.cheribsdRootfs / targetDir
        commonArgs = [
            "-N", str(self.userGroupDbDir),  # Use a custom user/group database text file
            "-U",  # Indicate that install is running unprivileged (do not change uid/gid)
            "-M", str(self.manifestFile),  # the mtree manifest to write the entry to
            "-D", str(self.config.cheribsdRootfs),  # DESTDIR (will be stripped from the start of the mtree file
            "-o", user, "-g", group,  # uid and gid
            "-m", mode,  # access rights
        ]
        # install -d: Create directories. Missing parent directories are created as required.
        # If we only create the parent directory if it doesn't exist yet we might break the build if rootfs wasn't
        # cleaned before running disk-image. We get errors like this:
        #   makefs: ./root/.ssh: missing directory in specification
        #   makefs: failed at line 27169 of the specification
        # Having the directory in the spec multiple times is fine, so we just do that instead
        runCmd(["install", "-d"] + commonArgs + [str(parentDir)], printVerboseOnly=True)
        # need to pass target file and destination dir so that METALOG can be filled correctly
        runCmd(["install"] + commonArgs + [str(file), str(parentDir)], printVerboseOnly=True)
        if file in self.extraFiles:
            self.extraFiles.remove(file)  # remove it from extraFiles so we don't install it twice

    def createFileForImage(self, outDir: Path, pathInImage: str, *, contents: str="\n", showContentsByDefault=True):
        if pathInImage.startswith("/"):
            pathInImage = pathInImage[1:]
        assert not pathInImage.startswith("/")
        userProvided = self.config.extraFiles / pathInImage
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
        self.addFileToImage(targetFile, str(Path(pathInImage).parent))

    def prepareRootfs(self, outDir: Path):
        self.manifestFile = outDir / "METALOG"
        self.copyFile(self.config.cheribsdRootfs / "METALOG", self.manifestFile)

        # we need to add /etc/fstab and /etc/rc.conf as well as the SSH host keys to the disk-image
        # If they do not exist in the extra-files directory yet we generate a default one and use that
        # Additionally all other files in the extra-files directory will be added to the disk image
        for root, dirnames, filenames in os.walk(str(self.config.extraFiles)):
            for filename in filenames:
                self.extraFiles.append(Path(root, filename))

        # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
        # Overlay extra-files over additional stuff over cheribsd rootfs dir

        self.createFileForImage(outDir, "/etc/fstab", contents="/dev/ada0 / ufs rw 1 1\ntmpfs /tmp tmpfs rw 0 0\n")
        # enable ssh and set hostname
        # TODO: use separate file in /etc/rc.conf.d/ ?
        rcConfContents = """hostname="qemu-cheri-{username}"
ifconfig_le0="DHCP"  # use DHCP on the standard QEMU usermode nic
sshd_enable="YES"
sendmail_enable="NONE"  # completely disable sendmail
# disable cron, as this removes errors like: cron[600]: _secure_path: cannot stat /etc/login.conf: Permission denied
# it should also speed up boot a bit
cron_enable="NO"
# tmpmfs="YES" only creates a 20 MB ramdisk for /tmp, use /etc/fstab and tmpfs instead
# the extra m in tmpmfs is not a typo: it means mount /tmp as a memory filesystem (MFS)
# tmpmfs="YES"
""".format(username=os.getlogin())
        self.createFileForImage(outDir, "/etc/rc.conf", contents=rcConfContents)

        # make sure that the disk image always has the same SSH host keys
        # If they don't exist the system will generate one on first boot and we have to accept them every time
        self.generateSshHostKeys()

        print("Adding 'PermitRootLogin without-password' to /etc/ssh/sshd_config")
        # make sure we can login as root with pubkey auth:
        sshdConfig = self.config.cheribsdRootfs / "etc/ssh/sshd_config"
        newSshdConfigContents = self.readFile(sshdConfig)
        newSshdConfigContents += "\n# Allow root login with pubkey auth:\nPermitRootLogin without-password\n"
        self.createFileForImage(outDir, "/etc/ssh/sshd_config", contents=newSshdConfigContents,
                                showContentsByDefault=False)

        # now try adding the right ~/.ssh/authorized_keys
        authorizedKeys = self.config.extraFiles / "root/.ssh/authorized_keys"
        if not authorizedKeys.is_file():
            sshKeys = list(Path(os.path.expanduser("~/.ssh/")).glob("id_*.pub"))
            if len(sshKeys) > 0:
                print("Found the following ssh keys:", list(map(str, sshKeys)))
                if self.queryYesNo("Should they be added to /root/.ssh/authorized_keys?", defaultResult=True):
                    contents = ""
                    for pubkey in sshKeys:
                        contents += self.readFile(pubkey)
                    self.createFileForImage(outDir, "/root/.ssh/authorized_keys", contents=contents)
                    print("Should this authorized_keys file be used by default? ",
                          "You can always change them by editing/deleting '", authorizedKeys, "'.", end="", sep="")
                    if self.queryYesNo(""):
                        self.copyFile(outDir / "root/.ssh/authorized_keys", authorizedKeys)

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

        rawDiskImage = Path(str(self.config.diskImage).replace(".qcow2", ".img"))
        runCmd([
            "makefs",
            "-b", "70%",  # minimum 70% free blocks
            "-f", "30%",  # minimum 30% free inodes
            "-M", "4g",  # minimum image size = 4GB
            "-B", "be",  # big endian byte order
            "-F", self.manifestFile,  # use METALOG as the manifest for the disk image
            "-N", self.userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file
            # which makes sure that the numeric UID values are correct
            rawDiskImage,  # output file
            self.config.cheribsdRootfs  # directory tree to use for the image
        ])
        # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
        if self.config.verbose:
            runCmd(qemuImgCommand, "info", rawDiskImage)
        runCmd("rm", "-f", self.config.diskImage, printVerboseOnly=True)
        # create a qcow2 version from the raw image:
        runCmd(qemuImgCommand, "convert",
               "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
               "-O", "qcow2",  # convert to qcow2 format
               rawDiskImage,  # input file
               self.config.diskImage)  # output file
        if self.config.verbose:
            runCmd(qemuImgCommand, "info", self.config.diskImage)

    def process(self):
        if not (self.config.cheribsdRootfs / "METALOG").is_file():
            fatalError("mtree manifest", self.config.cheribsdRootfs / "METALOG", "is missing")
        if not (self.userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in ", self.userGroupDbDir)

        if self.config.diskImage.is_file():
            # only show prompt if we can actually input something to stdin
            print("An image already exists (" + str(self.config.diskImage) + "). ", end="")
            if not self.queryYesNo("Overwrite?", defaultResult=True):
                return  # we are done here
            printCommand("rm", self.config.diskImage)
            self.config.diskImage.unlink()

        with tempfile.TemporaryDirectory() as outDir:
            self.prepareRootfs(Path(outDir))
            # now add all the user provided files to the image:
            # we have to make a copy as we modify self.extraFiles in self.addFileToImage()
            for p in self.extraFiles.copy():
                pathInImage = p.relative_to(self.config.extraFiles)
                print("Adding user provided file /", pathInImage, " to disk image.", sep="")
                self.addFileToImage(p, str(pathInImage.parent))
            # finally create the disk image
            self.makeImage()

    def generateSshHostKeys(self):
        # do the same as "ssh-keygen -A" just with a different output directory as it does not allow customizing that
        sshDir = self.config.extraFiles / "etc/ssh"
        self._makedirs(sshDir)
        # -t type Specifies the type of key to create.  The possible values are "rsa1" for protocol version 1
        #  and "dsa", "ecdsa","ed25519", or "rsa" for protocol version 2.

        for keyType in ("rsa1", "rsa", "dsa", "ecdsa", "ed25519"):
            # SSH1 protocol uses just /etc/ssh/ssh_host_key without the type
            privateKeyName = "ssh_host_key" if keyType == "rsa1" else "ssh_host_" + keyType + "_key"
            privateKey = sshDir / privateKeyName
            publicKey = sshDir / (privateKeyName + ".pub")
            if not privateKey.is_file():
                runCmd("ssh-keygen", "-t", keyType,
                       "-N", "",  # no passphrase
                       "-f", str(privateKey))
            self.addFileToImage(privateKey, "etc/ssh", mode="0600")
            self.addFileToImage(publicKey, "etc/ssh", mode="0644")
