#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
import os

from .crosscompileproject import CrossCompileAutotoolsProject, CrossCompileMesonProject
from ..project import DefaultInstallDir, GitRepository
from ...config.compilation_targets import CompilationTargets
from ...processutils import set_env
from ...utils import OSInfo


class X11AutotoolsProjectBase(CrossCompileAutotoolsProject):
    do_not_add_to_targets = True
    path_in_rootfs = "/usr/local"  # Always install X11 programs in /usr/local/bin to make X11 forwarding work
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS

    def __init__(self, config):
        super().__init__(config)
        self.configure_command = self.source_dir / "autogen.sh"


class BuildXorgMacros(X11AutotoolsProjectBase):
    target = "xorg-macros"
    project_name = "xorg-macros"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/util/macros.git")


# Like X11AutotoolsProjectBase but also adds xorg-macros as a dependency
class X11AutotoolsProject(X11AutotoolsProjectBase):
    do_not_add_to_targets = True
    dependencies = ["xorg-macros"]

    def setup(self):
        super().setup()
        self.configure_environment["ACLOCAL_PATH"] = BuildXorgMacros.get_install_dir(self) / "share/aclocal"
        # Avoid building documentation
        self.configure_args.append("--with-doxygen=no")
        if not self.compiling_for_host():
            self.configure_args.append("--with-sysroot=" + str(self.sdk_sysroot))
            # Needed for many of the projects but not all of them:
            self.configure_args.append("--enable-malloc0returnsnull")


class BuildXCBProto(X11AutotoolsProject):
    target = "xcbproto"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/proto/xcbproto.git")


class BuildXorgProto(X11AutotoolsProject):
    target = "xorgproto"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/proto/xorgproto.git")


class BuildLibXau(X11AutotoolsProject):
    target = "libxau"
    dependencies = ["xorgproto", "xorg-macros"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxau.git")


class BuildLibXCBPthreadStubs(X11AutotoolsProject):
    target = "xorg-pthread-stubs"
    project_name = "xorg-pthread-stubs"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/pthread-stubs.git")


class BuildLibXCB(X11AutotoolsProject):
    target = "libxcb"
    dependencies = ["xcbproto", "libxau", "xorg-pthread-stubs"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb.git")


class BuildLibXTrans(X11AutotoolsProject):
    target = "libxtrans"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxtrans.git")


class BuildLibX11(X11AutotoolsProject):
    target = "libx11"
    dependencies = ["xorgproto", "libxcb", "libxtrans"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libx11.git")

    # pkg-config doesn't handle --sysroot very well, specify the path explicitly
    def setup(self):
        super().setup()
        self.configure_args.append("--with-keysymdefdir=" + str(self.install_dir / "include/X11"))
        if not self.compiling_for_host():
            # The build system gets confused when cross-compiling from macOS, tell it we don't want launchd support.
            self.configure_args.append("--without-launchd")
            # Lots of CHERI warnings in xlibi18n (hopefully we don't need that code)
            self.cross_warning_flags += ["-Wno-error=cheri-capability-misuse"]


class BuildLibXext(X11AutotoolsProject):
    target = "libxext"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxext.git")


class BuildLibXrender(X11AutotoolsProject):
    target = "libxrender"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxrender.git")


class BuildLibXrandr(X11AutotoolsProject):
    target = "libxrandr"
    dependencies = ["libxext", "libxrender"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxrandr.git")


# One of the simplest programs:
class BuildXEv(X11AutotoolsProject):
    target = "xev"
    dependencies = ["libxrandr"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xev.git")


class BuildLibSM(X11AutotoolsProject):
    target = "libsm"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libsm.git")


class BuildLibIce(X11AutotoolsProject):
    target = "libice"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libice.git")

    def setup(self):
        super().setup()
        # TODO: fix the source code instead
        self.cross_warning_flags.append("-Wno-error=format")  # otherwise configure does not detect asprintf


class BuildLibXt(X11AutotoolsProject):
    target = "libxt"
    dependencies = ["libice", "libsm"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxt.git")

    def setup(self):
        super().setup()
        # TODO: fix the source code instead
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


class BuildLibXmu(X11AutotoolsProject):
    target = "libxmu"
    dependencies = ["libxext", "libxrender", "libxt"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxmu.git")

    def setup(self):
        super().setup()
        # TODO: fix the source code instead
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")


class BuildXHost(X11AutotoolsProject):
    target = "xhost"
    dependencies = ["libxau", "libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xhost.git")


class BuildXAuth(X11AutotoolsProject):
    target = "xauth"
    dependencies = ["libx11", "libxau", "libxext", "libxmu", "xorgproto"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xauth")


class BuildLibXKBCommon(CrossCompileMesonProject):
    target = "libxkbcommon"
    cross_install_dir = DefaultInstallDir.ROOTFS_LOCALBASE
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    repository = GitRepository("https://github.com/xkbcommon/libxkbcommon.git")

    def setup(self):
        # avoid wayland dep for now
        super().setup()
        self.configure_args.append("-Denable-wayland=false")
        # Don't build docs with Doxygen
        self.configure_args.append("-Denable-docs=false")

    def process(self):
        newpath = os.getenv("PATH")
        if OSInfo.IS_MAC:
            # /usr/bin/bison on macOS is not compatible with this build system
            try:
                prefix = self.run_cmd("brew", "--prefix", "bison", capture_output=True, run_in_pretend_mode=True,
                                      print_verbose_only=True).stdout.decode("utf-8").strip()
                newpath = prefix + "/bin:" + newpath
            except Exception as e:
                self.fatal("Could not find a compatible bison version:", e, fixit_hint="brew install bison")
        with set_env(PATH=newpath):
            super().process()
