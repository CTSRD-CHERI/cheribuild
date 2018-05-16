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
import shlex
import subprocess
import sys
import shutil
import pprint

from pathlib import Path

from .config.loader import ConfigLoaderBase, CommandLineConfigOption
from .config.jenkinsconfig import JenkinsConfig, CrossCompileTarget
from .projects.project import SimpleProject, Project
# noinspection PyUnresolvedReferences
from .projects import *  # make sure all projects are loaded so that targetManager gets populated
# noinspection PyUnresolvedReferences
from .projects.cross import *  # make sure all projects are loaded so that targetManager gets populated
from .projects.cross.crosscompileproject import CrossCompileMixin
from .targets import targetManager, Target
from .utils import *

EXTRACT_SDK_TARGET = "extract-sdk"

class JenkinsConfigLoader(ConfigLoaderBase):
    """
    A simple config loader that always returns the default value for all added options
    """

    def load(self):
        self._parsedArgs = self._parser.parse_args()

    def finalizeOptions(self, availableTargets: list, **kwargs):
        targetOption = self._parser.add_argument("targets", metavar="TARGET", nargs=1, help="The target to build",
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

    def parseArguments(self):
        self._parsedArgs = self._parser.parse_args()


class SdkArchive(object):
    def __init__(self, cheriConfig: JenkinsConfig, name, *, required_globs: list=None, extra_args:list=None):
        self.cheriConfig = cheriConfig
        self.archive = cheriConfig.workspace / name  # type: Path
        self.required_globs = [] if required_globs is None else required_globs  # type: list
        self.extra_args = [] if extra_args is None else extra_args  # type: list

    def extract(self):
        assert self.archive.exists()
        runCmd(["tar", "Jxf", self.archive, "-C", self.cheriConfig.sdkDir] + self.extra_args)
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

def get_sdk_archives(cheriConfig) -> "typing.List[SdkArchive]":
    # Try the full SDK archive first:
    if cheriConfig.sdkArchivePath.exists():
        return [SdkArchive(cheriConfig, cheriConfig.sdkArchivePath.name, extra_args=["--strip-components", "1"],
                           required_globs=["bin/clang", "sysroot/usr/include"])]

    llvm_cpu = os.getenv("LLVM_CPU", "cheri-multi")
    clang_archive_name = "{}-{}-clang-llvm.tar.xz".format(llvm_cpu, os.getenv("LLVM_BRANCH", "master"))
    clang_archive = SdkArchive(cheriConfig, clang_archive_name, required_globs=["bin/clang"],
                               extra_args=["--strip-components", "1"])
    if not clang_archive.archive.exists():
        warningMessage("Neither full SDK archive", cheriConfig.sdkArchiveName, " nor clang archive", clang_archive_name,
                       "exists, will use only existing $WORKSPACE/cherisdk")
        return []
    if cheriConfig.crossCompileTarget == CrossCompileTarget.NATIVE:
        # we need the LLVM builtin includes:
        llvm_includes_name = "{}-{}-clang-include.tar.xz".format(llvm_cpu, os.getenv("LLVM_BRANCH", "master"))
        includes_archive = SdkArchive(cheriConfig, llvm_includes_name, required_globs=["lib/clang/*/include/stddef.h"])
        return [clang_archive, includes_archive]
    else:
        # if we only extracted the compiler, extract the sysroot now
        cheri_sysroot_archive_name = "{}-vanilla-jemalloc-cheribsd-world.tar.xz".format(cheriConfig.sdk_cpu)
        extra_args = ["--strip-components", "1"]
        # Don't extract FreeBSD binaries on a linux host:
        if not IS_FREEBSD:
            extra_args += ["--exclude", "bin/*"]
        sysroot_archive = SdkArchive(cheriConfig, cheri_sysroot_archive_name, required_globs=["sysroot/usr/include"],
                                     extra_args=extra_args)
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

    # Use the host ar/ranlib if they are missing
    for tool in ("ar", "ranlib"):
        if not (cheriConfig.sdkDir / "bin" / tool).exists():
            cheriConfig.FS.createSymlink(Path(shutil.which(tool)), cheriConfig.sdkBinDir / tool, relative=False)
            cheriConfig.FS.createBuildtoolTargetSymlinks(cheriConfig.sdkBinDir / tool)


def create_sdk_from_archives(cheriConfig):
    # If the archive is newer, delete the existing sdk unless --keep-sdk is passed install root:
    possiblyDeleteSdkJob = ThreadJoiner(None)
    archives = get_sdk_archives(cheriConfig)
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
    if cheriConfig.targets[0] == EXTRACT_SDK_TARGET:
        create_sdk_from_archives(cheriConfig)
        sys.exit()

    if cheriConfig.do_build:
        if Path("/cheri-sdk/bin/cheri-unknown-freebsd-clang").exists():
            assert cheriConfig.sdkDir == Path("/cheri-sdk"), cheriConfig.sdkDir
        elif cheriConfig.without_sdk:
            statusUpdate("Not using CHERI SDK, only files from /usr")
            assert cheriConfig.clangPath.exists(), cheriConfig.clangPath
            assert cheriConfig.clangPlusPlusPath.exists(), cheriConfig.clangPlusPlusPath
        else:
            create_sdk_from_archives(cheriConfig)

        assert len(cheriConfig.targets) == 1
        target = targetManager.get_target(cheriConfig.targets[0])
        for tgt in targetManager.targets:
            cls = tgt.projectClass
            if issubclass(cls, Project):
                cls.defaultInstallDir = Path(str(cheriConfig.outputRoot) + str(cheriConfig.installationPrefix))
                cls.installDir = Path(str(cheriConfig.outputRoot) + str(cheriConfig.installationPrefix))
                # print(project.projectClass.projectName, project.projectClass.installDir)
        Target.instantiating_targets_should_warn = False
        target.checkSystemDeps(cheriConfig)
        # need to set destdir after checkSystemDeps:
        project = target.get_or_create_project(cheriConfig)
        assert project
        if isinstance(project, CrossCompileMixin):
            project.destdir = cheriConfig.outputRoot
            project.installPrefix = cheriConfig.installationPrefix
            project.installDir = cheriConfig.outputRoot
        statusUpdate("Configuration options for building", project.projectName)
        for attr in dir(project):
            if attr.startswith("_"):
                continue
            value = getattr(project, attr)
            if not callable(value):
                print("   ", attr, "=", pprint.pformat(value, width=160, indent=8, compact=True))
        # delete the install root:
        cleaningTask = cheriConfig.FS.asyncCleanDirectory(cheriConfig.outputRoot) if not cheriConfig.keepInstallDir else ThreadJoiner(None)
        with cleaningTask:
            target.execute()

    if cheriConfig.do_tarball:
        if IS_LINUX:
            owner_flags = ["--owner=0", "--group=0", "--numeric-owner"]
        else:
            owner_flags = ["--uid=0", "--gid=0", "--numeric-owner"]
        statusUpdate("Creating tarball", cheriConfig.tarball_name)
        runCmd(["tar", "--create", "--xz"] + owner_flags + ["-f", cheriConfig.tarball_name, "-C", "tarball", "."])


def jenkins_main():
    try:
        _jenkins_main()
    except KeyboardInterrupt:
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError as err:
        fatalError("Command ", "`" + commandline_to_str(err.cmd) + "` failed with non-zero exit code",
                   err.returncode)
