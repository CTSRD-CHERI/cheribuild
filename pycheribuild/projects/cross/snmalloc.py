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

from .crosscompileproject import BuildType, CrossCompileCMakeProject, DefaultInstallDir, GitRepository
from ..project import ComputedDefaultValue
from ..simple_project import (
    BoolConfigOption,
    OptionalIntConfigOption,
    OptionalStringConfigOption,
)


class SNMalloc(CrossCompileCMakeProject):
    target = "snmalloc"
    repository = GitRepository("https://github.com/nwf/snmalloc")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    default_build_type = BuildType.DEBUG

    just_so = BoolConfigOption("just-so", help="Just build the .so shim")
    debug = BoolConfigOption("debug", help="Turn on debugging features")
    stats = BoolConfigOption("stats", help="Turn on statistics tracking")
    check_client = BoolConfigOption("check-client", help="Don't accept malformed input to free")
    pagemap_pointers = BoolConfigOption("pagemap-pointers", help="Change pagemap data structure to store pointers")
    pagemap_rederive = BoolConfigOption("pagemap-rederive", help="Rederive internal pointers using the pagemap")
    cheri_align = BoolConfigOption("cheri-align", help="Align sizes for CHERI bounds setting")
    cheri_bounds = BoolConfigOption(
        "cheri-bounds",
        default=ComputedDefaultValue(
            function=lambda config, proj: proj.crosscompile_target.is_cheri_purecap(),
            as_string="True if compiling for CHERI purecap, otherwise False",
        ),
        help="Set bounds on returned allocations",
    )
    quarantine = BoolConfigOption("quarantine", help="Quarantine deallocations")
    qpathresh = OptionalIntConfigOption("qpathresh", help="Quarantine physical memory per allocator threshold")
    qpacthresh = OptionalIntConfigOption("qpacthresh", help="Quarantine chunk per allocator threshold")
    qcsc = OptionalIntConfigOption("qcsc", help="Quarantine chunk size class")
    decommit = OptionalStringConfigOption("decommit", help="Specify memory decommit policy")
    zero = BoolConfigOption("zero", help="Specify memory decommit policy")
    revoke = BoolConfigOption("revoke", help="Revoke quarantine before reusing")
    revoke_dry_run = BoolConfigOption("revoke-dry-run", help="Do everything but caprevoke()")
    revoke_paranoia = BoolConfigOption("revoke-paranoia", help="Double-check the revoker")
    revoke_tput = BoolConfigOption("revoke-throughput", help="Optimize for throughput")
    revoke_verbose = BoolConfigOption("revoke-verbose", help="Report revocation statistics")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.revoke:
            self.quarantine = True
            self.cheri_bounds = True
            self.pagemap_rederive = True

        if self.cheri_bounds:
            self.pagemap_rederive = True
            self.cheri_align = True

        if self.pagemap_rederive:
            self.pagemap_pointers = True

        self.add_cmake_options(USE_REVOCATION=self.revoke)
        self.add_cmake_options(USE_SNMALLOC_STATS=self.stats)
        self.COMMON_FLAGS.append(f"-DSNMALLOC_CHERI_ALIGN={int(self.cheri_align)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_PAGEMAP_POINTERS={int(self.pagemap_pointers)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_PAGEMAP_REDERIVE={int(self.pagemap_rederive)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_CHERI_SETBOUNDS={int(self.cheri_bounds)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_QUARANTINE_DEALLOC={int(self.quarantine)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_REVOKE_QUARANTINE={int(self.revoke)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_REVOKE_DRY_RUN={int(self.revoke_dry_run)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_REVOKE_PARANOIA={int(self.revoke_paranoia)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_REVOKE_THROUGHPUT={int(self.revoke_tput)}")
        self.COMMON_FLAGS.append(f"-DSNMALLOC_QUARANTINE_CHATTY={int(self.revoke_verbose)}")
        self.COMMON_FLAGS.append(
            "-DSNMALLOC_DEFAULT_ZERO=%s" % ("ZeroMem::YesZero" if self.zero else "ZeroMem::NoZero")
        )

        if self.decommit is not None:
            self.COMMON_FLAGS.append(f"-DUSE_DECOMMIT_STRATEGY={self.decommit}")

        if self.qpathresh is not None:
            self.COMMON_FLAGS.append(f"-DSNMALLOC_QUARANTINE_PER_ALLOC_THRESHOLD={self.qpathresh}")

        if self.qpacthresh is not None:
            self.COMMON_FLAGS.append(f"-DSNMALLOC_QUARANTINE_PER_ALLOC_CHUNK_THRESHOLD={self.qpacthresh}")

        if self.qcsc is not None:
            self.COMMON_FLAGS.append(f"-DSNMALLOC_QUARANTINE_CHUNK_SIZECLASS={self.qcsc}")

        if not self.debug:
            self.COMMON_FLAGS.append("-DNDEBUG")

        if self.check_client:
            self.COMMON_FLAGS.append("-DCHECK_CLIENT")

    def compile(self, **kwargs):
        if self.just_so:
            self.run_make("libsnmallocshim.so", cwd=kwargs.get("cwd"))
        else:
            return super().compile(**kwargs)

    def install(*args, **kwargs):
        pass
