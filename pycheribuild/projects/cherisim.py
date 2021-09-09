#
# Copyright (c) 2016 Alex Richardson
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
import shlex
from pathlib import Path

from .project import (BasicCompilationTargets, CheriConfig, DefaultInstallDir, GitRepository, MakeCommandKind, Project,
                      ReuseOtherProjectRepository)
from ..utils import OSInfo


class BuildBluespecCompiler(Project):
    target = "bluespec-compiler"
    default_directory_basename = "bsc"
    repository = GitRepository("https://github.com/B-Lang-org/bsc.git")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS
    build_in_source_dir = True
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.add_required_system_tool("ghc", apt="ghc", homebrew="ghc")
        self.add_required_system_tool("cabal", apt="cabal-install", homebrew="cabal-install")
        for i in ("autoconf", "gperf", "bison", "flex"):
            self.add_required_system_tool(i, homebrew=i)
        self.make_args.set(PREFIX=self.install_dir)

    def compile(self, **kwargs):
        try:
            self.run_make("all")
        except Exception:
            self.info("Compilation failed. If it complains about missing packages try running:\n"
                      "\tcabal install regex-compat syb old-time split\n"
                      "If this doesn't fix the issue `v1-install` instead of `install` (e.g. macOS).")
            if OSInfo.IS_MAC:
                self.info("Alternatively, try running:",
                          self.source_dir / ".github/workflows/install_dependencies_macos.sh")
            elif OSInfo.is_ubuntu():
                self.info("Alternatively, try running:",
                          self.source_dir / ".github/workflows/install_dependencies_ubuntu.sh")
            raise


class BuildCheriSim(Project):
    target = "cheri-sim"
    default_directory_basename = "cheri-cpu"
    dependencies = ["bluespec-compiler"]
    repository = GitRepository("git@github.com:CTSRD-CHERI/cheri-cpu")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    build_in_source_dir = True  # Needs to build in the source dir
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # TODO: move this to project
        self.add_required_system_tool("dtc", apt="device-tree-compiler", homebrew="dtc")
        self.add_required_system_tool("bsc", cheribuild_target="bluespec-compiler")
        self.add_required_system_header("mpfr.h", apt="libmpfr-dev")
        self.make_args.set(COP1="1" if self.build_fpu else "0")
        if self.build_cheri:
            if self.config.mips_cheri_bits == 128:
                self.make_args.set(CAP128="1")
            else:
                self.make_args.set(CAP="1")
        self.make_args.set(NOPRINTS="1")  # This massively speeds up the simulator

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_fpu = cls.add_bool_option("fpu", default=True, help="include the FPU code")
        cls.build_cheri = cls.add_bool_option("cheri", default=True,
                                              help="include the CHERI code in the simulator. If false build BERI")

    def clean(self):
        self.run_make("clean", parallel=False, cwd=self.source_dir / "cheri")
        return None

    def compile(self, **kwargs):
        setup_sh = self.source_dir / "cheri" / "setup.sh"
        if self.config.fpga_custom_env_setup_script:
            setup_sh = self.config.fpga_custom_env_setup_script
        if not setup_sh.exists():
            self.fatal("Could not find setup.sh, please set --cheri-sim/source-directory or --fpga-env-setup-script")
        source_cmd = "source {setup_script}".format(setup_script=setup_sh)
        self.run_shell_script(
            source_cmd + " && " + self.commandline_to_str(self.get_make_commandline("sim", parallel=False)),
            cwd=self.source_dir / "cheri", shell="bash")

    def install(self, **kwargs):
        pass

    def process(self):
        if OSInfo.is_ubuntu() and not Path("/usr/lib/x86_64-linux-gnu/libgmp.so.3").exists():
            # BSC needs libgmp.so.3
            self.fatal("libgmp.so.3 is needed to run BSC",
                       fixit_hint="Creating a symlink to /usr/lib/x86_64-linux-gnu/libgmp.so.10 seems to work.\n"
                                  "\t\tTry running `sudo ln -s libgmp.so.10 /usr/lib/x86_64-linux-gnu/libgmp.so.3`")
        super().process()


class BuildBeriCtl(Project):
    target = "berictl"
    repository = ReuseOtherProjectRepository(source_project=BuildCheriSim, subdirectory="cherilibs/tools/debug",
                                             do_update=True)
    native_install_dir = DefaultInstallDir.CHERI_SDK
    build_in_source_dir = True  # Needs to build in the source dir
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.make_args.set(JTAG_ATLANTIC=1)  # MUCH faster

    def clean(self):
        self.run_make("clean", parallel=False, cwd=self.source_dir)
        return None

    def compile(self, **kwargs):
        sim_project = BuildCheriSim.get_instance(self, cross_target=BasicCompilationTargets.NATIVE)
        setup_sh = sim_project.source_dir / "cheri" / "setup.sh"
        if self.config.fpga_custom_env_setup_script:
            setup_sh = self.config.fpga_custom_env_setup_script
        if not setup_sh.exists():
            self.fatal("Could not find setup.sh")
        self.run_shell_script("source {} && ".format(shlex.quote(str(setup_sh))) + self.commandline_to_str(
            self.get_make_commandline("", parallel=False)), cwd=self.source_dir, shell="bash")

    def install(self, **kwargs):
        pass

    def process(self):
        if OSInfo.is_ubuntu() and not Path("/usr/lib/x86_64-linux-gnu/libgmp.so.3").exists():
            # BSC needs libgmp.so.3
            self.fatal("libgmp.so.3 is needed to run BSC",
                       fixit_hint="Creating a symlink to /usr/lib/x86_64-linux-gnu/libgmp.so.10 seems to work.\n"
                                  "\t\tTry running `sudo ln -s libgmp.so.10 /usr/lib/x86_64-linux-gnu/libgmp.so.3`")
        super().process()
