#
# Copyright (c) 2018 Alex Richardson
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
import os
import shlex
import typing
from pathlib import Path
from typing import Optional

from .crosscompileproject import CompilationTargets
from ..project import Project
from ...config.chericonfig import BuildType
from ...processutils import commandline_to_str
from ...utils import find_free_port, SocketAndPort

if typing.TYPE_CHECKING:
    _BenchmarkMixinBase = Project
else:
    _BenchmarkMixinBase = object


# We also build benchmarks for hybrid to see whether those compilation flags change the results
class BenchmarkMixin(_BenchmarkMixinBase):
    supported_architectures = CompilationTargets.ALL_CHERIBSD_TARGETS_WITH_HYBRID_FOR_PURECAP_ROOTFS + [
        CompilationTargets.NATIVE]
    default_build_type = BuildType.RELEASE
    prefer_full_lto_over_thin_lto = True

    @property
    def optimization_flags(self):
        if self.build_type.is_release:
            return ["-O3"]
        return super().optimization_flags

    def run_fpga_benchmark(self, benchmarks_dir: Path, *, output_file: str = None, benchmark_script: str = None,
                           benchmark_script_args: list = None, extra_runbench_args: list = None):
        assert benchmarks_dir is not None
        assert output_file is not None, "output_file must be set to a valid value"
        xtarget = self.crosscompile_target
        assert not self.compiling_for_host() and self.target_info.is_cheribsd(), "Only supported for CheriBSD targets"
        self.strip_elf_files(benchmarks_dir)
        for root, dirnames, filenames in os.walk(str(benchmarks_dir)):
            for filename in filenames:
                file = Path(root, filename)
                if file.suffix == ".dump":
                    # TODO: make this an error since we should have deleted them
                    self.warning("Will copy a .dump file to the FPGA:", file)

        runbench_args = [benchmarks_dir, "--target=" + self.config.benchmark_ssh_host, "--out-path=" + output_file]
        qemu_ssh_socket: "Optional[SocketAndPort]" = None
        basic_args = []
        if self.config.benchmark_with_qemu:
            from ...projects.build_qemu import BuildQEMU
            qemu_path = BuildQEMU.qemu_binary(self)
            qemu_ssh_socket = find_free_port()
            if not qemu_path.exists():
                self.fatal("QEMU binary", qemu_path, "doesn't exist")
            basic_args += ["--use-qemu-instead-of-fpga", "--qemu-path=" + str(qemu_path),
                           "--qemu-ssh-port=" + str(qemu_ssh_socket.port)]
        elif not self.compiling_for_mips(include_purecap=True):
            self.fatal("run_fpga_benchmark has not been updated for RISC-V/AArch64")
            return

        if self.config.test_ssh_key is not None:
            basic_args.extend(["--ssh-key", str(self.config.test_ssh_key.with_suffix(""))])

        if self.config.benchmark_ld_preload:
            runbench_args.append("--extra-input-files=" + str(self.config.benchmark_ld_preload))
            if xtarget.is_cheri_purecap() and not xtarget.get_rootfs_target().is_cheri_purecap():
                env_var = "LD_CHERI_PRELOAD"
            elif not xtarget.is_cheri_purecap() and xtarget.get_rootfs_target().is_cheri_purecap():
                env_var = "LD_64_PRELOAD"
            else:
                env_var = "LD_PRELOAD"
            pre_cmd = "export {}={};".format(env_var,
                                             shlex.quote("/tmp/benchdir/" + self.config.benchmark_ld_preload.name))
            runbench_args.append("--pre-command=" + pre_cmd)
        if self.config.benchmark_fpga_extra_args:
            basic_args.extend(self.config.benchmark_fpga_extra_args)
        if self.config.benchmark_extra_args:
            runbench_args.extend(self.config.benchmark_extra_args)
        if self.config.tests_interact:
            runbench_args.append("--interact")

        from ...projects.cross.cheribsd import BuildCheriBsdMfsKernel, ConfigPlatform
        if self.config.benchmark_with_qemu:
            # When benchmarking with QEMU we always spawn a new instance
            # noinspection PyProtectedMember
            kernel_image = self.target_info._get_mfs_root_kernel(ConfigPlatform.QEMU,
                                                                 not self.config.benchmark_with_debug_kernel)
            basic_args.append("--kernel-img=" + str(kernel_image))
        elif self.config.benchmark_clean_boot:
            # use a bitfile from jenkins. TODO: add option for overriding
            assert xtarget.is_riscv(include_purecap=True)
            basic_args.append("--jenkins-bitfile")
            mfs_kernel = BuildCheriBsdMfsKernel.get_instance_for_cross_target(xtarget.get_rootfs_target(), self.config,
                                                                              caller=self)
            kernel_config = mfs_kernel.default_kernel_config(ConfigPlatform.GFE,
                                                             benchmark=not self.config.benchmark_with_debug_kernel)
            kernel_image = mfs_kernel.get_kernel_install_path(kernel_config)
            basic_args.append("--kernel-img=" + str(kernel_image))
        else:
            runbench_args.append("--skip-boot")
        if benchmark_script:
            runbench_args.append("--script-name=" + benchmark_script)
        if benchmark_script_args:
            runbench_args.append("--script-args=" + commandline_to_str(benchmark_script_args))
        if extra_runbench_args:
            runbench_args.extend(extra_runbench_args)

        cheribuild_path = Path(__file__).absolute().parent.parent.parent
        if self.config.benchmark_with_qemu:
            # Free the port that we reserved for QEMU before starting the FPGA boot script
            if qemu_ssh_socket is not None:
                qemu_ssh_socket.socket.close()
        self.run_cmd(
            [str(cheribuild_path / "vcu118-bsd-boot.py")] + basic_args + ["-vvvvv", "runbench"] + runbench_args,
            give_tty_control=True)
