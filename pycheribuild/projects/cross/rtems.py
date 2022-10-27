# -
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

from .crosscompileproject import CompilationTargets, CrossCompileProject, DefaultInstallDir, GitRepository
from ..run_qemu import LaunchQEMUBase


class BuildRtems(CrossCompileProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/rtems",
                               force_branch=True, default_branch="cheri_waf1")
    target = "rtems"
    include_os_in_target_suffix = False
    dependencies = ["newlib", "compiler-rt-builtins"]
    is_sdk_target = True
    needs_sysroot = False  # We don't need a complete sysroot
    supported_architectures = CompilationTargets.ALL_SUPPORTED_RTEMS_TARGETS
    default_install_dir = DefaultInstallDir.ROOTFS_LOCALBASE

    # RTEMS BSPs to build
    rtems_bsps = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.target_info.target.is_cheri_purecap():
            self.rtems_bsps = ["rv64imafdcxcheri_medany", "rv64xcheri_gfe", "rv64xcheri_qemu"]
        else:
            self.rtems_bsps = ["rv64imafdc_medany"]

    def _run_waf(self, *args, **kwargs):
        cmdline = [self.source_dir / "waf", "-t", self.source_dir, "-o", self.build_dir] + list(args)
        if self.config.verbose:
            cmdline.append("-v")
        return self.run_cmd(cmdline, cwd=self.source_dir, **kwargs)

    def configure(self, **kwargs):
        waf_run = self._run_waf("bsp_defaults", "--rtems-bsps=" + ",".join(self.rtems_bsps), "--rtems-compiler=clang",
                                capture_output=True)

        # waf configure reads config.ini by default to read RTEMS flags from
        self.write_file(self.source_dir / "config.ini", str(waf_run.stdout, 'utf-8'), overwrite=True)
        self._run_waf("configure", "--prefix", self.destdir)

    def compile(self, **kwargs):
        self._run_waf("build", self.config.make_j_flag)

    def install(self, **kwargs):
        self._run_waf("install")

    def process(self):
        with self.set_env(PATH=str(self.sdk_bindir) + ":" + os.getenv("PATH", ""),
                          CFLAGS="--sysroot=" + str(self.sdk_sysroot),
                          LDFLAGS="--sysroot=" + str(self.sdk_sysroot)):
            super().process()


class LaunchRtemsQEMU(LaunchQEMUBase):
    target = "run-rtems"
    dependencies = ["rtems"]
    supported_architectures = [CompilationTargets.RTEMS_RISCV64_PURECAP]
    forward_ssh_port = False
    qemu_user_networking = False
    _enable_smbfs_support = False
    _add_virtio_rng = False

    def setup(self):
        super().setup()
        self.kernel_project = BuildRtems.get_instance(self)
        self.current_kernel = self.kernel_project.build_dir / "riscv/rv64xcheri_qemu/testsuites/samples/capture.exe"

    def get_riscv_bios_args(self) -> "list[str]":
        # Run a simple RTEMS shell application (run in machine mode using the -bios none)
        return ["-bios", "none"]

    def process(self):
        super().process()
