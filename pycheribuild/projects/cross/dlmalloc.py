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

from .crosscompileproject import CrossCompileProject, DefaultInstallDir, GitRepository, MakeCommandKind
from ..simple_project import (
    BoolConfigOption,
    OptionalFloatConfigOption,
    OptionalIntConfigOption,
)
from ...processutils import commandline_to_str


class DLMalloc(CrossCompileProject):
    target = "dlmalloc"
    repository = GitRepository("https://github.com/CTSRD-CHERI/dlmalloc_nonreuse")
    make_kind = MakeCommandKind.GnuMake
    native_install_dir = DefaultInstallDir.CHERI_SDK

    just_so = BoolConfigOption("just-so", help="Just build the .so shim")
    debug = BoolConfigOption("debug", help="Turn on debugging features")
    cheri_set_bounds = BoolConfigOption("cheri-bounds", default=True, help="Set bounds on allocations")
    qmabs = OptionalIntConfigOption("qmabs", help="Quarantine memory absolute threshold")
    qmratio = OptionalFloatConfigOption("qmratio", help="Quarantine memory ratio threshold")
    qmmin = OptionalIntConfigOption("qmmin", help="Minimum amount quarantined to trigger a revocation based on ratio")
    revoke = BoolConfigOption("revoke", help="Revoke quarantine before reusing")
    consolidate_on_free = BoolConfigOption("consolidate", default=True, help="Consolidate memory when quarantining")
    zero_memory = BoolConfigOption("zero-memory", help="Zero allocated memory")
    stats_at_exit = BoolConfigOption("stats-at-exit", default=True, help="print statistics on exit")
    unmap_support = BoolConfigOption("unmap-support", default=True, help="support for unmapping")
    unmap_threshold = OptionalIntConfigOption(
        "unmap-threshold",
        help="Threshold (in pages) at which interior pages of quanantined chunks are unmapped",
    )
    quar_unsafe = BoolConfigOption("unsafe-quarantine", help="Don't isolate quarantine structures")

    def setup(self):
        super().setup()
        if self.cheri_set_bounds:
            self.CFLAGS.append("-DCHERI_SET_BOUNDS")

        if self.revoke:
            self.CFLAGS.append("-DCAPREVOKE")

        if self.qmabs:
            self.CFLAGS.append(f"-DDEFAULT_MAX_FREEBUFBYTES={self.qmabs}")

        if self.qmratio:
            self.CFLAGS.append(f"-DDEFAULT_FREEBUF_PERCENT={self.qmratio:f}")

        if self.qmmin:
            self.CFLAGS.append(f"-DDEFAULT_MIN_FREEBUFBYTES={self.qmmin}")

        if self.consolidate_on_free:
            self.CFLAGS.append("-DCONSOLIDATE_ON_FREE=1")
        else:
            self.CFLAGS.append("-DCONSOLIDATE_ON_FREE=0")

        if self.zero_memory:
            self.CFLAGS.append("-DZERO_MEMORY=1")
        else:
            self.CFLAGS.append("-DZERO_MEMORY=0")

        if self.unmap_support:
            self.CFLAGS.append("-DSUPPORT_UNMAP=1")
        else:
            self.CFLAGS.append("-DSUPPORT_UNMAP=0")

        if self.unmap_threshold:
            self.CFLAGS.append(f"-DDEFAULT_UNMAP_THRESHOLD={self.unmap_threshold}")

        if not self.quar_unsafe:
            self.CFLAGS.append("-DSAFE_FREEBUF")

        if self.stats_at_exit:
            self.CFLAGS.append("-DSWEEP_STATS=1")

        self.make_args.add_flags("-f", self.source_dir / "Makefile.cheribuild")
        self.make_args.set(DEBUG=self.debug)
        self.make_args.set(CAPREVOKE=self.revoke)
        self.make_args.set(SRCDIR=self.source_dir)
        self.make_args.set_env(CC=self.CC, CFLAGS=commandline_to_str(self.default_compiler_flags() + self.CFLAGS))
        if not self.compiling_for_host():
            self.make_args.set_env(CHERI_SDK=self.target_info.sdk_root_dir)

    def compile(self, **kwargs):
        if self.just_so:
            self.run_make("libdlmalloc_nonreuse.so", cwd=self.build_dir)
        else:
            self.run_make("all", cwd=self.build_dir)

    def install(*args, **kwargs):
        pass
