from pathlib import Path
from unittest import TestCase
from pycheribuild.projects.project import Project, CompilationTargets, ExternallyManagedSourceRepository
from pycheribuild.utils import setCheriConfig, IS_LINUX
from .setup_mock_chericonfig import setup_mock_chericonfig, MockConfig
import os
import tempfile
import time
import unittest
import subprocess


# noinspection PyTypeChecker
class MockProject(Project):
    doNotAddToTargets = True
    project_name = "FAKE"
    target = "FAKE"
    _should_not_be_instantiated = False
    repository = ExternallyManagedSourceRepository()

    def __init__(self, config: MockConfig, name: str):
        self._crossCompileTarget = CompilationTargets.NATIVE
        self.project_name = name
        expected_src = config.sourceRoot / "sources" / name  # type: Path
        self.default_source_dir = expected_src
        expected_install = config.sourceRoot / "install" / name  # type: Path
        self._installDir = expected_install
        expected_build = Path(config.sourceRoot, "build", name + "-build")  # type: Path
        self.buildDir = expected_build
        super().__init__(config)
        assert self.default_source_dir == expected_src
        assert self.sourceDir == expected_src
        assert self.buildDir == expected_build
        assert self.installDir == expected_install
        os.makedirs(str(self.sourceDir))

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
        self.config = setup_mock_chericonfig(self.tempRoot)
        self.config.pretend = False
        self.config.sleep_before_delete = False

        self.assertEqual(self.tempRoot, self.config.sourceRoot)
        self.assertEqual(self.tempRoot / "build", self.config.buildRoot)
        self.project = MockProject(self.config, "foo")
        assert self.project.sourceDir.exists(), self.project.sourceDir

    def tearDown(self):
        self._tempRoot.cleanup()

    def test_create_build_dir(self):
        assert not self.project.buildDir.exists(), self.project.buildDir
        self.project.clean()
        assert self.project.buildDir.exists(), self.project.buildDir

    def _checkStatTimesDifferent(self, path, message):
        stat = path.stat()
        self.assertNotEqual(stat.st_atime_ns, stat.st_ctime_ns, message + " -> atime and ctime should differ")

    def _checkStatTimesSame(self, path, message):
        stat = path.stat()
        self.assertEqual(stat.st_atime_ns, stat.st_ctime_ns, message + " -> atime and ctime should be the same")

    def _assertNumFiles(self, path, numFiles):
        self.assertEqual(len(list(path.iterdir())), numFiles, "expected %d files in %s" % (numFiles, path))

    def test_keeproot(self):
        # Not sure how to test this, Linux reuses the inode number on tmpfs
        if False:
            os.makedirs(str(self.project.buildDir))
            self._checkStatTimesSame(self.project.buildDir, "initial created")
            time.sleep(.05)
            (self.project.buildDir / "something").mkdir()
            self._checkStatTimesDifferent(self.project.buildDir, "subdir created")
            self._assertNumFiles(self.project.buildDir, 1)
            time.sleep(.05)
            self.project.clean_directory(self.project.buildDir, keep_root=True)
            self._checkStatTimesDifferent(self.project.buildDir, "subdir deleted")
            self._assertNumFiles(self.project.buildDir, 0)

            # now try again but don't keep the root
            time.sleep(.05)
            (self.project.buildDir / "something").mkdir()
            self._checkStatTimesDifferent(self.project.buildDir, "subdir created")
            time.sleep(.05)
            self._assertNumFiles(self.project.buildDir, 1)
            self.project.clean_directory(self.project.buildDir, keep_root=False)
            time.sleep(.05)
            self._assertNumFiles(self.project.buildDir, 0)
            self._checkStatTimesSame(self.project.buildDir, "dir recreated")

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

    def _check_async_delete(self, message, tmpdirExpected: bool):
        self._dump_dir_tree(self.config.sourceRoot / "build", message)
        moved_builddir = self.project.buildDir.with_suffix(".delete-me-pls")
        with self.project.async_clean_directory(self.project.buildDir):
            self._assertDirEmpty(self.project.buildDir)  # build directory should be available immediately and be empty
            # should take 1 second before the deleting starts
            if tmpdirExpected:
                assert moved_builddir.exists(), "tmpdir should exist"
                self._assertNumFiles(moved_builddir, 1)
            else:
                assert not moved_builddir.exists()  # tempdir should be deleted now
        self._assertDirEmpty(self.project.buildDir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now

    def test_async_delete_build_dir(self):
        subdir = self.project.buildDir / "subdir"
        moved_builddir = self.project.buildDir.with_suffix(".delete-me-pls")
        os.makedirs(str(subdir))
        self.config.sleep_before_delete = True
        assert not moved_builddir.exists()

        # default test: full build dir
        self._check_async_delete("non-empty buildir, no tmpdir", tmpdirExpected=True)

        # now check that it also works if the dir is empty, we just don't create a new dir
        self._assertDirEmpty(self.project.buildDir)
        assert not moved_builddir.exists()
        self._check_async_delete("empty buildir, no tmpdir", tmpdirExpected=False)

        # now check that it also works if the dir does not exist yet
        self._assertDirEmpty(self.project.buildDir)
        self.project.buildDir.rmdir()
        assert not self.project.buildDir.exists(), self.project.buildDir
        self._check_async_delete("missing build dir, no tmpdir", tmpdirExpected=False)

        # now try that it also works even if builddir and tempdir still exists (e.g. from a previous crashed run)
        self._assertDirEmpty(self.project.buildDir)
        os.makedirs(str(moved_builddir / "subdir"))
        self._assertNumFiles(moved_builddir, 1)
        subdir.mkdir()
        self._assertNumFiles(self.project.buildDir, 1)
        self._check_async_delete("non-empty buildir, tmpdir exists", tmpdirExpected=True)

        # same with an empty builddir and tempdir still exists (e.g. from a previous crashed run)
        self._assertDirEmpty(self.project.buildDir)
        os.makedirs(str(moved_builddir / "subdir"))
        self._assertNumFiles(moved_builddir, 1)
        self._check_async_delete("empty buildir, tmpdir exists", tmpdirExpected=True)

        # now try that it also works even if the tempdir still exists and builddir is missing
        os.makedirs(str(moved_builddir / "subdir"))
        self.project.buildDir.rmdir()
        self._assertNumFiles(moved_builddir, 1)
        assert not self.project.buildDir.exists(), self.project.buildDir
        self._check_async_delete("missing builddir, tmpdir exists", tmpdirExpected=True)

    def test_async_delete_keep_root(self):
        subdir = self.project.buildDir / "subdir"
        subdir2 = self.project.buildDir / "subdir2"
        subdir3 = self.project.buildDir / "subdir3"
        os.makedirs(str(subdir))
        os.makedirs(str(subdir2))
        os.makedirs(str(subdir3))
        moved_builddir = self.project.buildDir.with_suffix(".delete-me-pls")
        self.config.sleep_before_delete = True
        assert not moved_builddir.exists()

        # default test: full build dir
        self._dump_dir_tree(self.config.sourceRoot / "build", "non-empty buildir, no tmpdir, keep root")
        with self.project.async_clean_directory(self.project.buildDir, keep_root=True):
            self._assertDirEmpty(self.project.buildDir)  # build directory should be available immediately and be empty
            # should take 1 second before the deleting starts
            assert moved_builddir.exists(), "tmpdir should exist"
            self._assertNumFiles(moved_builddir, 3)
        self._assertDirEmpty(self.project.buildDir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now

        # now try again with existing moved tempdir
        os.makedirs(str(moved_builddir / "subdir"))
        os.makedirs(str(subdir))
        os.makedirs(str(subdir2))
        os.makedirs(str(subdir3))
        self._dump_dir_tree(self.config.sourceRoot / "build", "non-empty buildir, with tmpdir, keep root")
        with self.project.async_clean_directory(self.project.buildDir, keep_root=True):
            self._assertDirEmpty(self.project.buildDir)  # build directory should be available immediately and be empty
            # should take 1 second before the deleting starts
            assert moved_builddir.exists(), "tmpdir should exist"
            self._assertNumFiles(moved_builddir, 3)
            self._assertNumFiles(self.project.buildDir, 0)

        self._assertDirEmpty(self.project.buildDir)  # dir should still be empty
        assert not moved_builddir.exists()  # tempdir should be deleted now



if __name__ == '__main__':
    unittest.main()
