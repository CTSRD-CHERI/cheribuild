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

from .crosscompileproject import CrossCompileCMakeProject, DefaultInstallDir, GitRepository
from ...utils import InstallInstructions


class BuildRos2(CrossCompileCMakeProject):
    target = "ros2"
    repository = GitRepository("https://github.com/dodsonmg/ros2_dashing_minimal.git", default_branch="master",
                               force_branch=True)
    # it may eventually be useful to install to rootfs or sysroot depending on whether we want to use ROS2
    # as a library for building other applications using cheribuild
    # therefore, the _install_dir doesn't do anything, but cheribuild requires them
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    dependencies = ["poco"]

    def _ignore_packages(self):
        packages = ["src/ros2/rcl_logging/rcl_logging_log4cxx"]  # relative to self.source_dir
        for package in packages:
            cmdline = ["touch", str(self.source_dir / package / "COLCON_IGNORE")]
            self.run_cmd(cmdline, cwd=self.source_dir)

    def _run_vcs(self):
        # this is the meta version control system used by ros for downloading and unpacking repos
        if not shutil.which("vcs"):
            self.dependency_error("Missing vcs command",
                                  install_instructions=InstallInstructions("pip3 install --user vcstool"))
        cmdline = ["vcs", "import", "--input", "ros2_minimal.repos", "src"]
        self.run_cmd(cmdline, cwd=self.source_dir)

    def _run_colcon(self, **kwargs):
        # colcon is the meta build system (on top of cmake) used by ros
        if not shutil.which("colcon"):
            self.dependency_error("Missing colcon command",
                                  install_instructions=InstallInstructions(
                                      "pip3 install --user colcon-common-extensions"))
        colcon_cmd = ["colcon", "build",
                      "--install-base", self.install_dir,
                      "--build-base", self.build_dir,
                      "--merge-install"]
        colcon_args = ["--no-warn-unused-cli", "--packages-skip-build-finished"]
        cmake_args = ["--cmake-args", "-DBUILD_TESTING=NO"]
        if not self.compiling_for_host():
            cmake_args.append("-DCMAKE_TOOLCHAIN_FILE=" + str(self.source_dir / "CrossToolchain.cmake"))
        cmdline = colcon_cmd + cmake_args + colcon_args
        if self.config.verbose:
            cmdline.append("--event-handlers")
            cmdline.append("console_cohesion+")
        cmdline.extend(["--parallel-workers", str(self.config.make_jobs)])
        self.run_cmd(cmdline, cwd=self.source_dir, **kwargs)

    def clean(self):
        self.clean_directory(self.source_dir / "install")
        self.clean_directory(self.source_dir / "build")
        return super().clean()

    @property
    def cmake_prefix_paths(self):
        return super().cmake_prefix_paths + [self.install_dir]

    def _set_env(self):
        # create cheri_setup.csh and cheri_setup.sh files in self.source_dir which can be source'ed
        # to set environment variables (primarily LD_CHERI_LIBRARY_PATH)
        #
        # based off the install/setup.bash file sourced for ubuntu installs

        # source the setup script created by ROS to set LD_LIBRARY_PATH
        setup_script = self.install_dir / "setup.bash"
        if not setup_script.is_file():
            self.warning("No setup.bash file to source.")
            return
        cmdline = shlex.split("bash -c 'source " + str(setup_script) + " && echo $LD_LIBRARY_PATH'")
        output = self.run_cmd(cmdline, cwd=self.source_dir, capture_output=True, print_verbose_only=False)

        # extract LD_LIBRARY_PATH into a variable
        ld_library_path = output.stdout.decode("utf-8").rstrip()
        if len(ld_library_path) == 0:
            self.warning("LD_LIBRARY_PATH not set.")
            return

        # add Poco libraries to LD_LIBRARY_PATH
        poco_path = self.target_info.sysroot_install_prefix_absolute / "lib"
        if (poco_path / "libPocoFoundation.so.71").is_file():
            self.info("Found libPocoFoundation.so.71 in the expected path: ", poco_path)
            ld_library_path = ld_library_path + ":" + str(poco_path)
        else:
            self.fatal("libPocoFoundation.so.71 was not found in the expected path: ", poco_path)

        # remove the host prefix for the install directory from entries in the
        # LD_LIBRARY_PATH
        host_prefix = str(self.install_dir).split("/opt")[0]
        ld_library_path = ld_library_path.replace(str(host_prefix), "")

        # convert LD_LIBRARY_PATH into LD_CHERI_LIBRARY_PATH for CheriBSD
        ld_cheri_library_path = ld_library_path
        ld_cheri_library_path += ":${LD_CHERI_LIBRARY_PATH}"
        ld_library_path += ":${LD_LIBRARY_PATH}"

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
""".format(LD_CHERI_LIBRARY_PATH=ld_cheri_library_path, LD_LIBRARY_PATH=ld_library_path)
        self.write_file(self.install_dir / 'cheri_setup.csh', csh_script, overwrite=True)
        posix_sh_script = """#!/bin/sh
rootdir=`pwd`
export LD_CHERI_LIBRARY_PATH={LD_CHERI_LIBRARY_PATH}
export LD_LIBRARY_PATH={LD_LIBRARY_PATH}
""".format(LD_CHERI_LIBRARY_PATH=ld_cheri_library_path, LD_LIBRARY_PATH=ld_library_path)
        # write LD_CHERI_LIBRARY_PATH to a text file to source from sh in CheriBSD
        self.write_file(self.install_dir / 'cheri_setup.sh', posix_sh_script, overwrite=True)

    def update(self):
        super().update()
        if not (self.source_dir / "src").is_dir():
            self.makedirs(self.source_dir / "src")
        self._run_vcs()
        self._ignore_packages()

    def configure(self, **kwargs):
        # overriding this method allows creation of CrossToolchain.cmake
        # without actually calling cmake, as super().configure() would do
        if not self.compiling_for_host():
            self.generate_cmake_toolchain_file(self.source_dir / "CrossToolchain.cmake")

    def compile(self, **kwargs):
        self._run_colcon(**kwargs)

    def install(self, **kwargs):
        # colcon build performs an install, so we override this to make sure
        # super doesn't attemt to install with ninja

        # call the functions to copy the poco library and create an env setup file
        if not self.compiling_for_host():
            self._set_env()

    def run_tests(self):
        # only test when not compiling for host
        if not self.compiling_for_host():
            self.target_info.run_cheribsd_test_script("run_ros2_tests.py",
                                                      mount_sourcedir=True,
                                                      mount_installdir=True,
                                                      mount_sysroot=True)
