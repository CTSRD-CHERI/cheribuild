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
import copy
import datetime
import inspect
import itertools
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import typing
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Tuple

from .simple_project import SimpleProject, _default_stdout_filter
from ..config.chericonfig import BuildType, CheriConfig, ComputedDefaultValue, Linkage, supported_build_type_strings
from ..config.loader import ConfigOptionBase
from ..config.target_info import (AbstractProject, AutoVarInit, BasicCompilationTargets, CPUArchitecture,
                                  CrossCompileTarget, TargetInfo, NativeTargetInfo)
from ..processutils import (commandline_to_str, CompilerInfo, get_compiler_info, get_program_version,
                            get_version_output, run_command, ssh_host_accessible)
from ..targets import target_manager
from ..utils import (AnsiColour, cached_property, classproperty, coloured, include_local_file, InstallInstructions,
                     is_jenkins_build, OSInfo, remove_prefix, status_update, ThreadJoiner, remove_duplicates)

__all__ = ["Project", "CMakeProject", "AutotoolsProject", "CheriConfig", "MakeOptions", "MakeCommandKind", # no-combine
           "MercurialRepository", "CrossCompileTarget", "CPUArchitecture", "GitRepository", # no-combine
           "commandline_to_str", "ComputedDefaultValue", "TargetInfo", "ReuseOtherProjectRepository", # no-combine
           "ExternallyManagedSourceRepository", "ReuseOtherProjectDefaultTargetRepository", # no-combine
           "MakefileProject", "MesonProject", "TargetBranchInfo", "Linkage", "BasicCompilationTargets",  # no-combine
           "DefaultInstallDir", "BuildType", "SubversionRepository", "default_source_dir_in_subdir"] # no-combine


def install_dir_not_specified(_: CheriConfig, project: "Project"):
    raise RuntimeError("install_dir_not_specified! dummy impl must not be called: " + str(project))


def _default_build_dir(config: CheriConfig, project: "SimpleProject"):
    assert isinstance(project, Project)
    target = project.get_crosscompile_target(config)
    return project.build_dir_for_target(target)


class MakeCommandKind(Enum):
    DefaultMake = "system default make"
    GnuMake = "GNU make"
    BsdMake = "BSD make"
    Ninja = "ninja"
    CMake = "cmake"
    CustomMakeTool = "custom make tool"


class MakeOptions(object):
    def __init__(self, kind: MakeCommandKind, project: SimpleProject, **kwargs):
        self.__project = project
        self._vars = OrderedDict()  # type: typing.Dict[str, str]
        # Used by e.g. FreeBSD:
        self._with_options = OrderedDict()  # type: typing.Dict[str, bool]
        self._flags = []  # type: typing.List[str]
        self.env_vars = {}  # type: typing.Dict[str, str]
        self.set(**kwargs)
        self.kind = kind
        # We currently need to differentiate cmake driving ninja and cmake driving make since there is no
        # generator-independent option to pass -k (and ninja/make expect a different format)
        self.subkind = None
        self.__can_pass_j_flag = None  # type: typing.Optional[bool]
        self.__command = None  # type: typing.Optional[str]
        self.__command_args = []  # type: typing.List[str]

    def __deepcopy__(self, memo):
        assert False, "Should not be called!"
        pass

    @staticmethod
    def __do_set(target_dict: "typing.Dict[str, str]", **kwargs):
        for k, v in kwargs.items():
            if isinstance(v, bool):
                v = "1" if v else "0"
            if isinstance(v, (Path, int)):
                v = str(v)
            assert isinstance(v, str), "Should only pass int/bool/str/Path here and not " + str(type(v))
            target_dict[k] = v

    def set(self, **kwargs):
        self.__do_set(self._vars, **kwargs)

    def set_env(self, **kwargs):
        self.__do_set(self.env_vars, **kwargs)

    def set_with_options(self, **kwargs):
        """
        For every argument in kwargs sets a WITH_FOO if FOO=True or a WITHOUT_FOO if FOO=False
        Used by the FreeBSD build sysmtem: e.g. make -DWITH_MAN / -DWITHOUT_MAN
        :return: dict of VAR=True/False
        """
        for k, v in kwargs.items():
            assert not k.startswith("WITH_"), "Invalid WITH/WITHOUT options name " + k
            assert not k.startswith("WITHOUT_"), "Invalid WITH/WITHOUT options name " + k
            assert isinstance(v, bool)
            self._with_options[k] = v

    def add_flags(self, *args) -> None:
        """
        :param args: the flags to add (e.g. -j 16, etc.)
        """
        self._flags.extend(args)

    def _get_defined_var(self, name) -> str:
        # BSD make supports a -DVAR syntax but GNU doesn't
        if self.kind == MakeCommandKind.BsdMake:
            return "-D" + name
        else:
            assert self.kind in (MakeCommandKind.GnuMake, MakeCommandKind.DefaultMake)
            return name + "=1"

    @property
    def is_gnu_make(self):
        if self.kind == MakeCommandKind.GnuMake:
            return True
        if self.kind != MakeCommandKind.DefaultMake:
            return False
        # otherwise parse make --version
        return b"GNU Make" in get_version_output(Path(self.command))

    @property
    def command(self) -> str:
        # Don't cache this value in case the user changes the kind
        if self.__command is not None:
            return self.__command
        cmd = self.__infer_command()
        assert self.kind == MakeCommandKind.CustomMakeTool or not Path(cmd).is_absolute()
        return cmd

    # noinspection PyProtectedMember
    def __infer_command(self) -> str:
        if self.kind == MakeCommandKind.DefaultMake:
            if OSInfo.IS_MAC and shutil.which("gmake"):
                # Using /usr/bin/make on macOS breaks compilation DB creation with bear since SIP prevents it from
                # injecting shared libraries into any process that is installed as part of the system.
                # Prefer homebrew-installed gmake if it is available.
                return "gmake"
            else:
                self.__project.add_required_system_tool("make")
                return "make"
        elif self.kind == MakeCommandKind.GnuMake:
            if OSInfo.IS_LINUX and not shutil.which("gmake"):
                status_update("Could not find `gmake` command, assuming `make` is GNU make")
                self.__project.add_required_system_tool("make")
                return "make"
            else:
                self.__project.add_required_system_tool("gmake", homebrew="make")
                return "gmake"
        elif self.kind == MakeCommandKind.BsdMake:
            return "make" if OSInfo.IS_FREEBSD else "bmake"
        elif self.kind == MakeCommandKind.Ninja:
            self.__project.add_required_system_tool("ninja", homebrew="ninja", apt="ninja-build")
            return "ninja"
        elif self.kind == MakeCommandKind.CMake:
            assert self.__project.has_required_system_tool("cmake")
            assert self.subkind is not None
            return "cmake"
        else:
            if self.__command is not None:
                return self.__command
            self.__project.fatal("Cannot infer path from CustomMakeTool. Set self.make_args.set_command(\"tool\")")
            raise RuntimeError()

    def set_command(self, value, can_pass_j_flag=True, early_args: "typing.List[str]" = None, *,
                    install_instructions=None):
        self.__command = str(value)
        if early_args is None:
            early_args = []
        self.__command_args = early_args
        assert isinstance(self.__command_args, list)
        # noinspection PyProtectedMember
        if not Path(value).is_absolute():
            self.__project.add_required_system_tool(value, install_instructions=install_instructions)
        self.__can_pass_j_flag = can_pass_j_flag

    @property
    def all_commandline_args(self) -> list:
        return self.get_commandline_args()

    def get_commandline_args(self, *, targets: "typing.List[str]" = None, jobs: int = None, verbose=False,
                             continue_on_error=False) -> "typing.List[str]":
        assert self.kind
        result = list(self.__command_args)
        actual_build_tool = self.kind
        # TODO: this code is rather ugly. It would probably be a lot simpler to use inheritance.
        if self.kind == MakeCommandKind.CMake:
            assert self.subkind is not None
            # For CMake we pass target, jobs, and verbose directly to cmake, all other options are fowarded to the real
            # build tool. Ideally we wouldn't care about the underlying build tool, but we want to be able to pass the
            # -k flag.
            actual_build_tool = self.subkind
            result.extend(["--build", "."])
            # TODO: pass CMake version instead of using the minimum to check for --build features
            # noinspection PyProtectedMember
            cmake_version = CMakeProject._minimum_cmake_or_meson_version
            if jobs:
                # -j added in 3.12: https://cmake.org/cmake/help/latest/release/3.12.html#command-line
                assert cmake_version >= (3, 12, 0)
                result.extend(["-j", str(jobs)])
                jobs = None  # don't pass the flag to the build tool again
            if verbose:
                # --verbose added in 3.14: https://cmake.org/cmake/help/latest/release/3.14.html#command-line
                if cmake_version >= (3, 14, 0):
                    result.append("--verbose")
                    verbose = None  # don't pass the flag to the build tool again
            if targets:
                # CMake 3.15 allows multiple targets to be passed to --target. For older versions we pass the
                # targets as arguments to the build tool. This will work for make and ninja (and other generators are
                # not really supported anyway).
                result.append("--target")
                assert all(isinstance(t, str) for t in targets), "Invalid empty/non-string target name"
                if cmake_version >= (3, 15, 0):
                    result.extend(targets)
                    targets = None  # don't pass the targets to the build tool again
                else:
                    result.append(targets[0])
                    targets = targets[1:]  # pass remaining targets to the build tool directly
            # Forward all remaining arguments to make/ninja
            result.append("--")

        # All other options are forwarded to the actual tool.
        if jobs and self.can_pass_jflag:
            result.append("-j" + str(jobs))
        # Cmake and ninja have an explicit verbose flag, other build tools use custom env vars, etc.
        if verbose and actual_build_tool == MakeCommandKind.Ninja:
            result.append("-v")
        if targets:
            assert all(isinstance(t, str) for t in targets), "Invalid empty/non-string target name"
            result.extend(targets)

        # First all the variables:
        for k, v in self._vars.items():
            assert isinstance(v, str)
            if v == "1":
                result.append(self._get_defined_var(k))
            else:
                result.append(k + "=" + v)
        # then the WITH/WITHOUT variables:
        for k, v in self._with_options.items():
            result.append(self._get_defined_var("WITH_" if v else "WITHOUT_") + k)
        # and finally the command line flags like -k
        result.extend(self._flags)
        if continue_on_error:
            continue_flag = "-k"
            if actual_build_tool == MakeCommandKind.Ninja:
                # Ninja expects a maximum number of jobs that can fail instead of continuing for as long as possible.
                continue_flag += "50"
            result.append(continue_flag)
        return result

    def remove_var(self, variable):
        if variable in self._vars:
            del self._vars[variable]
        if variable in self._with_options:
            del self._with_options[variable]
        for flag in self._flags.copy():
            if flag.strip() == "-D" + variable or flag.startswith(variable + "="):
                self._flags.remove(flag)

    def get_var(self, variable, default=None):
        return self._vars.get(variable, default)

    def remove_flag(self, flag: str):
        if flag in self._flags:
            self._flags.remove(flag)

    def remove_all(self, predicate: "typing.Callable[[str], bool]"):
        keys = list(self._vars.keys())
        for k in keys:
            if predicate(k):
                del self._vars[k]

    def copy(self):
        result = copy.copy(self)
        # Make sure that the list and dict objects are different
        result._vars = copy.deepcopy(self._vars)
        result._with_options = copy.deepcopy(self._with_options)
        result._flags = copy.deepcopy(self._flags)
        result.env_vars = copy.deepcopy(self.env_vars)
        return result

    def update(self, other: "MakeOptions"):
        self._vars.update(other._vars)
        self._with_options.update(other._with_options)
        self._flags.extend(other._flags)
        self.env_vars.update(other.env_vars)

    @property
    def can_pass_jflag(self):
        if self.__can_pass_j_flag is not None:
            return self.__can_pass_j_flag
        return self.kind != MakeCommandKind.CustomMakeTool


class SourceRepository(object):
    def ensure_cloned(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: Path,
                      skip_submodules=False) -> None:
        raise NotImplementedError

    def update(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: Path = None, revision=None,
               skip_submodules=False) -> None:
        raise NotImplementedError

    def get_real_source_dir(self, caller: SimpleProject, base_project_source_dir: Path) -> Path:
        return base_project_source_dir


class ExternallyManagedSourceRepository(SourceRepository):
    def ensure_cloned(self, current_project: "Project", src_dir: Path, **kwargs):
        current_project.info("Not cloning repositiory since it is externally managed")

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        current_project.info("Not updating", src_dir, "since it is externally managed")


class ReuseOtherProjectRepository(SourceRepository):
    def __init__(self, source_project: "typing.Type[Project]", *, subdirectory=".",
                 repo_for_target: CrossCompileTarget = None, do_update=False):
        self.source_project = source_project
        self.subdirectory = subdirectory
        self.repo_for_target = repo_for_target
        self.do_update = do_update

    def ensure_cloned(self, current_project: "Project", **kwargs) -> None:
        # noinspection PyProtectedMember
        src = self.get_real_source_dir(current_project, current_project._initial_source_dir)
        if not src.exists():
            current_project.fatal("Source repository for target", current_project.target, "does not exist.",
                                  fixit_hint="This project uses the sources from the " + self.source_project.target +
                                             "target so you will have to clone that first. Try running:\n\t`" +
                                             "cheribuild.py " + self.source_project.target +
                                             "--no-skip-update --skip-configure --skip-build --skip-install`")

    def get_real_source_dir(self, caller: SimpleProject, base_project_source_dir: typing.Optional[Path]) -> Path:
        if base_project_source_dir is not None:
            return base_project_source_dir
        return self.source_project.get_source_dir(caller, caller.config,
                                                  cross_target=self.repo_for_target) / self.subdirectory

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        if self.do_update:
            src_proj = self.source_project.get_instance(current_project, cross_target=self.repo_for_target)
            src_proj.update()
        else:
            current_project.info("Not updating", src_dir, "since it reuses the repository for ",
                                 self.source_project.target)


class ReuseOtherProjectDefaultTargetRepository(ReuseOtherProjectRepository):
    def __init__(self, source_project: "typing.Type[Project]", *, subdirectory=".", do_update=False):
        super().__init__(source_project, subdirectory=subdirectory, do_update=do_update,
                         repo_for_target=source_project.supported_architectures[0])


# Use git-worktree to handle per-target branches:
class TargetBranchInfo(object):
    def __init__(self, branch: str, directory_name: str, url: str = None):
        self.branch = branch
        self.directory_name = directory_name
        self.url = url


_PRETEND_RUN_GIT_COMMANDS = os.getenv("_TEST_SKIP_GIT_COMMANDS") is None


class GitRepository(SourceRepository):
    def __init__(self, url: str, *, old_urls: typing.List[bytes] = None, default_branch: str = None,
                 force_branch: bool = False, temporary_url_override: str = None,
                 url_override_reason: "typing.Any" = None,
                 per_target_branches: typing.Dict[CrossCompileTarget, TargetBranchInfo] = None,
                 old_branches: typing.Dict[str, str] = None):
        self.old_urls = old_urls
        if temporary_url_override is not None:
            self.url = temporary_url_override
            _ = url_override_reason  # silence unused argument warning
            if self.old_urls is None:
                self.old_urls = [url.encode("utf-8")]
            else:
                self.old_urls.append(url.encode("utf-8"))
        else:
            self.url = url
        self._default_branch = default_branch
        self.force_branch = force_branch
        if per_target_branches is None:
            per_target_branches = dict()
        self.per_target_branches = per_target_branches
        self.old_branches = old_branches

    def get_default_branch(self, current_project: "Project", *, include_per_target: bool) -> str:
        if include_per_target:
            target_override = self.per_target_branches.get(current_project.crosscompile_target, None)
            if target_override is not None:
                return target_override.branch
        return self._default_branch

    @staticmethod
    def get_current_branch(src_dir: Path) -> "typing.Optional[bytes]":
        status = run_command("git", "status", "-b", "-s", "--porcelain", "-u", "no",
                             capture_output=True, print_verbose_only=True, cwd=src_dir,
                             run_in_pretend_mode=_PRETEND_RUN_GIT_COMMANDS)
        if status.stdout.startswith(b"## "):
            return status.stdout[3:status.stdout.find(b"...")].strip()
        return None

    @staticmethod
    def contains_commit(current_project: "Project", commit: str, *, src_dir: Path, expected_branch="HEAD",
                        invalid_commit_ref_result: typing.Any = False):
        if current_project.config.pretend and not src_dir.exists():
            return False
        # Note: merge-base --is-ancestor exits with code 0/1, so we need to pass allow_unexpected_returncode
        is_ancestor = run_command("git", "merge-base", "--is-ancestor", commit, expected_branch, cwd=src_dir,
                                  print_verbose_only=True, capture_error=True, allow_unexpected_returncode=True,
                                  run_in_pretend_mode=_PRETEND_RUN_GIT_COMMANDS, raise_in_pretend_mode=True)
        if is_ancestor.returncode == 0:
            current_project.verbose_print(coloured(AnsiColour.blue, expected_branch, "contains commit", commit))
            return True
        elif is_ancestor.returncode == 1:
            current_project.verbose_print(
                coloured(AnsiColour.blue, expected_branch, "does not contains commit", commit))
            return False
        elif is_ancestor.returncode == 128 or (
                is_ancestor.stderr and (b"Not a valid commit name" in is_ancestor.stderr or  # e.g. not fetched yet
                                        b"no upstream configured" in is_ancestor.stderr)):  # @{u} without an upstream.
            # Strip the fatal: prefix from the error message for easier to understand debug output.
            error_message = remove_prefix(is_ancestor.stderr.decode("utf-8"), "fatal: ").strip()
            current_project.verbose_print(coloured(AnsiColour.blue, "Could not determine if ", expected_branch,
                                                   " contains ", commit, ":", sep=""),
                                          coloured(AnsiColour.yellow, error_message))
            return invalid_commit_ref_result
        else:
            current_project.warning("Unknown return code", is_ancestor)
            # some other error -> raise so that I can see what went wrong
            raise subprocess.CalledProcessError(is_ancestor.returncode, is_ancestor.args, output=is_ancestor.stdout,
                                                stderr=is_ancestor.stderr)

    def ensure_cloned(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: Path,
                      skip_submodules=False) -> None:
        if current_project.config.skip_clone:
            if not (src_dir / ".git").exists():
                current_project.fatal("Sources for", str(src_dir), " missing!")
            return
        if base_project_source_dir is None:
            base_project_source_dir = src_dir
        # git-worktree creates a .git file instead of a .git directory so we can't use .is_dir()
        if not (base_project_source_dir / ".git").exists():
            assert isinstance(self.url, str), self.url
            assert not self.url.startswith("<"), "Invalid URL " + self.url
            if current_project.config.confirm_clone and not current_project.query_yes_no(
                    str(base_project_source_dir) + " is not a git repository. Clone it from '" + self.url + "'?",
                    default_result=True):
                current_project.fatal("Sources for", str(base_project_source_dir), " missing!")
            clone_cmd = ["git", "clone"]
            if current_project.config.shallow_clone and not current_project.needs_full_history:
                # Note: we pass --no-single-branch since otherwise git fetch will not work with branches and
                # the solution of running  `git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"`
                # is not very intuitive. This increases the amount of data fetched but increases usability
                clone_cmd.extend(["--depth", "1", "--no-single-branch"])
            if not skip_submodules:
                clone_cmd.append("--recurse-submodules")
            clone_branch = self.get_default_branch(current_project, include_per_target=False)
            if self._default_branch:
                clone_cmd += ["--branch", clone_branch]
            current_project.run_cmd(clone_cmd + [self.url, base_project_source_dir], cwd="/")
            # Could also do this but it seems to fetch more data than --no-single-branch
            # if self.config.shallow_clone:
            #    current_project.run_cmd(["git", "config", "remote.origin.fetch",
            #                             "+refs/heads/*:refs/remotes/origin/*"], cwd=src_dir)

        if src_dir == base_project_source_dir:
            return  # Nothing else to do

        # Handle per-target overrides by adding a new git-worktree git-worktree
        target_override = self.per_target_branches.get(current_project.crosscompile_target, None)
        default_clone_branch = self.get_default_branch(current_project, include_per_target=False)
        assert target_override is not None, "Default src != base src -> must have a per-target override"
        assert target_override.branch != default_clone_branch, \
            "Cannot create worktree with same branch as base repo: {} vs {}".format(target_override.branch,
                                                                                    default_clone_branch)
        if (src_dir / ".git").exists():
            return
        current_project.info("Creating git-worktree checkout of", base_project_source_dir, "with branch",
                             target_override.branch, "for", src_dir)

        # Find the first valid remote
        per_target_url = target_override.url if target_override.url else self.url
        matching_remote = None
        remotes = run_command(["git", "-C", base_project_source_dir, "remote", "-v"],
                              capture_output=True).stdout.decode("utf-8")
        for r in remotes.splitlines():
            remote_name = r.split()[0].strip()
            if per_target_url in r:
                current_project.verbose_print("Found per-target URL", per_target_url)
                matching_remote = remote_name
                break  # Found the matching remote
            # Also check the raw config file entry in case insteadOf/pushInsteadOf rewrote the URL so it no longer works
            try:
                raw_url = run_command(
                    ["git", "-C", base_project_source_dir, "config", "remote." + remote_name + ".url"],
                    capture_output=True).stdout.decode("utf-8").strip()
                if raw_url == per_target_url:
                    matching_remote = remote_name
                    break
            except Exception as e:
                current_project.warning("Could not get URL for remote", remote_name, e)
                continue
        if matching_remote is None:
            current_project.warning("Could not find remote for URL", per_target_url, "will add a new one")
            new_remote = "remote-" + current_project.crosscompile_target.generic_arch_suffix
            run_command(["git", "-C", base_project_source_dir, "remote", "add", new_remote, per_target_url],
                        print_verbose_only=False)
            matching_remote = new_remote
        # Fetch from the remote to ensure that the target ref exists (otherwise git worktree add fails)
        run_command(["git", "-C", base_project_source_dir, "fetch", matching_remote], print_verbose_only=False)
        while True:
            try:
                url = run_command(["git", "-C", base_project_source_dir, "remote", "get-url", matching_remote],
                                  capture_output=True).stdout.decode("utf-8").strip()
            except subprocess.CalledProcessError as e:
                current_project.warning("Could not determine URL for remote", matching_remote, str(e))
                url = None
            if url == self.url:
                break
            current_project.info("URL '", url, "' for remote ", matching_remote, " does not match expected url '",
                                 self.url, "'", sep="")
            if current_project.query_yes_no("Use this remote?"):
                break
            matching_remote = input("Please enter the correct remote: ")
        # TODO --track -B?
        try:
            run_command(["git", "-C", base_project_source_dir, "worktree", "add", "--track", "-b",
                         target_override.branch, src_dir, matching_remote + "/" + target_override.branch],
                        print_verbose_only=False)
        except subprocess.CalledProcessError:
            current_project.warning("Could not create worktree with branch name ", target_override.branch,
                                    ", maybe it already exists. Trying fallback name.", sep="")
            run_command(["git", "-C", base_project_source_dir, "worktree", "add", "--track", "-b",
                         "worktree-fallback-" + target_override.branch, src_dir,
                         matching_remote + "/" + target_override.branch], print_verbose_only=False)

    def get_real_source_dir(self, caller: SimpleProject, base_project_source_dir: Path) -> Path:
        target_override = self.per_target_branches.get(caller.crosscompile_target, None)
        if target_override is None:
            return base_project_source_dir
        return base_project_source_dir.with_name(target_override.directory_name)

    def update(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: Path = None, revision=None,
               skip_submodules=False):
        self.ensure_cloned(current_project, src_dir=src_dir, base_project_source_dir=base_project_source_dir,
                           skip_submodules=skip_submodules)
        if current_project.skip_update:
            return
        if not src_dir.exists():
            return

        def get_remote_name():
            # Try to get the name of the default remote from the configured upstream branch
            try:
                revparse = run_command(["git", "-C", base_project_source_dir, "rev-parse", "--symbolic-full-name",
                                        "@{upstream}"], run_in_pretend_mode=True, capture_output=True,
                                       capture_error=True).stdout.decode("utf-8")
                if revparse.startswith("refs/remotes") and len(revparse.split("/")) > 3:
                    return revparse.split("/")[2]
                else:
                    current_project.warning("Could not parse git rev-parse output. ",
                                            "Output was", revparse, "-- will not attempt to update remote URLs.")
            except subprocess.CalledProcessError as e:
                if b"no upstream configured" in e.stderr:
                    return None
                else:
                    current_project.warning("git rev-parse failed, will not attempt to update remote URLs:", e)
            return None

        # handle repositories that have moved:
        if src_dir.exists() and self.old_urls:
            remote_name = get_remote_name()
            if remote_name is not None:
                remote_url = run_command("git", "remote", "get-url", remote_name, capture_output=True,
                                         cwd=src_dir).stdout.strip()
                # Strip any .git suffix to match more old URLs
                if remote_url.endswith(b".git"):
                    remote_url = remote_url[:-4]
                # Update from the old url:
                for old_url in self.old_urls:
                    assert isinstance(old_url, bytes)
                    if old_url.endswith(b".git"):
                        old_url = old_url[:-4]
                    if remote_url == old_url:
                        current_project.warning(current_project.target, "still points to old repository", remote_url)
                        if current_project.query_yes_no("Update to correct URL?"):
                            run_command("git", "remote", "set-url", remote_name, self.url,
                                        run_in_pretend_mode=_PRETEND_RUN_GIT_COMMANDS, cwd=src_dir)

        # First fetch all the current upstream branch to see if we need to autostash/pull.
        # Note: "git fetch" without other arguments will fetch from the currently configured upstream.
        # If there is no upstream, it will just return immediately.
        run_command(["git", "fetch"], cwd=src_dir)

        if revision is not None:
            # TODO: do some rev-parse stuff to check if we are on the right revision?
            run_command("git", "checkout", revision, cwd=src_dir, print_verbose_only=True)
            if not skip_submodules:
                run_command("git", "submodule", "update", "--init", "--recursive", cwd=src_dir, print_verbose_only=True)
            return

        # Handle forced branches now that we have fetched the latest changes
        if src_dir.exists() and (self.force_branch or self.old_branches):
            current_branch = self.get_current_branch(src_dir)
            if current_branch is not None:
                current_branch = current_branch.decode('utf-8')
            if current_branch is None:
                default_branch = None
            elif self.force_branch:
                assert self.old_branches is None, "cannot set both force_branch and old_branches"
                default_branch = self.get_default_branch(current_project, include_per_target=True)
                assert default_branch, "default_branch must be set if force_branch is true!"
            else:
                default_branch = self.old_branches.get(current_branch)
            if default_branch and current_branch != default_branch:
                current_project.warning("You are trying to build the", current_branch,
                                        "branch. You should be using", default_branch)
                if current_project.query_yes_no("Would you like to change to the " + default_branch + " branch?"):
                    try:
                        run_command("git", "checkout", default_branch, cwd=src_dir, capture_error=True)
                    except subprocess.CalledProcessError as e:
                        # If the branch doesn't exist and there are multiple upstreams with that branch, use --track
                        # to create a new branch that follows the upstream one
                        if e.stderr.strip().endswith(b") remote tracking branches"):
                            run_command("git", "checkout", "--track", f"{get_remote_name()}/{default_branch}",
                                        cwd=src_dir, capture_error=True)
                        else:
                            raise e

                else:
                    current_project.ask_for_confirmation("Are you sure you want to continue?", force_result=False,
                                                         error_message="Wrong branch: " + current_branch)

        # We don't need to update if the upstream commit is an ancestor of the current HEAD.
        # This check ensures that we avoid a rebase if the current branch is a few commits ahead of upstream.
        # When checking if we are up to date, we treat a missing @{upstream} reference (no upstream branch
        # configured) as success to avoid getting an error from git pull.
        up_to_date = self.contains_commit(current_project, "@{upstream}", src_dir=src_dir,
                                          invalid_commit_ref_result="invalid")
        if up_to_date is True:
            current_project.info("Skipping update: Current HEAD is up-to-date or ahead of upstream.")
            return
        elif up_to_date == "invalid":
            # Info message was already printed.
            current_project.info("Skipping update: no upstream configured to update from.")
            return
        assert up_to_date is False
        current_project.verbose_print(coloured(AnsiColour.blue, "Current HEAD is behind upstream."))

        # make sure we run git stash if we discover any local changes
        has_changes = len(run_command("git", "diff", "--stat", "--ignore-submodules",
                                      capture_output=True, cwd=src_dir, print_verbose_only=True).stdout) > 1
        pull_cmd = ["git", "pull"]
        has_autostash = False
        git_version = get_program_version(Path(shutil.which("git") or "git"), config=current_project.config)
        # Use the autostash flag for Git >= 2.14 (https://stackoverflow.com/a/30209750/894271)
        if git_version >= (2, 14):
            has_autostash = True
            pull_cmd.append("--autostash")

        if has_changes:
            print(coloured(AnsiColour.green, "Local changes detected in", src_dir))
            # TODO: add a config option to skip this query?
            if current_project.config.force_update:
                status_update("Updating", src_dir, "with autostash due to --force-update")
            elif not current_project.query_yes_no("Stash the changes, update and reapply?", default_result=True,
                                                  force_result=True):
                status_update("Skipping update of", src_dir)
                return
            if not has_autostash:
                # TODO: ask if we should continue?
                stash_result = run_command("git", "stash", "save", "Automatic stash by cheribuild.py",
                                           capture_output=True, cwd=src_dir, print_verbose_only=True).stdout
                # print("stash_result =", stash_result)
                if "No local changes to save" in stash_result.decode("utf-8"):
                    # print("NO REAL CHANGES")
                    has_changes = False  # probably git diff showed something from a submodule

        if not skip_submodules:
            pull_cmd.append("--recurse-submodules")
        rebase_flag = "--rebase=merges" if git_version >= (2, 18) else "--rebase=preserve"
        run_command(pull_cmd + [rebase_flag], cwd=src_dir, print_verbose_only=True)
        if not skip_submodules:
            run_command("git", "submodule", "update", "--init", "--recursive", cwd=src_dir, print_verbose_only=True)
        if has_changes and not has_autostash:
            run_command("git", "stash", "pop", cwd=src_dir, print_verbose_only=True)


class MercurialRepository(SourceRepository):
    def __init__(self, url: str, *, old_urls: typing.List[bytes] = None, default_branch: str = None,
                 force_branch: bool = False, temporary_url_override: str = None,
                 url_override_reason: "typing.Any" = None):
        self.old_urls = old_urls
        if temporary_url_override is not None:
            self.url = temporary_url_override
            _ = url_override_reason  # silence unused argument warning
            if self.old_urls is None:
                self.old_urls = [url.encode("utf-8")]
            else:
                self.old_urls.append(url.encode("utf-8"))
        else:
            self.url = url
        self.default_branch = default_branch
        self.force_branch = force_branch

    @staticmethod
    def run_hg(src_dir: "typing.Optional[Path]", *args, **kwargs):
        assert src_dir is None or isinstance(src_dir, Path)
        command = ["hg"]
        if src_dir:
            command += ["--cwd", str(src_dir)]
        command += ["--noninteractive"]
        # Ensure we get a non-interactive merge if doing something that
        # requires a merge, like update (e.g. on macOS it will default to
        # opening FileMerge.app... sigh).
        command += [
            "--config", "ui.merge=diff3",
            "--config", "merge-tools.diff3.args=$local $base $other -m > $output",
        ]
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            command += args[0]  # list with parameters was passed
        else:
            command += args
        return run_command(command, **kwargs)

    @staticmethod
    def contains_commit(current_project: "Project", commit: str, *, src_dir: Path, expected_branch="HEAD"):
        if current_project.config.pretend and not src_dir.exists():
            return False
        revset = "ancestor(" + commit + ",.) and id(" + commit + ")"
        log = MercurialRepository.run_hg(src_dir, "log", "--quiet", "--rev", revset,
                                         capture_output=True, print_verbose_only=True)
        if len(log.stdout) > 0:
            current_project.verbose_print(coloured(AnsiColour.blue, expected_branch, "contains commit", commit))
            return True
        else:
            current_project.verbose_print(
                coloured(AnsiColour.blue, expected_branch, "does not contain commit", commit))
            return False

    def ensure_cloned(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: Path,
                      skip_submodules=False) -> None:
        if current_project.config.skip_clone:
            if not (src_dir / ".hg").exists():
                current_project.fatal("Sources for", str(src_dir), " missing!")
            return
        if base_project_source_dir is None:
            base_project_source_dir = src_dir
        if not (base_project_source_dir / ".hg").exists():
            assert isinstance(self.url, str), self.url
            assert not self.url.startswith("<"), "Invalid URL " + self.url
            if current_project.config.confirm_clone and not current_project.query_yes_no(
                    str(base_project_source_dir) + " is not a mercurial repository. Clone it from '" + self.url + "'?",
                    default_result=True):
                current_project.fatal("Sources for", str(base_project_source_dir), " missing!")
            clone_cmd = ["clone"]
            if self.default_branch:
                clone_cmd += ["--branch", self.default_branch]
            self.run_hg(None, clone_cmd + [self.url, base_project_source_dir], cwd="/")
        assert src_dir == base_project_source_dir, "Worktrees only supported with git"

    def update(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: Path = None, revision=None,
               skip_submodules=False):
        self.ensure_cloned(current_project, src_dir=src_dir, base_project_source_dir=base_project_source_dir,
                           skip_submodules=skip_submodules)
        if current_project.skip_update:
            return
        if not src_dir.exists():
            return

        # handle repositories that have moved
        if src_dir.exists() and self.old_urls:
            remote_url = self.run_hg(src_dir, "paths", "default", capture_output=True).stdout.strip()
            # Update from the old url:
            for old_url in self.old_urls:
                assert isinstance(old_url, bytes)
                if remote_url == old_url:
                    current_project.warning(current_project.target, "still points to old repository", remote_url)
                    if current_project.query_yes_no("Update to correct URL?"):
                        current_project.fatal("Not currently implemented; please manually update .hg/hgrc")
                        return

        # First pull all the incoming changes to see if we need to update.
        # Note: hg pull is similar to git fetch
        self.run_hg(src_dir, "pull")

        if revision is not None:
            # TODO: do some identify stuff to check if we are on the right revision?
            self.run_hg(src_dir, "update", "--merge", revision, print_verbose_only=True)
            return

        # Handle forced branches now that we have fetched the latest changes
        if src_dir.exists() and self.force_branch:
            assert self.default_branch, "default_branch must be set if force_branch is true!"
            branch = self.run_hg(src_dir, "branch", capture_output=True, print_verbose_only=True)
            current_branch = branch.stdout.decode("utf-8")
            if current_branch != self.force_branch:
                current_project.warning("You are trying to build the", current_branch,
                                        "branch. You should be using", self.default_branch)
                if current_project.query_yes_no("Would you like to change to the " + self.default_branch + " branch?"):
                    self.run_hg(src_dir, "update", "--merge", self.default_branch)
                else:
                    current_project.ask_for_confirmation("Are you sure you want to continue?", force_result=False,
                                                         error_message="Wrong branch: " + current_branch)

        # We don't need to update if the tip is an ancestor of the current dirctory.
        up_to_date = self.contains_commit(current_project, "tip", src_dir=src_dir)
        if up_to_date is True:
            current_project.info("Skipping update: Current directory is up-to-date or ahead of tip.")
            return
        assert up_to_date is False
        current_project.verbose_print(coloured(AnsiColour.blue, "Current directory is behind tip."))

        # make sure we run git stash if we discover any local changes
        has_changes = len(self.run_hg(src_dir, "diff", "--stat",
                                      capture_output=True, print_verbose_only=True).stdout) > 1
        if has_changes:
            print(coloured(AnsiColour.green, "Local changes detected in", src_dir))
            # TODO: add a config option to skip this query?
            if current_project.config.force_update:
                status_update("Updating", src_dir, "with merge due to --force-update")
            elif not current_project.query_yes_no("Update and merge the changes?", default_result=True,
                                                  force_result=True):
                status_update("Skipping update of", src_dir)
                return
        self.run_hg(src_dir, "update", "--merge", print_verbose_only=True)


class SubversionRepository(SourceRepository):
    def __init__(self, url, *, default_branch: str = None):
        self.url = url
        self._default_branch = default_branch

    def ensure_cloned(self, current_project: "Project", src_dir: Path, **kwargs):
        if (src_dir / ".svn").is_dir():
            return

        if current_project.config.skip_clone:
            current_project.fatal("Sources for", str(src_dir), " missing!")
            return

        assert isinstance(self.url, str), self.url
        assert not self.url.startswith("<"), "Invalid URL " + self.url
        checkout_url = self.url
        if self._default_branch:
            checkout_url = checkout_url + '/' + self._default_branch
        if current_project.config.confirm_clone and not current_project.query_yes_no(
                str(src_dir) + " is not a subversion checkout. Checkout from '" + checkout_url + "'?",
                default_result=True):
            current_project.fatal("Sources for", str(src_dir), " missing!")
            return

        checkout_cmd = ["svn", "checkout"]
        current_project.run_cmd(checkout_cmd + [checkout_url, src_dir], cwd="/")

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        self.ensure_cloned(current_project, src_dir=src_dir)
        if current_project.skip_update:
            return
        if not src_dir.exists():
            return

        update_command = ["svn", "update"]
        run_command(update_command, cwd=src_dir)


class DefaultInstallDir(Enum):
    DO_NOT_INSTALL = "Should not be installed"
    IN_BUILD_DIRECTORY = "$BUILD_DIR/test-install-prefix"
    # Note: ROOTFS_LOCALBASE will be searched for libraries, ROOTFS_OPTBASE will not. The former should be used for
    # libraries that will be used by other programs, and the latter should be used for standalone programs (such as
    # PostgreSQL or WebKit).
    # Note: for ROOTFS_OPTBASE, the path_in_rootfs attribute can be used to override the default of /opt/...
    # This also works for ROOTFS_LOCALBASE
    ROOTFS_OPTBASE = "The rootfs for this target (<rootfs>/opt/<arch>/<program> by default)"
    ROOTFS_LOCALBASE = "The sysroot for this target (<rootfs>/usr/local/<arch> by default)"
    KDE_PREFIX = "The sysroot for this target (<rootfs>/opt/<arch>/kde by default)"
    COMPILER_RESOURCE_DIR = "The compiler resource directory"
    CHERI_SDK = "The CHERI SDK directory"
    MORELLO_SDK = "The Morello SDK directory"
    BOOTSTRAP_TOOLS = "The bootstap tools directory"
    CUSTOM_INSTALL_DIR = "Custom install directory"
    SYSROOT_FOR_BAREMETAL_ROOTFS_OTHERWISE = "Sysroot for baremetal projects, rootfs otherwise"


_INVALID_INSTALL_DIR = Path("/this/dir/should/be/overwritten/and/not/used/!!!!")
_DO_NOT_INSTALL_PATH = Path("/this/project/should/not/be/installed!!!!")


# noinspection PyProtectedMember
def _default_install_dir_handler(config: CheriConfig, project: "Project") -> Path:
    install_dir = project.get_default_install_dir_kind()
    if install_dir == DefaultInstallDir.DO_NOT_INSTALL:
        return _DO_NOT_INSTALL_PATH
    elif install_dir == DefaultInstallDir.IN_BUILD_DIRECTORY:
        return project.build_dir / "test-install-prefix"
    elif install_dir == DefaultInstallDir.ROOTFS_OPTBASE:
        assert not project.compiling_for_host(), "Should not use DefaultInstallDir.ROOTFS_OPTBASE for native builds!"
        if hasattr(project, "path_in_rootfs"):
            assert project.path_in_rootfs.startswith("/"), project.path_in_rootfs
            return project.rootfs_dir / project.path_in_rootfs[1:]
        return Path(
            project.rootfs_dir / "opt" / project.target_info.install_prefix_dirname / project._rootfs_install_dir_name)
    elif install_dir == DefaultInstallDir.KDE_PREFIX:
        if project.compiling_for_host():
            return config.output_root / "kde"
        else:
            return Path(project.rootfs_dir, "opt", project.target_info.install_prefix_dirname, "kde")
    elif install_dir == DefaultInstallDir.COMPILER_RESOURCE_DIR:
        compiler_for_resource_dir = project.CC
        # For the NATIVE variant we want to install to CHERI clang:
        if project.compiling_for_host():
            compiler_for_resource_dir = config.cheri_sdk_bindir / "clang"
        return get_compiler_info(compiler_for_resource_dir, config=config).get_resource_dir()
    elif install_dir == DefaultInstallDir.ROOTFS_LOCALBASE:
        if project.compiling_for_host():
            return config.output_root / "local"
        assert getattr(project, "path_in_rootfs", None) is None, \
            "path_in_rootfs only applies to ROOTFS_OPTBASE: " + str(project)
        return project.sdk_sysroot
    elif install_dir == DefaultInstallDir.CHERI_SDK:
        assert project.compiling_for_host(), "CHERI_SDK is only a valid install dir for native, " \
                                             "use ROOTFS_LOCALBASE/ROOTFS_OPTBASE for cross"
        return config.cheri_sdk_dir
    elif install_dir == DefaultInstallDir.MORELLO_SDK:
        assert project.compiling_for_host(), "MORELLO_SDK is only a valid install dir for native, " \
                                             "use ROOTFS_LOCALBASE/ROOTFS_OPTBASE for cross"
        return config.morello_sdk_dir
    elif install_dir == DefaultInstallDir.BOOTSTRAP_TOOLS:
        assert project.compiling_for_host(), "BOOTSTRAP_TOOLS is only a valid install dir for native, " \
                                             "use ROOTFS_LOCALBASE/ROOTS for cross"
        return config.other_tools_dir
    elif install_dir == DefaultInstallDir.CUSTOM_INSTALL_DIR:
        return _INVALID_INSTALL_DIR
    raise ValueError(f"Unknown install dir for {project.target}")


def _default_install_dir_str(project: "Project") -> str:
    install_dir = project.get_default_install_dir_kind()
    return str(install_dir.value)
    # fatal_error("Unknown install dir for", project.target)


def _default_source_dir(config: CheriConfig, project: "Project", subdir: Path = Path()) -> "typing.Optional[Path]":
    if project.repository is not None and isinstance(project.repository, ReuseOtherProjectRepository):
        # For projects that reuse other source directories, we return None to use the default for the source project.
        return None
    if project.default_directory_basename:
        return Path(config.source_root / subdir / project.default_directory_basename)
    return Path(config.source_root / subdir / project.target)


def default_source_dir_in_subdir(subdir: Path) -> ComputedDefaultValue:
    """
    :param subdir: the subdirectory below the source root (e.g. qt5 or kde-frameworks)
    :return: A ComputedDefaultValue for projects that build in a subdirectory below the source root.
    """
    return ComputedDefaultValue(
        function=lambda config, project: _default_source_dir(config, project, subdir),
        as_string=lambda cls: f"$SOURCE_ROOT/{subdir}/{(cls.default_directory_basename or cls.target)}")


class Project(SimpleProject):
    repository = None  # type: SourceRepository
    # is_large_source_repository can be set to true to set some git config options to speed up operations:
    # Ideally this would be a flag in GitRepository, but that will not work with inheritance (since some
    # subclasses use different repositories and they would all have to set that flag again). Annoying for LLVM/FreeBSD
    is_large_source_repository = False
    git_revision = None
    needs_full_history = False  # Some projects need the full git history when cloning
    skip_git_submodules = False
    compile_db_requires_bear = True
    do_not_add_to_targets = True
    set_pkg_config_path = True  # set the PKG_CONFIG_* environment variables when building
    can_run_parallel_install = False  # Most projects don't work well with parallel installation
    default_source_dir = ComputedDefaultValue(
        function=_default_source_dir, as_string=lambda cls: "$SOURCE_ROOT/" + cls.default_directory_basename)
    needs_native_build_for_crosscompile = False  # Some projects (e.g. python) need a native build for build tools, etc.
    # Some projects build docbook xml files and in order to do so we need to set certain env vars to skip the
    # DTD validation with newer XML processing tools.
    builds_docbook_xml = False
    # Some projects have build flags to enable/disable test building. For some projects skipping them can result in a
    # significant build speedup as they should not be needed for most users.
    has_optional_tests = False
    default_build_tests = True  # whether to build tests by default
    show_optional_tests_in_help = True  # whether to show the --foo/build-tests in --help

    @classmethod
    def dependencies(cls, config: CheriConfig) -> "list[str]":
        if cls.needs_native_build_for_crosscompile and not cls.get_crosscompile_target(config).is_native():
            return [cls.get_class_for_target(BasicCompilationTargets.NATIVE).target]
        return []

    @classmethod
    def project_build_dir_help(cls):
        result = "$BUILD_ROOT/"
        if isinstance(cls.default_directory_basename, ComputedDefaultValue):
            result += cls.default_directory_basename.as_string
        else:
            result += cls.default_directory_basename
        if cls._xtarget is not BasicCompilationTargets.NATIVE or cls.add_build_dir_suffix_for_native:
            result += "-$TARGET"
        result += "-build"
        return result

    default_build_dir = ComputedDefaultValue(
        function=_default_build_dir, as_string=lambda cls: cls.project_build_dir_help())

    make_kind = MakeCommandKind.DefaultMake
    """
    The kind of too that is used for building and installing (defaults to using "make")
    Set this to MakeCommandKind.GnuMake if the build system needs GNU make features or BsdMake if it needs bmake
    """

    # A per-project config option to generate a CMakeLists.txt that just has a custom taget that calls cheribuild.py
    @property
    def generate_cmakelists(self):
        return self.config.generate_cmakelists

    # TODO: remove these three
    @classmethod
    def get_source_dir(cls, caller: "SimpleProject", config: CheriConfig = None,
                       cross_target: CrossCompileTarget = None):
        return cls.get_instance(caller, config, cross_target).source_dir

    @classmethod
    def get_build_dir(cls, caller: "AbstractProject", config: CheriConfig = None,
                      cross_target: CrossCompileTarget = None):
        return cls.get_instance(caller, config, cross_target).build_dir

    @classmethod
    def get_install_dir(cls, caller: "AbstractProject", config: CheriConfig = None,
                        cross_target: CrossCompileTarget = None):
        return cls.get_instance(caller, config, cross_target).real_install_root_dir

    def build_dir_for_target(self, target: CrossCompileTarget):
        return self.config.build_root / (
                    self.default_directory_basename + self.build_configuration_suffix(target) + "-build")

    default_use_asan = False

    @classproperty
    def can_build_with_asan(self):
        return self._xtarget is None or not self._xtarget.is_cheri_purecap()

    @classproperty
    def can_build_with_ccache(self):
        return False

    @classmethod
    def get_default_install_dir_kind(cls) -> DefaultInstallDir:
        if cls.default_install_dir is not None:
            install_dir = cls.default_install_dir
        else:
            if cls._xtarget is not None and cls._xtarget.is_native():
                install_dir = cls.native_install_dir
            else:
                install_dir = cls.cross_install_dir
        if install_dir is None and cls._default_install_dir_fn is Project._default_install_dir_fn:
            raise RuntimeError(
                "native_install_dir/cross_install_dir/_default_install_dir_fn not specified for " + cls.target)
        if install_dir == DefaultInstallDir.SYSROOT_FOR_BAREMETAL_ROOTFS_OTHERWISE:
            if cls._xtarget is not None and (
                    cls._xtarget.target_info_cls.is_baremetal() or cls._xtarget.target_info_cls.is_rtems()):
                install_dir = DefaultInstallDir.ROOTFS_LOCALBASE
            else:
                install_dir = DefaultInstallDir.ROOTFS_OPTBASE
        return install_dir

    default_install_dir = None  # type: typing.Optional[DefaultInstallDir]
    # To provoide different install locations when cross-compiling and when native
    native_install_dir = None  # type: typing.Optional[DefaultInstallDir]
    cross_install_dir = None  # type: typing.Optional[DefaultInstallDir]
    # For more precise control over the install dir it is possible to provide a callback function
    _default_install_dir_fn = ComputedDefaultValue(function=_default_install_dir_handler,
                                                   as_string=_default_install_dir_str)
    """ The default installation directory """
    @property
    def _rootfs_install_dir_name(self):
        return self.default_directory_basename

    # useful for cross compile projects that use a prefix and DESTDIR
    _install_prefix = None
    destdir = None

    __can_use_lld_map = dict()  # type: typing.Dict[str, bool]

    def can_use_lld(self, compiler: Path):
        command = [str(compiler)] + self.essential_compiler_and_linker_flags + ["-fuse-ld=lld", "-xc", "-o",
                                                                                "/dev/null", "-"]
        command_str = commandline_to_str(command)
        if command_str not in Project.__can_use_lld_map:
            try:
                run_command(command, run_in_pretend_mode=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, raise_in_pretend_mode=True,
                            input="int main() { return 0; }\n", print_verbose_only=True)
                status_update(compiler, "supports -fuse-ld=lld, linking should be much faster!")
                Project.__can_use_lld_map[command_str] = True
            except subprocess.CalledProcessError:
                status_update(compiler, "does not support -fuse-ld=lld, using slower bfd instead")
                Project.__can_use_lld_map[command_str] = False
        return Project.__can_use_lld_map[command_str]

    def can_run_binaries_on_remote_morello_board(self):
        morello_ssh_hostname = self.config.remote_morello_board
        return morello_ssh_hostname and self.target_info.is_cheribsd() and self.compiling_for_aarch64(
            include_purecap=True) and ssh_host_accessible(morello_ssh_hostname)

    def can_use_lto(self, ccinfo: CompilerInfo):
        if ccinfo.compiler == "apple-clang":
            return True
        elif ccinfo.compiler == "clang" and (
                not self.compiling_for_host() or (ccinfo.version >= (4, 0, 0) and self.can_use_lld(ccinfo.path))):
            return True
        else:
            return False

    def check_system_dependencies(self):
        # Check that the make command exists (this will also add it to the required system tools)
        if self.make_args.command is None:
            self.fatal("Make command not set!")
        super().check_system_dependencies()

    lto_by_default = False  # Don't default to LTO
    prefer_full_lto_over_thin_lto = False  # If LTO is enabled, use LLVM's ThinLTO by default
    lto_set_ld = True
    default_build_type = BuildType.DEFAULT
    default_auto_var_init = AutoVarInit.NONE

    @classmethod
    def setup_config_options(cls, install_directory_help="", **kwargs):
        super().setup_config_options(**kwargs)
        if cls.source_dir is None:
            cls._initial_source_dir = cls.add_path_option("source-directory", metavar="DIR",
                                                          default=cls.default_source_dir,
                                                          help="Override default source directory for " + cls.target)
        # --<target>-<suffix>/build-directory is not inherited from the unsuffixed target (unless there is only one
        # supported target).
        default_xtarget = cls.default_architecture
        if cls._xtarget is not None or default_xtarget is not None:
            cls.build_dir = cls.add_path_option("build-directory", metavar="DIR", default=cls.default_build_dir,
                                                help="Override default source directory for " + cls.target,
                                                use_default_fallback_config_names=cls._xtarget == default_xtarget)
        if cls.can_build_with_asan:
            asan_default = ComputedDefaultValue(
                function=lambda config, proj: False if proj.get_crosscompile_target(
                    config).is_cheri_purecap() else proj.default_use_asan,
                as_string=str(cls.default_use_asan))
            cls.use_asan = cls.add_bool_option("use-asan", default=asan_default,
                                               help="Build with AddressSanitizer enabled")
        else:
            cls.use_asan = False
        if cls.can_build_with_ccache:
            cls.use_ccache = cls.add_bool_option("use-ccache", default=False,
                                                 help="Build with CCache")
        else:
            cls.use_ccache = False
        cls.auto_var_init = cls.add_config_option("auto-var-init", kind=AutoVarInit,
                                                  default=ComputedDefaultValue(
                                                      lambda config, proj: proj.default_auto_var_init,
                                                      lambda c: (
                                                              "the value of the global --skip-update option ("
                                                              "defaults to \"" +
                                                              c.default_auto_var_init.value + "\")")),
                                                  help="Whether to initialize all local variables (currently only "
                                                       "supported when compiling with clang)")
        cls.skip_update = cls.add_bool_option("skip-update",
                                              default=ComputedDefaultValue(lambda config, proj: config.skip_update,
                                                                           "the value of the global --skip-update "
                                                                           "option"),
                                              help="Override --skip-update/--no-skip-update for this target only ")
        cls.force_configure = cls.add_bool_option("reconfigure", altname="force-configure",
                                                  default=ComputedDefaultValue(
                                                      lambda config, proj: config.force_configure,
                                                      "the value of the global --reconfigure/--force-configure option"),
                                                  help="Override --(no-)reconfigure/--(no-)force-configure for this "
                                                       "target only")

        if not install_directory_help:
            install_directory_help = "Override default install directory for " + cls.target
        cls._install_dir = cls.add_path_option("install-directory", metavar="DIR", help=install_directory_help,
                                               default=cls._default_install_dir_fn)
        if "repository" in dir(cls) and isinstance(cls.repository, GitRepository) and \
                "git_revision" not in cls.__dict__:
            cls.git_revision = cls.add_config_option("git-revision", metavar="REVISION",
                                                     help="The git revision to checkout prior to building. Useful if "
                                                          "HEAD is broken for one "
                                                          "project but you still want to update the other projects.")
            # TODO: can argparse action be used to store to the class member directly?
            # seems like I can create a new action a pass a reference to the repository:
            # class FooAction(argparse.Action):
            # def __init__(self, option_strings, dest, nargs=None, **kwargs):
            #     if nargs is not None:
            #         raise ValueError("nargs not allowed")
            #     super(FooAction, self).__init__(option_strings, dest, **kwargs)
            # def __call__(self, parser, namespace, values, option_string=None):
            #     print('%r %r %r' % (namespace, values, option_string))
            #     setattr(namespace, self.dest, values)
            cls._repository_url = cls.add_config_option("repository", kind=str, help="The URL of the git repository",
                                                        default=cls.repository.url, metavar="REPOSITORY")
        cls.use_lto = cls.add_bool_option("use-lto", help="Build with link-time optimization (LTO)",
                                          default=cls.lto_by_default)
        cls.use_cfi = False  # doesn't work yet
        cls._linkage = cls.add_config_option("linkage", default=Linkage.DEFAULT, kind=Linkage,
                                             help="Build static or dynamic (or use the project default)")

        cls.build_type = cls.add_config_option("build-type",
                                               help="Optimization+debuginfo defaults (supports the same values as "
                                                    "CMake (as well as 'DEFAULT' which"
                                                    " does not pass any additional flags to the configure command).",
                                               default=cls.default_build_type, kind=BuildType,
                                               enum_choice_strings=supported_build_type_strings)  # type: BuildType

        if cls.has_optional_tests and "build_tests" not in cls.__dict__:
            cls.build_tests = cls.add_bool_option("build-tests", help="Build the tests",
                                                  default=cls.default_build_tests,
                                                  show_help=cls.show_optional_tests_in_help)

    def linkage(self):
        if self.target_info.must_link_statically:
            return Linkage.STATIC
        if self._linkage == Linkage.DEFAULT:
            if self.compiling_for_host():
                return Linkage.DEFAULT  # whatever the project chooses as a default
            else:
                return self.config.crosscompile_linkage  # either force static or force dynamic
        return self._linkage

    @property
    def force_static_linkage(self) -> bool:
        return self.linkage() == Linkage.STATIC

    @property
    def force_dynamic_linkage(self) -> bool:
        return self.linkage() == Linkage.DYNAMIC

    _force_debug_info = None  # Override the debug info setting from --build-type

    @property
    def should_include_debug_info(self) -> bool:
        if self._force_debug_info is not None:
            return self._force_debug_info
        return self.build_type.should_include_debug_info

    def should_use_extra_c_compat_flags(self):
        # TODO: add a command-line option and default to true for
        return self.compiling_for_cheri() and self.target_info.is_baremetal()

    @property
    def extra_c_compat_flags(self):
        if not self.compiling_for_cheri():
            return []
        # Build with virtual address interpretation, data-dependent provenance and pcrelative captable ABI
        return ["-cheri-uintcap=addr", "-Xclang", "-cheri-data-dependent-provenance"]

    @property
    def optimization_flags(self):
        return self._build_type_basic_compiler_flags

    @property
    def _build_type_basic_compiler_flags(self):
        # Not needed for CMakeProjects since those already add flags based on build type
        cbt = self.build_type
        if cbt == BuildType.DEFAULT:
            return []
        elif cbt == BuildType.DEBUG:
            # TODO: once clang's -Og is useful: if self.get_compiler_info(self.CC).supports_Og_flag:
            if self.get_compiler_info(self.CC).compiler == "gcc":
                return ["-Og"]
            return ["-O0"]
        elif cbt in (BuildType.RELEASE, BuildType.RELWITHDEBINFO):
            return ["-O2"]
        elif cbt in (BuildType.MINSIZEREL, BuildType.MINSIZERELWITHDEBINFO):
            return ["-Os"]

    needs_mxcaptable_static = False  # E.g. for postgres which is just over the limit:
    needs_mxcaptable_dynamic = False  # This might be true for Qt/QtWebkit

    @property
    def compiler_warning_flags(self) -> "list[str]":
        if self.compiling_for_host():
            return self.common_warning_flags + self.host_warning_flags
        else:
            return self.common_warning_flags + self.cross_warning_flags

    @property
    def default_compiler_flags(self) -> "list[str]":
        assert self._setup_called
        result = []
        if self.use_lto:
            result.extend(self._lto_compiler_flags)
        if self.use_cfi:
            if not self.use_lto:
                self.fatal("Cannot use CFI without LTO!")
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
            result.append("-fvisibility=hidden")
        if self.compiling_for_host():
            return result + self.COMMON_FLAGS + self.compiler_warning_flags + self.optimization_flags
        result += self.essential_compiler_and_linker_flags + self.optimization_flags
        result += self.COMMON_FLAGS + self.compiler_warning_flags
        if self.config.csetbounds_stats:
            result.extend(["-mllvm", "-collect-csetbounds-output=" + str(self.csetbounds_stats_file),
                           "-mllvm", "-collect-csetbounds-stats=csv",
                           # "-Xclang", "-cheri-bounds=everywhere-unsafe"])
                           ])
        # Add mxcaptable for projects that need it
        if self.compiling_for_mips(include_purecap=True):
            if self.crosscompile_target.is_cheri_purecap():
                if self.force_static_linkage and self.needs_mxcaptable_static:
                    result.append("-mxcaptable")
                if self.force_dynamic_linkage and self.needs_mxcaptable_dynamic:
                    result.append("-mxcaptable")
            # Do the same for MIPS to get even performance comparisons
            else:
                if self.force_static_linkage and self.needs_mxcaptable_static:
                    result.extend(["-mxgot", "-mllvm", "-mxmxgot"])
                if self.force_dynamic_linkage and self.needs_mxcaptable_dynamic:
                    result.extend(["-mxgot", "-mllvm", "-mxmxgot"])
        return result

    @property
    def default_ldflags(self) -> "list[str]":
        result = list(self.COMMON_LDFLAGS)
        if self.use_lto:
            result.extend(self._lto_linker_flags)
        if self.force_static_linkage:
            result.append("-static")
        if self.use_cfi:
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
        if self.compiling_for_host():
            return result

        # Should work fine without linker emulation (the linker should infer it from input files)
        # if self.compiling_for_cheri():
        #     emulation = "elf64btsmip_cheri_fbsd" if not self.target_info.is_baremetal() else "elf64btsmip_cheri"
        # elif self.compiling_for_mips(include_purecap=False):
        #     emulation = "elf64btsmip_fbsd" if not self.target_info.is_baremetal() else "elf64btsmip"
        # result.append("-Wl,-m" + emulation)
        result += self.essential_compiler_and_linker_flags
        ccinfo = self.get_compiler_info(self.CC)
        result.extend(ccinfo.linker_override_flags(self.target_info.linker))
        if self.should_include_debug_info and ".bfd" not in self.target_info.linker.name:
            # Add a gdb_index to massively speed up running GDB on CHERIBSD:
            result.append("-Wl,--gdb-index")
            # Also reduce the size of debug info to make copying files over faster
            result.append("-Wl,--compress-debug-sections=zlib")
        if self.target_info.is_cheribsd() and self.config.with_libstatcounters:
            # We need to include the constructor even if there is no reference to libstatcounters:
            # TODO: always include the .a file?
            result += ["-Wl,--whole-archive", "-lstatcounters", "-Wl,--no-whole-archive"]
        return result

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # set up the install/build/source directories (allowing overrides from config file)
        assert isinstance(self.repository, SourceRepository), self.target + " repository member is wrong!"
        if hasattr(self, "_repository_url") and isinstance(self.repository, GitRepository):
            # TODO: remove this and use a custom argparse.Action subclass
            self.repository.url = self._repository_url

        if isinstance(self.default_directory_basename, ComputedDefaultValue):
            self.default_directory_basename = self.default_directory_basename(config, self)
        if isinstance(self.repository, ReuseOtherProjectRepository):
            initial_source_dir = inspect.getattr_static(self, "_initial_source_dir")
            assert isinstance(initial_source_dir, ConfigOptionBase)
            # noinspection PyProtectedMember
            assert initial_source_dir._get_default_value(config, self) is None, \
                "initial source dir != None for ReuseOtherProjectRepository"
        if self.source_dir is None:
            self.source_dir = self.repository.get_real_source_dir(self, self._initial_source_dir)
        else:
            if isinstance(self.source_dir, ComputedDefaultValue):
                self.source_dir = self.source_dir(config, self)
            self._initial_source_dir = self.source_dir

        if self.build_in_source_dir:
            assert not self.build_via_symlink_farm, "Using a symlink farm only makes sense with a separate build dir"
            self.verbose_print("Cannot build", self.target, "in a separate build dir, will build in", self.source_dir)
            self.build_dir = self.source_dir

        self.configure_command = None
        # non-assignable variables:
        self.configure_args = []  # type: typing.List[str]
        self.configure_environment = {}  # type: typing.Dict[str,str]
        self._last_stdout_line_can_be_overwritten = False
        self.make_args = MakeOptions(self.make_kind, self)
        self._compiledb_tool = None  # type: typing.Optional[str]
        if self.config.create_compilation_db and self.compile_db_requires_bear:
            # CompileDB seems to generate broken compile_commands,json
            if self.make_args.is_gnu_make and False:
                # use compiledb instead of bear for gnu make
                # https://blog.jetbrains.com/clion/2018/08/working-with-makefiles-in-clion-using-compilation-db/
                self.add_required_system_tool("compiledb", install_instructions="Run `pip install --user compiledb``")
                self._compiledb_tool = "compiledb"
            else:
                self.add_required_system_tool("bear", homebrew="bear", cheribuild_target="bear")
                self._compiledb_tool = "bear"
        self._force_clean = False
        self._prevent_assign = True

        # Setup destdir and installprefix:
        if not self.compiling_for_host():
            install_dir_kind = self.get_default_install_dir_kind()
            # Install to SDK if CHERIBSD_ROOTFS is the install dir but we are not building for CheriBSD
            if install_dir_kind == DefaultInstallDir.ROOTFS_LOCALBASE:
                if self.target_info.is_baremetal():
                    self.destdir = typing.cast(Path, self.sdk_sysroot.parent)
                    self._install_prefix = Path("/", self.target_info.target_triple)
                elif self.target_info.is_rtems():
                    self.destdir = self.sdk_sysroot.parent
                    self._install_prefix = Path("/", self.target_info.target_triple)
                else:
                    self._install_prefix = Path("/", self.target_info.sysroot_install_prefix_relative)
                    self.destdir = self._install_dir
            elif install_dir_kind in (DefaultInstallDir.ROOTFS_OPTBASE, DefaultInstallDir.KDE_PREFIX):
                relative_to_rootfs = os.path.relpath(str(self._install_dir), str(self.rootfs_dir))
                if relative_to_rootfs.startswith(os.path.pardir):
                    self.verbose_print("Custom install dir", self._install_dir, "-> using / as install prefix")
                    self._install_prefix = Path("/")
                    self.destdir = self._install_dir
                else:
                    self._install_prefix = Path("/", relative_to_rootfs)
                    self.destdir = self.rootfs_dir
            elif install_dir_kind in (None, DefaultInstallDir.DO_NOT_INSTALL, DefaultInstallDir.COMPILER_RESOURCE_DIR,
                                      DefaultInstallDir.IN_BUILD_DIRECTORY, DefaultInstallDir.CUSTOM_INSTALL_DIR):
                self._install_prefix = self._install_dir
                self.destdir = None
            else:
                assert self._install_prefix and self.destdir is not None, "both must be set!"

        # convert the tuples into mutable lists (this is needed to avoid modifying class variables)
        # See https://github.com/CTSRD-CHERI/cheribuild/issues/33
        # FIXME: this should move to target_info
        self.cross_warning_flags = ["-Werror=implicit-function-declaration",
                                    "-Werror=format", "-Werror=incompatible-pointer-types"]
        self.host_warning_flags = []
        self.common_warning_flags = []
        target_arch = self.crosscompile_target
        # compiler flags:
        self.COMMON_FLAGS = self.target_info.default_initial_compile_flags()
        if target_arch.is_cheri_purecap([CPUArchitecture.MIPS64]) and self.force_static_linkage:
            # clang currently gets the TLS model wrong:
            # https://github.com/CTSRD-CHERI/cheribsd/commit/f863a7defd1bdc797712096b6778940cfa30d901
            self.COMMON_FLAGS.append("-ftls-model=initial-exec")
            # TODO: remove the data-dependent provenance flag:
            if self.should_use_extra_c_compat_flags():
                self.COMMON_FLAGS.extend(self.extra_c_compat_flags)  # include cap-table-abi flags

        assert self.install_dir, "must be set"
        self.verbose_print(self.target, "INSTALLDIR = ", self._install_dir, "INSTALL_PREFIX=", self._install_prefix,
                           "DESTDIR=", self.destdir)

        if self.should_include_debug_info:
            if not self.target_info.is_macos():
                self.COMMON_FLAGS.append("-ggdb")
                if not self.compiling_for_mips(include_purecap=True):
                    # compressed debug info is broken on big endian until
                    # we depend on a lld version with the fix.
                    self.COMMON_FLAGS.append("-gz")
        self.CFLAGS = []  # type: typing.List[str]
        self.CXXFLAGS = []  # type: typing.List[str]
        self.ASMFLAGS = []  # type: typing.List[str]
        self.LDFLAGS = self.target_info.required_link_flags()
        self.COMMON_LDFLAGS = []  # type: typing.List[str]
        # Don't build CHERI with ASAN since that doesn't work or make much sense
        if self.use_asan and not self.compiling_for_cheri():
            self.COMMON_FLAGS.append("-fsanitize=address")
            self.COMMON_LDFLAGS.append("-fsanitize=address")
        if self.crosscompile_target.is_libcompat_target():
            self.COMMON_LDFLAGS.append("-L" + str(self.sdk_sysroot / "usr" / self.target_info.default_libdir))

        self._lto_linker_flags = []  # type: typing.List[str]
        self._lto_compiler_flags = []  # type: typing.List[str]

    @cached_property
    def dependency_install_prefixes(self) -> "list[Path]":
        # TODO: if this is too slow we could look at the direct dependencies only
        deps = self.cached_full_dependencies()
        all_install_dirs = dict()  # Use a dict to ensure reproducible order (guaranteed since Python 3.6)
        for d in deps:
            if d.xtarget is not self.crosscompile_target:
                continue  # Don't add pkg-config directories for targets with a different architecture
            project = d.get_or_create_project(None, self.config)
            install_dir = project.install_dir
            if install_dir is not None:
                all_install_dirs[install_dir] = 1
        try:
            # Don't add the rootfs directory, since e.g. target_info.pkgconfig_candidates(<rootfs>) will not return the
            # correct values. For the root directory we rely on the methods in target_info instead.
            all_install_dirs.pop(self.rootfs_dir, None)
        except NotImplementedError:
            pass  # If there isn't a rootfs, there is no need to skip that project.
        return list(all_install_dirs.keys())

    @property
    def pkgconfig_dirs(self) -> "list[str]":
        dependency_pkgconfig_dirs = self.target_info.pkgconfig_dirs
        for d in self.dependency_install_prefixes:
            dependency_pkgconfig_dirs.extend(self.target_info.pkgconfig_candidates(d))
        return remove_duplicates(dependency_pkgconfig_dirs)

    @property
    def host_dependency_prefixes(self) -> "list[Path]":
        """:return: a list of prefixes for native dependencies (only for cross-compilation)"""
        assert not self.compiling_for_host()
        result = dict()  # Use a dict to ensure reproducible order (guaranteed since Python 3.6)
        if self.needs_native_build_for_crosscompile:
            result[self.get_install_dir(self, self.config, cross_target=BasicCompilationTargets.NATIVE)] = True
        for d in self.cached_full_dependencies():
            if d.xtarget.is_native() and not d.project_class.is_toolchain_target():
                result[d.get_or_create_project(d.xtarget, self.config).install_dir] = True
        result[self.config.other_tools_dir] = True
        return list(result.keys())

    __cached_native_pkg_config_libdir = None

    @classmethod
    def _native_pkg_config_libdir(cls):
        if cls.__cached_native_pkg_config_libdir is not None:
            return cls.__cached_native_pkg_config_libdir
        if OSInfo.is_cheribsd() and shutil.which("pkg-config") == "/usr/local64/bin/pkg-config":
            # When building natively on CheriBSD with pkg-config installed using pkg64, the default pkg-config
            # search path will use the non-CHERI libraries in /usr/local64.
            cls.__cached_native_pkg_config_libdir = "/usr/local/libdata/pkgconfig:/usr/libdata/pkgconfig"
        else:
            cls.__cached_native_pkg_config_libdir = ""
        return cls.__cached_native_pkg_config_libdir

    def setup(self):
        super().setup()
        if self.set_pkg_config_path:
            pkg_config_args = dict()
            if self.compiling_for_host():
                # We have to add the boostrap tools pkgconfig directory to PKG_CONFIG_PATH so that it is searched in
                # addition to the default paths. Note: We do not set PKG_CONFIG_LIBDIR since that overrides the default.
                pkg_config_args = dict(
                    PKG_CONFIG_PATH=":".join(self.pkgconfig_dirs + [os.getenv("PKG_CONFIG_PATH", "")]))
                if self._native_pkg_config_libdir():
                    pkg_config_args["PKG_CONFIG_LIBDIR"] = self._native_pkg_config_libdir()
            elif self.needs_sysroot:
                # We need to set the PKG_CONFIG variables both when configuring and when running make since some
                # projects (e.g. GDB) run the configure scripts lazily during the make all stage. If we don't set
                # them*, these configure steps will find the libraries on the host instead and cause the build to fail.
                # PKG_CONFIG_PATH: list of directories to be searched for .pc files before the default locations.
                # PKG_CONFIG_LIBDIR: list of directories to replace the default pkg-config search path.
                # Since we only want libraries from our sysroots we set both.
                pkgconfig_dirs = ":".join(self.pkgconfig_dirs)
                pkg_config_args = dict(
                    PKG_CONFIG_PATH=pkgconfig_dirs,
                    PKG_CONFIG_LIBDIR=pkgconfig_dirs,
                    PKG_CONFIG_SYSROOT_DIR=str(self.target_info.sysroot_dir)
                )
            if pkg_config_args:
                self.configure_environment.update(pkg_config_args)
                self.make_args.set_env(**pkg_config_args)
        cc_info = self.get_compiler_info(self.CC)
        if self.use_lto and self.CC.exists():
            self.add_lto_build_options(cc_info)

        if self.crosscompile_target.is_hybrid_or_purecap_cheri():
            self.cross_warning_flags += ["-Werror=cheri-capability-misuse", "-Werror=cheri-bitwise-operations"]
            # The morello compiler still uses the old flag name
            supports_new_flag = cc_info.supports_warning_flag("-Werror=cheri-prototypes")
            self.cross_warning_flags.append("-Werror=cheri-prototypes" if supports_new_flag else
                                            "-Werror=mips-cheri-prototypes")
            # Make underaligned capability loads/stores an error and require an explicit cast:
            self.cross_warning_flags.append("-Werror=pass-failed")
        if self.CC.exists() and cc_info.is_clang:
            self.cross_warning_flags += ["-Werror=undefined-internal"]

        # We might be setting too many flags, ignore this (for now)
        if not self.compiling_for_host() and self.CC.exists() and self.get_compiler_info(self.CC).is_clang:
            self.COMMON_FLAGS.append("-Wno-error=unused-command-line-argument")
        if self.builds_docbook_xml and OSInfo.IS_MAC:
            catalog = self.get_homebrew_prefix() / "etc/xml/catalog"
            if not catalog.exists():
                self.dependency_error(OSInfo.install_instructions("docbook-xsl", False, homebrew="docbook-xsl"))
            # Without XML_CATALOG_FILES we get the following error: "I/O error : Attempt to load network entity"
            self.configure_environment["XML_CATALOG_FILES"] = str(catalog)
            self.make_args.set_env(XML_CATALOG_FILES=catalog)

    def set_lto_binutils(self, ar, ranlib, nm, ld):
        self.fatal("Building", self.target, "with LTO is not supported (yet).")
        # raise NotImplementedError()

    def add_lto_build_options(self, ccinfo: CompilerInfo) -> bool:
        compiler = ccinfo.path
        if not self.can_use_lto(ccinfo):
            return False
        self.info("Trying to build with LTO enabled")
        if ccinfo.compiler == "clang":
            # For non apple-clang compilers we need to use llvm binutils:
            version_suffix = ""
            if compiler.name.startswith("clang"):
                version_suffix = compiler.name[len("clang"):]
            llvm_ar = ccinfo.get_matching_binutil("llvm-ar")
            llvm_ranlib = ccinfo.get_matching_binutil("llvm-ranlib")
            llvm_nm = ccinfo.get_matching_binutil("llvm-nm")
            lld = ccinfo.get_matching_binutil("ld.lld")
            # Find lld with the correct version (it must match the version of clang otherwise it breaks!)
            self._lto_linker_flags.extend(ccinfo.linker_override_flags(lld, linker_type="lld"))
            if not llvm_ar or not llvm_ranlib or not llvm_nm:
                self.warning("Could not find llvm-{ar,ranlib,nm}" + version_suffix,
                             "-> disabling LTO (resulting binary will be a bit slower)")
                return False
            ld = lld if self.lto_set_ld else None
            self.set_lto_binutils(ar=llvm_ar, ranlib=llvm_ranlib, nm=llvm_nm, ld=ld)
        if self.prefer_full_lto_over_thin_lto:
            self._lto_compiler_flags.append("-flto")
            self._lto_linker_flags.append("-flto")
        else:
            self._lto_compiler_flags.append("-flto=thin")
            self._lto_linker_flags.append("-flto=thin")
            if self.can_use_lld(ccinfo.path):
                thinlto_cache_flag = "--thinlto-cache-dir="
            else:
                # Apple ld uses a different flag for the thinlto cache dir
                assert ccinfo.compiler == "apple-clang"
                thinlto_cache_flag = "-cache_path_lto,"
            self._lto_linker_flags.append("-Wl," + thinlto_cache_flag + str(self.build_dir / "thinlto-cache"))
        if self.compiling_for_cheri_hybrid([CPUArchitecture.AARCH64]):
            # Hybrid flags are not inferred from the input files, so we have to explicitly pass -mattr= to ld.lld.
            self._lto_linker_flags.extend(["-Wl,-mllvm,-mattr=+morello"])
        self.info("Building with LTO")
        return True

    @cached_property
    def rootfs_dir(self):
        return self.target_info.get_rootfs_project(t=Project).install_dir

    @property
    def _no_overwrite_allowed(self) -> "typing.Iterable[str]":
        return super()._no_overwrite_allowed + ("configure_args", "configure_environment", "make_args")

    # Make sure that API is used properly
    def __setattr__(self, name, value):
        # if self.__dict__.get("_locked") and name == "x":
        #     raise AttributeError, "MyClass does not allow assignment to .x member"
        # self.__dict__[name] = value
        if self.__dict__.get("_prevent_assign"):
            # assert name not in ("source_dir", "build_dir", "install_dir")
            assert name != "install_dir", "install_dir should not be modified, only _install_dir or _install_prefix"
            assert name != "install_prefix", "install_prefix should not be modified, only _install_dir or " \
                                             "_install_prefix"
            if name in self._no_overwrite_allowed:
                import traceback
                traceback.print_stack()
                raise RuntimeError(self.__class__.__name__ + "." + name + " mustn't be set. Called from" +
                                   self.__class__.__name__)
        self.__dict__[name] = value

    def _get_make_commandline(self, make_target: "typing.Optional[typing.Union[str, typing.List[str]]]", make_command,
                              options: MakeOptions, parallel: bool = True, compilation_db_name: str = None):
        assert options is not None
        assert make_command is not None
        options = options.copy()
        if compilation_db_name is not None and self.config.create_compilation_db and self.compile_db_requires_bear:
            assert self._compiledb_tool is not None
            compdb_extra_args = []
            if self._compiledb_tool == "bear":
                compdb_extra_args = ["--output", self.build_dir / compilation_db_name, "--append", "--", make_command]
            elif self._compiledb_tool == "compiledb":
                compdb_extra_args = ["--output", self.build_dir / compilation_db_name, "make", "--cmd", make_command]
            else:
                self.fatal("Invalid tool")
            tool_path = shutil.which(self._compiledb_tool)
            if not tool_path:
                self.dependency_error(
                    "Cannot find '" + self._compiledb_tool + "' which is needed to create a compilation DB")
                tool_path = self._compiledb_tool
            options.set_command(tool_path, can_pass_j_flag=options.can_pass_jflag, early_args=compdb_extra_args)
            # Ensure that recursive make invocations reuse the compilation DB tool
            options.set(MAKE=commandline_to_str([options.command] + compdb_extra_args))
            make_command = options.command

        all_args = [make_command] + options.get_commandline_args(
            targets=[make_target] if isinstance(make_target, str) and make_target else make_target,
            jobs=self.config.make_jobs if parallel else None,
            verbose=self.config.verbose, continue_on_error=self.config.pass_dash_k_to_make
        )
        if not self.config.make_without_nice:
            all_args = ["nice"] + all_args
        return all_args

    def get_make_commandline(self, make_target: "typing.Union[str, typing.List[str]]", make_command: str = None,
                             options: MakeOptions = None, parallel: bool = True,
                             compilation_db_name: str = None) -> list:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = self.make_args.command
        return self._get_make_commandline(make_target, make_command, options, parallel, compilation_db_name)

    def run_make(self, make_target: "typing.Optional[typing.Union[str, typing.List[str]]]" = None, *,
                 make_command: str = None, options: MakeOptions = None, logfile_name: str = None, cwd: Path = None,
                 append_to_logfile=False, compilation_db_name="compile_commands.json", parallel: bool = True,
                 stdout_filter: "typing.Optional[typing.Callable[[bytes], None]]" = _default_stdout_filter) -> None:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = options.command
        all_args = self._get_make_commandline(make_target, make_command, options, parallel=parallel,
                                              compilation_db_name=compilation_db_name)
        if not cwd:
            cwd = self.build_dir
        if not logfile_name:
            logfile_name = Path(make_command).name
            if make_target:
                logfile_name += "." + (make_target if isinstance(make_target, str) else "_".join(make_target))

        starttime = time.time()
        if not self.config.write_logfile and stdout_filter == _default_stdout_filter:
            # if output isatty() (i.e. no logfile) ninja already filters the output -> don't slow this down by
            # adding a redundant filter in python
            if make_command == "ninja" and make_target != "install":
                stdout_filter = None
        if stdout_filter is _default_stdout_filter:
            stdout_filter = self._stdout_filter
        env = options.env_vars
        self.run_with_logfile(all_args, logfile_name=logfile_name, stdout_filter=stdout_filter, cwd=cwd, env=env,
                              append_to_logfile=append_to_logfile)
        # if we create a compilation db, copy it to the source dir:
        if self.config.copy_compilation_db_to_source_dir and (self.build_dir / compilation_db_name).exists():
            self.install_file(self.build_dir / compilation_db_name, self.source_dir / compilation_db_name, force=True)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", make_command, make_target, "took", time.time() - starttime, "seconds")

    def update(self):
        if not self.repository and not self.skip_update:
            self.fatal("Cannot update", self.target, "as it is missing a repository source",
                       fatal_when_pretending=True)
        self.repository.update(self, src_dir=self.source_dir, base_project_source_dir=self._initial_source_dir,
                               revision=self.git_revision, skip_submodules=self.skip_git_submodules)
        if self.is_large_source_repository and (self.source_dir / ".git").exists():
            # This is a large repository, tell git to do whatever it can to speed up operations (new in 2.24):
            # https://git-scm.com/docs/git-config#Documentation/git-config.txt-featuremanyFiles
            self.run_cmd("git", "config", "--local", "feature.manyFiles", "true", cwd=self.source_dir,
                         print_verbose_only=True)

    _extra_git_clean_excludes = []

    def _git_clean_source_dir(self, git_dir: Path = None):
        if git_dir is None:
            git_dir = self.source_dir
        # just use git clean for cleanup
        self.warning(self.target, "does not support out-of-source builds, using git clean to remove build artifacts.")
        git_clean_cmd = ["git", "clean", "-dfx", "--exclude=.*"] + self._extra_git_clean_excludes
        # Try to keep project files for IDEs and other dotfiles:
        self.run_cmd(git_clean_cmd, cwd=git_dir)

    def clean(self) -> ThreadJoiner:
        assert self.with_clean or self._force_clean
        # TODO: never use the source dir as a build dir (unfortunately mibench and elftoolchain won't work)
        # will have to check how well binutils and qemu work there
        if (self.build_dir / ".git").is_dir():
            if (
                    self.build_dir / "GNUmakefile").is_file() and self.make_kind != MakeCommandKind.BsdMake and \
                    self.target != "elftoolchain":
                run_command(self.make_args.command, "distclean", cwd=self.build_dir)
            else:
                assert self.source_dir == self.build_dir
                self._git_clean_source_dir()
        elif self.build_dir == self.source_dir:
            self.fatal("Cannot clean non-git source directories. Please override")
        else:
            return self.async_clean_directory(self.build_dir, keep_root=True)
        return ThreadJoiner(None)

    def needs_configure(self) -> bool:
        """
        :return: Whether the configure command needs to be run (by default assume yes)
        """
        return True

    def should_run_configure(self):
        if self.force_configure or self.config.configure_only:
            return True
        if self.with_clean:
            return True
        return self.needs_configure()

    def add_configure_env_arg(self, arg: str, value: "typing.Union[str,Path]"):
        if value is None:
            return
        assert not isinstance(value, list), ("Wrong type:", type(value))
        assert not isinstance(value, tuple), ("Wrong type:", type(value))
        self.configure_environment[arg] = str(value)

    def set_configure_prog_with_args(self, prog: str, path: Path, args: list):
        fullpath = str(path)
        if args:
            fullpath += " " + self.commandline_to_str(args)
        self.configure_environment[prog] = fullpath

    def configure(self, cwd: Path = None, configure_path: Path = None):
        if cwd is None:
            cwd = self.build_dir
        if not self.should_run_configure():
            return

        if self.build_via_symlink_farm:
            banned_dirs = {".hg", ".git", ".svn"}
            for root, dirnames, filenames in os.walk(self.source_dir):
                dirnames[:] = [d for d in dirnames if d not in banned_dirs]
                root = Path(root)
                relroot = root.relative_to(self.source_dir)
                for dirname in dirnames:
                    self.makedirs(self.build_dir / relroot / dirname)
                self.create_symlinks(map(lambda x: root / x, filenames), self.build_dir / relroot)

        if configure_path is None:
            configure_path = self.configure_command
        if configure_path is None:
            self.verbose_print("No configure command specified, skippping configure step.")
        else:
            assert configure_path, "configure_command should not be empty!"
            if not Path(configure_path).exists():
                self.fatal("Configure command ", configure_path, "does not exist!")
            self.run_with_logfile([str(configure_path)] + self.configure_args, logfile_name="configure", cwd=cwd,
                                  env=self.configure_environment)

    def compile(self, cwd: Path = None, parallel: bool = True):
        if cwd is None:
            cwd = self.build_dir
        self.run_make("all", cwd=cwd, parallel=parallel)

    @property
    def make_install_env(self):
        if self.destdir:
            env = self.make_args.env_vars.copy()
            if "DESTDIR" not in env:
                env["DESTDIR"] = str(self.destdir)
            return env
        return self.make_args.env_vars

    @property
    def real_install_root_dir(self):
        """
        :return: the real install root directory (e.g. if prefix == /usr/local and destdir == /tmp/benchdir it will
         return /tmp/benchdir/usr/local
        """
        if self.destdir is not None:
            assert self._install_prefix
            return self.destdir / Path(self._install_prefix).relative_to(Path("/"))
        return self._install_dir

    @property
    def install_dir(self):
        return self.real_install_root_dir

    @property
    def install_prefix(self) -> Path:
        if self._install_prefix is not None:
            return self._install_prefix
        return self._install_dir

    def run_make_install(self, *, options: MakeOptions = None, _stdout_filter=_default_stdout_filter, cwd=None,
                         parallel: bool = None, target: "typing.Union[str, typing.List[str]]" = "install",
                         make_install_env=None, **kwargs):
        if parallel is None:
            parallel = self.can_run_parallel_install
        if options is None:
            options = self.make_args.copy()
        else:
            options = options.copy()
        if make_install_env is None:
            make_install_env = self.make_install_env
        options.env_vars.update(make_install_env)
        self.run_make(make_target=target, options=options, stdout_filter=_stdout_filter, cwd=cwd,
                      parallel=parallel, **kwargs)

    def install(self, _stdout_filter=_default_stdout_filter):
        self.run_make_install(_stdout_filter=_stdout_filter)
        if self.compiling_for_cheri() and not (self.real_install_root_dir / "lib64c").exists():
            self.create_symlink(self.real_install_root_dir / "lib", self.real_install_root_dir / "lib64c")

    def _do_generate_cmakelists(self):
        assert not isinstance(self, CMakeProject), self
        cmakelists = """
# Do not edit!
# Generated by cheribuild.py
#
cmake_minimum_required(VERSION 3.8)
project({project} LANGUAGES NONE)
set(CLEAR_MAKEENV env -u MAKEFLAGS -u MAKELEVEL -u MAKE -u MAKE_TERMERR -u MAKE_TERMOUT -u MFLAGS)
add_custom_target(cheribuild ALL VERBATIM USES_TERMINAL COMMAND {command} --skip-update --skip-install {target})
add_custom_target(cheribuild-j1 VERBATIM USES_TERMINAL COMMAND {command} --skip-update -j1 {target})
add_custom_target(cheribuild-verbose VERBATIM USES_TERMINAL COMMAND {command} --skip-update -v {target})
add_custom_target(cheribuild-verbose-j1 VERBATIM USES_TERMINAL COMMAND {command} --skip-update -v -j1 {target})

add_custom_target(cheribuild-with-install VERBATIM USES_TERMINAL COMMAND {command} --skip-update {target})
add_custom_target(cheribuild-full VERBATIM USES_TERMINAL COMMAND {command} {target})
""".format(command="${CLEAR_MAKEENV} " + sys.argv[0], project=self.target, target=self.target)
        target_file = self.source_dir / "CMakeLists.txt"
        create = True
        if target_file.exists():
            existing_code = self.read_file(target_file)
            if existing_code == cmakelists:
                create = False
            elif "Generated by cheribuild.py" not in existing_code:
                print("A different CMakeLists.txt already exists. Contents:\n",
                      coloured(AnsiColour.green, existing_code), end="")
                if not self.query_yes_no("Overwrite?", force_result=False):
                    create = False
        if create:
            self.write_file(target_file, cmakelists, overwrite=True)

    @property
    def csetbounds_stats_file(self) -> Path:
        return self.build_dir / "csetbounds-stats.csv"

    def strip_elf_files(self, benchmark_dir):
        """
        Strip all ELF binaries to reduce the size of the benchmark directory
        :param benchmark_dir: The directory containing multiple ELF binaries
        """
        self.info("Stripping all ELF files in", benchmark_dir)
        self.run_cmd("du", "-sh", benchmark_dir)
        for root, dirnames, filenames in os.walk(str(benchmark_dir)):
            for filename in filenames:
                file = Path(root, filename)
                if file.suffix == ".dump":
                    # TODO: make this an error since we should have deleted them
                    self.warning("Will copy a .dump file to the FPGA:", file)
                # Try to reduce the amount of copied data
                self.maybe_strip_elf_file(file)
        self.run_cmd("du", "-sh", benchmark_dir)

    # @cached_property is important to only compute it once since we encode seconds in the file name:
    @cached_property
    def default_statcounters_csv_name(self) -> str:
        assert isinstance(self, Project)
        suffix = self.build_configuration_suffix()
        if self.config.benchmark_statcounters_suffix:
            user_suffix = self.config.benchmark_statcounters_suffix
            if not user_suffix.startswith("-"):
                user_suffix = "-" + user_suffix
            suffix += user_suffix
        else:
            # If we explicitly override the linkage model, encode it in the statcounters file
            if self.force_static_linkage:
                suffix += "-static"
            elif self.force_dynamic_linkage:
                suffix += "-dynamic"
            if self.config.benchmark_lazy_binding:
                suffix += "-lazybinding"
        return self.target + "-statcounters{}-{}.csv".format(
            suffix, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))

    def copy_asan_dependencies(self, dest_libdir):
        # ASAN depends on libraries that are not included in the benchmark image by default:
        assert self.compiling_for_mips(include_purecap=False) and self.use_asan
        self.info("Adding ASAN library dependencies to", dest_libdir)
        self.makedirs(dest_libdir)
        for lib in ("usr/lib/librt.so.1", "usr/lib/libexecinfo.so.1", "lib/libgcc_s.so.1", "lib/libelf.so.2"):
            self.install_file(self.sdk_sysroot / lib, dest_libdir / Path(lib).name, force=True,
                              print_verbose_only=False)

    _check_install_dir_conflict = True

    def _last_build_kind_path(self):
        return Path(self.build_dir, ".cheribuild_last_build_kind")

    def _last_clean_counter_path(self):
        return Path(self.build_dir, ".cheribuild_last_clean_counter")

    def _parse_require_clean_build_counter(self) -> typing.Optional[int]:
        require_clean_path = Path(self.source_dir, ".require_clean_build")
        if not require_clean_path.exists():
            return None
        with require_clean_path.open("r") as f:
            latest_counter = None  # type: typing.Optional[int]
            for i, line in enumerate(f.readlines()):
                # Remove comments
                while "#" in line:
                    line = line[:line.index('#')]
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = int(line)
                    if latest_counter is not None and parsed < latest_counter:
                        self.warning(require_clean_path, ":", i + 1, ": parsed counter ", parsed,
                                     " is smaller than previous one: ", latest_counter, sep="")
                    else:
                        latest_counter = parsed
                except ValueError as e:
                    self.warning(require_clean_path, ":", i + 1, ": could not parse line (", line, "): ", e, sep="")
                    continue
            if latest_counter is None:
                self.warning("Could not find latest counter in", require_clean_path)
            return latest_counter

    def prepare_install_dir_for_archiving(self):
        """Perform cleanup to reduce the size of the tarball that jenkins creates"""
        self.info("No project-specific cleanup for", self.target)

    def process(self):
        if self.generate_cmakelists:
            self._do_generate_cmakelists()
        if self.config.verbose:
            print(self.target, "directories: source=%s, build=%s, install=%s" %
                  (self.source_dir, self.build_dir, self.install_dir))

        if self.use_asan and self.compiling_for_mips(include_purecap=False):
            # copy the ASAN lib into the right directory:
            resource_dir = self.get_compiler_info(self.CC).get_resource_dir()
            status_update("Copying ASAN libs to", resource_dir)
            expected_path = resource_dir / "lib/freebsd/"
            asan_libdir_candidates = list((self.sdk_sysroot / "usr/lib/clang").glob("*"))
            versions = [a.name for a in asan_libdir_candidates]
            # Find the newest ASAN runtime library versions from the FreeBSD sysroot
            found_asan_lib = None
            from distutils.version import StrictVersion
            libname = "libclang_rt.asan-mips64.a"
            for version in reversed(sorted(versions, key=StrictVersion)):
                asan_libs = self.sdk_sysroot / "usr/lib/clang" / version / "lib/freebsd"
                if (asan_libs / libname).exists():
                    found_asan_lib = asan_libs / libname
                    break
            if not found_asan_lib:
                self.fatal("Cannot find", libname, "library in sysroot dirs", asan_libdir_candidates,
                           "-- Compilation will fail!")
                found_asan_lib = Path("/some/invalid/path/to/lib")
            self.makedirs(expected_path)
            run_command("cp", "-av", found_asan_lib.parent, expected_path.parent)
            # For some reason they are 644 so we can't overwrite for the next build unless we chmod first
            run_command("chmod", "-R", "u+w", expected_path.parent)
            if not (expected_path / libname).exists():
                self.fatal("Cannot find", libname, "library in compiler dir", expected_path,
                           "-- Compilation will fail!")
        install_dir_kind = self.get_default_install_dir_kind()
        if install_dir_kind != DefaultInstallDir.DO_NOT_INSTALL and self._check_install_dir_conflict:
            xtarget = self._xtarget  # type: CrossCompileTarget
            # If the conflicting target is also in supported_architectures, check for conficts:
            if xtarget.check_conflict_with is not None and xtarget.check_conflict_with in self.supported_architectures:
                # Check that we are not installing to the same directory as MIPS to avoid conflicts
                base = getattr(self, "synthetic_base", None)
                assert base is not None
                assert issubclass(base, SimpleProject)
                other_instance = base.get_instance_for_cross_target(xtarget.check_conflict_with, self.config,
                                                                    caller=self)
                if self.config.verbose:
                    self.info(self.target, "install dir for", xtarget.name, "is", self.install_dir)
                    other_xtarget = other_instance.get_crosscompile_target(self.config)
                    self.info(self.target, "install dir for", other_xtarget.name, "is", self.install_dir)
                assert other_instance.install_dir != self.install_dir, \
                    other_instance.target + " reuses the same install prefix! This will cause conflicts: " + str(
                        other_instance.install_dir)

        if self.skip_update:
            # When --skip-update is set (or we don't have working internet) only check that the repository exists
            if self.repository:
                self.repository.ensure_cloned(self, src_dir=self.source_dir,
                                              base_project_source_dir=self._initial_source_dir,
                                              skip_submodules=self.skip_git_submodules)
        else:
            self.update()
        if not self._system_deps_checked:
            self.check_system_dependencies()
        assert self._system_deps_checked, "self._system_deps_checked must be set by now!"

        last_build_file = self._last_build_kind_path()
        if self.build_in_source_dir and not self.with_clean:
            if not last_build_file.exists():
                self._force_clean = True  # could be an old build prior to adding this check
            else:
                last_build_kind = self.read_file(last_build_file)
                if last_build_kind != self.build_configuration_suffix():
                    if not self.query_yes_no("Last build was for configuration" + last_build_kind +
                                             " but currently building" + self.build_configuration_suffix() +
                                             ". Will clean before build. Continue?", force_result=True,
                                             default_result=True):
                        self.fatal("Cannot continue")
                        return
                    self._force_clean = True

        required_clean_counter = self._parse_require_clean_build_counter()
        clean_counter_in_build_dir = None  # type: typing.Optional[int]
        last_clean_counter_path = self._last_clean_counter_path()
        if required_clean_counter is not None:
            # Check if the last clean build had a smaller counter than the current required on and if so perform a clean
            # build and increment the value in the build directory.
            if not last_clean_counter_path.is_file():
                self.verbose_print("Forcing full rebuild since clean counter", last_clean_counter_path,
                                   "does not exist yet")
                self._force_clean = True
            else:
                try:
                    clean_counter_in_build_dir = int(last_clean_counter_path.read_text().strip())
                    if clean_counter_in_build_dir < required_clean_counter:
                        self.info("Forcing full rebuild since clean counter in build dir (", clean_counter_in_build_dir,
                                  ") is less than required minimum ", required_clean_counter, sep="")
                        self._force_clean = True
                    else:
                        self.verbose_print("Not forcing clean build since clean counter in build dir",
                                           clean_counter_in_build_dir, "is >= required minimum", required_clean_counter)
                except Exception as e:
                    self.warning("Could not parse", last_clean_counter_path, "-> assuming clean build is required.", e)
                    self._force_clean = True

        # run the rm -rf <build dir> in the background
        cleaning_task = self.clean() if (self._force_clean or self.with_clean) else ThreadJoiner(None)
        if cleaning_task is None:
            cleaning_task = ThreadJoiner(None)
        assert isinstance(cleaning_task, ThreadJoiner), ""
        with cleaning_task:
            if not self.build_dir.is_dir():
                self.makedirs(self.build_dir)

            # Clean has been performed -> write the last clean counter now (if needed).
            if required_clean_counter is not None and clean_counter_in_build_dir != required_clean_counter:
                self.write_file(last_clean_counter_path, str(required_clean_counter), overwrite=True)
            # Update the last build kind file if we are building in the source dir;
            if self.build_in_source_dir:
                self.write_file(last_build_file, self.build_configuration_suffix(), overwrite=True)
            # Clean completed

            # Configure step
            if not self.config.skip_configure or self.config.configure_only:
                if self.should_run_configure():
                    status_update("Configuring", self.display_name, "... ")
                    self.configure()
            if self.config.configure_only:
                return

            # Build step
            if not self.config.skip_build:
                if self.config.csetbounds_stats and (self.csetbounds_stats_file.exists() or self.config.pretend):
                    self.move_file(self.csetbounds_stats_file,
                                   self.csetbounds_stats_file.with_suffix(".from-configure.csv"),
                                   force=True)
                    # move any csetbounds stats from configuration (since they are not useful)
                status_update("Building", self.display_name, "... ")
                self.compile()

            # Install step
            if not self.config.skip_install:
                status_update("Installing", self.display_name, "... ")
                if install_dir_kind == DefaultInstallDir.DO_NOT_INSTALL:
                    self.info("Not installing", self.target, "since install dir is set to DO_NOT_INSTALL")
                else:
                    self.install()
                if is_jenkins_build():
                    self.prepare_install_dir_for_archiving()


# Shared between meson and CMake
class _CMakeAndMesonSharedLogic(Project):
    do_not_add_to_targets = True
    tests_need_full_disk_image = False
    _minimum_cmake_or_meson_version = None  # type: Tuple[int, int, int]
    _configure_tool_name = None  # type: str
    _configure_tool_cheribuild_target = None
    _toolchain_template = None  # type: str
    _toolchain_file = None  # type: Path

    class CommandLineArgs:
        """Simple wrapper to distinguish CMake (space-separated string) from Meson (python-style list)"""

        def __init__(self, args: list):
            self.args = args

        def __str__(self):
            return str(self.args)

    class EnvVarPathList:
        """Simple wrapper to distinguish CMake (:-separated string) from Meson (python-style list)"""

        def __init__(self, paths: list):
            self.paths = paths

        def __str__(self):
            return str(self.paths)

    def _toolchain_file_list_to_str(self, value: list) -> str:
        raise NotImplementedError()

    def _toolchain_file_command_args_to_str(self, value: CommandLineArgs) -> str:
        return self._toolchain_file_list_to_str(value.args)

    def _toolchain_file_env_var_path_list_to_str(self, value: EnvVarPathList) -> str:
        return self._toolchain_file_list_to_str(value.paths)

    def _bool_to_str(self, value: bool) -> str:
        raise NotImplementedError()

    def _replace_value(self, template: str, required: bool, key: str, value: str) -> str:
        if isinstance(value, bool):
            strval = self._bool_to_str(value)
        elif isinstance(value, _CMakeAndMesonSharedLogic.CommandLineArgs):
            # The CMake toolchain file generated by Meson uses a CMake list for compiler args, but that results in
            # CMake calling `clang -target;foo;--sysroot=...". We have to use a space-separated list instead, so we
            # also expand @{KEY}_STR@ (but don't make it an error if it doesn't exist in the toolchain file).
            # Feature request: https://github.com/mesonbuild/meson/issues/8534
            template = self._replace_value(template, required=False,
                                           key=key + '_STR', value=commandline_to_str(value.args))
            strval = self._toolchain_file_command_args_to_str(value)
        elif isinstance(value, _CMakeAndMesonSharedLogic.EnvVarPathList):
            strval = self._toolchain_file_env_var_path_list_to_str(value)
        elif isinstance(value, list):
            strval = self._toolchain_file_list_to_str(value)
        else:
            if not isinstance(value, (str, Path, int)):
                self.fatal(f"Unexpected value type {type(value)} for {key}: {value}", fatal_when_pretending=True)
            strval = str(value)
        result = template.replace("@" + key + "@", strval)
        if required and result == template:
            raise ValueError(key + " not used in toolchain file")
        return result

    @property
    def cmake_prefix_paths(self):
        return remove_duplicates(self.target_info.cmake_prefix_paths(self.config) + self.dependency_install_prefixes)

    def _replace_values_in_toolchain_file(self, template: str, file: Path, **kwargs):
        result = template
        for key, value in kwargs.items():
            if value is None:
                continue
            result = self._replace_value(result, required=True, key=key, value=value)
        # work around jenkins paths that might contain @[0-9]+ in the path:
        configured_jenkins_workaround = re.sub(r"@\d+", "", result)
        at_index = configured_jenkins_workaround.find("@")
        if at_index != -1:
            self.fatal("Did not replace all keys:", configured_jenkins_workaround[at_index:],
                       fatal_when_pretending=True)
        self.write_file(contents=result, file=file, overwrite=True)

    def _prepare_toolchain_file_common(self, output_file: Path = None, **kwargs):
        if output_file is None:
            output_file = self._toolchain_file
        assert self._toolchain_template is not None
        # XXX: We currently use CHERI LLVM tools for native builds
        sdk_bindir = self.sdk_bindir if not self.compiling_for_host() else self.config.cheri_sdk_bindir
        cmdline = _CMakeAndMesonSharedLogic.CommandLineArgs
        system_name = self.target_info.cmake_system_name if not self.compiling_for_host() else sys.platform
        if isinstance(self, MesonProject):
            # Meson expects lower-case system names:
            # https://mesonbuild.com/Reference-tables.html#operating-system-names
            system_name = system_name.lower()
        self._replace_values_in_toolchain_file(
            self._toolchain_template, output_file,
            TOOLCHAIN_SDK_BINDIR=sdk_bindir,
            TOOLCHAIN_COMPILER_BINDIR=self.CC.parent,
            TOOLCHAIN_TARGET_TRIPLE=self.target_info.target_triple,
            TOOLCHAIN_COMMON_FLAGS=cmdline(self.default_compiler_flags),
            TOOLCHAIN_C_FLAGS=cmdline(self.CFLAGS),
            TOOLCHAIN_LINKER_FLAGS=cmdline(self.default_ldflags + self.LDFLAGS),
            TOOLCHAIN_CXX_FLAGS=cmdline(self.CXXFLAGS),
            TOOLCHAIN_ASM_FLAGS=cmdline(self.ASMFLAGS),
            TOOLCHAIN_C_COMPILER=self.CC,
            TOOLCHAIN_CXX_COMPILER=self.CXX,
            TOOLCHAIN_AR=self.target_info.ar,
            TOOLCHAIN_RANLIB=self.target_info.ranlib,
            TOOLCHAIN_NM=self.target_info.nm,
            TOOLCHAIN_STRIP=self.target_info.strip_tool,
            TOOLCHAIN_SYSROOT=self.sdk_sysroot if self.needs_sysroot else "",
            TOOLCHAIN_SYSTEM_PROCESSOR=self.target_info.cmake_processor_id,
            TOOLCHAIN_SYSTEM_NAME=system_name,
            TOOLCHAIN_SYSTEM_VERSION=self.target_info.toolchain_system_version or "",
            TOOLCHAIN_CMAKE_PREFIX_PATH=self.cmake_prefix_paths,
            TOOLCHAIN_PKGCONFIG_DIRS=_CMakeAndMesonSharedLogic.EnvVarPathList(self.pkgconfig_dirs),
            COMMENT_IF_NATIVE="#" if self.compiling_for_host() else "",
            **kwargs)

    def _add_configure_options(self, *, _include_empty_vars=False, _replace=True, _implicitly_convert_lists=False,
                               _config_file_options: list, **kwargs):
        for option, value in kwargs.items():
            if not _replace and any(x.startswith("-D" + option + "=") for x in self.configure_args):
                self.verbose_print("Not replacing ", option, "since it is already set.")
                return
            if any(x.startswith("-D" + option) for x in _config_file_options):
                self.info("Not using default value of '", value, "' for configure option '", option,
                          "' since it is explicitly overwritten in the configuration", sep="")
                continue
            if isinstance(value, bool):
                value = self._bool_to_str(value)
            if (not str(value) or not value) and not _include_empty_vars:
                continue
            assert _implicitly_convert_lists or not isinstance(value, list), \
                "Lists must be converted to strings explicitly: " + str(value)
            assert value is not None
            self.configure_args.append("-D" + option + "=" + str(value))

    def _get_configure_tool_version(self) -> "typing.Tuple[int, int, int]":
        cmd = Path(self.configure_command)
        assert self.configure_command is not None
        if not cmd.is_absolute() or not Path(self.configure_command).exists():
            self.fatal("Could not find", self._configure_tool_name, "binary:", self.configure_command)
            return 0, 0, 0
        assert cmd.is_absolute()
        return get_program_version(cmd, config=self.config, **self._get_version_args)

    @property
    def _get_version_args(self) -> dict:
        raise NotImplementedError()

    def _configure_tool_install_instructions(self) -> InstallInstructions:
        raise NotImplementedError()

    def check_system_dependencies(self):
        assert self.configure_command is not None
        if not Path(self.configure_command).is_absolute():
            abspath = shutil.which(self.configure_command)
            if abspath:
                self.configure_command = abspath
        super().check_system_dependencies()
        if self._minimum_cmake_or_meson_version:
            version_components = self._get_configure_tool_version()
            # noinspection PyTypeChecker
            if version_components < self._minimum_cmake_or_meson_version:
                version_str = ".".join(map(str, version_components))
                expected_str = ".".join(map(str, self._minimum_cmake_or_meson_version))
                tool = self._configure_tool_name
                install_instrs = self._configure_tool_install_instructions()
                self.dependency_error(tool, "version", version_str, "is too old (need at least", expected_str + ")",
                                      install_instructions=install_instrs,
                                      cheribuild_target=install_instrs.cheribuild_target)


class CMakeProject(_CMakeAndMesonSharedLogic):
    """
    Like Project but automatically sets up the defaults for CMake projects
    Sets configure command to CMake, adds -DCMAKE_INSTALL_PREFIX=installdir
    and checks that CMake is installed
    """
    do_not_add_to_targets = True
    compile_db_requires_bear = False  # cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON does it
    generate_cmakelists = False  # There is already a CMakeLists.txt
    make_kind = MakeCommandKind.CMake
    _default_cmake_generator_arg = "-GNinja"  # We default to using the Ninja generator since it's faster
    _configure_tool_name = "CMake"
    default_build_type = BuildType.RELWITHDEBINFO
    # Some projects (e.g. LLVM) don't store the CMakeLists.txt in the project root directory.
    root_cmakelists_subdirectory = None  # type: Path
    ctest_script_extra_args = tuple()  # type: typing.Iterable[str]
    # 3.13.4 is the minimum version for LLVM and that also allows us to use "cmake --build -j <N>" unconditionally.
    _minimum_cmake_or_meson_version = (3, 13, 4)

    def _toolchain_file_list_to_str(self, value: list) -> str:
        assert isinstance(value, list), f"Expected a list and not {type(value)}: {value}"
        return ";".join(map(str, value))

    def _toolchain_file_command_args_to_str(self, value: _CMakeAndMesonSharedLogic.CommandLineArgs) -> str:
        return commandline_to_str(value.args)

    def _toolchain_file_env_var_path_list_to_str(self, value: _CMakeAndMesonSharedLogic.EnvVarPathList) -> str:
        # We store the raw ':'-separated list in the CMake toolchain file since it's also set using set(ENV{FOO} ...)
        return ":".join(map(str, value.paths))

    def _bool_to_str(self, value: bool) -> str:
        return "TRUE" if value else "FALSE"

    def _configure_tool_install_instructions(self) -> InstallInstructions:
        return OSInfo.install_instructions("cmake", False, default="cmake", cheribuild_target="cmake")

    @property
    def _get_version_args(self) -> dict:
        return dict(program_name=b"cmake")

    @property
    def _build_type_basic_compiler_flags(self):
        # No need to add any flags here, cmake does it for us already
        return []

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.cmake_options = cls.add_config_option("cmake-options", default=[], kind=list, metavar="OPTIONS",
                                                  help="Additional command line options to pass to CMake")

    def __init__(self, config):
        super().__init__(config)
        self.configure_command = os.getenv("CMAKE_COMMAND", None)
        if self.configure_command is None:
            self.configure_command = "cmake"
            self.add_required_system_tool("cmake", homebrew="cmake", zypper="cmake", apt="cmake", freebsd="cmake")
        # allow a -G flag in cmake-options to override the default generator (Ninja).
        custom_generator = next((x for x in self.cmake_options if x.startswith("-G")), None)
        generator = custom_generator if custom_generator else self._default_cmake_generator_arg
        self.ctest_environment = dict()  # type: dict[str, str]
        self.configure_args.append(generator)
        self.build_type_var_suffix = ""
        if "Ninja" in generator:
            self.make_args.subkind = MakeCommandKind.Ninja
            self.add_required_system_tool("ninja", homebrew="ninja", apt="ninja-build")
        elif "Makefiles" in generator:
            self.make_args.subkind = MakeCommandKind.DefaultMake
            self.add_required_system_tool("make")
        else:
            self.make_args.subkind = MakeCommandKind.CustomMakeTool  # VS/XCode, etc.

    def setup(self):
        super().setup()
        # CMake 3.13+ supports explicit source+build dir arguments
        cmakelists_dir = self.source_dir
        if self.root_cmakelists_subdirectory is not None:
            assert not self.root_cmakelists_subdirectory.is_absolute()
            cmakelists_dir = self.source_dir / self.root_cmakelists_subdirectory
        if self._get_configure_tool_version() >= (3, 13):
            self.configure_args.extend(["-S", str(cmakelists_dir), "-B", str(self.build_dir)])
        else:
            self.configure_args.append(str(cmakelists_dir))
        if self.build_type != BuildType.DEFAULT:
            if self.build_type == BuildType.MINSIZERELWITHDEBINFO:
                # no CMake equivalent for MinSizeRelWithDebInfo -> set minsizerel and force debug info
                self._force_debug_info = True
                self.add_cmake_options(CMAKE_BUILD_TYPE=BuildType.MINSIZEREL.value)
                self.build_type_var_suffix = "_" + BuildType.MINSIZEREL.value.upper()
            else:
                self.add_cmake_options(CMAKE_BUILD_TYPE=self.build_type.value)
                self.build_type_var_suffix = "_" + self.build_type.value.upper()
        if self.config.create_compilation_db:
            # TODO: always generate it?
            self.configure_args.append("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
        if self.compiling_for_host():
            # When building natively, pass arguments on the command line instead of using the toolchain file.
            # This makes it a lot easier to reproduce the builds outside of cheribuild.
            self.add_cmake_options(CMAKE_PREFIX_PATH=self._toolchain_file_list_to_str(self.cmake_prefix_paths))
        else:
            self._toolchain_template = include_local_file("files/CrossToolchain.cmake.in")
            self._toolchain_file = self.build_dir / "CrossToolchain.cmake"
            self.add_cmake_options(CMAKE_TOOLCHAIN_FILE=self._toolchain_file)
        # Don't add the user provided options here, add them in configure() so that they are put last

    def add_cmake_options(self, *, _include_empty_vars=False, _replace=True, **kwargs):
        return self._add_configure_options(_config_file_options=self.cmake_options, _replace=_replace,
                                           _include_empty_vars=_include_empty_vars, **kwargs)

    def set_minimum_cmake_version(self, major: int, minor: int, patch: int = 0):
        new_version = (major, minor, patch)
        assert self._minimum_cmake_or_meson_version is None or new_version >= self._minimum_cmake_or_meson_version
        self._minimum_cmake_or_meson_version = new_version

    def _cmake_install_stdout_filter(self, line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        self._show_line_stdout_filter(line)

    def set_lto_binutils(self, ar, ranlib, nm, ld):
        # LD is never invoked directly, so the -fuse-ld=/--ld-path flag is sufficient
        self.add_cmake_options(CMAKE_AR=ar, CMAKE_RANLIB=ranlib)

    def needs_configure(self) -> bool:
        if self.config.pretend and (self.force_configure or self.with_clean):
            return True
        # CMake is smart enough to detect when it must be reconfigured -> skip configure if cache exists
        cmake_cache = self.build_dir / "CMakeCache.txt"
        assert self.make_args.kind == MakeCommandKind.CMake
        build_file = "build.ninja" if self.make_args.subkind == MakeCommandKind.Ninja else "Makefile"
        return not cmake_cache.exists() or not (self.build_dir / build_file).exists()

    def generate_cmake_toolchain_file(self, file: Path):
        # CMAKE_CROSSCOMPILING will be set when we change CMAKE_SYSTEM_NAME:
        # This means we may not need the toolchain file at all
        # https://cmake.org/cmake/help/latest/variable/CMAKE_CROSSCOMPILING.html
        # TODO: avoid the toolchain file and set all flags on the command line
        self._prepare_toolchain_file_common(file, TOOLCHAIN_FORCE_STATIC=self.force_static_linkage,
                                            TOOLCHAIN_FILE_PATH=file.absolute())

    def configure(self, **kwargs):
        if self.install_prefix != self.install_dir:
            assert self.destdir, "custom install prefix requires DESTDIR being set!"
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.install_prefix)
        else:
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.install_dir)
        custom_ldflags = self.default_ldflags + self.LDFLAGS
        self.add_cmake_options(
            CMAKE_C_COMPILER=self.CC,
            CMAKE_CXX_COMPILER=self.CXX,
            CMAKE_ASM_COMPILER=self.CC,  # Compile assembly files with the default compiler
            # All of these should be commandlines not CMake lists:
            CMAKE_C_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.CFLAGS),
            CMAKE_CXX_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.CXXFLAGS),
            CMAKE_ASM_FLAGS_INIT=commandline_to_str(self.default_compiler_flags + self.ASMFLAGS),
            CMAKE_EXE_LINKER_FLAGS_INIT=commandline_to_str(
                custom_ldflags + self.target_info.additional_executable_link_flags),
            CMAKE_SHARED_LINKER_FLAGS_INIT=commandline_to_str(
                custom_ldflags + self.target_info.additional_shared_library_link_flags),
            CMAKE_MODULE_LINKER_FLAGS_INIT=commandline_to_str(
                custom_ldflags + self.target_info.additional_shared_library_link_flags),
        )
        if self.optimization_flags:
            # If the project uses custom optimization flags (e.g. SPEC), override the CMake defaults defined in
            # Modules/Compiler/GNU.cmake. Just adding them to CMAKE_<LANG>_FLAGS_INIT is not enough since the
            # CMAKE_<LANG>_FLAGS_<CONFIG>_INIT and  CMAKE_<LANG>_FLAGS variables will be appended and override the
            # optimization flags that we passed as part of CMAKE_<LANG>_FLAGS_INIT.
            flags = " " + commandline_to_str(self.optimization_flags)
            if self.build_type.is_release:
                flags += " -DNDEBUG"
            self.add_cmake_options(**{f"CMAKE_C_FLAGS{self.build_type_var_suffix}": flags,
                                      f"CMAKE_CXX_FLAGS{self.build_type_var_suffix}": flags})
        if not self.compiling_for_host():
            # TODO: set CMAKE_STRIP, CMAKE_NM, CMAKE_OBJDUMP, CMAKE_READELF, CMAKE_DLLTOOL, CMAKE_DLLTOOL,
            #  CMAKE_ADDR2LINE
            self.generate_cmake_toolchain_file(self._toolchain_file)
            self.add_cmake_options(
                _CMAKE_TOOLCHAIN_LOCATION=self.target_info.sdk_root_dir / "bin",
                CMAKE_LINKER=self.target_info.linker)

        if self.target_info.additional_executable_link_flags:
            self.add_cmake_options(
                CMAKE_REQUIRED_LINK_OPTIONS=commandline_to_str(self.target_info.additional_executable_link_flags))
            # TODO: if this doesn't work we can set CMAKE_TRY_COMPILE_TARGET_TYPE to build a static lib instead
            # https://cmake.org/cmake/help/git-master/variable/CMAKE_TRY_COMPILE_TARGET_TYPE.html
            # XXX: we should have everything set up correctly so this should no longer be needed for FreeBSD
            if self.target_info.is_baremetal():
                self.add_cmake_options(CMAKE_TRY_COMPILE_TARGET_TYPE="STATIC_LIBRARY")
        if self.force_static_linkage:
            self.add_cmake_options(
                CMAKE_SHARED_LIBRARY_SUFFIX=".a",
                CMAKE_FIND_LIBRARY_SUFFIXES=".a",
                CMAKE_EXTRA_SHARED_LIBRARY_SUFFIXES=".a")
        else:
            # Use $ORIGIN in the build RPATH (this should make it easier to run tests without having the absolute
            # build directory mounted).
            self.add_cmake_options(CMAKE_BUILD_RPATH_USE_ORIGIN=True)
            # Infer the RPATH needed for each executable.
            self.add_cmake_options(CMAKE_INSTALL_RPATH_USE_LINK_PATH=True)
            # CMake does not add the install directory even if it's a non-default location, so add it manually.
            self.add_cmake_options(CMAKE_INSTALL_RPATH="$ORIGIN/../lib")
        if not self.compiling_for_host() and self.make_args.subkind == MakeCommandKind.Ninja:
            # Ninja can't change the RPATH when installing: https://gitlab.kitware.com/cmake/cmake/issues/13934
            # Fixed in https://gitlab.kitware.com/cmake/cmake/-/merge_requests/6240 (3.21.20210625)
            self.add_cmake_options(
                CMAKE_BUILD_WITH_INSTALL_RPATH=self._get_configure_tool_version() < (3, 21, 20210625))
        # TODO: BUILD_SHARED_LIBS=OFF?

        # Add the options from the config file:
        self.configure_args.extend(self.cmake_options)
        # make sure we get a completely fresh cache when --reconfigure is passed:
        cmake_cache = self.build_dir / "CMakeCache.txt"
        if self.force_configure:
            self.delete_file(cmake_cache)
        super().configure(**kwargs)
        if self.config.copy_compilation_db_to_source_dir and (self.build_dir / "compile_commands.json").exists():
            self.install_file(self.build_dir / "compile_commands.json", self.source_dir / "compile_commands.json",
                              force=True)

    def install(self, _stdout_filter=_default_stdout_filter):
        if _stdout_filter is _default_stdout_filter:
            _stdout_filter = self._cmake_install_stdout_filter
        super().install(_stdout_filter=_stdout_filter)

    def run_tests(self):
        if (self.build_dir / "CTestTestfile.cmake").exists() or self.config.pretend:
            # We can run tests using CTest
            if self.compiling_for_host():
                self.run_cmd(shutil.which(os.getenv("CTEST_COMMAND", "ctest")) or "ctest", "-V", "--output-on-failure",
                             cwd=self.build_dir, env=self.ctest_environment)
            else:
                try:
                    cmake_xtarget = self.crosscompile_target
                    # Use a non-CHERI CMake binary for the purecap rootfs since CMake does not build yet.
                    if cmake_xtarget.is_cheri_purecap() and self.target_info.is_cheribsd():
                        cmake_xtarget = cmake_xtarget.get_non_cheri_for_purecap_rootfs_target()
                    # Use a string here instead of BuildCrossCompiledCMake to avoid a cyclic import.
                    cmake_target = target_manager.get_target("cmake-crosscompiled", cmake_xtarget, self.config, self)
                    cmake_project = cmake_target.project_class.get_instance(self, cross_target=cmake_xtarget)
                    expected_ctest_path = cmake_project.install_dir / "bin/ctest"
                    if not expected_ctest_path.is_file():
                        self.dependency_error(f"cannot find CTest binary ({expected_ctest_path}) to run tests.",
                                              cheribuild_target=cmake_project.target)
                    # --output-junit needs version 3.21
                    min_version = "3.21"
                    if not list(cmake_project.install_dir.glob("share/*/Help/release/" + min_version + ".rst")):
                        self.dependency_error("cannot find release notes for CMake", min_version,
                                              "- installed CMake version is too old",
                                              cheribuild_target=cmake_project.target)
                except LookupError:
                    self.warning("Do not know how to cross-compile CTest for", self.target_info, "-> cannot run tests")
                    return
                args = ["--cmake-install-dir", str(cmake_project.install_dir)]
                for var, value in self.ctest_environment.items():
                    args.append("--test-setup-command=export " + shlex.quote(var + "=" + value))
                args.extend(self.ctest_script_extra_args)
                self.target_info.run_cheribsd_test_script("run_ctest_tests.py", *args, mount_builddir=True,
                                                          mount_sysroot=True, mount_sourcedir=True,
                                                          use_full_disk_image=self.tests_need_full_disk_image)
        else:
            if self.has_optional_tests:
                self.fatal("Can't run tests for projects that were built with tests disabled. ",
                           "Please re-run build the target with --", self.get_config_option_name("build_tests"), sep="")
            self.warning("Do not know how to run tests for", self.target)

    @staticmethod
    def find_package(name: str) -> bool:
        try:
            cmd = "cmake --find-package -DCOMPILER_ID=Clang -DLANGUAGE=CXX -DMODE=EXIST -DQUIET=TRUE".split()
            cmd.append("-DNAME=" + name)
            return run_command(cmd).returncode == 0
        except subprocess.CalledProcessError:
            return False


class AutotoolsProject(Project):
    do_not_add_to_targets = True
    _configure_supports_prefix = True
    make_kind = MakeCommandKind.GnuMake
    add_host_target_build_config_options = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.extra_configure_flags = cls.add_config_option("configure-options", default=[], kind=list, metavar="OPTIONS",
                                                          help="Additional command line options to pass to configure")

    """
    Like Project but automatically sets up the defaults for autotools like projects
    Sets configure command to ./configure, adds --prefix=installdir
    """
    def __init__(self, config, configure_script="configure"):
        super().__init__(config)
        self.configure_command = self.source_dir / configure_script

    def setup(self):
        super().setup()
        buildhost = self.get_host_triple()
        if self.add_host_target_build_config_options:
            if not self.compiling_for_host():
                autotools_triple = self.target_info.target_triple
                # Most scripts don't like the final -purecap component:
                autotools_triple = autotools_triple.replace("-purecap", "")
                # TODO: do we have to remove these too?
                # autotools_triple = autotools_triple.replace("mips64c128-", "cheri-")
                self.configure_args.extend(["--host=" + autotools_triple, "--target=" + autotools_triple,
                                            "--build=" + buildhost])
            elif self.compiling_for_cheri():
                # When compiling natively on CheriBSD, most autotools projects don't like the inferred config.guess
                # value of aarch64c-unknown-freebsd14.0. Override it to make this work in most cases.
                self.configure_args.extend(["--build=" + buildhost])
        if self.config.verbose:
            # Most autotools-base projects enable verbose output by setting V=1
            self.make_args.set_env(V=1)

    def configure(self, **kwargs):
        if self._configure_supports_prefix:
            if self.install_prefix != self.install_dir:
                assert self.destdir, "custom install prefix requires DESTDIR being set!"
                self.configure_args.append("--prefix=" + str(self.install_prefix))
            else:
                self.configure_args.append("--prefix=" + str(self.install_dir))
        if self.extra_configure_flags:
            self.configure_args.extend(self.extra_configure_flags)
        super().configure(**kwargs)

    def needs_configure(self):
        # Most autotools projects use makefiles, but we also use this class for the CMake
        # bootstrap build which ends up generating a build.ninja file instead of a Makefile.
        build_file = "build.ninja" if self.make_args.kind == MakeCommandKind.Ninja else "Makefile"
        return not (self.build_dir / build_file).exists()

    def set_lto_binutils(self, ar, ranlib, nm, ld):
        kwargs = {"NM": nm, "AR": ar, "RANLIB": ranlib}
        if ld:
            kwargs["LD"] = ld
        self.configure_environment.update(**kwargs)
        # self.make_args.env_vars.update(NM=llvm_nm, AR=llvm_ar, RANLIB=llvm_ranlib)
        self.make_args.set(**kwargs)
        self.make_args.env_vars.update(**kwargs)


class MakefileProject(Project):
    """A very simple project that just set some defualt variables such as CC/CXX, etc"""
    do_not_add_to_targets = True
    build_in_source_dir = True  # Most makefile projects don't support out-of-source builds
    make_kind = MakeCommandKind.GnuMake  # Default to GNU make since that's what most makefile projects use
    _define_ld = False
    set_commands_on_cmdline = False  # Set variables such as CC/CXX on the command line instead of the environment

    def setup(self):
        super().setup()
        # Most projects expect that a plain $CC foo.c will work so we include the -target, etc in CC
        essential_flags = self.essential_compiler_and_linker_flags
        self.set_make_cmd_with_args("CC", self.CC, essential_flags)
        self.set_make_cmd_with_args("CPP", self.CPP, essential_flags)
        self.set_make_cmd_with_args("CXX", self.CXX, essential_flags)
        self.set_make_cmd_with_args("CCLD", self.CC, essential_flags)
        self.set_make_cmd_with_args("CXXLD", self.CXX, essential_flags)
        self.make_args.set_env(AR=self.target_info.ar)

        # Some projects expect LD to be CCLD others really mean the raw linker
        if self._define_ld:
            self.make_args.set_env(LD=self.target_info.linker)

        # Set values in the environment so that projects can override them
        cppflags = self.default_compiler_flags
        self.make_args.set_env(
            CFLAGS=commandline_to_str(cppflags + self.CFLAGS),
            CXXFLAGS=commandline_to_str(cppflags + self.CXXFLAGS),
            CPPFLAGS=commandline_to_str(cppflags + self.CFLAGS),
            LDFLAGS=commandline_to_str(self.default_ldflags + self.LDFLAGS),
        )

    def set_make_cmd_with_args(self, var, cmd: Path, args: list):
        value = str(cmd)
        if args:
            value += " " + self.commandline_to_str(args)
        if self.set_commands_on_cmdline:
            self.make_args.set(**{var: value})
        else:
            self.make_args.set_env(**{var: value})


class MesonProject(_CMakeAndMesonSharedLogic):
    do_not_add_to_targets = True
    make_kind = MakeCommandKind.Ninja
    compile_db_requires_bear = False  # generated by default
    default_build_type = BuildType.RELWITHDEBINFO
    generate_cmakelists = False  # Can use compilation DB
    # Meson already sets PKG_CONFIG_* variables internally based on the cross toolchain
    set_pkg_config_path = False
    _configure_tool_name = "Meson"
    meson_test_script_extra_args = tuple()  # additional arguments to pass to run_meson_tests.py

    def set_minimum_meson_version(self, major: int, minor: int, patch: int = 0):
        new_version = (major, minor, patch)
        assert self._minimum_cmake_or_meson_version is None or new_version >= self._minimum_cmake_or_meson_version
        self._minimum_cmake_or_meson_version = new_version

    def _configure_tool_install_instructions(self) -> InstallInstructions:
        return OSInfo.install_instructions(
            "meson", False, default="meson",
            alternative="run `pip3 install --upgrade --user meson` to install the latest version")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.meson_options = cls.add_config_option("meson-options", default=[], kind=list, metavar="OPTIONS",
                                                  help="Additional command line options to pass to Meson")

    def __init__(self, config):
        super().__init__(config)
        self.configure_command = os.getenv("MESON_COMMAND", None)
        if self.configure_command is None:
            self.configure_command = "meson"
            # Ubuntu/Debian's packages are way too old, suggest pip instead
            install_instructions = None
            if OSInfo.is_ubuntu() or OSInfo.is_debian():
                install_instructions = "Try running `pip3 install --upgrade --user meson`"
            self.add_required_system_tool("meson", homebrew="meson", zypper="meson", freebsd="meson", apt="meson",
                                          install_instructions=install_instructions)
        self.configure_args.insert(0, "setup")
        # We generate a toolchain file when cross-compiling and the toolchain files need at least 0.57
        self.set_minimum_meson_version(0, 57)

    @property
    def _native_toolchain_file(self) -> Path:
        assert not self.compiling_for_host()
        return self.build_dir / "meson-native-file.ini"

    def add_meson_options(self, _include_empty_vars=False, _replace=True, **kwargs):
        return self._add_configure_options(_config_file_options=self.meson_options, _replace=_replace,
                                           _include_empty_vars=_include_empty_vars, **kwargs)

    def setup(self):
        super().setup()
        self._toolchain_template = include_local_file("files/meson-machine-file.ini.in")
        if not self.compiling_for_host():
            assert self.target_info.is_freebsd(), "Only tested with FreeBSD so far"
            self._toolchain_file = self.build_dir / "meson-cross-file.ini"
            self.configure_args.extend(["--cross-file", str(self._toolchain_file)])
            # We also have to pass a native machine file to override pkg-config/cmake search dirs for host tools
            self.configure_args.extend(["--native-file", str(self._native_toolchain_file)])
        else:
            # Recommended way to override compiler is using a native config file:
            self._toolchain_file = self.build_dir / "meson-native-file.ini"
            self.configure_args.extend(["--native-file", str(self._toolchain_file)])
            # PKG_CONFIG_LIBDIR can only be set in the toolchain file when cross-compiling, set it in the environment
            # for CheriBSD with pkg-config installed via pkg64.
            if self._native_pkg_config_libdir():
                self.configure_environment.update(PKG_CONFIG_LIBDIR=self._native_pkg_config_libdir())
                self.make_args.set_env(PKG_CONFIG_LIBDIR=self._native_pkg_config_libdir())
        if self.force_configure and not self.with_clean and (self.build_dir / "meson-info").exists():
            self.configure_args.append("--reconfigure")
        # Don't use bundled fallback dependencies, we always want to use the (potentially patched) system packages.
        self.configure_args.append("--wrap-mode=nofallback")
        self.add_meson_options(**self.build_type.to_meson_args())
        if self.use_lto:
            self.add_meson_options(b_lto=True, b_lto_threads=self.config.make_jobs,
                                   b_lto_mode="thin" if self.get_compiler_info(self.CC).is_clang else "default")
        if self.use_asan:
            self.add_meson_options(b_sanitize="address,undefined", b_lundef=False)

        # Unlike CMake, Meson does not set the DT_RUNPATH entry automatically:
        # See https://github.com/mesonbuild/meson/issues/6220, https://github.com/mesonbuild/meson/issues/6541, etc.
        extra_libdirs = [s / self.target_info.default_libdir for s in self.dependency_install_prefixes]
        try:
            # If we are installing into a rootfs, remove the rootfs prefix from the RPATH
            extra_libdirs = ["/" + str(s.relative_to(self.rootfs_dir)) for s in extra_libdirs]
        except NotImplementedError:
            pass  # If there isn't a rootfs, we use the absolute paths instead.
        rpath_dirs = remove_duplicates(self.target_info.additional_rpath_directories + extra_libdirs)
        if rpath_dirs:
            self.COMMON_LDFLAGS.append("-Wl,-rpath=" + ":".join(map(str, rpath_dirs)))

    def needs_configure(self) -> bool:
        return not (self.build_dir / "build.ninja").exists()

    def _toolchain_file_list_to_str(self, values: list) -> str:
        # The meson toolchain file uses python-style lists
        assert all(isinstance(x, str) or isinstance(x, Path) for x in values), \
            "All values should be strings/Paths: " + str(values)
        return str(list(map(str, values)))

    def _bool_to_str(self, value: bool) -> str:
        return "true" if value else "false"

    @property
    def _get_version_args(self) -> dict:
        return dict(regex=b"(\\d+)\\.(\\d+)\\.?(\\d+)?")

    def configure(self, **kwargs):
        pkg_config_bin = shutil.which("pkg-config") or "pkg-config"
        cmake_bin = shutil.which(os.getenv("CMAKE_COMMAND", "cmake")) or "cmake"
        self._prepare_toolchain_file_common(
            self._toolchain_file,
            TOOLCHAIN_LINKER=self.target_info.linker,
            TOOLCHAIN_MESON_CPU_FAMILY=self.crosscompile_target.cpu_architecture.as_meson_cpu_family(),
            TOOLCHAIN_ENDIANESS=self.crosscompile_target.cpu_architecture.endianess(),
            TOOLCHAIN_PKGCONFIG_BINARY=pkg_config_bin,
            TOOLCHAIN_CMAKE_BINARY=cmake_bin,
        )
        if not self.compiling_for_host():
            native_toolchain_template = include_local_file("files/meson-cross-file-native-env.ini.in")
            # Create a stub NativeTargetInfo to obtain the host {CMAKE_PREFIX,PKG_CONFIG}_PATH.
            # NB: we pass None as the project argument here to ensure the results do not different between projects.
            # noinspection PyTypeChecker
            host_target_info = NativeTargetInfo(BasicCompilationTargets.NATIVE, None)  # pytype: disable=wrong-arg-types
            host_prefixes = self.host_dependency_prefixes
            assert self.config.other_tools_dir in host_prefixes
            host_pkg_config_dirs = list(itertools.chain.from_iterable(
                host_target_info.pkgconfig_candidates(x) for x in host_prefixes))
            self._replace_values_in_toolchain_file(
                native_toolchain_template, self._native_toolchain_file,
                NATIVE_C_COMPILER=self.host_CC, NATIVE_CXX_COMPILER=self.host_CXX,
                TOOLCHAIN_PKGCONFIG_BINARY=pkg_config_bin, TOOLCHAIN_CMAKE_BINARY=cmake_bin,
                # To find native packages we have to add the bootstrap tools to PKG_CONFIG_PATH and CMAKE_PREFIX_PATH.
                NATIVE_PKG_CONFIG_PATH=remove_duplicates(host_pkg_config_dirs),
                NATIVE_CMAKE_PREFIX_PATH=remove_duplicates(
                    host_prefixes + host_target_info.cmake_prefix_paths(self.config))
            )

        if self.install_prefix != self.install_dir:
            assert self.destdir, "custom install prefix requires DESTDIR being set!"
            self.add_meson_options(prefix=self.install_prefix)
        else:
            self.add_meson_options(prefix=self.install_dir)
        # Meson setup --reconfigure does not update cached dependencies, we have to manually run
        # `meson configure --clearcache` (https://github.com/mesonbuild/meson/issues/6180).
        if self.force_configure and not self.with_clean and (self.build_dir / "meson-info").exists():
            self.configure_args.append("--reconfigure")
            self.run_cmd(self.configure_command, "configure", "--clearcache", cwd=self.build_dir)
        self.configure_args.append(str(self.source_dir))
        self.configure_args.append(str(self.build_dir))
        super().configure(**kwargs)
        if self.config.copy_compilation_db_to_source_dir and (self.build_dir / "compile_commands.json").exists():
            self.install_file(self.build_dir / "compile_commands.json", self.source_dir / "compile_commands.json",
                              force=True)

    def run_tests(self):
        if self.compiling_for_host():
            self.run_cmd(self.configure_command, "test", "--print-errorlogs", cwd=self.build_dir)
        elif self.target_info.is_cheribsd():
            self.target_info.run_cheribsd_test_script("run_meson_tests.py", *self.meson_test_script_extra_args,
                                                      mount_builddir=True, mount_sysroot=True, mount_sourcedir=True,
                                                      use_full_disk_image=self.tests_need_full_disk_image)
        else:
            self.info("Don't know how to run tests for", self.target, "when cross-compiling for",
                      self.crosscompile_target)
