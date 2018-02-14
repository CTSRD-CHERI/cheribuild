import sys
import tempfile
import unittest
import typing
from pathlib import Path
from unittest import TestCase
from pycheribuild.utils import *

sys.path.append(str(Path(__file__).parent.parent))

# First thing we need to do is set up the config loader (before importing anything else!)
# We can"t do from pycheribuild.configloader import ConfigLoader here because that will only update the local copy
from pycheribuild.config.loader import DefaultValueOnlyConfigLoader, ConfigLoaderBase
from pycheribuild.projects.project import SimpleProject
SimpleProject._configLoader = DefaultValueOnlyConfigLoader()
from pycheribuild.targets import targetManager
# noinspection PyUnresolvedReferences
from pycheribuild.projects import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.cross import *  # make sure all projects are loaded so that targetManager gets populated
from pycheribuild.projects.cheribsd import BuildCHERIBSD


# Init code:
BuildCHERIBSD.crossbuild = True

def _sort_targets(targets: typing.List[str], add_dependencies=False) -> typing.List[str]:
    real_targets = list(targetManager.get_target(t) for t in targets)
    # print(real_targets)
    result = list(t.name for t in targetManager.get_all_targets(real_targets, add_dependencies))
    # print("result = ", result)
    return result


def test_sdk():
    freestanding_deps = ["elftoolchain", "binutils", "llvm", "qemu", "freestanding-sdk"]
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
                                                             "freestanding-sdk", "cheribsd-sysroot", "cheribsd-sdk",
                                                             "gdb-mips", "disk-image", "run"]


def test_run_disk_image():
    assert _sort_targets(["run", "disk-image", "run-freebsd-mips", "llvm", "disk-image-freebsd-x86"]) == [
                          "llvm", "disk-image", "disk-image-freebsd-x86", "run", "run-freebsd-mips"]


def test_remove_duplicates():
    assert _sort_targets(["binutils", "elftoolchain"], add_dependencies=True) == ["elftoolchain", "binutils"]
