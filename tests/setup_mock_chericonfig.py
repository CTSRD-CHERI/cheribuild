from pathlib import Path
from enum import Enum
from pycheribuild.utils import setCheriConfig
from pycheribuild.config.chericonfig import CrossCompileTarget, MipsFloatAbi, CheriConfig
from pycheribuild.config.loader import ConfigLoaderBase, DefaultValueOnlyConfigLoader
from pycheribuild.projects.project import SimpleProject
from pycheribuild.targets import Target

class MockArgs(object):
    targets = []

class MockActions(Enum):
    pass

class MockConfig(CheriConfig):
    def __init__(self, sourceRoot: Path):
        self.fake_loader = DefaultValueOnlyConfigLoader()
        self.fake_loader._parsedArgs = MockArgs()
        super().__init__(self.fake_loader, action_class=MockActions)
        self.default_action = ""
        self.sourceRoot = sourceRoot
        self.buildRoot = sourceRoot / "build"
        self.outputRoot = sourceRoot / "output"
        self.cheribsd_image_root = self.outputRoot
        self.pretend = True
        self.clean = True
        self.verbose = True
        self.quiet = False
        self.skipUpdate = True
        self.skipInstall = True
        self.skipClone = True
        self.skipConfigure = True
        self.forceConfigure = False
        self.includeDependencies = False
        self.create_compilation_db = False
        self.copy_compilation_db_to_source_dir = False
        self.crossCompileTarget = CrossCompileTarget.CHERI
        self.cheriBits = 256
        self.makeJobs = 2
        self.makeWithoutNice = True
        self.force_update = False
        self.force = True
        self.noLogfile = True
        self.load()

        # for the async delete test:
        self.sourceRoot = sourceRoot
        self.buildRoot = sourceRoot / "build"
        self.outputRoot = sourceRoot / "output"
        self.sdkDir = self.outputRoot / "sdk"
        self.otherToolsDir = self.outputRoot / "other"
        self.dollarPathWithOtherTools = "$PATH"

        assert self._ensureRequiredPropertiesSet()


def setup_mock_chericonfig(source_root: Path) -> MockConfig:
    config = MockConfig(source_root)
    # noinspection PyTypeChecker
    setCheriConfig(config)
    ConfigLoaderBase._cheriConfig = config
    SimpleProject._configLoader = DefaultValueOnlyConfigLoader()
    SimpleProject._configLoader._cheriConfig = config
    Target.instantiating_targets_should_warn = False
    # FIXME: There should only be one singleton instance
    return config
