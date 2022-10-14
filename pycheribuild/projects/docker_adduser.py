#
# Copyright (c) 2021 George V. Neville-Neil
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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

from .simple_project import SimpleProject
from ..utils import OSInfo


class DockerAdduser(SimpleProject):
    target = "docker-adduser"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.build_dir = self.config.build_root / (self.target + "-build")

    def process(self):
        if not self.build_dir.is_dir():
            self.makedirs(self.build_dir)

        try:
            user = getpass.getuser()
        except KeyError:
            self.fatal("Could not get current username")
            user = "nobody"

        uid = os.getuid()
        gid = os.getgid()
        if OSInfo.IS_MAC:
            # macOS doesn't create per-user groups, so users are in system
            # groups and thus we don't want to create them in the container
            # lest they conflict with Ubuntu's sytem groups. Unlike Linux we
            # don't actually need IDs to match, just that we have a proper
            # non-root user, so use the UID as the GID.
            gid = uid

        # Create a Dockerfile that will contain this user's name, gid, uid
        self.write_file(self.build_dir / "Dockerfile", overwrite=True, contents=f"""
FROM {self.config.docker_container}
RUN addgroup --gid {gid} {user} && \
    adduser --uid {uid} --ingroup {user} {user}
""")

        # Build a new image from our installed image with this user
        try:
            docker_run_cmd = ["docker", "build", "--tag=" + self.config.docker_container, "."]
            self.run_cmd(docker_run_cmd, cwd=self.build_dir)

        except subprocess.CalledProcessError:
            # if the image is missing print a helpful error message:
            error = "Failed to add your user to the docker image " + \
                    self.config.docker_container
            hint = "Ensure you have " + self.config.docker_container + \
                   "available (check using docker image ls)"
            self.fatal(error, fixit_hint=hint)
