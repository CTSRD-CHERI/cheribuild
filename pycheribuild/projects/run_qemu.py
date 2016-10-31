import sys
import socket

from ..project import Project
from ..utils import *


class LaunchQEMU(Project):
    target = "run"
    dependencies = ["qemu", "disk-image"]

    def __init__(self, config):
        super().__init__(config, projectName="run-qemu")
        self.qemuBinary = self.config.sdkDir / "bin/qemu-system-cheri"
        self.currentKernel = self.config.cheribsdRootfs / "boot/kernel/kernel"

    def process(self):
        if not self.qemuBinary.exists():
            self.dependencyError("QEMU is missing:", self.qemuBinary,
                                 installInstructions="Run `cheribuild.py qemu` or `cheribuild.py run -d`.")
        if not self.currentKernel.exists():
            self.dependencyError("CheriBSD kernel is missing:", self.currentKernel,
                                 installInstructions="Run `cheribuild.py cheribsd` or `cheribuild.py run -d`.")
        if not self.config.diskImage.exists():
            self.dependencyError("CheriBSD disk image is missing:", self.config.diskImage,
                                 installInstructions="Run `cheribuild.py disk-image` or `cheribuild.py run -d`.")

        if not self.isPortAvailable(self.config.sshForwardingPort):
            print("Port usage information:")
            if IS_FREEBSD:
                runCmd("sockstat", "-P", "tcp", "-p", str(self.config.sshForwardingPort))
            elif IS_LINUX:
                runCmd("sh", "-c", "netstat -tulpne | grep \":" + str(str(self.config.sshForwardingPort)) + "\"")
            fatalError("SSH forwarding port", self.config.sshForwardingPort, "is already in use!")

        monitorOptions = []
        if self.config.qemuUseTelnet:
            monitorPort = self.config.sshForwardingPort + 1
            monitorOptions = ["-monitor", "telnet:127.0.0.1:" + str(monitorPort) + ",server,nowait"]
            if not self.isPortAvailable(monitorPort):
                warningMessage("Cannot connect QEMU montitor to port", monitorPort)
                if self.queryYesNo("Will connect the monitor to stdio instead. Continue?"):
                    monitorOptions = []
                else:
                    fatalError("Monitor port not available and stdio is not acceptable.")
                    return

        print("About to run QEMU with image", self.config.diskImage, "and kernel", self.currentKernel,
              coloured(AnsiColour.green, "\nListening for SSH connections on localhost:" +
                       str(self.config.sshForwardingPort)))
        # input("Press enter to continue")
        runCmd([self.qemuBinary, "-M", "malta",  # malta cpu
                "-kernel", self.currentKernel,  # assume the current image matches the kernel currently build
                "-nographic",  # no GPU
                "-m", "2048",  # 2GB memory
                "-hda", self.config.diskImage,
                "-net", "nic", "-net", "user",
                # bind the qemu ssh port to the hosts port 9999
                "-redir", "tcp:" + str(self.config.sshForwardingPort) + "::22",
                ] + monitorOptions, stdout=sys.stdout)  # even with --quiet we want stdout here

    @staticmethod
    def isPortAvailable(port: int):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False
