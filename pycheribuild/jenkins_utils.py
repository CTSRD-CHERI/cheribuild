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
from typing import Optional

from .config.jenkinsconfig import CheriConfig, JenkinsConfig
from .config.loader import CommandLineConfigOption
from .config.target_info import CrossCompileTarget
from .projects.project import Project
from .targets import MultiArchTargetAlias, SimpleTargetAlias, Target, target_manager
from .utils import fatal_error, status_update


def default_install_prefix(xtarget: CrossCompileTarget, cheri_config: CheriConfig) -> Path:
    # noinspection PyProtectedMember
    dirname = xtarget.target_info_cls._install_prefix_dirname(xtarget, cheri_config)
    return Path("/opt", dirname)


def jenkins_override_install_dirs_hack(cheri_config: CheriConfig, install_prefix_arg: Optional[Path]):
    # Ugly workaround to override all install dirs to go to the tarball
    all_targets = [
        x
        for x in target_manager.targets(cheri_config)
        if not isinstance(x, (SimpleTargetAlias, MultiArchTargetAlias)) and issubclass(x.project_class, Project)
    ]
    if isinstance(cheri_config, JenkinsConfig):
        sysroot_targets = [
            target_manager.get_chosen_target(cheri_config, target_name)
            for target_name in cheri_config.sysroot_install_dir_targets
        ]
    else:
        sysroot_targets = []

    for target in sysroot_targets:
        if target.xtarget.is_native():
            fatal_error("Cannot use non-existent sysroot for native target", target.name, pretend=False)

    def expected_install_root(tgt: Target) -> Path:
        if tgt in sysroot_targets:
            # noinspection PyProtectedMember
            proj = tgt._get_or_create_project_no_setup(cross_target=None, config=cheri_config, caller=None)
            target_info = proj.target_info
            sysroot_dir = target_info.sysroot_dir
            return sysroot_dir
        else:
            return cheri_config.output_root

    def expected_install_prefix(tgt: Target) -> Path:
        if install_prefix_arg is None:
            if tgt.project_class.is_rootfs_target:
                return Path("/")
            return default_install_prefix(tgt.xtarget, cheri_config)
        else:
            return install_prefix_arg

    def expected_install_path(tgt: Target) -> Path:
        root_dir = expected_install_root(tgt)
        install_prefix = expected_install_prefix(tgt)
        return Path(f"{root_dir}{install_prefix}")

    for target in all_targets:
        cls = target.project_class
        cls._default_install_dir_fn = expected_install_path(target)

    Target.instantiating_targets_should_warn = False
    # Now that we have set the _install_dir member, override the prefix/destdir after instantiating.
    for target in all_targets:
        # noinspection PyProtectedMember
        project = target._get_or_create_project_no_setup(None, cheri_config, caller=None)
        assert isinstance(project, Project)
        # noinspection PyProtectedMember
        i = inspect.getattr_static(project, "_install_dir")._get_option()
        assert isinstance(i, CommandLineConfigOption)
        # But don't change it if it was specified on the command line. Note: This also does the config
        # inheritance: i.e. setting --cheribsd/install-dir will also affect cheribsd-cheri/cheribsd-mips
        # noinspection PyTypeChecker
        cls = target.project_class
        try:
            from_cmdline: Optional[Path] = i.load_option(cheri_config, cls, cls, raise_err_if_default=True)
        except ValueError:
            from_cmdline = None
        if from_cmdline is not None:
            status_update("Install directory for", cls.target, "was specified on commandline:", from_cmdline)
            project._install_dir = from_cmdline
        else:
            project._install_dir = expected_install_root(target)
            project._check_install_dir_conflict = False
            # Using "/" as the install prefix results inconsistently prefixing some paths with '/usr/'.
            # To avoid this, just use the full install path as the prefix.
            if expected_install_prefix(target) == Path("/"):
                project._install_prefix = expected_install_path(target)
                project.destdir = Path("/")
            else:
                project._install_prefix = expected_install_prefix(target)
                project.destdir = expected_install_root(target)
            assert project.real_install_root_dir == expected_install_path(target)
        assert isinstance(inspect.getattr_static(project, "_install_dir"), Path)
