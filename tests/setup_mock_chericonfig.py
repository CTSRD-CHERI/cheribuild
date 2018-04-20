from pathlib import Path
from pycheribuild.utils import setCheriConfig
from pycheribuild.config.chericonfig import CrossCompileTarget
from pycheribuild.config.loader import ConfigLoaderBase
from pycheribuild.projects.project import SimpleProject

class MockConfig(object):
    def __init__(self, sourceRoot: Path):
        self.pretend = False
        self.clean = True
        self.verbose = True
        self.quiet = False
        self.skipUpdate = True
        self.skipInstall = True
        self.createCompilationDB = False
        self.sourceRoot = sourceRoot
        # for the test:
        self.sleep_before_delete = False
        self.use_sdk_clang_for_native_xbuild = False
        self.crossCompileTarget = CrossCompileTarget.CHERI


def setup_mock_chericonfig(source_root: Path) -> MockConfig:
    config = MockConfig(source_root)
    # noinspection PyTypeChecker
    setCheriConfig(config)
    ConfigLoaderBase._cheriConfig = config
    SimpleProject._configLoader._cheriConfig = config
    # FIXME: There should only be one singleton instance
    return config
