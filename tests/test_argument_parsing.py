import sys
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

sys.path.append(str(Path(__file__).parent.parent))

# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from pycheribuild.configloader import ConfigLoader here because that will only update the local copy
from pycheribuild.config.loader import ConfigLoaderBase, JsonAndCommandLineConfigLoader

_loader = JsonAndCommandLineConfigLoader()
from pycheribuild.projects.project import SimpleProject

SimpleProject._configLoader = _loader
from pycheribuild.targets import targetManager
from pycheribuild.config.defaultconfig import DefaultCheriConfig
# noinspection PyUnresolvedReferences
from pycheribuild.projects import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.cross import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.disk_image import BuildCheriBSDDiskImage

_targets_registered = False


class TestArgumentParsing(TestCase):

    @staticmethod
    def _parse_arguments(args, *, config_file=Path("/this/does/not/exist")):
        global _targets_registered
        global _cheriConfig
        if not _targets_registered:
            allTargetNames = list(sorted(targetManager.targetNames)) + ["__run_everything__"]
            ConfigLoaderBase._cheriConfig = DefaultCheriConfig(_loader, allTargetNames)
            SimpleProject._configLoader = _loader
            targetManager.registerCommandLineOptions()
            _targets_registered = True
        ConfigLoaderBase._cheriConfig.loader._configPath = config_file
        sys.argv = ["cheribuild.py"] + args
        ConfigLoaderBase._cheriConfig.loader.reload()
        # pprint.pprint(vars(ret))
        assert ConfigLoaderBase._cheriConfig
        return ConfigLoaderBase._cheriConfig

    def test_skip_update(self):
        # default is false:
        self.assertFalse(self._parse_arguments(["--skip-configure"]).skipUpdate)
        # check that --no-foo and --foo work:
        self.assertTrue(self._parse_arguments(["--skip-update"]).skipUpdate)
        self.assertFalse(self._parse_arguments(["--no-skip-update"]).skipUpdate)
        # check config file
        with tempfile.NamedTemporaryFile() as t:
            config = Path(t.name)
            config.write_bytes(b'{ "skip-update": true}')
            self.assertTrue(self._parse_arguments([], config_file=config).skipUpdate)
            # command line overrides config file:
            self.assertTrue(self._parse_arguments(["--skip-update"], config_file=config).skipUpdate)
            self.assertFalse(self._parse_arguments(["--no-skip-update"], config_file=config).skipUpdate)
            config.write_bytes(b'{ "skip-update": false}')
            self.assertFalse(self._parse_arguments([], config_file=config).skipUpdate)
            # command line overrides config file:
            self.assertTrue(self._parse_arguments(["--skip-update"], config_file=config).skipUpdate)
            self.assertFalse(self._parse_arguments(["--no-skip-update"], config_file=config).skipUpdate)

    def test_per_project_override(self):
        config = self._parse_arguments(["--skip-configure"])
        source_root = config.sourceRoot
        print(BuildCheriBSDDiskImage.extraFilesDir)
        self.assertEqual(BuildCheriBSDDiskImage.extraFilesDir, source_root / "extra-files")
        self._parse_arguments(["--disk-image/extra-files=/foo/bar"])
        self.assertEqual(BuildCheriBSDDiskImage.extraFilesDir, Path("/foo/bar/"))
        self._parse_arguments(["--disk-image/extra-files", "/bar/foo"])
        self.assertEqual(BuildCheriBSDDiskImage.extraFilesDir, Path("/bar/foo/"))
        # different source root should affect the value:
        self._parse_arguments(["--source-root=/tmp"])
        self.assertEqual(BuildCheriBSDDiskImage.extraFilesDir, Path("/tmp/extra-files"))

        with tempfile.NamedTemporaryFile() as t:
            config = Path(t.name)
            config.write_bytes(b'{ "source-root": "/x"}')
            self._parse_arguments([], config_file=config)
            self.assertEqual(BuildCheriBSDDiskImage.extraFilesDir, Path("/x/extra-files"))

            # check that source root can be overridden
            self._parse_arguments(["--source-root=/y"])
            self.assertEqual(BuildCheriBSDDiskImage.extraFilesDir, Path("/y/extra-files"))

    def _get_config_with_include(self, tmpdir: Path, config_json: bytes, workdir: Path = None):
        if not workdir:
            workdir = tmpdir
        config = workdir / "config.json"
        config.write_bytes(config_json)
        return self._parse_arguments([], config_file=config)

    def test_config_file_include(self):
        with tempfile.TemporaryDirectory() as d:
            config_dir = Path(d)
            (config_dir / "128-common.json").write_bytes(b'{ "cheri-bits": 128 }')
            (config_dir / "256-common.json").write_bytes(b'{ "cheri-bits": 256 }')
            (config_dir / "common.json").write_bytes(b'{ "source-root": "/this/is/a/unit/test" }')

            # Check that the config file is parsed:
            result = self._get_config_with_include(config_dir, b'{ "#include": "common.json"}')
            self.assertEqual("/this/is/a/unit/test", str(result.sourceRoot))

            # Check that the current file always has precendence
            result = self._get_config_with_include(config_dir, b'{ "#include": "256-common.json", "cheri-bits": 128}')
            self.assertEqual(128, result.cheriBits)
            result = self._get_config_with_include(config_dir, b'{ "#include": "128-common.json", "cheri-bits": 256}')
            self.assertEqual(256, result.cheriBits)
            # order doesn't matter since the #include is only evaluated after the whole file has been parsed:
            result = self._get_config_with_include(config_dir, b'{ "cheri-bits": 128, "#include": "256-common.json"}')
            self.assertEqual(128, result.cheriBits)
            result = self._get_config_with_include(config_dir, b'{ "cheri-bits": 256, "#include": "128-common.json"}')
            self.assertEqual(256, result.cheriBits)

            # TODO: handled nested cases: the level closest to the initial file wins
            (config_dir / "change-source-root.json").write_bytes(
                b'{ "source-root": "/source/root/override", "#include": "common.json" }')
            result = self._get_config_with_include(config_dir, b'{ "#include": "change-source-root.json"}')
            self.assertEqual("/source/root/override", str(result.sourceRoot))
            # And again the root file wins:
            result = self._get_config_with_include(config_dir,
                                                   b'{ "source-root": "/override/twice", "#include": "change-source-root.json"}')
            self.assertEqual("/override/twice", str(result.sourceRoot))
            # no matter in which order it is written:
            result = self._get_config_with_include(config_dir,
                                                   b'{ "#include": "change-source-root.json", "source-root": "/override/again"}')
            self.assertEqual("/override/again", str(result.sourceRoot))

            with tempfile.TemporaryDirectory() as d2:
                # Check that relative paths work
                relpath = b"../" + str(Path(d).relative_to(Path(d2).parent)).encode("utf-8")
                result = self._get_config_with_include(config_dir,
                                                       b'{ "#include": "' + relpath + b'/common.json" }', workdir=Path(d2))
                self.assertEqual("/this/is/a/unit/test", str(result.sourceRoot))

                # Check that absolute paths work as expected:
                abspath = b"" + str(Path(d)).encode("utf-8")
                result = self._get_config_with_include(config_dir,
                                                       b'{ "#include": "' + abspath + b'/common.json" }', workdir=Path(d2))
                self.assertEqual("/this/is/a/unit/test", str(result.sourceRoot))

            # Nonexistant paths should raise an error
            with self.assertRaisesRegex(FileNotFoundError, 'No such file or directory'):
                self._get_config_with_include(config_dir, b'{ "#include": "bad-path.json"}')


if __name__ == '__main__':
    unittest.main()
