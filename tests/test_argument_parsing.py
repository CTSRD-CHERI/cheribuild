import sys
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

sys.path.append(str(Path(__file__).parent.parent))

# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from pycheribuild.configloader import ConfigLoader here because that will only update the local copy
import pycheribuild.config.loader
pycheribuild.config.loader.setConfigLoader(pycheribuild.config.loader.JsonAndCommandLineConfigLoader())

from pycheribuild.targets import targetManager
from pycheribuild.config.defaultconfig import CheriConfig
# noinspection PyUnresolvedReferences
from pycheribuild.projects import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.cross import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.disk_image import BuildCheriBSDDiskImage


_targets_registered = False


class TestArgumentParsing(TestCase):

    @staticmethod
    def _parse_arguments(args, *, config_file=Path("/this/does/not/exist")):
        global _targets_registered
        if not _targets_registered:
            targetManager.registerCommandLineOptions()
            _targets_registered = True
        pycheribuild.config.loader.ConfigLoader._configPath = config_file
        pycheribuild.config.loader.ConfigLoader.reload()
        sys.argv = ["cheribuild.py"] + args
        allTargetNames = list(sorted(targetManager.targetNames)) + ["__run_everything__"]
        ret = CheriConfig(allTargetNames)
        # pprint.pprint(vars(ret))
        return ret

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



if __name__ == '__main__':
    unittest.main()
