import inspect
import sys
import tempfile
import typing
# noinspection PyUnresolvedReferences
from pathlib import Path

import pytest

# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from pycheribuild.configloader import ConfigLoader here because that will only update the local copy
from pycheribuild.config.compilation_targets import CompilationTargets
from pycheribuild.config.loader import ConfigLoaderBase, JsonAndCommandLineConfigLoader, JsonAndCommandLineConfigOption
from pycheribuild.projects.run_qemu import LaunchCheriBSD

from pycheribuild.targets import MultiArchTargetAlias, target_manager, Target
from pycheribuild.config.defaultconfig import DefaultCheriConfig
# noinspection PyUnresolvedReferences
from pycheribuild.projects import *  # noqa: F401, F403
from pycheribuild.projects.cross import *  # noqa: F401, F403
# noinspection PyProtectedMember
from pycheribuild.projects.disk_image import BuildCheriBSDDiskImage, _BuildDiskImageBase
from pycheribuild.projects.cross.qt5 import BuildQtBase
from pycheribuild.projects.cross.cheribsd import BuildCHERIBSD, BuildFreeBSD, FreeBSDToolchainKind

# Override the default config loader:
from pycheribuild.projects.project import SimpleProject
_loader = JsonAndCommandLineConfigLoader()
SimpleProject._config_loader = _loader

_targets_registered = False
Target.instantiating_targets_should_warn = False

T = typing.TypeVar('T', bound=SimpleProject)


def _get_target_instance(target_name: str, config, cls: typing.Type[T] = SimpleProject) -> T:
    result = target_manager.get_target_raw(target_name).get_or_create_project(None, config)
    assert isinstance(result, cls)
    return result


def _get_cheribsd_instance(target_name: str, config) -> BuildCHERIBSD:
    return _get_target_instance(target_name, config, BuildCHERIBSD)


# noinspection PyProtectedMember
def _parse_arguments(args: typing.List[str], *, config_file=Path("/this/does/not/exist"),
                     allow_unknown_options=False) -> DefaultCheriConfig:
    assert isinstance(args, list)
    assert all(isinstance(arg, str) for arg in args), "Invalid argv " + str(args)
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
    ConfigLoaderBase._cheri_config.loader.reset()
    ConfigLoaderBase._cheri_config.loader.unknown_config_option_is_error = not allow_unknown_options
    ConfigLoaderBase._cheri_config.load()
    # pprint.pprint(vars(ret))
    assert ConfigLoaderBase._cheri_config
    return ConfigLoaderBase._cheri_config


def _parse_config_file_and_args(config_file_contents: bytes, *args: str,
                                allow_unknown_options=False) -> DefaultCheriConfig:
    with tempfile.NamedTemporaryFile() as t:
        config = Path(t.name)
        config.write_bytes(config_file_contents)
        return _parse_arguments(list(args), config_file=config, allow_unknown_options=allow_unknown_options)


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
    xtarget = CompilationTargets.CHERIBSD_RISCV_PURECAP
    project = BuildCheriBSDDiskImage.get_instance(None, config, cross_target=xtarget)
    assert project.extra_files_dir == source_root / "extra-files"
    _parse_arguments(["--disk-image/extra-files=/foo/bar"])
    assert project.extra_files_dir == Path("/foo/bar/")
    _parse_arguments(["--disk-image/extra-files", "/bar/foo"])
    assert project.extra_files_dir == Path("/bar/foo/")
    # different source root should affect the value:
    _parse_arguments(["--source-root=/tmp"])
    assert project.extra_files_dir == Path("/tmp/extra-files")

    with tempfile.NamedTemporaryFile() as t:
        config_path = Path(t.name)
        config_path.write_bytes(b'{ "source-root": "/x"}')
        _parse_arguments([], config_file=config_path)
        assert project.extra_files_dir == Path("/x/extra-files")

        # check that source root can be overridden
        _parse_arguments(["--source-root=/y"])
        assert project.extra_files_dir == Path("/y/extra-files")


@pytest.mark.parametrize("target_name,resolved_target", [
    pytest.param("llvm", "llvm-native"),
    pytest.param("gdb", "gdb-native"),
    pytest.param("upstream-llvm", "upstream-llvm"),  # no -native target for upstream-llvm
    pytest.param("qemu", "qemu"),  # same for QEMU

    # These used to have defaults but that is confusing now. So check that they no longe rhave default values
    pytest.param("cheribsd", None),
    pytest.param("disk-image", None),
    pytest.param("run", None),
    pytest.param("freebsd", None),
    pytest.param("disk-image-freebsd", None),
    pytest.param("disk-image-freebsd", None),
    pytest.param("qtbase", None),
    pytest.param("libcxx", None),

    # The only exception are the fett-* targets which should target riscv64-purecap by default
    pytest.param("fett-sqlite", "fett-sqlite-riscv64-purecap"),
    pytest.param("fett-sqlite", "fett-sqlite-riscv64-purecap"),
    pytest.param("fett-sqlite", "fett-sqlite-riscv64-purecap"),
    pytest.param("run-fett", "run-fett-riscv64-purecap"),
    pytest.param("disk-image-fett", "disk-image-fett-riscv64-purecap"),
    pytest.param("cheribsd-fett", "cheribsd-fett-riscv64-purecap"),
    ])
def test_target_aliases_default_target(target_name, resolved_target):
    # Check that only some targets (e.g. llvm) have a default target and that we have to explicitly
    # specify the target name for e.g. cheribsd-* run-*, etc
    if resolved_target is None:
        # The target should not exist in the list of targets accepted on the command line
        assert target_name not in target_manager.target_names
        # However, if we use get_target_raw we should get the TargetAlias
        assert isinstance(target_manager.get_target_raw(target_name), MultiArchTargetAlias)
        assert target_manager.get_target_raw(target_name).project_class.default_architecture is None
    else:
        assert target_name in target_manager.target_names
        raw_target = target_manager.get_target_raw(target_name)
        assert isinstance(raw_target, MultiArchTargetAlias) or raw_target.name == resolved_target
        target = target_manager.get_target(target_name, None, _parse_arguments([]),
                                           caller="test_target_aliases_default_target")
        assert target.name == resolved_target


def test_cross_compile_project_inherits():
    # Parse args once to ensure target_manager is initialized
    config = _parse_arguments(["--skip-configure"])
    qtbase_class = target_manager.get_target_raw("qtbase").project_class
    qtbase_native = _get_target_instance("qtbase-native", config, BuildQtBase)
    qtbase_mips = _get_target_instance("qtbase-mips64-hybrid", config, BuildQtBase)

    # Check that project name is the same:
    assert qtbase_mips.project_name == qtbase_native.project_name
    # These classes were generated:
    # noinspection PyUnresolvedReferences
    assert qtbase_native.synthetic_base == qtbase_class
    # noinspection PyUnresolvedReferences
    assert qtbase_mips.synthetic_base == qtbase_class
    assert not hasattr(qtbase_class, "synthetic_base")

    # Now check a property that should be inherited:
    _parse_arguments(["--qtbase-native/build-tests"])
    assert qtbase_native.build_tests, "qtbase-native build-tests should be set on cmdline"
    assert not qtbase_mips.build_tests, "qtbase-mips build-tests should default to false"
    # If the base qtbase option is set but no per-target one use the basic one:
    _parse_arguments(["--qtbase/build-tests"])
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert qtbase_mips.build_tests, "qtbase-mips should inherit build-tests from qtbase(default)"

    # But target-specific ones should override
    _parse_arguments(["--qtbase/build-tests", "--qtbase-mips64-hybrid/no-build-tests"])
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests"

    # Check that we hav ethe same behaviour when loading from json:
    _parse_config_file_and_args(b'{"qtbase-native/build-tests": true }')
    assert qtbase_native.build_tests, "qtbase-native build-tests should be set on cmdline"
    assert not qtbase_mips.build_tests, "qtbase-mips build-tests should default to false"
    # If the base qtbase option is set but no per-target one use the basic one:
    _parse_config_file_and_args(b'{"qtbase/build-tests": true }')
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert qtbase_mips.build_tests, "qtbase-mips should inherit build-tests from qtbase(default)"

    # But target-specific ones should override
    _parse_config_file_and_args(b'{"qtbase/build-tests": true, "qtbase-mips-hybrid/build-tests": false }')
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests"

    # And that cmdline still overrides JSON:
    _parse_config_file_and_args(b'{"qtbase/build-tests": true }', "--qtbase-mips64-hybrid/no-build-tests")
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests"
    # But if a per-target option is set in the json that still overrides the default set on the cmdline
    _parse_config_file_and_args(b'{"qtbase-mips-hybrid/build-tests": false }', "--qtbase/build-tests")
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a JSON false override for build-tests"

    # However, don't inherit for build_dir since that doesn't make sense:
    def assert_build_dirs_different():
        # Default should be CHERI purecap
        # print("Native build dir:", qtbase_native.build_dir)
        # print("Mips build dir:", qtbase_mips.build_dir)
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
    _parse_arguments(["--qtbase/build-directory=/foo/bar", "--qtbase-mips64-hybrid/build-directory=/bar/foo"])
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"qtbase/build-directory": "/foo/bar",'
                                b' "qtbase-mips-hybrid/build-directory": "/bar/foo"}')
    assert_build_dirs_different()


# FIXME: cheribsd-mips64-hybrid/kernel-config should use the cheribsd/kernel-config value


def test_cheribsd_purecap_inherits_config_from_cheribsd():
    # Parse args once to ensure target_manager is initialized
    config = _parse_arguments(["--skip-configure"])
    cheribsd_class = target_manager.get_target_raw("cheribsd").project_class
    cheribsd_mips = _get_cheribsd_instance("cheribsd-mips64", config)
    cheribsd_mips_hybrid = _get_cheribsd_instance("cheribsd-mips64-hybrid", config)
    cheribsd_mips_purecap = _get_cheribsd_instance("cheribsd-mips64-purecap", config)

    # Check that project name is the same:
    assert cheribsd_mips.project_name == cheribsd_mips_hybrid.project_name
    assert cheribsd_mips_hybrid.project_name == cheribsd_mips_purecap.project_name

    # noinspection PyUnresolvedReferences
    assert cheribsd_mips_hybrid.synthetic_base == cheribsd_class
    # noinspection PyUnresolvedReferences
    assert cheribsd_mips_purecap.synthetic_base == cheribsd_class

    _parse_arguments(["--cheribsd-mips64/debug-kernel"])
    assert not cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap debug-kernel should default to false"
    assert not cheribsd_mips_hybrid.debug_kernel, "cheribsd-mips-hybrid debug-kernel should default to false"
    assert cheribsd_mips.debug_kernel, "cheribsd-mips64 debug-kernel should be set on cmdline"
    _parse_arguments(["--cheribsd-mips64-purecap/debug-kernel"])
    assert cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap debug-kernel should be set on cmdline"
    assert not cheribsd_mips_hybrid.debug_kernel, "cheribsd-mips-hybrid debug-kernel should default to false"
    assert not cheribsd_mips.debug_kernel, "cheribsd-mips64 debug-kernel should default to false"
    _parse_arguments(["--cheribsd-mips64-hybrid/debug-kernel"])
    assert not cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap debug-kernel should default to false"
    assert cheribsd_mips_hybrid.debug_kernel, "cheribsd-mips64-hybrid debug-kernel should be set on cmdline"
    assert not cheribsd_mips.debug_kernel, "cheribsd-mips64 debug-kernel should default to false"

    # If the base cheribsd option is set but no per-target one use both cheribsd-mips64-hybrid and cheribsd-purecap
    # should    # inherit basic one:
    _parse_arguments(["--cheribsd/debug-kernel"])
    assert cheribsd_mips_hybrid.debug_kernel, "mips64-hybrid should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"

    # But target-specific ones should override
    _parse_arguments(["--cheribsd/debug-kernel", "--cheribsd-mips64-purecap/no-debug-kernel"])
    assert cheribsd_mips_hybrid.debug_kernel, "mips64-hybrid should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap should have a false override for debug-kernel"

    _parse_arguments(["--cheribsd/debug-kernel", "--cheribsd-mips64-hybrid/no-debug-kernel"])
    assert cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_mips_hybrid.debug_kernel, "mips64-hybrid should have a false override for debug-kernel"

    # Check that we hav ethe same behaviour when loading from json:
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true }')
    assert cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_mips_hybrid.debug_kernel, "mips64-hybrid should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_mips.debug_kernel, "cheribsd-mips should inherit debug-kernel from cheribsd(default)"

    # But target-specific ones should override
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true, "cheribsd-mips64-hybrid/debug-kernel": false }')
    assert cheribsd_mips.debug_kernel, "cheribsd-mips debug-kernel should be inherited on cmdline"
    assert cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_mips_hybrid.debug_kernel, "mips64-hybrid should have a false override for debug-kernel"

    # And that cmdline still overrides JSON:
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true }', "--cheribsd-mips64-hybrid/no-debug-kernel")
    assert cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_mips.debug_kernel, "cheribsd-mips debug-kernel should be inherited from cheribsd(default)"
    assert not cheribsd_mips_hybrid.debug_kernel, "mips64-hybrid should have a false override for debug-kernel"
    # But if a per-target option is set in the json that still overrides the default set on the cmdline
    _parse_config_file_and_args(b'{"cheribsd-mips64-hybrid/debug-kernel": false }', "--cheribsd/debug-kernel")
    assert cheribsd_mips_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_mips.debug_kernel, "cheribsd-mips debug-kernel should be inherited from cheribsd(default)"
    assert not cheribsd_mips_hybrid.debug_kernel, "mips64-hybrid should have a JSON false override for debug-kernel"

    # However, don't inherit for build_dir since that doesn't make sense:
    def assert_build_dirs_different():
        assert cheribsd_mips_hybrid.build_dir != cheribsd_mips_purecap.build_dir
        assert cheribsd_mips_hybrid.build_dir != cheribsd_mips.build_dir

    assert_build_dirs_different()
    # overriding native build dir is fine:
    _parse_arguments(["--cheribsd-mips64-purecap/build-directory=/foo/bar"])
    assert cheribsd_mips_purecap.build_dir == Path("/foo/bar")
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"cheribsd-mips64-purecap/build-directory": "/foo/bar"}')
    assert cheribsd_mips_purecap.build_dir == Path("/foo/bar")
    assert_build_dirs_different()
    _parse_arguments(["--cheribsd-mips64-hybrid/build-directory=/foo/bar"])
    assert cheribsd_mips_hybrid.build_dir == Path("/foo/bar")
    assert cheribsd_mips_purecap.build_dir != Path("/foo/bar")
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"cheribsd-mips-hybrid/build-directory": "/foo/bar"}')
    assert cheribsd_mips_hybrid.build_dir == Path("/foo/bar")
    assert cheribsd_mips_purecap.build_dir != Path("/foo/bar")
    assert_build_dirs_different()

    # cheribsd-mips64-hybrid/builddir should have higher prirority:
    _parse_arguments(["--cheribsd/build-directory=/foo/bar", "--cheribsd-mips64-hybrid/build-directory=/bar/foo"])
    assert cheribsd_mips_hybrid.build_dir == Path("/bar/foo")
    assert_build_dirs_different()
    _parse_config_file_and_args(b'{"cheribsd/build-directory": "/foo/bar",'
                                b' "cheribsd-mips64-hybrid/build-directory": "/bar/foo"}')
    assert cheribsd_mips_hybrid.build_dir == Path("/bar/foo")
    assert_build_dirs_different()


def test_legacy_cheri_suffix_target_alias():
    config = _parse_config_file_and_args(b'{"cheribsd-cheri/mfs-root-image": "/some/image"}')
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips-cheri
    # We should load config options for that target from
    cheribsd_cheri = _get_cheribsd_instance("cheribsd-cheri", config)
    assert str(cheribsd_cheri.mfs_root_image) == "/some/image"
    cheribsd_mips_hybrid = _get_cheribsd_instance("cheribsd-mips64-hybrid", config)
    assert str(cheribsd_mips_hybrid.mfs_root_image) == "/some/image"
    # Try again with the other key:
    config = _parse_config_file_and_args(b'{"cheribsd-mips-hybrid/mfs-root-image": "/some/image"}')
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips64-hybrid
    # We should load config options for that target from
    cheribsd_cheri = _get_cheribsd_instance("cheribsd-cheri", config)
    assert str(cheribsd_cheri.mfs_root_image) == "/some/image"
    cheribsd_mips_hybrid = _get_cheribsd_instance("cheribsd-mips64-hybrid", config)
    assert str(cheribsd_mips_hybrid.mfs_root_image) == "/some/image"

    # Check command line aliases:
    config = _parse_config_file_and_args(b'{"cheribsd-cheri/mfs-root-image": "/json/value"}',
                                         "--cheribsd-mips64-hybrid/mfs-root-image=/command/line/value")
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips-cheri
    # We should load config options for that target from
    cheribsd_cheri = _get_cheribsd_instance("cheribsd-cheri", config)
    assert str(cheribsd_cheri.mfs_root_image) == "/command/line/value"
    cheribsd_mips_hybrid = _get_cheribsd_instance("cheribsd-mips64-hybrid", config)
    assert str(cheribsd_mips_hybrid.mfs_root_image) == "/command/line/value"

    config = _parse_config_file_and_args(b'{"cheribsd-cheri/mfs-root-image": "/json/value"}',
                                         "--cheribsd-mips64-hybrid/mfs-root-image=/command/line/value")
    # Check that cheribsd-cheri is a (deprecated) target alias for cheribsd-mips-cheri
    # We should load config options for that target from
    cheribsd_cheri = _get_cheribsd_instance("cheribsd-cheri", config)
    assert str(cheribsd_cheri.mfs_root_image) == "/command/line/value"
    cheribsd_mips_hybrid = _get_cheribsd_instance("cheribsd-mips64-hybrid", config)
    assert str(cheribsd_mips_hybrid.mfs_root_image) == "/command/line/value"


def test_kernconf():
    # Parse args once to ensure target_manager is initialized
    # check default values
    config = _parse_arguments([])
    cheribsd_cheri = _get_cheribsd_instance("cheribsd-mips64-hybrid", config)
    cheribsd_mips = _get_cheribsd_instance("cheribsd-mips64", config)
    freebsd_mips = _get_target_instance("freebsd-mips64", config, BuildFreeBSD)
    freebsd_native = _get_target_instance("freebsd-amd64", config, BuildFreeBSD)
    assert config.freebsd_kernconf is None
    assert freebsd_mips.kernel_config == "MALTA64"
    assert cheribsd_cheri.kernel_config == "CHERI_MALTA64"
    assert freebsd_native.kernel_config == "GENERIC"

    # Check that --kernconf is used as the fallback
    config = _parse_arguments(["--kernconf=LINT", "--freebsd-mips64/kernel-config=NOTMALTA64"])
    assert config.freebsd_kernconf == "LINT"
    attr = inspect.getattr_static(freebsd_mips, "kernel_config")
    # previously we would replace the command line attribute with a string -> check this is no longer true
    assert isinstance(attr, JsonAndCommandLineConfigOption)
    assert freebsd_mips.kernel_config == "NOTMALTA64"
    assert cheribsd_cheri.kernel_config == "LINT"
    assert freebsd_native.kernel_config == "LINT"

    config = _parse_arguments(["--kernconf=LINT", "--cheribsd-mips64-hybrid/kernel-config=SOMETHING"])
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
    with pytest.raises(SyntaxError, match="duplicate key: 'output-root'"):
        _parse_config_file_and_args(b'{ "output-root": "/foo", "some-other-key": "abc", "output-root": "/bar" }')


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
                                          b'{'
                                          b'  "run": { "ssh-forwarding-port": 12345 },'
                                          b'  "#include": "change-smb-dir.json"'
                                          b'}')
        run_project = _get_target_instance("run-riscv64-purecap", result, LaunchCheriBSD)
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
        with pytest.raises(FileNotFoundError, match="No such file or directory"):
            _get_config_with_include(config_dir, b'{ "#include": "bad-path.json"}')

        # Currently only one #include per config file is allowed
        # TODO: this could be supported but it might be better to accept a list instead?
        with pytest.raises(SyntaxError, match="duplicate key: '#include'"):
            _get_config_with_include(config_dir,
                                     b'{ "#include": "128-common.json", "foo": "bar", "#include": "256-common.json"}')


def test_libcxxrt_dependency_path():
    # Test that we pick the correct libunwind path when building libcxxrt
    def check_libunwind_path(path, target_name):
        tgt = _get_target_instance(target_name, config)
        for i in tgt.configure_args:
            if i.startswith("-DLIBUNWIND_PATH="):
                assert i == ("-DLIBUNWIND_PATH=" + str(path)), tgt.configure_args
                return
        assert False, "Should have found -DLIBUNWIND_PATH= in " + str(tgt.configure_args)

    config = _parse_arguments(["--skip-configure"])
    check_libunwind_path(config.build_root / "libunwind-native-build/test-install-prefix/lib", "libcxxrt-native")
    check_libunwind_path(config.output_root / "rootfs-mips64-purecap/opt/mips64-purecap/c++/lib",
                         "libcxxrt-mips64-purecap")
    check_libunwind_path(config.output_root / "rootfs-mips64-hybrid/opt/mips64-hybrid/c++/lib",
                         "libcxxrt-mips64-hybrid")
    # Check the defaults:
    config = _parse_arguments(["--skip-configure"])
    check_libunwind_path(config.build_root / "libunwind-native-build/test-install-prefix/lib", "libcxxrt-native")
    config = _parse_arguments(["--skip-configure"])
    check_libunwind_path(config.output_root / "rootfs-mips64-hybrid/opt/mips64-hybrid/c++/lib",
                         "libcxxrt-mips64-hybrid")
    check_libunwind_path(config.output_root / "rootfs-mips64/opt/mips64/c++/lib", "libcxxrt-mips64")


class SystemClangIfExistsElse:
    def __init__(self, fallback: str):
        self.fallback = fallback


@pytest.mark.parametrize("target,expected_path,kind,extra_args", [
    # FreeBSD targets default to system clang if it exists, otherwise LLVM:
    pytest.param("freebsd-mips64", SystemClangIfExistsElse("$OUTPUT$/upstream-llvm/bin/clang"),
                 FreeBSDToolchainKind.DEFAULT_EXTERNAL, []),
    pytest.param("freebsd-mips64", "$OUTPUT$/upstream-llvm/bin/clang", FreeBSDToolchainKind.UPSTREAM_LLVM, []),
    pytest.param("freebsd-mips64", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.CHERI_LLVM, []),
    pytest.param("freebsd-mips64", "/this/path/should/not/be/used/when/bootstrapping/bin/clang",
                 FreeBSDToolchainKind.BOOTSTRAP, []),
    pytest.param("freebsd-mips64", "/path/to/custom/toolchain/bin/clang", FreeBSDToolchainKind.CUSTOM,
                 ["--freebsd-mips64/toolchain-path", "/path/to/custom/toolchain"]),

    # CheriBSD-mips can be built with all these toolchains (but defaults to CHERI LLVM):
    pytest.param("cheribsd-mips64", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.DEFAULT_EXTERNAL, []),
    pytest.param("cheribsd-mips64", "$OUTPUT$/upstream-llvm/bin/clang", FreeBSDToolchainKind.UPSTREAM_LLVM, []),
    pytest.param("cheribsd-mips64", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.CHERI_LLVM, []),
    pytest.param("cheribsd-mips64", "/this/path/should/not/be/used/when/bootstrapping/bin/clang",
                 FreeBSDToolchainKind.BOOTSTRAP, []),
    pytest.param("cheribsd-mips64", "/path/to/custom/toolchain/bin/clang", FreeBSDToolchainKind.CUSTOM,
                 ["--cheribsd-mips64/toolchain-path", "/path/to/custom/toolchain"]),
    ])
def test_freebsd_toolchains(target, expected_path, kind: FreeBSDToolchainKind, extra_args):
    args = ["--" + target + "/toolchain", kind.value]
    args.extend(extra_args)
    config = _parse_arguments(args)
    project = _get_target_instance(target, config, BuildFreeBSD)
    if isinstance(expected_path, SystemClangIfExistsElse):
        clang_root, _, _ = project._try_find_compatible_system_clang()
        expected_path = str(clang_root / "bin/clang") if clang_root is not None else expected_path.fallback
    expected_path = expected_path.replace("$OUTPUT$", str(config.output_root))
    assert str(project.CC) == str(expected_path)
    if kind == FreeBSDToolchainKind.BOOTSTRAP:
        assert "XCC" not in project.buildworld_args.env_vars
        assert "XCC=" not in project.kernel_make_args_for_config("GENERIC", None).env_vars
    else:
        assert project.buildworld_args.env_vars.get("XCC", None) == expected_path
        assert project.kernel_make_args_for_config("GENERIC", None).env_vars.get("XCC", None) == expected_path


@pytest.mark.parametrize("target,expected_name", [
    # CheriBSD
    pytest.param("disk-image-mips64", "cheribsd-mips64.img"),
    pytest.param("disk-image-mips64-hybrid", "cheribsd-mips64-hybrid.img"),
    pytest.param("disk-image-purecap", "cheribsd-mips64-purecap.img"),
    pytest.param("disk-image-riscv64", "cheribsd-riscv64.img"),
    pytest.param("disk-image-riscv64-hybrid", "cheribsd-riscv64-hybrid.img"),
    pytest.param("disk-image-riscv64-purecap", "cheribsd-riscv64-purecap.img"),
    pytest.param("disk-image-amd64", "cheribsd-amd64.img"),
    pytest.param("disk-image-morello-hybrid", "cheribsd-morello-hybrid.img"),
    pytest.param("disk-image-morello-purecap", "cheribsd-morello-purecap.img"),
    # Minimal image
    pytest.param("disk-image-minimal-mips64", "cheribsd-minimal-mips64.img"),
    pytest.param("disk-image-minimal-mips64-hybrid", "cheribsd-minimal-mips64-hybrid.img"),
    pytest.param("disk-image-minimal-purecap", "cheribsd-minimal-mips64-purecap.img"),
    pytest.param("disk-image-minimal-riscv64", "cheribsd-minimal-riscv64.img"),
    pytest.param("disk-image-minimal-riscv64-hybrid", "cheribsd-minimal-riscv64-hybrid.img"),
    pytest.param("disk-image-minimal-riscv64-purecap", "cheribsd-minimal-riscv64-purecap.img"),
    # FreeBSD
    pytest.param("disk-image-freebsd-mips64", "freebsd-mips64.img"),
    pytest.param("disk-image-freebsd-riscv64", "freebsd-riscv64.img"),
    # pytest.param("disk-image-freebsd-aarch64", "freebsd-aarch64.img"),
    # pytest.param("disk-image-freebsd-i386", "freebsd-i386.img"),
    pytest.param("disk-image-freebsd-amd64", "freebsd-amd64.img"),
    # FreeBSD with default options
    pytest.param("disk-image-freebsd-with-default-options-mips64", "freebsd-mips64.img"),
    pytest.param("disk-image-freebsd-with-default-options-riscv64", "freebsd-riscv64.img"),
    # pytest.param("disk-image-freebsd-with-default-options-aarch64", "freebsd-aarch64.img"),
    pytest.param("disk-image-freebsd-with-default-options-i386", "freebsd-i386.img"),
    pytest.param("disk-image-freebsd-with-default-options-amd64", "freebsd-amd64.img"),
    ])
def test_disk_image_path(target, expected_name):
    config = _parse_arguments([])
    project = _get_target_instance(target, config, _BuildDiskImageBase)
    assert str(project.disk_image_path) == str(config.output_root / expected_name)


# noinspection PyTypeChecker
def test_freebsd_toolchains_cheribsd_purecap():
    # Targets that need CHERI don't have the --toolchain option:
    # Argparse should exit with exit code 2
    with pytest.raises(SystemExit, match=r'^2$'):
        for i in FreeBSDToolchainKind:
            test_freebsd_toolchains("cheribsd-purecap", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-mips64-hybrid", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-riscv64-hybrid", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-riscv64-purecap", "/wrong/path", i, [])


@pytest.mark.parametrize("target,args,expected", [
    pytest.param("cheribsd-mips64-hybrid", [], "cheribsd-mips64-hybrid-build"),
    pytest.param("llvm", [], "llvm-project-build"),
    pytest.param("cheribsd-purecap", [], "cheribsd-mips64-purecap-build"),
    # --subobject debug should not have any effect if subobject bounds is disabled
    pytest.param("cheribsd-purecap", ["--subobject-bounds=conservative", "--subobject-debug"],
                 "cheribsd-mips64-purecap-build"),
    pytest.param("cheribsd-purecap", ["--subobject-bounds=subobject-safe", "--subobject-debug"],
                 "cheribsd-mips64-purecap-subobject-safe-build"),
    pytest.param("cheribsd-purecap", ["--subobject-bounds=subobject-safe", "--no-subobject-debug"],
                 "cheribsd-mips64-purecap-subobject-safe-subobject-nodebug-build"),
    # Passing "--cap-table-abi=pcrel" also changes the build dir even though it's (currently) the default for all
    # architectures.
    pytest.param("cheribsd-mips64-hybrid", ["--cap-table-abi=pcrel", "--subobject-bounds=conservative"],
                 "cheribsd-mips64-hybrid-pcrel-build"),
    # plt should be encoded
    pytest.param("cheribsd-mips64-hybrid", ["--cap-table-abi=plt", "--subobject-bounds=conservative"],
                 "cheribsd-mips64-hybrid-plt-build"),
    # everything
    pytest.param("cheribsd-mips64-purecap",
                 ["--cap-table-abi=plt", "--subobject-bounds=aggressive", "--mips-float-abi=hard"],
                 "cheribsd-mips64-purecap-plt-aggressive-hardfloat-build"),
    # plt should be encoded
    pytest.param("sqlite-mips64-hybrid", [], "sqlite-mips64-hybrid-build"),
    pytest.param("sqlite-native", [], "sqlite-native-build"),
    ])
def test_default_build_dir(target: str, args: list, expected: str):
    # Check that the cheribsd build dir is correct
    config = _parse_arguments(args)
    target = target_manager.get_target(target, None, config, caller="test_default_arch")
    builddir = target.get_or_create_project(None, config).build_dir
    assert isinstance(builddir, Path)
    assert builddir.name == expected


@pytest.mark.parametrize("target,args,expected_sysroot,expected_rootfs", [
    pytest.param("cheribsd-mips64", [],
                 "sdk/sysroot-mips64", "rootfs-mips64"),
    pytest.param("cheribsd-mips64-hybrid", [],
                 "sdk/sysroot-mips64-hybrid", "rootfs-mips64-hybrid"),
    pytest.param("cheribsd-mips64-purecap", [],
                 "sdk/sysroot-mips64-purecap", "rootfs-mips64-purecap"),
    pytest.param("cheribsd-riscv64", [],
                 "sdk/sysroot-riscv64", "rootfs-riscv64"),
    pytest.param("cheribsd-riscv64-hybrid", [],
                 "sdk/sysroot-riscv64-hybrid", "rootfs-riscv64-hybrid"),
    pytest.param("cheribsd-riscv64-purecap", [],
                 "sdk/sysroot-riscv64-purecap", "rootfs-riscv64-purecap"),
    pytest.param("cheribsd-aarch64", [],
                 "sdk/sysroot-aarch64", "rootfs-aarch64"),
    pytest.param("cheribsd-amd64", [],
                 "sdk/sysroot-amd64", "rootfs-amd64"),
    # Morello uses a different SDK dir
    # TODO: pytest.param("cheribsd-morello"/"cheribsd-morello-nocheri"
    pytest.param("cheribsd-morello-hybrid", [],
                 "morello-sdk/sysroot-morello-hybrid", "rootfs-morello-hybrid"),
    pytest.param("cheribsd-morello-purecap", [],
                 "morello-sdk/sysroot-morello-purecap", "rootfs-morello-purecap"),

    # Check that various global flags are encoded
    # --subobject debug should not have any effect if subobject bounds is disabled
    pytest.param("cheribsd-riscv64-purecap", ["--subobject-bounds=conservative", "--subobject-debug"],
                 "sdk/sysroot-riscv64-purecap", "rootfs-riscv64-purecap"),
    pytest.param("cheribsd-riscv64-purecap", ["--subobject-bounds=subobject-safe", "--subobject-debug"],
                 "sdk/sysroot-riscv64-purecap-subobject-safe", "rootfs-riscv64-purecap-subobject-safe"),
    pytest.param("cheribsd-riscv64-purecap", ["--subobject-bounds=subobject-safe", "--no-subobject-debug"],
                 "sdk/sysroot-riscv64-purecap-subobject-safe-subobject-nodebug",
                 "rootfs-riscv64-purecap-subobject-safe-subobject-nodebug"),

    # Passing "--cap-table-abi=pcrel" also changes the dir even though it's the default for all architectures.
    pytest.param("cheribsd-mips64-purecap", ["--cap-table-abi=pcrel", "--subobject-bounds=conservative"],
                 "sdk/sysroot-mips64-purecap-pcrel", "rootfs-mips64-purecap-pcrel"),
    pytest.param("cheribsd-mips64-purecap", ["--cap-table-abi=plt", "--subobject-bounds=conservative"],
                 "sdk/sysroot-mips64-purecap-plt", "rootfs-mips64-purecap-plt"),
    pytest.param("cheribsd-mips64-purecap",
                 ["--cap-table-abi=plt", "--subobject-bounds=aggressive", "--mips-float-abi=hard"],
                 "sdk/sysroot-mips64-purecap-plt-aggressive-hardfloat",
                 "rootfs-mips64-purecap-plt-aggressive-hardfloat"),

    # FreeBSD
    pytest.param("freebsd-aarch64", [],
                 "sdk/sysroot-freebsd-aarch64", "freebsd-aarch64"),
    pytest.param("freebsd-amd64", [],
                 "sdk/sysroot-freebsd-amd64", "freebsd-amd64"),
    pytest.param("freebsd-i386", [],
                 "sdk/sysroot-freebsd-i386", "freebsd-i386"),
    pytest.param("freebsd-mips64", [],
                 "sdk/sysroot-freebsd-mips64", "freebsd-mips64"),
    pytest.param("freebsd-riscv64", [],
                 "sdk/sysroot-freebsd-riscv64", "freebsd-riscv64"),
    ])
def test_default_rootfs_and_sysroot_dir(target: str, args: list, expected_sysroot: str, expected_rootfs: str):
    # Check that the cheribsd build dir is correct
    config = _parse_arguments(args)
    project = _get_target_instance(target, config, BuildFreeBSD)
    sysroot_dir = project.cross_sysroot_path
    assert sysroot_dir == project.target_info.sysroot_dir
    assert str(sysroot_dir.relative_to(config.output_root)) == expected_sysroot
    rootfs_dir = project.install_dir
    assert str(rootfs_dir.relative_to(config.output_root)) == expected_rootfs


def test_backwards_compat_old_suffixes():
    config = _parse_config_file_and_args(b'{"qtbase-mips-purecap/build-directory": "/some/build/dir"}')
    # Check that qtbase-mips-purecap is a (deprecated) target alias for qtbase-mips64-purecap
    # and that we still load config options for that old target name
    qtbase_mips_purecap = _get_target_instance("qtbase-mips64-purecap", config, BuildQtBase)
    assert str(qtbase_mips_purecap.build_dir) == "/some/build/dir"
    qtbase_mips_purecap = _get_target_instance("qtbase-mips-purecap", config, BuildQtBase)
    assert str(qtbase_mips_purecap.build_dir) == "/some/build/dir"


def _check_build_dir(target: str, expected: str, config_file: bytes, cmdline: typing.List[str]):
    config = _parse_config_file_and_args(config_file, *cmdline)
    project = _get_target_instance(target, config)
    assert str(project.build_dir) == expected


def test_backwards_compat_old_suffixes_freebsd_mips():
    # Check that we still load the value from the deprecated key name from the JSON config file
    _check_build_dir("freebsd-mips64", "/from/json",
                     b'{"freebsd-mips/build-directory": "/from/json"}', [])

    # It should also override a command line value for the un-suffixed target
    _check_build_dir("freebsd-mips64", "/from/json",
                     b'{"freebsd-mips/build-directory": "/from/json"}',
                     ["--freebsd/build-directory=/fallback/from/cmdline/"])

    # The new key name should have priority:
    _check_build_dir("freebsd-mips64", "/new/dir",
                     b'{"freebsd-mips/build-directory": "/old/dir",'
                     b' "freebsd-mips64/build-directory": "/new/dir" }', [])

    # Also check the CheriBSD names:
    _check_build_dir("cheribsd-mips64", "/from/json",
                     b'{"cheribsd-mips-nocheri/build-directory": "/from/json"}', [])
    _check_build_dir("cheribsd-mips64-hybrid", "/from/json",
                     b'{"cheribsd-mips-hybrid/build-directory": "/from/json"}', [])
    _check_build_dir("cheribsd-mips64-purecap", "/from/json",
                     b'{"cheribsd-mips-purecap/build-directory": "/from/json"}', [])

    # Finally, using the old name on the command line should be an error:
    with pytest.raises(SystemExit, match="^2$"):
        _ = _parse_config_file_and_args(b'{}', "--freebsd-mips/build-directory=/cmdline")


def test_expand_tilde_and_env_vars(monkeypatch):
    monkeypatch.setenv("HOME", "/home/foo")
    monkeypatch.setenv("MYHOME", "/home/foo")
    # Check that relative paths in config files resolve relative to the file that it's being loaded from
    assert _parse_config_file_and_args(b'{ "build-root": "~/build" }').build_root == Path("/home/foo/build")
    assert _parse_config_file_and_args(b'{ "build-root": "$MYHOME/build" }').build_root == Path("/home/foo/build")
    assert _parse_config_file_and_args(b'{ "build-root": "${MYHOME}/build" }').build_root == Path("/home/foo/build")
    # Having HOME==/ broke jenkins, test this here:
    monkeypatch.setenv("HOME", "/")
    assert _parse_config_file_and_args(b'{ "build-root": "~/build" }').build_root == Path("/build")
    assert _parse_config_file_and_args(b'{ "build-root": "$HOME/build" }').build_root == Path("/build")
    assert _parse_config_file_and_args(b'{ "build-root": "${HOME}/build" }').build_root == Path("/build")
    # Multiple slashes should be removed:
    assert _parse_config_file_and_args(b'{ "build-root": "~//build//dir" }').build_root == Path("/build/dir")
    assert _parse_config_file_and_args(b'{ "build-root": "$HOME/build//dir" }').build_root == Path("/build/dir")
    assert _parse_config_file_and_args(b'{ "build-root": "$HOME//build//dir" }').build_root == Path("/build/dir")
    assert _parse_config_file_and_args(b'{ "build-root": "${HOME}//build//dir" }').build_root == Path("/build/dir")


def test_relative_paths_in_config():
    # Check that relative paths in config files resolve relative to the file that it's being loaded from
    with tempfile.TemporaryDirectory() as td:
        configfile = Path(td, "config.json")
        subdir = Path(td, "subdir")
        subdir.mkdir()
        sub_configfile = subdir / "sub-config.json"
        sub_configfile.write_bytes(b'{ "build-root": "./build", "source-root": "../some-other-dir" }')
        configfile.write_bytes(b'{ "output-root": "./output", "#include": "./subdir/sub-config.json" }')
        config = _parse_arguments([], config_file=configfile)
        assert config.build_root == Path(td, "subdir/build")
        assert config.source_root == Path(td, "some-other-dir")
        assert config.output_root == Path(td, "output")
