from pathlib import Path
from pycheribuild.utils import setCheriConfig
from pycheribuild.config.chericonfig import CrossCompileTarget, MipsFloatAbi, CheriConfig
from pycheribuild.config.loader import ConfigLoaderBase, DefaultValueOnlyConfigLoader
from pycheribuild.projects.project import SimpleProject


class MockArgs(object):
    targets = []

class MockConfig(CheriConfig):
    def __init__(self, sourceRoot: Path):
        self.fake_loader = DefaultValueOnlyConfigLoader()
        self.fake_loader._parsedArgs = MockArgs()
        super().__init__(self.fake_loader)
        self.sourceRoot = sourceRoot
        self.buildRoot = sourceRoot / "build"
        self.outputRoot = sourceRoot / "output"
        self.pretend = True
        self.clean = True
        self.verbose = True
        self.quiet = False
        self.skipUpdate = True
        self.skipInstall = True
        self.skipConfigure = True
        self.forceConfigure = False
        self.includeDependencies = False
        self.createCompilationDB = False
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
        self.sysrootArchiveName = "sysroot.tar.gz"

        assert self._ensureRequiredPropertiesSet()



def setup_mock_chericonfig(source_root: Path) -> MockConfig:
    config = MockConfig(source_root)
    # noinspection PyTypeChecker
    setCheriConfig(config)
    ConfigLoaderBase._cheriConfig = config
    SimpleProject._configLoader._cheriConfig = config
    # FIXME: There should only be one singleton instance
    return config
