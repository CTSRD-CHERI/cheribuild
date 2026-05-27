import inspect
import re
from pathlib import Path

import pytest

from .setup_mock_chericonfig import CheriConfig, setup_mock_chericonfig
from pycheribuild.config.compilation_targets import CompilationTargets
from pycheribuild.config.config_loader_base import ComputedDefaultValue
from pycheribuild.config.loader import ConfigOptionHandle
from pycheribuild.config.target_info import BasicCompilationTargets, DefaultInstallDir
from pycheribuild.projects.cmake_project import CMakeProject
from pycheribuild.projects.repository import ExternallyManagedSourceRepository
from pycheribuild.projects.simple_project import (
    BoolConfigOption,
    DefaultValueOnlyDescriptor,
    IntConfigOption,
    PerProjectConfigOption,
    SimpleProject,
)
from pycheribuild.targets import target_manager


def test_add_cmake_option():
    class TestCMakeProject(CMakeProject):
        target = "fake-cmake-project"
        repository = ExternallyManagedSourceRepository()
        default_install_dir = DefaultInstallDir.DO_NOT_INSTALL

    def add_options_test(expected, **kwargs):
        test_project.add_cmake_options(**kwargs)
        assert test_project.configure_args == expected
        test_project.configure_args.clear()  # reset for next test

    config: CheriConfig = setup_mock_chericonfig()
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


def test_mixin_and_overridden_config_options():
    """
    Verify that config option descriptors declared in mixin classes are correctly
    registered on target subclasses, and that any options overridden as static
    fixed values in concrete classes are successfully skipped and pruned.
    """

    class TestOptionMixin:
        mixin_option = BoolConfigOption("mixin-option", help="Mixin Option", default=True)

    class TestBaseProject(SimpleProject):
        target = "test-base-project"
        repository = ExternallyManagedSourceRepository()
        default_install_dir = DefaultInstallDir.DO_NOT_INSTALL

        base_option = BoolConfigOption("base-option", help="Base Option", default=False)

    class TestConcreteProject(TestOptionMixin, TestBaseProject):
        target = "test-concrete-project"
        base_option = True

        def process(self):
            pass

    class TestOverrideMixinProject(TestOptionMixin, TestBaseProject):
        target = "test-override-mixin-project"
        mixin_option = False

        def process(self):
            pass

    target_manager.reset()
    TestConcreteProject.setup_config_options()
    TestOverrideMixinProject.setup_config_options()

    assert isinstance(inspect.getattr_static(TestConcreteProject, "mixin_option"), ConfigOptionHandle)
    assert isinstance(inspect.getattr_static(TestConcreteProject, "base_option"), bool)
    assert inspect.getattr_static(TestConcreteProject, "base_option") is True

    assert isinstance(inspect.getattr_static(TestOverrideMixinProject, "mixin_option"), bool)
    assert inspect.getattr_static(TestOverrideMixinProject, "mixin_option") is False

    assert "mixin_option" in TestConcreteProject._local_config_options
    assert isinstance(TestConcreteProject._local_config_options["mixin_option"], PerProjectConfigOption)

    assert "mixin_option" not in TestOverrideMixinProject._local_config_options
    assert "base_option" not in TestConcreteProject._local_config_options

    config: CheriConfig = setup_mock_chericonfig()

    instance = TestConcreteProject(config, crosscompile_target=BasicCompilationTargets.NATIVE_NON_PURECAP)
    assert instance.mixin_option is True
    assert instance.base_option is True

    instance_override = TestOverrideMixinProject(config, crosscompile_target=BasicCompilationTargets.NATIVE_NON_PURECAP)
    assert instance_override.mixin_option is False


def test_conditional_config_options():
    """
    Verify that options defined with an extra_condition are correctly pruned
    during metaclass options registration on targets where the condition is not met.
    """
    target_manager.reset()
    config: CheriConfig = setup_mock_chericonfig()

    class TestMultiArchProject(SimpleProject):
        target = "test-multiarch-project"
        repository = ExternallyManagedSourceRepository()
        default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
        _supported_architectures = (CompilationTargets.NATIVE, CompilationTargets.CHERIBSD_RISCV_PURECAP)

        cond_option = IntConfigOption(
            "cond-option",
            default=1234,
            help="Condition Option",
            extra_condition=lambda cls: cls._xtarget is not None and cls._xtarget.is_native(),
        )

        computed_option = IntConfigOption(
            "computed-option",
            default=ComputedDefaultValue(
                lambda config, proj: 9999 if "native" in proj.target else 1111,
                "description",
            ),
            help="Computed Option",
            extra_condition=lambda cls: cls._xtarget is not None and cls._xtarget.is_native(),
        )

        def process(self):
            pass

    for target in target_manager.targets(config):
        if issubclass(target.project_class, TestMultiArchProject):
            target.project_class.setup_config_options()

    # Verify multi-architecture get_class_for_target registration
    native_cls = TestMultiArchProject.get_class_for_target(CompilationTargets.NATIVE)
    cross_cls = TestMultiArchProject.get_class_for_target(CompilationTargets.CHERIBSD_RISCV_PURECAP)
    assert "cond_option" in native_cls._local_config_options
    assert "cond_option" in cross_cls._local_config_options

    # Verify that the options are registered in the config loader for native, but not for cross
    assert native_cls.target + "/cond-option" in config.loader.option_handles
    assert cross_cls.target + "/cond-option" not in config.loader.option_handles
    assert native_cls.target + "/computed-option" in config.loader.option_handles
    assert cross_cls.target + "/computed-option" not in config.loader.option_handles

    # Verify type of the field (should be ConfigOptionHandle for native, and DefaultValueOnlyDescriptor for cross)
    native_opt = inspect.getattr_static(native_cls, "cond_option")
    cross_opt = inspect.getattr_static(cross_cls, "cond_option")
    assert isinstance(native_opt, ConfigOptionHandle)
    assert isinstance(cross_opt, DefaultValueOnlyDescriptor)

    native_computed_opt = inspect.getattr_static(native_cls, "computed_option")
    cross_computed_opt = inspect.getattr_static(cross_cls, "computed_option")
    assert isinstance(native_computed_opt, ConfigOptionHandle)
    assert isinstance(cross_computed_opt, DefaultValueOnlyDescriptor)

    instance_cond = native_cls(config, crosscompile_target=CompilationTargets.NATIVE)
    assert isinstance(instance_cond.cond_option, int)
    assert instance_cond.cond_option == 1234  # default value
    assert instance_cond.computed_option == 9999

    instance_cross = cross_cls(config, crosscompile_target=CompilationTargets.CHERIBSD_RISCV_PURECAP)
    assert isinstance(instance_cross.cond_option, int)
    assert instance_cross.cond_option == 1234  # default value evaluated via descriptor
    # computed default evaluated dynamically via descriptor and different per-instance
    assert instance_cross.computed_option == 1111


def test_subclass_option_inheritance_from_concrete_parent(tmp_path):
    config = setup_mock_chericonfig(tmp_path)

    class ParentConcreteProject(CMakeProject):
        target = "parent-concrete"
        _supported_architectures = (CompilationTargets.NATIVE,)

        parent_option = IntConfigOption(
            "parent-option",
            default=100,
            help="Parent Option",
        )

        def process(self):
            pass

    class ChildConcreteProject(ParentConcreteProject):
        target = "child-concrete"
        _supported_architectures = (CompilationTargets.NATIVE,)

        def process(self):
            pass

    for target in target_manager.targets(config):
        if target.name in ("parent-concrete", "child-concrete"):
            target.project_class.setup_config_options()

    # Both parent and child target classes should have their respective option registered in the config loader
    assert "parent-concrete/parent-option" in config.loader.option_handles
    assert "child-concrete/parent-option" in config.loader.option_handles
