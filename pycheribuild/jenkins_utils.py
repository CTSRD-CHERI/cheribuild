#
# Copyright (c) 2023 Alex Richardson
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
import inspect
from pathlib import Path

from .config.chericonfig import CheriConfig
from .config.loader import CommandLineConfigOption
from .projects.project import Project
from .targets import MultiArchTargetAlias, SimpleTargetAlias, Target, target_manager
from .utils import status_update


def jenkins_override_install_dirs_hack(cheri_config: CheriConfig, install_prefix: Path):
    expected_install_path = Path(f"{cheri_config.output_root}{install_prefix}")
    # Ugly workaround to override all install dirs to go to the tarball
    for tgt in target_manager.targets(cheri_config):
        if isinstance(tgt, SimpleTargetAlias):
            continue
        cls = tgt.project_class
        if issubclass(cls, Project) and not isinstance(tgt, MultiArchTargetAlias):
            cls._default_install_dir_fn = Path(expected_install_path)
            i = inspect.getattr_static(cls, "_install_dir")
            assert isinstance(i, CommandLineConfigOption)
            # But don't change it if it was specified on the command line. Note: This also does the config
            # inheritance: i.e. setting --cheribsd/install-dir will also affect cheribsd-cheri/cheribsd-mips
            # noinspection PyTypeChecker
            from_cmdline = i.load_option(cheri_config, cls, cls, return_none_if_default=True)
            if from_cmdline is not None:
                status_update("Install directory for", cls.target, "was specified on commandline:", from_cmdline)
            else:
                cls._install_dir = cheri_config.output_root
                cls._check_install_dir_conflict = False

        Target.instantiating_targets_should_warn = False
        # Override the installation directory for all enabled targets
        for name in cheri_config.targets:
            target = target_manager.get_target_raw(name)
            # noinspection PyProtectedMember
            project = target._get_or_create_project_no_setup(None, cheri_config, caller=None)
            if isinstance(project, Project):
                # Using "/" as the install prefix results inconsistently prefixing some paths with '/usr/'.
                # To avoid this, just use the full install path as the prefix.
                if install_prefix == Path("/"):
                    project._install_prefix = expected_install_path
                    project.destdir = Path("/")
                else:
                    project._install_prefix = install_prefix
                    project.destdir = cheri_config.output_root
                assert project.real_install_root_dir == expected_install_path
