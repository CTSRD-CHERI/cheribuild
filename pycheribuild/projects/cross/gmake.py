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
from ...utils import OSInfo


class BuildGmake(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/mirror/make.git")

    def setup(self):
        super().setup()

        self.configure_args.append("--program-prefix=g")

        # Defines getenv without a prototype, but uses it correctly.
        self.cross_warning_flags.append("-Wno-error=cheri-prototypes")
        # Wraps realloc with an incompatible prototype for everything except
        # glibc, DJGPP and Windows, complaining about "the broken Ultrix
        # compiler"...
        # TODO: Add FreeBSD to the list to remove the stupid workaround?
        self.cross_warning_flags.append("-Wno-error=incompatible-pointer-types")

        # maintMakefile, used for Git builds, assumes GCC; this is the
        # documented way to build with other compilers (see README.git).
        self.make_args.set(MAKE_CFLAGS="")

        if OSInfo.IS_MAC:
            # src/config.h-vms.template has ä encoded in ISO-8859-1 (Latin-1)
            # not UTF-8, and macOS's sed chokes on that with:
            #   sed: RE error: illegal byte sequence
            # when the locale is something like en_GB.UTF-8. C.UTF-8 seems to
            # work fine, not just C, so use that for the build.
            self.make_args.set_env(LC_ALL="C.UTF-8")
