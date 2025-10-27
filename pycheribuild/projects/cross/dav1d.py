#-
# Copyright (c) 2025 Konrad Witaszczyk
#
# SPDX-License-Identifier: BSD-2-Clause
#
# This software was developed by SRI International, the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology), and Capabilities Limited under Defense Advanced Research
# Projects Agency (DARPA) Contract No. FA8750-24-C-B047 ("DEC").
#

from .crosscompileproject import CompilationTargets, CrossCompileMesonProject, GitRepository

class BuildDav1d(CrossCompileMesonProject):
    target = "dav1d"
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE
    repository = GitRepository(
        "https://code.videolan.org/videolan/dav1d.git",
        temporary_url_override="https://github.com/CTSRD-CHERI/dav1d.git",
        default_branch="1.2.1-cheriabi",
    )

    def setup(self):
        super().setup()
        self.add_meson_options(enable_asm="false")
