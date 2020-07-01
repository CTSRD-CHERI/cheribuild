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
import shutil
from pathlib import Path

from .project import (AutotoolsProject, CheriConfig, CMakeProject, DefaultInstallDir, GitRepository,
                      TargetAliasWithDependencies)


# http://wiki.gnustep.org/index.php/GNUstep_under_Ubuntu_Linux


class BuildLibObjC2(CMakeProject):
    repository = GitRepository("https://github.com/gnustep/libobjc2.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configure_args.extend([
            "-DCMAKE_ASM_COMPILER=clang",
            "-DCMAKE_ASM_COMPILER_ID=Clang",  # For some reason CMake doesn't detect the ASM compiler ID for clang
            "-DCMAKE_ASM_FLAGS=-c",  # required according to docs when using clang as ASM compiler
            # "-DLLVM_OPTS=OFF",  # For now don't build the LLVM plugin, it will break when clang is updated
            "-DTESTS=OFF",
            # Don't install in the location that gnustep-config says, it might be a directory that is not writable by
            # the current user:
            "-DGNUSTEP_INSTALL_TYPE=NONE",
            ])
        # TODO: require libdispatch?
        self.add_required_system_tool("clang")
        self.add_required_system_tool("clang++")


# noinspection PyPep8Naming
class BuildGnuStep_Make(AutotoolsProject):
    repository = GitRepository("https://github.com/gnustep/tools-make.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.configure_args.extend([
            "--with-layout=fhs",  # more traditional file system layout
            "--with-library-combo=ng-gnu-gnu",  # use the new libobjc2 that supports ARC
            "--enable-objc-nonfragile-abi",  # not sure if required but given in install guide
            "CC=" + str(self.CC),
            "CXX=" + str(self.CXX),
            "LDFLAGS=-Wl,-rpath," + str(self.install_dir / "lib")  # add rpath, otherwise everything breaks
            ])


# FIXME: do we need to source Makefiles/GNUstep.sh before building?
class GnuStepModule(AutotoolsProject):
    do_not_add_to_targets = True
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    build_in_source_dir = True  # out of source builds don't seem to work!

    def __init__(self, config: CheriConfig, module_name: str):
        self.repository = GitRepository("https://github.com/gnustep/libs-" + module_name + ".git")
        super().__init__(config)
        self.add_required_pkg_config("gnutls")
        # Ubuntu puts libtiff-4 is in libtiff5-dev...
        self.add_required_pkg_config("libtiff-4", apt="libtiff5-dev")
        self.add_required_pkg_config("freetype2", apt="libfreetype6-dev")

    def configure(self):
        if not shutil.which("gnustep-config"):
            self.dependency_error("gnustep-config should have been installed in the last build step!")
            gnustep_libdir = Path("/invalid/path")
        else:
            gnustep_libdir = self.run_cmd("gnustep-config", "--variable=GNUSTEP_SYSTEM_LIBRARIES",
                                          capture_output=True, print_verbose_only=True,
                                          run_in_pretend_mode=True).stdout.strip().decode("utf-8")
        # Just to confirm that we have set up the -rpath flag correctly
        expected_libdir = self.install_dir / "lib"
        if not expected_libdir.is_dir():
            self.fatal("Expected gnustep libdir", expected_libdir, "doesn't exist")
        if not Path(gnustep_libdir).is_dir():
            self.fatal("GNUSTEP_SYSTEM_LIBRARIES directory", gnustep_libdir, "doesn't exist")
        if Path(gnustep_libdir).exists() and Path(gnustep_libdir).resolve() != expected_libdir.resolve():
            self.fatal("GNUSTEP_SYSTEM_LIBRARIES was", gnustep_libdir, "but expected ", expected_libdir)

        # print(coloured(AnsiColour.green, "LDFLAGS=-L" + gnustep_libdir))
        # TODO: what about spaces??
        # self.configure_args.append("LDFLAGS=-L" + gnustep_libdir + " -Wl,-rpath," + gnustep_libdir)
        super().configure()


# noinspection PyPep8Naming
class BuildGnuStep_Base(GnuStepModule):
    do_not_add_to_targets = False  # Even though it ends in Base this is not a Base class

    def __init__(self, config: CheriConfig):
        super().__init__(config, module_name="base")
        self.configure_args.extend([
            "--disable-mixedabi",
            # TODO: "--enable-libdispatch",
            # "--with-config-file=" + str(self.install_dir / "etc/GNUStep/GNUStep.conf")
            ])


# noinspection PyPep8Naming
class BuildGnuStep_Gui(GnuStepModule):
    def __init__(self, config: CheriConfig):
        super().__init__(config, module_name="gui")

    def check_system_dependencies(self):
        # TODO check that libjpeg62-devel is not installed on opensuse, must use libjpeg8-devel
        # rpm -q libjpeg62-devel must not return 0
        super().check_system_dependencies()


# noinspection PyPep8Naming
class BuildGnuStep_Back(GnuStepModule):
    def __init__(self, config: CheriConfig):
        super().__init__(config, module_name="back")
        self.configure_args.append("--enable-graphics=cairo")


class BuildGnuStep(TargetAliasWithDependencies):
    target = "gnustep"
    dependencies = ["libobjc2", "gnustep-make", "gnustep-base", "gnustep-gui", "gnustep-back"]
