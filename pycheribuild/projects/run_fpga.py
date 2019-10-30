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
from .project import *
from .cross.multiarchmixin import MultiArchBaseMixin
from pathlib import Path
from .cherisim import BuildCheriSim, BuildBeriCtl
from typing import Optional


class LaunchFPGABase(SimpleProject):
    doNotAddToTargets = True

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.extra_base_options = cls.addConfigOption("extra-options", default=[], kind=list, metavar="OPTIONS",
                                                     help="Additional command line flags to pass to beri-fpga-bsd-boot")
        cls.extra_bootonly_options = cls.addConfigOption("extra-boot-options", default=[], kind=list, metavar="OPTIONS",
                                                         help="Additional command line flags to pass to the bootonly subcommand of beri-fpga-bsd-boot")
        cls.benchmark_kernel = cls.addBoolOption("benchmark-kernel",
                                                 help="Use the benchmark kernel instead of one with assertions enabled.")
        cls.attach_only = cls.addBoolOption("attach-only", help="Connect to console instead of booting.")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.currentKernel = None  # type: Optional[Path]

    def process(self):
        assert self.currentKernel is not None
        if self.currentKernel is not None and not self.currentKernel.exists():
            self.dependencyError("Kernel is missing:", self.currentKernel,
                                 installInstructions="Run `cheribuild.py cheribsd` or `cheribuild.py run -d`.")
        sim_project = BuildCheriSim.get_instance(self)
        cherilibs_dir = Path(sim_project.sourceDir, "cherilibs")
        cheri_dir = Path(sim_project.sourceDir, "cheri")
        if not cheri_dir.exists() or not cherilibs_dir.exists():
            self.fatal("cheri-cpu repository missing. Run `cheribuild.py berictl` or `git clone {} {}`".format(
                sim_project.repository.url, sim_project.sourceDir))
        basic_args = ["--berictl=" + str(BuildBeriCtl.getBuildDir(self) / "berictl")]

        if self.extra_base_options:
            basic_args.extend(self.extra_base_options)
        # use a bitfile from jenkins. TODO: add option for overriding
        basic_args.append("--jenkins-bitfile=cheri" + self.config.cheriBitsStr)
        basic_args.append("--kernel-img=" + str(self.currentKernel))

        bootonly_args = ["--interact"]
        if self.extra_bootonly_options:
            bootonly_args.extend(self.extra_bootonly_options)
        cheribuild_path = Path(__file__).parent.parent.parent
        if self.attach_only:
            subcmd_and_args = ["console"]
        else:
            subcmd_and_args = ["bootonly", *bootonly_args]
        beri_fpga_bsd_boot_script = """
set +x
source "{cheri_dir}/setup.sh"
set -x
export PATH="$PATH:{cherilibs_dir}/tools:{cherilibs_dir}/tools/debug"
exec {cheribuild_path}/beri-fpga-bsd-boot.py {basic_args} -vvvvv {subcmd_and_args}
            """.format(cheri_dir=cheri_dir, cherilibs_dir=cherilibs_dir, basic_args=commandline_to_str(basic_args),
                       subcmd_and_args=commandline_to_str(subcmd_and_args), cheribuild_path=cheribuild_path)
        self.runShellScript(beri_fpga_bsd_boot_script, shell="bash")  # the setup script needs bash not sh


class LaunchCheriBSDOnFGPA(MultiArchBaseMixin, LaunchFPGABase):
    projectName = "run-fpga"
    dependencies = ["cheribsd-mfs-root-kernel-cheri"]
    supported_architectures = [CrossCompileTarget.CHERI]

    def process(self):
        from .cross.cheribsd import BuildCheriBsdMfsKernel
        mfs_kernel = BuildCheriBsdMfsKernel.get_instance_for_cross_target(CrossCompileTarget.CHERI, self.config)
        # TODO: allow using a plain MIPS kernel?
        if self.benchmark_kernel:
            kernel_config = mfs_kernel.fpga_kernconf + "_BENCHMARK"
        else:
            kernel_config = mfs_kernel.fpga_kernconf
        self.currentKernel = mfs_kernel.installed_kernel_for_config(self.config, kernel_config)
        super().process()

# TODO: boot purecap minimal disk image
