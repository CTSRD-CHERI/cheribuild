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
import argparse
import contextlib
import inspect
import os
import pprint
import shutil
import sys
import typing
# noinspection PyUnresolvedReferences
from pathlib import Path

from .config.jenkinsconfig import JenkinsAction, JenkinsConfig
from .config.loader import CommandLineConfigOption, CommandLineConfigLoader
# make sure all projects are loaded so that target_manager gets populated
# noinspection PyUnresolvedReferences
from .projects import *  # noqa: F401,F403
# noinspection PyUnresolvedReferences
from .projects.cross import *  # noqa: F401,F403
from .projects.cross.crosscompileproject import CrossCompileMixin
from .projects.project import Project
from .projects.simple_project import SimpleProject
from .targets import SimpleTargetAlias, Target, target_manager
from .processutils import get_program_version, run_and_kill_children_on_exit, run_command
from .utils import fatal_error, init_global_config, OSInfo, status_update, ThreadJoiner, warning_message

EXTRACT_SDK_TARGET: str = "extract-sdk"
RUN_EVERYTHING_TARGET: str = "__run_everything__"


class JenkinsConfigLoader(CommandLineConfigLoader):
    def load(self) -> None:
        super().load()
        assert isinstance(self._parsed_args.targets, list)
        self._parsed_args.verbose = True

    def finalize_options(self, available_targets: list, **kwargs) -> None:
        target_option = self._parser.add_argument(
            "targets", metavar="TARGET", nargs=argparse.ZERO_OR_MORE, help="The target to build",
            choices=available_targets + [EXTRACT_SDK_TARGET, RUN_EVERYTHING_TARGET])
        if self.is_completing_arguments:
            try:
                import argcomplete
            except ImportError:
                sys.exit("argcomplete missing")
            target_completer = argcomplete.completers.ChoicesCompleter(available_targets)
            target_option.completer = target_completer
            argcomplete.autocomplete(
                self._parser,
                always_complete_options=None,  # don't print -/-- by default
                print_suppressed=True,  # also include target-specific options
                )


class SdkArchive(object):
    def __init__(self, cheri_config: JenkinsConfig, name, *, required_globs: list = None, extra_args: list = None,
                 output_dir: Path):
        self.output_dir = output_dir
        self.cheri_config = cheri_config
        self.archive = cheri_config.workspace / name  # type: Path
        self.required_globs = [] if required_globs is None else required_globs  # type: list
        self.extra_args = [] if extra_args is None else extra_args  # type: list

    def extract(self) -> None:
        assert self.archive.exists(), str(self.archive)
        self.cheri_config.FS.makedirs(self.output_dir)
        run_command(["tar", "xf", self.archive, "-C", self.output_dir] + self.extra_args,
                    cwd=self.cheri_config.workspace)
        self.check_required_files()

    def check_required_files(self, fatal=True) -> bool:
        status_update("Checking for required files in", self.output_dir)
        for glob in self.required_globs:
            found = list(self.output_dir.glob(glob))
            status_update("Checking ", glob, ": ", found, sep="")
            # print("Matched files:", found)
            if len(found) == 0:
                if fatal:
                    fatal_error("required files", glob, "missing. Source archive =", self.archive)
                else:
                    status_update("required files", glob, "missing. Source archive was", self.archive)
                    return False
        return True

    def __repr__(self) -> str:
        return str(self.archive)


def get_sdk_archives(cheri_config, needs_cheribsd_sysroot: bool) -> "typing.List[SdkArchive]":
    clang_archive = SdkArchive(cheri_config, cheri_config.compiler_archive_name,
                               output_dir=cheri_config.compiler_archive_output_path,
                               required_globs=["bin/clang"], extra_args=["--strip-components", "1"])
    all_archives = []
    if clang_archive.archive.exists():
        all_archives.append(clang_archive)
    else:
        warning_message("Compiler archive", clang_archive.archive, "does not exists, will use only existing tools")
    if not needs_cheribsd_sysroot or cheri_config.extract_compiler_only:
        return all_archives  # only need the clang archive
    # if we only extracted the compiler, extract the sysroot now
    extra_args = ["--strip-components", "1"]
    sysroot_archive = SdkArchive(cheri_config, cheri_config.sysroot_archive_name,
                                 output_dir=cheri_config.sysroot_archive_output_path,
                                 required_globs=["usr/include"], extra_args=extra_args)
    if not sysroot_archive.archive.exists():
        warning_message("Project needs a sysroot archive but ", sysroot_archive.archive,
                        "is missing. Will attempt to build anyway but build will most likely fail.")
        run_command("ls", "-la", cwd=cheri_config.workspace)
        return all_archives
    else:
        all_archives.append(sysroot_archive)
        # Old sysroot archives had a leading ./, newer ones don't anymore
        # TODO: remove when master has been updated
        contents = run_command("tar", "tf", sysroot_archive.archive, capture_output=True)
        if contents.stdout.startswith(b'./'):
            warning_message("Old sysroot archive detected, stripping one more path component")
            sysroot_archive.extra_args = ["--strip-components", "2"]
    return all_archives


def extract_sdk_archives(cheri_config: JenkinsConfig, archives: "typing.List[SdkArchive]"):
    expected_bindir = cheri_config.compiler_archive_output_path / "bin"
    if expected_bindir.is_dir():
        status_update(expected_bindir, "already exists, not extracting SDK archives")
        return

    for archive in archives:
        archive.extract()

    if not expected_bindir.exists():
        fatal_error("SDK bin dir", expected_bindir, "does not exist after extracting sysroot archives!")

    # Use llvm-ar/llvm-ranlib or the host ar/ranlib if they ar/ranlib are missing from archive
    for tool in ("ar", "ranlib", "nm"):
        if not (expected_bindir / tool).exists():
            # If llvm-ar/ranlib/nm exists use that
            if (expected_bindir / ("llvm-" + tool)).exists():
                cheri_config.FS.create_symlink(expected_bindir / ("llvm-" + tool),
                                               expected_bindir / tool, relative=True)
            else:
                # otherwise fall back to the /usr/bin version
                cheri_config.FS.create_symlink(Path(shutil.which(tool)), expected_bindir / tool,
                                               relative=False)
    if not (expected_bindir / "ld").exists():
        status_update("Adding missing $SDK/ld link to ld.lld")
        cheri_config.FS.create_symlink(expected_bindir / "ld.lld",
                                       expected_bindir / "ld", relative=True)


def create_sdk_from_archives(cheri_config: JenkinsConfig, needs_cheribsd_sysroot, extract_all: bool) -> None:
    # If the archive is newer, delete the existing sdk unless --keep-sdk is passed install root:
    all_archives = get_sdk_archives(cheri_config, needs_cheribsd_sysroot=needs_cheribsd_sysroot)
    status_update("Will use the following SDK archives:", all_archives)
    if extract_all:
        archives = all_archives
    else:
        # Only extract if the archive is newer
        archives = []
        for a in all_archives:
            if a.output_dir.stat().st_ctime < a.archive.stat().st_ctime:
                msgkind = status_update if not cheri_config.keep_sdk_dir else warning_message
                msgkind("SDK archive", a.archive, "is newer than the existing SDK directory")
                archives.append(a)
                break
    # unpack the SDK if it has not been extracted yet:
    with contextlib.ExitStack() as stack:
        if not cheri_config.keep_sdk_dir:
            status_update("Deleting old SDK and extracting archive")
            dirs_cleaned = set()  # avoid cleaning twice
            for a in archives:
                if a.output_dir not in dirs_cleaned:
                    stack.enter_context(cheri_config.FS.async_clean_directory(a.output_dir))
                    dirs_cleaned.add(a.output_dir)
        extract_sdk_archives(cheri_config, archives)


def _jenkins_main() -> None:
    os.environ["_CHERIBUILD_JENKINS_BUILD"] = "1"
    all_target_names = list(sorted(target_manager.target_names(None)))
    config_loader = JenkinsConfigLoader()
    # Register all command line options
    cheri_config = JenkinsConfig(config_loader, all_target_names)
    # Make sure nothing other than the config loader uses this as it will include disabled target names
    del all_target_names
    SimpleProject._config_loader = config_loader
    target_manager.register_command_line_options()
    cheri_config.load()
    init_global_config(cheri_config, test_mode=False)

    # special target to extract the sdk
    if JenkinsAction.EXTRACT_SDK in cheri_config.action or (
            len(cheri_config.targets) > 0 and cheri_config.targets[0] == EXTRACT_SDK_TARGET):
        create_sdk_from_archives(cheri_config, not cheri_config.extract_compiler_only, extract_all=True)
        sys.exit()

    if RUN_EVERYTHING_TARGET in cheri_config.targets:
        cheri_config.targets = list(sorted(target_manager.target_names(cheri_config)))

    if cheri_config.action == [""]:
        fatal_error("No action specified, did you mean to pass --build?")
        sys.exit()

    if len(cheri_config.targets) < 1:
        fatal_error("Missing target?")
        sys.exit()

    if JenkinsAction.CREATE_TARBALL in cheri_config.action and len(cheri_config.targets) != 1:
        fatal_error("--create-tarball expects exactly one target!")
        sys.exit()

    if len(cheri_config.targets) != 1 and not cheri_config.allow_more_than_one_target:
        fatal_error("More than one target is not supported yet.")
        sys.exit()

    if JenkinsAction.BUILD in cheri_config.action or JenkinsAction.TEST in cheri_config.action:
        # Ugly workaround to override all install dirs to go to the tarball
        for tgt in target_manager.targets(cheri_config):
            if isinstance(tgt, SimpleTargetAlias):
                continue
            cls = tgt.project_class
            if issubclass(cls, Project):
                cls._default_install_dir_fn = Path(
                    str(cheri_config.output_root) + str(cheri_config.installation_prefix))
                i = inspect.getattr_static(cls, "_install_dir")
                assert isinstance(i, CommandLineConfigOption)
                # But don't change it if it was specified on the command line. Note: This also does the config
                # inheritance: i.e. setting --cheribsd/install-dir will also affect cheribsd-cheri/cheribsd-mips
                # noinspection PyTypeChecker
                from_cmdline = i.load_option(cheri_config, cls, cls, return_none_if_default=True)
                if from_cmdline is not None:
                    status_update("Install directory for", cls.target, "was specified on commandline:", from_cmdline)
                else:
                    cls._install_dir = Path(str(cheri_config.output_root) + str(cheri_config.installation_prefix))
                    cls._check_install_dir_conflict = False

        Target.instantiating_targets_should_warn = False
        for target in cheri_config.targets:
            build_target(cheri_config, target_manager.get_target_raw(target))

    if JenkinsAction.CREATE_TARBALL in cheri_config.action:
        create_tarball(cheri_config)


def build_target(cheri_config, target: Target) -> None:
    # Note: This if exists for now to avoid a large diff.
    if True:
        target.check_system_deps(cheri_config)
        # need to set destdir after check_system_deps:
        project = target.get_or_create_project(None, cheri_config)
        assert project
        _ = project.all_dependency_names(cheri_config)  # Ensure dependencies are cached.
        if isinstance(project, CrossCompileMixin):
            project.destdir = cheri_config.output_root
            project._install_prefix = cheri_config.installation_prefix
            project._install_dir = cheri_config.output_root

        if cheri_config.debug_output:
            status_update("Configuration options for building", project.target, file=sys.stderr)
            for attr in dir(project):
                if attr.startswith("_"):
                    continue
                value = getattr(project, attr)
                if not callable(value):
                    print("   ", attr, "=", pprint.pformat(value, width=160, indent=8, compact=True), file=sys.stderr)
        # delete the install root:
        if JenkinsAction.BUILD in cheri_config.action:
            cleaning_task = cheri_config.FS.async_clean_directory(
                cheri_config.output_root) if not cheri_config.keep_install_dir else ThreadJoiner(None)
            with cleaning_task:
                target.execute(cheri_config)
        if JenkinsAction.TEST in cheri_config.action:
            target.run_tests(cheri_config)


def create_tarball(cheri_config) -> None:
    if True:  # Note: This if exists for now to avoid a large whitespace diff.
        bsdtar_path = shutil.which("bsdtar")
        tar_cmd = None
        tar_flags = ["--invalid-flag"]
        if bsdtar_path:
            bsdtar_version = get_program_version(Path(bsdtar_path), regex=b"bsdtar\\s+(\\d+)\\.(\\d+)\\.?(\\d+)?",
                                                 config=cheri_config)
            if bsdtar_version > (3, 0, 0):
                # Only newer versions support --uid/--gid
                tar_cmd = bsdtar_path
                tar_flags = ["--uid=0", "--gid=0", "--numeric-owner"]
            if bsdtar_version > (3, 2, 0):
                # Use parallel xz compression
                tar_flags.append("--options=xz:threads=" + str(cheri_config.make_jobs))

        if not tar_cmd and (shutil.which("gtar") or OSInfo.IS_LINUX):
            # GNU tar
            tar_cmd = "tar" if OSInfo.IS_LINUX else "gtar"
            tar_flags = ["--owner=0", "--group=0", "--numeric-owner"]

        # bsdtar too old and GNU tar not found
        if not tar_cmd:
            fatal_error("Could not find a usable version of the tar command")
            return
        status_update("Creating tarball", cheri_config.tarball_name)
        # Strip all ELF files:
        if cheri_config.strip_elf_files:
            # TODO: we only accept one target name to infer the correct llvm-strip binary path
            assert len(cheri_config.targets) == 1, "--create-tarball only accepts one target name"
            target = target_manager.get_target_raw(cheri_config.targets[0])
            Target.instantiating_targets_should_warn = False
            project = target.get_or_create_project(None, cheri_config)
            strip_binaries(cheri_config, project, cheri_config.workspace / "tarball")
        run_command(
            [tar_cmd, "--create", "--xz"] + tar_flags + ["-f", cheri_config.tarball_name, "-C", "tarball", "."],
            cwd=cheri_config.workspace)
        run_command("du", "-sh", cheri_config.workspace / cheri_config.tarball_name)


def strip_binaries(_: JenkinsConfig, project: SimpleProject, directory: Path) -> None:
    status_update("Tarball directory size before stripping ELF files:")
    run_command("du", "-sh", directory)
    for root, dirs, filelist in os.walk(str(directory)):
        for file in filelist:
            # Try to shrink the size by stripping all elf binaries
            filepath = Path(root, file)
            if filepath.is_symlink():
                continue
            project.maybe_strip_elf_file(filepath)
    status_update("Tarball directory size after stripping ELF files:")
    run_command("du", "-sh", directory)


def jenkins_main() -> None:
    run_and_kill_children_on_exit(_jenkins_main)
