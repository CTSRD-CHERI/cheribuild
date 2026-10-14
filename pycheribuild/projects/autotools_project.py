#
# Copyright (c) 2016 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
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
import subprocess

from .project import MakeCommandKind, Project
from .repository import GitRepository
from .simple_project import ListConfigOption

__all__ = ["AutotoolsProject"]


class AutotoolsProject(Project):
    """
    Like Project but automatically sets up the defaults for autotools like projects
    Sets configure command to ./configure, adds --prefix=installdir
    """

    do_not_add_to_targets: bool = True
    _configure_supports_prefix: bool = True
    _can_use_autogen_sh = True  # Whether autogen.sh can be used to create ./configure
    _verbose_make_uses_v_eq_1 = True  # verbose build output can be enabled using V=1
    make_kind: MakeCommandKind = MakeCommandKind.GnuMake
    add_host_target_build_config_options: bool = True

    extra_configure_flags = ListConfigOption(
        "configure-options", metavar="OPTIONS", help="Additional command line options to pass to configure"
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.configure_command = self.source_dir / "configure"

    def setup(self) -> None:
        super().setup()
        buildhost = self.get_host_triple()
        if self.add_host_target_build_config_options:
            if not self.compiling_for_host():
                autotools_triple = self.target_info.target_triple
                # Most scripts don't like the final -purecap component:
                autotools_triple = autotools_triple.replace("-purecap", "")
                # TODO: do we have to remove these too?
                # autotools_triple = autotools_triple.replace("mips64c128-", "cheri-")
                self.configure_args.extend(
                    ["--host=" + autotools_triple, "--target=" + autotools_triple, "--build=" + buildhost]
                )
            elif self.crosscompile_target.is_hybrid_or_purecap_cheri():
                # When compiling natively on CheriBSD, most autotools projects don't like the inferred config.guess
                # value of aarch64c-unknown-freebsd14.0. Override it to make this work in most cases.
                self.configure_args.extend(["--build=" + buildhost])
        # Most autotools-base projects enable verbose output by setting V=1.
        if self.config.verbose and self._verbose_make_uses_v_eq_1:
            self.make_args.set(V=1)

    def configure(self, **kwargs) -> None:
        if self._configure_supports_prefix:
            if self.install_prefix != self.install_dir:
                assert self.destdir, "custom install prefix requires DESTDIR being set!"
                self.configure_args.append("--prefix=" + str(self.install_prefix))
            else:
                self.configure_args.append("--prefix=" + str(self.install_dir))
        if self.make_args.kind != MakeCommandKind.DefaultMake:
            self.add_configure_env_arg("MAKE", self.make_args.command)
        if self.extra_configure_flags:
            self.configure_args.extend(self.extra_configure_flags)
        # If there is no ./configure script but ./autogen.sh exists, try running that first
        if (
            self._can_use_autogen_sh
            and self.configure_command is not None
            and (self.configure_command.parent / "autogen.sh").is_file()
        ):
            generate_configure = not self.configure_command.exists()
            # We should also run autogen.sh if configure_command is not tracked by git and --clean is passed
            if not generate_configure and self.config.clean and isinstance(self.repository, GitRepository):
                generate_configure = not GitRepository.is_tracked(self, self.source_dir, self.configure_command)
            # We need to pass NOCONFIGURE=1, to avoid invoking the configure script directly plus any environment
            # variables that might affect the autoconf lookup (e.g. ACLOCAL_PATH).
            if generate_configure:
                self.run_cmd(
                    self.configure_command.parent / "autogen.sh",
                    cwd=self.configure_command.parent,
                    env={**dict(NOCONFIGURE=1), **self.configure_environment},
                )
        super().configure(**kwargs)

    def needs_configure(self) -> bool:
        # Most autotools projects use makefiles, but we also use this class for the CMake
        # bootstrap build which ends up generating a build.ninja file instead of a Makefile.
        build_file = "build.ninja" if self.make_args.kind == MakeCommandKind.Ninja else "Makefile"
        return not (self.build_dir / build_file).exists()

    def set_lto_binutils(self, ar, ranlib, nm, ld) -> None:
        kwargs = {"NM": nm, "AR": ar, "RANLIB": ranlib}
        if ld:
            kwargs["LD"] = ld
        self.configure_environment.update(**kwargs)
        # self.make_args.env_vars.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
        self.make_args.set(**kwargs)
        self.make_args.env_vars.update(**kwargs)

    def run_tests(self) -> None:
        # Most autotools projects have a "check" target that we can use.
        try:
            self.run_cmd(
                self.make_args.command,
                *self.make_args.get_commandline_args(targets=["-n", "check"], jobs=1, config=self.config),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=self.build_dir,
            )
        except subprocess.CalledProcessError:
            # If make -n check fails, assume there are no tests.
            return super().run_tests()
        self.run_make("check", parallel=False, logfile_name="test")  # Unlikely to be parallel-safe
