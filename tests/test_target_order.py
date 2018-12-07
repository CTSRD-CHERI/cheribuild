import sys

try:
    import typing
except ImportError:
    typing = {}
import pytest
from pathlib import Path
from pycheribuild.utils import *

sys.path.append(str(Path(__file__).parent.parent))

# First thing we need to do is set up the config loader (before importing anything else!)
# We can"t do from pycheribuild.configloader import ConfigLoader here because that will only update the local copy
from pycheribuild.config.loader import DefaultValueOnlyConfigLoader, ConfigLoaderBase
from pycheribuild.projects.project import SimpleProject
from pycheribuild.targets import targetManager
# noinspection PyUnresolvedReferences
from pycheribuild.projects import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.cross import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.cross.cheribsd import BuildCHERIBSD
from .setup_mock_chericonfig import setup_mock_chericonfig

setup_mock_chericonfig(Path("/this/path/does/not/exist"))
# Init code:
BuildCHERIBSD.crossbuild = True


def _sort_targets(targets: "typing.List[str]", add_dependencies=False, skip_sdk=False) -> "typing.List[str]":
    targetManager.reset()
    # print(real_targets)
    config = get_global_config()
    real_targets = list(targetManager.get_target(t, None, config) for t in targets)
    config.includeDependencies = add_dependencies
    config.skipSdk = skip_sdk
    for t in real_targets:
        t.get_dependencies(config)  # ensure they have been cached
    result = list(t.name for t in targetManager.get_all_targets(real_targets, config))
    # print("result = ", result)
    return result

freestanding_deps = ["elftoolchain", "binutils", "llvm", "qemu", "gdb-native", "freestanding-sdk"]
baremetal_deps = freestanding_deps + ["newlib-baremetal-mips", "compiler-rt-baremetal-mips", "libcxxrt-baremetal-mips",
                                      "libcxx-baremetal-mips", "baremetal-sdk"]
cheribsd_sdk_deps = freestanding_deps + ["cheribsd-cheri", "cheribsd-sysroot-cheri", "cheribsd-sdk"]

@pytest.mark.parametrize("target_name,expected_list", [
    pytest.param("freestanding-sdk", freestanding_deps, id="freestanding-sdk"),
    pytest.param("baremetal-sdk", baremetal_deps, id="baremetal-sdk"),
    # Ensure that cheribsd is added to deps even on Linux/Mac
    pytest.param("cheribsd-sdk", cheribsd_sdk_deps, id="cheribsd-sdk"),
    pytest.param("sdk", (cheribsd_deps if IS_FREEBSD else freestanding_deps) + ["sdk"], id="sdk"),
])
def test_sdk(target_name, expected_list):
    assert expected_list == _sort_targets([target_name])


@pytest.mark.parametrize("target_name,expected_name", [
    pytest.param("cheribsd", "cheribsd-cheri"),
    pytest.param("freebsd", "freebsd-native"),
    pytest.param("gdb", "gdb-native"),
    pytest.param("libcxx", "libcxx-cheri"),
    pytest.param("libcxx-baremetal", "libcxx-baremetal-mips"),
    pytest.param("libcxxrt-baremetal", "libcxxrt-baremetal-mips"),
])
def test_alias_resolving(target_name, expected_name):
    # test that we select the default target for multi projects:
    assert _sort_targets([target_name]) == [expected_name]


def test_reordering():
    # GDB is a cross compiled project so cheribsd should be built first
    assert _sort_targets(["cheribsd", "gdb-mips"]) == ["cheribsd-cheri", "gdb-mips"]
    assert _sort_targets(["gdb-mips", "cheribsd"]) == ["cheribsd-cheri", "gdb-mips"]
    assert _sort_targets(["gdb-mips", "cheribsd-sysroot-cheri"]) == ["cheribsd-sysroot-cheri", "gdb-mips"]


def test_run_comes_last():
    assert _sort_targets(["run", "disk-image"]) == ["disk-image", "run"]


def test_disk_image_comes_second_last():
    assert _sort_targets(["run", "disk-image"]) == ["disk-image", "run"]
    assert _sort_targets(["run", "disk-image", "cheribsd"]) == ["cheribsd-cheri", "disk-image", "run"]
    assert _sort_targets(["run", "gdb-mips", "disk-image", "cheribsd"]) == ["cheribsd-cheri", "gdb-mips", "disk-image", "run"]
    assert _sort_targets(["run", "disk-image", "postgres", "cheribsd"]) == ["cheribsd-cheri", "postgres-cheri", "disk-image", "run"]


def test_all_run_deps():
    assert _sort_targets(["run"], add_dependencies=True) == ["qemu", "llvm", "cheribsd-cheri", "elftoolchain", "binutils",
                                                             "gdb-native", "freestanding-sdk", "cheribsd-sysroot-cheri",
                                                             "cheribsd-sdk", "gdb-mips", "disk-image", "run"]


def test_run_disk_image():
    assert _sort_targets(["run", "disk-image", "run-freebsd-mips", "llvm", "disk-image-freebsd-native"]) == [
                          "llvm", "disk-image", "disk-image-freebsd-native", "run", "run-freebsd-mips"]


def test_remove_duplicates():
    assert _sort_targets(["binutils", "elftoolchain"], add_dependencies=True) == ["elftoolchain", "binutils"]


def test_minimal_run():
    # Check that we build the mfs root first
    assert _sort_targets(["disk-image-minimal", "cheribsd-mfs-root-kernel", "run-minimal"]) == \
                         ["disk-image-minimal", "cheribsd-mfs-root-kernel", "run-minimal"]
    assert _sort_targets(["cheribsd-mfs-root-kernel", "disk-image-minimal", "run-minimal"]) == \
                         ["disk-image-minimal", "cheribsd-mfs-root-kernel", "run-minimal"]


# Check that libcxx deps with skip sdk pick the matching -native/-mips versions
# Also the libcxx target should resolve to libcxx-cheri:
@pytest.mark.parametrize("suffix,expected_suffix", [
    pytest.param("-native", "-native", id="native"),
    pytest.param("-mips", "-mips", id="mips"),
    pytest.param("-cheri", "-cheri", id="cheri"),
    # no suffix should resolve to the -cheri targets:
    pytest.param("", "-cheri", id="no suffix"),
])
def test_libcxx_deps(suffix, expected_suffix):
    expected = ["libunwind" + expected_suffix, "libcxxrt" + expected_suffix, "libcxx" + expected_suffix]
    # Now check that the cross-compile versions explicitly chose the matching target:
    assert expected == _sort_targets(["libcxx" + suffix], add_dependencies=True, skip_sdk=True)

