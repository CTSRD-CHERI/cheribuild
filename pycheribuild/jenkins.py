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
import inspect
import os
import pprint
import shutil
import subprocess
import sys
import typing
# noinspection PyUnresolvedReferences
from pathlib import Path

from .config.jenkinsconfig import JenkinsAction, JenkinsConfig
from .config.loader import CommandLineConfigOption, ConfigLoaderBase
# make sure all projects are loaded so that target_manager gets populated
# noinspection PyUnresolvedReferences
from .projects import *  # noqa: F401,F403
# noinspection PyUnresolvedReferences
from .projects.cross import *  # noqa: F401,F403
from .projects.cross.crosscompileproject import CrossCompileMixin
from .projects.project import Project, SimpleProject
from .targets import MultiArchTargetAlias, SimpleTargetAlias, Target, target_manager
from .utils import (commandline_to_str, fatal_error, get_program_version, init_global_config, OSInfo, run_command,
                    set_env,
                    status_update, ThreadJoiner, warning_message)

EXTRACT_SDK_TARGET = "extract-sdk"


class JenkinsConfigLoader(ConfigLoaderBase):
    """
    A simple config loader that always returns the default value for all added options
    """

    def load(self):
        self._parsed_args = self._parser.parse_args()
        if self._parsed_args.targets is None:
            self._parsed_args.targets = []
        if isinstance(self._parsed_args.targets, str):
            self._parsed_args.targets = [self._parsed_args.targets]
        assert isinstance(self._parsed_args.targets, list)

    def finalize_options(self, available_targets: list, **kwargs):
        target_option = self._parser.add_argument("targets", metavar="TARGET", nargs=argparse.OPTIONAL,
                                                  help="The target to build",
                                                  choices=available_targets + [EXTRACT_SDK_TARGET])
        if self._completing_arguments:
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

    def __init__(self):
        super().__init__(CommandLineConfigOption)


class SdkArchive(object):
    def __init__(self, cheri_config: JenkinsConfig, name, *, required_globs: list = None, extra_args: list = None):
        self.cheri_config = cheri_config
        self.archive = cheri_config.workspace / name  # type: Path
        self.required_globs = [] if required_globs is None else required_globs  # type: list
        self.extra_args = [] if extra_args is None else extra_args  # type: list

    def extract(self):
        assert self.archive.exists(), str(self.archive)
        run_command(["tar", "Jxf", self.archive, "-C", self.cheri_config.cheri_sdk_dir] + self.extra_args,
                    cwd=self.cheri_config.workspace)
        self.check_required_files()

    def check_required_files(self, fatal=True) -> bool:
        for glob in self.required_globs:
            found = list(self.cheri_config.cheri_sdk_dir.glob(glob))
            # print("Matched files:", found)
            if len(found) == 0:
                if fatal:
                    fatal_error("required files", glob, "missing. Source archive =", self.archive)
                else:
                    status_update("required files", glob, "missing. Source archive was", self.archive)
                    return False
        return True

    def __repr__(self):
        return str(self.archive)


def get_sdk_archives(cheri_config, needs_cheribsd_sysroot: bool) -> "typing.List[SdkArchive]":
    # Try the full SDK archive first:
    if cheri_config.sdk_archive_path.exists():
        required_globs = ["bin/clang"]
        if needs_cheribsd_sysroot:
            required_globs.append("sysroot/usr/include")
        return [SdkArchive(cheri_config, cheri_config.sdk_archive_path.name, extra_args=["--strip-components", "1"],
                           required_globs=required_globs)]

    llvm_cpu = os.getenv("LLVM_CPU", "cheri-multi")
    clang_archive_name = "{}-{}-clang-llvm.tar.xz".format(llvm_cpu, os.getenv("LLVM_BRANCH", "master"))
    clang_archive = SdkArchive(cheri_config, clang_archive_name, required_globs=["bin/clang"],
                               extra_args=["--strip-components", "1"])
    if not clang_archive.archive.exists():
        warning_message("Neither full SDK archive", cheri_config.sdk_archive_name, " nor clang archive",
                        clang_archive_name,
                        "exists, will use only existing $WORKSPACE/cherisdk")
        return []
    if not needs_cheribsd_sysroot or cheri_config.extract_compiler_only:
        return [clang_archive]  # only need the clang archive
    # if we only extracted the compiler, extract the sysroot now
    cheri_sysroot_archive_name = "{}-{}-cheribsd-world.tar.xz".format(cheri_config.sdk_cpu,
                                                                      cheri_config.cheri_sdk_isa_name)
    extra_args = ["--strip-components", "1"]
    # Don't extract FreeBSD binaries on a linux host:
    if not OSInfo.IS_FREEBSD:
        extra_args += ["--exclude", "bin/*"]
    sysroot_archive = SdkArchive(cheri_config, cheri_sysroot_archive_name, required_globs=["sysroot/usr/include"],
                                 extra_args=extra_args)
    if not sysroot_archive.archive.exists():
        warning_message("Project needs a full SDK archive but only clang archive was found and",
                        sysroot_archive.archive, "is missing. Will attempt to build anyway but build "
                                                 "will most likely fail.")
        run_command("ls", "-la", cwd=cheri_config.workspace)
        return [clang_archive]
    return [clang_archive, sysroot_archive]


def extract_sdk_archives(cheri_config: JenkinsConfig, archives: "typing.List[SdkArchive]"):
    if cheri_config.cheri_sdk_bindir.is_dir():
        status_update(cheri_config.cheri_sdk_bindir, "already exists, not extracting SDK archives")
        return

    cheri_config.FS.makedirs(cheri_config.cheri_sdk_dir)
    for archive in archives:
        archive.extract()

    if not cheri_config.cheri_sdk_bindir.exists():
        fatal_error("SDK bin dir does not exist after extracting sysroot archives!")

    # Use llvm-ar/llvm-ranlib or the host ar/ranlib if they ar/ranlib are missing from archive
    for tool in ("ar", "ranlib", "nm"):
        if not (cheri_config.cheri_sdk_bindir / tool).exists():
            # If llvm-ar/ranlib/nm exists use that
            if (cheri_config.cheri_sdk_bindir / ("llvm-" + tool)).exists():
                cheri_config.FS.create_symlink(cheri_config.cheri_sdk_bindir / ("llvm-" + tool),
                                               cheri_config.cheri_sdk_bindir / tool, relative=True)
            else:
                # otherwise fall back to the /usr/bin version
                cheri_config.FS.create_symlink(Path(shutil.which(tool)), cheri_config.cheri_sdk_bindir / tool,
                                               relative=False)
    if not (cheri_config.cheri_sdk_bindir / "ld").exists():
        status_update("Adding missing $SDK/ld link to ld.lld")
        cheri_config.FS.create_symlink(cheri_config.cheri_sdk_bindir / "ld.lld",
                                       cheri_config.cheri_sdk_bindir / "ld", relative=True)


def create_sdk_from_archives(cheri_config: JenkinsConfig, needs_cheribsd_sysroot):
    # If the archive is newer, delete the existing sdk unless --keep-sdk is passed install root:
    possibly_delete_sdk_job = ThreadJoiner(None)
    archives = get_sdk_archives(cheri_config, needs_cheribsd_sysroot=needs_cheribsd_sysroot)
    status_update("Will use the following SDK archives:", archives)
    if any(not a.check_required_files(fatal=False) for a in archives):
        # if any of the required files is missing clean up and extract
        status_update("Required files missing -> recreating SDK")
        possibly_delete_sdk_job = cheri_config.FS.async_clean_directory(cheri_config.cheri_sdk_dir)
    elif cheri_config.cheri_sdk_dir.exists() and all(a.archive.exists() for a in archives):
        for a in archives:
            if cheri_config.cheri_sdk_dir.stat().st_ctime < a.archive.stat().st_ctime:
                msgkind = status_update if not cheri_config.keep_sdk_dir else warning_message
                msgkind("SDK archive", a.archive, "is newer than the existing SDK directory")
                if not cheri_config.keep_sdk_dir:
                    status_update("Deleting old SDK and extracting archive")
                    possibly_delete_sdk_job = cheri_config.FS.async_clean_directory(cheri_config.cheri_sdk_dir)
                break
    # unpack the SDK if it has not been extracted yet:
    with possibly_delete_sdk_job:
        extract_sdk_archives(cheri_config, archives)


def _jenkins_main():
    os.environ["_CHERIBUILD_JENKINS_BUILD"] = "1"
    all_target_names = list(sorted(target_manager.target_names))
    config_loader = JenkinsConfigLoader()
    # Register all command line options
    cheri_config = JenkinsConfig(config_loader, all_target_names)
    SimpleProject._config_loader = config_loader
    target_manager.register_command_line_options()
    cheri_config.load()
    if cheri_config.verbose:
        # json = cheri_config.get_options_json()  # make sure all config options are loaded
        # pprint.pprint(config_loader.options)
        pass
    init_global_config(test_mode=False, pretend_mode=cheri_config.pretend,
                       verbose_mode=cheri_config.verbose, quiet_mode=cheri_config.quiet)

    # special target to extract the sdk
    if JenkinsAction.EXTRACT_SDK in cheri_config.action or (
            len(cheri_config.targets) > 0 and cheri_config.targets[0] == EXTRACT_SDK_TARGET):
        create_sdk_from_archives(cheri_config, not cheri_config.extract_compiler_only)
        sys.exit()

    if cheri_config.action == [""]:
        fatal_error("No action specified, did you mean to pass --build?")
        sys.exit()

    if len(cheri_config.targets) != 1:
        fatal_error("Expected exactly one target!")
        sys.exit()

    if JenkinsAction.BUILD in cheri_config.action or JenkinsAction.TEST in cheri_config.action:
        assert len(cheri_config.targets) == 1
        target = target_manager.get_target_raw(cheri_config.targets[0])

        for tgt in target_manager.targets:
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
                # print(project.project_class.project_name, project.project_class.install_dir)

        Target.instantiating_targets_should_warn = False
        target.check_system_deps(cheri_config)
        # need to set destdir after check_system_deps:
        project = target.get_or_create_project(cheri_config.preferred_xtarget, cheri_config)
        assert project
        cross_target = project.get_crosscompile_target(cheri_config)
        if isinstance(target,
                      MultiArchTargetAlias) and cross_target is not None and cross_target != \
                cheri_config.preferred_xtarget \
                and cheri_config.preferred_xtarget is not None:
            fatal_error("Cannot build project", project.target, "with cross compile target", cross_target.name,
                        "when --cpu is set to", cheri_config.preferred_xtarget.name, fatal_when_pretending=True)
        if isinstance(project, CrossCompileMixin):
            project.destdir = cheri_config.output_root
            project._install_prefix = cheri_config.installation_prefix
            project._install_dir = cheri_config.output_root

        if JenkinsAction.BUILD in cheri_config.action:
            if Path("/cheri-sdk/bin/cheri-unknown-freebsd-clang").exists():
                assert cheri_config.cheri_sdk_dir == Path("/cheri-sdk"), cheri_config.cheri_sdk_dir
            elif cheri_config.without_sdk:
                status_update("Not using CHERI SDK, only files from /usr")
                assert cheri_config.clang_path.exists(), cheri_config.clang_path
                assert cheri_config.clang_plusplus_path.exists(), cheri_config.clang_plusplus_path
            elif cheri_config.cheri_sdk_path:
                expected_clang = cheri_config.cheri_sdk_bindir / "clang"
                if not expected_clang.exists():
                    fatal_error("--cheri-sdk-path specified but", expected_clang, "does not exist")
            else:
                need_cheribsd_sysroot = project.needs_sysroot and project.target_info.is_cheribsd()
                create_sdk_from_archives(cheri_config, needs_cheribsd_sysroot=need_cheribsd_sysroot)

        if project.needs_sysroot and not project.target_info.sysroot_dir.exists() and JenkinsAction.BUILD in \
                cheri_config.action:
            fatal_error("Sysroot directory", project.target_info.sysroot_dir, "does not exist")

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
            new_path = os.getenv("PATH", "")
            if not cheri_config.without_sdk:
                new_path = str(cheri_config.cheri_sdk_bindir) + ":" + new_path
            with set_env(PATH=new_path):
                with cleaning_task:
                    target.execute(cheri_config)
        if JenkinsAction.TEST in cheri_config.action:
            target.run_tests(cheri_config)

    if JenkinsAction.CREATE_TARBALL in cheri_config.action:
        bsdtar_path = shutil.which("bsdtar")
        tar_cmd = None
        tar_flags = ["--invalid-flag"]
        if bsdtar_path:
            bsdtar_version = get_program_version(Path(bsdtar_path), regex=b"bsdtar\\s+(\\d+)\\.(\\d+)\\.?(\\d+)?")
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
            strip_binaries(cheri_config, cheri_config.workspace / "tarball")
        run_command(
            [tar_cmd, "--create", "--xz"] + tar_flags + ["-f", cheri_config.tarball_name, "-C", "tarball", "."],
            cwd=cheri_config.workspace)
        run_command("du", "-sh", cheri_config.workspace / cheri_config.tarball_name)


def strip_binaries(cheri_config: JenkinsConfig, directory: Path):
    status_update("Tarball directory size before stripping ELF files:")
    run_command("du", "-sh", directory)
    for root, dirs, filelist in os.walk(str(directory)):
        for file in filelist:
            # Try to shrink the size by stripping all elf binaries
            filepath = Path(root, file)
            if filepath.is_symlink():
                continue
            try:
                with filepath.open("rb") as f:
                    if f.read(4) == b"\x7fELF":
                        # self.verbose_print("Stripping ELF binary", filepath)
                        run_command(cheri_config.cheri_sdk_bindir / "llvm-strip", filepath)
            except Exception as e:
                warning_message("Failed to detect type of file:", filepath, e)
    status_update("Tarball directory size after stripping ELF files:")
    run_command("du", "-sh", directory)


def jenkins_main():
    try:
        _jenkins_main()
    except KeyboardInterrupt:
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError as err:
        fatal_error("Command ", "`" + commandline_to_str(err.cmd) + "` failed with non-zero exit code",
                    err.returncode)
