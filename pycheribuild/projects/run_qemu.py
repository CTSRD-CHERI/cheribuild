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
import socket

from .cheribsd import _BuildFreeBSD
from .cherios import BuildCheriOS
from .build_qemu import BuildQEMU
from .disk_image import _BuildFreeBSDImageBase
from .disk_image import *
from pathlib import Path


def defaultSshForwardingPort():
    # chose a different port for each user (hopefully it isn't in use yet)
    return 9999 + ((os.getuid() - 1000) % 10000)


class LaunchQEMUBase(SimpleProject):
    doNotAddToTargets = True
    _forwardSSHPort = True
    sshForwardingPort = None  # type: int

    @classmethod
    def setupConfigOptions(cls, sshPortShortname: "typing.Optional[str]", defaultSshPort: int=None, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.extraOptions = cls.addConfigOption("extra-options", default=[], kind=list, metavar="QEMU_OPTIONS",
                                               help="Additional command line flags to pass to qemu-system-cheri")
        cls.logfile = cls.addPathOption("logfile", default=None, metavar="LOGFILE",
                                        help="The logfile that QEMU should use.")
        cls.logDir = cls.addPathOption("log-directory", default=None, metavar="DIR",
                                       help="If set QEMU will log to a timestamped file in this directory. Will be "
                                            "ignored if the 'logfile' option is set")
        cls.useTelnet = cls.addConfigOption("monitor-over-telnet", kind=int, metavar="PORT", showHelp=True,
                                            help="If set, the QEMU monitor will be reachable by connecting to localhost"
                                                 "at $PORT via telnet instead of using CTRL+A,C")
        # TODO: -s will no longer work, not sure anyone uses it though
        if cls._forwardSSHPort:
            cls.sshForwardingPort = cls.addConfigOption("ssh-forwarding-port", shortname=sshPortShortname, kind=int,
                                                        default=defaultSshPort, metavar="PORT", showHelp=True,
                                                        help="The port on localhost to forward to the QEMU ssh port. "
                                                             "You can then use `ssh root@localhost -p $PORT` connect "
                                                             "to the VM")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.qemuBinary = BuildQEMU.qemu_binary(config)

        self.currentKernel = None  # type: Path
        self.diskImage = None  # type: Path
        self._diskOptions = []
        self._projectSpecificOptions = []
        self.machineFlags = ["-M", "malta"]  # malta cpu
        self._qemuUserNetworking = True

    def process(self):
        if not self.qemuBinary.exists():
            self.dependencyError("QEMU is missing:", self.qemuBinary,
                                 installInstructions="Run `cheribuild.py qemu` or `cheribuild.py run -d`.")
        if self.currentKernel is not None and not self.currentKernel.exists():
            self.dependencyError("Kernel is missing:", self.currentKernel,
                                 installInstructions="Run `cheribuild.py cheribsd` or `cheribuild.py run -d`.")
        if self.diskImage:
            if len(self._diskOptions) == 0:
                self._diskOptions = ["-hda", self.diskImage]
            if not self.diskImage.exists():
                self.dependencyError("Disk image is missing:", self.diskImage,
                                     installInstructions="Run `cheribuild.py disk-image` or `cheribuild.py run -d`.")
        if self._forwardSSHPort and not self.isPortAvailable(self.sshForwardingPort):
            self.printPortUsage(self.sshForwardingPort)
            fatalError("SSH forwarding port", self.sshForwardingPort, "is already in use! Make sure you don't ",
                       "already have a QEMU instance running or change the chosen port by setting the config option",
                       self.target + "/ssh-forwarding-port")

        monitorOptions = []
        if self.useTelnet:
            monitorPort = self.useTelnet
            monitorOptions = ["-monitor", "telnet:127.0.0.1:" + str(monitorPort) + ",server,nowait"]
            if not self.isPortAvailable(monitorPort):
                warningMessage("Cannot connect QEMU montitor to port", monitorPort)
                self.printPortUsage(monitorPort)
                if self.queryYesNo("Will connect the QEMU monitor to stdio instead. Continue?"):
                    monitorOptions = []
                else:
                    fatalError("Monitor port not available and stdio is not acceptable.")
                    return
        logfileOptions = []
        if self.logfile:
            logfileOptions = ["-D", self.logfile]
        elif self.logDir:
            if not self.logDir.is_dir():
                self.makedirs(self.logDir)
            filename = "qemu-cheri-" + datetime.datetime.now().strftime("%Y%m%d_%H-%M-%S") + ".log"
            latestSymlink = self.logDir / "qemu-cheri-latest.log"
            if latestSymlink.is_symlink():
                latestSymlink.unlink()
            if not latestSymlink.exists():
                self.createSymlink(self.logDir / filename, latestSymlink, relative=True, cwd=self.logDir)
            logfileOptions = ["-D", self.logDir / filename]
        # input("Press enter to continue")
        kernelFlags = ["-kernel", self.currentKernel] if self.currentKernel else []
        qemuCommand = [self.qemuBinary] + self.machineFlags + kernelFlags + [
            "-m", "2048",  # 2GB memory
            "-nographic",  # no GPU
        ] + self._projectSpecificOptions + self._diskOptions + monitorOptions + logfileOptions + self.extraOptions
        if self._qemuUserNetworking:
            qemuCommand += ["-net", "nic", "-net", "user"]
        statusUpdate("About to run QEMU with image", self.diskImage, "and kernel", self.currentKernel)
        if self._forwardSSHPort:
            # bind the qemu ssh port to the hosts port
            qemuCommand += ["-redir", "tcp:" + str(self.sshForwardingPort) + "::22"]
            print(coloured(AnsiColour.green, "\nListening for SSH connections on localhost:", self.sshForwardingPort))

        runCmd(qemuCommand, stdout=sys.stdout, stderr=sys.stderr)  # even with --quiet we want stdout here

    @staticmethod
    def printPortUsage(port: int):
        print("Port", port, "usage information:")
        if IS_FREEBSD:
            runCmd("sockstat", "-P", "tcp", "-p", str(port))
        elif IS_LINUX:
            runCmd("sh", "-c", "netstat -tulpne | grep \":" + str(port) + "\"")

    @staticmethod
    def isPortAvailable(port: int):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False


class AbstractLaunchFreeBSD(LaunchQEMUBase):
    doNotAddToTargets = True

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        if not IS_FREEBSD:
            cls.remoteKernelPath = cls.addConfigOption("remote-kernel-path", showHelp=True,
                                                       help="Path to the FreeBSD kernel image on a remote host. "
                                                            "Needed because FreeBSD cannot be cross-compiled.")
            cls.skipKernelUpdate = cls.addBoolOption("skip-kernel-update", showHelp=True,
                                                     help="Don't update the kernel from the remote host")

    def __init__(self, config: CheriConfig, sourceClass: type(_BuildFreeBSD),
                 diskImageClass: type(_BuildFreeBSDImageBase)):
        super().__init__(config)
        self.sourceClass = sourceClass
        self.currentKernel = sourceClass.rootfsDir(self.config) / "boot/kernel/kernel"
        self.diskImage = diskImageClass.diskImagePath
        self.needsRemoteKernelCopy = True
        # no need to copy from remote host if we were crossbuilding
        if IS_FREEBSD or sourceClass.crossbuild:
            self.needsRemoteKernelCopy = False
        # same if skip-update was passed
        elif self.skipKernelUpdate or self.config.skipUpdate:
            self.needsRemoteKernelCopy = False

    def _copyKernelImageFromRemoteHost(self):
        statusUpdate("Copying kernel image from FreeBSD build machine")
        if not self.remoteKernelPath:
            fatalError("Path to the remote disk image is not set, option '--", self.target, "/",
                       "remote-kernel-path' must be set to a path that scp understands",
                       " (e.g. vica:/foo/bar/kernel)", sep="")
            return
        scpPath = os.path.expandvars(self.remoteKernelPath)
        self.makedirs(self.currentKernel.parent)
        self.copyRemoteFile(scpPath, self.currentKernel)

    def process(self):
        if self.needsRemoteKernelCopy:
            self._copyKernelImageFromRemoteHost()
        super().process()


class LaunchCheriBSD(AbstractLaunchFreeBSD):
    projectName = "run"
    dependencies = ["qemu", "disk-image"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(sshPortShortname="-ssh-forwarding-port",
                                   defaultSshPort=defaultSshForwardingPort(),
                                   **kwargs)

    def __init__(self, config):
        super().__init__(config, BuildCHERIBSD, BuildCheriBSDDiskImage)


class LaunchFreeBSDMips(AbstractLaunchFreeBSD):
    projectName = "run-freebsd-mips"
    dependencies = ["qemu", "disk-image-freebsd-mips"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(sshPortShortname=None, useTelnetShortName=None,
                                   defaultSshPort=defaultSshForwardingPort() + 2,
                                   **kwargs)

    def __init__(self, config):
        super().__init__(config, BuildFreeBSDForMIPS, BuildFreeBSDDiskImageMIPS)


class LaunchCheriOSQEMU(LaunchQEMUBase):
    target = "run-cherios"
    projectName = "run-cherios"
    dependencies = ["qemu", "cherios"]
    _forwardSSHPort = False
    _qemuUserNetworking = False

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(sshPortShortname=None, useTelnetShortName=None,
                                   defaultSshPort=defaultSshForwardingPort() + 4,
                                   **kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # FIXME: these should be config options
        self.currentKernel = BuildCheriOS.buildDir / "boot/cherios.elf"
        self.diskImage = self.config.outputRoot / "cherios-disk.img"
        self._projectSpecificOptions = ["-no-reboot"]
        self._diskOptions = ["-drive", "if=none,file=" + str(self.diskImage) + ",id=drv,format=raw",
                             "-device", "virtio-blk-device,drive=drv"]
        self._qemuUserNetworking = False

    def process(self):
        if not self.diskImage.exists():
            if self.queryYesNo("CheriOS disk image is missing. Would you like to create a zero-filled 1MB image?"):
                runCmd("dd", "if=/dev/zero", "of=" + str(self.diskImage), "bs=1M", "count=1")
        super().process()


class LaunchFreeBSDX86(AbstractLaunchFreeBSD):
    projectName = "run-freebsd-x86"
    dependencies = ["disk-image-freebsd-x86"]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(sshPortShortname=None, useTelnetShortName=None,
                                   defaultSshPort=defaultSshForwardingPort() + 6,
                                   **kwargs)

    def __init__(self, config):
        super().__init__(config, BuildFreeBSDForX86, BuildFreeBSDDiskImageX86)
        self._addRequiredSystemTool("qemu-system-x86_64")
        self.qemuBinary = Path(shutil.which("qemu-system-x86_64"))
        self.machineFlags = [] # default cpu
        self.currentKernel = None  # needs the bootloader

