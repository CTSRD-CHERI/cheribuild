from pathlib import Path
from unittest import mock

from .setup_mock_chericonfig import setup_mock_chericonfig
from pycheribuild.projects.cross.llvm import BuildLLVMMonoRepoBase


def test_generate_config_file_contents():
    source_root = Path("/home/user/src")
    config = setup_mock_chericonfig(source_root)
    # Roots from setup_mock_chericonfig:
    # source_root = /home/user/src
    # build_root = /home/user/src/build
    # output_root = /home/user/src/output
    config.output_root = Path("/home/user/output")

    cfg_dir = Path("/home/user/output/sdk/utils")
    flags = [
        "-target",
        "riscv64-unknown-freebsd13",
        "--sysroot=/home/user/output/sdk/sysroot",
        "-I/home/user/src/include",
        "-L/home/user/output/sdk/lib",
        "-B/home/user/output/sdk/bin",
        "/home/user/output/sdk/lib/crt1.o",
        "-O2",
        "-g",
        "-isystem/home/user/src/include",
        "-Wl,-rpath,/home/user/output/sdk/lib",
        "-Wl,-z,relro",
        "/usr/lib/libc.so",
        "foo/bar/libabc.a",
        "-ffile-prefix-map=/home/user/src=/sources/",
        "-fdebug-prefix-map=/home/user/build=/build/",
        "-fmacro-prefix-map=/home/user/other=/other/",
    ]
    expected_contents = (
        "\n".join(
            [
                "-target",
                "riscv64-unknown-freebsd13",
                "--sysroot=<CFGDIR>/../sysroot",
                "-I<CFGDIR>/../../../src/include",
                "-L<CFGDIR>/../lib",
                "-B<CFGDIR>/../bin",
                "<CFGDIR>/../lib/crt1.o",
                "-O2",
                "-g",
                "-isystem<CFGDIR>/../../../src/include",
                "-Wl,-rpath,<CFGDIR>/../lib",
                "-Wl,-z,relro",
                "/usr/lib/libc.so",  # Not inside any of the roots so we shouldn't replace it
                "foo/bar/libabc.a",  # relative paths should not change
                # Not sure if those should actually change, but let's just be consistent and remap everything
                "-ffile-prefix-map=<CFGDIR>/../../../src=/sources/",
                "-fdebug-prefix-map=<CFGDIR>/../../../build=/build/",
                "-fmacro-prefix-map=<CFGDIR>/../../../other=/other/",
            ]
        )
        + "\n"
    )

    with mock.patch("pathlib.Path.home", return_value=Path("/home/user")):
        assert BuildLLVMMonoRepoBase.generate_config_file_contents(config, flags, cfg_dir) == expected_contents


def test_generate_config_file_contents_home_dir():
    source_root = Path("/home/user/src")
    config = setup_mock_chericonfig(source_root)
    cfg_dir = Path("/home/user/output/sdk/utils")
    flags = [
        "-I/home/user/.local/include",
        "-L/home/user/sdk/lib",
    ]

    with mock.patch("pathlib.Path.home", return_value=Path("/home/user")):
        contents = BuildLLVMMonoRepoBase.generate_config_file_contents(config, flags, cfg_dir)
        # /home/user is 3 levels up from /home/user/output/sdk/utils
        assert "-I<CFGDIR>/../../../.local/include" in contents
        assert "-L<CFGDIR>/../../../sdk/lib" in contents


def test_generate_config_file_contents_no_dir():
    source_root = Path("/home/user/src")
    config = setup_mock_chericonfig(source_root)
    flags = ["-O2", "-g"]
    expected_contents = "-O2\n-g\n"
    assert BuildLLVMMonoRepoBase.generate_config_file_contents(config, flags, None) == expected_contents


def test_generate_config_file_contents_relative_path_in_flags():
    source_root = Path("/home/user/src")
    config = setup_mock_chericonfig(source_root)
    cfg_dir = Path("/home/user/output/sdk/utils")
    # If it's already relative, it should probably be left alone or handled carefully.
    # Current implementation only handles absolute paths.
    flags = ["-Irelative/path", "-L../other/path"]
    expected_contents = "-Irelative/path\n-L../other/path\n"
    assert BuildLLVMMonoRepoBase.generate_config_file_contents(config, flags, cfg_dir) == expected_contents
