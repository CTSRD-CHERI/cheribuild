import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import TestCase

from pycheribuild.config.compilation_targets import CompilationTargets
from pycheribuild.projects.project import DefaultInstallDir, ExternallyManagedSourceRepository, Project
from .setup_mock_chericonfig import MockConfig, setup_mock_chericonfig


# noinspection PyTypeChecker
class MockProject(Project):
    do_not_add_to_targets = True
    target = "FAKE"
    default_directory_basename = "FAKE"
    _xtarget = CompilationTargets.NATIVE
    _should_not_be_instantiated = False
    default_install_dir = DefaultInstallDir.CUSTOM_INSTALL_DIR
    repository = ExternallyManagedSourceRepository()

    def __init__(self, config: MockConfig, name: str):
        self.target = name
        self.default_directory_basename = name
        self.expected_src = config.source_root / "sources" / name
        self._initial_source_dir = self.expected_src
        self.expected_install = config.source_root / "install" / name
        self._install_dir = self.expected_install
        self.expected_build = Path(config.source_root, "build", name + "-build")
        self.build_dir = self.expected_build
        super().__init__(config, crosscompile_target=CompilationTargets.NATIVE)

    def setup(self):
        super().setup()
        assert self.source_dir == self.expected_src
        assert self.build_dir == self.expected_build
        assert self.install_dir == self.expected_install
        self.source_dir.mkdir(parents=True)

    @classmethod
    def cached_full_dependencies(cls):
        return []

    def _delete_directories(self, *dirs):
        if self.config.sleep_before_delete:
            print("SLEEPING")
            time.sleep(0.05)
        super()._delete_directories(*dirs)


class TestAsyncDelete(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = setup_mock_chericonfig(Path("/invalid/path"))
        MockProject.setup_config_options()

    def setUp(self):
        self._tempRoot = tempfile.TemporaryDirectory()
        self.tempRoot = Path(self._tempRoot.name)
        self.config = setup_mock_chericonfig(self.tempRoot, pretend=False)
        self.config.sleep_before_delete = False

        assert self.tempRoot == self.config.source_root
        assert self.tempRoot / "build" == self.config.build_root
        self.project = MockProject(self.config, "foo")
        self.project.setup()
        assert self.project.source_dir.exists(), self.project.source_dir

    def tearDown(self):
        self._tempRoot.cleanup()

    def test_create_build_dir(self):
        assert not self.project.build_dir.exists(), self.project.build_dir
        self.project.clean()
        assert self.project.build_dir.exists(), self.project.build_dir

    def _check_stat_times_different(self, path, message):
        stat = path.stat()
        assert stat.st_atime_ns != stat.st_ctime_ns, message + " -> atime and ctime should differ"

    def _check_stat_times_same(self, path, message):
        stat = path.stat()
        assert stat.st_atime_ns == stat.st_ctime_ns, message + " -> atime and ctime should be the same"

    def _assert_num_files(self, path, num_files):
        assert len(list(path.iterdir())) == num_files, "expected %d files in %s" % (num_files, path)

    # noinspection PyUnreachableCode
    def test_keeproot(self):
        # Not sure how to test this, Linux reuses the inode number on tmpfs
        if False:
            self.project.build_dir.mkdir(parents=True)
            self._check_stat_times_same(self.project.build_dir, "initial created")
            time.sleep(0.05)
            (self.project.build_dir / "something").mkdir()
            self._check_stat_times_different(self.project.build_dir, "subdir created")
            self._assert_num_files(self.project.build_dir, 1)
            time.sleep(0.05)
            self.project.clean_directory(self.project.build_dir, keep_root=True)
            self._check_stat_times_different(self.project.build_dir, "subdir deleted")
            self._assert_num_files(self.project.build_dir, 0)

            # now try again but don't keep the root
            time.sleep(0.05)
            (self.project.build_dir / "something").mkdir()
            self._check_stat_times_different(self.project.build_dir, "subdir created")
            time.sleep(0.05)
            self._assert_num_files(self.project.build_dir, 1)
            self.project.clean_directory(self.project.build_dir, keep_root=False)
            time.sleep(0.05)
            self._assert_num_files(self.project.build_dir, 0)
            self._check_stat_times_same(self.project.build_dir, "dir recreated")

    def _assert_dir_empty(self, path):
        assert path.is_dir(), str(path) + "doesn't exist!"
        self._assert_num_files(path, 0)

    @staticmethod
    def _dump_dir_tree(directory: Path, message: str):
        print("State for test", message)
        if not directory.exists():
            print("(nonexistant)", directory)
        files = subprocess.check_output(["find", str(directory)]).rstrip().decode("utf-8").split("\n")
        print("   ", "\n    ".join(files))

    def _check_async_delete(self, message, tmpdir_expected: bool):
        self._dump_dir_tree(self.config.source_root / "build", message)
        moved_builddir = self.project.build_dir.with_suffix(".delete-me-pls")
        with self.project.async_clean_directory(self.project.build_dir):
            # build directory should be available immediately and be empty
            self._assert_dir_empty(self.project.build_dir)
            # should take 1 second before the deleting starts
            if tmpdir_expected:
                assert moved_builddir.exists(), "tmpdir should exist"
                self._assert_num_files(moved_builddir, 1)
            else:
                assert not moved_builddir.exists()  # tempdir should be deleted now
        self._assert_dir_empty(self.project.build_dir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now

    def test_async_delete_build_dir(self):
        subdir = self.project.build_dir / "subdir"
        moved_builddir = self.project.build_dir.with_suffix(".delete-me-pls")
        subdir.mkdir(parents=True)
        self.config.sleep_before_delete = True
        assert not moved_builddir.exists()

        # default test: full build dir
        self._check_async_delete("non-empty buildir, no tmpdir", tmpdir_expected=True)

        # now check that it also works if the dir is empty, we just don't create a new dir
        self._assert_dir_empty(self.project.build_dir)
        assert not moved_builddir.exists()
        self._check_async_delete("empty buildir, no tmpdir", tmpdir_expected=False)

        # now check that it also works if the dir does not exist yet
        self._assert_dir_empty(self.project.build_dir)
        self.project.build_dir.rmdir()
        assert not self.project.build_dir.exists(), self.project.build_dir
        self._check_async_delete("missing build dir, no tmpdir", tmpdir_expected=False)

        # now try that it also works even if builddir and tempdir still exists (e.g. from a previous crashed run)
        self._assert_dir_empty(self.project.build_dir)
        (moved_builddir / "subdir").mkdir(parents=True)
        self._assert_num_files(moved_builddir, 1)
        subdir.mkdir()
        self._assert_num_files(self.project.build_dir, 1)
        self._check_async_delete("non-empty buildir, tmpdir exists", tmpdir_expected=True)

        # same with an empty builddir and tempdir still exists (e.g. from a previous crashed run)
        self._assert_dir_empty(self.project.build_dir)
        (moved_builddir / "subdir").mkdir(parents=True)
        self._assert_num_files(moved_builddir, 1)
        self._check_async_delete("empty buildir, tmpdir exists", tmpdir_expected=True)

        # now try that it also works even if the tempdir still exists and builddir is missing
        (moved_builddir / "subdir").mkdir(parents=True)
        self.project.build_dir.rmdir()
        self._assert_num_files(moved_builddir, 1)
        assert not self.project.build_dir.exists(), self.project.build_dir
        self._check_async_delete("missing builddir, tmpdir exists", tmpdir_expected=True)

    def test_async_delete_keep_root(self):
        subdir = self.project.build_dir / "subdir"
        subdir2 = self.project.build_dir / "subdir2"
        subdir3 = self.project.build_dir / "subdir3"
        subdir.mkdir(parents=True)
        subdir2.mkdir(parents=True)
        subdir3.mkdir(parents=True)
        moved_builddir = self.project.build_dir.with_suffix(".delete-me-pls")
        self.config.sleep_before_delete = True
        assert not moved_builddir.exists()

        # default test: full build dir
        self._dump_dir_tree(self.config.source_root / "build", "non-empty buildir, no tmpdir, keep root")
        with self.project.async_clean_directory(self.project.build_dir, keep_root=True):
            # build directory should be available immediately and be empty
            self._assert_dir_empty(self.project.build_dir)
            # should take 1 second before the deleting starts
            assert moved_builddir.exists(), "tmpdir should exist"
            self._assert_num_files(moved_builddir, 3)
        self._assert_dir_empty(self.project.build_dir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now

        # now try again with existing moved tempdir
        (moved_builddir / "subdir").mkdir(parents=True)
        subdir.mkdir(parents=True)
        subdir2.mkdir(parents=True)
        subdir3.mkdir(parents=True)
        self._dump_dir_tree(self.config.source_root / "build", "non-empty buildir, with tmpdir, keep root")
        with self.project.async_clean_directory(self.project.build_dir, keep_root=True):
            # build directory should be available immediately and be empty
            self._assert_dir_empty(self.project.build_dir)
            # should take 1 second before the deleting starts
            assert moved_builddir.exists(), "tmpdir should exist"
            self._assert_num_files(moved_builddir, 3)
            self._assert_num_files(self.project.build_dir, 0)

        self._assert_dir_empty(self.project.build_dir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now


if __name__ == "__main__":
    unittest.main()
