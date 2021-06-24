#
# Copyright (c) 2019 Alex Richardson
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
import tempfile
from pathlib import Path
from typing import Optional

from .cherisim import BuildBeriCtl, BuildCheriSim
from .project import CheriConfig, SimpleProject
from .cross.cheribsd import ConfigPlatform
from ..config.compilation_targets import CompilationTargets


class LaunchFPGABase(SimpleProject):
    do_not_add_to_targets = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.extra_base_options = cls.add_config_option("extra-options", default=[], kind=list, metavar="OPTIONS",
                                                       help="Additional command line flags to pass to "
                                                            "beri-fpga-bsd-boot")
        cls.extra_bootonly_options = cls.add_config_option("extra-boot-options", default=[], kind=list,
                                                           metavar="OPTIONS",
                                                           help="Additional command line flags to pass to the "
                                                                "bootonly subcommand of beri-fpga-bsd-boot")
        cls.attach_only = cls.add_bool_option("attach-only", help="Connect to console instead of booting.")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.current_kernel = None  # type: Optional[Path]

    def process(self):
        assert self.current_kernel is not None
        if self.current_kernel is not None and not self.current_kernel.exists():
            self.dependency_error("Kernel is missing:", self.current_kernel,
                                  install_instructions="Run `cheribuild.py cheribsd` or `cheribuild.py run -d`.")
        sim_project = BuildCheriSim.get_instance(self, cross_target=CompilationTargets.NATIVE)
        cherilibs_dir = Path(sim_project.source_dir, "cherilibs")
        cheri_dir = Path(sim_project.source_dir, "cheri")
        if not cheri_dir.exists() or not cherilibs_dir.exists():
            self.fatal("cheri-cpu repository missing. Run `cheribuild.py berictl` or `git clone {} {}`".format(
                sim_project.repository.url, sim_project.source_dir))
        basic_args = [
            "--berictl=" + str(BuildBeriCtl.get_build_dir(self, cross_target=CompilationTargets.NATIVE) / "berictl")]

        if self.extra_base_options:
            basic_args.extend(self.extra_base_options)
        if self.config.test_ssh_key.with_suffix("").exists():
            basic_args.extend(["--ssh-key", str(self.config.test_ssh_key.with_suffix(""))])
        # use a bitfile from jenkins. TODO: add option for overriding
        basic_args.append("--jenkins-bitfile=cheri" + self.config.mips_cheri_bits_str)
        basic_args.append("--kernel-img=" + str(self.current_kernel))

        bootonly_args = ["--interact"]
        if self.extra_bootonly_options:
            bootonly_args.extend(self.extra_bootonly_options)
        cheribuild_path = Path(__file__).absolute().parent.parent.parent
        if self.attach_only:
            subcmd_and_args = ["console"]
        else:
            subcmd_and_args = ["bootonly", *bootonly_args]
        if self.config.fpga_custom_env_setup_script:
            env_setup_script = self.config.fpga_custom_env_setup_script
        else:
            env_setup_script = "{cheri_dir}/setup.sh".format(cheri_dir=cheri_dir)

        beri_fpga_bsd_boot_script = """
set +x
source "{env_setup_script}"
set -x
export PATH="$PATH:{cherilibs_dir}/tools:{cherilibs_dir}/tools/debug"
exec {cheribuild_path}/beri-fpga-bsd-boot.py {basic_args} -vvvvv {subcmd_and_args}
""".format(env_setup_script=env_setup_script, cherilibs_dir=cherilibs_dir, cheribuild_path=cheribuild_path,
           basic_args=self.commandline_to_str(basic_args), subcmd_and_args=self.commandline_to_str(subcmd_and_args))
        self.run_shell_script(beri_fpga_bsd_boot_script, shell="bash")  # the setup script needs bash not sh


class LaunchCheriBSDOnFGPA(LaunchFPGABase):
    target = "run-fpga"
    dependencies = ["cheribsd-mfs-root-kernel-mips64-hybrid"]
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_HYBRID]

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.benchmark_kernel = cls.add_bool_option("benchmark-kernel",
                                                   help="Use the benchmark kernel instead of one with assertions "
                                                        "enabled.")
        cls.kernel_image = cls.add_config_option("kernel-image", kind=Path, help="Override the kernel image to boot")
        cls.kernel_config = cls.add_config_option("alternative-kernel",
                                                  help="Override kernel configuration to boot by specifying the kernel "
                                                       "configuration name")

    def process(self):
        from .cross.cheribsd import BuildCheriBsdMfsKernel
        mfs_kernel = BuildCheriBsdMfsKernel.get_instance(self)
        # TODO: allow using a plain MIPS kernel?
        if self.kernel_image:
            self.current_kernel = self.kernel_image
        else:
            if self.kernel_config:
                kernconf = self.kernel_config
            else:
                kernconf = mfs_kernel.default_kernel_config(ConfigPlatform.BERI,
                                                            benchmark=self.benchmark_kernel)
            self.current_kernel = mfs_kernel.get_kernel_install_path(kernconf)

        with tempfile.TemporaryDirectory() as kernel_image_tmpdir:
            # Strip to kernel image to save some time when copying it to the FPGA booting
            # TODO: move into beri-fpga-bsd-boot?
            stripped_target = Path(kernel_image_tmpdir, self.current_kernel.name + ".stripped")
            if self.maybe_strip_elf_file(self.current_kernel, output_path=stripped_target):
                self.run_cmd("du", "-h", self.current_kernel, stripped_target)
                self.current_kernel = stripped_target
            super().process()

# TODO: boot purecap minimal disk image
