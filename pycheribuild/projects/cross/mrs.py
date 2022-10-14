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


class MRS(CrossCompileCMakeProject):
    target = "mrs"
    repository = GitRepository("https://github.com/CTSRD-CHERI/mrs")
    supported_architectures = [CompilationTargets.CHERIBSD_RISCV_PURECAP, CompilationTargets.CHERIBSD_MORELLO_PURECAP]
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE

    # set --mrs/build-type <type> to control build type, default is RelWithDebInfo

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        cls.build_target = cls.add_config_option("build-target", kind=str, help="specify a target to build, or all")

        cls.debug = cls.add_bool_option("debug", help="enable debug output")
        cls.offload_quarantine = cls.add_bool_option("offload-quarantine",
                                                     help="process the quarantine in a separate worker thread")
        cls.bypass_quarantine = cls.add_bool_option("bypass-quarantine", help="MADV_FREE freed page-size allocations")
        cls.clear_on_alloc = cls.add_bool_option("clear-on-alloc", help="zero regions during allocation")
        cls.clear_on_free = cls.add_bool_option("clear-on-free", help="zero regions as they come out of quarantine")
        cls.print_stats = cls.add_bool_option("print-stats", help="print heap statistics on exit")
        cls.print_caprevoke = cls.add_bool_option("print-caprevoke", help="print per-revocation statistics")
        cls.concurrent_revocation_passes = cls.add_config_option("concurrent-revocation-passes", kind=int,
                                                                 help="enable N concurrent revocation passes before "
                                                                      "the stop-the-world pass")
        cls.revoke_on_free = cls.add_bool_option("revoke-on-free",
                                                 help="perform revocation on free rather than during allocation "
                                                      "routines")

        cls.just_interpose = cls.add_bool_option("just-interpose", help="just call the real functions")
        cls.just_bookkeeping = cls.add_bool_option("just-bookkeeping", help="just update data structures")
        cls.just_quarantine = cls.add_bool_option("just-quarantine", help="do bookkeeping and quarantining")
        cls.just_paint_bitmap = cls.add_bool_option("just-paint-bitmap",
                                                    help="do bookkeeping, quarantining, and bitmap painting")

        cls.quarantine_ratio = cls.add_config_option("quarantine-ratio", kind=int,
                                                     help="limit the quarantine size to 1/QUARANTINE_RATIO times the "
                                                          "size of the heap")
        cls.quarantine_highwater = cls.add_config_option("quarantine-highwater", kind=int,
                                                         help="limit the quarantine size to QUARANTINE_HIGHWATER "
                                                              "bytes (supersedes QUARANTINE_RATIO)")

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
