import copy
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
from pycheribuild.projects.project import CompilationTargets
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
    real_targets = list(targetManager.get_target(t, CompilationTargets.NONE, config, caller="_sort_targets") for t in targets)
    config.includeDependencies = add_dependencies
    config.skipSdk = skip_sdk
    for t in real_targets:
        if t._project_class._crossCompileTarget is CompilationTargets.NONE:
            continue
        t.projectClass._cached_deps = None
        t.get_dependencies(config)  # ensure they have been cached
    result = list(t.name for t in targetManager.get_all_targets(real_targets, config))
    # print("result = ", result)
    return result


freestanding_deps = ["llvm-native", "qemu", "gdb-native", "freestanding-sdk"]
baremetal_deps = freestanding_deps + ["newlib-baremetal-mips", "compiler-rt-builtins-baremetal-mips",
                                      "libunwind-baremetal-mips", "libcxxrt-baremetal-mips",
                                      "libcxx-baremetal-mips", "baremetal-sdk"]
cheribsd_sdk_deps = freestanding_deps + ["cheribsd-cheri", "cheribsd-sdk"]


@pytest.mark.parametrize("target_name,expected_list", [
    pytest.param("freestanding-sdk", freestanding_deps, id="freestanding-sdk"),
    pytest.param("baremetal-sdk", baremetal_deps, id="baremetal-sdk"),
    # Ensure that cheribsd is added to deps even on Linux/Mac
    pytest.param("cheribsd-sdk", cheribsd_sdk_deps, id="cheribsd-sdk"),
    pytest.param("sdk", cheribsd_sdk_deps + ["sdk"], id="sdk"),
    ])
def test_sdk(target_name, expected_list):
    assert _sort_targets([target_name]) == expected_list


@pytest.mark.parametrize("target_name,expected_name", [
    pytest.param("cheribsd", "cheribsd-cheri"),
    pytest.param("freebsd", "freebsd-x86_64"),
    pytest.param("gdb", "gdb-native"),
    pytest.param("libcxx", "libcxx-cheri"),
    ])
def test_alias_resolving(target_name, expected_name):
    # test that we select the default target for multi projects:
    assert _sort_targets([target_name]) == [expected_name]


def test_reordering():
    # GDB is a cross compiled project so cheribsd should be built first
    assert _sort_targets(["cheribsd", "gdb-mips-hybrid"]) == ["cheribsd-cheri", "gdb-mips-hybrid"]
    assert _sort_targets(["gdb-mips-hybrid", "cheribsd"]) == ["cheribsd-cheri", "gdb-mips-hybrid"]
    assert _sort_targets(["gdb-mips-hybrid", "cheribsd-cheri"]) == ["cheribsd-cheri", "gdb-mips-hybrid"]


def test_run_comes_last():
    assert _sort_targets(["run", "disk-image"]) == ["disk-image-mips-hybrid", "run-mips-hybrid"]


def test_disk_image_comes_second_last():
    assert _sort_targets(["run", "disk-image"]) == ["disk-image-mips-hybrid", "run-mips-hybrid"]
    assert _sort_targets(["run", "disk-image", "cheribsd"]) == ["cheribsd-cheri", "disk-image-mips-hybrid", "run-mips-hybrid"]
    assert _sort_targets(["run", "gdb-mips-hybrid", "disk-image", "cheribsd"]) == ["cheribsd-cheri", "gdb-mips-hybrid", "disk-image-mips-hybrid", "run-mips-hybrid"]
    assert _sort_targets(["run", "disk-image", "postgres", "cheribsd"]) == ["cheribsd-cheri", "postgres-cheri", "disk-image-mips-hybrid", "run-mips-hybrid"]


def test_cheribsd_default_aliases():
    assert _sort_targets(["run"]) == ["run-mips-hybrid"]
    assert _sort_targets(["disk-image"]) == ["disk-image-mips-hybrid"]
    assert _sort_targets(["cheribsd"]) == ["cheribsd-cheri"]


def test_all_run_deps():
    assert _sort_targets(["run"], add_dependencies=True) == ["qemu", "llvm-native", "gdb-native", "cheribsd-cheri",
                                                             "gdb-mips-hybrid", "disk-image-mips-hybrid",
                                                             "run-mips-hybrid"]


def test_run_disk_image():
    assert _sort_targets(["run", "disk-image", "run-freebsd-mips", "llvm", "disk-image-freebsd-x86_64"]) == [
                          "llvm-native", "disk-image-mips-hybrid", "disk-image-freebsd-x86_64", "run-mips-hybrid", "run-freebsd-mips"]


def test_remove_duplicates():
    assert _sort_targets(["binutils", "elftoolchain"], add_dependencies=True) == ["elftoolchain", "binutils"]


def test_minimal_run():
    # Check that we build the mfs root first
    assert _sort_targets(["disk-image-minimal", "cheribsd-mfs-root-kernel", "run-minimal"]) == \
                         ["disk-image-minimal-mips-hybrid", "cheribsd-mfs-root-kernel-mips-hybrid", "run-minimal-mips-hybrid"]
    assert _sort_targets(["cheribsd-mfs-root-kernel", "disk-image-minimal", "run-minimal"]) == \
                         ["disk-image-minimal-mips-hybrid", "cheribsd-mfs-root-kernel-mips-hybrid", "run-minimal-mips-hybrid"]


def _check_deps_not_cached(classes):
    for c in classes:
        with pytest.raises(ValueError, match="_cached_dependencies called before allDependencyNames()"):
            c._cached_dependencies()

def _check_deps_cached(classes):
    for c in classes:
        assert len(c._cached_dependencies()) > 0


def test_webkit_cached_deps():
    # regression test for a bug in caching deps
    config = copy.copy(get_global_config())
    config.skipSdk = True
    webkit_native = targetManager.get_target_raw("qtwebkit-native").projectClass
    webkit_cheri = targetManager.get_target_raw("qtwebkit-cheri").projectClass
    webkit_mips = targetManager.get_target_raw("qtwebkit-mips-hybrid").projectClass
    # Check that the deps are not cached yet
    _check_deps_not_cached((webkit_native, webkit_cheri, webkit_mips))

    cheri_target_names = list(sorted(webkit_cheri.allDependencyNames(config)))
    assert cheri_target_names == ["icu4c-cheri", "icu4c-native", "libxml2-cheri", "qtbase-cheri", "sqlite-cheri"]
    _check_deps_not_cached([webkit_native, webkit_mips])
    _check_deps_cached([webkit_cheri])

    mips_target_names = list(sorted(webkit_mips.allDependencyNames(config)))
    assert mips_target_names == ["icu4c-mips-hybrid", "icu4c-native", "libxml2-mips-hybrid", "qtbase-mips-hybrid", "sqlite-mips-hybrid"]
    _check_deps_cached([webkit_cheri, webkit_mips])
    _check_deps_not_cached([webkit_native])

    native_target_names = list(sorted(webkit_native.allDependencyNames(config)))
    assert native_target_names == ["icu4c-native", "libxml2-native", "qtbase-native", "sqlite-native"]
    _check_deps_cached([webkit_cheri, webkit_mips, webkit_native])


def test_webkit_deps_2():
    assert _sort_targets(["qtwebkit-native"], add_dependencies=True, skip_sdk=True) == \
                         ["qtbase-native", "icu4c-native", "libxml2-native", "sqlite-native", "qtwebkit-native"]
    # SDK should not add new targets
    assert _sort_targets(["qtwebkit-native"], add_dependencies=True, skip_sdk=False) == \
                         ["qtbase-native", "icu4c-native", "libxml2-native", "sqlite-native", "qtwebkit-native"]

    assert _sort_targets(["qtwebkit-mips-hybrid"], add_dependencies=True, skip_sdk=True) == \
                         ["qtbase-mips-hybrid", "icu4c-native", "icu4c-mips-hybrid", "libxml2-mips-hybrid", "sqlite-mips-hybrid", "qtwebkit-mips-hybrid"]
    assert _sort_targets(["qtwebkit-cheri"], add_dependencies=True, skip_sdk=True) == \
                         ["qtbase-cheri", "icu4c-native", "icu4c-cheri", "libxml2-cheri", "sqlite-cheri", "qtwebkit-cheri"]


def test_riscv():
    assert _sort_targets(["bbl-riscv64", "cheribsd-riscv64"], add_dependencies=False, skip_sdk=False) == \
                         ["cheribsd-riscv64", "bbl-riscv64"]
    assert _sort_targets(["run-riscv64"], add_dependencies=True, skip_sdk=True) == \
                         ["disk-image-riscv64", "run-riscv64"]
    assert _sort_targets(["run-riscv64-purecap"], add_dependencies=True, skip_sdk=True) == \
                         ["bbl-riscv64-purecap", "disk-image-riscv64-purecap", "run-riscv64-purecap"]
    assert _sort_targets(["disk-image-riscv64"], add_dependencies=True, skip_sdk=False) == \
           ["qemu", "llvm-native", "gdb-native", "cheribsd-riscv64", "gdb-riscv64",
            "disk-image-riscv64"]
    assert _sort_targets(["run-riscv64"], add_dependencies=True, skip_sdk=False) == \
           ["qemu", "llvm-native", "gdb-native", "cheribsd-riscv64", "gdb-riscv64",
            "disk-image-riscv64", "run-riscv64"]

# Check that libcxx deps with skip sdk pick the matching -native/-mips versions
# Also the libcxx target should resolve to libcxx-cheri:
@pytest.mark.parametrize("suffix,expected_suffix", [
    pytest.param("-native", "-native", id="native"),
    pytest.param("-mips-nocheri", "-mips-nocheri", id="mips-nocheri"),
    pytest.param("-mips-hybrid", "-mips-hybrid", id="mips-hybrid"),
    pytest.param("-cheri", "-cheri", id="cheri"),
    # no suffix should resolve to the -cheri targets:
    pytest.param("", "-cheri", id="no suffix"),
])
def test_libcxx_deps(suffix, expected_suffix):
    expected = ["libunwind" + expected_suffix, "libcxxrt" + expected_suffix, "libcxx" + expected_suffix]
    # Now check that the cross-compile versions explicitly chose the matching target:
    assert expected == _sort_targets(["libcxx" + suffix], add_dependencies=True, skip_sdk=True)

