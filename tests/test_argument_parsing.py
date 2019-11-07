import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import TestCase

# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from pycheribuild.configloader import ConfigLoader here because that will only update the local copy
from pycheribuild.config.loader import ConfigLoaderBase, JsonAndCommandLineConfigLoader, JsonAndCommandLineConfigOption

_loader = JsonAndCommandLineConfigLoader()
from pycheribuild.projects.project import SimpleProject, CrossCompileTarget

SimpleProject._configLoader = _loader
from pycheribuild.targets import targetManager, Target
from pycheribuild.config.defaultconfig import DefaultCheriConfig
# noinspection PyUnresolvedReferences
from pycheribuild.projects import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.cross import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.disk_image import BuildCheriBSDDiskImage
from pycheribuild.projects.cross.qt5 import BuildQtBase
from pycheribuild.projects.cross.cheribsd import BuildCHERIBSD
import pytest
import re

_targets_registered = False
Target.instantiating_targets_should_warn = False

try:
    import typing
except ImportError:
    typing = {}


# python 3.4 compatibility
def write_bytes(path: Path, contents: bytes):
    with path.open(mode='wb') as f:
        return f.write(contents)


# noinspection PyProtectedMember
def _parse_arguments(args, *, config_file=Path("/this/does/not/exist")) -> DefaultCheriConfig:
    global _targets_registered
    # noinspection PyGlobalUndefined
    global _cheriConfig
    if not _targets_registered:
        allTargetNames = list(sorted(targetManager.targetNames)) + ["__run_everything__"]
        ConfigLoaderBase._cheriConfig = DefaultCheriConfig(_loader, allTargetNames)
        SimpleProject._configLoader = _loader
        targetManager.registerCommandLineOptions()
        _targets_registered = True
    targetManager.reset()
    ConfigLoaderBase._cheriConfig.loader._configPath = config_file
    sys.argv = ["cheribuild.py"] + args
    ConfigLoaderBase._cheriConfig.loader.reload()
    ConfigLoaderBase._cheriConfig.load()
    # pprint.pprint(vars(ret))
    assert ConfigLoaderBase._cheriConfig
    return ConfigLoaderBase._cheriConfig

def _parse_config_file_and_args(config_file_contents: bytes, *args) -> DefaultCheriConfig:
    with tempfile.NamedTemporaryFile() as t:
        config = Path(t.name)
        write_bytes(config, config_file_contents)
        return _parse_arguments(list(args), config_file=config)

def test_skip_update():
    # default is false:
    assert not _parse_arguments(["--skip-configure"]).skipUpdate
    # check that --no-foo and --foo work:
    assert _parse_arguments(["--skip-update"]).skipUpdate
    assert not _parse_arguments(["--no-skip-update"]).skipUpdate
    # check config file
    with tempfile.NamedTemporaryFile() as t:
        config = Path(t.name)
        write_bytes(config, b'{ "skip-update": true}')
        assert _parse_arguments([], config_file=config).skipUpdate
        # command line overrides config file:
        assert _parse_arguments(["--skip-update"], config_file=config).skipUpdate
        assert not _parse_arguments(["--no-skip-update"], config_file=config).skipUpdate
        write_bytes(config, b'{ "skip-update": false}')
        assert not _parse_arguments([], config_file=config).skipUpdate
        # command line overrides config file:
        assert _parse_arguments(["--skip-update"], config_file=config).skipUpdate
        assert not _parse_arguments(["--no-skip-update"], config_file=config).skipUpdate

def test_per_project_override():
    config = _parse_arguments(["--skip-configure"])
    source_root = config.sourceRoot
    assert config.sdkDir is not None
    assert BuildCheriBSDDiskImage.get_instance(None, config).extraFilesDir == source_root / "extra-files"
    _parse_arguments(["--disk-image/extra-files=/foo/bar"])
    assert BuildCheriBSDDiskImage.get_instance(None, config).extraFilesDir == Path("/foo/bar/")
    _parse_arguments(["--disk-image/extra-files", "/bar/foo"])
    assert BuildCheriBSDDiskImage.get_instance(None, config).extraFilesDir == Path("/bar/foo/")
    # different source root should affect the value:
    _parse_arguments(["--source-root=/tmp"])
    assert BuildCheriBSDDiskImage.get_instance(None, config).extraFilesDir == Path("/tmp/extra-files")

    with tempfile.NamedTemporaryFile() as t:
        config_path = Path(t.name)
        write_bytes(config_path, b'{ "source-root": "/x"}')
        _parse_arguments([], config_file=config_path)
        assert BuildCheriBSDDiskImage.get_instance(None, config).extraFilesDir == Path("/x/extra-files")

        # check that source root can be overridden
        _parse_arguments(["--source-root=/y"])
        assert BuildCheriBSDDiskImage.get_instance(None, config).extraFilesDir == Path("/y/extra-files")

def test_cross_compile_project_inherits():
    # Parse args once to ensure targetManager is initialized
    config = _parse_arguments(["--skip-configure"])
    qtbase_class = targetManager.get_target_raw("qtbase")._project_class
    qtbase_default = targetManager.get_target_raw("qtbase").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildQtBase
    qtbase_native = targetManager.get_target_raw("qtbase-native").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildQtBase
    qtbase_mips = targetManager.get_target_raw("qtbase-mips").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildQtBase

    # Check that project name is the same:
    assert qtbase_default.projectName == qtbase_native.projectName
    assert qtbase_mips.projectName == qtbase_native.projectName
    # These classes were generated:
    assert qtbase_native.synthetic_base == qtbase_class
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
    _parse_arguments(["--qtbase/build-tests", "--qtbase-mips/no-build-tests"])
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
    _parse_config_file_and_args(b'{"qtbase/build-tests": true, "qtbase-mips/build-tests": false }')
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests"

    # And that cmdline still overrides JSON:
    _parse_config_file_and_args(b'{"qtbase/build-tests": true }', "--qtbase-mips/no-build-tests")
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a false override for build-tests"
    # But if a per-target option is set in the json that still overrides the default set on the cmdline
    _parse_config_file_and_args(b'{"qtbase-mips/build-tests": false }', "--qtbase/build-tests")
    assert qtbase_default.build_tests, "qtbase(default) build-tests should be set on cmdline"
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_mips.build_tests, "qtbase-mips should have a JSON false override for build-tests"

    # However, don't inherit for buildDir since that doesn't make sense:
    def assertBuildDirsDifferent():
        # Default should be CHERI purecap
        # print("Default build dir:", qtbase_default.buildDir)
        # print("Native build dir:", qtbase_native.buildDir)
        # print("Mips build dir:", qtbase_mips.buildDir)
        assert qtbase_default.buildDir != qtbase_native.buildDir
        assert qtbase_default.buildDir != qtbase_mips.buildDir
        assert qtbase_mips.buildDir != qtbase_native.buildDir

    assertBuildDirsDifferent()
    # overriding native build dir is fine:
    _parse_arguments(["--qtbase-native/build-directory=/foo/bar"])
    assertBuildDirsDifferent()
    _parse_config_file_and_args(b'{"qtbase-native/build-directory": "/foo/bar"}')
    assertBuildDirsDifferent()
    # Should not inherit from the default one:
    _parse_arguments(["--qtbase/build-directory=/foo/bar"])
    assertBuildDirsDifferent()
    _parse_config_file_and_args(b'{"qtbase/build-directory": "/foo/bar"}')
    assertBuildDirsDifferent()

    # Should not inherit from the default one:
    _parse_arguments(["--qtbase/build-directory=/foo/bar", "--qtbase-mips/build-directory=/bar/foo"])
    assertBuildDirsDifferent()
    _parse_config_file_and_args(b'{"qtbase/build-directory": "/foo/bar",'
                                     b' "qtbase-mips/build-directory": "/bar/foo"}')
    assertBuildDirsDifferent()


# FIXME: cheribsd-cheri/kernel-config should use the cheribsd/kernel-config value
def test_cheribsd_purecap_inherits_config_from_cheribsd():
    # Parse args once to ensure targetManager is initialized
    config = _parse_arguments(["--skip-configure"])
    cheribsd_class = targetManager.get_target_raw("cheribsd")._project_class
    cheribsd_default_tgt = targetManager.get_target_raw("cheribsd").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildCHERIBSD
    assert cheribsd_default_tgt.target == "cheribsd-cheri"
    cheribsd_mips = targetManager.get_target_raw("cheribsd-mips").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildCHERIBSD
    cheribsd_cheri = targetManager.get_target_raw("cheribsd-cheri").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildCHERIBSD
    cheribsd_purecap = targetManager.get_target_raw("cheribsd-purecap").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildCHERIBSD

    # Check that project name is the same:
    assert cheribsd_mips.projectName == cheribsd_cheri.projectName
    assert cheribsd_cheri.projectName == cheribsd_purecap.projectName

    # cheribsd-cheri is a synthetic class, but cheribsd-purecap inst:
    assert cheribsd_cheri.synthetic_base == cheribsd_class
    assert not hasattr(cheribsd_purecap, "synthetic_base")

    _parse_arguments(["--cheribsd-mips/build-tests"])
    assert not cheribsd_purecap.build_tests, "cheribsd-purecap build-tests should default to false"
    assert not cheribsd_cheri.build_tests, "cheribsd-cheri build-tests should default to false"
    _parse_arguments(["--cheribsd-purecap/build-tests"])
    assert cheribsd_purecap.build_tests, "cheribsd-purecap build-tests should be set on cmdline"
    assert not cheribsd_cheri.build_tests, "cheribsd-cheri build-tests should default to false"
    _parse_arguments(["--cheribsd-cheri/build-tests"])
    assert not cheribsd_purecap.build_tests, "cheribsd-purecap build-tests should default to false"
    assert cheribsd_cheri.build_tests, "cheribsd-cheri build-tests should be set on cmdline"

    # If the base cheribsd option is set but no per-target one use both cheribsd-cheri and cheribsd-purecap should inherit basic one:
    _parse_arguments(["--cheribsd/build-tests"])
    assert cheribsd_cheri.build_tests, "cheribsd-cheri should inherit build-tests from cheribsd(default)"
    assert cheribsd_purecap.build_tests, "cheribsd-purecap should inherit build-tests from cheribsd(default)"

    # But target-specific ones should override
    _parse_arguments(["--cheribsd/build-tests", "--cheribsd-purecap/no-build-tests"])
    assert cheribsd_cheri.build_tests, "cheribsd-cheri should inherit build-tests from cheribsd(default)"
    assert not cheribsd_purecap.build_tests, "cheribsd-purecap should have a false override for build-tests"

    _parse_arguments(["--cheribsd/build-tests", "--cheribsd-cheri/no-build-tests"])
    assert cheribsd_purecap.build_tests, "cheribsd-purecap should inherit build-tests from cheribsd(default)"
    assert not cheribsd_cheri.build_tests, "cheribsd-cheri should have a false override for build-tests"

    # Check that we hav ethe same behaviour when loading from json:
    _parse_config_file_and_args(b'{"cheribsd/build-tests": true }')
    assert cheribsd_purecap.build_tests, "cheribsd-purecap should inherit build-tests from cheribsd(default)"
    assert cheribsd_cheri.build_tests, "cheribsd-cheri should inherit build-tests from cheribsd(default)"
    assert cheribsd_mips.build_tests, "cheribsd-mips should inherit build-tests from cheribsd(default)"

    # But target-specific ones should override
    _parse_config_file_and_args(b'{"cheribsd/build-tests": true, "cheribsd-cheri/build-tests": false }')
    assert cheribsd_mips.build_tests, "cheribsd-mips build-tests should be inherited on cmdline"
    assert cheribsd_purecap.build_tests, "cheribsd-purecap should inherit build-tests from cheribsd(default)"
    assert not cheribsd_cheri.build_tests, "cheribsd-cheri should have a false override for build-tests"

    # And that cmdline still overrides JSON:
    _parse_config_file_and_args(b'{"cheribsd/build-tests": true }', "--cheribsd-cheri/no-build-tests")
    assert cheribsd_purecap.build_tests, "cheribsd-purecap should inherit build-tests from cheribsd(default)"
    assert cheribsd_mips.build_tests, "cheribsd-mips build-tests should be inherited from cheribsd(default)"
    assert not cheribsd_cheri.build_tests, "cheribsd-cheri should have a false override for build-tests"
    # But if a per-target option is set in the json that still overrides the default set on the cmdline
    _parse_config_file_and_args(b'{"cheribsd-cheri/build-tests": false }', "--cheribsd/build-tests")
    assert cheribsd_purecap.build_tests, "cheribsd-purecap should inherit build-tests from cheribsd(default)"
    assert cheribsd_mips.build_tests, "cheribsd-mips build-tests should be inherited from cheribsd(default)"
    assert not cheribsd_cheri.build_tests, "cheribsd-cheri should have a JSON false override for build-tests"

    # However, don't inherit for buildDir since that doesn't make sense:
    def assertBuildDirsDifferent():
        assert cheribsd_cheri.buildDir != cheribsd_purecap.buildDir
        assert cheribsd_cheri.buildDir != cheribsd_mips.buildDir
        assert cheribsd_cheri.buildDir == cheribsd_default_tgt.buildDir

    assertBuildDirsDifferent()
    # overriding native build dir is fine:
    _parse_arguments(["--cheribsd-purecap/build-directory=/foo/bar"])
    assert cheribsd_purecap.buildDir == Path("/foo/bar")
    assertBuildDirsDifferent()
    _parse_config_file_and_args(b'{"cheribsd-purecap/build-directory": "/foo/bar"}')
    assert cheribsd_purecap.buildDir == Path("/foo/bar")
    assertBuildDirsDifferent()
    # cheribsd-cheri should inherit from the default one, but not cheribsd-purecap:
    _parse_arguments(["--cheribsd/build-directory=/foo/bar"])
    assert cheribsd_cheri.buildDir == Path("/foo/bar")
    assert cheribsd_purecap.buildDir != Path("/foo/bar")
    assertBuildDirsDifferent()
    _parse_config_file_and_args(b'{"cheribsd/build-directory": "/foo/bar"}')
    assert cheribsd_cheri.buildDir == Path("/foo/bar")
    assert cheribsd_purecap.buildDir != Path("/foo/bar")
    assertBuildDirsDifferent()

    # cheribsd-cheri/builddir should have higher prirority:
    _parse_arguments(["--cheribsd/build-directory=/foo/bar", "--cheribsd-cheri/build-directory=/bar/foo"])
    assert cheribsd_cheri.buildDir == Path("/bar/foo")
    assertBuildDirsDifferent()
    _parse_config_file_and_args(b'{"cheribsd/build-directory": "/foo/bar",'
                                     b' "cheribsd-cheri/build-directory": "/bar/foo"}')
    assert cheribsd_cheri.buildDir == Path("/bar/foo")
    assertBuildDirsDifferent()


def test_kernconf():
    # Parse args once to ensure targetManager is initialized

    # check default values
    config = _parse_arguments([])
    cheribsd_cheri = targetManager.get_target_raw("cheribsd-cheri").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildCHERIBSD
    freebsd_mips = targetManager.get_target_raw("freebsd-mips").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildCHERIBSD
    freebsd_native = targetManager.get_target_raw("freebsd-x86_64").get_or_create_project(CrossCompileTarget.NONE, config)  # type: BuildCHERIBSD
    assert config.freebsd_kernconf is None
    attr = inspect.getattr_static(freebsd_mips, "kernelConfig")
    assert freebsd_mips.kernelConfig == "MALTA64"
    assert cheribsd_cheri.kernelConfig == "CHERI128_MALTA64"
    assert freebsd_native.kernelConfig == "GENERIC"

    # Check that --kernconf is used as the fallback
    config = _parse_arguments(["--kernconf=LINT", "--freebsd-mips/kernel-config=NOTMALTA64"])
    assert config.freebsd_kernconf == "LINT"
    attr = inspect.getattr_static(freebsd_mips, "kernelConfig")
    # previously we would replace the command line attribute with a string -> check this is no longer true
    assert isinstance(attr, JsonAndCommandLineConfigOption)
    assert freebsd_mips.kernelConfig == "NOTMALTA64"
    assert cheribsd_cheri.kernelConfig == "LINT"
    assert freebsd_native.kernelConfig == "LINT"

    config = _parse_arguments(["--kernconf=LINT", "--cheribsd-cheri/kernel-config=SOMETHING"])
    assert config.freebsd_kernconf == "LINT"
    assert freebsd_mips.kernelConfig == "LINT"
    assert cheribsd_cheri.kernelConfig == "SOMETHING"
    assert freebsd_native.kernelConfig == "LINT"


def test_duplicate_key():
    with pytest.raises(SyntaxError) as excinfo:
        _parse_config_file_and_args(b'{ "cheri-bits": 128, "some-other-key": "abc", "cheri-bits": 256 }')
        assert re.search("duplicate key: 'cheri-bits'", excinfo.value)

def _get_config_with_include(tmpdir: Path, config_json: bytes, workdir: Path = None):
    if not workdir:
        workdir = tmpdir
    config = workdir / "config.json"
    write_bytes(config, config_json)
    return _parse_arguments([], config_file=config)

def test_config_file_include():
    with tempfile.TemporaryDirectory() as d:
        config_dir = Path(d)
        write_bytes(config_dir / "128-common.json", b'{ "cheri-bits": 128 }')
        write_bytes(config_dir / "256-common.json", b'{ "cheri-bits": 256 }')
        write_bytes(config_dir / "common.json", b'{ "source-root": "/this/is/a/unit/test" }')

        # Check that the config file is parsed:
        result = _get_config_with_include(config_dir, b'{ "#include": "common.json"}')
        assert "/this/is/a/unit/test" == str(result.sourceRoot)

        # Check that the current file always has precendence
        result = _get_config_with_include(config_dir, b'{ "#include": "256-common.json", "cheri-bits": 128}')
        assert 128 == result.cheriBits
        result = _get_config_with_include(config_dir, b'{ "#include": "128-common.json", "cheri-bits": 256}')
        assert 256 == result.cheriBits
        # order doesn't matter since the #include is only evaluated after the whole file has been parsed:
        result = _get_config_with_include(config_dir, b'{ "cheri-bits": 128, "#include": "256-common.json"}')
        assert 128 == result.cheriBits
        result = _get_config_with_include(config_dir, b'{ "cheri-bits": 256, "#include": "128-common.json"}')
        assert 256 == result.cheriBits

        # TODO: handled nested cases: the level closest to the initial file wins
        write_bytes(config_dir / "change-source-root.json",
            b'{ "source-root": "/source/root/override", "#include": "common.json" }')
        result = _get_config_with_include(config_dir, b'{ "#include": "change-source-root.json"}')
        assert "/source/root/override" == str(result.sourceRoot)
        # And again the root file wins:
        result = _get_config_with_include(config_dir,
                                               b'{ "source-root": "/override/twice", "#include": "change-source-root.json"}')
        assert "/override/twice" == str(result.sourceRoot)
        # no matter in which order it is written:
        result = _get_config_with_include(config_dir,
                                               b'{ "#include": "change-source-root.json", "source-root": "/override/again"}')
        assert "/override/again" == str(result.sourceRoot)


        # Test merging of objects:
        write_bytes(config_dir / "change-smb-dir.json",
            b'{ "run": { "smb-host-directory": "/some/path" }, "#include": "common.json" }')
        result = _get_config_with_include(config_dir, b'{ "run": { "ssh-forwarding-port": 12345 }, "#include": "change-smb-dir.json" }')
        run_project = targetManager.get_target_raw("run").get_or_create_project(CrossCompileTarget.NONE, result)
        assert run_project.custom_qemu_smb_mount == Path("/some/path")
        assert run_project.sshForwardingPort == 12345


        with tempfile.TemporaryDirectory() as d2:
            # Check that relative paths work
            relpath = b"../" + str(Path(d).relative_to(Path(d2).parent)).encode("utf-8")
            result = _get_config_with_include(config_dir,
                                                   b'{ "#include": "' + relpath + b'/common.json" }', workdir=Path(d2))
            assert "/this/is/a/unit/test" == str(result.sourceRoot)

            # Check that absolute paths work as expected:
            abspath = b"" + str(Path(d)).encode("utf-8")
            result = _get_config_with_include(config_dir,
                                                   b'{ "#include": "' + abspath + b'/common.json" }', workdir=Path(d2))
            assert "/this/is/a/unit/test" == str(result.sourceRoot)

        # Nonexistant paths should raise an error
        with pytest.raises(FileNotFoundError) as excinfo:
            _get_config_with_include(config_dir, b'{ "#include": "bad-path.json"}')
            assert re.search("No such file or directory", excinfo.value)

        # Currently only one #include per config file is allowed
        # TODO: this could be supported but it might be better to accept a list instead?
        with pytest.raises(SyntaxError) as excinfo:
            _get_config_with_include(config_dir, b'{ "#include": "128-common.json", "foo": "bar", "#include": "256-common.json"}')
            assert re.search("duplicate key: '#include'", excinfo.value)


def test_libcxxrt_dependency_path():
    # Test that we pick the correct libunwind path when building libcxxrt
    def check_libunwind_path(path, target_name):
        tgt = targetManager.get_target_raw(target_name).get_or_create_project(CrossCompileTarget.NONE, config)
        for i in tgt.configureArgs:
            if i.startswith("-DLIBUNWIND_PATH="):
                assert i == ("-DLIBUNWIND_PATH=" + str(path)), tgt.configureArgs
                return
        assert False, "Should have found -DLIBUNWIND_PATH= in " + str(tgt.configureArgs)

    config = _parse_arguments(["--skip-configure"])
    check_libunwind_path(config.buildRoot / "libunwind-native-build/test-install-prefix/lib", "libcxxrt-native")
    check_libunwind_path(config.outputRoot / "rootfs128/opt/cheri128/c++/lib", "libcxxrt-cheri")
    check_libunwind_path(config.outputRoot / "rootfs128/opt/mips/c++/lib", "libcxxrt-mips")
    # Check the defaults:
    config = _parse_arguments(["--skip-configure", "--xhost"])
    check_libunwind_path(config.buildRoot / "libunwind-native-build/test-install-prefix/lib", "libcxxrt")
    check_libunwind_path(config.buildRoot / "libunwind-native-build/test-install-prefix/lib", "libcxxrt-native")
    config = _parse_arguments(["--skip-configure", "--xmips", "--no-use-hybrid-sysroot-for-mips"])
    check_libunwind_path(config.outputRoot / "rootfs-mips/opt/mips/c++/lib", "libcxxrt")
    check_libunwind_path(config.outputRoot / "rootfs-mips/opt/mips/c++/lib", "libcxxrt-mips")
    config = _parse_arguments(["--skip-configure", "--256"])
    check_libunwind_path(config.outputRoot / "rootfs256/opt/cheri256/c++/lib", "libcxxrt")
    check_libunwind_path(config.outputRoot / "rootfs256/opt/cheri256/c++/lib", "libcxxrt-cheri")
    config = _parse_arguments(["--skip-configure", "--128"])
    check_libunwind_path(config.outputRoot / "rootfs128/opt/cheri128/c++/lib", "libcxxrt")
    check_libunwind_path(config.outputRoot / "rootfs128/opt/cheri128/c++/lib", "libcxxrt-cheri")


@pytest.mark.parametrize("base_name,expected", [
    pytest.param("cheribsd", "cheribsd-cheri"),
    pytest.param("freebsd", "freebsd-x86_64"),
    pytest.param("newlib-baremetal", "newlib-baremetal-mips"),
    pytest.param("libcxxrt-baremetal", "libcxxrt-baremetal-mips"),
    pytest.param("compiler-rt-baremetal", "compiler-rt-builtins-baremetal-mips"),
])
def test_default_arch(base_name, expected):
    # The default target should be selected regardless of --xmips/--xhost/--128/--256 flags
    # Parse args once to ensure targetManager is initialized
    for default_flag in ("--xhost", "--xmips", "--256", "--128"):
        config = _parse_arguments(["--skip-configure", default_flag])
        target = targetManager.get_target(base_name, None, config)
        assert expected == target.name, "Failed for " + default_flag


@pytest.mark.parametrize("target,args,expected", [
    pytest.param("cheribsd", ["--foo"],
                 "cheribsd-128-build"),
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
    # No change for pcrel:
    pytest.param("cheribsd", ["--cap-table-abi=pcrel", "--subobject-bounds=conservative"],
                 "cheribsd-128-build"),
    # plt should be encoded
    pytest.param("cheribsd", ["--cap-table-abi=plt", "--subobject-bounds=conservative"],
                 "cheribsd-128-plt-build"),
    # everything
    pytest.param("cheribsd-purecap", ["--cap-table-abi=plt", "--subobject-bounds=aggressive", "--mips-float-abi=hard"],
                 "cheribsd-purecap-128-plt-aggressive-hardfloat-build"),
])
def test_default_arch(target: str, args: list, expected: str):
    # Check that the cheribsd build dir is correct
    config = _parse_arguments(args)
    target = targetManager.get_target(target, CrossCompileTarget.NONE, config, caller="test_default_arch")
    builddir = target.get_or_create_project(CrossCompileTarget.NONE, config).buildDir
    assert isinstance(builddir, Path)
    assert builddir.name == expected
