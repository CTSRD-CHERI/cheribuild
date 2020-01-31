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

from ..project import *
from ...utils import IS_MAC


def opensbi_install_dir(config: CheriConfig, project: SimpleProject):
    dir_name = project.crosscompile_target.cpu_architecture.value
    if project.crosscompile_target.is_cheri_purecap():
        dir_name += "c"
    return config.cheri_sdk_dir / "opensbi" / dir_name


class BuildOpenSBI(Project):
    target = "opensbi"
    repository = GitRepository("https://github.com/CTSRD-CHERI/opensbi")
    default_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR
    default_build_type = BuildType.RELWITHDEBINFO
    supported_architectures = [CompilationTargets.BAREMETAL_NEWLIB_RISCV64]
    make_kind = MakeCommandKind.GnuMake
    _always_add_suffixed_targets = True
    _default_install_dir_fn = ComputedDefaultValue(function=opensbi_install_dir,
                                                   as_string="$SDK_ROOT/opensbi/riscv{32,64}{c,}")

    def __init__(self, config):
        super().__init__(config)
        self.addRequiredSystemTool("dtc", homebrew="dtc")
        if IS_MAC:
            self.addRequiredSystemTool("greadlink", homebrew="coreutils")
            self.make_args.set(READLINK="greadlink")

    def setup(self):
        super().setup()
        compflags = " " + commandline_to_str(self.target_info.essential_compiler_and_linker_flags)
        compflags += " -Qunused-arguments"  # -mstrict-align -no-pie
        self.make_args.set(PLATFORM="qemu/virt",
            O=self.buildDir,  # output dir
            I=self.installDir,  # install dir
            CROSS_COMPILE=str(self.sdk_bindir) + "/",
            CC=str(self.CC) + compflags,
            CXX=str(self.CXX) + compflags,
            CPP=str(self.CPP) + compflags,
            LD=self.target_info.linker,
            AR=self.sdk_bindir / "llvm-ar",
            OBJCOPY=self.sdk_bindir / "llvm-objcopy",
            LD_IS_LLD=True,
            FW_OPTIONS="0x2",  # Debug output enabled for now
            # FW_JUMP_ADDR= ## cheribsd start addr
            # FW_JUMP_FDT_ADDR= ## cheribsd fdt addr
            #
        )
        if self.config.verbose:
            self.make_args.set(V=True)

    def compile(self, **kwargs):
        self.runMake(parallel=False, cwd=self.sourceDir)

    def install(self, **kwargs):
        self.makedirs(self.installDir)
        self.runMakeInstall(cwd=self.sourceDir)

    @property
    def _fw_jump_path(self) -> str:
        return "platform/qemu/virt/firmware/fw_jump.elf"

    def get_nocap_bios(self, caller) -> Path:
        return self.getInstallDir(caller, cross_target=CompilationTargets.BAREMETAL_NEWLIB_RISCV64) / self._fw_jump_path

    def get_purecap_bios(self, caller):
        return self.get_instance(caller, cross_target=CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP) / self._fw_jump_path
