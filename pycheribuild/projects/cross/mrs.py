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

import shutil

from .crosscompileproject import *


class MRS(CrossCompileCMakeProject):
    projectName = "mrs"
    repository = GitRepository("https://github.com/CTSRD-CHERI/mrs")
    appendCheriBitsToBuildDir = True
    supported_architectures = [CrossCompileTarget.CHERIBSD_MIPS_PURECAP]

    # set --mrs/build-type <type> to control build type, default is RelWithDebInfo

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)

        cls.build_target= cls.addConfigOption("build-target", kind=str, help="specify a target to build, or all")

        cls.debug = cls.addBoolOption("debug", help="enable debug output")
        cls.offload_quarantine = cls.addBoolOption("offload-quarantine", help="process the quarantine in a separate worker thread")
        cls.bypass_quarantine = cls.addBoolOption("bypass-quarantine", help="MADV_FREE freed page-size allocations")
        cls.clear_on_alloc= cls.addBoolOption("clear-on-alloc", help="zero regions during allocation")
        cls.clear_on_free= cls.addBoolOption("clear-on-free", help="zero regions as they come out of quarantine")
        cls.print_stats= cls.addBoolOption("print-stats", help="print heap statistics on exit")
        cls.print_caprevoke= cls.addBoolOption("print-caprevoke", help="print per-revocation statistics")
        cls.concurrent_revocation_pass = cls.addBoolOption("concurrent-revocation-pass", help="enable a concurrent revocation pass before the stop-the-world pass")
        cls.revoke_on_free = cls.addBoolOption("revoke-on-free", help="perform revocation on free rather than during allocation routines")

        cls.just_interpose = cls.addBoolOption("just-interpose", help="just call the real functions")
        cls.just_bookkeeping = cls.addBoolOption("just-bookkeeping", help="just update data structures")
        cls.just_quarantine = cls.addBoolOption("just-quarantine", help="do bookkeeping and quarantining")
        cls.just_paint_bitmap = cls.addBoolOption("just-paint-bitmap", help="do bookkeeping, quarantining, and bitmap painting")

        cls.quarantine_ratio = cls.addConfigOption("quarantine-ratio", kind=int, help="limit the quarantine size to 1/QUARANTINE_RATIO times the size of the heap")
        cls.quarantine_highwater = cls.addConfigOption("quarantine-highwater", kind=int, help="limit the quarantine size to QUARANTINE_HIGHWATER bytes (supersedes QUARANTINE_RATIO)")

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)

        if self.debug:
            self.add_cmake_options(DEBUG="ON")
        if self.offload_quarantine:
            self.add_cmake_options(OFFLOAD_QUARANTINE="ON")
        if self.bypass_quarantine:
            self.add_cmake_options(BYPASS_QUARANTINE="ON")
        if self.clear_on_alloc:
            self.add_cmake_options(CLEAR_ON_ALLOC="ON")
        if self.clear_on_free:
            self.add_cmake_options(CLEAR_ON_FREE="ON")
        if self.print_stats:
            self.add_cmake_options(PRINT_STATS="ON")
        if self.print_caprevoke:
            self.add_cmake_options(PRINT_CAPREVOKE="ON")
        if self.concurrent_revocation_pass:
            self.add_cmake_options(CONCURRENT_REVOCATION_PASS="ON")
        if self.revoke_on_free:
            self.add_cmake_options(REVOKE_ON_FREE="ON")

        if self.just_interpose:
            self.add_cmake_options(JUST_INTERPOSE="ON")
        if self.just_bookkeeping:
            self.add_cmake_options(JUST_BOOKKEEPING="ON")
        if self.just_quarantine:
            self.add_cmake_options(JUST_QUARANTINE="ON")
        if self.just_paint_bitmap:
            self.add_cmake_options(JUST_PAINT_BITMAP="ON")

        if self.quarantine_ratio:
            self.add_cmake_options(QUARANTINE_RATIO=self.quarantine_ratio)
        if self.quarantine_highwater:
            self.add_cmake_options(QUARANTINE_HIGHWATER=self.quarantine_highwater)

    def compile(self, **kwargs):
        if self.build_target:
            # self.runMake("libsnmallocshim.so", cwd=kwargs.get("cwd"))
            self.runMake(self.build_target)
        else:
            return super().compile(**kwargs)

    def install(*args, **kwargs):
        pass
