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
import typing
from pathlib import Path

from .cheritrace import BuildCheriTrace
from .project import CheriConfig, DefaultInstallDir, GitRepository, MakeCommandKind, Project
from ..utils import OSInfo, ThreadJoiner


def gnustep_install_instructions():
    if OSInfo.IS_FREEBSD:
        return "Try running `pkg install gnustep-make gnustep-gui` or `cheribuild.py gnustep` to build from source"
    if OSInfo.IS_LINUX:
        return ("Try running `cheribuild.py gnustep`. It might also be possible to use distribution packages but they"
                " will probably be too old.")
        # packaged versions don't seem to work
        #     osRelease = parseOSRelease()
        #     print(osRelease)
        #     if osRelease["ID"] == "ubuntu":
        #         return """Somehow install GNUStep"""
        #     elif osRelease["ID"] == "opensuse":
        #         return """Try installing gnustep-make from the X11:/GNUstep project:
        # sudo zypper addrepo http://download.opensuse.org/repositories/X11:/GNUstep/openSUSE_{OPENSUSE_VERSION}/
        # gnustep
        # sudo zypper in libobjc2-devel gnustep-make gnustep-gui-devel gnustep-base-devel""".format(
        # OPENSUSE_VERSION=osRelease["VERSION"])


class BuildCheriVis(Project):
    repository = GitRepository("https://github.com/CTSRD-CHERI/CheriVis.git")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    # dependencies = ["cheritrace"]
    if OSInfo.IS_MAC:
        build_in_source_dir = True
        make_kind = MakeCommandKind.CustomMakeTool
    else:
        dependencies = ["gnustep"]
        make_kind = MakeCommandKind.GnuMake

    # TODO: allow external cheritrace
    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.add_required_system_tool("clang")
        self.add_required_system_tool("clang++")
        if OSInfo.IS_LINUX or OSInfo.IS_FREEBSD:
            self.add_required_system_tool("gnustep-config", install_instructions=gnustep_install_instructions)
        self.gnustep_makefiles_dir = None  # type: typing.Optional[Path]
        if OSInfo.IS_MAC:
            self.make_args.set_command("xcodebuild", can_pass_j_flag=False,
                                       install_instructions="Install Command Line Tools")
            assert self.make_args.kind == MakeCommandKind.CustomMakeTool
        print("command = ", self.make_args.command)

        self.cheritrace_path = None
        # Build Cheritrace as a subproject
        self.cheritrace_subproject = BuildCheriTrace(config)
        self.cheritrace_subproject.source_dir = self.source_dir / "cheritrace"
        self.cheritrace_subproject.build_dir = self.source_dir / "cheritrace/Build"
        self.cheritrace_subproject._install_dir = "/this/path/does/not/exist"

    def check_system_dependencies(self):
        super().check_system_dependencies()
        self.cheritrace_subproject.check_system_dependencies()

        # expectedCheritraceLib = str(self.config.cheri_sdk_dir / "lib/libcheritrace.a")
        # cheritraceLib = Path(os.getenv("CHERITRACE_LIB") or expectedCheritraceLib)
        # if not cheritraceLib.exists():
        #     self.fatal(cheritraceLib, "does not exist", fixit_hint="Try running `cheribuild.py cheritrace` and if
        #     that"
        #                " doesn't work set the environment variable CHERITRACE_LIB to point to libcheritrace.so")
        #     return
        # self.cheritrace_path = cheritraceLib
        if OSInfo.IS_MAC:
            return  # don't need GnuStep here

        config_output = self.run_cmd("gnustep-config", "--variable=GNUSTEP_MAKEFILES", capture_output=True).stdout
        self.gnustep_makefiles_dir = Path(config_output.decode("utf-8").strip())
        common_dot_make = self.gnustep_makefiles_dir / "common.make"
        if not common_dot_make.is_file():
            self.dependency_error("gnustep-config binary exists, but", common_dot_make, "does not exist!",
                                  install_instructions=gnustep_install_instructions())
        # TODO: set ADDITIONAL_LIB_DIRS?
        # http://www.gnustep.org/resources/documentation/Developer/Make/Manual/gnustep-make_1.html#SEC17
        # http://www.gnustep.org/resources/documentation/Developer/Make/Manual/gnustep-make_1.html#SEC29

        # library combos:
        # http://www.gnustep.org/resources/documentation/Developer/Make/Manual/gnustep-make_1.html#SEC35

        # has to be a relative path for some reason....
        # pathlib.relative_to() won't work if the prefix is not the same...
        # cheritrace_rel_path = os.path.relpath(str(self.cheritrace_path.parent.resolve()),
        # str(self.source_dir.resolve()))
        self.make_args.set(CXX=self.CXX,
                           CC=self.CPP,
                           GNUSTEP_MAKEFILES=self.gnustep_makefiles_dir,
                           # Uncomment this to enable building with an install libchertrace
                           # CHERITRACE_DIR=cheritrace_rel_path,  # make it find the cheritrace library
                           # GNUSTEP_INSTALLATION_DOMAIN="USER",
                           GNUSTEP_INSTALLATION_DOMAIN="SYSTEM",
                           GNUSTEP_NG_ARC=1,
                           messages="yes")

    def clean(self):
        # doesn't seem to be possible to use a out of source build
        self.run_make("clean", cwd=self.source_dir)
        self.clean_directory(self.cheritrace_subproject.build_dir)
        return ThreadJoiner(None)  # can't be done async

    def compile(self, **kwargs):
        # First build the bundled cheritrace
        assert self.cheritrace_subproject.source_dir == self.source_dir / "cheritrace"
        assert self.cheritrace_subproject.build_dir == self.source_dir / "cheritrace/Build"
        assert self.cheritrace_subproject.install_dir == "/this/path/does/not/exist", \
            self.cheritrace_subproject.install_dir
        self.makedirs(self.cheritrace_subproject.build_dir)
        self.cheritrace_subproject.setup()
        self.cheritrace_subproject.configure()
        self.cheritrace_subproject.compile()
        if OSInfo.IS_MAC:
            self.run_make(cwd=self.source_dir)
        else:
            self.run_make("print-gnustep-make-help", cwd=self.source_dir)
            self.run_make("all", cwd=self.source_dir)

    def install(self, **kwargs):
        if OSInfo.IS_MAC:
            # TODO: xcodebuild install?
            self.run_cmd("cp", "-aRv", self.source_dir / "build/Release/CheriVis.app", self.config.cheri_sdk_dir)
        else:
            self.run_make("install", cwd=self.source_dir)


#
# Some of these settings seem required:
"""
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>GSAllowWindowsOverIcons</key>
    <integer>1</integer>
    <key>GSAppOwnsMiniwindow</key>
    <integer>0</integer>
    <key>GSBackHandlesWindowDecorations</key>
    <integer>0</integer>
    <key>GSUseFreedesktopThumbnails</key>
    <integer>1</integer>
    <key>GraphicCompositing</key>
    <integer>1</integer>
    <key>NSInterfaceStyleDefault</key>
    <string>NSWindows95InterfaceStyle</string>
    <key>NSMenuInterfaceStyle</key>
    <string>NSWindows95InterfaceStyle</string>
</dict>
</plist>
"""
#
