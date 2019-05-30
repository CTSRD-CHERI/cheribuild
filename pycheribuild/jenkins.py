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
import shlex
import subprocess
import sys
import shutil
import pprint

from pathlib import Path

from .config.loader import ConfigLoaderBase, CommandLineConfigOption
from .config.jenkinsconfig import JenkinsConfig, CrossCompileTarget, JenkinsAction
from .projects.project import SimpleProject, Project
# noinspection PyUnresolvedReferences
from .projects import *  # make sure all projects are loaded so that targetManager gets populated
# noinspection PyUnresolvedReferences
from .projects.cross import *  # make sure all projects are loaded so that targetManager gets populated
from .projects.cross.crosscompileproject import CrossCompileMixin
from .projects.cross.cheribsd import BuildFreeBSDBase
from .targets import targetManager, Target
from .utils import *

EXTRACT_SDK_TARGET = "extract-sdk"

class JenkinsConfigLoader(ConfigLoaderBase):
    """
    A simple config loader that always returns the default value for all added options
    """

    def load(self):
        self._parsedArgs = self._parser.parse_args()
        if self._parsedArgs.targets is None:
            self._parsedArgs.targets = []
        if isinstance(self._parsedArgs.targets, str):
            self._parsedArgs.targets = [self._parsedArgs.targets]
        assert isinstance(self._parsedArgs.targets, list)

    def finalizeOptions(self, availableTargets: list, **kwargs):
        targetOption = self._parser.add_argument("targets", metavar="TARGET", nargs=argparse.OPTIONAL, help="The target to build",
                                                 choices=availableTargets + [EXTRACT_SDK_TARGET])
        if "_ARGCOMPLETE" in os.environ:
            try:
                import argcomplete
            except ImportError:
                sys.exit("argcomplete missing")
            targetCompleter = argcomplete.completers.ChoicesCompleter(availableTargets)
            targetOption.completer = targetCompleter
            argcomplete.autocomplete(
                self._parser,
                always_complete_options=None,  # don't print -/-- by default
                print_suppressed=True,  # also include target-specific options
            )

    def __init__(self):
        super().__init__(CommandLineConfigOption)


class SdkArchive(object):
    def __init__(self, cheriConfig: JenkinsConfig, name, *, required_globs: list=None, extra_args:list=None):
        self.cheriConfig = cheriConfig
        self.archive = cheriConfig.workspace / name  # type: Path
        self.required_globs = [] if required_globs is None else required_globs  # type: list
        self.extra_args = [] if extra_args is None else extra_args  # type: list

    def extract(self):
        assert self.archive.exists(), str(self.archive)
        runCmd(["tar", "Jxf", self.archive, "-C", self.cheriConfig.sdkDir] + self.extra_args, cwd=self.cheriConfig.workspace)
        self.check_required_files()

    def check_required_files(self, fatal=True) -> bool:
        for glob in self.required_globs:
            found = list(self.cheriConfig.sdkDir.glob(glob))
            # print("Matched files:", found)
            if len(found) == 0:
                if fatal:
                    fatalError("required files", glob, "missing. Source archive =", self.archive)
                else:
                    statusUpdate("required files", glob, "missing. Source archive was", self.archive)
                    return False
        return True

    def __repr__(self):
        return str(self.archive)

def get_sdk_archives(cheriConfig, needs_cheribsd_sysroot: bool) -> "typing.List[SdkArchive]":
    # Try the full SDK archive first:
    if cheriConfig.sdkArchivePath.exists():
        required_globs = ["bin/clang"]
        if needs_cheribsd_sysroot:
            required_globs.append("sysroot/usr/include")
        return [SdkArchive(cheriConfig, cheriConfig.sdkArchivePath.name, extra_args=["--strip-components", "1"],
                           required_globs=required_globs)]

    llvm_cpu = os.getenv("LLVM_CPU", "cheri-multi")
    clang_archive_name = "{}-{}-clang-llvm.tar.xz".format(llvm_cpu, os.getenv("LLVM_BRANCH", "master"))
    clang_archive = SdkArchive(cheriConfig, clang_archive_name, required_globs=["bin/clang"],
                               extra_args=["--strip-components", "1"])
    if not clang_archive.archive.exists():
        warningMessage("Neither full SDK archive", cheriConfig.sdkArchiveName, " nor clang archive", clang_archive_name,
                       "exists, will use only existing $WORKSPACE/cherisdk")
        return []
    if cheriConfig.crossCompileTarget == CrossCompileTarget.NATIVE:
        # we need the LLVM builtin includes (should be part of the clang archive)
        clang_archive.required_globs.append("lib/clang/*/include/stddef.h")
        return [clang_archive]
    else:
        if not needs_cheribsd_sysroot or cheriConfig.extract_compiler_only:
            return [clang_archive]  # only need the clang archive
        # if we only extracted the compiler, extract the sysroot now
        cheri_sysroot_archive_name = "{}-{}-cheribsd-world.tar.xz".format(cheriConfig.sdk_cpu, cheriConfig.cheri_sdk_isa_name)
        extra_args = ["--strip-components", "1"]
        # Don't extract FreeBSD binaries on a linux host:
        if not IS_FREEBSD:
            extra_args += ["--exclude", "bin/*"]
        sysroot_archive = SdkArchive(cheriConfig, cheri_sysroot_archive_name, required_globs=["sysroot/usr/include"],
                                     extra_args=extra_args)
        if not sysroot_archive.archive.exists():
            warningMessage("Project needs a full SDK archive but only clang archive was found and",
                           sysroot_archive.archive, "is missing. Will attempt to build anyway but build "
                                                    "will most likely fail.")
            runCmd("ls", "-la", cwd=cheriConfig.workspace)
            return [clang_archive]
        return [clang_archive, sysroot_archive]


def extract_sdk_archives(cheriConfig, archives: "typing.List[SdkArchive]"):
    if cheriConfig.sdkBinDir.is_dir():
        statusUpdate(cheriConfig.sdkBinDir, "already exists, not extracting SDK archives")
        return

    cheriConfig.FS.makedirs(cheriConfig.sdkDir)
    for archive in archives:
        archive.extract()

    if not cheriConfig.sdkBinDir.exists():
        fatalError("SDK bin dir does not exist after extracting sysroot archives!")

    # Use llvm-ar/llvm-ranlib or the host ar/ranlib if they ar/ranlib are missing from archive
    for tool in ("ar", "ranlib", "nm"):
        if not (cheriConfig.sdkBinDir / tool).exists():
            # If llvm-ar/ranlib/nm exists use that
            if (cheriConfig.sdkBinDir / ("llvm-" + tool)).exists():
                cheriConfig.FS.createSymlink(cheriConfig.sdkBinDir / ("llvm-" + tool),
                                             cheriConfig.sdkBinDir / tool, relative=True)
            else:
                # otherwise fall back to the /usr/bin version
                cheriConfig.FS.createSymlink(Path(shutil.which(tool)), cheriConfig.sdkBinDir / tool, relative=False)
            cheriConfig.FS.createBuildtoolTargetSymlinks(cheriConfig.sdkBinDir / tool)
    if not (cheriConfig.sdkBinDir / "ld").exists():
        statusUpdate("Adding missing $SDK/ld link to ld.lld")
        cheriConfig.FS.createSymlink(cheriConfig.sdkBinDir / "ld.lld", cheriConfig.sdkBinDir / "ld", relative=True)


def create_sdk_from_archives(cheriConfig, needs_cheribsd_sysroot=True):
    # If the archive is newer, delete the existing sdk unless --keep-sdk is passed install root:
    possiblyDeleteSdkJob = ThreadJoiner(None)
    archives = get_sdk_archives(cheriConfig, needs_cheribsd_sysroot=needs_cheribsd_sysroot)
    statusUpdate("Will use the following SDK archives:", archives)
    if any(not a.check_required_files(fatal=False) for a in archives):
        # if any of the required files is missing clean up and extract
        statusUpdate("Required files missing -> recreating SDK")
        possiblyDeleteSdkJob = cheriConfig.FS.asyncCleanDirectory(cheriConfig.sdkDir)
    elif cheriConfig.sdkDir.exists() and all(a.archive.exists() for a in archives):
        for a in archives:
            if cheriConfig.sdkDir.stat().st_ctime < a.archive.stat().st_ctime:
                msgkind = statusUpdate if not cheriConfig.keepSdkDir else warningMessage
                msgkind("SDK archive", a.archive, "is newer than the existing SDK directory")
                if not cheriConfig.keepSdkDir:
                    statusUpdate("Deleting old SDK and extracting archive")
                    possiblyDeleteSdkJob = cheriConfig.FS.asyncCleanDirectory(cheriConfig.sdkDir)
                break
    # unpack the SDK if it has not been extracted yet:
    with possiblyDeleteSdkJob:
        extract_sdk_archives(cheriConfig, archives)


def _jenkins_main():
    os.environ["_CHERIBUILD_JENKINS_BUILD"] = "1"
    allTargetNames = list(sorted(targetManager.targetNames))
    configLoader = JenkinsConfigLoader()
    # Register all command line options
    cheriConfig = JenkinsConfig(configLoader, allTargetNames)
    SimpleProject._configLoader = configLoader
    targetManager.registerCommandLineOptions()
    cheriConfig.load()
    if cheriConfig.verbose:
        # json = cheriConfig.getOptionsJSON()  # make sure all config options are loaded
        # pprint.pprint(configLoader.options)
        pass
    setCheriConfig(cheriConfig)

    # special target to extract the sdk
    if JenkinsAction.EXTRACT_SDK in cheriConfig.action or (len(cheriConfig.targets) > 0 and cheriConfig.targets[0] == EXTRACT_SDK_TARGET):
        create_sdk_from_archives(cheriConfig, not cheriConfig.extract_compiler_only)
        sys.exit()

    if cheriConfig.action == [""]:
        fatalError("No action specified, did you mean to pass --build?")
        sys.exit()

    if len(cheriConfig.targets) != 1:
        fatalError("Expected exactly one target!")
        sys.exit()

    if JenkinsAction.BUILD in cheriConfig.action or JenkinsAction.TEST in cheriConfig.action:
        assert len(cheriConfig.targets) == 1
        target = targetManager.get_target_raw(cheriConfig.targets[0])
        if JenkinsAction.BUILD in cheriConfig.action:
            for tgt in targetManager.targets:
                cls = tgt.projectClass
                if issubclass(cls, Project):
                    if not issubclass(cls, BuildFreeBSDBase):
                        # override the default install directory to point to the jenkins WORKSPACE
                        # But don't do it for FreeBSD/CheriBSD derived projects
                        cls.defaultInstallDir = Path(str(cheriConfig.outputRoot) + str(cheriConfig.installationPrefix))
                    i = inspect.getattr_static(cls, "_installDir")
                    assert isinstance(i, CommandLineConfigOption)
                    # But don't change it if it was specified on the command line. Note: This also does the config
                    # inheritance: i.e. setting --cheribsd/install-dir will also affect cheribsd-cheri/cheribsd-mips
                    from_cmdline = i.loadOption(cheriConfig, cls, cls, return_none_if_default=True)
                    if from_cmdline is not None:
                        statusUpdate("Install directory for", cls.target, "was specified on commandline:", from_cmdline)
                    else:
                        if not issubclass(cls, BuildFreeBSDBase):
                            cls._installDir = Path(str(cheriConfig.outputRoot) + str(cheriConfig.installationPrefix))
                        cls._check_install_dir_conflict = False
                    # print(project.projectClass.projectName, project.projectClass.installDir)
            if Path("/cheri-sdk/bin/cheri-unknown-freebsd-clang").exists():
                assert cheriConfig.sdkDir == Path("/cheri-sdk"), cheriConfig.sdkDir
            elif cheriConfig.without_sdk:
                statusUpdate("Not using CHERI SDK, only files from /usr")
                assert cheriConfig.clangPath.exists(), cheriConfig.clangPath
                assert cheriConfig.clangPlusPlusPath.exists(), cheriConfig.clangPlusPlusPath
            elif cheriConfig.cheri_sdk_path:
                expected_clang = cheriConfig.sdkBinDir / "clang"
                if not expected_clang.exists():
                    fatalError("--cheri-sdk-path specified but", expected_clang, "does not exist")
            else:
                create_sdk_from_archives(cheriConfig, needs_cheribsd_sysroot=target.projectClass.needs_cheribsd_sysroot(cheriConfig.crossCompileTarget))

        Target.instantiating_targets_should_warn = False
        target.checkSystemDeps(cheriConfig)
        # need to set destdir after checkSystemDeps:
        project = target.get_or_create_project(None, cheriConfig)
        assert project
        cross_target = project.get_crosscompile_target(cheriConfig)
        if cross_target is not None and cross_target != cheriConfig.crossCompileTarget:
            fatalError("Cannot build project", project.target, "with cross compile target", cross_target.name,
                       "when --cpu is set to", cheriConfig.crossCompileTarget.name)
        if isinstance(project, CrossCompileMixin):
            project.destdir = cheriConfig.outputRoot
            project._installPrefix = cheriConfig.installationPrefix
            project._installDir = cheriConfig.outputRoot

        if cheriConfig.debug_output:
            statusUpdate("Configuration options for building", project.projectName, file=sys.stderr)
            for attr in dir(project):
                if attr.startswith("_"):
                    continue
                value = getattr(project, attr)
                if not callable(value):
                    print("   ", attr, "=", pprint.pformat(value, width=160, indent=8, compact=True), file=sys.stderr)
        # delete the install root:
        if JenkinsAction.BUILD in cheriConfig.action:
            cleaningTask = cheriConfig.FS.asyncCleanDirectory(cheriConfig.outputRoot) if not cheriConfig.keepInstallDir else ThreadJoiner(None)
            new_path = os.getenv("PATH", "")
            if not cheriConfig.without_sdk:
                new_path = str(cheriConfig.sdkBinDir) + ":" + new_path
            with setEnv(PATH=new_path):
                with cleaningTask:
                    target.execute(cheriConfig)
        if JenkinsAction.TEST in cheriConfig.action:
            target.run_tests(cheriConfig)


    if JenkinsAction.CREATE_TARBALL in cheriConfig.action:
        bsdtar_path = shutil.which("bsdtar")
        tar_cmd = None
        owner_flags = ["--invalid-flag"]
        if bsdtar_path:
            bsdtar_version = get_program_version(Path(bsdtar_path), regex=b"bsdtar\\s+(\\d+)\\.(\\d+)\\.?(\\d+)?")
            if bsdtar_version > (3, 0, 0):
                # Only newer versions support --uid/--gid
                tar_cmd = bsdtar_path
                owner_flags = ["--uid=0", "--gid=0", "--numeric-owner"]

        if not tar_cmd and (shutil.which("gtar") or IS_LINUX):
            # GNU tar
            tar_cmd = "tar" if IS_LINUX else "gtar"
            owner_flags = ["--owner=0", "--group=0", "--numeric-owner"]

        # bsdtar too old and GNU tar not found
        if not tar_cmd:
            fatalError("Could not find a usable version of the tar command")
            return
        statusUpdate("Creating tarball", cheriConfig.tarball_name)
        # Strip all ELF files:
        if cheriConfig.strip_elf_files:
            strip_binaries(cheriConfig, cheriConfig.workspace / "tarball")
        runCmd([tar_cmd, "--create", "--xz"] + owner_flags + ["-f", cheriConfig.tarball_name, "-C", "tarball", "."], cwd=cheriConfig.workspace)


def strip_binaries(cheriConfig: JenkinsConfig, directory: Path):
    statusUpdate("Tarball size before stripping ELF files:")
    runCmd("du", "-sh", directory)
    for root, dirs, files in os.walk(str(directory)):
        for file in files:
            # Try to shrink the size by stripping all elf binaries
            filepath = Path(root, file)
            if filepath.is_symlink():
                continue
            try:
                with filepath.open("rb") as f:
                    if f.read(4) == b"\x7fELF":
                        # self.verbose_print("Stripping ELF binary", filepath)
                        runCmd(cheriConfig.sdkBinDir / "llvm-strip", filepath)
            except Exception as e:
                warningMessage("Failed to detect type of file:", filepath, e)
    statusUpdate("Tarball size after stripping ELF files:")
    runCmd("du", "-sh", directory)

def jenkins_main():
    try:
        _jenkins_main()
    except KeyboardInterrupt:
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError as err:
        fatalError("Command ", "`" + commandline_to_str(err.cmd) + "` failed with non-zero exit code",
                   err.returncode)
