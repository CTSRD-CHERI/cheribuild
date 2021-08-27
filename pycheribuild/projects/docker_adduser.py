#
# Copyright (c) 2021 George V. Neville-Neil
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
import getpass
import subprocess

from pathlib import Path

from .project import SimpleProject, DefaultInstallDir
from ..utils import (AnsiColour, coloured)
from ..processutils import run_command


class DockerAdduser(SimpleProject):
    target = "docker-adduser"
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def process(self):
        target_file = self.config.output_root.absolute() / "Dockerfile.adduser"
        try:
            user = getpass.getuser()
        except KeyError:
            self.fatal("Could not get current username")
            user = "nobody"

        # Create a Dockerfile that will contain this user's name, gid, uid
        self.write_file(target_file, overwrite=True, contents=f"""
FROM cheribuild-docker
RUN addgroup --gid {os.getgid()} {user} && \
    adduser --uid {os.getuid()} --ingroup {user} {user}
""")

        # Build a new image from our installed image with this user
        try:
            docker_run_cmd = ["docker", "build", "--tag=cheribuild-docker",
                              "-f", target_file, "."]
            run_command(docker_run_cmd, config=self.config)

        except subprocess.CalledProcessError as e:
            # if the image is missing print a helpful error message:
            if e.returncode == 1:
                cheribuild_dir = str(Path(__file__).absolute().parent.parent.parent)
                error = "It seems like the docker image " + \
                        self.config.docker_container + " was not found."
                hint = "In order to build the default docker image for cheribuild run:" + \
                    coloured(AnsiColour.blue, "cd", cheribuild_dir +
                             "/docker && docker build --tag=" +
                             self.config.docker_container + " .")
                self.fatal(error, fixit_hint=hint)
            else:
                self.fatal("Docker build failed.", e)
