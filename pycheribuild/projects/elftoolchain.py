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
import typing
from pathlib import Path

from .project import CheriConfig, DefaultInstallDir, GitRepository, MakeCommandKind, Project
from ..utils import OSInfo
from ..processutils import get_program_version


class BuildElftoolchain(Project):
    target = "elftoolchain"
    repository = GitRepository("https://github.com/emaste/elftoolchain.git", default_branch="master")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    make_kind = MakeCommandKind.BsdMake
    is_sdk_target = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # TODO: move this to project
        self.makedirs(self.build_dir)
        self.make_args.env_vars["MAKEOBJDIRPREFIX"] = self.build_dir
        # TODO: build static?
        if self.build_static:
            self.make_args.set(LDSTATIC="-static")
        self.make_args.set(WITH_TESTS="no", WITH_PE="no", WITH_DOCUMENTATION="no")
        # HACK: we don't want the binaries to depend on libelftc.so because the build system doesn't handle rpath
        # setting SHLIB_FULLVERSION to empty is a hack to prevent building of shared libraries
        # as we want the build tools to be statically linked but e.g. libarchive might not be available
        # as a static library (e.g. on openSUSE)
        self.make_args.set(SHLIB_MAJOR="", SHLIB_FULLVERSION="",  # don't build shared libraries
                           CC=str(self.CC))
        self.make_args.set(MK_MAN="no")

        if not self.config.verbose:
            self.make_args.add_flags("-s")
        self.programs_to_build = ["brandelf", "elfcopy", "elfdump", "strings", "nm", "readelf", "addr2line",
                                  "size", "findtextrel"]
        # some make targets install more than one tool:
        # strip, objcopy and mcs are links to elfcopy and ranlib is a link to ar
        self.extra_programs = ["strip", "objcopy", "mcs"]
        self.lib_targets = ["common", "libelf", "libelftc", "libdwarf"]
        if self.build_ar:
            self.programs_to_build.append("ar")
            self.extra_programs.append("ranlib")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.build_ar = cls.add_bool_option("build-ar", default=True, help="build the ar/ranlib programs")
        cls.build_static = cls.add_bool_option("build-static", help="Try to link elftoolchain statically "
                                                                    "(needs patches on Linux)")

    def check_system_dependencies(self):
        super().check_system_dependencies()
        if OSInfo.IS_MAC and not Path("/usr/local/opt/libarchive/lib").exists():
            self.dependency_error("libarchive is missing", install_instructions="Run `brew install libarchive`")

    def compile(self, **kwargs):
        is_old_broken_bmake = True
        try:
            version = get_program_version(Path(self.make_args.command), ("-V", "MAKE_VERSION"), regex=b"(.+)",
                                          config=self.config)[0]
            # The version of bmake shipped with ubuntu doesn't handle depedencies correctly
            if version > 20170101:
                is_old_broken_bmake = False
            else:
                self.info("Note: Working around old version of bmake: ", version)
        except Exception as e:
            self.warning("Could not determine bmake version:", e)
        if is_old_broken_bmake:
            # build is not parallel-safe -> we can't make with all the all-foo targets and -jN
            # To speed it up run make for the individual library directories instead and then for all the binaries
            first_call = True  # recreate logfile on first call, after that append
            for tgt in self.lib_targets + self.programs_to_build:
                self.run_make("obj", cwd=self.source_dir / tgt, logfile_name="build", append_to_logfile=not first_call)
                self.run_make("all", cwd=self.source_dir / tgt, logfile_name="build", append_to_logfile=True)
                first_call = False
        else:
            self.run_make("obj", cwd=self.source_dir)
            self.run_make("all", cwd=self.source_dir, append_to_logfile=True)

    def install(self, **kwargs):
        self.makedirs(self.install_dir / "bin")
        # We don't actually want to install all the files, just copy the binaries that we want
        group = self.config.get_group_name()
        user = self.config.get_user_name()
        self.make_args.set(DESTDIR=self.install_dir)
        self.make_args.set(
            # elftoolchain tries to install as root -> override *GRP and *OWN flags
            BINGRP=group, BINOWN=user,
            MANGRP=group, MANOWN=user,
            INFOGRP=group, INFOOWN=user,
            LIBGRP=group, LIBOWN=user,
            FILESGRP=group, FILESOWN=user,
            )

        self.make_args.set(
            BINDIR="/bin",
            LIBDIR="/lib",
            INCSDIR="/include",
            SHAREDIR="/share",
            )

        if OSInfo.IS_LINUX:
            # $INSTALL is not set to create leading directories on Ubuntu
            self.make_args.set(MANDIR="/share/man", INSTALL="install -D")

        mandirs = ("share/man/man1", "share/man/man3", "share/man/man5", "share/man1", "share/man3", "share/man5")
        # The build system assumes all install directories already exist;
        for i in ("bin", "lib", "include", "share") + mandirs:
            self.makedirs(self.install_dir / i)
        first_call = True  # recreate logfile on first call, after that append
        for tgt in self.programs_to_build:
            self.run_make_install(cwd=self.source_dir / tgt, logfile_name="install", append_to_logfile=not first_call,
                                  parallel=False)
            first_call = False

        all_installed_tools = self.programs_to_build + self.extra_programs
        for prog in all_installed_tools:
            if prog == "strip":
                self.delete_file(self.install_dir / "bin" / ("cheri-unknown-freebsd-" + prog))
                self.delete_file(self.install_dir / "bin" / ("mips64-unknown-freebsd-" + prog))
                self.delete_file(self.install_dir / "bin" / ("mips4-unknown-freebsd-" + prog))
            else:
                self.create_triple_prefixed_symlinks(self.install_dir / "bin" / prog)
        # if we didn't build ar/ranlib add symlinks to the versions in /usr/bin
        if not self.build_ar:
            self.create_symlink(Path("/usr/bin/ar"), self.install_dir / "bin/ar", relative=False)
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/ar")
            self.create_symlink(Path("/usr/bin/ranlib"), self.install_dir / "bin/ranlib", relative=False)
            self.create_triple_prefixed_symlinks(self.install_dir / "bin/ranlib")

    @property
    def triple_prefixes_for_binaries(self) -> typing.Iterable[str]:
        return ["cheri-unknown-freebsd-"]  # compat only

    def process(self):
        # work around bug in latest bmake that assumes metamode support
        with self.set_env(META_NOECHO="echo"):
            super().process()
