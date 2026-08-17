#
# Copyright (c) 2025-2026 Paul Metzger
# All rights reserved.
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

from .crosscompileproject import CrossCompileAutotoolsProject, DefaultInstallDir, GitRepository, MakeCommandKind
from .libbsd import BuildLibbsd
from ...config.compilation_targets import CompilationTargets
from ...utils import classproperty


class BuildLibxo(CrossCompileAutotoolsProject):
    _supported_architectures = CompilationTargets.ALL_CHERI_AND_MORELLO_LINUX_TARGETS
    make_kind = MakeCommandKind.GnuMake
    repository = GitRepository(
        "https://github.com/Juniper/libxo.git",
        # Version 2.0 treats failing libint- and msgfmt-related
        # tests performed by configure as errors. As a
        # temporary workaround, stay with the preceding version.
        force_tag="1.7.5",
    )

    @classproperty
    def default_install_dir(self):
        return DefaultInstallDir.ROOTFS_LOCALBASE

    @classmethod
    def dependencies(cls, config) -> "tuple[str, ...]":
        return "libbsd", "libmd"

    def setup(self):
        super().setup()
        # These tests fail due to cross-compilation issues. Muslc's malloc
        # and realloc return a pointer when passed a size of zero.
        self.configure_args.append("ac_cv_func_realloc_0_nonnull=yes")
        self.configure_args.append("ac_cv_func_malloc_0_nonnull=yes")
        # Don't depend on libgcc_s. If this isn't set then clang wants to link
        # with libgcc_s, which is not available on Morello Linux.
        self.LDFLAGS.append("--unwindlib=none")
        # This prompts libtool to pass '--unwindlib=none' to clang during
        # linking. Libtool ignores "--unwindlib=none" and needs
        # "-Wc,--unwindlib=none" instead. "-Wc,--unwindlib=none" is turned
        # into "--unwindlib=none" by libtool when it invokes clang.
        self.CFLAGS.append("-Wc,--unwindlib=none")
        self.CFLAGS.append("-isystem")
        # Some files expect sys/queue.h to exist, but we can't add libbsd/include/bsd to the
        # include path without breaking the build, so just create a symlink in a temp dir.
        self.CFLAGS.append(str(self.build_dir / "libbsd-workaround"))

    def configure(self, **kwargs):
        self.run_shell_script("sh bin/setup.sh", shell="sh", cwd=self.source_dir)
        self.makedirs(self.build_dir / "libbsd-workaround/sys")
        self.create_symlink(
            BuildLibbsd.get_install_dir(self) / "include/bsd/sys/queue.h",
            self.build_dir / "libbsd-workaround/sys/queue.h",
            relative=False,
        )
        super().configure(**kwargs)

    def install(self, **kwargs) -> None:
        self.run_make_install()

        # Copy the libraries from the cross-compile sysroot into rootfs/lib
        for sofile in self.install_dir.glob("lib/libxo.so*"):
            self.install_file(sofile, self.install_dir / "rootfs/lib/" / sofile.name, create_dirs=True)
