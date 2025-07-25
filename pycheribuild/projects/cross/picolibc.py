#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright 2022 Alex Richardson
# Copyright 2022 Google LLC
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

from .crosscompileproject import CrossCompileMesonProject, GitRepository
from ..build_qemu import BuildQEMU
from ..project import DefaultInstallDir
from ...config.compilation_targets import CompilationTargets, PicolibcBaremetalTargetInfo
from ...config.target_info import CPUArchitecture


class BuildPicoLibc(CrossCompileMesonProject):
    target = "picolibc"
    repository = GitRepository("https://github.com/picolibc/picolibc.git")
    supported_architectures = CompilationTargets.ALL_NATIVE + CompilationTargets.ALL_PICOLIBC_TARGETS
    # Installing the native headers and libraries to <output>/local breaks other native project builds.
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    needs_sysroot = False
    is_rootfs_target = True
    include_os_in_target_suffix = False  # Avoid adding -picolibc- as we are building picolibc here
    # ld.lld: error: -r and --gdb-index may not be used together
    add_gdb_index = False

    @classmethod
    def dependencies(cls, config) -> "tuple[str, ...]":
        if cls._xtarget and cls._xtarget.is_native():
            return tuple()
        return ("compiler-rt-builtins",)

    @property
    def _meson_extra_binaries(self):
        if not self.compiling_for_host():
            if self.compiling_for_riscv(include_purecap=True):
                return "exe_wrapper = ['sh', '-c', 'test -z \"$PICOLIBC_TEST\" || run-riscv \"$@\"', 'run-riscv']"
            elif self.crosscompile_target.cpu_architecture == CPUArchitecture.ARM32:
                return "exe_wrapper = ['sh', '-c', 'test -z \"$PICOLIBC_TEST\" || run-arm \"$@\"', 'run-arm']"
            else:
                assert False, "Not supported yet"
        return ""

    @property
    def _meson_extra_properties(self):
        if self.compiling_for_host():
            return ""
        else:
            assert isinstance(self.target_info, PicolibcBaremetalTargetInfo)
            layout = self.target_info.memory_layout
            return f"""
default_flash_addr = '{hex(layout.flash_start)}'
default_flash_size = '{hex(layout.flash_size)}'
default_ram_addr   = '{hex(layout.dram_start)}'
default_ram_size   = '{hex(layout.dram_size)}'
"""

    def setup(self):
        super().setup()
        self.add_meson_options(
            tests=True,
            multilib=False,
            **{
                "io-long-long": True,
                "io-long-double": True,
                "tests-enable-stack-protector": False,
                "tinystdio": True,
            },
        )
        if self.compiling_for_cheri():
            self.add_meson_options(
                **{
                    "newlib-initfini": False,
                    "thread-local-storage": False,  # TODO: needs more fixes
                },
            )
        if self.compiling_for_host():  # see scripts/do-native-configure
            self.add_meson_options(
                **{
                    "picocrt": False,
                    "picolib": False,
                    "semihost": False,
                    "posix-console": True,
                },
            )
        else:
            self.add_meson_options(**{"picocrt": True})

    @property
    def default_compiler_flags(self):
        if self.crosscompile_target.is_riscv64(include_purecap=True):
            # We have to resolve undef weak symbols to 0, but ld.lld doesn't do the rewriting of instructions and
            # codegen isn't referencing the GOT, so until https://reviews.llvm.org/D107280 lands, we have to use -fpie
            # See also https://github.com/ClangBuiltLinux/linux/issues/1409 and
            # https://github.com/riscv-non-isa/riscv-elf-psabi-doc/pull/201
            return [*super().default_compiler_flags, "-fpie"]
        return super().default_compiler_flags

    @property
    def default_ldflags(self):
        result = super().default_ldflags
        if not self.compiling_for_host():
            # We have to add -nostdlib here, otherwise the meson "linker flag supported" checks fail since it implicitly
            # tries to pull in -lc. This also allows us to avoid setting "skip_sanity_check=True"
            result += ["-L" + str(self.build_dir / "local-libgcc"), "-nostdlib"]
        return result

    def configure(self, **kwargs):
        if not self.compiling_for_host():
            # Symlink libgcc.a to the build dir to allow linking against it without adding all of <sysroot>/lib.
            self.makedirs(self.build_dir / "local-libgcc")
            self.create_symlink(
                self.sdk_sysroot / "lib/libgcc.a",
                self.build_dir / "local-libgcc/libgcc.a",
                print_verbose_only=False,
            )
        super().configure(**kwargs)

    def install(self, **kwargs):
        super().install(**kwargs)
        if self.crosscompile_target.is_riscv64(include_purecap=True):
            # The clang baremetal driver expect the following directory to exist:
            self.makedirs(self.install_dir / "rv64imafdc")
            self.create_symlink(self.install_dir, self.install_dir / "rv64imafdc/lp64d", print_verbose_only=False)
        if not (self.install_dir / "lib/libdl.a").exists():
            # The libunwind test suite expects -ldl to exists since CMAKE_DL_LIBS is always non-empty for baremetal...
            # Work around this for now by creating an empty file
            self.run_cmd(self.target_info.ar, "r", self.install_dir / "lib/libdl.a")

    def run_tests(self):
        if not self.compiling_for_host():
            qemu = BuildQEMU.qemu_binary_for_target(self.crosscompile_target, self.config)
            new_env = dict(PATH=str(qemu.parent) + ":" + os.getenv("PATH", ""), QEMU_BIN=qemu)
            if self.crosscompile_target.cpu_architecture == CPUArchitecture.ARM32:
                new_env["QEMU_CPU"] = "max"
            with self.set_env(**new_env, print_verbose_only=False):
                self.run_cmd(self.configure_command, "test", "--print-errorlogs", cwd=self.build_dir)
        else:
            super().run_tests()
