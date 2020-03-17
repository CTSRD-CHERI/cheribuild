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
from ...utils import IS_MAC, classproperty


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
    supported_architectures = [CompilationTargets.BAREMETAL_NEWLIB_RISCV64,
                               CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP]
    make_kind = MakeCommandKind.GnuMake
    _always_add_suffixed_targets = True
    _default_install_dir_fn = ComputedDefaultValue(function=opensbi_install_dir,
                                                   as_string="$SDK_ROOT/opensbi/riscv{32,64}{c,}")

    @classproperty
    def needs_sysroot(cls):
        return False  # we can build without a sysroot

    def __init__(self, config):
        super().__init__(config)
        self.addRequiredSystemTool("dtc", apt="device-tree-compiler", homebrew="dtc")
        if IS_MAC:
            self.addRequiredSystemTool("greadlink", homebrew="coreutils")
            self.make_args.set(READLINK="greadlink")

    def setup(self):
        super().setup()
        compflags = " " + commandline_to_str(self.target_info.essential_compiler_and_linker_flags)
        compflags += " -Qunused-arguments"  # -mstrict-align -no-pie
        self.make_args.set(
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

    @property
    def all_platforms(self):
        platforms_dir = self.sourceDir / "platform"
        self.info(list(platforms_dir.glob("**/config.mk")))
        all_platforms = []
        for c in platforms_dir.glob("**/config.mk"):
            relpath = str(c.parent.relative_to(platforms_dir))
            print(relpath)
            if relpath != "template":
                all_platforms.append(relpath)
        if "qemu/virt" not in all_platforms:
            self.fatal("qemu/virt platform missing?")
        return all_platforms

    def compile(self, **kwargs):
        for platform in self.all_platforms:
            args = self.make_args.copy()
            args.set(PLATFORM=platform)
            self.run_make(parallel=False, cwd=self.sourceDir, options=args)

    def install(self, **kwargs):
        self.makedirs(self.installDir)
        for platform in self.all_platforms:
            args = self.make_args.copy()
            args.set(PLATFORM=platform)
            self.runMakeInstall(cwd=self.sourceDir, options=args)

    @staticmethod
    def _fw_jump_path() -> str:
        return "platform/qemu/virt/firmware/fw_jump.elf"

    @classmethod
    def get_nocap_instance(cls, caller, cpu_arch=CPUArchitecture.RISCV64) -> "BuildOpenSBI":
        assert cpu_arch == CPUArchitecture.RISCV64, "RISCV32 not supported yet"
        return cls.get_instance(caller, cross_target=CompilationTargets.BAREMETAL_NEWLIB_RISCV64)

    @classmethod
    def get_purecap_instance(cls, caller, cpu_arch=CPUArchitecture.RISCV64) -> "BuildOpenSBI":
        assert cpu_arch == CPUArchitecture.RISCV64, "RISCV32 not supported yet"
        return cls.get_instance(caller, cross_target=CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP)

    @classmethod
    def get_nocap_bios(cls, caller) -> Path:
        return cls.get_nocap_instance(caller).installDir / cls._fw_jump_path()

    @classmethod
    def get_purecap_bios(cls, caller):
        return cls.get_purecap_instance(caller).installDir / cls._fw_jump_path()
