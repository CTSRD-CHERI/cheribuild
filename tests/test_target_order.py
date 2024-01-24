import copy
import inspect

# noinspection PyUnresolvedReferences
from pathlib import Path

import pytest

from pycheribuild.config.compilation_targets import CompilationTargets, enable_hybrid_for_purecap_rootfs_targets
from pycheribuild.config.target_info import CrossCompileTarget

# Make sure all projects are loaded so that target_manager gets populated
from pycheribuild.projects import *  # noqa: F401, F403, RUF100
from pycheribuild.projects.cmake import BuildCrossCompiledCMake
from pycheribuild.projects.cross import *  # noqa: F401, F403, RUF100
from pycheribuild.projects.cross.benchmarks import BenchmarkMixin
from pycheribuild.projects.cross.cheribsd import (
    BuildCHERIBSD,
    BuildCheriBsdMfsImageAndKernels,
    BuildCheriBsdSysrootArchive,
)
from pycheribuild.projects.cross.gdb import BuildGDBBase
from pycheribuild.projects.cross.gmp import BuildGmp
from pycheribuild.projects.cross.llvm import BuildCheriLLVM, BuildMorelloLLVM
from pycheribuild.projects.cross.mpfr import BuildMpfr
from pycheribuild.projects.cross.qt5 import BuildQtBase
from pycheribuild.projects.disk_image import BuildDiskImageBase
from pycheribuild.projects.project import DefaultInstallDir, Project
from pycheribuild.projects.run_fvp import LaunchFVPBase
from pycheribuild.projects.run_qemu import BuildAll, BuildAndRunCheriBSD, LaunchCheriBSD
from pycheribuild.projects.sdk import BuildCheriBSDSdk, BuildSdk
from pycheribuild.projects.simple_project import SimpleProject
from pycheribuild.projects.spike import RunCheriSpikeBase
from pycheribuild.projects.syzkaller import BuildSyzkaller, RunSyzkaller
from pycheribuild.targets import Target, target_manager
from .setup_mock_chericonfig import CheriConfig, setup_mock_chericonfig


# noinspection PyProtectedMember
def _sort_targets(
    targets: "list[str]",
    *,
    add_dependencies=False,
    add_toolchain=True,
    skip_sdk=False,
    build_morello_from_source=False,
    only_dependencies=False,
) -> "list[str]":
    target_manager.reset()
    # print(real_targets)
    global_config = setup_mock_chericonfig(Path("/this/path/does/not/exist"))
    global_config.include_dependencies = add_dependencies
    global_config.include_toolchain_dependencies = add_toolchain
    global_config.skip_sdk = skip_sdk
    global_config.build_morello_firmware_from_source = build_morello_from_source
    global_config.only_dependencies = only_dependencies
    real_targets = list(target_manager.get_target(t, config=global_config, caller="_sort_targets") for t in targets)

    for t in real_targets:
        if t.project_class._xtarget is None:
            continue
    for t in target_manager.targets(global_config):
        assert t.project_class._cached_full_deps is None
        assert t.project_class._cached_filtered_deps is None
    result = list(t.name for t in target_manager.get_all_targets(real_targets, global_config))
    # print("result = ", result)
    return result


freestanding_deps = ["llvm-native", "qemu", "gdb-native", "freestanding-cheri-sdk"]
cheribsd_sdk_deps = [*freestanding_deps, "cheribsd-riscv64-hybrid", "cheribsd-sdk-riscv64-hybrid"]


@pytest.mark.parametrize(
    ("target_name", "expected_list"),
    [
        pytest.param("freestanding-cheri-sdk", freestanding_deps, id="freestanding-sdk"),
        # Ensure that cheribsd is added to deps even on Linux/Mac
        pytest.param("cheribsd-sdk-riscv64-hybrid", cheribsd_sdk_deps, id="cheribsd-sdk"),
        pytest.param("sdk-riscv64-hybrid", [*cheribsd_sdk_deps, "sdk-riscv64-hybrid"], id="sdk"),
        pytest.param(
            "sdk-morello-purecap",
            [
                "morello-llvm-native",
                "qemu",
                "freestanding-morello-sdk",
                "cheribsd-morello-purecap",
                "cheribsd-sdk-morello-purecap",
                "sdk-morello-purecap",
            ],
            id="morello-purecap",
        ),
    ],
)
def test_sdk(target_name: str, expected_list: "list[str]"):
    assert _sort_targets([target_name]) == expected_list


@pytest.mark.parametrize(
    ("target_name", "expected_name"),
    [
        pytest.param("llvm", "llvm-native"),
        pytest.param("gdb", "gdb-native"),
    ],
)
def test_alias_resolving(target_name: str, expected_name: str):
    # test that we select the default target for multi projects:
    assert _sort_targets([target_name]) == [expected_name]


def test_reordering():
    # GDB is a cross compiled project so cheribsd should be built first
    assert _sort_targets(["cheribsd-riscv64-hybrid", "gdb-riscv64-hybrid"]) == [
        "cheribsd-riscv64-hybrid",
        "gdb-riscv64-hybrid",
    ]
    assert _sort_targets(["gdb-riscv64-hybrid", "cheribsd-riscv64-hybrid"]) == [
        "cheribsd-riscv64-hybrid",
        "gdb-riscv64-hybrid",
    ]


def test_run_comes_last():
    assert _sort_targets(["run-riscv64-hybrid", "disk-image-riscv64-hybrid"]) == [
        "disk-image-riscv64-hybrid",
        "run-riscv64-hybrid",
    ]


def test_disk_image_comes_second_last():
    assert _sort_targets(["run-riscv64-hybrid", "disk-image-riscv64-hybrid"]) == [
        "disk-image-riscv64-hybrid",
        "run-riscv64-hybrid",
    ]
    assert _sort_targets(["run-riscv64-hybrid", "disk-image-riscv64-hybrid", "cheribsd-riscv64-hybrid"]) == [
        "cheribsd-riscv64-hybrid",
        "disk-image-riscv64-hybrid",
        "run-riscv64-hybrid",
    ]
    assert _sort_targets(
        ["run-riscv64-hybrid", "gdb-riscv64-hybrid", "disk-image-riscv64-hybrid", "cheribsd-riscv64-hybrid"],
    ) == ["cheribsd-riscv64-hybrid", "gdb-riscv64-hybrid", "disk-image-riscv64-hybrid", "run-riscv64-hybrid"]
    assert _sort_targets(
        ["run-riscv64-purecap", "disk-image-riscv64-purecap", "postgres-riscv64-purecap", "cheribsd-riscv64-purecap"],
    ) == ["cheribsd-riscv64-purecap", "postgres-riscv64-purecap", "disk-image-riscv64-purecap", "run-riscv64-purecap"]


def test_cheribsd_default_aliases():
    assert _sort_targets(["run-riscv64-hybrid"]) == ["run-riscv64-hybrid"]
    assert _sort_targets(["disk-image-riscv64-hybrid"]) == ["disk-image-riscv64-hybrid"]
    assert _sort_targets(["cheribsd-riscv64-hybrid"]) == ["cheribsd-riscv64-hybrid"]


@pytest.mark.parametrize(
    ("target_name", "expected_list"),
    [
        pytest.param("build-and-run-cheribsd-riscv64", ["cheribsd-riscv64", "disk-image-riscv64", "run-riscv64"]),
        pytest.param("build-and-run-cheribsd-aarch64", ["cheribsd-aarch64", "disk-image-aarch64", "run-aarch64"]),
        pytest.param(
            "build-and-run-cheribsd-riscv64-purecap",
            ["cheribsd-riscv64-purecap", "disk-image-riscv64-purecap", "run-riscv64-purecap"],
        ),
        pytest.param(
            "build-and-run-freebsd-riscv64",
            ["freebsd-riscv64", "disk-image-freebsd-riscv64", "run-freebsd-riscv64"],
        ),
        pytest.param(
            "build-and-run-freebsd-aarch64",
            ["freebsd-aarch64", "disk-image-freebsd-aarch64", "run-freebsd-aarch64"],
        ),
        pytest.param("build-and-run-freebsd-amd64", ["freebsd-amd64", "disk-image-freebsd-amd64", "run-freebsd-amd64"]),
    ],
)
def test_build_and_run(target_name: str, expected_list: "list[str]"):
    assert _sort_targets([target_name], add_dependencies=False) == [*expected_list, target_name]


@pytest.mark.parametrize(
    ("target", "add_toolchain", "expected_deps"),
    [
        # Note: For architectures that CHERI QEMU builds by default we currently
        # explicitly default to using that rather than the system QEMU.
        pytest.param(
            "run-morello-hybrid",
            True,
            [
                "qemu",
                "morello-llvm-native",
                "cheribsd-morello-hybrid",
                "gmp-morello-hybrid",
                "mpfr-morello-hybrid",
                "gdb-morello-hybrid",
                "disk-image-morello-hybrid",
            ],
        ),
        pytest.param(
            "run-morello-purecap",
            True,
            [
                "qemu",
                "morello-llvm-native",
                "cheribsd-morello-purecap",
                "gmp-morello-hybrid-for-purecap-rootfs",
                "mpfr-morello-hybrid-for-purecap-rootfs",
                "gdb-morello-hybrid-for-purecap-rootfs",
                "disk-image-morello-purecap",
            ],
        ),
        pytest.param(
            "run-riscv64",
            True,
            [
                "qemu",
                "llvm-native",
                "cheribsd-riscv64",
                "gmp-riscv64",
                "mpfr-riscv64",
                "gdb-riscv64",
                "disk-image-riscv64",
            ],
        ),
        pytest.param(
            "run-riscv64-hybrid",
            True,
            [
                "qemu",
                "llvm-native",
                "cheribsd-riscv64-hybrid",
                "gmp-riscv64-hybrid",
                "mpfr-riscv64-hybrid",
                "gdb-riscv64-hybrid",
                "bbl-baremetal-riscv64-purecap",
                "disk-image-riscv64-hybrid",
            ],
        ),
        pytest.param(
            "run-riscv64-purecap",
            True,
            [
                "qemu",
                "llvm-native",
                "cheribsd-riscv64-purecap",
                "gmp-riscv64-hybrid-for-purecap-rootfs",
                "mpfr-riscv64-hybrid-for-purecap-rootfs",
                "gdb-riscv64-hybrid-for-purecap-rootfs",
                "bbl-baremetal-riscv64-purecap",
                "disk-image-riscv64-purecap",
            ],
        ),
        pytest.param(
            "run-aarch64",
            True,
            [
                "qemu",
                "llvm-native",
                "cheribsd-aarch64",
                "gmp-aarch64",
                "mpfr-aarch64",
                "gdb-aarch64",
                "disk-image-aarch64",
            ],
        ),
        pytest.param(
            "run-amd64",
            True,
            [
                "qemu",
                "llvm-native",
                "cheribsd-amd64",
                "gmp-amd64",
                "mpfr-amd64",
                "gdb-amd64",
                "disk-image-amd64",
            ],
        ),
        # Morello code won't run on QEMU (yet)
        pytest.param(
            "run-fvp-morello-hybrid",
            True,
            [
                "install-morello-fvp",
                "morello-llvm-native",
                "cheribsd-morello-hybrid",
                "gmp-morello-hybrid",
                "mpfr-morello-hybrid",
                "gdb-morello-hybrid",
                "morello-firmware",
                "disk-image-morello-hybrid",
            ],
        ),
        pytest.param(
            "run-fvp-morello-purecap",
            True,
            [
                "install-morello-fvp",
                "morello-llvm-native",
                "cheribsd-morello-purecap",
                "gmp-morello-hybrid-for-purecap-rootfs",
                "mpfr-morello-hybrid-for-purecap-rootfs",
                "gdb-morello-hybrid-for-purecap-rootfs",
                "morello-firmware",
                "disk-image-morello-purecap",
            ],
        ),
    ],
)
def test_all_run_deps(target: str, add_toolchain: bool, expected_deps: "list[str]"):
    assert _sort_targets(
        [target],
        add_dependencies=True,
        add_toolchain=add_toolchain,
        build_morello_from_source=False,
    ) == [*expected_deps, target]
    assert (
        _sort_targets(
            [target],
            add_dependencies=True,
            add_toolchain=add_toolchain,
            build_morello_from_source=False,
            only_dependencies=True,
        )
        == expected_deps
    )


def test_run_disk_image():
    assert _sort_targets(
        ["run-riscv64-hybrid", "disk-image-riscv64-hybrid", "run-freebsd-riscv64", "llvm", "disk-image-freebsd-amd64"],
    ) == [
        "llvm-native",
        "disk-image-riscv64-hybrid",
        "disk-image-freebsd-amd64",
        "run-riscv64-hybrid",
        "run-freebsd-riscv64",
    ]


def test_remove_duplicates():
    assert _sort_targets(["llvm-native", "llvm"], add_dependencies=True) == ["llvm-native"]


def test_mfs_root_run():
    # Check that we build the mfs root first
    assert _sort_targets(
        [
            "disk-image-mfs-root-riscv64-hybrid",
            "cheribsd-mfs-root-kernel-riscv64-hybrid",
            "run-mfs-root-riscv64-hybrid",
        ],
    ) == [
        "disk-image-mfs-root-riscv64-hybrid",
        "cheribsd-mfs-root-kernel-riscv64-hybrid",
        "run-mfs-root-riscv64-hybrid",
    ]
    assert _sort_targets(
        [
            "cheribsd-mfs-root-kernel-riscv64-hybrid",
            "disk-image-mfs-root-riscv64-hybrid",
            "run-mfs-root-riscv64-hybrid",
        ],
    ) == [
        "disk-image-mfs-root-riscv64-hybrid",
        "cheribsd-mfs-root-kernel-riscv64-hybrid",
        "run-mfs-root-riscv64-hybrid",
    ]


def _check_deps_not_cached(classes):
    for c in classes:
        with pytest.raises(ValueError, match="cached_full_dependencies called before value was cached"):
            # noinspection PyProtectedMember
            c.cached_full_dependencies()


def _check_deps_cached(classes):
    for c in classes:
        # noinspection PyProtectedMember
        assert len(c.cached_full_dependencies()) > 0


def _qtbase_x11_deps(suffix):
    result = [
        x + suffix
        for x in (
            "shared-mime-info-",
            "sqlite-",
            "xorg-macros-",
            "xorgproto-",
            "xcbproto-",
            "libxau-",
            "xorg-pthread-stubs-",
            "libxcb-",
            "libxtrans-",
            "libx11-",
            "xkeyboard-config-",
            "libxkbcommon-",
            "libxcb-render-util-",
            "libxcb-util-",
            "libxcb-image-",
            "libxcb-cursor-",
            "libice-",
            "libsm-",
            "libxext-",
            "libxfixes-",
            "libxi-",
            "libxtst-",
            "libxcb-wm-",
            "libxcb-keysyms-",
            "libpng-",
            "libjpeg-turbo-",
            "dejavu-fonts-",
            "libexpat-",
            "dbus-",
            "freetype2-",
            "fontconfig-",
            "linux-input-h-",
            "mtdev-",
            "libevdev-",
            "libudev-devd-",
            "epoll-shim-",
            "libinput-",
            "libglvnd-",
            "libpciaccess-",
            "libdrm-",
        )
    ]
    if suffix != "native":
        result.insert(result.index("shared-mime-info-" + suffix), "shared-mime-info-native")
    return result


def _avoid_native_qtbase_x11_deps():
    BuildQtBase.use_x11 = True
    BuildQtBase.use_opengl = False
    BuildQtBase.minimal = False
    # Avoid native X11 dependencies:
    qtbase_native = target_manager.get_target_raw("qtbase-native").project_class
    assert issubclass(qtbase_native, BuildQtBase)
    qtbase_native.use_x11 = False
    qtbase_native.use_opengl = False  # also pulls in some X11 deps right now
    qtbase_native.minimal = True  # avoid all non-core deps to keep them the same across operating systems


def test_ksyntaxhighlighting_includes_native_dependency():
    _avoid_native_qtbase_x11_deps()
    ksyntaxhighlighting_deps = _sort_targets(["ksyntaxhighlighting-amd64"], add_dependencies=True, skip_sdk=True)
    assert "ksyntaxhighlighting-native" in ksyntaxhighlighting_deps


def test_webkit_cached_deps():
    # regression test for a bug in caching deps
    config = copy.copy(setup_mock_chericonfig(Path("/this/path/does/not/exist")))
    config.skip_sdk = True
    config.include_toolchain_dependencies = False
    config.include_dependencies = True
    _avoid_native_qtbase_x11_deps()

    webkit_native = target_manager.get_target_raw("qtwebkit-native").project_class
    webkit_purecap = target_manager.get_target_raw("qtwebkit-riscv64-purecap").project_class
    webkit_riscv = target_manager.get_target_raw("qtwebkit-riscv64").project_class
    # Check that the deps are not cached yet
    _check_deps_not_cached((webkit_native, webkit_purecap, webkit_riscv))
    assert inspect.getattr_static(webkit_native, "dependencies") == ("qtbase", "icu4c", "libxml2", "sqlite")
    assert inspect.getattr_static(webkit_purecap, "dependencies") == ("qtbase", "icu4c", "libxml2", "sqlite")
    assert inspect.getattr_static(webkit_riscv, "dependencies") == ("qtbase", "icu4c", "libxml2", "sqlite")

    cheri_target_names = list(sorted(webkit_purecap.all_dependency_names(config)))
    expected_cheri_names = sorted(
        [
            "llvm-native",
            "cheribsd-riscv64-purecap",
            *_qtbase_x11_deps("riscv64-purecap"),
            "qtbase-riscv64-purecap",
            "icu4c-native",
            "icu4c-riscv64-purecap",
            "libxml2-riscv64-purecap",
        ],
    )
    assert cheri_target_names == expected_cheri_names
    _check_deps_not_cached([webkit_native, webkit_riscv])
    _check_deps_cached([webkit_purecap])
    mips_target_names = list(sorted(webkit_riscv.all_dependency_names(config)))
    expected_mips_names = sorted(
        [
            "llvm-native",
            "cheribsd-riscv64",
            *_qtbase_x11_deps("riscv64"),
            "qtbase-riscv64",
            "icu4c-native",
            "icu4c-riscv64",
            "libxml2-riscv64",
        ],
    )
    assert mips_target_names == expected_mips_names
    _check_deps_cached([webkit_purecap, webkit_riscv])
    _check_deps_not_cached([webkit_native])

    native_target_names = list(sorted(webkit_native.all_dependency_names(config)))
    assert native_target_names == [
        "icu4c-native",
        "libxml2-native",
        "qtbase-native",
        "shared-mime-info-native",
        "sqlite-native",
    ]
    _check_deps_cached([webkit_purecap, webkit_riscv, webkit_native])
    assert inspect.getattr_static(webkit_native, "dependencies") == ("qtbase", "icu4c", "libxml2", "sqlite")
    assert inspect.getattr_static(webkit_purecap, "dependencies") == ("qtbase", "icu4c", "libxml2", "sqlite")
    assert inspect.getattr_static(webkit_riscv, "dependencies") == ("qtbase", "icu4c", "libxml2", "sqlite")


def test_webkit_deps_2():
    _avoid_native_qtbase_x11_deps()

    # SDK should not add new targets
    assert _sort_targets(["qtwebkit-native"], add_dependencies=True, skip_sdk=False) == [
        "shared-mime-info-native",
        "sqlite-native",
        "qtbase-native",
        "icu4c-native",
        "libxml2-native",
        "qtwebkit-native",
    ]
    assert _sort_targets(["qtwebkit-native"], add_dependencies=True, skip_sdk=True) == [
        "shared-mime-info-native",
        "sqlite-native",
        "qtbase-native",
        "icu4c-native",
        "libxml2-native",
        "qtwebkit-native",
    ]

    assert _sort_targets(["qtwebkit-riscv64"], add_dependencies=True, skip_sdk=True) == [
        *_qtbase_x11_deps("riscv64"),
        "qtbase-riscv64",
        "icu4c-native",
        "icu4c-riscv64",
        "libxml2-riscv64",
        "qtwebkit-riscv64",
    ]
    assert _sort_targets(["qtwebkit-riscv64-purecap"], add_dependencies=True, skip_sdk=True) == [
        *_qtbase_x11_deps("riscv64-purecap"),
        "qtbase-riscv64-purecap",
        "icu4c-native",
        "icu4c-riscv64-purecap",
        "libxml2-riscv64-purecap",
        "qtwebkit-riscv64-purecap",
    ]


def test_riscv():
    assert _sort_targets(["bbl-baremetal-riscv64", "cheribsd-riscv64"], add_dependencies=False, skip_sdk=False) == [
        "bbl-baremetal-riscv64",
        "cheribsd-riscv64",
    ]
    assert _sort_targets(["run-riscv64"], add_dependencies=True, skip_sdk=True) == ["disk-image-riscv64", "run-riscv64"]
    assert _sort_targets(["run-riscv64-purecap"], add_dependencies=True, skip_sdk=True) == [
        "bbl-baremetal-riscv64-purecap",
        "disk-image-riscv64-purecap",
        "run-riscv64-purecap",
    ]
    assert _sort_targets(["disk-image-riscv64"], add_dependencies=True, skip_sdk=False) == [
        "llvm-native",
        "cheribsd-riscv64",
        "gmp-riscv64",
        "mpfr-riscv64",
        "gdb-riscv64",
        "disk-image-riscv64",
    ]
    assert _sort_targets(["run-riscv64"], add_dependencies=True, skip_sdk=False) == [
        "qemu",
        "llvm-native",
        "cheribsd-riscv64",
        "gmp-riscv64",
        "mpfr-riscv64",
        "gdb-riscv64",
        "disk-image-riscv64",
        "run-riscv64",
    ]


# Check that libcxx deps with skip sdk pick the matching -native/-mips versions
# Also the libcxx target should resolve to libcxx-riscv64-purecap:
@pytest.mark.parametrize(
    ("suffix", "expected_suffix"),
    [
        pytest.param("-native", "-native", id="native"),
        pytest.param("-riscv64", "-riscv64", id="riscv64"),
        pytest.param("-riscv64-purecap", "-riscv64-purecap", id="riscv64-purecap"),
    ],
)
def test_libcxx_deps(suffix: str, expected_suffix: str):
    expected = ["libunwind" + expected_suffix, "libcxxrt" + expected_suffix, "libcxx" + expected_suffix]
    # Now check that the cross-compile versions explicitly chose the matching target:
    assert expected == _sort_targets(["libcxx" + suffix], add_dependencies=True, skip_sdk=True)


@pytest.mark.parametrize(
    ("target_name", "include_recursive_deps", "include_toolchain", "expected_deps", "morello_from_source"),
    [
        pytest.param(
            "morello-firmware",
            False,
            False,
            [
                "morello-scp-firmware",
                "morello-trusted-firmware",
                "morello-flash-images",
                "morello-uefi",
                "morello-firmware",
            ],
            True,
            id="firmware from source (no deps)",
        ),
        pytest.param(
            "morello-firmware",
            True,
            True,
            [
                "morello-scp-firmware",
                "morello-trusted-firmware",
                "morello-flash-images",
                "morello-uefi",
                "morello-firmware",
            ],
            True,
            id="firmware from source (deps)",
        ),
        pytest.param("morello-firmware", True, True, ["morello-firmware"], False, id="firmware dowload (deps)"),
        pytest.param("morello-firmware", False, False, ["morello-firmware"], False, id="firmware dowload (no deps)"),
        pytest.param("morello-uefi", False, False, ["morello-uefi"], True),
        pytest.param("morello-uefi", False, True, ["morello-uefi"], True),
        pytest.param("morello-uefi", True, False, ["morello-uefi"], True),
        pytest.param(
            "morello-uefi",
            True,
            True,
            ["gdb-native", "morello-acpica", "morello-llvm-native", "morello-uefi"],
            True,
        ),
    ],
)
def test_skip_toolchain_deps(
    target_name: str,
    include_recursive_deps: bool,
    include_toolchain: bool,
    expected_deps: "list[str]",
    morello_from_source: bool,
):
    # Check that morello-firmware does not include toolchain dependencies by default, but the individual ones does
    # TODO: should we do the same for all-<target>?
    assert (
        _sort_targets(
            [target_name],
            add_dependencies=include_recursive_deps,
            add_toolchain=include_toolchain,
            build_morello_from_source=morello_from_source,
        )
        == expected_deps
    )


@pytest.mark.parametrize(
    "enable_hybrid_targets",
    [
        pytest.param(True),
        pytest.param(False),
    ],
)
def test_hybrid_targets(enable_hybrid_targets: bool):
    # there should only be very few targets that are built hybrid
    config = setup_mock_chericonfig(Path("/this/path/does/not/exist"))
    config.enable_hybrid_targets = enable_hybrid_targets
    all_hybrid_targets = [
        x
        for x in target_manager.targets(config)
        if x.project_class._xtarget and x.project_class._xtarget.is_cheri_hybrid()
    ]

    def should_include_target(target: Target):
        cls = target.project_class

        # We allow hybrid for baremetal targets:
        xtarget = cls.get_crosscompile_target()
        if xtarget.target_info_cls.is_baremetal():
            return False

        # Syzkaller is always built hybrid
        if issubclass(target.project_class, (BuildSyzkaller, RunSyzkaller)):
            return False

        # Should never see anything else if hybrid targets aren't enabled
        if not enable_hybrid_targets and xtarget.get_rootfs_target().is_cheri_hybrid():
            return True

        # Ignore explicitly requested hybrid-for-purecap-rootfs targets
        if enable_hybrid_for_purecap_rootfs_targets() and xtarget.get_rootfs_target().is_cheri_purecap():
            return False

        # We expect certain targets to be built hybrid: CheriBSD/disk image/GDB/LLVM/run
        if issubclass(
            cls,
            (
                BuildCHERIBSD,
                LaunchCheriBSD,
                BuildCheriBsdSysrootArchive,
                BuildDiskImageBase,
                BuildGDBBase,
                BuildCheriLLVM,
                BuildMorelloLLVM,
                LaunchFVPBase,
                RunCheriSpikeBase,
                BuildSyzkaller,
                RunSyzkaller,
            ),
        ):
            return False
        # Also filter out some target aliases
        if issubclass(
            cls,
            (BuildCheriBsdMfsImageAndKernels, BuildAll, BuildCheriBSDSdk, BuildSdk, BuildAndRunCheriBSD),
        ):
            return False

        # Benchmarks can also be built hybrid:
        if issubclass(cls, BenchmarkMixin):
            return False
        # We also build CMake for the hybrid rootfs so that it can be used by --test mode
        if issubclass(cls, BuildCrossCompiledCMake):
            return False

        # Finally, filter out dependencies of any of the above:
        if issubclass(cls, (BuildGmp, BuildMpfr)):
            return False

        # Otherwise this target is unexpected
        return True

    unexpected_hybrid_targets = filter(should_include_target, all_hybrid_targets)
    assert list(unexpected_hybrid_targets) == []


def _get_native_targets():
    config = setup_mock_chericonfig(Path("/this/path/does/not/exist"))
    for target in target_manager.targets(config):
        if target.xtarget.is_native():
            yield pytest.param(config, target, id=target.name)


@pytest.mark.parametrize(("config", "native_target"), list(_get_native_targets()))
def test_no_dependencies_in_build_dir(config: CheriConfig, native_target: Target):
    # Ensure that native targets do not depend on other targets that do not install their libraries, etc.
    assert native_target.xtarget.is_native()
    proj = native_target.get_or_create_project(native_target.xtarget, config, caller=None)
    if not isinstance(proj, Project):
        assert isinstance(proj, SimpleProject)
        # SimpleProject, so also not installed -> we can ignore this target
        pytest.skip(f"Skipping {proj.target}")
        return
    if proj.get_default_install_dir_kind() in (DefaultInstallDir.IN_BUILD_DIRECTORY, DefaultInstallDir.DO_NOT_INSTALL):
        # Also not installed, we can ignore this target
        pytest.skip(f"Skipping {proj.target}: {proj.get_default_install_dir_kind()}")
        return
    for dep in proj.all_dependency_names(config):
        dep_project = target_manager.get_target(
            dep,
            arch_for_unqualified_targets=native_target.xtarget,
            config=config,
            caller="test",
        ).project_class
        assert issubclass(dep_project, SimpleProject)
        if not issubclass(dep_project, Project):
            continue
        assert (
            dep_project.get_default_install_dir_kind() != DefaultInstallDir.IN_BUILD_DIRECTORY
        ), f"{proj.target} depends on {dep_project.target} which is installed to the build dir!"
        assert (
            dep_project.get_default_install_dir_kind() != DefaultInstallDir.DO_NOT_INSTALL
        ), f"{proj.target} depends on {dep_project.target} which is not installed!"


@pytest.mark.parametrize(
    ("xtarget", "expected"),
    [
        pytest.param(CompilationTargets.CHERIBSD_RISCV_PURECAP, ["llvm-native"]),
        pytest.param(CompilationTargets.CHERIBSD_X86_64, ["llvm-native"]),
        pytest.param(CompilationTargets.CHERIBSD_MORELLO_PURECAP, ["morello-llvm-native"]),
        pytest.param(CompilationTargets.CHERIBSD_MORELLO_NO_CHERI, ["morello-llvm-native"]),
        pytest.param(CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP, ["llvm-native"]),
        pytest.param(CompilationTargets.BAREMETAL_NEWLIB_RISCV64, ["llvm-native"]),
        pytest.param(CompilationTargets.FREESTANDING_RISCV64_PURECAP, ["llvm-native"]),
        pytest.param(CompilationTargets.FREESTANDING_MORELLO_NO_CHERI, ["morello-llvm-native"]),
        pytest.param(CompilationTargets.RTEMS_RISCV64_PURECAP, ["llvm-native"]),
        pytest.param(CompilationTargets.ARM_NONE_EABI, []),
        pytest.param(CompilationTargets.CHERIOS_RISCV_PURECAP, ["cherios-llvm"]),
        pytest.param(CompilationTargets.FREEBSD_RISCV64, ["upstream-llvm"]),
        pytest.param(CompilationTargets.NATIVE, []),
    ],
)
def test_toolchain_dependencies(xtarget: CrossCompileTarget, expected: "list[str]"):
    config = setup_mock_chericonfig(Path("/this/path/does/not/exist"))
    assert xtarget.target_info_cls.toolchain_targets(xtarget, config) == expected
    if expected:
        assert len(expected) == 1
        compiler_target = target_manager.get_target_raw(expected[0])
        assert compiler_target.get_real_target(CompilationTargets.NATIVE, config) == compiler_target
