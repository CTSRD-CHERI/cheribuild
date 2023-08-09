import re
from pathlib import Path

import pytest

from pycheribuild.config.target_info import BasicCompilationTargets, DefaultInstallDir
from pycheribuild.projects.cmake_project import CMakeProject
from pycheribuild.projects.repository import ExternallyManagedSourceRepository
from pycheribuild.targets import target_manager
from .setup_mock_chericonfig import CheriConfig, setup_mock_chericonfig


def test_add_cmake_option():
    class TestCMakeProject(CMakeProject):
        target = "fake-cmake-project"
        repository = ExternallyManagedSourceRepository()
        default_install_dir = DefaultInstallDir.DO_NOT_INSTALL

    def add_options_test(expected, **kwargs):
        test_project.add_cmake_options(**kwargs)
        assert test_project.configure_args == expected
        test_project.configure_args.clear()  # reset for next test

    config: CheriConfig = setup_mock_chericonfig(Path("/this/path/does/not/exist"))
    target_manager.reset()
    TestCMakeProject.setup_config_options()
    test_project = TestCMakeProject(config, crosscompile_target=BasicCompilationTargets.NATIVE_NON_PURECAP)
    assert test_project.configure_args == ["-GNinja"]
    test_project.configure_args.clear()  # reset for next test

    # Test adding various types of options:
    add_options_test(["-DSTR_OPTION=abc"], STR_OPTION="abc")
    add_options_test(["-DINT_OPTION=2"], INT_OPTION=2)
    add_options_test(["-DBOOL_OPTION1=TRUE", "-DBOOL_OPTION2=FALSE"], BOOL_OPTION1=True, BOOL_OPTION2=False)
    add_options_test(["-DPATH_OPTION=/some/path"], PATH_OPTION=Path("/some/path"))
    # Lists need to be converted manually
    with pytest.raises(TypeError, match=re.escape("Unsupported type <class 'list'>: ['a', 'b', 'c']")):
        add_options_test(
            ["-DLIST_OPTION_1=a;b;c", "-DLIST_OPTION_2=a", "-DLIST_OPTION_3="],
            LIST_OPTION_1=["a", "b", "c"],
            LIST_OPTION_2=["a"],
            LIST_OPTION_3=[],
        )
    # Floats need to be converted manually
    with pytest.raises(TypeError, match=re.escape("Unsupported type <class 'float'>: 0.1")):
        add_options_test([], FLOAT_OPTION=0.1)
    # Check that tuples and bytes are rejected
    with pytest.raises(TypeError, match=re.escape("Unsupported type <class 'bytes'>: b'abc'")):
        add_options_test([], BYTE_OPTION=b"abc")
    with pytest.raises(TypeError, match=re.escape("Unsupported type <class 'tuple'>: ('abc',)")):
        add_options_test([], TUPLE_OPTION=("abc",))
