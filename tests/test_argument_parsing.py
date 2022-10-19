import collections
import inspect
import re
import sys
import tempfile
import typing
# noinspection PyUnresolvedReferences
from pathlib import Path

import pytest

# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from pycheribuild.configloader import ConfigLoader here because that will only update the local copy
from pycheribuild.config.compilation_targets import CompilationTargets, FreeBSDTargetInfo
from pycheribuild.config.defaultconfig import DefaultCheriConfig
from pycheribuild.config.loader import ConfigLoaderBase, ConfigOptionBase, JsonAndCommandLineConfigOption
# noinspection PyUnresolvedReferences
from pycheribuild.projects import *  # noqa: F401, F403
from pycheribuild.projects.cross import *  # noqa: F401, F403
from pycheribuild.projects.cross.cheribsd import (BuildCHERIBSD, BuildCheriBsdMfsKernel, BuildFreeBSD,
                                                  FreeBSDToolchainKind)
from pycheribuild.projects.cross.llvm import BuildCheriLLVM
from pycheribuild.projects.cross.qt5 import BuildQtBase
# noinspection PyProtectedMember
from pycheribuild.projects.disk_image import BuildCheriBSDDiskImage, BuildDiskImageBase
# Override the default config loader:
from pycheribuild.projects.simple_project import SimpleProject
from pycheribuild.projects.run_qemu import LaunchCheriBSD
from pycheribuild.targets import MultiArchTargetAlias, Target, target_manager


Target.instantiating_targets_should_warn = False

T = typing.TypeVar("T", bound=SimpleProject)


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
    ConfigLoaderBase._cheri_config._cached_deps = collections.defaultdict(dict)
    target_manager.reset()
    ConfigLoaderBase._cheri_config.loader._config_path = config_file
    sys.argv = ["cheribuild.py"] + args
    ConfigLoaderBase._cheri_config.loader.reset()
    ConfigLoaderBase._cheri_config.loader.is_running_unit_tests = True
    ConfigLoaderBase._cheri_config.loader.unknown_config_option_is_error = not allow_unknown_options
    ConfigLoaderBase._cheri_config.load()
    # pprint.pprint(vars(ret))
    assert isinstance(ConfigLoaderBase._cheri_config, DefaultCheriConfig)
    return ConfigLoaderBase._cheri_config


def _parse_config_file_and_args(config_file_contents: bytes, *args: str,
                                allow_unknown_options=False) -> DefaultCheriConfig:
    with tempfile.NamedTemporaryFile() as t:
        config = Path(t.name)
        config.write_bytes(config_file_contents)
        return _parse_arguments(list(args), config_file=config, allow_unknown_options=allow_unknown_options)


def test_skip_update():
    # default is false:
    conf = _parse_arguments(["--skip-configure"])
    skip = inspect.getattr_static(conf, "skip_update")
    assert isinstance(skip, JsonAndCommandLineConfigOption)
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


@pytest.mark.parametrize("args,expected", [
    pytest.param(["--include-dependencies", "run-riscv64-purecap"],
                 ["qemu", "llvm-native", "cheribsd-riscv64-purecap",
                  "gmp-riscv64-hybrid-for-purecap-rootfs", "gdb-riscv64-hybrid-for-purecap-rootfs",
                  "bbl-baremetal-riscv64-purecap", "disk-image-riscv64-purecap", "run-riscv64-purecap"],
                 id="run-include-deps"),
    pytest.param(["--include-dependencies", "--skip-sdk", "run-riscv64-purecap"],
                 ["bbl-baremetal-riscv64-purecap", "disk-image-riscv64-purecap", "run-riscv64-purecap"],
                 id="run-include-deps-skip-sdk"),
    pytest.param(["--include-dependencies", "--start-with=bbl-baremetal-riscv64-purecap", "run-riscv64-purecap"],
                 ["bbl-baremetal-riscv64-purecap", "disk-image-riscv64-purecap", "run-riscv64-purecap"],
                 id="run-start-with"),
    pytest.param(["--include-dependencies", "--start-after=bbl-baremetal-riscv64-purecap", "run-riscv64-purecap"],
                 ["disk-image-riscv64-purecap", "run-riscv64-purecap"],
                 id="run-start-after"),
])
def test_target_subsets(args: "list[str]", expected):
    config = _parse_arguments(args)
    selected = list(x.name for x in target_manager.get_all_chosen_targets(config))
    assert selected == expected


@pytest.mark.parametrize("args,expected", [
    pytest.param(["--include-dependencies", "--skip-sdk", "libx11-amd64"],
                 ["xorg-macros-amd64", "xorgproto-amd64", "xcbproto-amd64", "libxau-amd64", "xorg-pthread-stubs-amd64",
                  "libxcb-amd64", "libxtrans-amd64", "libx11-amd64"],
                 id="libx11-amd64"),
    pytest.param(["--include-dependencies", "--skip-sdk", "--skip-dependency-filter=libxau-amd64", "libx11-amd64"],
                 ["xorg-macros-amd64", "xorgproto-amd64", "xcbproto-amd64", "xorg-pthread-stubs-amd64",
                  "libxcb-amd64", "libxtrans-amd64", "libx11-amd64"],
                 id="libx11-amd64-withtout-libxau"),
    pytest.param(["--include-dependencies", "--skip-sdk", "--skip-dependency-filter=qtbase.*", "kcoreaddons-amd64"],
                 ["extra-cmake-modules-amd64", "kcoreaddons-amd64"],
                 id="kcoreaddons-amd64-without-qtbase"),
    pytest.param(["--include-dependencies", "--skip-sdk", "--qtbase-native/minimal",  # skip native deps
                  "--skip-dependency-filter=libx.*", "--skip-dependency-filter=xorg.*", "kauth-amd64"],
                 ["shared-mime-info-native", "shared-mime-info-amd64", "sqlite-amd64", "libice-amd64", "libsm-amd64",
                  "libpng-amd64", "libjpeg-turbo-amd64", "dejavu-fonts-amd64", "libexpat-amd64",
                  "dbus-amd64", "freetype2-amd64", "fontconfig-amd64", "linux-input-h-amd64", "mtdev-amd64",
                  "libevdev-amd64", "libudev-devd-amd64", "epoll-shim-amd64", "libinput-amd64", "libglvnd-amd64",
                  "libpciaccess-amd64", "libdrm-amd64", "qtbase-amd64", "extra-cmake-modules-amd64",
                  "kcoreaddons-amd64", 'sqlite-native', "qtbase-native", "extra-cmake-modules-native",
                  "kcoreaddons-native", "kauth-amd64"],
                 id="kauth-amd64-full-without-x11"),
    pytest.param(["--include-dependencies", "--skip-sdk", "--qtbase-native/minimal",  # skip native X11 deps
                  "--skip-dependency-filter=libx.*", "--skip-dependency-filter=xorg.*", "--skip-dependency-filter=qt.*",
                  "kauth-amd64"],
                 ["extra-cmake-modules-amd64", "kcoreaddons-amd64", "extra-cmake-modules-native", "kcoreaddons-native",
                  "kauth-amd64"],
                 id="kauth-amd64-without-qt-without-x11"),  # skips most dependencies but includes kcoreaddons-native
])
def test_skip_dependency_regex(args: "list[str]", expected):
    config = _parse_arguments(args)
    selected = list(x.name for x in target_manager.get_all_chosen_targets(config))
    assert selected == expected


def test_invalid_skip_dependency_regex():
    with pytest.raises(re.error, match="missing \\), unterminated subpattern at position 3"):
        _parse_arguments(["--include-dependencies", "--skip-sdk", "--skip-dependency-filter=abc("])


@pytest.mark.parametrize("args,exception_type,errmessage", [
    pytest.param(["--include-dependencies", "--skip-sdk", "--start-after=llvm-project", "run-riscv64-purecap"],
                 ValueError, "--start-after/--start-with target 'llvm-project' is not being built",
                 id="run-start-after-skip-sdk"),
    pytest.param(["--include-dependencies", "--skip-sdk", "--start-with=llvm-project", "run-riscv64-purecap"],
                 ValueError, "--start-after/--start-with target 'llvm-project' is not being built",
                 id="run-start-with-skip-sdk"),
    pytest.param(["--include-dependencies", "--skip-sdk", "--start-after=run-riscv64-purecap", "run-riscv64-purecap"],
                 ValueError, "selected target list is empty after --start-after/--start-with filtering",
                 id="run-start-after-empty"),
])
def test_target_subsets_bad(args: "list[str]", exception_type, errmessage: str):
    with pytest.raises(exception_type, match=errmessage):
        target_manager.get_all_chosen_targets(_parse_arguments(args))


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

    # These used to have defaults but that is confusing now. So check that they no longer have default values
    pytest.param("cheribsd", None),
    pytest.param("disk-image", None),
    pytest.param("run", None),
    pytest.param("freebsd", None),
    pytest.param("disk-image-freebsd", None),
    pytest.param("disk-image-freebsd", None),
    pytest.param("qtbase", None),
    pytest.param("libcxx", None),
])
def test_target_aliases_default_target(target_name, resolved_target):
    # Check that only some targets (e.g. llvm) have a default target and that we have to explicitly
    # specify the target name for e.g. cheribsd-* run-*, etc
    if resolved_target is None:
        # The target should not exist in the list of targets accepted on the command line
        assert target_name not in target_manager.target_names(None)
        # However, if we use get_target_raw we should get the TargetAlias
        assert isinstance(target_manager.get_target_raw(target_name), MultiArchTargetAlias)
        assert target_manager.get_target_raw(target_name).project_class.default_architecture is None
    else:
        assert target_name in target_manager.target_names(None)
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
    qtbase_riscv = _get_target_instance("qtbase-riscv64", config, BuildQtBase)

    # Check that project name is the same:
    assert qtbase_riscv.default_directory_basename == qtbase_native.default_directory_basename
    # These classes were generated:
    # noinspection PyUnresolvedReferences
    assert qtbase_native.synthetic_base == qtbase_class
    # noinspection PyUnresolvedReferences
    assert qtbase_riscv.synthetic_base == qtbase_class
    assert not hasattr(qtbase_class, "synthetic_base")

    # Now check a property that should be inherited:
    _parse_arguments(["--qtbase-native/build-tests"])
    assert qtbase_native.build_tests, "qtbase-native build-tests should be set on cmdline"
    assert not qtbase_riscv.build_tests, "qtbase-mips build-tests should default to false"
    # If the base qtbase option is set but no per-target one use the basic one:
    _parse_arguments(["--qtbase/build-tests"])
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert qtbase_riscv.build_tests, "qtbase-mips should inherit build-tests from qtbase(default)"

    # But target-specific ones should override
    _parse_arguments(["--qtbase/build-tests", "--qtbase-riscv64/no-build-tests"])
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_riscv.build_tests, "qtbase-mips should have a false override for build-tests"

    # Check that we have the same behaviour when loading from json:
    _parse_config_file_and_args(b'{"qtbase-native/build-tests": true }')
    assert qtbase_native.build_tests, "qtbase-native build-tests should be set on cmdline"
    assert not qtbase_riscv.build_tests, "qtbase-mips build-tests should default to false"
    # If the base qtbase option is set but no per-target one use the basic one:
    _parse_config_file_and_args(b'{"qtbase/build-tests": true }')
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert qtbase_riscv.build_tests, "qtbase-mips should inherit build-tests from qtbase(default)"

    # But target-specific ones should override
    _parse_config_file_and_args(b'{"qtbase/build-tests": true, "qtbase-riscv64/build-tests": false }')
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_riscv.build_tests, "qtbase-mips should have a false override for build-tests"

    # And that cmdline still overrides JSON:
    _parse_config_file_and_args(b'{"qtbase/build-tests": true }', "--qtbase-riscv64/no-build-tests")
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_riscv.build_tests, "qtbase-mips should have a false override for build-tests"
    # But if a per-target option is set in the json that still overrides the default set on the cmdline
    _parse_config_file_and_args(b'{"qtbase-riscv64/build-tests": false }', "--qtbase/build-tests")
    assert qtbase_native.build_tests, "qtbase-native should inherit build-tests from qtbase(default)"
    assert not qtbase_riscv.build_tests, "qtbase-mips should have a JSON false override for build-tests"


def test_build_dir_not_inherited():
    # build-directory config option should only be added allowed for suffixed targets (and never inherited)
    config = _parse_arguments([])
    mfs_riscv64_purecap = _get_target_instance("cheribsd-mfs-root-kernel-riscv64-purecap", config,
                                               BuildCheriBsdMfsKernel)
    cheribsd_riscv64_purecap = _get_target_instance("cheribsd-riscv64-purecap", config, BuildCHERIBSD)

    _parse_arguments(["--cheribsd-riscv64-purecap/build-directory=/foo/bar"])
    assert cheribsd_riscv64_purecap.build_dir == Path("/foo/bar")
    assert mfs_riscv64_purecap.build_dir.name == "cheribsd-riscv64-purecap-build"
    # An unsuffixed build-directory argument should not be allowed
    with pytest.raises(KeyError, match="error: unknown argument '--cheribsd/build-directory=/foo/bar'"):
        _parse_arguments(["--cheribsd/build-directory=/foo/bar"])
    with pytest.raises(ValueError, match="^Unknown config option 'cheribsd/build-directory'$"):
        _parse_config_file_and_args(b'{"cheribsd/build-directory": "/foo/bar"}')

    # The only exception are targets that have a default architecture (in which case the unsuffixed version can be used)
    llvm_native = _get_target_instance("llvm-native", config, BuildCheriLLVM)
    llvm_riscv64 = _get_target_instance("llvm-riscv64", config, BuildCheriLLVM)
    _parse_arguments(["--llvm-native/build-directory=/override1"])
    assert llvm_native.build_dir == Path("/override1")
    assert llvm_riscv64.build_dir.name == "llvm-project-riscv64-build"
    # Unsuffixed config options should be accepted for targets with a default architecture:
    _parse_arguments(["--llvm/build-directory=/override2"])
    assert llvm_native.build_dir == Path("/override2")
    assert llvm_riscv64.build_dir.name == "llvm-project-riscv64-build"
    # If an unsuffixed config option exists, the suffixed version is preferred
    _parse_arguments(["--llvm/build-directory=/generic", "--llvm/build-directory=/suffixed"])
    assert llvm_native.build_dir == Path("/suffixed")
    assert llvm_riscv64.build_dir.name == "llvm-project-riscv64-build"


def test_cheribsd_purecap_inherits_config_from_cheribsd():
    # Parse args once to ensure target_manager is initialized
    config = _parse_arguments(["--skip-configure"])
    cheribsd_class = target_manager.get_target_raw("cheribsd").project_class
    cheribsd_riscv = _get_cheribsd_instance("cheribsd-riscv64", config)
    cheribsd_riscv_hybrid = _get_cheribsd_instance("cheribsd-riscv64-hybrid", config)
    cheribsd_riscv_purecap = _get_cheribsd_instance("cheribsd-riscv64-purecap", config)

    # Check that project name is the same:
    assert cheribsd_riscv.default_directory_basename == cheribsd_riscv_hybrid.default_directory_basename
    assert cheribsd_riscv_hybrid.default_directory_basename == cheribsd_riscv_purecap.default_directory_basename

    # noinspection PyUnresolvedReferences
    assert cheribsd_riscv_hybrid.synthetic_base == cheribsd_class
    # noinspection PyUnresolvedReferences
    assert cheribsd_riscv_purecap.synthetic_base == cheribsd_class

    _parse_arguments(["--cheribsd-riscv64/debug-kernel"])
    assert not cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap debug-kernel should default to false"
    assert not cheribsd_riscv_hybrid.debug_kernel, "cheribsd-mips-hybrid debug-kernel should default to false"
    assert cheribsd_riscv.debug_kernel, "cheribsd-riscv64 debug-kernel should be set on cmdline"
    _parse_arguments(["--cheribsd-riscv64-purecap/debug-kernel"])
    assert cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap debug-kernel should be set on cmdline"
    assert not cheribsd_riscv_hybrid.debug_kernel, "cheribsd-mips-hybrid debug-kernel should default to false"
    assert not cheribsd_riscv.debug_kernel, "cheribsd-riscv64 debug-kernel should default to false"
    _parse_arguments(["--cheribsd-riscv64-hybrid/debug-kernel"])
    assert not cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap debug-kernel should default to false"
    assert cheribsd_riscv_hybrid.debug_kernel, "cheribsd-riscv64-hybrid debug-kernel should be set on cmdline"
    assert not cheribsd_riscv.debug_kernel, "cheribsd-riscv64 debug-kernel should default to false"

    # If the base cheribsd option is set but no per-target one use both cheribsd-riscv64-hybrid and cheribsd-purecap
    # should    # inherit basic one:
    _parse_arguments(["--cheribsd/debug-kernel"])
    assert cheribsd_riscv_hybrid.debug_kernel, "riscv64-hybrid should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"

    # But target-specific ones should override
    _parse_arguments(["--cheribsd/debug-kernel", "--cheribsd-riscv64-purecap/no-debug-kernel"])
    assert cheribsd_riscv_hybrid.debug_kernel, "riscv64-hybrid should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap should have a false override for debug-kernel"

    _parse_arguments(["--cheribsd/debug-kernel", "--cheribsd-riscv64-hybrid/no-debug-kernel"])
    assert cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_riscv_hybrid.debug_kernel, "riscv64-hybrid should have a false override for debug-kernel"

    # Check that we have the same behaviour when loading from json:
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true }')
    assert cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_riscv_hybrid.debug_kernel, "riscv64-hybrid should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_riscv.debug_kernel, "cheribsd-mips should inherit debug-kernel from cheribsd(default)"

    # But target-specific ones should override
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true, "cheribsd-riscv64-hybrid/debug-kernel": false }')
    assert cheribsd_riscv.debug_kernel, "cheribsd-mips debug-kernel should be inherited on cmdline"
    assert cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert not cheribsd_riscv_hybrid.debug_kernel, "riscv64-hybrid should have a false override for debug-kernel"

    # And that cmdline still overrides JSON:
    _parse_config_file_and_args(b'{"cheribsd/debug-kernel": true }', "--cheribsd-riscv64-hybrid/no-debug-kernel")
    assert cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_riscv.debug_kernel, "cheribsd-mips debug-kernel should be inherited from cheribsd(default)"
    assert not cheribsd_riscv_hybrid.debug_kernel, "riscv64-hybrid should have a false override for debug-kernel"
    # But if a per-target option is set in the json that still overrides the default set on the cmdline
    _parse_config_file_and_args(b'{"cheribsd-riscv64-hybrid/debug-kernel": false }', "--cheribsd/debug-kernel")
    assert cheribsd_riscv_purecap.debug_kernel, "cheribsd-purecap should inherit debug-kernel from cheribsd(default)"
    assert cheribsd_riscv.debug_kernel, "cheribsd-mips debug-kernel should be inherited from cheribsd(default)"
    assert not cheribsd_riscv_hybrid.debug_kernel, "riscv64-hybrid should have a JSON false override for debug-kernel"


def test_kernconf():
    # The kernel-config command line option is special: There is a global (command-line-only) flag that is used
    # as the default, but otherwise there should be no inheritance
    config = _parse_arguments([])
    cheribsd_riscv_hybrid = _get_cheribsd_instance("cheribsd-riscv64-hybrid", config)
    cheribsd_riscv = _get_cheribsd_instance("cheribsd-riscv64", config)
    freebsd_riscv = _get_target_instance("freebsd-riscv64", config, BuildFreeBSD)
    freebsd_native = _get_target_instance("freebsd-amd64", config, BuildFreeBSD)
    assert config.freebsd_kernconf is None
    assert freebsd_riscv.kernel_config == "QEMU"
    assert cheribsd_riscv_hybrid.kernel_config == "CHERI-QEMU"
    assert freebsd_native.kernel_config == "GENERIC"

    # Check that --kernconf is used as the fallback
    config = _parse_arguments(["--kernconf=LINT", "--freebsd-riscv64/kernel-config=FOO"])
    assert config.freebsd_kernconf == "LINT"
    attr = inspect.getattr_static(freebsd_riscv, "kernel_config")
    # previously we would replace the command line attribute with a string -> check this is no longer true
    assert isinstance(attr, JsonAndCommandLineConfigOption)
    assert freebsd_riscv.kernel_config == "FOO"
    assert cheribsd_riscv_hybrid.kernel_config == "LINT"
    assert freebsd_native.kernel_config == "LINT"

    config = _parse_arguments(["--kernconf=LINT", "--cheribsd-riscv64-hybrid/kernel-config=SOMETHING"])
    assert config.freebsd_kernconf == "LINT"
    assert freebsd_riscv.kernel_config == "LINT"
    assert cheribsd_riscv_hybrid.kernel_config == "SOMETHING"
    assert freebsd_native.kernel_config == "LINT"

    config = _parse_config_file_and_args(b'{ "cheribsd-riscv64/kernel-config": "RISCV64_CONFIG" }',
                                         "--kernconf=GENERIC")
    assert config.freebsd_kernconf == "GENERIC"
    assert cheribsd_riscv_hybrid.kernel_config == "GENERIC"
    assert cheribsd_riscv.kernel_config == "RISCV64_CONFIG"
    assert freebsd_riscv.kernel_config == "GENERIC"
    assert freebsd_native.kernel_config == "GENERIC"

    # kernel-config/--kernconf should only be valid on the command line:
    with pytest.raises(ValueError, match="^Unknown config option 'freebsd/kernel-config'$"):
        _parse_config_file_and_args(b'{ "freebsd/kernel-config": "GENERIC" }')
    # kernel-config/--kernconf should only be valid on the command line:
    with pytest.raises(ValueError, match="^Option 'kernel-config' cannot be used in the config file$"):
        _parse_config_file_and_args(b'{ "kernel-config": "GENERIC" }')
    with pytest.raises(ValueError, match="^Option 'kernconf' cannot be used in the config file$"):
        _parse_config_file_and_args(b'{ "kernconf": "GENERIC" }')

    # There should not be any unsuffixed kernel-config options:
    for tgt in ("cheribsd", "freebsd", "cheribsd-mfs-root-kernel"):
        with pytest.raises(KeyError, match=r"error: unknown argument '--[\w-]+/kernel-config'"):
            _parse_arguments(["--" + tgt + "/source-directory=/foo", "--" + tgt + "/kernel-config", "ABC"])


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
    check_libunwind_path(config.output_root / "rootfs-riscv64-purecap/opt/riscv64-purecap/c++/lib",
                         "libcxxrt-riscv64-purecap")
    check_libunwind_path(config.output_root / "rootfs-riscv64/opt/riscv64/c++/lib",
                         "libcxxrt-riscv64")
    # Check the defaults:
    config = _parse_arguments(["--skip-configure"])
    check_libunwind_path(config.build_root / "libunwind-native-build/test-install-prefix/lib", "libcxxrt-native")
    config = _parse_arguments(["--skip-configure"])
    check_libunwind_path(config.output_root / "rootfs-riscv64/opt/riscv64/c++/lib",
                         "libcxxrt-riscv64")
    check_libunwind_path(config.output_root / "rootfs-riscv64/opt/riscv64/c++/lib", "libcxxrt-riscv64")


class SystemClangIfExistsElse:
    def __init__(self, fallback: str):
        self.fallback = fallback


@pytest.mark.parametrize("target,expected_path,kind,extra_args", [
    # FreeBSD targets default to system clang if it exists, otherwise LLVM:
    pytest.param("freebsd-riscv64", SystemClangIfExistsElse("$OUTPUT$/upstream-llvm/bin/clang"),
                 FreeBSDToolchainKind.DEFAULT_COMPILER, []),
    pytest.param("freebsd-riscv64", "$OUTPUT$/upstream-llvm/bin/clang", FreeBSDToolchainKind.UPSTREAM_LLVM, []),
    pytest.param("freebsd-riscv64", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.CHERI_LLVM, []),
    pytest.param("freebsd-riscv64", "$BUILD$/freebsd-riscv64-build/tmp/usr/bin/clang",
                 FreeBSDToolchainKind.BOOTSTRAPPED, []),
    pytest.param("freebsd-riscv64", "/path/to/custom/toolchain/bin/clang", FreeBSDToolchainKind.CUSTOM,
                 ["--freebsd-riscv64/toolchain-path", "/path/to/custom/toolchain"]),

    # CheriBSD-mips can be built with all these toolchains (but defaults to CHERI LLVM):
    pytest.param("cheribsd-riscv64", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.DEFAULT_COMPILER, []),
    pytest.param("cheribsd-riscv64", "$OUTPUT$/upstream-llvm/bin/clang", FreeBSDToolchainKind.UPSTREAM_LLVM, []),
    pytest.param("cheribsd-riscv64", "$OUTPUT$/sdk/bin/clang", FreeBSDToolchainKind.CHERI_LLVM, []),
    pytest.param("cheribsd-riscv64", "$BUILD$/cheribsd-riscv64-build/tmp/usr/bin/clang",
                 FreeBSDToolchainKind.BOOTSTRAPPED, []),
    pytest.param("cheribsd-riscv64", "/path/to/custom/toolchain/bin/clang", FreeBSDToolchainKind.CUSTOM,
                 ["--cheribsd-riscv64/toolchain-path", "/path/to/custom/toolchain"]),
])
def test_freebsd_toolchains(target: str, expected_path, kind: FreeBSDToolchainKind, extra_args):
    # Avoid querying bmake for the objdir
    args = ["--" + target + "/toolchain", kind.value, "--build-root=/some/path/that/does/not/exist", "--pretend"]
    args.extend(extra_args)
    config = _parse_arguments(args)
    project = _get_target_instance(target, config, BuildFreeBSD)
    if isinstance(expected_path, SystemClangIfExistsElse):
        clang_root, _, _ = project._try_find_compatible_system_clang()
        expected_path = str(clang_root / "bin/clang") if clang_root is not None else expected_path.fallback
    expected_path = expected_path.replace("$OUTPUT$", str(config.output_root))
    expected_path = expected_path.replace("$BUILD$", str(config.build_root))
    assert str(project.CC) == str(expected_path)
    if kind == FreeBSDToolchainKind.BOOTSTRAPPED:
        kernel_make_args = project.kernel_make_args_for_config("GENERIC", None)
        # If we override CC, we have to also override XCC
        for (var, default) in (("CC", "cc"), ("CPP", "cpp"), ("CXX", "c++")):
            if var in project.buildworld_args.env_vars:
                assert project.buildworld_args.env_vars.get("X" + var, None) == default
                assert kernel_make_args.env_vars.get("X" + var, None) == default
            else:
                assert "X" + var not in project.buildworld_args.env_vars
                assert "X" + var not in kernel_make_args.env_vars
    else:
        assert project.buildworld_args.env_vars.get("XCC", None) == expected_path
        assert project.kernel_make_args_for_config("GENERIC", None).env_vars.get("XCC", None) == expected_path


@pytest.mark.parametrize("target,expected_name", [
    # CheriBSD
    pytest.param("disk-image-riscv64", "cheribsd-riscv64.img"),
    pytest.param("disk-image-riscv64-hybrid", "cheribsd-riscv64-hybrid.img"),
    pytest.param("disk-image-riscv64-purecap", "cheribsd-riscv64-purecap.img"),
    pytest.param("disk-image-amd64", "cheribsd-amd64.img"),
    pytest.param("disk-image-morello-hybrid", "cheribsd-morello-hybrid.img"),
    pytest.param("disk-image-morello-purecap", "cheribsd-morello-purecap.img"),
    # Minimal image
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
    project = _get_target_instance(target, config, BuildDiskImageBase)
    assert str(project.disk_image_path) == str(config.output_root / expected_name)


@pytest.mark.parametrize("target,config_options,expected_name,extra_kernels", [
    # RISCV kernconf tests
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/no-build-alternate-abi-kernels"],
                 "CHERI-QEMU",
                 []),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/build-fpga-kernels"],
                 "CHERI-QEMU",
                 ["CHERI-PURECAP-QEMU"]),
    pytest.param("cheribsd-riscv64-purecap",
                 [],
                 "CHERI-QEMU",
                 ["CHERI-PURECAP-QEMU"]),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/build-alternate-abi-kernels",
                  "--cheribsd/default-kernel-abi", "purecap"],
                 "CHERI-PURECAP-QEMU",
                 ["CHERI-QEMU"]),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/build-fett-kernels",
                  "--cheribsd/no-build-alternate-abi-kernels"],
                 "CHERI-QEMU-FETT",
                 ["CHERI-QEMU"]),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/build-fett-kernels"],
                 "CHERI-QEMU-FETT",
                 ["CHERI-QEMU",
                  "CHERI-PURECAP-QEMU"]),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/build-bench-kernels",
                  "--cheribsd/no-build-alternate-abi-kernels"],
                 "CHERI-QEMU",
                 ["CHERI-QEMU-NODEBUG"]),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/build-bench-kernels"],
                 "CHERI-QEMU",
                 ["CHERI-QEMU-NODEBUG",
                  "CHERI-PURECAP-QEMU-NODEBUG",
                  "CHERI-PURECAP-QEMU"]),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/build-fett-kernels",
                  "--cheribsd/build-fpga-kernels",
                  "--cheribsd/no-build-alternate-abi-kernels"],
                 "CHERI-QEMU-FETT",
                 ["CHERI-QEMU",
                  "CHERI-FETT"]),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cheribsd/build-fett-kernels",
                  "--cheribsd/build-fpga-kernels"],
                 "CHERI-QEMU-FETT",
                 ["CHERI-QEMU",
                  "CHERI-PURECAP-QEMU",
                  "CHERI-FETT",
                  "CHERI-PURECAP-FETT"]),
    # Morello kernconf tests
    pytest.param("cheribsd-aarch64",
                 [],
                 "GENERIC",
                 []),
    pytest.param("cheribsd-morello-purecap",
                 ["--cheribsd/no-build-alternate-abi-kernels"],
                 "GENERIC-MORELLO",
                 []),
    pytest.param("cheribsd-morello-purecap",
                 [],
                 "GENERIC-MORELLO",
                 ["GENERIC-MORELLO-PURECAP"]),
    # FreeBSD kernel configs
    pytest.param("freebsd-i386", [], "GENERIC", []),
    pytest.param("freebsd-aarch64", [], "GENERIC", []),
    pytest.param("freebsd-amd64", [], "GENERIC", []),
    pytest.param("freebsd-riscv64", [], "QEMU", []),
    pytest.param("freebsd-mips64", [], "MALTA64", []),
    pytest.param("freebsd-with-default-options-i386", [], "GENERIC", []),
    pytest.param("freebsd-with-default-options-aarch64", [], "GENERIC", []),
    pytest.param("freebsd-with-default-options-amd64", [], "GENERIC", []),
    pytest.param("freebsd-with-default-options-riscv64", [], "QEMU", []),
    pytest.param("freebsd-with-default-options-mips64", [], "MALTA64", []),
])
def test_kernel_configs(target, config_options: "list[str]", expected_name, extra_kernels):
    config = _parse_arguments(config_options)
    project = _get_target_instance(target, config, BuildFreeBSD)
    assert project.kernel_config == expected_name
    assert project.extra_kernels == extra_kernels


@pytest.mark.parametrize("target,config_options,expected_kernels", [
    # RISCV kernconf tests
    pytest.param("cheribsd-mfs-root-kernel-riscv64",
                 [],
                 ["QEMU-MFS-ROOT"]),
    pytest.param("cheribsd-mfs-root-kernel-riscv64",
                 ["--cheribsd-mfs-root-kernel-riscv64/build-fpga-kernels"],
                 ["QEMU-MFS-ROOT",
                  "GFE"]),
    pytest.param("cheribsd-mfs-root-kernel-riscv64-purecap",
                 ["--cheribsd-mfs-root-kernel-riscv64-purecap/build-fpga-kernels",
                  "--cheribsd-mfs-root-kernel-riscv64-purecap/no-build-alternate-abi-kernels"],
                 ["CHERI-QEMU-MFS-ROOT",
                  "CHERI-GFE"]),
    pytest.param("cheribsd-mfs-root-kernel-riscv64-purecap",
                 ["--cheribsd-mfs-root-kernel-riscv64-purecap/build-fpga-kernels"],
                 ["CHERI-QEMU-MFS-ROOT",
                  "CHERI-PURECAP-QEMU-MFS-ROOT",
                  "CHERI-GFE",
                  "CHERI-PURECAP-GFE"]),
    pytest.param("cheribsd-mfs-root-kernel-riscv64-purecap",
                 ["--cheribsd-mfs-root-kernel-riscv64-purecap/build-fpga-kernels",
                  "--cheribsd-mfs-root-kernel-riscv64-purecap/build-alternate-abi-kernels",
                  "--cheribsd-mfs-root-kernel-riscv64-purecap/kernel-config=CHERI-QEMU-MFS-ROOT"],
                 ["CHERI-QEMU-MFS-ROOT"]),
])
def test_mfsroot_kernel_configs(target: str, config_options: "list[str]", expected_kernels: "list[str]"):
    config = _parse_arguments(config_options)
    project = _get_target_instance(target, config, BuildCheriBsdMfsKernel)
    assert project.get_kernel_configs(None) == expected_kernels


# noinspection PyTypeChecker
def test_freebsd_toolchains_cheribsd_purecap():
    # Targets that need CHERI don't have the --toolchain option:
    # Argparse should exit with exit code 2
    with pytest.raises(KeyError, match=r"error: unknown argument '--[\w-]+/toolchain'"):
        for i in FreeBSDToolchainKind:
            test_freebsd_toolchains("cheribsd-riscv64-hybrid", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-riscv64-purecap", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-morello-hybrid", "/wrong/path", i, [])
            test_freebsd_toolchains("cheribsd-morello-purecap", "/wrong/path", i, [])


@pytest.mark.parametrize("target,args,expected", [
    pytest.param("cheribsd-riscv64-hybrid", [], "cheribsd-riscv64-hybrid-build"),
    pytest.param("llvm", [], "llvm-project-build"),
    pytest.param("cheribsd-riscv64-purecap", [], "cheribsd-riscv64-purecap-build"),
    # --subobject debug should not have any effect if subobject bounds is disabled
    pytest.param("cheribsd-riscv64-purecap", ["--subobject-bounds=conservative", "--subobject-debug"],
                 "cheribsd-riscv64-purecap-build"),
    pytest.param("cheribsd-riscv64-purecap", ["--subobject-bounds=subobject-safe", "--subobject-debug"],
                 "cheribsd-riscv64-purecap-subobject-safe-build"),
    pytest.param("cheribsd-riscv64-purecap", ["--subobject-bounds=subobject-safe", "--no-subobject-debug"],
                 "cheribsd-riscv64-purecap-subobject-safe-subobject-nodebug-build"),
    # Passing "--cap-table-abi=pcrel" also changes the build dir even though it's (currently) the default for all
    # architectures.
    pytest.param("cheribsd-riscv64-hybrid", ["--cap-table-abi=pcrel", "--subobject-bounds=conservative"],
                 "cheribsd-riscv64-hybrid-pcrel-build"),
    # plt should be encoded
    pytest.param("cheribsd-riscv64-hybrid", ["--cap-table-abi=plt", "--subobject-bounds=conservative"],
                 "cheribsd-riscv64-hybrid-plt-build"),
    # plt should be encoded
    pytest.param("sqlite-riscv64-purecap", [], "sqlite-riscv64-purecap-build"),
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
    pytest.param("cheribsd-riscv64-purecap", ["--cap-table-abi=pcrel", "--subobject-bounds=conservative"],
                 "sdk/sysroot-riscv64-purecap-pcrel", "rootfs-riscv64-purecap-pcrel"),
    pytest.param("cheribsd-riscv64-purecap", ["--cap-table-abi=plt", "--subobject-bounds=conservative"],
                 "sdk/sysroot-riscv64-purecap-plt", "rootfs-riscv64-purecap-plt"),
    pytest.param("cheribsd-riscv64-purecap",
                 ["--cap-table-abi=plt", "--subobject-bounds=aggressive"],
                 "sdk/sysroot-riscv64-purecap-plt-aggressive",
                 "rootfs-riscv64-purecap-plt-aggressive"),

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
    assert project.cross_sysroot_path == project.target_info.sysroot_dir
    assert isinstance(project.target_info, FreeBSDTargetInfo)
    sysroot_dir = project.target_info.get_non_rootfs_sysroot_dir()
    assert str(sysroot_dir.relative_to(config.output_root)) == expected_sysroot
    rootfs_dir = project.install_dir
    assert str(rootfs_dir.relative_to(config.output_root)) == expected_rootfs


def _check_source_dir(target: str, expected: str, config_file: bytes, cmdline: typing.List[str]):
    config = _parse_config_file_and_args(config_file, *cmdline)
    project = _get_target_instance(target, config)
    # noinspection PyProtectedMember
    assert str(project._initial_source_dir) == expected


def test_backwards_compat_old_suffixes_freebsd_mips():
    # Check that we still load the value from the deprecated key name from the JSON config file
    _check_source_dir("freebsd-mips64", "/from/json",
                      b'{"freebsd-mips/source-directory": "/from/json"}', [])

    # It should also override a command line value for the un-suffixed target
    _check_source_dir("freebsd-mips64", "/from/json",
                      b'{"freebsd-mips/source-directory": "/from/json"}',
                      ["--freebsd/source-directory=/fallback/from/cmdline/"])

    # The new key name should have priority:
    _check_source_dir("freebsd-mips64", "/new/dir",
                      b'{"freebsd-mips/source-directory": "/old/dir",'
                      b' "freebsd-mips64/source-directory": "/new/dir" }', [])

    # Finally, using the old name on the command line should be an error:
    with pytest.raises(KeyError, match=r"error: unknown argument '--freebsd-mips/source-directory=/cmdline'"):
        _ = _parse_config_file_and_args(b'{}', "--freebsd-mips/source-directory=/cmdline")


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


def test_source_dir_option_when_reusing_git_repo(monkeypatch):
    """Passing the --foo/source-dir=/some/path should also work if the target reuses another target's source dir"""
    # By default, compiler-rt-native should reuse the LLVM source dir.
    config = _parse_config_file_and_args(b'{ "llvm/source-directory": "/custom/llvm/dir" }', )
    assert str(_get_target_instance("llvm-native", config).source_dir) == "/custom/llvm/dir"
    assert str(_get_target_instance("compiler-rt-native", config).source_dir) == "/custom/llvm/dir/compiler-rt"
    assert str(_get_target_instance("compiler-rt-riscv64", config).source_dir) == "/custom/llvm/dir/compiler-rt"

    # An explicit override should have priority:
    config = _parse_config_file_and_args(b'{ "llvm/source-directory": "/custom/llvm/dir2"}',
                                         "--compiler-rt/source-directory=/custom/compiler-rt/dir2")
    assert str(_get_target_instance("llvm-native", config).source_dir) == "/custom/llvm/dir2"
    assert str(_get_target_instance("compiler-rt-native", config).source_dir) == "/custom/compiler-rt/dir2"
    assert str(_get_target_instance("compiler-rt-riscv64", config).source_dir) == "/custom/compiler-rt/dir2"

    # Same again just with the -native suffix:
    config = _parse_config_file_and_args(b'{ "llvm-native/source-directory": "/custom/llvm/dir3",'
                                         b'  "source-root": "/foo" }',
                                         "--compiler-rt-native/source-directory=/custom/compiler-rt/dir3")
    assert str(_get_target_instance("llvm-native", config).source_dir) == "/custom/llvm/dir3"
    assert str(_get_target_instance("compiler-rt-native", config).source_dir) == "/custom/compiler-rt/dir3"
    # compiler-rt-riscv64 uses the default path, since we only changed llvm-native and compiler-rt-native:
    assert str(_get_target_instance("compiler-rt-riscv64", config).source_dir) == "/foo/llvm-project/compiler-rt"

    # Check that cheribsd-mfs-root-kernel reused the cheribsd source dir
    assert str(_get_target_instance("cheribsd-mfs-root-kernel-riscv64-purecap",
                                    config).source_dir) == "/foo/cheribsd"
    assert str(_get_target_instance("cheribsd-mfs-root-kernel-riscv64-hybrid",
                                    config).source_dir) == "/foo/cheribsd"
    config = _parse_config_file_and_args(b'{ "cheribsd-riscv64-purecap/source-directory": "/custom/cheribsd-riscv-dir",'
                                         b'  "source-root": "/foo" }')
    assert str(_get_target_instance("cheribsd-mfs-root-kernel-riscv64-hybrid",
                                    config).source_dir) == "/foo/cheribsd"
    assert str(_get_target_instance("cheribsd-mfs-root-kernel-riscv64-purecap",
                                    config).source_dir) == "/custom/cheribsd-riscv-dir"


def test_mfs_root_kernel_config_options():
    """ Check that the mfs-kernel class does not inherit unnecessary command line options from BuildCheriBSD """
    project = _get_target_instance("cheribsd-mfs-root-kernel-riscv64-purecap", _parse_arguments([]),
                                   BuildCheriBsdMfsKernel)
    config_options = [attr for attr in project.__class__.__dict__ if
                      isinstance(inspect.getattr_static(project, attr), ConfigOptionBase)]
    config_options.sort()
    assert config_options == ["_initial_source_dir", "_install_dir", "_linkage",
                              "auto_var_init", "build_alternate_abi_kernels",
                              "build_bench_kernels", "build_dir", "build_fett_kernels", "build_fpga_kernels",
                              "build_type", "caprevoke_kernel", "debug_kernel", "default_kernel_abi",
                              "extra_make_args", "fast_rebuild", "force_configure", "kernel_config",
                              "mfs_root_image", "skip_update", "use_ccache", "use_lto", "with_clean",
                              "with_debug_files", "with_debug_info"]


def test_mfs_root_kernel_inherits_defaults_from_cheribsd():
    """ Check that the mfs-kernel defaults are inherited from cheribsd (other than kernel-config """
    # Parse args once to ensure target_manager is initialized
    config = _parse_arguments([])
    mfs_riscv64 = _get_target_instance("cheribsd-mfs-root-kernel-riscv64-purecap", config, BuildCheriBsdMfsKernel)
    cheribsd_riscv64_purecap = _get_target_instance("cheribsd-riscv64-purecap", config, BuildCHERIBSD)
    _parse_arguments(["--cheribsd/build-alternate-abi-kernels"])

    assert cheribsd_riscv64_purecap.build_alternate_abi_kernels
    assert mfs_riscv64.build_alternate_abi_kernels

    _parse_arguments(["--cheribsd/no-build-alternate-abi-kernels"])

    assert not cheribsd_riscv64_purecap.build_alternate_abi_kernels
    assert not mfs_riscv64.build_alternate_abi_kernels

    _parse_arguments(["--cheribsd/build-alternate-abi-kernels",
                      "--cheribsd-riscv64-purecap/no-build-alternate-abi-kernels"])

    assert not cheribsd_riscv64_purecap.build_alternate_abi_kernels
    assert not mfs_riscv64.build_alternate_abi_kernels

    _parse_arguments(["--cheribsd/no-build-alternate-abi-kernels",
                      "--cheribsd-riscv64-purecap/build-alternate-abi-kernels"])

    assert cheribsd_riscv64_purecap.build_alternate_abi_kernels
    assert mfs_riscv64.build_alternate_abi_kernels

    # Check that the config options are inherited in the right order:
    _parse_arguments(["--cheribsd/source-directory=/generic-base-target-dir",
                      "--cheribsd-riscv64-purecap/source-directory=/base-target-dir-riscv64",
                      "--cheribsd-mfs-root-kernel/source-directory=/generic-mfs-target-dir",
                      "--cheribsd-mfs-root-kernel-riscv64-hybrid/source-directory=/mfs-target-dir-riscv64-hybrid"])
    mfs_riscv64 = _get_target_instance("cheribsd-mfs-root-kernel-riscv64-purecap", config, BuildCheriBsdMfsKernel)
    mfs_riscv64_hybrid = _get_target_instance("cheribsd-mfs-root-kernel-riscv64-hybrid", config, BuildCheriBsdMfsKernel)
    cheribsd_riscv64_purecap = _get_target_instance("cheribsd-riscv64-purecap", config, BuildCHERIBSD)
    cheribsd_riscv64_hybrid = _get_target_instance("cheribsd-riscv64-hybrid", config, BuildCHERIBSD)
    assert str(cheribsd_riscv64_purecap._initial_source_dir) == "/base-target-dir-riscv64"
    assert str(cheribsd_riscv64_hybrid._initial_source_dir) == "/generic-base-target-dir"
    assert str(mfs_riscv64._initial_source_dir) == "/generic-mfs-target-dir"
    assert str(mfs_riscv64_hybrid._initial_source_dir) == "/mfs-target-dir-riscv64-hybrid"

    _parse_arguments(["--cheribsd-riscv64-purecap/kernel-config=BASE_CONFIG_RISCV64",
                      "--cheribsd-mfs-root-kernel-riscv64-hybrid/kernel-config=MFS_CONFIG_RISCV64_HYBRID"])
    assert cheribsd_riscv64_purecap.kernel_config == "BASE_CONFIG_RISCV64"
    assert cheribsd_riscv64_hybrid.kernel_config == "CHERI-QEMU"
    assert mfs_riscv64.kernel_config is None
    assert mfs_riscv64_hybrid.kernel_config == "MFS_CONFIG_RISCV64_HYBRID"
    _parse_arguments(["--kernel-config=CONFIG_DEFAULT",
                      "--cheribsd-riscv64-purecap/kernel-config=BASE_CONFIG_RISCV64",
                      "--cheribsd-mfs-root-kernel-riscv64-hybrid/kernel-config=MFS_CONFIG_RISCV64_HYBRID"])
    assert cheribsd_riscv64_purecap.kernel_config == "BASE_CONFIG_RISCV64"
    assert cheribsd_riscv64_hybrid.kernel_config == "CONFIG_DEFAULT"
    assert mfs_riscv64.kernel_config == "CONFIG_DEFAULT"
    assert mfs_riscv64_hybrid.kernel_config == "MFS_CONFIG_RISCV64_HYBRID"


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
