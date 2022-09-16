import argparse
import pytest
import sys
from pathlib import Path

from pycheribuild.config.loader import ConfigLoaderBase, JsonAndCommandLineConfigLoader
from pycheribuild.config.defaultconfig import DefaultCheriConfig
from pycheribuild.projects.simple_project import SimpleProject
from pycheribuild.targets import target_manager


class TestArgumentParser(argparse.ArgumentParser):
    # This is not a test, despite its name matching Test*
    __test__ = False

    # Don't use sys.exit(), raise an exception instead
    def exit(self, status=0, message=None):
        if status == 2:
            raise KeyError(message)
        else:
            raise RuntimeError(status, message)


@pytest.fixture(scope="session", autouse=True)
def _register_targets():
    sys.argv = ["cheribuild.py"]
    loader = JsonAndCommandLineConfigLoader(argparser_class=TestArgumentParser)
    loader._config_path = Path("/dev/null")
    all_target_names = list(sorted(target_manager.target_names(None))) + ["__run_everything__"]
    ConfigLoaderBase._cheri_config = DefaultCheriConfig(loader, all_target_names)
    ConfigLoaderBase._cheri_config.TEST_MODE = True
    SimpleProject._config_loader = loader
    target_manager.register_command_line_options()
    ConfigLoaderBase._cheri_config.load()
