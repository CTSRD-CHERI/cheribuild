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
import shutil
import os

from .crosscompileproject import CrossCompileCMakeProject, DefaultInstallDir, GitRepository


class BuildRos2(CrossCompileCMakeProject):
    project_name = "ros2"
    repository = GitRepository("https://github.com/dodsonmg/ros2_dashing_minimal.git", default_branch="master", force_branch=True)

    # atm, we build and install in the sourceDir.
    build_in_source_dir = True
    # it may eventually be useful to install to rootfs or sysroot depending on whether we want to use ROS2
    # as a library for building other applications using cheribuild
    # therefore, the _install_dir doesn't do anything, but cheribuild requires them
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS
    dependencies = ["poco"]
    _extra_git_clean_excludes = ["--exclude=src"]  # don't delete src/ when running clean

    def _ignore_packages(self):
        packages = ["src/ros2/rcl_logging/rcl_logging_log4cxx"]  # relative to self.sourceDir
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

    def _get_poco(self, **kwargs):
        # find and copy libPocoFoundation.so.71 from the sysroot into self.sourceDir
        # this is a bit ugly, but allows us to link the poco library whether we're running
        # hybrid or purecap cheribsd.  this is helpful because if we're running hybrid cheribsd,
        # the hybrid rootfs gets mounted, which doesn't include the purecap build of the poco library.
        #
        # one day, if we're only running the purecap kernel, we can just append the path
        # of libPocoFoundation.so.71 to LD_CHERI_LIBRARY_PATH in _set_env() below.
        poco_path = self.target_info.sysroot_install_prefix_absolute / "lib/libPocoUtil.so.71"
        if poco_path.is_file():
            self.info("Found pocofoundation:", poco_path)
            self.installFile(poco_path, self.sourceDir / poco_path.name, force=True, print_verbose_only=False)
        else:
            self.fatal("libPocoFoundation.so.71 cannot be found at expected path", poco_path)

    def _set_env(self, **kwargs):
        # create cheri_setup.csh and cheri_setup.sh files in self.sourceDir which can be source'ed
        # to set environment variables (primarily LD_CHERI_LIBRARY_PATH)
        #
        # based off the install/setup.bash file sourced for ubuntu installs

        # source the setup script created by ROS to set LD_LIBRARY_PATH
        setup_script = self.sourceDir / "install" / "setup.bash"
        if not setup_script.is_file():
            self.warning("No setup.bash file to source.")
            return
        cmdline = shlex.split("bash -c 'source " + str(setup_script) + " && echo $LD_LIBRARY_PATH'")
        output = self.run_cmd(cmdline, cwd=self.sourceDir, captureOutput=True, print_verbose_only=False, **kwargs)

        # extract LD_LIBRARY_PATH into a variable
        LD_LIBRARY_PATH = output.stdout.decode("utf-8").rstrip()
        if len(LD_LIBRARY_PATH) == 0:
            self.warning("LD_LIBRARY_PATH not set.")
            return

        # convert LD_LIBRARY_PATH into LD_CHERI_LIBRARY_PATH for CheriBSD
        LD_LIBRARY_PATH = str(self.sourceDir) + ":" + LD_LIBRARY_PATH
        LD_LIBRARY_PATH = LD_LIBRARY_PATH.replace(str(self.sourceDir), "${rootdir}")
        LD_CHERI_LIBRARY_PATH = LD_LIBRARY_PATH
        LD_CHERI_LIBRARY_PATH += ":${LD_CHERI_LIBRARY_PATH}"
        LD_LIBRARY_PATH += ":${LD_LIBRARY_PATH}"

        # write LD_CHERI_LIBRARY_PATH to a text file to source from csh in CheriBSD
        csh_script = """#!/bin/csh
set rootdir=`pwd`
# csh doesn't like undefined variables
if (! $?LD_CHERI_LIBRARY_PATH ) then
  set LD_CHERI_LIBRARY_PATH=""
endif
setenv LD_CHERI_LIBRARY_PATH {LD_CHERI_LIBRARY_PATH}
if (! $?LD_LIBRARY_PATH ) then
  set LD_LIBRARY_PATH=""
endif
setenv LD_LIBRARY_PATH {LD_LIBRARY_PATH}
""".format(LD_CHERI_LIBRARY_PATH=LD_CHERI_LIBRARY_PATH, LD_LIBRARY_PATH=LD_LIBRARY_PATH)
        self.writeFile(self.sourceDir / 'cheri_setup.csh', csh_script, overwrite=True)
        posix_sh_script = """#!/bin/sh
rootdir=`pwd`
export LD_CHERI_LIBRARY_PATH={LD_CHERI_LIBRARY_PATH}
export LD_LIBRARY_PATH={LD_LIBRARY_PATH}
""".format(LD_CHERI_LIBRARY_PATH=LD_CHERI_LIBRARY_PATH, LD_LIBRARY_PATH=LD_LIBRARY_PATH)
        # write LD_CHERI_LIBRARY_PATH to a text file to source from sh in CheriBSD
        self.writeFile(self.sourceDir / 'cheri_setup.sh', posix_sh_script, overwrite=True)

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

        # call the functions to copy the poco library and create an env setup file
        if not self.compiling_for_host():
            self._get_poco(**kwargs)
            self._set_env(**kwargs)

    def run_tests(self):
        # only test when not compiling for host
        if not self.compiling_for_host():
            self.run_cheribsd_test_script("run_ros2_tests.py", mount_sourcedir=True, mount_sysroot=True)
