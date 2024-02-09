#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2024 John Baldwin
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

from .crosscompileproject import (
    CrossCompileAutotoolsProject,
    DefaultInstallDir,
    GitRepository,
)
from .gmp import BuildGmp
from ...config.compilation_targets import CompilationTargets


class BuildMpfr(CrossCompileAutotoolsProject):
    repository = GitRepository("https://gitlab.inria.fr/mpfr/mpfr.git")
    supported_architectures = (
        CompilationTargets.ALL_CHERIBSD_TARGETS_WITH_HYBRID
        + CompilationTargets.ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS
        + CompilationTargets.ALL_SUPPORTED_FREEBSD_TARGETS
        + CompilationTargets.ALL_NATIVE
    )
    native_install_dir = DefaultInstallDir.CHERI_SDK

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        # It would be nice if we could just disable building documentation, but until we can do so, missing makeinfo
        # results in failing build
        self.check_required_system_tool("makeinfo", default="texinfo")
        if self.compiling_for_host():
            self.check_required_pkg_config("gmp", freebsd="gmp")

    def setup(self):
        super().setup()
        self.configure_args.append("--with-gmp=" + str(BuildGmp.get_install_dir(self)))

    def install(self, **kwargs):
        super().install(**kwargs)
        if not self.compiling_for_host():
            self.delete_file(self.install_dir / "lib/libmpfr.la", warn_if_missing=True)
