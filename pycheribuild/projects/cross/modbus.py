#
# Copyright (c) 2020 Michael Dodson
# All rights reserved.
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

import shlex

from .crosscompileproject import CrossCompileCMakeProject, DefaultInstallDir, GitRepository


class BuildModbus(CrossCompileCMakeProject):
    project_name = "modbus"
    repository = GitRepository("https://github.com/dodsonmg/cheri_modbus.git",
        default_branch="master", force_branch=True)

    # atm, we build and install in the source_dir.
    build_in_source_dir = True
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS
    _extra_git_clean_excludes = ["--exclude=src"]  # don't delete src/ when running clean

    def _run_vcs(self):
        # this is a meta version control system for downloading and unpacking repos
        cmdline = ["vcs", "import", "--input", "modbus.repos", "src"]
        self.run_cmd(cmdline, cwd=self.source_dir)

    def _run_colcon(self, **kwargs):
        # colcon is the meta build system (on top of cmake) used by ros
        colcon_cmd = ["colcon", "build"]
        colcon_args = ["--no-warn-unused-cli", "--packages-skip-build-finished"]
        cmake_args = ["--cmake-args", "-DBUILD_TESTING=NO"]
        cmake_args.append("-DCMAKE_BUILD_TYPE=" + str(self.build_type.value))
        if not self.compiling_for_host():
            cmake_args.append("-DCMAKE_TOOLCHAIN_FILE=" + str(self.source_dir / "CrossToolchain.cmake"))

        cmdline = colcon_cmd + cmake_args + colcon_args
        if self.config.verbose:
            cmdline.append("--event-handlers")
            cmdline.append("console_cohesion+")
        self.run_cmd(cmdline, cwd=self.source_dir, **kwargs)

    def _set_env(self, **kwargs):
        # create cheri_setup.csh and cheri_setup.sh files in self.source_dir which can be source'ed
        # to set environment variables (primarily LD_CHERI_LIBRARY_PATH)
        #
        # based off the install/setup.bash file sourced for ubuntu installs

        # source the setup script created by ROS to set LD_LIBRARY_PATH
        setup_script = self.source_dir / "install" / "setup.bash"
        if not setup_script.is_file():
            self.warning("No setup.bash file to source.")
            return
        cmdline = shlex.split("bash -c 'source " + str(setup_script) + " && echo $LD_LIBRARY_PATH'")
        output = self.run_cmd(cmdline, cwd=self.source_dir, capture_output=True, print_verbose_only=False, **kwargs)

        # extract LD_LIBRARY_PATH into a variable
        LD_LIBRARY_PATH = output.stdout.decode("utf-8").rstrip()
        if len(LD_LIBRARY_PATH) == 0:
            self.warning("LD_LIBRARY_PATH not set.")
            return

        # convert LD_LIBRARY_PATH into LD_CHERI_LIBRARY_PATH for CheriBSD
        LD_LIBRARY_PATH = str(self.source_dir) + ":" + LD_LIBRARY_PATH
        LD_LIBRARY_PATH = LD_LIBRARY_PATH.replace(str(self.source_dir), "${rootdir}")
        LD_CHERI_LIBRARY_PATH = LD_LIBRARY_PATH
        LD_CHERI_LIBRARY_PATH += ":${LD_CHERI_LIBRARY_PATH}"
        LD_LIBRARY_PATH += ":${LD_LIBRARY_PATH}"

        # write LD_CHERI_LIBRARY_PATH to a text file to source from sh in CheriBSD
        posix_sh_script = """#!/bin/sh
rootdir=`pwd`
export LD_CHERI_LIBRARY_PATH={LD_CHERI_LIBRARY_PATH}
export LD_64_CHERI_LIBRARY_PATH={LD_CHERI_LIBRARY_PATH}
export LD_LIBRARY_PATH={LD_LIBRARY_PATH}
export LD_64_LIBRARY_PATH={LD_LIBRARY_PATH}
""".format(LD_CHERI_LIBRARY_PATH=LD_CHERI_LIBRARY_PATH, LD_LIBRARY_PATH=LD_LIBRARY_PATH)
        # write LD_CHERI_LIBRARY_PATH to a text file to source from sh in CheriBSD
        self.write_file(self.source_dir / 'cheri_setup.sh', posix_sh_script, overwrite=True)

    def update(self):
        super().update()
        if not (self.source_dir / "src").is_dir():
            self.makedirs(self.source_dir / "src")
        self._run_vcs()

    def configure(self, **kwargs):
        # overriding this method allows creation of CrossToolchain.cmake
        # without actually calling cmake, as super().configure() would do
        if not self.compiling_for_host():
            self.generate_cmake_toolchain_file(self.source_dir / "CrossToolchain.cmake")

    def compile(self, **kwargs):
        self._run_colcon(**kwargs)

    def install(self, **kwargs):
        # colcon build performs an install, so we override this to make sure
        # super doesn't attempt to install with ninja

        # call the function to create env setup files
        if not self.compiling_for_host():
            self._set_env(**kwargs)

    # def run_tests(self):
    #     # only test when not compiling for host
    #     if not self.compiling_for_host():
    #         self.target_info.run_cheribsd_test_script("run_ros2_tests.py", mount_source_dir=True, mount_sysroot=True)
