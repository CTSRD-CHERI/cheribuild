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

import os
from pathlib import Path

from .crosscompileproject import (CrossCompileSimpleProject, CrossCompileAutotoolsProject, CrossCompileCMakeProject,
                                  CrossCompileMakefileProject, CrossCompileMesonProject)
from ..project import ExternallyManagedSourceRepository, ComputedDefaultValue


_cwd_path = Path(os.getcwd())


def _cwd_source_dir(_, _1):
    return _cwd_path


def _cwd_directory_basename(_, _1):
    return _cwd_path.name


class CurrentDirectoryMixin(object):
    do_not_add_to_targets = True
    default_directory_basename = ComputedDefaultValue(function=_cwd_directory_basename,
                                                      as_string="$SOURCE_DIR_NAME")
    inherit_default_directory_basename = True
    repository = ExternallyManagedSourceRepository()
    source_dir = ComputedDefaultValue(function=_cwd_source_dir, as_string="$CWD")


class BuildCurrent_Directory_Autotools(CurrentDirectoryMixin, CrossCompileAutotoolsProject):
    autodetect_files = ["configure"]


class BuildCurrent_Directory_CMake(CurrentDirectoryMixin, CrossCompileCMakeProject):
    autodetect_files = ["CMakeLists.txt"]


class BuildCurrent_Directory_Makefile(CurrentDirectoryMixin, CrossCompileMakefileProject):
    autodetect_files = ["GNUmakefile", "makefile", "Makefile"]


class BuildCurrent_Directory_Meson(CurrentDirectoryMixin, CrossCompileMesonProject):
    autodetect_files = ["meson.build"]


class BuildCurrent_Directory(CurrentDirectoryMixin, CrossCompileSimpleProject):
    dependencies_must_be_built = True
    direct_dependencies_only = True

    @classmethod
    def dependencies(cls, config):
        classes = [
                BuildCurrent_Directory_Autotools,
                BuildCurrent_Directory_CMake,
                BuildCurrent_Directory_Makefile,
                BuildCurrent_Directory_Meson
            ]
        for c in classes:
            for f in c.autodetect_files:
                if (_cwd_path / f).is_file():
                    return [c.target]
        return []

    def process(self):
        if not self.dependencies(self.config):
            self.fatal("Could not infer build system in use")
