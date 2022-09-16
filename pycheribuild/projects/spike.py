#
# Copyright (c) 2020 Alex Richardson
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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
import sys

from .cross.bbl import BuildBBLNoPayload
from .cross.cheribsd import BuildCheriBsdMfsKernel, ConfigPlatform
from .project import AutotoolsProject, BuildType, CheriConfig, DefaultInstallDir, GitRepository, MakeCommandKind
from .simple_project import SimpleProject
from ..config.compilation_targets import CompilationTargets


class BuildCheriSpike(AutotoolsProject):
    target = "spike"
    repository = GitRepository("https://github.com/CTSRD-CHERI/riscv-isa-sim",
                               default_branch="cheri", force_branch=True)
    native_install_dir = DefaultInstallDir.CHERI_SDK
    default_build_type = BuildType.RELEASE
    lto_by_default = True
    prefer_full_lto_over_thin_lto = True
    lto_set_ld = False
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config):
        super().__init__(config)
        self.add_required_system_tool("dtc", apt="device-tree-compiler", homebrew="dtc")

    def setup(self):
        super().setup()
        self.configure_args.append("--enable-cheri")
        self.configure_args.append("--disable-rvfi-dii")
        # We have to pass LDFLAGS as part of CC/CXX since the build system is dumb.
        common_flags = self.default_compiler_flags + self.default_ldflags
        self.configure_environment["CC"] = self.commandline_to_str([self.CC] + common_flags + self.CFLAGS)
        self.configure_environment["CXX"] = self.commandline_to_str([self.CXX] + common_flags + self.CXXFLAGS)

    @classmethod
    def get_simulator_binary(cls, caller):
        return cls.get_install_dir(caller, cross_target=CompilationTargets.NATIVE) / "bin/spike"


class RunCheriSpikeBase(SimpleProject):
    do_not_add_to_targets = True
    _bbl_xtarget = CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP
    _bbl_class = BuildBBLNoPayload.get_class_for_target(_bbl_xtarget)
    _source_class = None

    @classmethod
    def dependencies(cls, _: CheriConfig) -> "list[str]":
        return [cls._source_class.target, cls._bbl_class.target, BuildCheriSpike.target]

    def process(self):
        kernel_project = self._source_class.get_instance(self)
        kernel_config = kernel_project.default_kernel_config(ConfigPlatform.QEMU)
        kernel = kernel_project.get_kernel_install_path(kernel_config)
        # We always want output even with --quiet
        self.run_cmd([BuildCheriSpike.get_simulator_binary(self), "+payload=" + str(kernel),
                      self._bbl_class.get_installed_kernel_path(self, cross_target=self._bbl_xtarget)],
                     give_tty_control=True, stdout=sys.stdout, stderr=sys.stderr)


class RunCheriBsdSpike(RunCheriSpikeBase):
    target = "run-spike"
    _source_class = BuildCheriBsdMfsKernel
    supported_architectures = [CompilationTargets.CHERIBSD_RISCV_PURECAP, CompilationTargets.CHERIBSD_RISCV_NO_CHERI,
                               CompilationTargets.CHERIBSD_RISCV_HYBRID]
