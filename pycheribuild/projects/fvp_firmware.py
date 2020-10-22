#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2020 Alex Richardson
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

from .cross.crosscompileproject import CrossCompileMakefileProject
from .project import DefaultInstallDir, GitRepository
from ..config.chericonfig import BuildType
from ..config.compilation_targets import CompilationTargets
from ..utils import OSInfo


class MorelloFirmwareBase(CrossCompileMakefileProject):
    do_not_add_to_targets = True
    supported_architectures = [CompilationTargets.MORELLO_BAREMETAL_HYBRID]
    cross_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY  # TODO: install it
    needs_sysroot = False  # We don't need a complete sysroot
    default_build_type = BuildType.DEBUG  # TODO: release once it works

    @property
    def optimization_flags(self):
        return []  # These projects won't build at -O0 (since it's too big), just use the default


class BuildMorelloScpFirmware(MorelloFirmwareBase):
    repository = GitRepository("git@git.morello-project.org:morello/scp-firmware.git")
    project_name = "morello-scp-firmware"
    supported_architectures = [CompilationTargets.ARM_NONE_EABI]
    cross_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR

    def setup(self):
        super().setup()
        self.make_args.set(PRODUCT="morello",
                           MODE="debug" if self.build_type.is_debug else "release",
                           LOG_LEVEL="TRACE" if self.build_type.is_debug else "INFO",  # TODO: change it to warn
                           V="y")
        # Build system tries to use macos tool which won't work
        self.make_args.set(
            AR=self.target_info.ar,
            OBJCOPY=self.CC.with_name(self.CC.name.replace("gcc", "objcopy")),
            SIZE=self.CC.with_name(self.CC.name.replace("gcc", "size")),
            )

    def install(self, **kwargs):
        pass  # TODO: implement

    def run_tests(self):
        self.run_make(make_target="test")  # XXX: doesn't work yet, needs a read/write/isatty()


class BuildMorelloTrustedFirmware(MorelloFirmwareBase):
    target = "morello-trusted-firmware"
    project_name = "morello-trusted-firmware-a"
    repository = GitRepository("git@git.morello-project.org:morello/trusted-firmware-a.git")
    set_commands_on_cmdline = True  # Need to override this on the command line since the makefile uses :=
    default_build_type = BuildType.RELEASE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_required_system_tool("dtc", homebrew="dtc", apt="dtc")

    def setup(self):
        super().setup()
        self.make_args.set(ENABLE_MORELLO_CAP=1, PLAT="morello", ARCH="aarch64",
                           DEBUG=1 if self.build_type.is_debug else 0,
                           E=0,  # disable -Werror since there are some unused functions
                           V=1,  # verbose
                           )
        self.make_args.set_env(CROSS_COMPILE=str(self.sdk_bindir) + "/")
        # Need to override this on the command line, not just the environment)
        self.make_args.set(LD=self.target_info.linker,
                           LINKER=self.target_info.linker)
        # Uses raw linker -> don't set LDFLAGS
        self.make_args.set_env(LDFLAGS="-verbose")
        self.make_args.set(HOSTCC=self.host_CC)

    def compile(self, **kwargs):
        self.run_make(make_target="all", cwd=self.source_dir)
        fip_make = self.make_args.copy()
        fip_make.set_env(CFLAGS="", CPPFLAGS="", CXXFLAGS="")
        if OSInfo.IS_MAC:
            # TODO: should handle non-homebrew too
            fip_make.set_env(HOSTLDFLAGS="-L/usr/local/opt/openssl@1.1/lib",
                             HOSTCCFLAGS="-I/usr/local/opt/openssl@1.1/include",
                             CPPFLAGS="-I/usr/local/opt/openssl@1.1/include")
            # FIXME: Makefile doesn't add HOSTLDFLAGS
            fip_make.set(HOSTCC=str(self.host_CC) + " " + fip_make.env_vars["HOSTLDFLAGS"])
        self.run_make(make_target="all", cwd=self.source_dir / "tools/fiptool", options=fip_make)

    def install(self, **kwargs):
        pass  # TODO: implement
