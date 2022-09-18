#
# Copyright (c) 2017 Alex Richardson
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

import os
import typing
from pathlib import Path

from ..cmake_project import CMakeProject
from ..meson_project import MesonProject
from ..project import (AutotoolsProject, BuildType, CheriConfig, commandline_to_str, CrossCompileTarget, GitRepository,
                       DefaultInstallDir, Linkage, MakeCommandKind, MakefileProject, Project, SubversionRepository)
from ..simple_project import SimpleProject
from ...config.compilation_targets import CompilationTargets
from ...utils import AnsiColour, coloured

__all__ = ["CheriConfig", "CrossCompileCMakeProject", "CrossCompileAutotoolsProject",  # no-combine
           "CrossCompileTarget", "CrossCompileSimpleProject", "CrossCompileProject",  # no-combine
           "MakeCommandKind", "Linkage", "DefaultInstallDir", "BuildType", "CompilationTargets",  # no-combine
           "GitRepository", "CrossCompileMixin", "CrossCompileMakefileProject",  # no-combine
           "CrossCompileMesonProject", "commandline_to_str", "SubversionRepository"]  # no-combine


if typing.TYPE_CHECKING:
    _CrossCompileMixinBase = SimpleProject
else:
    _CrossCompileMixinBase = object


# This mixin sets supported_architectures to ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS and thereby
# avoids repeating this for every target than can be cross-built
# noinspection PyAbstractClass
class CrossCompileMixin(_CrossCompileMixinBase):
    do_not_add_to_targets = True
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS
    add_build_dir_suffix_for_native = True  # Add the suffix for the native build
    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    _should_not_be_instantiated = True
    # Add a (mostly) reasonable default for installation directories:
    native_install_dir = DefaultInstallDir.ROOTFS_LOCALBASE
    cross_install_dir = DefaultInstallDir.ROOTFS_LOCALBASE


# noinspection PyAbstractClass
class CrossCompileSimpleProject(CrossCompileMixin, SimpleProject):
    do_not_add_to_targets = True


class CrossCompileProject(CrossCompileMixin, Project):
    do_not_add_to_targets = True


class CrossCompileMakefileProject(CrossCompileMixin, MakefileProject):
    do_not_add_to_targets = True


class CrossCompileCMakeProject(CrossCompileMixin, CMakeProject):
    do_not_add_to_targets = True  # only used as base class


class CrossCompileMesonProject(CrossCompileMixin, MesonProject):
    do_not_add_to_targets = True  # only used as base class


class CrossCompileAutotoolsProject(CrossCompileMixin, AutotoolsProject):
    do_not_add_to_targets = True  # only used as base class

    _autotools_add_default_compiler_args = True
    _configure_supports_libdir = True  # override in nginx
    _configure_supports_variables_on_cmdline = True  # override in nginx
    _configure_understands_enable_static = True
    _define_ld = True  # override to not define LD

    def add_configure_and_make_env_arg(self, arg: str, value: "typing.Union[str,Path]"):
        self.add_configure_env_arg(arg, value)
        self.make_args.set_env(**{arg: str(value)})

    def add_configure_env_arg(self, arg: str, value: "typing.Union[str,Path]"):
        super().add_configure_env_arg(arg, value)
        if self._configure_supports_variables_on_cmdline:
            self.configure_args.append(arg + "=" + str(value))

    def add_configure_vars(self, **kwargs):
        for k, v in kwargs.items():
            self.add_configure_env_arg(k, v)

    def set_configure_prog_with_args(self, prog: str, path: Path, args: list):
        super().set_configure_prog_with_args(prog, path, args)
        if self._configure_supports_variables_on_cmdline:
            self.configure_args.append(prog + "=" + self.configure_environment[prog])

    def setup(self):
        super().setup()
        if self._configure_understands_enable_static:  # workaround for nginx which isn't really autotools
            if self.force_static_linkage:
                self.configure_args.extend(["--enable-static", "--disable-shared"])
            elif self.force_dynamic_linkage:
                self.configure_args.extend(["--disable-static", "--enable-shared"])
            # Otherwise just let the project decide
            # else:
            #    self.configure_args.extend(["--enable-static", "--enable-shared"])
        if self.crosscompile_target.is_cheri_purecap() and self._configure_supports_libdir:
            # Install to lib and not libcheri since we have a separate prefix and that makes it
            # easier to handle build systems that assume that library are always in /lib
            self.configure_args.append("--libdir=" + str(self.install_prefix) + "/lib")

    def configure(self, **kwargs):
        if self._autotools_add_default_compiler_args:
            cppflags = self.default_compiler_flags
            for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
                assert key not in self.configure_environment, key
            # We have to include -target xxx-unknown-freebsd as part of CC for some build systems since they fail
            # if a plain $CC can't compile programs.
            self.set_configure_prog_with_args("CC", self.CC, self.essential_compiler_and_linker_flags)
            self.set_configure_prog_with_args("CXX", self.CXX, self.essential_compiler_and_linker_flags)
            # self.add_configure_env_arg("CPPFLAGS", self.commandline_to_str(CPPFLAGS))
            self.add_configure_env_arg("CFLAGS", self.commandline_to_str(cppflags + self.CFLAGS))
            self.add_configure_env_arg("CXXFLAGS", self.commandline_to_str(cppflags + self.CXXFLAGS))
            # this one seems to work:
            self.add_configure_env_arg("LDFLAGS", self.commandline_to_str(self.LDFLAGS + self.default_ldflags))

            if not self.compiling_for_host():
                self.set_configure_prog_with_args("CPP", self.CPP, cppflags)
                if self._define_ld:
                    self.add_configure_env_arg("LD", self.target_info.linker)

        # remove all empty items from environment:
        env = {k: v for k, v in self.configure_environment.items() if v}
        self.configure_environment.clear()
        self.configure_environment.update(env)
        self.print(coloured(AnsiColour.yellow, "Cross configure environment:\n\t",
                            "\n\t".join(k + "=" + str(v) for k, v in self.configure_environment.items())))
        super().configure(**kwargs)

    def process(self):
        if not self.compiling_for_host():
            # We run all these commands with $PATH containing $CHERI_SDK/bin to ensure the right tools are used
            with self.set_env(PATH=str(self.sdk_bindir) + ":" + os.getenv("PATH")):
                super().process()
        else:
            # when building the native target we just rely on the host tools in /usr/bin
            super().process()
