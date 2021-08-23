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
import sys

from pathlib import Path
from .project import CheriConfig, SimpleProject, DefaultInstallDir
from ..utils import OSInfo, status_update
from ..processutils import (get_program_version, print_command, run_and_kill_children_on_exit, run_command)

class AddUser(SimpleProject):
    target = "adduser"
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def __init__(self, config: CheriConfig):
        super().__init__(config)

    def process(self):
        file = str(self.config.output_root.absolute()) + "/Dockerfile.adduser"
        user = getpass.getuser()
        output = ""

        # Create a Dockerfile that will contain this user's name, gid, uid
        try:
            fp = open(file, mode = "w")
            contents = "FROM cheribuild-docker\n\nRUN addgroup --gid " + \
                     str(os.getgid()) + " " + user + \
                     " && adduser --uid " + str(os.getuid()) + " --ingroup " + \
                     user + " " + user + " \n"

            fp.write(contents)

            fp.close()

        except:
            status_update("Could not create ", file)
            sys.exit()

        # Build a new image from our installed image with this user
        try:
            docker_run_cmd = ["docker", "build", "-f", file, "."]
        
            output = run_command(docker_run_cmd, config=self.config, give_tty_control=True, capture_output=True)

            os.remove(file)
            
        except subprocess.CalledProcessError as e:
            os.remove(file) # Clean up after ourselves

            # if the image is missing print a helpful error message:
            if e.returncode == 125:
                status_update("It seems like the docker image", config.docker_container, "was not found.")
                status_update("In order to build the default docker image for cheribuild (cheribuild-test) run:")
                print(
                    coloured(AnsiColour.blue, "cd", cheribuild_dir + "/docker && docker build --tag cheribuild-docker ."))
                sys.exit(coloured(AnsiColour.red, "Failed to start docker!"))
                raise
            sys.exit()

        # Take the new image and retag it to the original name
        try:
            result = str(output.stdout)
            image = result[result.rfind(" ") + 1:-3] # -3 gets the \n off

            docker_run_cmd = ["docker", "image", "tag", image, "cheribuild-docker"]
            output = run_command(docker_run_cmd, config=self.config, give_tty_control=True, capture_output=True)

        except:
                sys.exit(coloured(AnsiColour.red, "Failed to retag docker!"))

        sys.exit()

