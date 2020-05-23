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

from .crosscompileproject import *
# from ..project import ReuseOtherProjectRepository


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
        packages = ["src/ros2/rcl_logging/rcl_logging_log4cxx"] # relative to self.sourceDir
        for package in packages:
            cmdline = ["touch"]
            cmdline.append(str(self.sourceDir / package / "COLCON_IGNORE"))
            self.run_cmd(cmdline, cwd=self.sourceDir)
        return

    def _run_vcs(self):
        cmdline = ["vcs", "import", "--input", "ros2_minimal.repos", "src"]
        return self.run_cmd(cmdline, cwd=self.sourceDir)

    def _run_colcon(self, **kwargs):
        colcon_cmd = ["colcon", "build"]
        colcon_args = ["--no-warn-unused-cli"]
        cmake_args = ["--cmake-args"]
        cmake_args.append("-DBUILD_TESTING=NO")
        if not self.compiling_for_host():
            cmake_args.append("-DCMAKE_TOOLCHAIN_FILE=" + str(self.sourceDir / "CrossToolchain.cmake"))
        cmdline = colcon_cmd + cmake_args + colcon_args
        if self.config.verbose:
            cmdline.append("--event-handlers")
            cmdline.append("console_cohesion+")
        return self.run_cmd(cmdline, cwd=self.sourceDir, **kwargs)
    
    def update(self):
        super().update()
        if not (self.sourceDir / "src").is_dir():
            self.makedirs(self.sourceDir / "src")
        self._run_vcs()
        self._ignore_packages()
        return

    def configure(self, **kwargs):
        # overriding this method allows creation of CrossToolchain.cmake
        # without actually calling cmake, as super().configure() would do
        if not self.compiling_for_host():
            self.generate_cmake_toolchain_file(self.sourceDir / "CrossToolchain.cmake")
        return

    def compile(self, **kwargs):
        return self._run_colcon(**kwargs)
    
    def install(self, **kwargs):
        # colcon build performs an install, so we override this to make sure
        # super doesn't attemt to install with ninja
        return

    ## consider a 'run' function that sources install/setup.bash and executes a test program?
