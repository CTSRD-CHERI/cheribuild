from pathlib import Path
from unittest import TestCase
from pycheribuild.projects.project import Project, ExternallyManagedSourceRepository, DefaultInstallDir
from pycheribuild.config.compilation_targets import CompilationTargets
from .setup_mock_chericonfig import setup_mock_chericonfig, MockConfig
import os
import tempfile
import time
import unittest
import subprocess


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
        expected_src = config.source_root / "sources" / name  # type: Path
        self._initial_source_dir = expected_src
        expected_install = config.source_root / "install" / name  # type: Path
        self._install_dir = expected_install
        expected_build = Path(config.source_root, "build", name + "-build")  # type: Path
        self.build_dir = expected_build
        super().__init__(config, crosscompile_target=CompilationTargets.NATIVE)
        assert self.source_dir == expected_src
        assert self.build_dir == expected_build
        assert self.install_dir == expected_install
        os.makedirs(str(self.source_dir))

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

        self.assertEqual(self.tempRoot, self.config.source_root)
        self.assertEqual(self.tempRoot / "build", self.config.build_root)
        self.project = MockProject(self.config, "foo")
        assert self.project.source_dir.exists(), self.project.source_dir

    def tearDown(self):
        self._tempRoot.cleanup()

    def test_create_build_dir(self):
        assert not self.project.build_dir.exists(), self.project.build_dir
        self.project.clean()
        assert self.project.build_dir.exists(), self.project.build_dir

    def _checkStatTimesDifferent(self, path, message):
        stat = path.stat()
        self.assertNotEqual(stat.st_atime_ns, stat.st_ctime_ns, message + " -> atime and ctime should differ")

    def _checkStatTimesSame(self, path, message):
        stat = path.stat()
        self.assertEqual(stat.st_atime_ns, stat.st_ctime_ns, message + " -> atime and ctime should be the same")

    def _assertNumFiles(self, path, num_files):
        self.assertEqual(len(list(path.iterdir())), num_files, "expected %d files in %s" % (num_files, path))

    # noinspection PyUnreachableCode
    def test_keeproot(self):
        # Not sure how to test this, Linux reuses the inode number on tmpfs
        if False:
            os.makedirs(str(self.project.build_dir))
            self._checkStatTimesSame(self.project.build_dir, "initial created")
            time.sleep(.05)
            (self.project.build_dir / "something").mkdir()
            self._checkStatTimesDifferent(self.project.build_dir, "subdir created")
            self._assertNumFiles(self.project.build_dir, 1)
            time.sleep(.05)
            self.project.clean_directory(self.project.build_dir, keep_root=True)
            self._checkStatTimesDifferent(self.project.build_dir, "subdir deleted")
            self._assertNumFiles(self.project.build_dir, 0)

            # now try again but don't keep the root
            time.sleep(.05)
            (self.project.build_dir / "something").mkdir()
            self._checkStatTimesDifferent(self.project.build_dir, "subdir created")
            time.sleep(.05)
            self._assertNumFiles(self.project.build_dir, 1)
            self.project.clean_directory(self.project.build_dir, keep_root=False)
            time.sleep(.05)
            self._assertNumFiles(self.project.build_dir, 0)
            self._checkStatTimesSame(self.project.build_dir, "dir recreated")

    def _assertDirEmpty(self, path):
        assert path.is_dir(), str(path) + "doesn't exist!"
        self._assertNumFiles(path, 0)

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
            self._assertDirEmpty(self.project.build_dir)  # build directory should be available immediately and be empty
            # should take 1 second before the deleting starts
            if tmpdir_expected:
                assert moved_builddir.exists(), "tmpdir should exist"
                self._assertNumFiles(moved_builddir, 1)
            else:
                assert not moved_builddir.exists()  # tempdir should be deleted now
        self._assertDirEmpty(self.project.build_dir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now

    def test_async_delete_build_dir(self):
        subdir = self.project.build_dir / "subdir"
        moved_builddir = self.project.build_dir.with_suffix(".delete-me-pls")
        os.makedirs(str(subdir))
        self.config.sleep_before_delete = True
        assert not moved_builddir.exists()

        # default test: full build dir
        self._check_async_delete("non-empty buildir, no tmpdir", tmpdir_expected=True)

        # now check that it also works if the dir is empty, we just don't create a new dir
        self._assertDirEmpty(self.project.build_dir)
        assert not moved_builddir.exists()
        self._check_async_delete("empty buildir, no tmpdir", tmpdir_expected=False)

        # now check that it also works if the dir does not exist yet
        self._assertDirEmpty(self.project.build_dir)
        self.project.build_dir.rmdir()
        assert not self.project.build_dir.exists(), self.project.build_dir
        self._check_async_delete("missing build dir, no tmpdir", tmpdir_expected=False)

        # now try that it also works even if builddir and tempdir still exists (e.g. from a previous crashed run)
        self._assertDirEmpty(self.project.build_dir)
        os.makedirs(str(moved_builddir / "subdir"))
        self._assertNumFiles(moved_builddir, 1)
        subdir.mkdir()
        self._assertNumFiles(self.project.build_dir, 1)
        self._check_async_delete("non-empty buildir, tmpdir exists", tmpdir_expected=True)

        # same with an empty builddir and tempdir still exists (e.g. from a previous crashed run)
        self._assertDirEmpty(self.project.build_dir)
        os.makedirs(str(moved_builddir / "subdir"))
        self._assertNumFiles(moved_builddir, 1)
        self._check_async_delete("empty buildir, tmpdir exists", tmpdir_expected=True)

        # now try that it also works even if the tempdir still exists and builddir is missing
        os.makedirs(str(moved_builddir / "subdir"))
        self.project.build_dir.rmdir()
        self._assertNumFiles(moved_builddir, 1)
        assert not self.project.build_dir.exists(), self.project.build_dir
        self._check_async_delete("missing builddir, tmpdir exists", tmpdir_expected=True)

    def test_async_delete_keep_root(self):
        subdir = self.project.build_dir / "subdir"
        subdir2 = self.project.build_dir / "subdir2"
        subdir3 = self.project.build_dir / "subdir3"
        os.makedirs(str(subdir))
        os.makedirs(str(subdir2))
        os.makedirs(str(subdir3))
        moved_builddir = self.project.build_dir.with_suffix(".delete-me-pls")
        self.config.sleep_before_delete = True
        assert not moved_builddir.exists()

        # default test: full build dir
        self._dump_dir_tree(self.config.source_root / "build", "non-empty buildir, no tmpdir, keep root")
        with self.project.async_clean_directory(self.project.build_dir, keep_root=True):
            self._assertDirEmpty(self.project.build_dir)  # build directory should be available immediately and be empty
            # should take 1 second before the deleting starts
            assert moved_builddir.exists(), "tmpdir should exist"
            self._assertNumFiles(moved_builddir, 3)
        self._assertDirEmpty(self.project.build_dir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now

        # now try again with existing moved tempdir
        os.makedirs(str(moved_builddir / "subdir"))
        os.makedirs(str(subdir))
        os.makedirs(str(subdir2))
        os.makedirs(str(subdir3))
        self._dump_dir_tree(self.config.source_root / "build", "non-empty buildir, with tmpdir, keep root")
        with self.project.async_clean_directory(self.project.build_dir, keep_root=True):
            self._assertDirEmpty(self.project.build_dir)  # build directory should be available immediately and be empty
            # should take 1 second before the deleting starts
            assert moved_builddir.exists(), "tmpdir should exist"
            self._assertNumFiles(moved_builddir, 3)
            self._assertNumFiles(self.project.build_dir, 0)

        self._assertDirEmpty(self.project.build_dir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now


if __name__ == '__main__':
    unittest.main()
