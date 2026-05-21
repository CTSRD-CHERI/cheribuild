#
# Copyright (c) 2019 Brett F. Gutstein
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

from .crosscompileproject import CompilationTargets, CrossCompileCMakeProject, DefaultInstallDir, GitRepository
from ..simple_project import (
    BoolConfigOption,
    OptionalIntConfigOption,
    OptionalStringConfigOption,
)


class MRS(CrossCompileCMakeProject):
    target = "mrs"
    repository = GitRepository("https://github.com/CTSRD-CHERI/mrs")
    _supported_architectures = (CompilationTargets.CHERIBSD_RISCV_PURECAP, CompilationTargets.CHERIBSD_MORELLO_PURECAP)
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE

    # set --mrs/build-type <type> to control build type, default is RelWithDebInfo

    build_target = OptionalStringConfigOption("build-target", help="specify a target to build, or all")
    debug = BoolConfigOption("debug", help="enable debug output")
    offload_quarantine = BoolConfigOption(
        "offload-quarantine", help="process the quarantine in a separate worker thread"
    )
    bypass_quarantine = BoolConfigOption("bypass-quarantine", help="MADV_FREE freed page-size allocations")
    clear_on_alloc = BoolConfigOption("clear-on-alloc", help="zero regions during allocation")
    clear_on_free = BoolConfigOption("clear-on-free", help="zero regions as they come out of quarantine")
    print_stats = BoolConfigOption("print-stats", help="print heap statistics on exit")
    print_caprevoke = BoolConfigOption("print-caprevoke", help="print per-revocation statistics")
    concurrent_revocation_passes = OptionalIntConfigOption(
        "concurrent-revocation-passes",
        help="enable N concurrent revocation passes before the stop-the-world pass",
    )
    revoke_on_free = BoolConfigOption(
        "revoke-on-free", help="perform revocation on free rather than during allocation routines"
    )

    just_interpose = BoolConfigOption("just-interpose", help="just call the real functions")
    just_bookkeeping = BoolConfigOption("just-bookkeeping", help="just update data structures")
    just_quarantine = BoolConfigOption("just-quarantine", help="do bookkeeping and quarantining")
    just_paint_bitmap = BoolConfigOption("just-paint-bitmap", help="do bookkeeping, quarantining, and bitmap painting")

    quarantine_ratio = OptionalIntConfigOption(
        "quarantine-ratio",
        help="limit the quarantine size to 1/QUARANTINE_RATIO times the size of the heap",
    )
    quarantine_highwater = OptionalIntConfigOption(
        "quarantine-highwater",
        help="limit the quarantine size to QUARANTINE_HIGHWATER bytes (supersedes QUARANTINE_RATIO)",
    )

    def setup(self):
        super().setup()
        if self.debug:
            self.add_cmake_options(DEBUG=True)
        if self.offload_quarantine:
            self.add_cmake_options(OFFLOAD_QUARANTINE=True)
        if self.bypass_quarantine:
            self.add_cmake_options(BYPASS_QUARANTINE=True)
        if self.clear_on_alloc:
            self.add_cmake_options(CLEAR_ON_ALLOC=True)
        if self.clear_on_free:
            self.add_cmake_options(CLEAR_ON_FREE=True)
        if self.print_stats:
            self.add_cmake_options(PRINT_STATS=True)
        if self.print_caprevoke:
            self.add_cmake_options(PRINT_CAPREVOKE=True)
        if self.revoke_on_free:
            self.add_cmake_options(REVOKE_ON_FREE=True)

        if self.just_interpose:
            self.add_cmake_options(JUST_INTERPOSE=True)
        if self.just_bookkeeping:
            self.add_cmake_options(JUST_BOOKKEEPING=True)
        if self.just_quarantine:
            self.add_cmake_options(JUST_QUARANTINE=True)
        if self.just_paint_bitmap:
            self.add_cmake_options(JUST_PAINT_BITMAP=True)

        if self.quarantine_ratio:
            self.add_cmake_options(QUARANTINE_RATIO=self.quarantine_ratio)
        if self.quarantine_highwater:
            self.add_cmake_options(QUARANTINE_HIGHWATER=self.quarantine_highwater)
        if self.concurrent_revocation_passes:
            self.add_cmake_options(CONCURRENT_REVOCATION_PASSES=self.concurrent_revocation_passes)

    def compile(self, **kwargs):
        if self.build_target:
            # self.run_make("libsnmallocshim.so", cwd=kwargs.get("cwd"))
            self.run_make(self.build_target)
        else:
            return super().compile(**kwargs)

    def install(*args, **kwargs):
        pass
