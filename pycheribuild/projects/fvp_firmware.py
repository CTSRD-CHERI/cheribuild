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
import os
import platform
import tempfile
from pathlib import Path

from .cross.crosscompileproject import CrossCompileMakefileProject
from .cross.gdb import BuildGDB
from .project import (DefaultInstallDir, GitRepository, MakefileProject, Project,
                      ReuseOtherProjectDefaultTargetRepository, SimpleProject,
                      TargetAliasWithDependencies)
from ..config.chericonfig import BuildType, CheriConfig
from ..config.compilation_targets import CompilationTargets
from ..config.loader import ComputedDefaultValue
from ..utils import OSInfo, set_env


def _morello_firmware_build_outputs_dir(config: CheriConfig, _: SimpleProject):
    return config.morello_sdk_dir / "firmware/morello-fvp"


class ArmNoneEabiToolchain(SimpleProject):
    target = "arm-none-eabi-toolchain"

    @classmethod
    def is_toolchain_target(cls):
        return True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_required_system_tool("wget")

    def process(self):
        url_prefix = "https://developer.arm.com/-/media/Files/downloads/gnu-rm/9-2020q2/"
        filename = None
        if self.target_info.is_linux():
            # XXX: assumes x86_64 host
            if self.crosscompile_target.is_x86_64():
                filename = "gcc-arm-none-eabi-9-2020-q2-update-x86_64-linux.tar.bz2"
            elif self.crosscompile_target.is_aarch64():
                filename = "gcc-arm-none-eabi-9-2020-q2-update-aarch64-linux.tar.bz2"
            else:
                self.fatal("Unsupported CPU architecture")
        elif self.target_info.is_macos():
            assert self.crosscompile_target.is_x86_64(), "Assumes x86_64"
            filename = "gcc-arm-none-eabi-9-2020-q2-update-mac.tar.bz2"
        if filename is None:
            self.fatal("Cannot infer download URL for current OS:", platform.platform(),
                       fixit_hint="Please visit https://developer.arm.com/tools-and-software/open-source-software/"
                                  "developer-tools/gnu-toolchain/gnu-rm/downloads and select the appropriate download.")
            return
        if not (self.config.build_root / filename).is_file() or self.config.clean:
            self.run_cmd("wget", url_prefix + filename, "-O", self.config.build_root / filename)
        with self.async_clean_directory(self.config.output_root / self.config.local_arm_none_eabi_toolchain_relpath):
            self.run_cmd(["tar", "xf", self.config.build_root / filename, "--strip-components", "1", "-C",
                          self.config.output_root / self.config.local_arm_none_eabi_toolchain_relpath])


class MorelloFirmwareBase(CrossCompileMakefileProject):
    do_not_add_to_targets = True
    supported_architectures = [CompilationTargets.MORELLO_BAREMETAL_HYBRID]
    cross_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY  # TODO: install it
    needs_sysroot = False  # We don't need a complete sysroot
    default_build_type = BuildType.DEBUG  # TODO: release once it works
    _default_install_dir_fn = ComputedDefaultValue(function=_morello_firmware_build_outputs_dir,
                                                   as_string="$MORELLO_SDK_ROOT/fvp-firmware/morello/build-outputs")

    @property
    def optimization_flags(self):
        return []  # These projects won't build at -O0 (since it's too big), just use the default


class BuildMorelloScpFirmware(MorelloFirmwareBase):
    repository = GitRepository("git@git.morello-project.org:morello/scp-firmware.git")
    project_name = "morello-scp-firmware"
    dependencies = ["arm-none-eabi-toolchain"]
    supported_architectures = [CompilationTargets.ARM_NONE_EABI]
    cross_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR
    default_build_type = BuildType.RELEASE

    @property
    def build_mode(self):
        return "debug" if self.build_type.is_debug else "release"

    def setup(self):
        super().setup()
        # FIXME: DEBUG seems to result in an infinite loop on startup (assertion failure?)
        self.make_args.set(PRODUCT="morello", MODE=self.build_mode,
                           LOG_LEVEL="TRACE" if self.build_type.is_debug else "TRACE",  # TODO: change it to warn
                           V="y")
        # Build system tries to use macos tool which won't work
        self.make_args.set(
            AR=self.target_info.ar,
            OBJCOPY=self.CC.with_name(self.CC.name.replace("gcc", "objcopy")),
            SIZE=self.CC.with_name(self.CC.name.replace("gcc", "size")),
            )

    def process(self):
        if not self.CC.exists():
            self.fatal("Could not find", self.CC,
                       fixit_hint="Install the ARM GCC manually or use "
                                  "`cheribuild.py " + ArmNoneEabiToolchain.target + "`")
        super().process()

    def install(self, **kwargs):
        binaries_dir = self.build_dir / "build/product/morello"
        for i in ("mcp_ramfw_fvp", "scp_ramfw_fvp", "mcp_romfw", "scp_romfw"):
            self.install_file(binaries_dir / i / self.build_mode / "bin" / (i + ".bin"),
                              self.install_dir / (i + ".bin"), print_verbose_only=False)
            self.install_file(binaries_dir / i / self.build_mode / "bin" / (i + ".elf"),
                              self.install_dir / (i + ".elf"), print_verbose_only=False)

    def run_tests(self):
        self.run_make(make_target="test")  # XXX: doesn't work yet, needs a read/write/isatty()

    @classmethod
    def mcp_rom_bin(cls, caller):
        return cls.get_install_dir(caller, cross_target=CompilationTargets.ARM_NONE_EABI) / "mcp_romfw.bin"

    @classmethod
    def scp_rom_bin(cls, caller):
        return cls.get_install_dir(caller, cross_target=CompilationTargets.ARM_NONE_EABI) / "scp_romfw.bin"


class BuildMorelloTrustedFirmware(MorelloFirmwareBase):
    target = "morello-trusted-firmware"
    project_name = "morello-trusted-firmware-a"
    repository = GitRepository("git@git.morello-project.org:morello/trusted-firmware-a.git")
    set_commands_on_cmdline = True  # Need to override this on the command line since the makefile uses :=
    default_build_type = BuildType.DEBUG

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_required_system_tool("dtc", homebrew="dtc", apt="dtc")

    def setup(self):
        super().setup()
        self.make_args.set(ENABLE_MORELLO_CAP=1, PLAT="morello", ARCH="aarch64",
                           DEBUG=1 if self.build_type.is_debug else 0,
                           CSS_USE_SCMI_SDS_DRIVER=1,
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
            fip_make.set(HOSTCC=str(self.host_CC) + " -Qunused-arguments " + fip_make.env_vars["HOSTLDFLAGS"])
        self.run_make(make_target="all", cwd=self.source_dir / "tools/fiptool", options=fip_make)

    def install(self, **kwargs):
        output_dir = self.build_dir / "build/morello" / ("debug" if self.build_type.is_debug else "release")
        self.install_file(output_dir / "bl31.bin", self.install_dir / "tf-bl31.bin", print_verbose_only=False)
        self.install_file(output_dir / "fdts/morello-fvp.dtb", self.install_dir / "morello-fvp.dtb",
                          print_verbose_only=False)
        self.install_file(self.build_dir / "tools/fiptool/fiptool", self.config.morello_sdk_dir / "bin/fiptool",
                          print_verbose_only=False)


class BuildMorelloACPICA(MakefileProject):
    target = "morello-acpica"
    project_name = "morello-acpica"
    repository = GitRepository("https://github.com/acpica/acpica.git")
    git_revision = "ba04ee3db1042c88cf4189a26a4ad506f856dd9a"
    needs_full_history = True
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL


class BuildMorelloUEFI(MorelloFirmwareBase):
    repository = GitRepository("git@git.morello-project.org:morello/edk2.git")
    morello_platforms_repository = GitRepository("git@git.morello-project.org:morello/edk2-platforms.git")
    dependencies = ["gdb-native", "morello-acpica"]  # To get ld.bfd
    target = "morello-uefi"
    project_name = "morello-edk2"
    default_build_type = BuildType.DEBUG
    _extra_git_clean_excludes = ["--exclude=edk2-platforms"]  # Don't delete edk2-platforms, we do it manually

    def update(self):
        super().update()
        self.morello_platforms_repository.update(self, src_dir=self.source_dir / "edk2-platforms",
                                                 skip_submodules=self.skip_git_submodules)

    def clean(self):
        super().clean()
        self._git_clean_source_dir(self.source_dir / "edk2-platforms")

    @property
    def build_mode(self):
        return "DEBUG" if self.build_type.is_debug else "RELEASE"

    def compile(self, **kwargs):
        # We need to use ld.bfd
        with tempfile.TemporaryDirectory() as td:
            self._compile(Path(td))

    def _compile(self, fake_compiler_dir: Path):
        acpica_build = BuildMorelloACPICA.get_build_dir(self, cross_target=CompilationTargets.NATIVE)
        iasl = acpica_build / "generate/unix/bin/iasl"
        if not iasl.exists():
            self.fatal("Missing iasl tool, run the", BuildMorelloACPICA.target, "first.")
        # Create the fake compiler directory with the tools and a clang wrapper script that forces bfd
        # Also disable lto since we don't install the LLVM LTO plugin
        self.write_file(fake_compiler_dir / "clang", contents="""#!/usr/bin/env python3
import subprocess
import sys

args = []
# drop arguments that won't work with a non-plugin ld
for arg in sys.argv[1:]:
    if arg.startswith("-Wl,-plugin-opt="):
        continue
    args.append(arg)
subprocess.check_call(["{real_clang}", "-B{fake_dir}"] + args + ["-fuse-ld=bfd", "-fno-lto", "-Qunused-arguments"])
""".format(real_clang=self.CC, fake_dir=fake_compiler_dir), overwrite=True, mode=0o755)
        self.run_cmd(fake_compiler_dir / "clang", "-v")  # check that the script works
        for i in ("llvm-objcopy", "llvm-objdump", "llvm-ar", "llvm-ranlib", "objcopy", "objdump", "ar", "ranlib",
                  "nm", "llvm-nm", "size", "llvm-size"):
            self.create_symlink(self.sdk_bindir / i, fake_compiler_dir / i, relative=False)

        # EDK2 needs bfd until the lld target is merged
        bfd_path = BuildGDB.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/ld.bfd"
        if not bfd_path.exists():
            self.fatal("Missing ld.bfd, please run `cheribuild.py gdb-native --reconfigure`")
        self.create_symlink(bfd_path, fake_compiler_dir / "ld", relative=False)
        self.create_symlink(bfd_path, fake_compiler_dir / "ld.bfd", relative=False)
        firmware_ver = self.run_cmd("git", "-C", self.source_dir, "rev-parse", "--short", "HEAD",
                                    capture_output=True, run_in_pretend_mode=True).stdout.decode("utf-8").strip()
        # if ! git diff-index --quiet HEAD --; then
        #   FIRMWARE_VER="${FIRMWARE_VER}-dirty"
        # fi
        with set_env(CROSS_COMPILE=str(fake_compiler_dir) + "/",
                     CLANG_BIN=fake_compiler_dir,
                     EDK2_TOOLCHAIN="CLANG38",
                     VERBOSE=1,
                     IASL_PREFIX=str(iasl.parent) + "/",
                     PATH=str(fake_compiler_dir) + ":" + os.getenv("PATH")):
            platform_desc = "Platform/ARM/Morello/MorelloPlatformFvp.dsc"
            if not (self.source_dir / "edk2-platforms" / platform_desc).exists():
                self.fatal("Could not find", self.source_dir / "edk2-platforms" / platform_desc)
            script = """
. edksetup.sh --reconfig
make -C BaseTools
export PACKAGES_PATH=:{src}:{src}/edk2-platforms:
export CLANG38_AARCH64_PREFIX={toolchain_bin}/llvm-
export CLANG38_BIN={toolchain_bin}/
build -n {make_jobs} -a AARCH64 -t CLANG38 -p {platform_desc} \
    -b {build_mode} -s -D EDK2_OUT_DIR=Build/morellofvp -D PLAT_TYPE_FVP \
    -D ENABLE_MORELLO_CAP -D FIRMWARE_VER={firmware_ver}""".format(
                src=self.source_dir, make_jobs=self.config.make_jobs, build_mode=self.build_mode,
                firmware_ver=firmware_ver, toolchain_bin=fake_compiler_dir, platform_desc=platform_desc)
            self.run_shell_script(script, shell="bash", cwd=self.source_dir)

    def install(self, **kwargs):
        self.install_file(self.build_dir / "Build/morellofvp" / (self.build_mode + "_CLANG38") / "FV/BL33_AP_UEFI.fd",
                          self.install_dir / "uefi.bin", print_verbose_only=False)

    @classmethod
    def uefi_bin(cls, caller):
        return cls.get_install_dir(caller, cross_target=CompilationTargets.MORELLO_BAREMETAL_HYBRID) / "uefi.bin"


class BuildMorelloFlashImages(Project):
    target = "morello-flash-images"
    dependencies = ["morello-scp-firmware", "morello-trusted-firmware"]
    _default_install_dir_fn = ComputedDefaultValue(function=_morello_firmware_build_outputs_dir,
                                                   as_string="$MORELLO_SDK_ROOT/fvp-firmware/morello/build-outputs")
    repository = ReuseOtherProjectDefaultTargetRepository(source_project=BuildMorelloScpFirmware)

    def process(self):
        fw_dir = _morello_firmware_build_outputs_dir(self.config, self)
        self.info("Building combined SCP and AP flash image")
        self.run_cmd(self.config.morello_sdk_dir / "bin/fiptool", "create",
                     "--scp-fw", fw_dir / "scp_ramfw_fvp.bin",
                     "--soc-fw", fw_dir / "tf-bl31.bin",
                     self.scp_ap_ram_firmware_image)
        self.info("Building MCP flash image")
        self.run_cmd(self.config.morello_sdk_dir / "bin/fiptool", "create",
                     "--blob", "uuid=54464222-a4cf-4bf8-b1b6-cee7dade539e,file=" + str(fw_dir / "mcp_ramfw_fvp.bin"),
                     self.mcp_ram_firmware_image)

    @property
    def scp_ap_ram_firmware_image(self):
        return self.install_dir / "scp_ap_image.bin"

    @property
    def mcp_ram_firmware_image(self):
        return self.install_dir / "mcp_image.bin"


class BuildMorelloFirmware(TargetAliasWithDependencies):
    target = "morello-firmware"
    dependencies = ["morello-scp-firmware", "morello-trusted-firmware", "morello-flash-images", "morello-uefi"]
