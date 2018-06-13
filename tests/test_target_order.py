import sys

try:
    import typing
except ImportError:
    typing = {}
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
    real_targets = list(targetManager.get_target(t) for t in targets)
    # print(real_targets)
    config = get_global_config()
    config.includeDependencies = add_dependencies
    config.skipSdk = skip_sdk
    for t in real_targets:
        t.get_dependencies(config)  # ensure they have been cached
    result = list(t.name for t in targetManager.get_all_targets(real_targets, config))
    # print("result = ", result)
    return result


def test_sdk():
    freestanding_deps = ["elftoolchain", "binutils", "llvm", "qemu", "gdb-native", "freestanding-sdk"]
    assert _sort_targets(["freestanding-sdk"]) == freestanding_deps
    baremetal_deps = freestanding_deps + ["newlib-baremetal", "compiler-rt-baremetal", "libcxxrt-baremetal",
                                          "libcxx-baremetal", "baremetal-sdk"]
    assert _sort_targets(["baremetal-sdk"]) == baremetal_deps
    # Ensure that cheribsd is added to deps even on Linux/Mac
    cheribsd_deps = freestanding_deps + ["cheribsd", "cheribsd-sysroot", "cheribsd-sdk"]
    assert _sort_targets(["cheribsd-sdk"]) == cheribsd_deps

    assert _sort_targets(["sdk"]) == (cheribsd_deps if IS_FREEBSD else freestanding_deps) + ["sdk"]


def test_reordering():
    # GDB is a cross compiled project so cheribsd should be built first
    assert _sort_targets(["cheribsd", "gdb"]) == ["cheribsd", "gdb"]
    assert _sort_targets(["gdb", "cheribsd"]) == ["cheribsd", "gdb"]
    assert _sort_targets(["gdb", "cheribsd-sysroot"]) == ["cheribsd-sysroot", "gdb"]


def test_run_comes_last():
    assert _sort_targets(["run", "disk-image"]) == ["disk-image", "run"]


def test_disk_image_comes_second_last():
    assert _sort_targets(["run", "disk-image"]) == ["disk-image", "run"]
    assert _sort_targets(["run", "disk-image", "cheribsd"]) == ["cheribsd", "disk-image", "run"]
    assert _sort_targets(["run", "gdb", "disk-image", "cheribsd"]) == ["cheribsd", "gdb", "disk-image", "run"]
    assert _sort_targets(["run", "disk-image", "postgres", "cheribsd"]) == ["cheribsd", "postgres", "disk-image", "run"]


def test_all_run_deps():
    assert _sort_targets(["run"], add_dependencies=True) == ["qemu", "llvm", "cheribsd", "elftoolchain", "binutils",
                                                             "gdb-native", "freestanding-sdk", "cheribsd-sysroot",
                                                             "cheribsd-sdk", "gdb-mips", "disk-image", "run"]


def test_run_disk_image():
    assert _sort_targets(["run", "disk-image", "run-freebsd-mips", "llvm", "disk-image-freebsd-x86"]) == [
                          "llvm", "disk-image", "disk-image-freebsd-x86", "run", "run-freebsd-mips"]


def test_remove_duplicates():
    assert _sort_targets(["binutils", "elftoolchain"], add_dependencies=True) == ["elftoolchain", "binutils"]


def test_minimal_run():
    # Check that we build the mfs root first
    assert _sort_targets(["disk-image-minimal", "cheribsd-mfs-root-kernel", "run-minimal"]) == \
                         ["disk-image-minimal", "cheribsd-mfs-root-kernel", "run-minimal"]
    assert _sort_targets(["cheribsd-mfs-root-kernel", "disk-image-minimal", "run-minimal"]) == \
                         ["disk-image-minimal", "cheribsd-mfs-root-kernel", "run-minimal"]


# Check cross-compile targets
def test_libcxx_deps():
    # Now check that the cross-compile versions explicitly chose the matching target:
    assert ["libunwind-native", "libcxxrt-native", "libcxx-native"] == \
           _sort_targets(["libcxx-native"], add_dependencies=True, skip_sdk=True)
    assert ["libunwind-cheri", "libcxxrt-cheri", "libcxx-cheri"] == \
           _sort_targets(["libcxx-cheri"], add_dependencies=True, skip_sdk=True)
    assert ["libunwind-mips", "libcxxrt-mips", "libcxx-mips"] == \
           _sort_targets(["libcxx-mips"], add_dependencies=True, skip_sdk=True)

    assert ["libunwind-cheri", "libcxxrt-cheri", "libcxx"] == \
           _sort_targets(["libcxx"], add_dependencies=True, skip_sdk=True)
