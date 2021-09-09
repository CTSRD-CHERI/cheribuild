#
# SPDX-License-Identifier: BSD-2-Clause
#
# Author: Hesham Almatary <Hesham.Almatary@cl.cam.ac.uk>
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
import os

from .crosscompileproject import (CheriConfig, CompilationTargets, CrossCompileAutotoolsProject, DefaultInstallDir,
                                  GitRepository)
from ...config.loader import ComputedDefaultValue


class BuildFreeRTOS(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/FreeRTOS",
                               force_branch=True, default_branch="hmka2")
    target = "freertos"
    dependencies = ["newlib", "compiler-rt-builtins"]
    is_sdk_target = True
    needs_sysroot = False  # We don't need a complete sysroot
    supported_architectures = [
        CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP,
        CompilationTargets.BAREMETAL_NEWLIB_RISCV32,
        CompilationTargets.BAREMETAL_NEWLIB_RISCV32_PURECAP,
        CompilationTargets.BAREMETAL_NEWLIB_RISCV64]
    default_install_dir = DefaultInstallDir.ROOTFS_LOCALBASE

    # FreeRTOS Demos to build
    supported_freertos_demos = [
        # Generic/simple (CHERI-)RISC-V Demo that runs main_blinky on simulators
        # and simple SoCs
        "RISC-V-Generic"]

    # Map Demos and the FreeRTOS apps we support building/running for
    supported_demo_apps = {"RISC-V-Generic": [
                                              "aws_ota",
                                              "coremark",
                                              "main_blinky",
                                              "main_peekpoke",
                                              "main_servers",
                                              "mibench",
                                              "modbus",
                                             ]}

    default_demo = "RISC-V-Generic"
    default_demo_app = "main_blinky"
    default_build_system = "waf"

    def _run_waf(self, *args, **kwargs):
        cmdline = ["./waf", "-t", self.source_dir / str("FreeRTOS/Demo/" + self.demo), "-o", self.build_dir] + list(args)
        if self.config.verbose:
            cmdline.append("-v")
        return self.run_cmd(cmdline, cwd=self.source_dir  / str("FreeRTOS/Demo/" + self.demo), **kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.compiler_resource = self.get_compiler_info(self.CC).get_resource_dir()

        self.default_demo_app = "qemu_virt-" + self.target_info.get_riscv_arch_string(self.crosscompile_target,
                                                                                      softfloat=True) + \
                                self.target_info.get_riscv_abi(self.crosscompile_target, softfloat=True)

        # Galois uses make build sysetm
        if self.demo == "RISC-V_Galois_demo":

            if self.toolchain == "llvm":
                self.make_args.set(USE_CLANG="yes")

            # Galois demo only runs on VCU118/GFE
            self.make_args.set(BSP="vcu118")

            if "rv32" in self.target_info.get_riscv_arch_string(self.crosscompile_target, softfloat=True):
                self.make_args.set(XLEN="32")
            else:
                self.make_args.set(XLEN="64")

            # Set sysroot Makefile arg to pick up libc
            self.make_args.set(SYSROOT_DIR=str(self.sdk_sysroot))

            if self.target_info.target.is_cheri_purecap():
                self.make_args.set(CHERI="1")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        cls.build_system = cls.add_config_option(
            "build_system", metavar="BUILD", show_help=True,
            default=cls.default_build_system,
            help="The FreeRTOS Demo Build System.")  # type: str

        cls.toolchain = cls.add_config_option(
            "toolchain", metavar="TOOLCHAIN", show_help=True,
            default="llvm",
            help="The toolchain to build FreeRTOS with.")  # type: str

        cls.demo = cls.add_config_option(
            "demo", metavar="DEMO", show_help=True,
            default=cls.default_demo,
            help="The FreeRTOS Demo build.")  # type: str

        cls.demo_app = cls.add_config_option(
            "prog", metavar="PROG", show_help=True,
            default=cls.default_demo_app,
            help="The FreeRTOS program to build.")  # type: str

        cls.platform = cls.add_config_option(
            "platform", metavar="PLATFORM", show_help=True,
            default="qemu_virt",
            help="The FreeRTOS platform to build for.")  # type: str

        cls.mem_start = cls.add_config_option(
            "memstart", metavar="MEMSTART", show_help=True,
            default=0x80000000,
            help="The DRAM start address")

        # Default to QEMU addresses
        cls.ipaddr = cls.add_config_option(
            "ipaddr", metavar="IPADDR", show_help=True,
            default="10.0.2.15/24",
            help="The static IP to assign to FreeRTOS.")  # type: str

        cls.gateway = cls.add_config_option(
            "gateway", metavar="GATEWAY", show_help=True,
            default="10.0.2.2",
            help="The static gateway IP for FreeRTOS.")  # type: str

        cls.compartmentalize= cls.add_bool_option("compartmentalize", show_help=True,
            default=False,
            help="Compartmentalize FreeRTOS")

        cls.compartmentalize_stdlibs = cls.add_bool_option("compartmentalize_stdlibs", show_help=True,
            default=False,
            help="Compartmentalize libc, libm and builtins")

        cls.plot_compartments = cls.add_bool_option("plot_compartments", show_help=True,
            default=False,
            help="Plot compartments deps graph using graphviz")

        cls.loc_stats = cls.add_bool_option("loc_stats", show_help=True,
            default=False,
            help="Calculate detailed LoC stats for the built system")

        cls.compartmentalization_mode = cls.add_config_option("compartmentalization_mode", show_help=True,
            default="objs",
            help="'Comparmentalization mode (either objs or libs)")

        cls.use_virtio_blk = cls.add_bool_option("use_virtio_blk", show_help=True,
            default=False,
            help="Use VirtIO Block as a disk for FreeRTOS")

        cls.create_disk_image = cls.add_bool_option("create_disk_image", show_help=True,
            default=False,
            help="Create, parition, format and write data into an external blk disk image")

        cls.debug = cls.add_bool_option("debug", show_help=True,
            default=False,
            help="Enable FreeRTOS debug featuers")

        cls.log_udp = cls.add_bool_option("log_udp", show_help=True,
            default=False,
            help="Send output over UDP instead of stdout/serial")

        cls.demo_bsp = cls.add_config_option(
            "bsp", metavar="BSP", show_help=True,
            default=ComputedDefaultValue(function=lambda _, p: p.default_demo_bsp(),
                                         as_string="target-dependent default"),
            help="The FreeRTOS BSP to build. This is only valid for the "
                 "paramterized RISC-V-Generic. The BSP option chooses "
                 "platform, RISC-V arch and RISC-V abi in the "
                 "$platform-$arch-$abi format. See RISC-V-Generic/README for more details")

    def default_demo_bsp(self):
        return "qemu_virt-" + self.target_info.get_riscv_arch_string(self.crosscompile_target, softfloat=True) + "-" + \
               self.target_info.get_riscv_abi(self.crosscompile_target, softfloat=True)

    def run_compartmentalize(self, *args, **kwargs):
        cmdline = ["./compartmentalize.py"]
        return self.run_cmd(cmdline, cwd=self.source_dir / str("FreeRTOS/Demo/" + self.demo), **kwargs)

    def compile(self, **kwargs):

        if self.build_system == "waf":
            self._run_waf("install", self.config.make_j_flag)
            return

        # Galois only currently has make build system
        if self.demo == "RISC-V_Galois_demo":
            # Need to clean before/between building apps, otherwise
            # irrelevant objs will be picked up from incompatible apps/builds
            self.make_args.set(PROG=self.demo_app)
            self.make_args.set(DEMO="cyberphys")
            self.run_make("clean", cwd=self.source_dir / str("FreeRTOS/Demo/" + self.demo))

            self.run_make(cwd=self.source_dir / str("FreeRTOS/Demo/" + self.demo))
            self.move_file(self.source_dir / str("FreeRTOS/Demo/" + self.demo + "/" + self.demo_app + ".elf"),
                           self.source_dir / str("FreeRTOS/Demo/" + self.demo + "/" + self.demo + self.demo_app + ".elf"))

    def configure(self):
        if self.build_system == "waf":

            if "servers" in self.demo_app:
                program_root = "./demo/servers"
            elif "aws_ota" in self.demo_app:
                program_root = "coreMQTT-Agent"
            elif "coremark" in self.demo_app:
                program_root = "coremark"
            elif "mibench" in self.demo_app:
                program_root = "MiBench2"
            elif "ipc_benchmark" in self.demo_app:
                program_root = "./demo/ipc_benchmark"
            elif "cyberphys" in self.demo_app:
                program_root = "./demo/cyberphys"
            elif "modbus" in self.demo_app:
                program_root = "./modcap"
            else:
                program_root = "/no/path"

            config_options = [
                          "--prefix", str(self.real_install_root_dir) + '/FreeRTOS/Demo/',
                          "--program", self.demo_app,
                          "--toolchain", self.toolchain,
                          "--riscv-arch", self.target_info.get_riscv_arch_string(self.crosscompile_target, softfloat=True),
                          "--riscv-abi", self.target_info.get_riscv_abi(self.crosscompile_target, softfloat=True),
                          "--riscv-platform", self.platform,
                          "--program-path", program_root,
                          "--sysroot",  str(self.sdk_sysroot),
                          "--mem-start", self.mem_start,
                          "--ipaddr", self.ipaddr,
                          "--gateway", self.gateway
                          ]

            config_options += ["--purecap"] if self.target_info.target.is_cheri_purecap() else []

            if self.compartmentalize:
              config_options += ["--compartmentalize"]
              config_options += ["--compartmentalization_mode", self.compartmentalization_mode]
              if self.compartmentalize_stdlibs:
                  config_options += ["--compartmentalize_stdlibs"]
              if self.plot_compartments:
                  config_options += ["--plot_compartments"]

            if self.loc_stats:
              config_options += ["--loc_stats"]

            if self.use_virtio_blk:
              config_options += ["--use-virtio-blk"]

            if self.create_disk_image:
              config_options += ["--create-disk-image"]

            if self.debug:
              config_options += ["--debug"]

            if self.log_udp:
              config_options += ["--log_udp"]

            self._run_waf("distclean", "configure", *config_options)

    def install(self, **kwargs):
        if self.build_system == "waf":
            return

        self.install_file(
            self.source_dir / str("FreeRTOS/Demo/" + self.demo + "/" + self.demo + self.demo_app + ".elf"),
            self.real_install_root_dir / str("FreeRTOS/Demo/bin/" + self.demo + "_" + self.demo_app + ".elf"))

    def process(self):

        #if self.demo not in self.supported_freertos_demos:
        #    self.fatal("Demo " + self.demo + "is not supported")

        #if self.demo_app not in self.supported_demo_apps[self.demo]:
        #    self.fatal(self.demo + " Demo doesn't support/have " + self.demo_app)

        if self.toolchain == "llvm":
            with self.set_env(PATH=str(self.sdk_bindir) + ":" + os.getenv("PATH", ""),
                         # Add compiler-rt location to the search path
                         CFLAGS= ' '.join(self.target_info.get_essential_compiler_and_linker_flags()),
                         LDFLAGS="-L" + str(self.compiler_resource / "lib")):
                super().process()
        else:
            super().process()
