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
from ...config.chericonfig import BuildType
from ...config.compilation_targets import CompilationTargets
from ...processutils import set_env
from ...utils import OSInfo


class X11AutotoolsProjectBase(CrossCompileAutotoolsProject):
    do_not_add_to_targets = True
    path_in_rootfs = "/usr/local"  # Always install X11 programs in /usr/local/bin to make X11 forwarding work
    default_build_type = BuildType.DEBUG  # Until we are confident things works
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS

    def __init__(self, config):
        super().__init__(config)
        self.configure_command = self.source_dir / "autogen.sh"


class BuildXorgMacros(X11AutotoolsProjectBase):
    target = "xorg-macros"
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/util/macros.git")


# Like X11AutotoolsProjectBase but also adds xorg-macros as a dependency
class X11AutotoolsProject(X11AutotoolsProjectBase):
    do_not_add_to_targets = True
    dependencies = ["xorg-macros"]

    def setup(self):
        super().setup()
        self.configure_environment["ACLOCAL_PATH"] = BuildXorgMacros.get_install_dir(self) / "share/aclocal"
        # Avoid building documentation
        self.configure_args.extend(["--with-doxygen=no", "--enable-specs=no", "--enable-devel-docs=no"])

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
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/pthread-stubs.git")


class BuildLibXCB(X11AutotoolsProject):
    target = "libxcb"
    dependencies = ["xcbproto", "libxau", "xorg-pthread-stubs"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb.git")


class BuildLibXCBUtil(X11AutotoolsProject):
    target = "libxcb-util"
    dependencies = ["libxcb"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-util.git")


class BuildLibXCBWM(X11AutotoolsProject):
    target = "libxcb-wm"
    dependencies = ["libxcb"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-wm.git")


class BuildLibXCBImage(X11AutotoolsProject):
    target = "libxcb-image"
    dependencies = ["libxcb-util"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-image.git")


class BuildLibXCBRenderUtil(X11AutotoolsProject):
    target = "libxcb-render-util"
    dependencies = ["libxcb"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-render-util.git")


class BuildLibXCBCursor(X11AutotoolsProject):
    target = "libxcb-cursor"
    dependencies = ["libxcb-render-util", "libxcb-image"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-cursor.git")

    def setup(self):
        super().setup()
        if not self.compiling_for_host():
            # Various underaligned capabilities in packed structs, hopefully not a problem at runtime
            self.cross_warning_flags += ["-Wno-error=cheri-capability-misuse"]


class BuildLibXCBKeysyms(X11AutotoolsProject):
    target = "libxcb-keysyms"
    dependencies = ["xorgproto"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxcb-keysyms.git")


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
        # TODO: disable locale support to speed things up?
        # self.configure_args.extend(["--disable-xlocale", "--disable-xlocaledir"])
        if not self.compiling_for_host():
            # The build system gets confused when cross-compiling from macOS, tell it we don't want launchd support.
            self.configure_args.append("--without-launchd")
            # A few warnings in xlibi18n that don't affect correct execution. Fixing them would require
            # using uintptr_t and there currently isn't a typedef for that in libX11.
            self.cross_warning_flags += ["-Wno-error=cheri-capability-misuse"]


class BuildLibXext(X11AutotoolsProject):
    target = "libxext"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxext.git")


class BuildLibXfixes(X11AutotoolsProject):
    target = "libxfixes"
    dependencies = ["libx11"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxfixes.git")


class BuildLibXi(X11AutotoolsProject):
    target = "libxi"
    dependencies = ["libxext", "libxfixes"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/lib/libxi.git")


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


class BuildXEyes(X11AutotoolsProject):
    target = "xeyes"
    dependencies = ["libxi", "libxmu", "libxrender"]
    repository = GitRepository("https://gitlab.freedesktop.org/xorg/app/xeyes.git")


class BuildLibXKBCommon(CrossCompileMesonProject):
    target = "libxkbcommon"
    dependencies = ["libx11"]
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    repository = GitRepository("https://github.com/xkbcommon/libxkbcommon.git")

    def setup(self):
        # avoid wayland dep for now
        super().setup()
        self.configure_args.append("-Denable-wayland=false")
        # Don't build docs with Doxygen
        self.configure_args.append("-Denable-docs=false")
        # Avoid libxml2 dep
        self.configure_args.append("-Denable-xkbregistry=false")

    def process(self):
        newpath = os.getenv("PATH")
        if OSInfo.IS_MAC:
            # /usr/bin/bison on macOS is not compatible with this build system
            newpath = str(self.get_homebrew_prefix("bison")) + "/bin:" + newpath
        with set_env(PATH=newpath):
            super().process()
