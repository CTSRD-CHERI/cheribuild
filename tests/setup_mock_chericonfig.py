from enum import Enum
from pathlib import Path

from pycheribuild.config.chericonfig import CheriConfig
from pycheribuild.config.loader import ConfigLoaderBase, DefaultValueOnlyConfigLoader
from pycheribuild.projects.simple_project import SimpleProject
from pycheribuild.targets import Target
from pycheribuild.utils import init_global_config


class MockArgs(object):
    targets = []


class MockActions(Enum):
    pass


class MockConfig(CheriConfig):
    def __init__(self, source_root: Path, pretend=True):  # allow overriding pretend for the async_delete test
        self.fake_loader = DefaultValueOnlyConfigLoader()
        self.fake_loader._parsed_args = MockArgs()
        super().__init__(self.fake_loader, action_class=MockActions)
        self.default_action = ""
        self.action = None
        self.source_root = source_root
        self.build_root = source_root / "build"
        self.output_root = source_root / "output"
        self.cheribsd_image_root = self.output_root
        self.pretend = pretend
        self.clean = True
        self.verbose = True
        self.debug_output = True
        self.quiet = False
        self.print_targets_only = False
        self.skip_update = True
        self.skip_install = True
        self.skip_clone = True
        self.confirm_clone = False
        self.skip_configure = True
        self.force_configure = False
        self.include_dependencies = False
        self.create_compilation_db = False
        self.copy_compilation_db_to_source_dir = False
        self.mips_cheri_bits = 128
        self.make_jobs = 2
        self.make_without_nice = True
        self.force_update = False
        self.force = True
        self.write_logfile = True
        self.test_extra_args = []
        self.load()

        # for the async delete test:
        self.source_root = source_root
        self.build_root = source_root / "build"
        self.output_root = source_root / "output"
        self.cheri_sdk_dir = self.output_root / "sdk"
        self.morello_sdk_dir = self.output_root / "morello-sdk"
        self.sysroot_output_root = self.output_root
        self.other_tools_dir = self.output_root / "other"

        assert self._ensure_required_properties_set()


def setup_mock_chericonfig(source_root: Path, pretend=True) -> MockConfig:
    config = MockConfig(source_root, pretend)
    # noinspection PyTypeChecker
    init_global_config(config, test_mode=True)
    ConfigLoaderBase._cheri_config = config
    SimpleProject._config_loader = DefaultValueOnlyConfigLoader()
    # noinspection PyProtectedMember
    SimpleProject._config_loader._cheri_config = config
    Target.instantiating_targets_should_warn = False
    # FIXME: There should only be one singleton instance
    return config
