import inspect
import re
import sys
import tempfile
# noinspection PyUnresolvedReferences
from pathlib import Path

import pytest

# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from pycheribuild.configloader import ConfigLoader here because that will only update the local copy
from pycheribuild.config.loader import ConfigLoaderBase, JsonAndCommandLineConfigLoader, JsonAndCommandLineConfigOption

_loader = JsonAndCommandLineConfigLoader()
from pycheribuild.projects.project import SimpleProject

SimpleProject._config_loader = _loader
from pycheribuild.targets import target_manager, Target
from pycheribuild.config.defaultconfig import DefaultCheriConfig
# noinspection PyUnresolvedReferences
from pycheribuild.projects import *  # make sure all projects are loaded so that target_manager gets populated
from pycheribuild.projects.cross import *  # make sure all projects are loaded so that target_manager gets populated
# noinspection PyProtectedMember
from pycheribuild.projects.disk_image import BuildCheriBSDDiskImage, _BuildDiskImageBase
from pycheribuild.projects.cross.qt5 import BuildQtBase
from pycheribuild.projects.cross.cheribsd import BuildCHERIBSD, BuildFreeBSD, FreeBSDToolchainKind

_targets_registered = False
Target.instantiating_targets_should_warn = False

try:
    import typing
except ImportError:
    typing = {}


# noinspection PyProtectedMember
def _parse_arguments(args, *, config_file=Path("/this/does/not/exist")) -> DefaultCheriConfig:
    global _targets_registered
    # noinspection PyGlobalUndefined
    global _cheri_config
    if not _targets_registered:
        all_target_names = list(sorted(target_manager.target_names)) + ["__run_everything__"]
        ConfigLoaderBase._cheri_config = DefaultCheriConfig(_loader, all_target_names)
        SimpleProject._config_loader = _loader
        target_manager.register_command_line_options()
        _targets_registered = True
    target_manager.reset()
    ConfigLoaderBase._cheri_config.loader._config_path = config_file
    sys.argv = ["cheribuild.py"] + args
    ConfigLoaderBase._cheri_config.loader.reload()
    ConfigLoaderBase._cheri_config.load()
    # pprint.pprint(vars(ret))
    assert ConfigLoaderBase._cheri_config
    return ConfigLoaderBase._cheri_config


def _parse_config_file_and_args(config_file_contents: bytes, *args) -> DefaultCheriConfig:
    with tempfile.NamedTemporaryFile() as t:
        config = Path(t.name)
        config.write_bytes(config_file_contents)
        return _parse_arguments(list(args), config_file=config)


def test_skip_update():
    # default is false:
    assert not _parse_arguments(["--skip-configure"]).skip_update
    # check that --no-foo and --foo work:
    assert _parse_arguments(["--skip-update"]).skip_update
    assert not _parse_arguments(["--no-skip-update"]).skip_update
    # check config file
    with tempfile.NamedTemporaryFile() as t:
        config = Path(t.name)
        config.write_bytes(b'{ "skip-update": true}')
        assert _parse_arguments([], config_file=config).skip_update
        # command line overrides config file:
        assert _parse_arguments(["--skip-update"], config_file=config).skip_update
        assert not _parse_arguments(["--no-skip-update"], config_file=config).skip_update
        config.write_bytes(b'{ "skip-update": false}')
        assert not _parse_arguments([], config_file=config).skip_update
        # command line overrides config file:
        assert _parse_arguments(["--skip-update"], config_file=config).skip_update
        assert not _parse_arguments(["--no-skip-update"], config_file=config).skip_update


def test_per_project_override():
    config = _parse_arguments(["--skip-configure"])
    source_root = config.source_root
    assert config.cheri_sdk_dir is not None
    assert BuildCheriBSDDiskImage.get_instance(None, config).extra_files_dir == source_root / "extra-files"
    _parse_arguments(["--disk-image/extra-files=/foo/bar"])
    assert BuildCheriBSDDiskImage.get_instance(None, config).extra_files_dir == Path("/foo/bar/")
    _parse_arguments(["--disk-image/extra-files", "/bar/foo"])
    assert BuildCheriBSDDiskImage.get_instance(None, config).extra_files_dir == Path("/bar/foo/")
    # different source root should affect the value:
    _parse_arguments(["--source-root=/tmp"])
    assert BuildCheriBSDDiskImage.get_instance(None, config).extra_files_dir == Path("/tmp/extra-files")

    with tempfile.NamedTemporaryFile() as t:
        config_path = Path(t.name)
        config_path.write_bytes(b'{ "source-root": "/x"}')
        _parse_arguments([], config_file=config_path)
        assert BuildCheriBSDDiskImage.get_instance(None, config).extra_files_dir == Path("/x/extra-files")

        # check that source root can be overridden
        _parse_arguments(["--source-root=/y"])
        assert BuildCheriBSDDiskImage.get_instance(None, config).extra_files_dir == Path("/y/extra-files")


def test_cross_compile_project_inherits():
    # Parse args once to ensure target_manager is initialized
    config = _parse_arguments(["--skip-configure"])
    qtbase_class = target_manager.get_target_raw("qtbase").project_class
    qtbase_default = target_manager.get_target_raw("qtbase").get_or_create_project(None, config)  # type: BuildQtBase
    qtbase_native = target_manager.get_target_raw("qtbase-native").get_or_create_project(None,
                                                                                         config)  # type: BuildQtBase
    qtbase_mips = target_manager.get_target_raw("qtbase-mips-hybrid").get_or_create_project(None,
                                                                                            config)  # type: BuildQtBase

    # Check that project name is the same:
    assert qtbase_default.project_name == qtbase_native.project_name
    assert qtbase_mips.project_name == qtbase_native.project_name
    # These classes were generated:
    # noinspection PyUnresolvedReferences
    assert qtbase_native.synthetic_base == qtbase_class
    # noinspection PyUnresolvedReferences
    assert qtbase_mips.synthetic_base == qtbase_class
    assert not hasattr(qtbase_class, "synthetic_base")

    # Now check a property that should be inherited:
    _parse_arguments(["--qtbase-native/build-tests"])
    assert not qtbase_default.build_tests, "qtbase-default build-tests should default to false"
    assert qtbase_native.build_tests, "qtbase-native build-tests should be set on cmdline"
    assert not qtbase_mips.build_tests, "qtbase-mips build-tests should default to false"
    # If the base qtbase option is set but no per-target one use the basic one:
    _parse_arguments(["--qtbase/build-tests"])
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert qtbase_mips.build_tests, "qtbase-mips should inherit build-tests from qtbase(default)"

    # But target-specific ones should override
    _parse_arguments(["--qtbase/build-tests", "--qtbase-mips-hybrid/no-build-tests"])
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests"

    # Check that we hav ethe same behaviour when loading from json:
    _parse_config_file_and_args(b'{"qtbase-native/build-tests": true }')
    assert not qtbase_default.build_tests, "qtbase-default build-tests should default to false"
    assert qtbase_native.build_tests, "qtbase-native build-tests should be set on cmdline"
    assert not qtbase_mips.build_tests, "qtbase-mips build-tests should default to false"
    # If the base qtbase option is set but no per-target one use the basic one:
    _parse_config_file_and_args(b'{"qtbase/build-tests": true }')
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert qtbase_mips.build_tests, "qtbase-mips should inherit build-tests from qtbase(default)"

    # But target-specific ones should override
    _parse_config_file_and_args(b'{"qtbase/build-tests": true, "qtbase-mips-hybrid/build-tests": false }')
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests"

    # And that cmdline still overrides JSON:
    _parse_config_file_and_args(b'{"qtbase/build-tests": true }', "--qtbase-mips-hybrid/no-build-tests")
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests"
    # But if a per-target option is set in the json that still overrides the default set on the cmdline
    _parse_config_file_and_args(b'{"qtbase-mips-hybrid/build-tests": false }', "--qtbase/build-tests")
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a JSON false override for build-tests"

    # However, don't inherit for build_dir since that doesn't make sense:
    def assert_build_dirs_different():
        # Default should be CHERI purecap
        # print("Default build dir:", qtbase_default.build_dir)
        # print("Native build dir:", qtbase_native.build_dir)
        # print("Mips build dir:", qtbase_mips.build_dir)
        assert qtbase_default.build_dir != qtbase_native.build_dir
        assert qtbase_default.build_dir != qtbase_mips.build_dir
        assert qtbase_mips.build_dir != qtbase_native.build_dir

    assert_build_dirs_different()
    # overriding native build dir is fine:
    _parse_arguments(["--qtbase-native/build-directory=/foo/bar"])
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"qtbase-native/build-directory": "/foo/bar"}')
    assert_build_dirs_different()
    # Should not inherit from the default one:
    _parse_arguments(["--qtbase/build-directory=/foo/bar"])
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"qtbase/build-directory": "/foo/bar"}')
    assert_build_dirs_different()

    # Should not inherit from the default one:
    _parse_arguments(["--qtbase/build-directory=/foo/bar", "--qtbase-mips-hybrid/build-directory=/bar/foo"])
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"qtbase/build-directory": "/foo/bar",'
                                b' "qtbase-mips-hybrid/build-directory": "/bar/foo"}')
    assert_build_dirs_different()


# FIXME: cheribsd-cheri/kernel-config should use the cheribsd/kernel-config value
def test_cheribsd_purecap_inherits_config_from_cheribsd():
    # Parse args once to ensure target_manager is initialized
    config = _parse_arguments(["--skip-configure"])
    cheribsd_class = target_manager.get_target_raw("cheribsd").project_class
    cheribsd_default_tgt = target_manager.get_target_raw("cheribsd").get_or_create_project(None,
                                                                                           config)  # type:
    # BuildCHERIBSD
    assert cheribsd_default_tgt.target == "cheribsd-mips-hybrid"
    cheribsd_mips = target_manager.get_target_raw("cheribsd-mips-nocheri").get_or_create_project(None,
                                                                                                 config)  # type:
    # BuildCHERIBSD
    cheribsd_cheri = target_manager.get_target_raw("cheribsd-mips-hybrid").get_or_create_project(None,
                                                                                                 config)  # type:
    # BuildCHERIBSD
    cheribsd_purecap = target_manager.get_target_raw("cheribsd-mips-purecap").get_or_create_project(None,
                                                                                                    config)  # type:
    # BuildCHERIBSD

    # Check that project name is the same:
    assert cheribsd_mips.project_name == cheribsd_cheri.project_name
    assert cheribsd_cheri.project_name == cheribsd_purecap.project_name

    # cheribsd-cheri is a synthetic class, but cheribsd-purecap inst:
    assert cheribsd_cheri.synthetic_base == cheribsd_class
    assert hasattr(cheribsd_purecap, "synthetic_base")

    _parse_arguments(["--cheribsd-mips-nocheri/debug-kernel"])
    assert not cheribsd_purecap.debug_kernel, "cheribsd-purecap debug-kernel should default to false"
    assert not cheribsd_cheri.debug_kernel, "cheribsd-mips-hybrid debug-kernel should default to false"
    assert cheribsd_mips.debug_kernel, "cheribsd-mips-nocheri debug-kernel should be set on cmdline"
    _parse_arguments(["--cheribsd-purecap/debug-kernel"])
    assert cheribsd_purecap.debug_kernel, "cheribsd-purecap debug-kernel should be set on cmdline"
    assert not cheribsd_cheri.debug_kernel, "cheribsd-mips-hybrid debug-kernel should default to false"
    assert not cheribsd_mips.debug_kernel, "cheribsd-mips-nocheri debug-kernel should default to false"
    _parse_arguments(["--cheribsd-cheri/debug-kernel"])
    assert not cheribsd_purecap.debug_kernel, "cheribsd-purecap debug-kernel should default to false"
    assert cheribsd_cheri.debug_kernel, "cheribsd-cheri debug-kernel should be set on cmdline"
    assert not cheribsd_mips.debug_kernel, "cheribsd-mips-nocheri debug-kernel should default to false"

    # If the base cheribsd option is set but no per-target one use both cheribsd-cheri and cheribsd-purecap should
    # inherit basic one:
    _parse_arguments(["--cheribsd/debug-kernel"])
    assert cheribsd_cheri.debug_kernel, "cheribsd-cheri should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"

    # But target-specific ones should override
    _parse_arguments(["--cheribsd/debug-kernel", "--cheribsd-purecap/no-debug-kernel"])
    assert cheribsd_cheri.debug_kernel, "cheribsd-cheri should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_purecap.debug_kernel, "cheribsd-purecap should have a false override for debug-kernel"

    _parse_arguments(["--cheribsd/debug-kernel", "--cheribsd-cheri/no-debug-kernel"])
    assert cheribsd_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_cheri.debug_kernel, "cheribsd-cheri should have a false override for debug-kernel"

    # Check that we hav ethe same behaviour when loading from json:
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true }')
    assert cheribsd_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_cheri.debug_kernel, "cheribsd-cheri should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_mips.debug_kernel, "cheribsd-mips should inherit debug-kernel from cheribsd(default)"

    # But target-specific ones should override
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true, "cheribsd-cheri/debug-kernel": false }')
    assert cheribsd_mips.debug_kernel, "cheribsd-mips debug-kernel should be inherited on cmdline"
    assert cheribsd_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_cheri.debug_kernel, "cheribsd-cheri should have a false override for debug-kernel"

    # And that cmdline still overrides JSON:
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true }', "--cheribsd-cheri/no-debug-kernel")
    assert cheribsd_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_mips.debug_kernel, "cheribsd-mips debug-kernel should be inherited from cheribsd(default)"
    assert not cheribsd_cheri.debug_kernel, "cheribsd-cheri should have a false override for debug-kernel"
    # But if a per-target option is set in the json that still overrides the default set on the cmdline
    _parse_config_file_and_args(b'{"cheribsd-cheri/debug-kernel": false }', "--cheribsd/debug-kernel")
    assert cheribsd_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_mips.debug_kernel, "cheribsd-mips debug-kernel should be inherited from cheribsd(default)"
    assert not cheribsd_cheri.debug_kernel, "cheribsd-cheri should have a JSON false override for debug-kernel"

    # However, don't inherit for build_dir since that doesn't make sense:
    def assert_build_dirs_different():
        assert cheribsd_cheri.build_dir != cheribsd_purecap.build_dir
        assert cheribsd_cheri.build_dir != cheribsd_mips.build_dir
        assert cheribsd_cheri.build_dir == cheribsd_default_tgt.build_dir

    assert_build_dirs_different()
    # overriding native build dir is fine:
    _parse_arguments(["--cheribsd-purecap/build-directory=/foo/bar"])
    assert cheribsd_purecap.build_dir == Path("/foo/bar")
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"cheribsd-purecap/build-directory": "/foo/bar"}')
    assert cheribsd_purecap.build_dir == Path("/foo/bar")
    assert_build_dirs_different()
    # cheribsd-cheri should inherit from the default one, but not cheribsd-purecap:
    _parse_arguments(["--cheribsd/build-directory=/foo/bar"])
    assert cheribsd_cheri.build_dir == Path("/foo/bar")
    assert cheribsd_purecap.build_dir != Path("/foo/bar")
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"cheribsd/build-directory": "/foo/bar"}')
    assert cheribsd_cheri.build_dir == Path("/foo/bar")
    assert cheribsd_purecap.build_dir != Path("/foo/bar")
    assert_build_dirs_different()

    # cheribsd-cheri/builddir should have higher prirority:
    _parse_arguments(["--cheribsd/build-directory=/foo/bar", "--cheribsd-cheri/build-directory=/bar/foo"])
    assert cheribsd_cheri.build_dir == Path("/bar/foo")
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"cheribsd/build-directory": "/foo/bar",'
                                b' "cheribsd-cheri/build-directory": "/bar/foo"}')
    assert cheribsd_cheri.build_dir == Path("/bar/foo")
    assert_build_dirs_different()


def test_target_alias():
    config = _parse_config_file_and_args(b'{"cheribsd-cheri/mfs-root-image": "/some/image"}')
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips-cheri
    # We should load config options for that target from
    cheribsd_cheri = target_manager.get_target_raw("cheribsd-cheri").get_or_create_project(None,
                                                                                           config)  # type:
    # BuildCHERIBSD
    assert str(cheribsd_cheri.mfs_root_image) == "/some/image"
    cheribsd_mips_hybrid = target_manager.get_target_raw("cheribsd-mips-hybrid").get_or_create_project(None,
                                                                                                       config)  #
    # type: BuildCHERIBSD
    assert str(cheribsd_mips_hybrid.mfs_root_image) == "/some/image"
    # Try again with the other key:
    config = _parse_config_file_and_args(b'{"cheribsd-mips-hybrid/mfs-root-image": "/some/image"}')
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips-cheri
    # We should load config options for that target from
    cheribsd_cheri = target_manager.get_target_raw("cheribsd-cheri").get_or_create_project(None,
                                                                                           config)  # type:
    # BuildCHERIBSD
    assert str(cheribsd_cheri.mfs_root_image) == "/some/image"
    cheribsd_mips_hybrid = target_manager.get_target_raw("cheribsd-mips-hybrid").get_or_create_project(None,
                                                                                                       config)  #
    # type: BuildCHERIBSD
    assert str(cheribsd_mips_hybrid.mfs_root_image) == "/some/image"

    # Check command line aliases:
    config = _parse_config_file_and_args(b'{"cheribsd-cheri/mfs-root-image": "/json/value"}',
                                         "--cheribsd-cheri/mfs-root-image=/command/line/value")
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips-cheri
    # We should load config options for that target from
    cheribsd_cheri = target_manager.get_target_raw("cheribsd-cheri").get_or_create_project(None,
                                                                                           config)  # type:
    # BuildCHERIBSD
    assert str(cheribsd_cheri.mfs_root_image) == "/command/line/value"
    cheribsd_mips_hybrid = target_manager.get_target_raw("cheribsd-mips-hybrid").get_or_create_project(None,
                                                                                                       config)  #
    # type: BuildCHERIBSD
    assert str(cheribsd_mips_hybrid.mfs_root_image) == "/command/line/value"

    config = _parse_config_file_and_args(b'{"cheribsd-cheri/mfs-root-image": "/json/value"}',
                                         "--cheribsd-mips-hybrid/mfs-root-image=/command/line/value")
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips-cheri
    # We should load config options for that target from
    cheribsd_cheri = target_manager.get_target_raw(
        "cheribsd-cheri").get_or_create_project(None, config)  # type: BuildCHERIBSD
    assert str(cheribsd_cheri.mfs_root_image) == "/command/line/value"
    cheribsd_mips_hybrid = target_manager.get_target_raw(
        "cheribsd-mips-hybrid").get_or_create_project(None, config)  # type: BuildCHERIBSD
    assert str(cheribsd_mips_hybrid.mfs_root_image) == "/command/line/value"


def test_cheri_mips_purecap_alias():
    # # For other targets we currently keep -cheri suffixed aliases for the -mips-purecap versions
    config = _parse_config_file_and_args(b'{"qtbase-mips-purecap/build-directory": "/some/build/dir"}')
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips-cheri
    # We should load config options for that target from
    qtbase_cheri = target_manager.get_target_raw("qtbase-mips-purecap").get_or_create_project(None,
                                                                                              config)  # type:
    # BuildCHERIBSD
    assert str(qtbase_cheri.build_dir) == "/some/build/dir"
    qtbase_mips_purecap = target_manager.get_target_raw("qtbase-mips-purecap").get_or_create_project(None,
                                                                                                     config)  # type:
    # BuildCHERIBSD
    assert str(qtbase_mips_purecap.build_dir) == "/some/build/dir"


def test_kernconf():
    # Parse args once to ensure target_manager is initialized
    # check default values
    config = _parse_arguments([])
    cheribsd_cheri = target_manager.get_target_raw("cheribsd-cheri").get_or_create_project(None,
                                                                                           config)  # type:
    # BuildCHERIBSD
    cheribsd_mips = target_manager.get_target_raw("cheribsd-mips-nocheri").get_or_create_project(None,
                                                                                                 config)  # type:
    # BuildCHERIBSD
    freebsd_mips = target_manager.get_target_raw("freebsd-mips").get_or_create_project(None,
                                                                                       config)  # type: BuildFreeBSD
    freebsd_native = target_manager.get_target_raw("freebsd-amd64").get_or_create_project(None,
                                                                                          config)  # type: BuildFreeBSD
    assert config.freebsd_kernconf is None
    assert freebsd_mips.kernel_config == "MALTA64"
    assert cheribsd_cheri.kernel_config == "CHERI_MALTA64"
    assert freebsd_native.kernel_config == "GENERIC"

    # Check that --kernconf is used as the fallback
    config = _parse_arguments(["--kernconf=LINT", "--freebsd-mips/kernel-config=NOTMALTA64"])
    assert config.freebsd_kernconf == "LINT"
    attr = inspect.getattr_static(freebsd_mips, "kernel_config")
    # previously we would replace the command line attribute with a string -> check this is no longer true
    assert isinstance(attr, JsonAndCommandLineConfigOption)
    assert freebsd_mips.kernel_config == "NOTMALTA64"
    assert cheribsd_cheri.kernel_config == "LINT"
    assert freebsd_native.kernel_config == "LINT"

    config = _parse_arguments(["--kernconf=LINT", "--cheribsd-cheri/kernel-config=SOMETHING"])
    assert config.freebsd_kernconf == "LINT"
    assert freebsd_mips.kernel_config == "LINT"
    assert cheribsd_cheri.kernel_config == "SOMETHING"
    assert freebsd_native.kernel_config == "LINT"

    config = _parse_arguments(["--kernconf=GENERIC", "--cheribsd/kernel-config=SOMETHING_ELSE"])
    assert config.freebsd_kernconf == "GENERIC"
    assert cheribsd_cheri.kernel_config == "SOMETHING_ELSE"
    assert cheribsd_mips.kernel_config == "SOMETHING_ELSE"
    assert freebsd_mips.kernel_config == "GENERIC"
    assert freebsd_native.kernel_config == "GENERIC"


def test_duplicate_key():
    with pytest.raises(SyntaxError) as excinfo:
        _parse_config_file_and_args(b'{ "output-root": "/foo", "some-other-key": "abc", "output-root": "/bar" }')
        assert re.search("duplicate key: 'output-root'", str(excinfo.value))


def _get_config_with_include(tmpdir: Path, config_json: bytes, workdir: Path = None):
    if not workdir:
        workdir = tmpdir
    config = workdir / "config.json"
    config.write_bytes(config_json)
    return _parse_arguments([], config_file=config)


def test_config_file_include():
    with tempfile.TemporaryDirectory() as d:
        config_dir = Path(d)
        (config_dir / "128-common.json").write_bytes(b'{ "output-root": "/output128" }')
        (config_dir / "256-common.json").write_bytes(b'{ "output-root": "/output256" }')
        (config_dir / "common.json").write_bytes(b'{ "source-root": "/this/is/a/unit/test" }')

        # Check that the config file is parsed:
        result = _get_config_with_include(config_dir, b'{ "#include": "common.json"}')
        assert "/this/is/a/unit/test" == str(result.source_root)

        # Check that the current file always has precendence
        result = _get_config_with_include(config_dir, b'{ "#include": "256-common.json", "output-root": "/output128"}')
        assert "/output128" == str(result.output_root)
        result = _get_config_with_include(config_dir, b'{ "#include": "128-common.json", "output-root": "/output256"}')
        assert "/output256" == str(result.output_root)
        # order doesn't matter since the #include is only evaluated after the whole file has been parsed:
        result = _get_config_with_include(config_dir, b'{ "output-root": "/output128", "#include": "256-common.json"}')
        assert "/output128" == str(result.output_root)
        result = _get_config_with_include(config_dir, b'{ "output-root": "/output256", "#include": "128-common.json"}')
        assert "/output256" == str(result.output_root)

        # TODO: handled nested cases: the level closest to the initial file wins
        (config_dir / "change-source-root.json").write_bytes(
            b'{ "source-root": "/source/root/override", "#include": "common.json" }')
        result = _get_config_with_include(config_dir, b'{ "#include": "change-source-root.json"}')
        assert "/source/root/override" == str(result.source_root)
        # And again the root file wins:
        result = _get_config_with_include(config_dir,
                                          b'{ "source-root": "/override/twice", "#include": "change-source-root.json"}')
        assert "/override/twice" == str(result.source_root)
        # no matter in which order it is written:
        result = _get_config_with_include(config_dir,
                                          b'{ "#include": "change-source-root.json", "source-root": "/override/again"}')
        assert "/override/again" == str(result.source_root)

        # Test merging of objects:
        (config_dir / "change-smb-dir.json").write_bytes(
            b'{ "run": { "smb-host-directory": "/some/path" }, "#include": "common.json" }')
        result = _get_config_with_include(config_dir,
                                          b'{ "run": { "ssh-forwarding-port": 12345 }, "#include": '
                                          b'"change-smb-dir.json" }')
        run_project = target_manager.get_target_raw("run").get_or_create_project(None, result)
        assert run_project.custom_qemu_smb_mount == Path("/some/path")
        assert run_project.ssh_forwarding_port == 12345

        with tempfile.TemporaryDirectory() as d2:
            # Check that relative paths work
            relpath = b"../" + str(Path(d).relative_to(Path(d2).parent)).encode("utf-8")
            result = _get_config_with_include(config_dir,
                                              b'{ "#include": "' + relpath + b'/common.json" }', workdir=Path(d2))
            assert "/this/is/a/unit/test" == str(result.source_root)

            # Check that absolute paths work as expected:
            abspath = b"" + str(Path(d)).encode("utf-8")
            result = _get_config_with_include(config_dir,
                                              b'{ "#include": "' + abspath + b'/common.json" }', workdir=Path(d2))
            assert "/this/is/a/unit/test" == str(result.source_root)

        # Nonexistant paths should raise an error
        with pytest.raises(FileNotFoundError) as excinfo:
            _get_config_with_include(config_dir, b'{ "#include": "bad-path.json"}')
            assert re.search("No such file or directory", str(excinfo.value))

        # Currently only one #include per config file is allowed
        # TODO: this could be supported but it might be better to accept a list instead?
        with pytest.raises(SyntaxError) as excinfo:
            _get_config_with_include(config_dir,
                                     b'{ "#include": "128-common.json", "foo": "bar", "#include": "256-common.json"}')
            assert re.search("duplicate key: '#include'", str(excinfo.value))


def test_libcxxrt_dependency_path():
    # Test that we pick the correct libunwind path when building libcxxrt
    def check_libunwind_path(path, target_name):
        tgt = target_manager.get_target_raw(target_name).get_or_create_project(None, config)
        for i in tgt.configure_args:
            if i.startswith("-DLIBUNWIND_PATH="):
                assert i == ("-DLIBUNWIND_PATH=" + str(path)), tgt.configure_args
                return
        assert False, "Should have found -DLIBUNWIND_PATH= in " + str(tgt.configure_args)

    config = _parse_arguments(["--skip-configure"])
    check_libunwind_path(config.build_root / "libunwind-native-build/test-install-prefix/lib", "libcxxrt-native")
    check_libunwind_path(config.output_root / "rootfs-purecap128/opt/mips-purecap/c++/lib", "libcxxrt-mips-purecap")
    check_libunwind_path(config.output_root / "rootfs128/opt/mips-hybrid/c++/lib", "libcxxrt-mips-hybrid")
    # Check the defaults:
    config = _parse_arguments(["--skip-configure"])
    check_libunwind_path(config.build_root / "libunwind-native-build/test-install-prefix/lib", "libcxxrt-native")
    config = _parse_arguments(["--skip-configure", "--no-use-hybrid-sysroot-for-mips"])
    check_libunwind_path(config.output_root / "rootfs128/opt/mips-hybrid/c++/lib", "libcxxrt-mips-hybrid")
    check_libunwind_path(config.output_root / "rootfs-mips/opt/mips-nocheri/c++/lib", "libcxxrt-mips-nocheri")


@pytest.mark.parametrize("target,expected_path,kind,extra_args", [
    # FreeBSD targets default to upstream LLVM:
    pytest.param("freebsd-mips", "$OUTPUT$/upstream-llvm/bin/clang", FreeBSDToolchainKind.DEFAULT_EXTERNAL, []),
    pytest.param("freebsd-mips", "$OUTPUT$/upstream-llvm/bin/clang", FreeBSDToolchainKind.UPSTREAM_LLVM, []),
    pytest.param("freebsd-mips", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.CHERI_LLVM, []),
    pytest.param("freebsd-mips", "/this/path/should/not/be/used/when/bootstrapping/bin/clang",
                 FreeBSDToolchainKind.BOOTSTRAP, []),
    pytest.param("freebsd-mips", "/path/to/custom/toolchain/bin/clang", FreeBSDToolchainKind.CUSTOM,
                 ["--freebsd-mips/toolchain-path", "/path/to/custom/toolchain"]),

    # CheriBSD-mips can be built with all these toolchains (but defaults to CHERI LLVM):
    pytest.param("cheribsd-mips-nocheri", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.DEFAULT_EXTERNAL, []),
    pytest.param("cheribsd-mips-nocheri", "$OUTPUT$/upstream-llvm/bin/clang", FreeBSDToolchainKind.UPSTREAM_LLVM, []),
    pytest.param("cheribsd-mips-nocheri", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.CHERI_LLVM, []),
    pytest.param("cheribsd-mips-nocheri", "/this/path/should/not/be/used/when/bootstrapping/bin/clang",
                 FreeBSDToolchainKind.BOOTSTRAP, []),
    pytest.param("cheribsd-mips-nocheri", "/path/to/custom/toolchain/bin/clang", FreeBSDToolchainKind.CUSTOM,
                 ["--cheribsd-mips-nocheri/toolchain-path", "/path/to/custom/toolchain"]),
    ])
def test_freebsd_toolchains(target, expected_path, kind: FreeBSDToolchainKind, extra_args):
    args = ["--" + target + "/toolchain", kind.value]
    args.extend(extra_args)
    config = _parse_arguments(args)
    expected_path = expected_path.replace("$OUTPUT$", str(config.output_root))
    project = target_manager.get_target_raw(target).get_or_create_project(None, config)
    assert isinstance(project, BuildFreeBSD)
    assert str(project.CC) == str(expected_path)
    if kind == FreeBSDToolchainKind.BOOTSTRAP:
        assert "XCC" not in project.buildworld_args.env_vars
        assert "XCC=" not in project.kernel_make_args_for_config("GENERIC", None).env_vars
    else:
        assert project.buildworld_args.env_vars.get("XCC", None) == expected_path
        assert project.kernel_make_args_for_config("GENERIC", None).env_vars.get("XCC", None) == expected_path


@pytest.mark.parametrize("target,expected_name", [
    # CheriBSD
    pytest.param("disk-image-mips-nocheri", "cheribsd-mips-nocheri.img"),
    pytest.param("disk-image-mips-hybrid", "cheribsd-mips-hybrid128.img"),
    pytest.param("disk-image-purecap", "cheribsd-mips-purecap128.img"),
    pytest.param("disk-image-riscv64", "cheribsd-riscv64.img"),
    pytest.param("disk-image-riscv64-hybrid", "cheribsd-riscv64-hybrid.img"),
    pytest.param("disk-image-riscv64-purecap", "cheribsd-riscv64-purecap.img"),
    pytest.param("disk-image-amd64", "cheribsd-amd64.img"),
    # Minimal image
    pytest.param("disk-image-minimal-mips-nocheri", "cheribsd-minimal-mips-nocheri.img"),
    pytest.param("disk-image-minimal-mips-hybrid", "cheribsd-minimal-mips-hybrid128.img"),
    pytest.param("disk-image-minimal-purecap", "cheribsd-minimal-mips-purecap128.img"),
    pytest.param("disk-image-minimal-riscv64", "cheribsd-minimal-riscv64.img"),
    pytest.param("disk-image-minimal-riscv64-hybrid", "cheribsd-minimal-riscv64-hybrid.img"),
    pytest.param("disk-image-minimal-riscv64-purecap", "cheribsd-minimal-riscv64-purecap.img"),
    # FreeBSD
    pytest.param("disk-image-freebsd-mips", "freebsd-mips.img"),
    pytest.param("disk-image-freebsd-riscv64", "freebsd-riscv64.img"),
    # pytest.param("disk-image-freebsd-aarch64", "freebsd-aarch64.img"),
    # pytest.param("disk-image-freebsd-i386", "freebsd-i386.img"),
    pytest.param("disk-image-freebsd-amd64", "freebsd-amd64.img"),
    # FreeBSD with default options
    pytest.param("disk-image-freebsd-with-default-options-mips", "freebsd-mips.img"),
    pytest.param("disk-image-freebsd-with-default-options-riscv64", "freebsd-riscv64.img"),
    # pytest.param("disk-image-freebsd-with-default-options-aarch64", "freebsd-aarch64.img"),
    pytest.param("disk-image-freebsd-with-default-options-i386", "freebsd-i386.img"),
    pytest.param("disk-image-freebsd-with-default-options-amd64", "freebsd-amd64.img"),
    ])
def test_disk_image_path(target, expected_name):
    config = _parse_arguments([])
    project = target_manager.get_target_raw(target).get_or_create_project(None, config)
    assert isinstance(project, _BuildDiskImageBase)
    assert str(project.disk_image_path) == str(config.output_root / expected_name)


def test_freebsd_toolchains_cheribsd_purecap():
    # Targets that need CHERI don't have the --toolchain option:
    # Argparse should exit with exit code 2
    with pytest.raises(SystemExit, match=r'2$'):
        for i in FreeBSDToolchainKind:
            test_freebsd_toolchains("cheribsd-purecap", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-mips-hybrid", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-riscv64-hybrid", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-riscv64-purecap", "/wrong/path", i, [])


@pytest.mark.parametrize("target,args,expected", [
    pytest.param("cheribsd", ["--foo"],
                 "cheribsd-mips-hybrid128-build"),
    pytest.param("llvm", ["--foo"],
                 "llvm-project-build"),
    pytest.param("cheribsd-purecap", ["--foo"],
                 "cheribsd-purecap-128-build"),
    # --subobject debug should not have any effect if subobject bounds is disabled
    pytest.param("cheribsd-purecap", ["--subobject-bounds=conservative", "--subobject-debug"],
                 "cheribsd-purecap-128-build"),
    pytest.param("cheribsd-purecap", ["--subobject-bounds=subobject-safe", "--subobject-debug"],
                 "cheribsd-purecap-128-subobject-safe-build"),
    pytest.param("cheribsd-purecap", ["--subobject-bounds=subobject-safe", "--no-subobject-debug"],
                 "cheribsd-purecap-128-subobject-safe-subobject-nodebug-build"),
    # Passing "--cap-table-abi=pcrel" also changes the build dir even though it's (currently) the default for all
    # architectures.
    pytest.param("cheribsd", ["--cap-table-abi=pcrel", "--subobject-bounds=conservative"],
                 "cheribsd-mips-hybrid128-pcrel-build"),
    # plt should be encoded
    pytest.param("cheribsd", ["--cap-table-abi=plt", "--subobject-bounds=conservative"],
                 "cheribsd-mips-hybrid128-plt-build"),
    # everything
    pytest.param("cheribsd-purecap", ["--cap-table-abi=plt", "--subobject-bounds=aggressive", "--mips-float-abi=hard"],
                 "cheribsd-purecap-128-plt-aggressive-hardfloat-build"),
    # plt should be encoded
    pytest.param("sqlite", [], "sqlite-128-build"),
    pytest.param("sqlite-mips-hybrid", [], "sqlite-mips-hybrid128-build"),
    pytest.param("sqlite-native", [], "sqlite-native-build"),
    ])
def test_default_build_dir(target: str, args: list, expected: str):
    # Check that the cheribsd build dir is correct
    config = _parse_arguments(args)
    target = target_manager.get_target(target, None, config, caller="test_default_arch")
    builddir = target.get_or_create_project(None, config).build_dir
    assert isinstance(builddir, Path)
    assert builddir.name == expected
