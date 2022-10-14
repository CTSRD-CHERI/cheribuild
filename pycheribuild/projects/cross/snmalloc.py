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


class SNMalloc(CrossCompileCMakeProject):
    target = "snmalloc"
    repository = GitRepository("https://github.com/nwf/snmalloc")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    default_build_type = BuildType.DEBUG

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        cls.just_so = cls.add_bool_option("just-so", help="Just build the .so shim")
        cls.debug = cls.add_bool_option("debug", help="Turn on debugging features")
        cls.stats = cls.add_bool_option("stats", help="Turn on statistics tracking")

        cls.check_client = cls.add_bool_option("check-client", help="Don't accept malformed input to free")

        cls.pagemap_pointers = cls.add_bool_option("pagemap-pointers",
                                                   help="Change pagemap data structure to store pointers")
        cls.pagemap_rederive = cls.add_bool_option("pagemap-rederive",
                                                   help="Rederive internal pointers using the pagemap")
        cls.cheri_align = cls.add_bool_option("cheri-align", help="Align sizes for CHERI bounds setting")
        cheri_bounds_default = cls._xtarget is not None and cls._xtarget.is_cheri_purecap()
        cls.cheri_bounds = cls.add_bool_option("cheri-bounds", default=cheri_bounds_default,
                                               help="Set bounds on returned allocations")

        cls.quarantine = cls.add_bool_option("quarantine", help="Quarantine deallocations")

        cls.qpathresh = cls.add_config_option("qpathresh", kind=int,
                                              help="Quarantine physical memory per allocator threshold")
        cls.qpacthresh = cls.add_config_option("qpacthresh", kind=int,
                                               help="Quarantine chunk per allocator threshold")
        cls.qcsc = cls.add_config_option("qcsc", kind=int,
                                         help="Quarantine chunk size class")

        cls.decommit = cls.add_config_option("decommit", kind=str,
                                             help="Specify memory decommit policy")

        cls.zero = cls.add_bool_option("zero", help="Specify memory decommit policy")

        cls.revoke = cls.add_bool_option("revoke", help="Revoke quarantine before reusing")
        cls.revoke_dry_run = cls.add_bool_option("revoke-dry-run", help="Do everything but caprevoke()")
        cls.revoke_paranoia = cls.add_bool_option("revoke-paranoia", help="Double-check the revoker")
        cls.revoke_tput = cls.add_bool_option("revoke-throughput", help="Optimize for throughput")

        # XXX misnamed now, but so be it
        cls.revoke_verbose = cls.add_bool_option("revoke-verbose", help="Report revocation statistics")

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
        self.COMMON_FLAGS.append("-DSNMALLOC_CHERI_ALIGN=%d" % self.cheri_align)
        self.COMMON_FLAGS.append("-DSNMALLOC_PAGEMAP_POINTERS=%d" % self.pagemap_pointers)
        self.COMMON_FLAGS.append("-DSNMALLOC_PAGEMAP_REDERIVE=%d" % self.pagemap_rederive)
        self.COMMON_FLAGS.append("-DSNMALLOC_CHERI_SETBOUNDS=%d" % self.cheri_bounds)
        self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_DEALLOC=%d" % self.quarantine)
        self.COMMON_FLAGS.append("-DSNMALLOC_REVOKE_QUARANTINE=%d" % self.revoke)
        self.COMMON_FLAGS.append("-DSNMALLOC_REVOKE_DRY_RUN=%d" % self.revoke_dry_run)
        self.COMMON_FLAGS.append("-DSNMALLOC_REVOKE_PARANOIA=%d" % self.revoke_paranoia)
        self.COMMON_FLAGS.append("-DSNMALLOC_REVOKE_THROUGHPUT=%d" % self.revoke_tput)
        self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_CHATTY=%d" % self.revoke_verbose)
        self.COMMON_FLAGS.append("-DSNMALLOC_DEFAULT_ZERO=%s" %
                                 ("ZeroMem::YesZero" if self.zero else "ZeroMem::NoZero"))

        if self.decommit is not None:
            self.COMMON_FLAGS.append("-DUSE_DECOMMIT_STRATEGY=%s" % self.decommit)

        if self.qpathresh is not None:
            self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_PER_ALLOC_THRESHOLD=%d" % self.qpathresh)

        if self.qpacthresh is not None:
            self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_PER_ALLOC_CHUNK_THRESHOLD=%d" % self.qpacthresh)

        if self.qcsc is not None:
            self.COMMON_FLAGS.append("-DSNMALLOC_QUARANTINE_CHUNK_SIZECLASS=%d" % self.qcsc)

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
