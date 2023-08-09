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
import os
import shutil
import subprocess
import typing
from pathlib import Path
from typing import Optional

from .simple_project import SimpleProject
from ..config.target_info import CrossCompileTarget
from ..processutils import get_program_version, run_command
from ..utils import AnsiColour, coloured, remove_prefix, status_update

if typing.TYPE_CHECKING:
    from .project import Project

__all__ = ["GitRepository", "ExternallyManagedSourceRepository", "MercurialRepository",  # no-combine
           "ReuseOtherProjectRepository", "ReuseOtherProjectDefaultTargetRepository",  # no-combine
           "SubversionRepository", "TargetBranchInfo", "SourceRepository"]  # no-combine


class SourceRepository:
    def ensure_cloned(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: Path,
                      skip_submodules=False) -> None:
        raise NotImplementedError

    def update(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: "Optional[Path]" = None,
               revision=None, skip_submodules=False) -> None:
        raise NotImplementedError

    def get_real_source_dir(self, caller: SimpleProject, base_project_source_dir: Path) -> Path:
        return base_project_source_dir


class ExternallyManagedSourceRepository(SourceRepository):
    def ensure_cloned(self, current_project: "Project", src_dir: Path, **kwargs):
        current_project.info("Not cloning repositiory since it is externally managed")

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        current_project.info("Not updating", src_dir, "since it is externally managed")


class ReuseOtherProjectRepository(SourceRepository):
    def __init__(self, source_project: "type[Project]", *, subdirectory=".",
                 repo_for_target: "Optional[CrossCompileTarget]" = None, do_update=False):
        self.source_project = source_project
        self.subdirectory = subdirectory
        self.repo_for_target = repo_for_target
        self.do_update = do_update

    def ensure_cloned(self, current_project: "Project", **kwargs) -> None:
        # noinspection PyProtectedMember
        src = self.get_real_source_dir(current_project, current_project._initial_source_dir)
        if not src.exists():
            current_project.fatal(
                f"Source repository for target {current_project.target} does not exist.",
                fixit_hint=f"This project uses the sources from the {self.source_project.target} target so you will"
                f" have to clone that first. Try running:\n\t`cheribuild.py {self.source_project.target} "
                f"--no-skip-update --skip-configure --skip-build --skip-install`",
            )

    def get_real_source_dir(self, caller: SimpleProject, base_project_source_dir: Optional[Path]) -> Path:
        if base_project_source_dir is not None:
            return base_project_source_dir
        return self.source_project.get_source_dir(caller, cross_target=self.repo_for_target) / self.subdirectory

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        if self.do_update:
            src_proj = self.source_project.get_instance(current_project, cross_target=self.repo_for_target)
            src_proj.update()
        else:
            current_project.info("Not updating", src_dir, "since it reuses the repository for ",
                                 self.source_project.target)


class ReuseOtherProjectDefaultTargetRepository(ReuseOtherProjectRepository):
    def __init__(self, source_project: "type[Project]", *, subdirectory=".", do_update=False):
        super().__init__(source_project, subdirectory=subdirectory, do_update=do_update,
                         repo_for_target=source_project.supported_architectures[0])


# Use git-worktree to handle per-target branches:
class TargetBranchInfo:
    def __init__(self, branch: str, directory_name: str, url: "Optional[str]" = None):
        self.branch = branch
        self.directory_name = directory_name
        self.url = url


_PRETEND_RUN_GIT_COMMANDS = os.getenv("_TEST_SKIP_GIT_COMMANDS") is None


# TODO: can use dataclasses once we depend on python 3.7+
class GitBranchInfo(typing.NamedTuple):
    local_branch: str
    upstream_branch: Optional[str] = None
    remote_name: Optional[str] = None


class GitRepository(SourceRepository):
    def __init__(self, url: str, *, old_urls: "Optional[list[bytes]]" = None, default_branch: "Optional[str]" = None,
                 force_branch: bool = False, temporary_url_override: "Optional[str]" = None,
                 url_override_reason: "typing.Any" = None,
                 per_target_branches: "Optional[dict[CrossCompileTarget, TargetBranchInfo]]" = None,
                 old_branches: "Optional[dict[str, str]]" = None):
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
    def get_branch_info(src_dir: Path) -> "Optional[GitBranchInfo]":
        try:
            status = run_command("git", "status", "-b", "-s", "--porcelain=v2", "-u", "no",
                                 capture_output=True, print_verbose_only=True, cwd=src_dir,
                                 run_in_pretend_mode=_PRETEND_RUN_GIT_COMMANDS)
            if not status.stdout.startswith(b"# branch"):
                return None  # unexpected output format
            headers = {}
            for line in status.stdout.splitlines():
                if not line.startswith(b"#"):
                    break  # end of metadata information
                key, value = line[1:].decode("utf-8").split(None, maxsplit=1)
                headers[key] = value
            upstream = headers.get("branch.upstream", None)
            remote_name, remote_branch = upstream.split("/", maxsplit=1) if upstream else (None, None)
            return GitBranchInfo(local_branch=headers.get("branch.head", None),
                                 remote_name=remote_name, upstream_branch=remote_branch)
        except subprocess.CalledProcessError as e:
            if isinstance(e.__cause__, FileNotFoundError):
                return None  # git not installed
            # Fall back to v1 output on error (v2 requires git 2.11 -- which should be available everywhere)
            # TODO: can we drop this support? I believe all systems should have support for git 2.11
            status = run_command("git", "status", "-b", "-s", "--porcelain", "-u", "no",
                                 capture_output=True, print_verbose_only=True, cwd=src_dir,
                                 run_in_pretend_mode=_PRETEND_RUN_GIT_COMMANDS)
            if not status.stdout.startswith(b"## "):
                return None  # unexpected output format
            branch_info = status.stdout.splitlines()[0].decode("utf-8")
            local_end_idx = branch_info.find("...")
            if local_end_idx == -1:
                return GitBranchInfo(local_branch=branch_info[3:])   # no upstream configured
            local_branch = branch_info[3:local_end_idx]
            upstream = branch_info[local_end_idx + 3 :].split()[0].rstrip()
            remote_name, remote_branch = upstream.split("/", maxsplit=1)
            return GitBranchInfo(local_branch=local_branch, remote_name=remote_name, upstream_branch=remote_branch)

    @staticmethod
    def contains_commit(current_project: "Project", commit: str, *, src_dir: Path, expected_branch="HEAD",
                        invalid_commit_ref_result: typing.Any = False):
        if current_project.config.pretend and (not src_dir.exists() or not shutil.which("git")):
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
            current_project.run_cmd([*clone_cmd, self.url, base_project_source_dir], cwd="/")
            # Could also do this but it seems to fetch more data than --no-single-branch
            # if self.config.shallow_clone:
            #    current_project.run_cmd(["git", "config", "remote.origin.fetch",
            #                             "+refs/heads/*:refs/remotes/origin/*"], cwd=src_dir)

        if src_dir == base_project_source_dir:
            return  # Nothing else to do

        # Handle per-target overrides by adding a new git-worktree.
        target_override = self.per_target_branches.get(current_project.crosscompile_target, None)
        default_clone_branch = self.get_default_branch(current_project, include_per_target=False)
        assert target_override is not None, "Default src != base src -> must have a per-target override"
        assert target_override.branch != default_clone_branch, \
            f"Cannot create worktree with same branch as base repo: {target_override.branch} vs {default_clone_branch}"
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

    def update(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: "Optional[Path]" = None,
               revision=None, skip_submodules=False):
        self.ensure_cloned(current_project, src_dir=src_dir, base_project_source_dir=base_project_source_dir,
                           skip_submodules=skip_submodules)
        if current_project.skip_update:
            return
        if not src_dir.exists():
            return

        # handle repositories that have moved:
        if src_dir.exists() and self.old_urls:
            branch_info = self.get_branch_info(src_dir)
            if branch_info is not None and branch_info.remote_name is not None:
                remote_url = run_command("git", "remote", "get-url", branch_info.remote_name, capture_output=True,
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
                            run_command("git", "remote", "set-url", branch_info.remote_name, self.url,
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
            branch_info = self.get_branch_info(src_dir)
            current_branch = branch_info.local_branch if branch_info is not None else None
            if branch_info is None:
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
                            run_command("git", "checkout", "--track", f"{branch_info.remote_name}/{default_branch}",
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
        run_command([*pull_cmd, rebase_flag], cwd=src_dir, print_verbose_only=True)
        if not skip_submodules:
            run_command("git", "submodule", "update", "--init", "--recursive", cwd=src_dir, print_verbose_only=True)
        if has_changes and not has_autostash:
            run_command("git", "stash", "pop", cwd=src_dir, print_verbose_only=True)


class MercurialRepository(SourceRepository):
    def __init__(self, url: str, *, old_urls: "Optional[list[bytes]]" = None, default_branch: "Optional[str]" = None,
                 force_branch: bool = False, temporary_url_override: "Optional[str]" = None,
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
    def run_hg(src_dir: "Optional[Path]", *args, project: "Project", **kwargs):
        assert src_dir is None or isinstance(src_dir, Path)
        command = ["hg"]
        project.check_required_system_tool("hg", default="mercurial")
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
                                         capture_output=True, print_verbose_only=True, project=current_project)
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
            self.run_hg(None, [*clone_cmd, self.url, base_project_source_dir], cwd="/", project=current_project)
        assert src_dir == base_project_source_dir, "Worktrees only supported with git"

    def update(self, current_project: "Project", *, src_dir: Path, base_project_source_dir: "Optional[Path]" = None,
               revision=None, skip_submodules=False):
        self.ensure_cloned(current_project, src_dir=src_dir, base_project_source_dir=base_project_source_dir,
                           skip_submodules=skip_submodules)
        if current_project.skip_update:
            return
        if not src_dir.exists():
            return

        # handle repositories that have moved
        if src_dir.exists() and self.old_urls:
            remote_url = self.run_hg(src_dir, "paths", "default", capture_output=True,
                                     project=current_project).stdout.strip()
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
        self.run_hg(src_dir, "pull", project=current_project)

        if revision is not None:
            # TODO: do some identify stuff to check if we are on the right revision?
            self.run_hg(src_dir, "update", "--merge", revision, print_verbose_only=True, project=current_project)
            return

        # Handle forced branches now that we have fetched the latest changes
        if src_dir.exists() and self.force_branch:
            assert self.default_branch, "default_branch must be set if force_branch is true!"
            branch = self.run_hg(src_dir, "branch", capture_output=True, print_verbose_only=True,
                                 project=current_project)
            current_branch = branch.stdout.decode("utf-8")
            if current_branch != self.force_branch:
                current_project.warning("You are trying to build the", current_branch,
                                        "branch. You should be using", self.default_branch)
                if current_project.query_yes_no("Would you like to change to the " + self.default_branch + " branch?"):
                    self.run_hg(src_dir, "update", "--merge", self.default_branch, project=current_project)
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
        has_changes = len(self.run_hg(src_dir, "diff", "--stat", capture_output=True, print_verbose_only=True,
                                      project=current_project).stdout) > 1
        if has_changes:
            print(coloured(AnsiColour.green, "Local changes detected in", src_dir))
            # TODO: add a config option to skip this query?
            if current_project.config.force_update:
                status_update("Updating", src_dir, "with merge due to --force-update")
            elif not current_project.query_yes_no("Update and merge the changes?", default_result=True,
                                                  force_result=True):
                status_update("Skipping update of", src_dir)
                return
        self.run_hg(src_dir, "update", "--merge", ".", print_verbose_only=True, project=current_project)


class SubversionRepository(SourceRepository):
    def __init__(self, url, *, default_branch: "Optional[str]" = None):
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
        current_project.run_cmd([*checkout_cmd, checkout_url, src_dir], cwd="/")

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        self.ensure_cloned(current_project, src_dir=src_dir)
        if current_project.skip_update:
            return
        if not src_dir.exists():
            return

        update_command = ["svn", "update"]
        run_command(update_command, cwd=src_dir)
