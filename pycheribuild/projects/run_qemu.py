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
import sys
import socket

from ..project import Project
from ..utils import *
from .cheribsd import BuildCHERIBSD
from pathlib import Path


class LaunchQEMU(Project):
    target = "run"
    dependencies = ["qemu", "disk-image"]

    @classmethod
    def setupConfigOptions(cls):
        super().setupConfigOptions()
        cls.extraOptions = cls.addConfigOption("extra-options", default=[], kind=list, metavar="QEMU_OPTIONS",
                                               help="Additional command line flags to pass to qemu-system-cheri")
        cls.logfile = cls.addConfigOption("logfile", default=None, kind=str, metavar="LOGFILE",
                                          help="The logfile that QEMU should use.")
        cls.logDir = cls.addConfigOption("log-directory", default=None, kind=str, metavar="DIR",
                                         help="If set QEMU will log to a timestamped file in this directory. Will be "
                                              "ignored if the 'logfile' option is set")

    def __init__(self, config):
        super().__init__(config, projectName="run-qemu")
        self.qemuBinary = self.config.sdkDir / "bin/qemu-system-cheri"
        self.currentKernel = BuildCHERIBSD.rootfsDir(self.config) / "boot/kernel/kernel"

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
        logfileOptions = []
        if self.logfile:
            logfileOptions = ["-D", self.logfile]
        elif self.logDir:
            logPath = Path(self.logDir)
            if not logPath.is_dir():
                self._makedirs(logPath)
            filename = "qemu-cheri-" + datetime.datetime.now().strftime("%Y%m%d_%H-%M-%S") + ".log"
            latestSymlink = logPath / "qemu-cheri-latest.log"
            if latestSymlink.is_symlink():
                latestSymlink.unlink()
            if not latestSymlink.exists():
                self.createSymlink(logPath / filename, latestSymlink, relative=True, cwd=logPath)
            logfileOptions = ["-D", logPath / filename]
        # input("Press enter to continue")
        runCmd([self.qemuBinary, "-M", "malta",  # malta cpu
                "-kernel", self.currentKernel,  # assume the current image matches the kernel currently build
                "-nographic",  # no GPU
                "-m", "2048",  # 2GB memory
                "-hda", self.config.diskImage,
                "-net", "nic", "-net", "user",
                # bind the qemu ssh port to the hosts port 9999
                "-redir", "tcp:" + str(self.config.sshForwardingPort) + "::22",
                ] + monitorOptions + logfileOptions + self.extraOptions,
               stdout=sys.stdout)  # even with --quiet we want stdout here

    @staticmethod
    def isPortAvailable(port: int):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False
