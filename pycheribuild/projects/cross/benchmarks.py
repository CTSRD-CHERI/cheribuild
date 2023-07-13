#
# Copyright (c) 2018 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
import os
import shutil
import tempfile
import typing
from pathlib import Path
from typing import Optional

from .benchmark_mixin import BenchmarkMixin
from .crosscompileproject import (
    CompilationTargets,
    CrossCompileAutotoolsProject,
    CrossCompileProject,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
)
from .llvm_test_suite import BuildLLVMTestSuite, BuildLLVMTestSuiteBase
from ..project import ReuseOtherProjectRepository
from ...config.target_info import CPUArchitecture
from ...processutils import get_program_version
from ...targets import target_manager
from ...utils import OSInfo, is_jenkins_build, replace_one


class BuildMibench(BenchmarkMixin, CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/mibench")
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    target = "mibench"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True
    # Keep the old bundles when cleaning
    _extra_git_clean_excludes = ["--exclude=*-bundle"]
    # The makefiles here can't support any other tagets:
    supported_architectures = (CompilationTargets.NATIVE,)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.benchmark_size = cls.add_config_option("benchmark-size", choices=("small", "large"), default="large",
                                                   kind=str, help="Size of benchmark input data to use")

    @property
    def bundle_dir(self):
        return Path(self.build_dir, self.crosscompile_target.generic_target_suffix +
                    self.build_configuration_suffix() + "-bundle")

    @property
    def benchmark_version(self):
        if self.compiling_for_host():
            return "x86"
        if self.crosscompile_target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            return "cheri" + self.config.mips_cheri_bits_str
        if self.compiling_for_mips(include_purecap=False):
            return "mips-asan" if self.use_asan else "mips"
        raise ValueError("Unsupported target architecture!")

    def compile(self, **kwargs):
        new_env = dict()
        if not self.compiling_for_host():
            new_env = dict(MIPS_SDK=self.target_info.sdk_root_dir,
                           CHERI128_SDK=self.target_info.sdk_root_dir,
                           CHERI256_SDK=self.target_info.sdk_root_dir,
                           CHERI_SDK=self.target_info.sdk_root_dir)
        with self.set_env(**new_env):
            # We can't fall back to /usr/bin/ar here since that breaks on MacOS
            if not self.compiling_for_host():
                self.make_args.set(AR=str(self.sdk_bindir / "llvm-ar") + " rc")
                self.make_args.set(AR2=str(self.sdk_bindir / "llvm-ranlib"))
                self.make_args.set(RANLIB=str(self.sdk_bindir / "llvm-ranlib"))
                self.make_args.set(MIPS_SYSROOT=self.sdk_sysroot, CHERI128_SYSROOT=self.sdk_sysroot,
                                   CHERI256_SYSROOT=self.sdk_sysroot)

            self.make_args.set(ADDITIONAL_CFLAGS=self.commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=self.commandline_to_str(self.default_ldflags))
            self.make_args.set(VERSION=self.benchmark_version)
            self.makedirs(self.build_dir / "bundle")
            self.make_args.set(BUNDLE_DIR=self.build_dir / self.bundle_dir)
            self.run_make("bundle_dump", cwd=self.source_dir)
            if self.compiling_for_mips(include_purecap=False) and self.use_asan:
                self.copy_asan_dependencies(self.build_dir / "bundle/lib")

    def _create_benchmark_dir(self, bench_dir: Path, *, keep_both_sizes: bool):
        self.makedirs(bench_dir)
        self.run_cmd("cp", "-av", self.bundle_dir, bench_dir, cwd=self.build_dir)
        # Remove all the .dump files from the tarball
        if self.config.verbose:
            self.run_cmd("find", bench_dir, "-name", "*.dump", "-print")
        self.run_cmd("find", bench_dir, "-name", "*.dump", "-delete")
        self.run_cmd("du", "-sh", bench_dir)
        if not keep_both_sizes:
            if self.benchmark_size == "large":
                if self.config.verbose:
                    self.run_cmd("find", bench_dir, "-name", "*small*", "-print")
                self.run_cmd("find", bench_dir, "-name", "*small*", "-delete")
            else:
                assert self.benchmark_size == "small"
                if self.config.verbose:
                    self.run_cmd("find", bench_dir, "-name", "*large*", "-print")
                self.run_cmd("find", bench_dir, "-name", "*large*", "-delete")
            self.run_cmd("du", "-sh", bench_dir)
        self.run_cmd("find", bench_dir)
        self.strip_elf_files(bench_dir)

    def install(self, **kwargs):
        if is_jenkins_build():
            self._create_benchmark_dir(self.install_dir, keep_both_sizes=True)
        else:
            self.info("Not installing MiBench for non-Jenkins builds")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
            return
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd '/build/{dirname}' && ./run_jenkins-bluehive.sh -d0 -r1 -s {size} {version}".format(
            dirname=self.bundle_dir.name, size=self.benchmark_size, version=self.benchmark_version)
        self.target_info.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                                  "--test-timeout", str(120 * 60), mount_builddir=True)

    def run_benchmarks(self):
        if not self.compiling_for_mips(include_purecap=True):
            self.fatal("Cannot run these benchmarks for non-MIPS yet")
            return
        with tempfile.TemporaryDirectory() as td:
            self._create_benchmark_dir(Path(td), keep_both_sizes=False)
            benchmark_dir = Path(td, self.bundle_dir.name)
            if not (benchmark_dir / "run_jenkins-bluehive.sh").exists():
                self.fatal("Created invalid benchmark bundle...")
            num_iterations = self.config.benchmark_iterations or 10
            self.run_fpga_benchmark(benchmark_dir, output_file=self.default_statcounters_csv_name,
                                    benchmark_script_args=["-d1", "-r" + str(num_iterations),
                                                           "-s", self.benchmark_size,
                                                           "-o", self.default_statcounters_csv_name,
                                                           self.benchmark_version])


class BuildMiBenchNew(BuildLLVMTestSuiteBase):
    repository = ReuseOtherProjectRepository(source_project=BuildLLVMTestSuite, do_update=True)
    target = "mibench-new"

    def setup(self):
        super().setup()
        self.add_cmake_options(TEST_SUITE_SUBDIRS="MultiSource/Benchmarks/MiBench",
                               TEST_SUITE_COPY_DATA=True)

    def compile(self, **kwargs):
        super().compile(**kwargs)
        self.install_file(self.source_dir / "MultiSource/lit.local.cfg",
                          self.build_dir / "MultiSource/lit.local.cfg", force=True)

    def install(self, **kwargs):
        root_dir = str(self.build_dir / "MultiSource/Benchmarks/MiBench")
        for curdir, dirnames, filenames in os.walk(root_dir):
            # We don't run some benchmarks (e.g. consumer-typeset or consumer-lame) yet
            for ignored_dirname in ('CMakeFiles', 'consumer-typeset', 'consumer-lame', 'office-ispell', 'telecomm-gsm'):
                if ignored_dirname in dirnames:
                    dirnames.remove(ignored_dirname)
            relpath = os.path.relpath(curdir, root_dir)
            for filename in filenames:
                new_file = Path(curdir, filename)
                if new_file.suffix in (".cmake", ".reference_output", ".time", ".test"):
                    continue
                self.install_file(new_file, self.install_dir / relpath / filename, print_verbose_only=True)


class BuildOlden(BenchmarkMixin, CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/olden")
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    target = "olden"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True
    # The makefiles here can't support any other tagets:
    supported_architectures = (CompilationTargets.NATIVE,)

    def compile(self, **kwargs):
        new_env = dict()
        if not self.compiling_for_host():
            new_env = dict(MIPS_SDK=self.target_info.sdk_root_dir,
                           CHERI128_SDK=self.target_info.sdk_root_dir,
                           CHERI256_SDK=self.target_info.sdk_root_dir,
                           CHERI_SDK=self.target_info.sdk_root_dir)
        with self.set_env(**new_env):
            if not self.compiling_for_host():
                self.make_args.set(SYSROOT_DIRNAME=self.cross_sysroot_path.name)
            self.make_args.add_flags("-f", "Makefile.jenkins")
            self.make_args.set(ADDITIONAL_CFLAGS=self.commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=self.commandline_to_str(self.default_ldflags))
            if self.compiling_for_host():
                self.run_make("x86")
            elif self.compiling_for_mips(include_purecap=False):
                self.run_make("mips-asan" if self.use_asan else "mips")
            elif self.crosscompile_target.is_cheri_purecap([CPUArchitecture.MIPS64]):
                self.run_make("cheriabi" + self.config.mips_cheri_bits_str)
            else:
                self.fatal("Unknown target: ", self.crosscompile_target)
        # copy asan libraries and the run script to the bin dir to ensure that we can run with --test from the
        # build directory.
        self.install_file(self.source_dir / "run_jenkins-bluehive.sh",
                          self.build_dir / "bin/run_jenkins-bluehive.sh", force=True)
        if self.compiling_for_mips(include_purecap=False) and self.use_asan:
            self.copy_asan_dependencies(self.build_dir / "bin/lib")

    @property
    def test_arch_suffix(self):
        if self.compiling_for_host():
            return "x86"
        elif self.compiling_for_mips(include_purecap=True):
            if self.crosscompile_target.is_cheri_purecap():
                return "cheri" + self.config.mips_cheri_bits_str
            return "mips-asan" if self.use_asan else "mips"
        else:
            raise ValueError("other arches not supported")

    def install(self, **kwargs):
        self.makedirs(self.install_dir)
        if is_jenkins_build():
            self._create_benchmark_dir(self.install_dir)
        else:
            # Note: no trailing slash to ensure bin/ subdir exists
            self.run_cmd("cp", "-av", self.source_dir / "bin", self.install_dir, cwd=self.build_dir)

    def _create_benchmark_dir(self, bench_dir: Path):
        self.makedirs(bench_dir)
        # Note: no trailing slash to ensure bin/ subdir exists
        self.run_cmd("cp", "-av", self.source_dir / "bin", bench_dir, cwd=self.build_dir)
        # Remove all the .dump files from the tarball
        self.run_cmd("find", bench_dir, "-name", "*.dump", "-delete")
        self.run_cmd("du", "-sh", bench_dir)
        self.strip_elf_files(bench_dir)

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
            return
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = f"cd /build/bin && ./run_jenkins-bluehive.sh -d0 -r1 {self.test_arch_suffix}"
        self.target_info.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                                  "--test-timeout", str(120 * 60),
                                                  mount_builddir=True)

    def run_benchmarks(self):
        if not self.compiling_for_mips(include_purecap=True):
            self.fatal("Cannot run these benchmarks for non-MIPS yet")
            return
        with tempfile.TemporaryDirectory() as td:
            self._create_benchmark_dir(Path(td))
            benchmark_dir = Path(td, "bin")
            self.run_cmd("find", benchmark_dir)
            if not (benchmark_dir / "run_jenkins-bluehive.sh").exists():
                self.fatal("Created invalid benchmark bundle...")
            num_iterations = self.config.benchmark_iterations or 15
            self.run_fpga_benchmark(benchmark_dir, output_file=self.default_statcounters_csv_name,
                                    benchmark_script_args=["-d1", "-r" + str(num_iterations), "-o",
                                                           self.default_statcounters_csv_name,
                                                           self.test_arch_suffix])


class BuildSpec2006New(BuildLLVMTestSuiteBase):
    repository = ReuseOtherProjectRepository(source_project=BuildLLVMTestSuite, do_update=True)
    target = "spec2006"
    spec_iso_path: "typing.ClassVar[Optional[Path]]"
    _config_file_aliases = ("spec2006-new",)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.spec_iso_path = cls.add_optional_path_option("iso-path", altname="spec-sources",
                                                         help="Path to the SPEC2006 ISO image or extracted sources")
        cls.fast_benchmarks_only = cls.add_bool_option("fast-benchmarks-only", default=False)
        cls.workload = cls.add_config_option("workload", choices=("test", "train", "ref"), default="test")
        cls.benchmark_override = cls.add_list_option("benchmarks", help="override the list of benchmarks to run")

    @property
    def extracted_spec_sources(self) -> Path:
        assert self.spec_iso_path is not None, "should only be called after setup()"
        if self.spec_iso_path.is_dir():
            return Path(self.spec_iso_path)  # assume we were passed the path to the extracted sources
        return self.build_dir / "spec-extracted"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Worst case benchmarks: 471.omnetpp 483.xalancbmk 400.perlbench (which won't compile)
        # Approximate duration for 3 runs on the FPGA:
        self.working_benchmark_list = [
            # "400.perlbench", # --- broken
            "401.bzip2",  # 3 runs = 0:10:33 -> ~3:30mins per run
            # "403.gcc", # --- broken
            # "429.mcf",  # Strange tag violation even after fixing realloc() and would use too much memory to
            # run on a 1GB RAM FPGA
            "445.gobmk",  # 3 runs = 1:05:43 -> ~22mins per run
            "456.hmmer",  # 3 runs = 0:05:50 -> ~2mins per run
            "458.sjeng",  # 3 runs = 0:23:14 -> ~7mins per run
            "462.libquantum",  # 3 runs = 0:00:21 -> ~7s per run
            "464.h264ref",  # 3 runs = 1:20:01 -> ~27mins per run
            "471.omnetpp",  # 3 runs = 0:05:09 -> ~1:45min per run
            "473.astar",  # 3 runs = 0:31:41  -> ~10:30 mins per run
            "483.xalancbmk",  # 3 runs = 0:00:55 -> ~20 secs per run"
            ]
        self.complete_benchmark_list = [*self.working_benchmark_list, "400.perlbench", "403.gcc", "429.mcf"]
        self.fast_list = ["471.omnetpp", "483.xalancbmk", "456.hmmer", "462.libquantum"]
        if self.benchmark_override:
            self.benchmark_list = self.benchmark_override
        elif self.fast_benchmarks_only:
            self.benchmark_list = self.fast_list
        else:
            self.benchmark_list = self.working_benchmark_list

    def setup(self):
        if self.spec_iso_path is None:
            self.fatal("You must set --", self.get_config_option_name("spec_iso_path"))
            self.spec_iso_path = Path("/missing/spec2006.iso")
        super().setup()
        # Only build spec2006
        # self.add_cmake_options(TEST_SUITE_SUBDIRS="External/SPEC/CINT2006;External/SPEC/CFP2006",
        self.add_cmake_options(TEST_SUITE_SUBDIRS="External/SPEC/CINT2006",
                               TEST_SUITE_COPY_DATA=True,
                               TEST_SUITE_RUN_TYPE=self.workload,
                               TEST_SUITE_SPEC2006_ROOT=self.extracted_spec_sources)

    def _check_broken_bsdtar(self, bsdtar: Path) -> "tuple[bool, tuple[int, ...]]":
        if self.config.pretend and not bsdtar.exists():
            return False, (0, 0, 0)
        bsdtar_version = get_program_version(bsdtar, regex=rb"bsdtar\s+(\d+)\.(\d+)\.?(\d+)? \- libarchive",
                                             config=self.config)
        # At least version 3.3.2 of libarchive fails to extract the SPEC ISO image correctly (at least two files
        # are missing). This does not appear to be a problem with Ubuntu 18.04's version 3.2.2.
        return (3, 3) <= bsdtar_version < (3, 5), bsdtar_version

    def extract_spec_iso_image(self):
        assert self.spec_iso_path != self.extracted_spec_sources
        if not (self.extracted_spec_sources / "install.sh").exists():
            self.clean_directory(self.extracted_spec_sources)  # clean up partial builds
            bsdtar = Path(shutil.which("bsdtar") or "/could/not/find/bsdtar")
            if self._check_broken_bsdtar(bsdtar)[0] and OSInfo.IS_MAC:
                # macOS 11.4 ships with 3.3.2, try to fall back to homebrew in that case
                libarchive_path = self.get_homebrew_prefix("libarchive")
                bsdtar = libarchive_path / "bin/bsdtar"
            bsdtar_broken, bsdtar_version = self._check_broken_bsdtar(bsdtar)
            if bsdtar_broken:
                self.fatal("The installed version of libarchive (", ".".join(map(str, bsdtar_version)),
                           ") has a bug that results in some files not being extracted from the SPEC ISO.",
                           fixit_hint="Please update bsdtar to at least 3.5.0", sep="")

            self.run_cmd(bsdtar, "xf", self.spec_iso_path, "-C", self.extracted_spec_sources, cwd=self.build_dir)
            # Some of the files in that archive are not user-writable; go pave
            # over the permissions so that we don't die if we try to clean up
            # later.
            self.run_cmd("chmod", "-R", "u+rwX", self.extracted_spec_sources, cwd=self.build_dir)

    def configure(self, **kwargs):
        if not self.spec_iso_path.is_dir():
            # Need to extract the ISO it before configuring
            self.makedirs(self.extracted_spec_sources)
            self.extract_spec_iso_image()
        super().configure(**kwargs)

    def install(self, **kwargs):
        self.install_benchmark_dir(str(self.build_dir / "External/SPEC/CINT2006"))
        # self.install_benchmark_dir(str(self.build_dir / "External/SPEC/CFP2006"))

    def install_benchmark_dir(self, root_dir: str):
        for curdir, dirnames, filenames in os.walk(root_dir):
            # We don't run some benchmarks (e.g. consumer-typeset or consumer-lame) yet
            for ignored_dirname in ('CMakeFiles', ):
                if ignored_dirname in dirnames:
                    dirnames.remove(ignored_dirname)
            relpath = os.path.relpath(curdir, root_dir)
            for filename in filenames:
                new_file = Path(curdir, filename)
                self.install_file(new_file, self.install_dir / relpath / filename, print_verbose_only=True)


for _arch in BuildSpec2006New.supported_architectures:
    _tgt = BuildSpec2006New.get_class_for_target(_arch).target
    target_manager.add_target_alias(replace_one(_tgt, "spec2006-", "spec2006-new-"), _tgt, deprecated=True)


class BuildLMBench(BenchmarkMixin, CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/cheri-lmbench", default_branch="cheri-lmbench")
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    target = "lmbench"
    # Needs bsd make to build
    make_kind = MakeCommandKind.GnuMake
    # and we have to build in the source directory
    build_in_source_dir = True
    # Keep the old bundles when cleaning
    _extra_git_clean_excludes = ["--exclude=*-bundle"]
    # The makefiles here can't support any other tagets:
    supported_architectures = (CompilationTargets.NATIVE,)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

    @property
    def bundle_dir(self):
        return Path(self.build_dir, "lmbench-" + self.crosscompile_target.generic_target_suffix +
                    self.build_configuration_suffix() + "-bundle")

    @property
    def benchmark_version(self):
        if self.compiling_for_host():
            return "x86"
        if self.crosscompile_target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            return "cheri" + self.config.mips_cheri_bits_str
        if self.compiling_for_mips(include_purecap=False):
            return "mips-asan" if self.use_asan else "mips"
        raise ValueError("Unsupported target architecture!")

    def compile(self, **kwargs):
        new_env = dict()
        if not self.compiling_for_host():
            new_env = dict(MIPS_SDK=self.target_info.sdk_root_dir,
                           CHERI128_SDK=self.target_info.sdk_root_dir,
                           CHERI_SDK=self.target_info.sdk_root_dir)
        with self.set_env(**new_env):
            self.make_args.set(CC="clang")
            if not self.compiling_for_host():
                self.make_args.set(AR=str(self.sdk_bindir / "llvm-ar"))
                self.make_args.set(OS="mips64c128-unknown-freebsd")

            self.make_args.set(ADDITIONAL_CFLAGS=self.commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=self.commandline_to_str(self.default_ldflags))
            if self.build_type.is_debug:
                self.run_make("debug", cwd=self.source_dir / "src")
            else:
                self.run_make("build")

    def _create_benchmark_dir(self, install_dir: Path):
        self.makedirs(install_dir)
        self.clean_directory(install_dir / "bin", keep_root=False,
                             ensure_dir_exists=False)
        self.clean_directory(install_dir / "scripts", keep_root=False,
                             ensure_dir_exists=False)
        self.copy_directory(self.build_dir / "bin", install_dir / "bin")
        self.copy_directory(self.build_dir / "scripts", install_dir / "scripts")
        self.install_file(self.source_dir / "src" / "Makefile",
                          install_dir / "src" / "Makefile")
        self.install_file(self.source_dir / "Makefile", install_dir / "Makefile")

    def install(self, **kwargs):
        if is_jenkins_build():
            self._create_benchmark_dir(self.install_dir / self.bundle_dir.name)
        else:
            self._create_benchmark_dir(self.bundle_dir)
            self.info("Not installing LMBench for non-Jenkins builds")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
            return
        # testing, not benchmarking -> run only once
        test_command = f"cd '/build/{self.bundle_dir.name}' && ./run_jenkins-bluehive.sh -d0 -r1 -s"
        self.target_info.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                                  "--test-timeout", str(120 * 60), mount_builddir=True)


class BuildUnixBench(BenchmarkMixin, CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/cheri-unixbench", default_branch="cheri-unixbench")
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    target = "unixbench"
    # Needs bsd make to build
    make_kind = MakeCommandKind.GnuMake
    # and we have to build in the source directory
    build_in_source_dir = True
    # Keep the old bundles when cleaning
    _extra_git_clean_excludes = ["--exclude=*-bundle"]
    # The makefiles here can't support any other tagets:
    supported_architectures = (CompilationTargets.NATIVE,)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.fixed_iterations = cls.add_bool_option(
            "fixed-iterations", default=False,
            help="Run benchmarks for given number of iterations instead of duration.")

    @property
    def bundle_dir(self):
        return Path(self.build_dir, "unixbench-" + self.crosscompile_target.generic_target_suffix +
                    self.build_configuration_suffix() + "-bundle")

    @property
    def benchmark_version(self):
        if self.compiling_for_host():
            return "x86"
        if self.crosscompile_target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            return "cheri" + self.config.mips_cheri_bits_str
        if self.compiling_for_mips(include_purecap=False):
            return "mips-asan" if self.use_asan else "mips"
        raise ValueError("Unsupported target architecture!")

    def compile(self, **kwargs):
        new_env = dict()
        if not self.compiling_for_host():
            new_env = dict(MIPS_SDK=self.target_info.sdk_root_dir,
                           CHERI128_SDK=self.target_info.sdk_root_dir,
                           CHERI_SDK=self.target_info.sdk_root_dir)
        with self.set_env(**new_env):
            self.make_args.set(CC="clang")
            if self.compiling_for_mips(include_purecap=True):
                self.make_args.set(OSNAME="freebsd")
                if self.crosscompile_target.is_cheri_purecap():
                    self.make_args.set(ARCHNAME="mips64c128")
                else:
                    self.make_args.set(ARCHNAME="mips64")

            # link with libstatcounters
            cflags = [*self.default_compiler_flags, "-lstatcounters"]
            if self.fixed_iterations:
                cflags += ["-DUNIXBENCH_FIXED_ITER"]
            self.make_args.set(ADDITIONAL_CFLAGS=self.commandline_to_str(cflags))
            if self.build_type.is_debug:
                self.run_make(cwd=self.source_dir / "UnixBench")
            else:
                self.run_make(cwd=self.source_dir / "UnixBench")

    def _create_benchmark_dir(self, install_dir: Path):
        self.makedirs(install_dir)
        self.clean_directory(install_dir / "pgms", keep_root=False,
                             ensure_dir_exists=False)
        self.copy_directory(self.build_dir / "UnixBench" / "pgms", install_dir / "pgms")
        self.install_file(self.source_dir / "run.sh", install_dir / "run.sh")

    def install(self, **kwargs):
        self._create_benchmark_dir(self.bundle_dir)


class NetPerfBench(BenchmarkMixin, CrossCompileAutotoolsProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/cheri-netperf", default_branch="cheri-netperf")
    target = "netperf"
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    # Needs bsd make to build
    make_kind = MakeCommandKind.GnuMake
    # Keep the old bundles when cleaning
    _extra_git_clean_excludes = ["--exclude=*-bundle"]
    # The makefiles here can't support any other tagets:
    supported_architectures = (CompilationTargets.CHERIBSD_RISCV_NO_CHERI,
                               CompilationTargets.CHERIBSD_RISCV_HYBRID,
                               CompilationTargets.CHERIBSD_RISCV_PURECAP)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.hw_counters = cls.add_config_option("enable-hw-counters",
                                                choices=("pmc", "statcounters"), default="statcounters",
                                                help="Use hardware performance counters")

    def configure(self, **kwargs):
        self.configure_args.append("--enable-unixdomain")
        if self.hw_counters:
            self.configure_args.append(f"--enable-pmc={self.hw_counters}")
        self.add_configure_vars(ac_cv_func_setpgrp_void="yes")
        super().configure(**kwargs)

    def process(self):
        if (self.compiling_for_riscv(include_purecap=True) and
                self.hw_counters == "pmc"):
            self.fatal("hwpmc not supported on riscv")
            return
        super().process()

    def install(self, **kwargs):
        self.run_make_install()
