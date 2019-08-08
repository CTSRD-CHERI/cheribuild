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

import shutil

from .crosscompileproject import *
from ...utils import getCompilerInfo, runCmd, IS_FREEBSD

class SNMalloc(CrossCompileCMakeProject):
    projectName = "snmalloc"
    repository = GitRepository("https://github.com/nwf/snmalloc")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    appendCheriBitsToBuildDir = True
    supported_architectures = [CrossCompileTarget.CHERI, CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]
    defaultOptimizationLevel = ["-O2"]
    default_build_type = BuildType.DEBUG
    defaultCMakeBuildType = "Debug"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)

        cls.debug            = cls.addBoolOption("debug", help="Turn on debugging features")
        cls.stats            = cls.addBoolOption("stats", help="Turn on statistics tracking")

        cls.pagemap_pointers = cls.addBoolOption("pagemap-pointers", help="Change pagemap data structure to store pointers")
        cls.pagemap_rederive = cls.addBoolOption("pagemap-rederive", help="Rederive internal pointers using the pagemap")
        cls.cheri_align      = cls.addBoolOption("cheri-align", help="Align sizes for CHERI bounds setting")
        cls.cheri_bounds     = cls.addBoolOption("cheri-bounds", help="Set bounds on returned allocations")

        # XXX Make this set the policy somehow?
        cls.quarantine       = cls.addBoolOption("quarantine", help="Quarantine deallocations")

        cls.qpathresh        = cls.addConfigOption("qpathresh", kind=int,
                                                   help="Quarantine physical memory per allocator threshold")
        cls.qpacthresh       = cls.addConfigOption("qpacthresh",  kind=int,
                                                   help="Quarantine chunk per allocator threshold")
        cls.qcsc             = cls.addConfigOption("qcsc", kind=int,
                                                   help="Quarantine chunk size class")

        cls.revoke           = cls.addBoolOption("revoke", help="Revoke quarantine before reusing")
        cls.revoke_dry_run   = cls.addBoolOption("revoke-dry-run", help="Do everything but caprevoke()")
        cls.revoke_paranoia  = cls.addBoolOption("revoke-paranoia", help="Double-check the revoker")
        cls.revoke_verbose   = cls.addBoolOption("revoke-verbose", help="Report revocation statistics")

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)

        if self.revoke:
            self.quarantine       = True
            self.cheri_bounds     = True
            self.pagemap_rederive = True

        if self.cheri_bounds:
            self.pagemap_rederive = True
            self.cheri_align      = True

        if self.pagemap_rederive:
            self.pagemap_pointers = True

        self.add_cmake_options(USE_REVOCATION=self.revoke)
        self.add_cmake_options(USE_SNMALLOC_STATS=self.stats)
        self.COMMON_FLAGS.append("-DSNMALLOC_CHERI_ALIGN=%d"        % self.cheri_align     )
        self.COMMON_FLAGS.append("-DSNMALLOC_PAGEMAP_POINTERS=%d"   % self.pagemap_pointers)
        self.COMMON_FLAGS.append("-DSNMALLOC_PAGEMAP_REDERIVE=%d"   % self.pagemap_rederive)
        self.COMMON_FLAGS.append("-DSNMALLOC_CHERI_SETBOUNDS=%d"    % self.cheri_bounds    )
        self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_DEALLOC=%d" % self.quarantine      )
        self.COMMON_FLAGS.append("-DSNMALLOC_REVOKE_QUARANTINE=%d"  % self.revoke          )
        self.COMMON_FLAGS.append("-DSNMALLOC_REVOKE_DRY_RUN=%d"     % self.revoke_dry_run  )
        self.COMMON_FLAGS.append("-DSNMALLOC_REVOKE_PARANOIA=%d"    % self.revoke_paranoia )
        self.COMMON_FLAGS.append("-DSNMALLOC_REVOKE_CHATTY=%d"      % self.revoke_verbose  )

        if self.qpathresh is not None:
            self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_PER_ALLOC_THRESHOLD=%d"       % self.qpathresh)

        if self.qpacthresh is not None:
            self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_PER_ALLOC_CHUNK_THRESHOLD=%d" % self.qpacthresh)

        if self.qcsc is not None:
            self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_CHUNK_SIZECLASS=%d"           % self.qcsc)

        if not self.debug:
            self.COMMON_FLAGS.append("-DNDEBUG")

    def install(*args, **kwargs):
        pass
