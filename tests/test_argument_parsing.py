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
from pycheribuild.projects.cross.qt5 import BuildQtBase

_targets_registered = False

try:
    import typing
except ImportError:
    typing = {}


# python 3.4 compatibility
def write_bytes(path: Path, contents: bytes):
    with path.open(mode='wb') as f:
        return f.write(contents)

class TestArgumentParsing(TestCase):
    @staticmethod
    def _parse_arguments(args, *, config_file=Path("/this/does/not/exist")) -> DefaultCheriConfig:
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

    @staticmethod
    def _parse_config_file_and_args(config_file_contents: bytes, *args) -> DefaultCheriConfig:
        with tempfile.NamedTemporaryFile() as t:
            config = Path(t.name)
            write_bytes(config, config_file_contents)
            return TestArgumentParsing._parse_arguments(list(args), config_file=config)

    def test_skip_update(self):
        # default is false:
        self.assertFalse(self._parse_arguments(["--skip-configure"]).skipUpdate)
        # check that --no-foo and --foo work:
        self.assertTrue(self._parse_arguments(["--skip-update"]).skipUpdate)
        self.assertFalse(self._parse_arguments(["--no-skip-update"]).skipUpdate)
        # check config file
        with tempfile.NamedTemporaryFile() as t:
            config = Path(t.name)
            write_bytes(config, b'{ "skip-update": true}')
            self.assertTrue(self._parse_arguments([], config_file=config).skipUpdate)
            # command line overrides config file:
            self.assertTrue(self._parse_arguments(["--skip-update"], config_file=config).skipUpdate)
            self.assertFalse(self._parse_arguments(["--no-skip-update"], config_file=config).skipUpdate)
            write_bytes(config, b'{ "skip-update": false}')
            self.assertFalse(self._parse_arguments([], config_file=config).skipUpdate)
            # command line overrides config file:
            self.assertTrue(self._parse_arguments(["--skip-update"], config_file=config).skipUpdate)
            self.assertFalse(self._parse_arguments(["--no-skip-update"], config_file=config).skipUpdate)

    def test_per_project_override(self):
        config = self._parse_arguments(["--skip-configure"])
        source_root = config.sourceRoot
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
            write_bytes(config, b'{ "source-root": "/x"}')
            self._parse_arguments([], config_file=config)
            self.assertEqual(BuildCheriBSDDiskImage.extraFilesDir, Path("/x/extra-files"))

            # check that source root can be overridden
            self._parse_arguments(["--source-root=/y"])
            self.assertEqual(BuildCheriBSDDiskImage.extraFilesDir, Path("/y/extra-files"))

    def test_cross_compile_project_inherits(self):
        # Parse args once to ensure targetManager is initialized
        self._parse_arguments(["--skip-configure"])
        qtbase_default = targetManager.get_target("qtbase").projectClass  # type: typing.Type[BuildQtBase]
        qtbase_native = targetManager.get_target("qtbase-native").projectClass  # type: typing.Type[BuildQtBase]
        qtbase_mips = targetManager.get_target("qtbase-mips").projectClass  # type: typing.Type[BuildQtBase]

        # Check that project name is the same:
        self.assertEqual(qtbase_default.projectName, qtbase_native.projectName)
        self.assertEqual(qtbase_mips.projectName, qtbase_native.projectName)
        # These classes were generated:
        self.assertEqual(qtbase_native.synthetic_base, qtbase_default)
        self.assertEqual(qtbase_mips.synthetic_base, qtbase_default)
        self.assertFalse(hasattr(qtbase_default, "synthetic_base"))

        # Now check a property that should be inherited:
        self._parse_arguments(["--qtbase-native/build-tests"])
        self.assertFalse(qtbase_default.build_tests, "qtbase-default build-tests should default to false")
        self.assertTrue(qtbase_native.build_tests, "qtbase-native build-tests should be set on cmdline")
        self.assertFalse(qtbase_mips.build_tests, "qtbase-mips build-tests should default to false")
        # If the base qtbase option is set but no per-target one use the basic one:
        self._parse_arguments(["--qtbase/build-tests"])
        self.assertTrue(qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline")
        self.assertTrue(qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)")
        self.assertTrue(qtbase_mips.build_tests, "qtbase-mips should inherit build-tests from qtbase(default)")

        # But target-specific ones should override
        self._parse_arguments(["--qtbase/build-tests", "--qtbase-mips/no-build-tests"])
        self.assertTrue(qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline")
        self.assertTrue(qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)")
        self.assertFalse(qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests")

        # Check that we hav ethe same behaviour when loading from json:
        self._parse_config_file_and_args(b'{"qtbase-native/build-tests": true }')
        self.assertFalse(qtbase_default.build_tests, "qtbase-default build-tests should default to false")
        self.assertTrue(qtbase_native.build_tests, "qtbase-native build-tests should be set on cmdline")
        self.assertFalse(qtbase_mips.build_tests, "qtbase-mips build-tests should default to false")
        # If the base qtbase option is set but no per-target one use the basic one:
        self._parse_config_file_and_args(b'{"qtbase/build-tests": true }')
        self.assertTrue(qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline")
        self.assertTrue(qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)")
        self.assertTrue(qtbase_mips.build_tests, "qtbase-mips should inherit build-tests from qtbase(default)")

        # But target-specific ones should override
        self._parse_config_file_and_args(b'{"qtbase/build-tests": true, "qtbase-mips/build-tests": false }')
        self.assertTrue(qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline")
        self.assertTrue(qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)")
        self.assertFalse(qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests")

        # And that cmdline still overrides JSON:
        self._parse_config_file_and_args(b'{"qtbase/build-tests": true }', "--qtbase-mips/no-build-tests")
        self.assertTrue(qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline")
        self.assertTrue(qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)")
        self.assertFalse(qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests")
        # But if a per-target option is set in the json that still overrides the default set on the cmdline
        self._parse_config_file_and_args(b'{"qtbase-mips/build-tests": false }', "--qtbase/build-tests")
        self.assertTrue(qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline")
        self.assertTrue(qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)")
        self.assertFalse(qtbase_mips.build_tests, "qtbase-mips should have a JSON false override for build-tests")

        # However, don't inherit for buildDir since that doesn't make sense:
        def assertBuildDirsDifferent():
            # Default should be CHERI purecap
            # print("Default build dir:", qtbase_default.buildDir)
            # print("Native build dir:", qtbase_native.buildDir)
            # print("Mips build dir:", qtbase_mips.buildDir)
            self.assertNotEqual(qtbase_default.buildDir, qtbase_native.buildDir)
            self.assertNotEqual(qtbase_default.buildDir, qtbase_mips.buildDir)
            self.assertNotEqual(qtbase_mips.buildDir, qtbase_native.buildDir)

        assertBuildDirsDifferent()
        # overriding native build dir is fine:
        self._parse_arguments(["--qtbase-native/build-directory=/foo/bar"])
        assertBuildDirsDifferent()
        self._parse_config_file_and_args(b'{"qtbase-native/build-directory": "/foo/bar"}')
        assertBuildDirsDifferent()
        # Should not inherit from the default one:
        self._parse_arguments(["--qtbase/build-directory=/foo/bar"])
        assertBuildDirsDifferent()
        self._parse_config_file_and_args(b'{"qtbase/build-directory": "/foo/bar"}')
        assertBuildDirsDifferent()

        # Should not inherit from the default one:
        self._parse_arguments(["--qtbase/build-directory=/foo/bar", "--qtbase-mips/build-directory=/bar/foo"])
        assertBuildDirsDifferent()
        self._parse_config_file_and_args(b'{"qtbase/build-directory": "/foo/bar",'
                                         b' "qtbase-mips/build-directory": "/bar/foo"}')
        assertBuildDirsDifferent()

    def test_duplicate_key(self):
        with self.assertRaisesRegex(SyntaxError, "duplicate key: 'cheri-bits'"):
            self._parse_config_file_and_args(b'{ "cheri-bits": 128, "some-other-key": "abc", "cheri-bits": 256 }')

    def _get_config_with_include(self, tmpdir: Path, config_json: bytes, workdir: Path = None):
        if not workdir:
            workdir = tmpdir
        config = workdir / "config.json"
        write_bytes(config, config_json)
        return self._parse_arguments([], config_file=config)

    def test_config_file_include(self):
        with tempfile.TemporaryDirectory() as d:
            config_dir = Path(d)
            write_bytes(config_dir / "128-common.json", b'{ "cheri-bits": 128 }')
            write_bytes(config_dir / "256-common.json", b'{ "cheri-bits": 256 }')
            write_bytes(config_dir / "common.json", b'{ "source-root": "/this/is/a/unit/test" }')

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
            write_bytes(config_dir / "change-source-root.json",
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

            # Currently only one #include per config file is allowed
            # TODO: this could be supported but it might be better to accept a list instead?
            with self.assertRaisesRegex(SyntaxError, "duplicate key: '#include"):
                self._get_config_with_include(config_dir, b'{ "#include": "128-common.json", "foo": "bar", "#include": "256-common.json"}')


if __name__ == '__main__':
    unittest.main()
