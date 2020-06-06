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
#

import shlex

from .crosscompileproject import CrossCompileCMakeProject, DefaultInstallDir, GitRepository


class BuildRos2(CrossCompileCMakeProject):
    project_name = "ros2"
    repository = GitRepository("https://github.com/dodsonmg/ros2_dashing_minimal.git", default_branch="master", force_branch=True)

    # atm, we build and install in the sourceDir.
    # it may eventually be useful to install to rootfs or sysroot depending on whether we want to use ROS2
    # as a library for building other applications using cheribuild
    # therefore, these _install_dir don't do anything, but cheribuild requires them
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS

    dependencies = ["poco"]

    def _ignore_packages(self):
        # some repositories have packages we don't want to build, so we add empty COLCON_IGNORE files
        packages = ["src/ros2/rcl_logging/rcl_logging_log4cxx"] # relative to self.sourceDir
        for package in packages:
            cmdline = ["touch", str(self.sourceDir / package / "COLCON_IGNORE")]
            self.run_cmd(cmdline, cwd=self.sourceDir)

    def _run_vcs(self):
        # this is the meta version control system used by ros for downloading and unpacking repos
        cmdline = ["vcs", "import", "--input", "ros2_minimal.repos", "src"]
        self.run_cmd(cmdline, cwd=self.sourceDir)

    def _run_colcon(self, **kwargs):
        # colcon is the meta build system (on top of cmake) used by ros
        colcon_cmd = ["colcon", "build"]
        colcon_args = ["--no-warn-unused-cli", "--packages-skip-build-finished"]
        cmake_args = ["--cmake-args", "-DBUILD_TESTING=NO"]
        if not self.compiling_for_host():
            cmake_args.append("-DCMAKE_TOOLCHAIN_FILE=" + str(self.sourceDir / "CrossToolchain.cmake"))
        cmdline = colcon_cmd + cmake_args + colcon_args
        if self.config.verbose:
            cmdline.append("--event-handlers")
            cmdline.append("console_cohesion+")
        self.run_cmd(cmdline, cwd=self.sourceDir, **kwargs)

    def _set_env(self, **kwargs):
        # create a cheri_setup.csh file in self.sourceDir which can be source'ed
        # to set environment variables (primarily LD_CHERI_LIBRARY_PATH)
        #
        # based off the install/setup.bash file sourced for ubuntu installs

        # source the setup script created by ROS to set LD_LIBRARY_PATH
        setup_script = self.sourceDir / "install" / "setup.bash"
        if not setup_script.is_file():
            print("No setup.bash file to source.")
            return
        cmdline = shlex.split("bash -c 'source " + str(setup_script) + " | echo $LD_LIBRARY_PATH'")
        output = self.run_cmd(cmdline, cwd=self.sourceDir, captureOutput=True, **kwargs)

        # extract LD_LIBRARY_PATH into a variable
        LD_LIBRARY_PATH = output.stdout.decode("utf-8")
        if len(LD_LIBRARY_PATH) == 0:
            print("LD_LIBRARY_PATH not set.")
            return

        # convert LD_LIBRARY_PATH into LD_CHERI_LIBRARY_PATH for CheriBSD
        LD_LIBRARY_PATHs = LD_LIBRARY_PATH.split(':')
        LD_CHERI_LIBRARY_PATH = "."
        for path in LD_LIBRARY_PATHs:
            LD_CHERI_LIBRARY_PATH += ":" + path
        LD_CHERI_LIBRARY_PATH += ":${LD_CHERI_LIBRARY_PATH}"

        # write LD_CHERI_LIBRARY_PATH to a text file to source from csh in CheriBSD
        with open(str(self.sourceDir / 'cheri_setup.csh'), 'w') as fout:
            fout.write("#!/bin/csh\n\n")
            fout.write("setenv LD_CHERI_LIBRARY_PATH " + LD_CHERI_LIBRARY_PATH + "\n\n")

        # write LD_CHERI_LIBRARY_PATH to a text file to source from sh in CheriBSD
        with open(str(self.sourceDir / 'cheri_setup.sh'), 'w') as fout:
            fout.write("#!/bin/sh\n\n")
            fout.write("setenv LD_CHERI_LIBRARY_PATH " + LD_CHERI_LIBRARY_PATH + "\n\n")

    def update(self):
        super().update()
        if not (self.sourceDir / "src").is_dir():
            self.makedirs(self.sourceDir / "src")
        self._run_vcs()
        self._ignore_packages()

    def configure(self, **kwargs):
        # overriding this method allows creation of CrossToolchain.cmake
        # without actually calling cmake, as super().configure() would do
        if not self.compiling_for_host():
            self.generate_cmake_toolchain_file(self.sourceDir / "CrossToolchain.cmake")

    def compile(self, **kwargs):
        self._run_colcon(**kwargs)

    def install(self, **kwargs):
        # colcon build performs an install, so we override this to make sure
        # super doesn't attemt to install with ninja
        #
        # call the function to create an env setup file
        if not self.compiling_for_host():
            self._set_env(**kwargs)

    def run_tests(self):
        # only test when not compiling for host
        if not self.compiling_for_host():
            self.run_cheribsd_test_script("run_ros2_tests.py", mount_sourcedir=True, mount_sysroot=False)
