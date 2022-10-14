#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Jessica Clarke
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

from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir
from ..project import MercurialRepository
from ...config.compilation_targets import CompilationTargets


class BuildGmp(CrossCompileAutotoolsProject):
    repository = MercurialRepository("https://gmplib.org/repo/gmp")
    supported_architectures = (CompilationTargets.ALL_CHERIBSD_TARGETS_WITH_HYBRID +
                               CompilationTargets.ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS +
                               CompilationTargets.ALL_SUPPORTED_FREEBSD_TARGETS + [CompilationTargets.NATIVE])
    native_install_dir = DefaultInstallDir.CHERI_SDK

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        # It would be nice if we could just disable building documentation, but until we can do so, missing makeinfo
        # results in failing build
        self.check_required_system_tool("makeinfo", freebsd="texinfo")

    def setup(self):
        super().setup()
        if self.crosscompile_target.is_hybrid_or_purecap_cheri():
            # configure script has checks that rely on implicit prototypes.
            # TODO: Fix and upstream
            self.cross_warning_flags.append("-Wno-error=cheri-prototypes")
        if self.crosscompile_target.is_cheri_purecap():
            # Obviously not ported, so just use generic C versions
            self.configure_args.append("--disable-assembly")

    def configure(self, **kwargs):
        self.run_cmd("./.bootstrap", cwd=self.source_dir)
        super().configure(**kwargs)
