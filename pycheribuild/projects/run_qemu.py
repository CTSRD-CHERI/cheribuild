import sys
import socket

from ..project import Project
from ..utils import *


class LaunchQEMU(Project):
    target = "run"
    dependencies = ["qemu", "disk-image"]

    def __init__(self, config):
        super().__init__(config, projectName="run-qemu")

    def process(self):
        qemuBinary = self.config.sdkDir / "bin/qemu-system-cheri"
        currentKernel = self.config.cheribsdRootfs / "boot/kernel/kernel"

        if not self.isForwardingPortAvailable():
            print("Port usage information:")
            if IS_FREEBSD:
                runCmd("sockstat", "-P", "tcp", "-p", str(self.config.sshForwardingPort))
            elif IS_LINUX:
                runCmd("sh", "-c", "netstat -tulpne | grep \":" + str(str(self.config.sshForwardingPort)) + "\"")
            fatalError("SSH forwarding port", self.config.sshForwardingPort, "is already in use!")

        print("About to run QEMU with image", self.config.diskImage, "and kernel", currentKernel,
              coloured(AnsiColour.green, "\nListening for SSH connections on localhost:" +
                       str(self.config.sshForwardingPort)))
        # input("Press enter to continue")
        runCmd([qemuBinary, "-M", "malta",  # malta cpu
                "-kernel", currentKernel,  # assume the current image matches the kernel currently build
                "-nographic",  # no GPU
                "-m", "2048",  # 2GB memory
                "-hda", self.config.diskImage,
                "-net", "nic", "-net", "user",
                # bind the qemu ssh port to the hosts port 9999
                "-redir", "tcp:" + str(self.config.sshForwardingPort) + "::22",
                ], stdout=sys.stdout)  # even with --quiet we want stdout here

    def isForwardingPortAvailable(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", self.config.sshForwardingPort))
                return True
        except OSError:
            return False
