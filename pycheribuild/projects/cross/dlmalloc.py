#
# Copyright (c) 2019 Nathaniel Filardo
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

from .crosscompileproject import *
from ...utils import setEnv

class DLMalloc(CrossCompileProject):
    project_name = "dlmalloc"
    repository = GitRepository("https://github.com/CTSRD-CHERI/dlmalloc_nonreuse")
    appendCheriBitsToBuildDir = True
    supported_architectures = [CrossCompileTarget.CHERIBSD_MIPS_PURECAP, CrossCompileTarget.NATIVE, CrossCompileTarget.CHERIBSD_MIPS]
    defaultOptimizationLevel = ["-O2"]
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        cls.just_so          = cls.addBoolOption("just-so", help="Just build the .so shim")
        cls.debug            = cls.addBoolOption("debug", help="Turn on debugging features")

        cls.cheri_set_bounds = cls.addBoolOption("cheri-bounds", default=True, help="Set bounds on allocations")

        cls.qmabs            = cls.addConfigOption("qmabs", kind=int,
                                                   help="Quarantine memory absolute threshold")

        cls.qmratio          = cls.addConfigOption("qmratio", kind=float,
                                                   help="Quarantine memory ratio threshold")

        cls.qmmin            = cls.addConfigOption("qmmin", kind=int,
                                                   help="Minimum amount quarantined to trigger a revocation based on ratio")

        cls.revoke           = cls.addBoolOption("revoke", help="Revoke quarantine before reusing")

        cls.consolidate_on_free = cls.addBoolOption("consolidate", default=True, help="Consolidate memory when quarantining")

        cls.zero_memory      = cls.addBoolOption("zero-memory", help="Zero allocated memory")

        cls.stats_at_exit    = cls.addBoolOption("stats-at-exit", default=True, help="print statistics on exit")

        cls.unmap_support    = cls.addBoolOption("unmap-support", default=True, help="support for unmapping")

        cls.unmap_threshold  = cls.addConfigOption("unmap-threshold", kind=int,
                                                   help="Threshold (in pages) at which interior pages of quanantined chunks are unmapped")
        cls.quar_unsafe      = cls.addBoolOption("unsafe-quarantine",
                                                 help="Don't isolate quarantine structures")

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)

    def compile(self, **kwargs):
        if self.cheri_set_bounds :
            self.CFLAGS.append("-DCHERI_SET_BOUNDS")

        if self.revoke :
            self.CFLAGS.append("-DCAPREVOKE")

        if self.qmabs :
            self.CFLAGS.append("-DDEFAULT_MAX_FREEBUFBYTES=%d" % self.qmabs)

        if self.qmratio :
            self.CFLAGS.append("-DDEFAULT_FREEBUF_PERCENT=%f" % self.qmratio)

        if self.qmmin :
            self.CFLAGS.append("-DDEFAULT_MIN_FREEBUFBYTES=%d" % self.qmmin)

        if self.consolidate_on_free :
            self.CFLAGS.append("-DCONSOLIDATE_ON_FREE=1")
        else :
            self.CFLAGS.append("-DCONSOLIDATE_ON_FREE=0")

        if self.zero_memory :
            self.CFLAGS.append("-DZERO_MEMORY=1")
        else :
            self.CFLAGS.append("-DZERO_MEMORY=0")

        if self.unmap_support :
            self.CFLAGS.append("-DSUPPORT_UNMAP=1")
        else :
            self.CFLAGS.append("-DSUPPORT_UNMAP=0")

        if self.unmap_threshold :
            self.CFLAGS.append("-DDEFAULT_UNMAP_THRESHOLD=%d" % self.unmap_threshold)

        if not self.quar_unsafe :
            self.CFLAGS.append("-DSAFE_FREEBUF")

        if self.stats_at_exit:
            self.CFLAGS.append("-DSWEEP_STATS=1")

        self.make_args.add_flags("-f", self.sourceDir / "Makefile.cheribuild")
        self.make_args.set(DEBUG=self.debug)
        self.make_args.set(CAPREVOKE=self.revoke)
        self.make_args.set(SRCDIR=self.sourceDir)
        if not self.compiling_for_host():
            self.CFLAGS.append("--sysroot=%s" % self.sdk_sysroot)
        with setEnv(CHERI_SDK=self.config.sdkDir,
                    CC=self.config.sdkBinDir/"clang",
                    CFLAGS=commandline_to_str(self.default_compiler_flags + self.CFLAGS)):
            if self.just_so :
                self.runMake("libdlmalloc_nonreuse.so", cwd=self.buildDir)
            else :
                self.runMake("all", cwd=self.buildDir)

    def install(*args, **kwargs):
        pass
