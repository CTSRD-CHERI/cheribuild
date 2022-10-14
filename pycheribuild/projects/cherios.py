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

from .cmake_project import CMakeProject
from .project import BuildType, GitRepository, ComputedDefaultValue
from ..config.compilation_targets import CompilationTargets


class BuildCheriOS(CMakeProject):
    dependencies = ["cherios-llvm", "makefs-linux"]
    default_build_type = BuildType.DEBUG
    repository = GitRepository("https://github.com/CTSRD-CHERI/cherios.git", default_branch="master")
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, p: config.output_root / ("cherios" +
                                                         p.crosscompile_target.build_suffix(config, include_os=False)),
        as_string="$OUTPUT_ROOT/cherios-{mips64,riscv64}")
    needs_sysroot = False
    supported_architectures = [CompilationTargets.CHERIOS_MIPS_PURECAP, CompilationTargets.CHERIOS_RISCV_PURECAP]

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.smp_cores = cls.add_config_option("smp-cores", default=1, kind=int)
        cls.build_net = cls.add_bool_option("build-net", default=False)

    def setup(self):
        super().setup()
        self.add_cmake_options(CHERI_SDK_DIR=self.target_info.sdk_root_dir)
        self.add_cmake_options(BUILD_FOR_CHERI128=self.config.mips_cheri_bits == 128)
        self.add_cmake_options(BUILD_WITH_NET=self.build_net)
        self.add_cmake_options(SMP_CORES=self.smp_cores)
        self.add_cmake_options(CMAKE_AR=self.sdk_bindir / "llvm-ar")
        self.add_cmake_options(CMAKE_RANLIB=self.sdk_bindir / "llvm-ranlib")
        self.add_cmake_options(PLATFORM=self.crosscompile_target.base_target_suffix)

    def install(self, **kwargs):
        pass  # nothing to install yet
