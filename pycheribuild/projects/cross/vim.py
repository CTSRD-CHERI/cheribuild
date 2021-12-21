#
# Copyright (c) 2021 Jessica Clarke
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

from .crosscompileproject import CrossCompileAutotoolsProject, GitRepository
from ...config.compilation_targets import FreeBSDTargetInfo


class BuildVim(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/vim/vim")
    build_via_symlink_farm = True

    def setup(self):
        super().setup()
        self.configure_args.extend(["--disable-gui", "--without-x"])
        if self.compiling_for_cheri():
            # Options storage uses void * with some longs stuffed in
            # TODO: Upstream using intptr_t?
            self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")
        if not self.compiling_for_host():
            assert isinstance(self.target_info, FreeBSDTargetInfo)
            # Various configure checks that require execution; extract with:
            #
            #     sed -n "/cross-compiling: please set/{s/.[^']*'//;s/'.*//;p;}" src/configure.ac
            #
            # Plus a bonus vim_cv_tgetent that has the wrong error message, and:
            #
            #     dnl When cross-compiling set $vim_cv_uname_output, $vim_cv_uname_r_output and
            #     dnl $vim_cv_uname_m_output to the desired value for the target system
            #
            # uname -r is only used for SunOS and uname -m for checking if it's
            # macOS/x86_64 but we can give them sensible values.
            self.configure_environment.update(
                vim_cv_toupper_broken="no",
                vim_cv_terminfo="yes",
                vim_cv_getcwd_broken="no",
                vim_cv_stat_ignores_slash="no",
                vim_cv_memmove_handles_overlap="yes",
                vim_cv_bcopy_handles_overlap="yes",
                vim_cv_memcpy_handles_overlap="no",
                vim_cv_tgetent="zero",
                vim_cv_uname_output="FreeBSD",
                vim_cv_uname_r_output="14.0-CURRENT",
                vim_cv_uname_m_output=self.target_info.freebsd_target,
            )
            # Terminal library selection also uses AC_TRY_RUN
            self.configure_args.append("--with-tlib=tinfo")

    def needs_configure(self):
        # Makefile exists in the source (both at the top level and within
        # src/), and we use a symlink farm in the build directory, so must
        # probe an output of configure.
        return not (self.build_dir / "src/auto/config.mk").exists()
