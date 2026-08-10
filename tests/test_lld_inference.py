import shutil
from pathlib import Path

from .setup_mock_chericonfig import setup_mock_chericonfig
from pycheribuild.config.chericonfig import _default_lld_path
from pycheribuild.processutils import CompilerInfo


def _setup_mock_env(monkeypatch, tmp_path):
    config = setup_mock_chericonfig(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    import pycheribuild.config.chericonfig as chericonfig

    compiler_infos = {}

    def mock_get_compiler_info(path, *, config):
        if path in compiler_infos:
            return compiler_infos[path]
        raise ValueError(f"Unexpected get_compiler_info call for {path}")

    monkeypatch.setattr(chericonfig, "get_compiler_info", mock_get_compiler_info)

    shutil_which_results = {}

    def mock_shutil_which(cmd, path=None):
        return shutil_which_results.get(cmd, None)

    monkeypatch.setattr(shutil, "which", mock_shutil_which)

    return config, bin_dir, compiler_infos, shutil_which_results


def test_lld_inference_clang_versioned(monkeypatch, tmp_path):
    config, bin_dir, compiler_infos, _ = _setup_mock_env(monkeypatch, tmp_path)

    fake_clang_19 = bin_dir / "clang-19"
    fake_clang_19.touch()
    fake_lld_19 = bin_dir / "ld.lld-19"
    fake_lld_19.touch()

    compiler_infos[fake_clang_19] = CompilerInfo(
        path=fake_clang_19,
        compiler="clang",
        version=(19, 0, 0),
        version_str="clang version 19.0.0",
        default_target="x86_64-unknown-linux-gnu",
        config=config,
    )

    config.clang_path = fake_clang_19
    assert _default_lld_path(config, None) == fake_lld_19


def test_lld_inference_clang_unversioned(monkeypatch, tmp_path):
    config, bin_dir, compiler_infos, _ = _setup_mock_env(monkeypatch, tmp_path)

    fake_clang = bin_dir / "clang"
    fake_clang.touch()
    fake_lld = bin_dir / "ld.lld"
    fake_lld.touch()

    compiler_infos[fake_clang] = CompilerInfo(
        path=fake_clang,
        compiler="clang",
        version=(14, 0, 0),
        version_str="clang version 14.0.0",
        default_target="x86_64-unknown-linux-gnu",
        config=config,
    )

    config.clang_path = fake_clang
    assert _default_lld_path(config, None) == fake_lld


def test_lld_inference_gcc_no_lld(monkeypatch, tmp_path):
    config, bin_dir, compiler_infos, shutil_which_results = _setup_mock_env(monkeypatch, tmp_path)

    fake_gcc = bin_dir / "gcc"
    fake_gcc.touch()

    compiler_infos[fake_gcc] = CompilerInfo(
        path=fake_gcc,
        compiler="gcc",
        version=(11, 0, 0),
        version_str="gcc version 11.0.0",
        default_target="x86_64-unknown-linux-gnu",
        config=config,
    )

    config.clang_path = fake_gcc
    shutil_which_results["ld"] = "/usr/bin/ld"
    assert _default_lld_path(config, None) == Path("/usr/bin/ld")


def test_lld_inference_gcc_with_lld_in_path(monkeypatch, tmp_path):
    config, bin_dir, compiler_infos, shutil_which_results = _setup_mock_env(monkeypatch, tmp_path)

    fake_gcc = bin_dir / "gcc"
    fake_gcc.touch()

    compiler_infos[fake_gcc] = CompilerInfo(
        path=fake_gcc,
        compiler="gcc",
        version=(11, 0, 0),
        version_str="gcc version 11.0.0",
        default_target="x86_64-unknown-linux-gnu",
        config=config,
    )

    config.clang_path = fake_gcc
    shutil_which_results["ld.lld"] = "/usr/bin/ld.lld"
    shutil_which_results["ld"] = "/usr/bin/ld"
    # Even if ld.lld is in path, GCC falls back to plain ld
    assert _default_lld_path(config, None) == Path("/usr/bin/ld")


def test_lld_inference_clang_versioned_missing_falls_back_to_local_unversioned(monkeypatch, tmp_path):
    config, bin_dir, compiler_infos, _ = _setup_mock_env(monkeypatch, tmp_path)

    fake_clang_19 = bin_dir / "clang-19"
    fake_clang_19.touch()
    fake_lld = bin_dir / "ld.lld"
    fake_lld.touch()

    compiler_infos[fake_clang_19] = CompilerInfo(
        path=fake_clang_19,
        compiler="clang",
        version=(19, 0, 0),
        version_str="clang version 19.0.0",
        default_target="x86_64-unknown-linux-gnu",
        config=config,
    )

    config.clang_path = fake_clang_19
    assert _default_lld_path(config, None) == fake_lld


def test_lld_inference_clang_versioned_missing_falls_back_to_path_unversioned(monkeypatch, tmp_path):
    config, bin_dir, compiler_infos, shutil_which_results = _setup_mock_env(monkeypatch, tmp_path)

    fake_clang_19 = bin_dir / "clang-19"
    fake_clang_19.touch()

    compiler_infos[fake_clang_19] = CompilerInfo(
        path=fake_clang_19,
        compiler="clang",
        version=(19, 0, 0),
        version_str="clang version 19.0.0",
        default_target="x86_64-unknown-linux-gnu",
        config=config,
    )

    config.clang_path = fake_clang_19
    shutil_which_results["ld.lld"] = "/usr/bin/ld.lld"
    shutil_which_results["ld"] = "/usr/bin/ld"
    assert _default_lld_path(config, None) == Path("/usr/bin/ld.lld")


def test_lld_inference_apple_clang(monkeypatch, tmp_path):
    config, bin_dir, compiler_infos, shutil_which_results = _setup_mock_env(monkeypatch, tmp_path)

    fake_apple_clang = bin_dir / "apple-clang"
    fake_apple_clang.touch()
    fake_lld = bin_dir / "ld.lld"
    fake_lld.touch()

    compiler_infos[fake_apple_clang] = CompilerInfo(
        path=fake_apple_clang,
        compiler="apple-clang",
        version=(14, 0, 0),
        version_str="Apple LLVM version 14.0.0",
        default_target="x86_64-apple-darwin",
        config=config,
    )

    config.clang_path = fake_apple_clang
    shutil_which_results["ld.lld"] = "/usr/bin/ld.lld"
    shutil_which_results["ld"] = "/usr/bin/ld"
    # Apple Clang falls back to plain ld even if ld.lld is in compiler dir or path
    assert _default_lld_path(config, None) == Path("/usr/bin/ld")
