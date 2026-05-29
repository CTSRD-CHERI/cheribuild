import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

if shutil.which("git") is None:
    pytest.skip("git command not found", allow_module_level=True)

from .setup_mock_chericonfig import setup_mock_chericonfig
from pycheribuild.config.target_info import BasicCompilationTargets, DefaultInstallDir
from pycheribuild.projects.project import Project
from pycheribuild.projects.repository import GitRepository, TargetBranchInfo


@pytest.fixture(scope="module", autouse=True)
def git_env():
    with pytest.MonkeyPatch.context() as mp:
        # Clear git hook environment variables to avoid affecting the host repo
        for var in list(os.environ.keys()):
            if var.startswith("GIT_"):
                mp.delenv(var, raising=False)
        mp.setenv("GIT_AUTHOR_NAME", "Test Author")
        mp.setenv("GIT_AUTHOR_EMAIL", "author@example.com")
        mp.setenv("GIT_COMMITTER_NAME", "Test Committer")
        mp.setenv("GIT_COMMITTER_EMAIL", "committer@example.com")
        yield


def create_remote_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True)
    subprocess.run(["git", "checkout", "-B", "main"], cwd=path, check=True)
    (path / "file").write_text("content")
    subprocess.run(["git", "add", "file"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True)
    subprocess.run(["git", "branch", "target-branch"], cwd=path, check=True)
    (path / "file2").write_text("content")
    subprocess.run(["git", "add", "file2"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "second commit"], cwd=path, check=True)

    # Create branches needed for tests
    subprocess.run(["git", "branch", "other-branch", "-f", "HEAD^"], cwd=path, check=True)
    return path


@pytest.fixture(scope="module")
def shared_remote(tmp_path_factory: pytest.TempPathFactory):
    remote_dir = tmp_path_factory.mktemp("shared_remote")
    create_remote_repo(remote_dir)
    return remote_dir


class MockProject(Project):
    do_not_add_to_targets = True
    _should_not_be_instantiated = False
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    target = "mock-git-project"
    default_directory_basename = "mock-git-project"

    def __init__(self, config, **kwargs):
        super().__init__(config, **kwargs)
        self.query_yes_no = Mock(return_value=True)  # ty: ignore
        self.skip_update = False
        self.events = []

    def info(self, *args, sep=" ", **kwargs):
        self.events.append(("info", sep.join(map(str, args))))

    def warning(self, *args, sep=" ", **kwargs):
        self.events.append(("warning", sep.join(map(str, args))))

    def verbose_print(self, *args, sep=" ", **kwargs):
        msg = sep.join(map(str, args))
        msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)
        self.events.append(("verbose", msg))

    def run_cmd(self, *args, **kwargs):
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            cmdline = args[0]
        else:
            cmdline = args
        self.events.append(("command", list(cmdline)))
        return super().run_cmd(*args, **kwargs)


def setup_test_project(local_dir, remote_dir, target_branch="target-branch"):
    local_dir.parent.mkdir(parents=True, exist_ok=True)

    config = setup_mock_chericonfig(local_dir.parent, pretend=False)
    config.skip_clone = False
    config.confirm_clone = False

    class TestProject(MockProject):
        target = "test-git-project"
        do_not_add_to_targets = True
        _should_not_be_instantiated = False
        _xtarget = BasicCompilationTargets.NATIVE_NON_PURECAP
        repository = GitRepository(
            str(remote_dir),
            force_branch=True,
            default_branch="main",
            per_target_branches={
                BasicCompilationTargets.NATIVE_NON_PURECAP: TargetBranchInfo(
                    branch=target_branch, directory_name="dummy-dir"
                )
            },
        )

    TestProject.setup_config_options()

    project = TestProject(config, crosscompile_target=BasicCompilationTargets.NATIVE_NON_PURECAP)
    return project


def get_git_revision(repo_dir: Path, ref: str = "HEAD") -> str:
    res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", ref],
        cwd=repo_dir,
        capture_output=True,
        check=True,
    )
    return res.stdout.decode("utf-8").strip()


@pytest.fixture
def local_repo(shared_remote: Path, tmp_path: Path):
    local_dir = tmp_path / "local"
    local_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=local_dir, check=True)
    subprocess.run(["git", "checkout", "-B", "main"], cwd=local_dir, check=True)
    (local_dir / "file").write_text("initial content")
    subprocess.run(["git", "add", "file"], cwd=local_dir, check=True)
    subprocess.run(["git", "commit", "-m", "conflicting initial commit"], cwd=local_dir, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(shared_remote)], cwd=local_dir, check=True)
    subprocess.run(["git", "fetch", "origin"], cwd=local_dir, check=True)
    return local_dir


def test_switch_branch_tracks_correct(shared_remote: Path, local_repo: Path):
    project = setup_test_project(local_repo, shared_remote)

    repo = project.repository
    repo.update(project, src_dir=local_repo)

    assert project.events == [
        ("warning", "You are trying to build the main branch. You should be using target-branch"),
        ("command", ["git", "remote", "-v"]),
        ("verbose", "Found existing remotes: origin"),
        ("info", "Fetching changes from remote origin"),
        ("command", ["git", "fetch", "origin"]),
        ("command", ["git", "show-ref", "--verify", "refs/heads/target-branch"]),
        ("info", "Branch target-branch does not exist locally. Checking it out from origin/target-branch"),
        ("command", ["git", "checkout", "--track", "origin/target-branch"]),
        ("command", ["git", "fetch"]),
        ("command", ["git", "merge-base", "--is-ancestor", "@{upstream}", "HEAD"]),
        ("verbose", "HEAD contains commit @{upstream}"),
        ("info", "Skipping update: Current HEAD is up-to-date or ahead of upstream."),
    ]

    assert get_git_revision(local_repo, "HEAD") == "target-branch"

    assert get_git_revision(local_repo, "target-branch@{u}") == "origin/target-branch"


def test_switch_branch_remote_missing(shared_remote: Path, local_repo: Path):
    project = setup_test_project(local_repo, shared_remote)
    subprocess.run(["git", "remote", "remove", "origin"], cwd=local_repo, check=True)

    repo = project.repository
    repo.update(project, src_dir=local_repo)

    res = subprocess.run(
        ["git", "remote", "-v"],
        cwd=local_repo,
        capture_output=True,
        check=True,
    )
    remotes = res.stdout.decode("utf-8")
    assert str(shared_remote) in remotes


def test_switch_branch_tracks_wrong_remote(shared_remote: Path, local_repo: Path, tmp_path: Path):
    other_remote_dir = tmp_path / "other_remote"
    create_remote_repo(other_remote_dir)

    subprocess.run(["git", "remote", "add", "other", str(other_remote_dir)], cwd=local_repo, check=True)
    subprocess.run(["git", "fetch", "other"], cwd=local_repo, check=True)
    subprocess.run(["git", "branch", "--track", "wrong-branch", "other/target-branch"], cwd=local_repo, check=True)
    subprocess.run(["git", "branch", "-m", "wrong-branch", "target-branch"], cwd=local_repo, check=True)

    project = setup_test_project(local_repo, shared_remote)

    repo = project.repository
    repo.update(project, src_dir=local_repo)

    assert project.events == [
        ("warning", "You are trying to build the main branch. You should be using target-branch"),
        ("command", ["git", "remote", "-v"]),
        ("verbose", "Found existing remotes: origin, other"),
        ("info", "Fetching changes from remote origin"),
        ("command", ["git", "fetch", "origin"]),
        ("command", ["git", "show-ref", "--verify", "refs/heads/target-branch"]),
        ("command", ["git", "rev-parse", "--abbrev-ref", "target-branch@{u}"]),
        (
            "info",
            "Branch target-branch tracks wrong branch other/target-branch. "
            "Creating new branch target-branch-cheribuild",
        ),
        ("command", ["git", "checkout", "-b", "target-branch-cheribuild", "origin/target-branch"]),
        ("command", ["git", "fetch"]),
        ("command", ["git", "merge-base", "--is-ancestor", "@{upstream}", "HEAD"]),
        ("verbose", "HEAD contains commit @{upstream}"),
        ("info", "Skipping update: Current HEAD is up-to-date or ahead of upstream."),
    ]

    assert get_git_revision(local_repo, "HEAD") == "target-branch-cheribuild"


def test_switch_branch_tracks_wrong_branch_same_remote(shared_remote: Path, local_repo: Path):
    subprocess.run(["git", "branch", "--track", "wrong-branch", "origin/other-branch"], cwd=local_repo, check=True)
    subprocess.run(["git", "branch", "-m", "wrong-branch", "target-branch"], cwd=local_repo, check=True)

    project = setup_test_project(local_repo, shared_remote)

    repo = project.repository
    repo.update(project, src_dir=local_repo)

    assert project.events == [
        ("warning", "You are trying to build the main branch. You should be using target-branch"),
        ("command", ["git", "remote", "-v"]),
        ("verbose", "Found existing remotes: origin"),
        ("info", "Fetching changes from remote origin"),
        ("command", ["git", "fetch", "origin"]),
        ("command", ["git", "show-ref", "--verify", "refs/heads/target-branch"]),
        ("command", ["git", "rev-parse", "--abbrev-ref", "target-branch@{u}"]),
        (
            "info",
            "Branch target-branch tracks wrong branch origin/other-branch. "
            "Creating new branch target-branch-cheribuild",
        ),
        ("command", ["git", "checkout", "-b", "target-branch-cheribuild", "origin/target-branch"]),
        ("command", ["git", "fetch"]),
        ("command", ["git", "merge-base", "--is-ancestor", "@{upstream}", "HEAD"]),
        ("verbose", "HEAD contains commit @{upstream}"),
        ("info", "Skipping update: Current HEAD is up-to-date or ahead of upstream."),
    ]

    assert get_git_revision(local_repo, "HEAD") == "target-branch-cheribuild"


def test_switch_branch_no_upstream(shared_remote: Path, local_repo: Path):
    subprocess.run(["git", "branch", "target-branch", "origin/target-branch"], cwd=local_repo, check=True)
    subprocess.run(["git", "branch", "--unset-upstream", "target-branch"], cwd=local_repo, check=True)

    project = setup_test_project(local_repo, shared_remote)

    repo = project.repository
    repo.update(project, src_dir=local_repo)

    assert project.events == [
        ("warning", "You are trying to build the main branch. You should be using target-branch"),
        ("command", ["git", "remote", "-v"]),
        ("verbose", "Found existing remotes: origin"),
        ("info", "Fetching changes from remote origin"),
        ("command", ["git", "fetch", "origin"]),
        ("command", ["git", "show-ref", "--verify", "refs/heads/target-branch"]),
        ("command", ["git", "rev-parse", "--abbrev-ref", "target-branch@{u}"]),
        ("info", "Branch target-branch has no upstream. Creating new branch target-branch-cheribuild"),
        ("command", ["git", "checkout", "-b", "target-branch-cheribuild", "origin/target-branch"]),
        ("command", ["git", "fetch"]),
        ("command", ["git", "merge-base", "--is-ancestor", "@{upstream}", "HEAD"]),
        ("verbose", "HEAD contains commit @{upstream}"),
        ("info", "Skipping update: Current HEAD is up-to-date or ahead of upstream."),
    ]

    assert get_git_revision(local_repo, "HEAD") == "target-branch-cheribuild"


def test_switch_branch_duplicate_remotes(shared_remote: Path, local_repo: Path):
    subprocess.run(["git", "remote", "add", "origin2", str(shared_remote)], cwd=local_repo, check=True)

    project = setup_test_project(local_repo, shared_remote)

    repo = project.repository
    repo.update(project, src_dir=local_repo)

    assert project.events == [
        ("warning", "You are trying to build the main branch. You should be using target-branch"),
        ("command", ["git", "remote", "-v"]),
        ("verbose", "Found existing remotes: origin, origin2"),
        ("info", "Fetching changes from remote origin"),
        ("command", ["git", "fetch", "origin"]),
        ("command", ["git", "show-ref", "--verify", "refs/heads/target-branch"]),
        ("info", "Branch target-branch does not exist locally. Checking it out from origin/target-branch"),
        ("command", ["git", "checkout", "--track", "origin/target-branch"]),
        ("command", ["git", "fetch"]),
        ("command", ["git", "merge-base", "--is-ancestor", "@{upstream}", "HEAD"]),
        ("verbose", "HEAD contains commit @{upstream}"),
        ("info", "Skipping update: Current HEAD is up-to-date or ahead of upstream."),
    ]

    assert get_git_revision(local_repo, "HEAD") == "target-branch"


def test_switch_branch_origin_wrong_url(shared_remote: Path, local_repo: Path):
    """Test creating a new remote if 'origin' points to the wrong URL."""
    # Change 'origin' to point to some wrong URL
    wrong_url = "https://example.com/wrong/repo.git"
    subprocess.run(["git", "remote", "set-url", "origin", wrong_url], cwd=local_repo, check=True)

    project = setup_test_project(local_repo, shared_remote)

    repo = project.repository
    repo.update(project, src_dir=local_repo)

    assert get_git_revision(local_repo, "HEAD") == "target-branch"
    assert get_git_revision(local_repo, "target-branch@{u}") == "new-origin/target-branch"

    assert project.events == [
        ("warning", "You are trying to build the main branch. You should be using target-branch"),
        ("command", ["git", "remote", "-v"]),
        ("verbose", "Found existing remotes: origin"),
        ("info", f"Remote for {shared_remote} not found. Adding it."),
        ("command", ["git", "remote", "add", "new-origin", str(shared_remote)]),
        ("info", "Fetching changes from remote new-origin"),
        ("command", ["git", "fetch", "new-origin"]),
        ("command", ["git", "show-ref", "--verify", "refs/heads/target-branch"]),
        ("info", "Branch target-branch does not exist locally. Checking it out from new-origin/target-branch"),
        ("command", ["git", "checkout", "--track", "new-origin/target-branch"]),
        ("command", ["git", "fetch"]),
        ("command", ["git", "merge-base", "--is-ancestor", "@{upstream}", "HEAD"]),
        ("verbose", "HEAD contains commit @{upstream}"),
        ("info", "Skipping update: Current HEAD is up-to-date or ahead of upstream."),
    ]


def test_switch_branch_origin_and_new_origin_wrong_url(shared_remote: Path, local_repo: Path):
    """Test creating a new remote if 'origin' and 'new-origin' point to the wrong URL."""
    wrong_url = "https://example.com/wrong/repo.git"
    subprocess.run(["git", "remote", "set-url", "origin", wrong_url], cwd=local_repo, check=True)
    subprocess.run(["git", "remote", "add", "new-origin", wrong_url], cwd=local_repo, check=True)

    project = setup_test_project(local_repo, shared_remote)

    repo = project.repository
    repo.update(project, src_dir=local_repo)

    assert get_git_revision(local_repo, "HEAD") == "target-branch"
    assert get_git_revision(local_repo, "target-branch@{u}") == "new-new-origin/target-branch"

    assert project.events == [
        ("warning", "You are trying to build the main branch. You should be using target-branch"),
        ("command", ["git", "remote", "-v"]),
        ("verbose", "Found existing remotes: new-origin, origin"),
        ("info", f"Remote for {shared_remote} not found. Adding it."),
        ("command", ["git", "remote", "add", "new-new-origin", str(shared_remote)]),
        ("info", "Fetching changes from remote new-new-origin"),
        ("command", ["git", "fetch", "new-new-origin"]),
        ("command", ["git", "show-ref", "--verify", "refs/heads/target-branch"]),
        ("info", "Branch target-branch does not exist locally. Checking it out from new-new-origin/target-branch"),
        ("command", ["git", "checkout", "--track", "new-new-origin/target-branch"]),
        ("command", ["git", "fetch"]),
        ("command", ["git", "merge-base", "--is-ancestor", "@{upstream}", "HEAD"]),
        ("verbose", "HEAD contains commit @{upstream}"),
        ("info", "Skipping update: Current HEAD is up-to-date or ahead of upstream."),
    ]
