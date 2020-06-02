from pathlib import Path
from enum import Enum
from pycheribuild.utils import init_global_config
from pycheribuild.config.chericonfig import CheriConfig
from pycheribuild.config.target_info import CompilationTargets, CrossCompileTarget
from pycheribuild.config.loader import ConfigLoaderBase, DefaultValueOnlyConfigLoader
from pycheribuild.projects.project import SimpleProject
from pycheribuild.targets import Target


class MockArgs(object):
    targets = []


class MockActions(Enum):
    pass


class MockConfig(CheriConfig):
    def __init__(self, source_root: Path, pretend=True):  # allow overriding pretend for the async_delete test
        self.fake_loader = DefaultValueOnlyConfigLoader()
        self.fake_loader._parsedArgs = MockArgs()
        super().__init__(self.fake_loader, action_class=MockActions)
        self.default_action = ""
        self.sourceRoot = source_root
        self.buildRoot = source_root / "build"
        self.outputRoot = source_root / "output"
        self.cheribsd_image_root = self.outputRoot
        self.pretend = pretend
        self.clean = True
        self.verbose = True
        self.debug_output = True
        self.quiet = False
        self.skipUpdate = True
        self.skipInstall = True
        self.skipClone = True
        self.skipConfigure = True
        self.forceConfigure = False
        self.includeDependencies = False
        self.create_compilation_db = False
        self.copy_compilation_db_to_source_dir = False
        self.preferred_xtarget = None
        self.mips_cheri_bits = 128
        self.makeJobs = 2
        self.makeWithoutNice = True
        self.force_update = False
        self.force = True
        self.write_logfile = True
        self.test_extra_args = []
        self.load()

        # for the async delete test:
        self.sourceRoot = source_root
        self.buildRoot = source_root / "build"
        self.outputRoot = source_root / "output"
        self.cheri_sdk_dir = self.outputRoot / "sdk"
        self.otherToolsDir = self.outputRoot / "other"

        assert self._ensure_required_properties_set()


def setup_mock_chericonfig(source_root: Path, pretend=True) -> MockConfig:
    config = MockConfig(source_root, pretend)
    # noinspection PyTypeChecker
    init_global_config(test_mode=True, pretend_mode=config.pretend,
        verbose_mode=config.verbose, quiet_mode=config.quiet)
    ConfigLoaderBase._cheriConfig = config
    SimpleProject._configLoader = DefaultValueOnlyConfigLoader()
    SimpleProject._configLoader._cheriConfig = config
    Target.instantiating_targets_should_warn = False
    # FIXME: There should only be one singleton instance
    return config
