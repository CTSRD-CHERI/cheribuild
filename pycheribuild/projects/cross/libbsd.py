#
# Copyright (c) 2025 Paul Metzger
# All rights reserved.
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
from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository
from ..project import (
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind
)

from ...config.compilation_targets import CompilationTargets
from ...utils import classproperty

class BuildLibbsd(CrossCompileAutotoolsProject):
    _always_add_suffixed_targets = True
    _can_use_autogen_sh = True
    make_kind = MakeCommandKind.GnuMake
    # `default_branch` is set because the build scripts assume that 
    # a git tag is checked out. In more detail ./get-version returns an 
    # empty string if main is checked out.
    repository = GitRepository("https://gitlab.freedesktop.org/libbsd/libbsd.git", 
                               default_branch="0.12.2")

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE
    
    @property
    def muslc_target(self) -> str:
        return self.target_info.target_triple
    
    def _apply_patch(self) -> None:
        patch = """
diff --git a/src/merge.c b/src/merge.c
index 3f1b3fb..f8cb602 100644
--- a/src/merge.c
+++ b/src/merge.c
@@ -84,8 +84,8 @@ static void insertionsort(unsigned char *, size_t, size_t,
  */
 /* Assumption: PSIZE is a power of 2. */
 #define EVAL(p) (unsigned char **)					\\
-	 (((unsigned char *)p + PSIZE - 1 -				\\
-	   (unsigned char *)0) & ~(PSIZE - 1))
+	 __builtin_cheri_address_set(p, ((__builtin_cheri_address_get(p) + PSIZE - 1 -				\\
+	   0) & ~(PSIZE - 1)))
 
 /*
  * Arguments are as for qsort.
"""
        self.write_file(self.source_dir / "merge.patch", patch, overwrite=True)
        self.run_cmd("git", "restore", ".", cwd=self.source_dir)
        self.run_cmd("git", "apply", "merge.patch", cwd=self.source_dir)
    
    def setup(self) -> None:
        super().setup()
        # Remove dependency on libgcc_eh
        self.COMMON_LDFLAGS.append("--unwindlib=none")
        # Remove dependcy on libgcc_s
        self.COMMON_LDFLAGS.append("-Wc,--unwindlib=none")

    def configure(self, **kwargs):
        if not self.configure_command.exists():
            self.run_cmd(self.source_dir / "autogen", cwd=self.source_dir)
        super().configure(**kwargs)

    def compile(self, **kwargs):
        self._apply_patch()
        self.run_make()
        super().compile()

    def install(self, **kwargs):
        self.run_make_install()
        super().install(**kwargs)


class BuildRISCVLibbsd(BuildLibbsd):
    _supported_architectures = (CompilationTargets.LINUX_RISCV64_PURECAP_093,)
    dependencies = ("cheri-std093-muslc", "cheri-std093-libmd")
    target = 'cheri-std093-libbsd'


class BuildMorelloLibbsd(BuildLibbsd):
    _supported_architectures = (CompilationTargets.LINUX_MORELLO_PURECAP,)
    dependencies = ("morello-muslc", "morello-libmd")
    target = 'morello-libbsd'