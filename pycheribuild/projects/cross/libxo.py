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

class BuildLibxo(CrossCompileAutotoolsProject):
    _always_add_suffixed_targets = True
    _supported_architectures = (CompilationTargets.LINUX_MORELLO_PURECAP,)
    _can_use_autogen_sh = True
    dependencies = ("libbsd", "libmd",  "morello-compiler-rt-builtins", "morello-muslc")
    make_kind = MakeCommandKind.GnuMake
    repository = GitRepository("https://github.com/Juniper/libxo.git")
    target = "libxo"

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE
    
    @property
    def muslc_target(self) -> str:
        return self.target_info.target_triple
    
    def _patch_to_use_libbsd(self, path):
        self.run_cmd("sed", "-i", "s|<sys/queue.h>|<bsd/sys/queue.h>|g", path, cwd=self.source_dir)
    
    def _patch_includes(self) -> None:
        self._patch_to_use_libbsd("libxo/xo_encoder.c")
        self._patch_to_use_libbsd("xopo/xopo.c")
        
    def _patch_configure_ac(self) -> None:
        
        self.run_cmd("sed", "-i", "s|AC_FUNC_REALLOC|#AC_FUNC_REALLOC|g", "configure.ac", cwd=self.source_dir)
        self.run_cmd("sed", "-i", "s|AC_FUNC_MALLOC|#AC_FUNC_MALLOC|g", "configure.ac", cwd=self.source_dir)

    def setup(self) -> None:
        super().setup()
        
        # Don't depend on libgcc_eh
        self.COMMON_LDFLAGS.append("--unwindlib=none")

        # Search for the build directory of compiler-rt-builtins
        compiler_rt_builtins_build_dir = None
        for d in self.cached_full_dependencies():
            if d.name == "morello-compiler-rt-builtins-linux-morello-purecap":
                compiler_rt_builtins_build_dir = d.get_or_create_project(None, None, self).get_build_dir(self)
                break
        print(compiler_rt_builtins_build_dir)
        # Don't try to link with libgcc_s. The compiler cannot find libclang_rt.builtins-aarch64.a if just 
        # -rtlib=compiler-rt is set without setting the resource path.
        self.make_args.set(
            CFLAGS=" ".join(self.default_compiler_flags() + 
                            ["-rtlib=compiler-rt", "-resource-dir={}".format(compiler_rt_builtins_build_dir)]),
        )
    
    def configure(self, **kwargs):
        self._patch_configure_ac()
        self._patch_includes()
        self.run_shell_script("sh bin/setup.sh", shell="sh", cwd=self.source_dir)
        super().configure(**kwargs)

    def compile(self, **kwargs):
        self.run_make()
        super().compile(**kwargs)

    def install(self, **kwargs):
        self.run_make_install()
        super().install(**kwargs)

