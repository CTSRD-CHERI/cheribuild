#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2021 Jessica Clarke
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

from .crosscompileproject import CrossCompileAutotoolsProject
from ..project import GitRepository
from ...config.compilation_targets import CompilationTargets


class BuildCurl(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/curl/curl.git")
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS + CompilationTargets.ALL_NATIVE

    def setup(self):
        super().setup()
        self.configure_args.append("--with-openssl")
        # Configure script disables auto-detection when cross-compiling
        if not self._xtarget.is_native():
            self.configure_args.append("--with-ca-path=/etc/ssl/certs")

    def configure(self, **kwargs):
        self.run_cmd("autoreconf", "-fi", cwd=self.source_dir)
        super().configure(**kwargs)
