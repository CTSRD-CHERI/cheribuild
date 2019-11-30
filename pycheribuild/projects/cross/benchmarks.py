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
import stat
import tempfile
from pathlib import Path

from .crosscompileproject import *
from ..project import ExternallyManagedSourceRepository
from ...utils import setEnv, commandline_to_str, is_jenkins_build


class BuildMibench(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/mibench")
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS
    project_name = "mibench"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True
    # Keep the old bundles when cleaning
    _extra_git_clean_excludes = ["--exclude=*-bundle"]
    # The makefiles here can't support any other other tagets:
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_PURECAP, CompilationTargets.CHERIBSD_MIPS,
                               CompilationTargets.NATIVE]

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.benchmark_size = cls.add_config_option("benchmark-size", choices=("small", "large"), default="large",
                                                   kind=str, help="Size of benchmark input data to use")

    @property
    def bundle_dir(self):
        return Path(self.buildDir, self.crosscompile_target.generic_suffix +
                    self.build_configuration_suffix() + "-bundle")

    @property
    def benchmark_version(self):
        if self.compiling_for_host():
            return "x86"
        if self.compiling_for_cheri():
            return "cheri" + self.config.cheriBitsStr
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
        with setEnv(**new_env):
            # We can't fall back to /usr/bin/ar here since that breaks on MacOS
            if not self.compiling_for_host():
                self.make_args.set(AR=str(self.sdk_bindir / "llvm-ar") + " rc")
                self.make_args.set(AR2=str(self.sdk_bindir / "llvm-ranlib"))
                self.make_args.set(RANLIB=str(self.sdk_bindir / "llvm-ranlib"))
            self.make_args.set(ADDITIONAL_CFLAGS=commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=commandline_to_str(self.default_ldflags))
            self.make_args.set(VERSION=self.benchmark_version)
            if self.compiling_for_mips(include_purecap=False):
                self.make_args.set(MIPS_SYSROOT=self.config.get_cheribsd_sysroot_path(CompilationTargets.CHERIBSD_MIPS))
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.make_args.set(VERSION="cheri128", CHERI128_SYSROOT=self.sdk_sysroot)
                else:
                    assert self.config.cheriBits == 256
                    self.make_args.set(VERSION="cheri256", CHERI256_SYSROOT=self.sdk_sysroot)
            self.makedirs(self.buildDir / "bundle")
            self.make_args.set(BUNDLE_DIR=self.buildDir / self.bundle_dir)
            self.runMake("bundle_dump", cwd=self.sourceDir)
            if self.compiling_for_mips(include_purecap=False) and self.use_asan:
                self.copy_asan_dependencies(self.buildDir / "bundle/lib")

    def _create_benchmark_dir(self, bench_dir: Path, *, keep_both_sizes: bool):
        self.makedirs(bench_dir)
        self.run_cmd("cp", "-av", self.bundle_dir, bench_dir, cwd=self.buildDir)
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
            self._create_benchmark_dir(self.installDir, keep_both_sizes=True)
        else:
            self.info("Not installing MiBench for non-Jenkins builds")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd '/build/{dirname}' && ./run_jenkins-bluehive.sh -d0 -r1 -s {size} {version}".format(
            dirname=self.bundle_dir.name, size=self.benchmark_size, version=self.benchmark_version)
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
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
                                    benchmark_script_args=["-d1", "-r" + str(num_iterations), "-s", self.benchmark_size,
                                                           "-o", self.default_statcounters_csv_name,
                                                           self.benchmark_version])


class BuildOlden(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/olden")
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS
    project_name = "olden"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True
    # The makefiles here can't support any other other tagets:
    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_PURECAP, CompilationTargets.CHERIBSD_MIPS,
                               CompilationTargets.NATIVE]

    def compile(self, **kwargs):
        new_env = dict()
        if not self.compiling_for_host():
            new_env = dict(MIPS_SDK=self.target_info.sdk_root_dir,
                           CHERI128_SDK=self.target_info.sdk_root_dir,
                           CHERI256_SDK=self.target_info.sdk_root_dir,
                           CHERI_SDK=self.target_info.sdk_root_dir)
        with setEnv(**new_env):
            if not self.compiling_for_host():
                self.make_args.set(SYSROOT_DIRNAME=self.crossSysrootPath.name)
            self.make_args.add_flags("-f", "Makefile.jenkins")
            self.make_args.set(ADDITIONAL_CFLAGS=commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=commandline_to_str(self.default_ldflags))
            if self.compiling_for_host():
                self.runMake("x86")
            if self.compiling_for_mips(include_purecap=False):
                self.runMake("mips-asan" if self.use_asan else "mips")
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.runMake("cheriabi128")
                else:
                    assert self.config.cheriBits == 256
                    self.runMake("cheriabi256")
        # copy asan libraries and the run script to the bin dir to ensure that we can run with --test from the
        # build directory.
        self.installFile(self.sourceDir / "run_jenkins-bluehive.sh",
                         self.buildDir / "bin/run_jenkins-bluehive.sh", force=True)
        if self.compiling_for_mips(include_purecap=False) and self.use_asan:
            self.copy_asan_dependencies(self.buildDir / "bin/lib")

    @property
    def test_arch_suffix(self):
        if self.compiling_for_host():
            return "x86"
        if self.compiling_for_cheri():
            return "cheri" + self.config.cheriBitsStr
        else:
            assert self.compiling_for_mips(include_purecap=False), "other arches not supported"
            return "mips-asan" if self.use_asan else "mips"

    def install(self, **kwargs):
        self.makedirs(self.installDir)
        if is_jenkins_build():
            self._create_benchmark_dir(self.installDir)
        else:
            # Note: no trailing slash to ensure bin/ subdir exists
            self.run_cmd("cp", "-av", self.sourceDir / "bin", self.installDir, cwd=self.buildDir)

    def _create_benchmark_dir(self, bench_dir: Path):
        self.makedirs(bench_dir)
        # Note: no trailing slash to ensure bin/ subdir exists
        self.run_cmd("cp", "-av", self.sourceDir / "bin", bench_dir, cwd=self.buildDir)
        # Remove all the .dump files from the tarball
        self.run_cmd("find", bench_dir, "-name", "*.dump", "-delete")
        self.run_cmd("du", "-sh", bench_dir)
        self.strip_elf_files(bench_dir)

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd /build/bin && ./run_jenkins-bluehive.sh -d0 -r1 {tgt}".format(tgt=self.test_arch_suffix)
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
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
                                                           self.default_statcounters_csv_name, self.test_arch_suffix])


class BuildSpec2006(CrossCompileProject):
    target = "spec2006"
    project_name = "spec2006"
    # No repository to clone (just hack around this):
    repository = ExternallyManagedSourceRepository()
    native_install_dir = DefaultInstallDir.IN_BUILD_DIRECTORY
    cross_install_dir = DefaultInstallDir.ROOTFS
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.ctsrd_evaluation_trunk = cls.add_path_option("ctsrd-evaluation-trunk", default="/you/must/set --spec2006/ctsrd-evaluation-trunk config option",
                                                        help="Path to the CTSRD evaluation/trunk svn checkout")
        cls.ctsrd_evaluation_vendor = cls.add_path_option("ctsrd-evaluation-vendor",
                                                        default="/you/must/set --spec2006/ctsrd-evaluation-vendor config option",
                                                        help="Path to the CTSRD evaluation/vendor svn checkout")
        cls.fast_benchmarks_only = cls.add_bool_option("fast-benchmarks-only", default=False)
        cls.benchmark_override = cls.add_config_option("benchmarks", default=[], kind=list, help="override the list of benchmarks to run")

    @property
    def config_name(self):
        if self.compiling_for_cheri():
            build_arch = "cheri" + self.config.cheri_bits_and_abi_str + "-" + self.linkage().value
            float_abi = self.config.mips_float_abi.name.lower() + "fp"
            return "freebsd-" + build_arch + "-" + float_abi
        elif self.compiling_for_mips(include_purecap=False):
            build_arch = "mips-" + self.linkage().value
            float_abi = self.config.mips_float_abi.name.lower() + "fp"
            return "freebsd-" + build_arch + "-" + float_abi
        else:
            self.fatal("NOT SUPPORTED YET")
            return "EROROR"

    @property
    def hw_cpu(self):
        if self.compiling_for_mips(include_purecap=False):
            return "BERI"
        elif self.compiling_for_cheri():
            return "CHERI" + self.config.cheri_bits_and_abi_str
        return "unknown"

    @property
    def spec_config_dir(self) -> Path:
        return self.ctsrd_evaluation_trunk / "201603-spec2006/config"

    @property
    def spec_run_scripts(self) -> Path:
        return self.ctsrd_evaluation_trunk / "spec-cpu2006-v1.1/cheri-scripts/CPU2006"

    @property
    def spec_iso(self) -> Path:
        return self.ctsrd_evaluation_vendor / "SPEC_CPU2006_v1.1/SPEC_CPU2006v1.1.iso"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Worst case benchmarks: 471.omnetpp 483.xalancbmk 400.perlbench (which won't compile)
        # Approximate duration for 3 runs on the FPGA:
        self.working_benchmark_list = [
            # "400.perlbench", # --- broken
            "401.bzip2",       # 3 runs = 0:10:33 -> ~3:30mins per run
            # "403.gcc", # --- broken
            # "429.mcf",      # Strange tag violation even after fixing realloc() and would use too much memory to run on 1GB FPGA
            "445.gobmk",      # 3 runs = 1:05:43 -> ~22mins per run
            "456.hmmer",      # 3 runs = 0:05:50 -> ~2mins per run
            "458.sjeng",      # 3 runs = 0:23:14 -> ~7mins per run
            "462.libquantum", # 3 runs = 0:00:21 -> ~7s per run
            "464.h264ref",    # 3 runs = 1:20:01 -> ~27mins per run
            "471.omnetpp",    # 3 runs = 0:05:09 -> ~1:45min per run
            "473.astar",      # 3 runs = 0:31:41  -> ~10:30 mins per run
            "483.xalancbmk",   # 3 runs = 0:00:55 -> ~20 secs per run"
        ]
        self.complete_benchmark_list = self.working_benchmark_list + ["400.perlbench", "403.gcc", "429.mcf"]
        # self.benchmark_list = ["456.hmmer"]
        self.fast_list = ["471.omnetpp", "483.xalancbmk", "456.hmmer", "462.libquantum"]
        if self.benchmark_override:
            self.benchmark_list = self.benchmark_override
        elif self.fast_benchmarks_only:
            self.benchmark_list = self.fast_list
        else:
            self.benchmark_list = self.working_benchmark_list

    def compile(self, cwd: Path = None):
        self.makedirs(self.buildDir / "spec")
        if not (self.buildDir / "spec/install.sh").exists():
            self.clean_directory(self.buildDir / "spec")  # clean up partial builds
            self.run_cmd("bsdtar", "xf", self.spec_iso, "-C", "spec", cwd=self.buildDir)
            self.run_cmd("chmod", "-R", "u+w", "spec/", cwd=self.buildDir)
            self.run_cmd(self.buildDir / "spec/install.sh", "-f", cwd=self.buildDir / "spec")

        # TODO: apply a patch instead?
        benchspec_overrides = self.ctsrd_evaluation_trunk / "spec-cpu2006-v1.1/benchspec"
        if benchspec_overrides.exists():
            for dir in benchspec_overrides.iterdir():
                self.run_cmd("cp", "-a", dir, ".", cwd=self.buildDir / "spec/benchspec")

        config_file_text = self.readFile(self.spec_config_dir / "freebsd-cheribuild.cfg")
        # FIXME: this should really not be needed....
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse") # FIXME: cannot patch xalanbmk
        self.cross_warning_flags.append("-Wno-error=implicit-function-declaration") # FIXME: cannot patch hmmr
        self.cross_warning_flags.append("-Wcheri") # FIXME: cannot patch xalanbmk
        self.cross_warning_flags.append("-Wno-c++11-narrowing") # FIXME: cannot patch xalanbmk
        self.cross_warning_flags.append("-Wno-undefined-bool-conversion")
        self.cross_warning_flags.append("-Wno-writable-strings")
        self.cross_warning_flags.append("-Wno-unused-variable")
        self.cross_warning_flags.append("-Wno-error=format")
        self.cross_warning_flags.append("-Wno-error=mips-cheri-prototypes")  # FIXME: h264 has this, but seeems to run fine
        self.cross_warning_flags.append("-Wno-unused-function")
        self.cross_warning_flags.append("-Wno-logical-op-parentheses") # so noisy in  xalanbmk
        # The C++ benchmarks have narrowing errors if we compile with the default std (c++14)
        self.CXXFLAGS.append("-std=gnu++98")

        config_file_text = config_file_text.replace("@HW_CPU@", self.hw_cpu)
        config_file_text = config_file_text.replace("@CONFIG_NAME@", self.config_name)
        config_file_text = config_file_text.replace("@CLANG@", str(self.CC))
        config_file_text = config_file_text.replace("@CLANGXX@", str(self.CXX))
        config_file_text = config_file_text.replace("@CFLAGS@", commandline_to_str(self.default_compiler_flags + self.CFLAGS + ["-ggdb"]))
        config_file_text = config_file_text.replace("@CXXFLAGS@", commandline_to_str(self.default_compiler_flags + self.CXXFLAGS + ["-ggdb"]))
        config_file_text = config_file_text.replace("@LDFLAGS@", commandline_to_str(self.default_ldflags + self.LDFLAGS))
        config_file_text = config_file_text.replace("@SYSROOT@", str(self.sdk_sysroot) if not self.compiling_for_host() else "/")
        config_file_text = config_file_text.replace("@SYS_BIN@", str(self.sdk_bindir) if not self.compiling_for_host() else "/")

        self.writeFile(self.buildDir / "spec/config/" / (self.config_name + ".cfg"), contents=config_file_text,
                       overwrite=True, noCommandPrint=False, mode=0o644)

        script = """
source shrc
# --make_no_clobber can avoid rebuilds but doesn't seem to work correctly
runspec -c {spec_config_name} --noreportable --action build {benchmark_list}
# ensure that the overwrite prompt gets yes as an answer:
echo y | runspec -c {spec_config_name} --noreportable --nobuild --size test --iterations 1 --make_bundle {spec_config_name} {benchmark_list}
""".format(benchmark_list=commandline_to_str(self.benchmark_list), spec_config_name=self.config_name)
        # TODO: add extra files to the bundle instead of copying later? https://www.spec.org/cpu2006/Docs/runspec.html#makebundle
        self.runShellScript(script, shell="bash", cwd=self.buildDir / "spec")

    def install(self, **kwargs):
        pass

    def create_tests_dir(self, output_dir: Path) -> Path:
        self.__check_valid_benchmark_list()
        spec_archive = self.buildDir / "spec/{}.cpu2006bundle.bz2".format(self.config_name)
        self.run_cmd("tar", "-xvjf", spec_archive, cwd=output_dir,
                     runInPretendMode=spec_archive.exists() and output_dir.exists(), raiseInPretendMode=False)
        spec_root = output_dir / "benchspec/CPU2006"
        if spec_root.exists():
            for dir in spec_root.iterdir():
                if dir.name.startswith("4") and "." in dir.name:
                    assert dir.name in self.complete_benchmark_list, "Got unknown benchmark " + dir.name
                    # Delete all benchmark files for benchmarks that we won't run
                    print(dir.name, self.benchmark_list)
                    if dir.name not in self.benchmark_list:
                        self.run_cmd("rm", "-rf", dir.resolve(), runInPretendMode=True)
                        continue
                # Copy run scripts for the benchmarks that we built
                if (self.spec_run_scripts / dir.name).exists():
                    self.run_cmd("cp", "-av", self.spec_run_scripts / dir.name, str(spec_root) + "/")
        run_script = spec_root / "run_jenkins-bluehive.sh"
        self.installFile(self.spec_run_scripts / "run_jenkins-bluehive.sh", run_script, mode=0o755, print_verbose_only=False)
        self.run_cmd("find", output_dir, runInPretendMode=True)
        if not self.config.pretend:
            assert run_script.stat().st_mode & stat.S_IXUSR

        # Add C++ dependencies for omnetpp and xalanbmk:
        # TODO: should we add these to the minimal disk image? would make things a bit easier.
        cxx_libs = ["libc++.so.1", "libcxxrt.so.1", "libgcc_s.so.1"]
        for needed_lib in cxx_libs:
            libdirs = []
            if self.compiling_for_cheri():
                libdirs = ["usr/libcheri"]
            elif self.compiling_for_mips(include_purecap=False):
                libdirs = ["usr/lib", "lib"]
            for libdir in libdirs:
                guess = Path(self.sdk_sysroot, libdir, needed_lib)
                if guess.exists():
                    self.installFile(guess, spec_root / "lib" / needed_lib, print_verbose_only=False, force=True)

        # Add libcheri_caprevoke if it exists:
        if self.compiling_for_cheri():
            caprevoke = "libcheri_caprevoke.so.1"
            if (self.sdk_sysroot / "usr/libcheri" / caprevoke).exists():
                self.installFile(self.sdk_sysroot / "usr/libcheri" / caprevoke, spec_root / "lib" / caprevoke,
                                 print_verbose_only=False, force=True)

        # To copy all of them:
        # self.run_cmd("cp", "-av", self.spec_run_scripts, output_dir / "benchspec/")
        self.clean_directory(output_dir / "config", ensure_dir_exists=False)
        if self.config.verbose:
            self.run_cmd("find", output_dir)
            self.run_cmd("du", "-h", output_dir)
        return output_dir / "benchspec/CPU2006/"

    def run_tests(self):
        if not self.compiling_for_mips(include_purecap=True):
            self.fatal("Cannot run these benchmarks for non-MIPS yet")
            return
        # self.makedirs(self.buildDir / "test")
        #self.run_cmd("tar", "-xvjf", self.buildDir / "spec/{}.cpu2006bundle.bz2".format(self.config_name),
        #             cwd=self.buildDir / "test")
        #self.run_cmd("find", ".", cwd=self.buildDir / "test")
        self.clean_directory(self.buildDir / "spec-test-dir")
        benchmarks_dir = self.create_tests_dir(self.buildDir / "spec-test-dir")
        test_command = """
export LD_LIBRARY_PATH=/sysroot/usr/lib:/sysroot/lib;
export LD_CHERI_LIBRARY_PATH=/sysroot/usr/libcheri;
cd /build/spec-test-dir/benchspec/CPU2006/ && ./run_jenkins-bluehive.sh -b "{bench_list}" -t {config} -d0 -r1 {arch}""".format(
            config=self.config_name, bench_list=" ".join(self.benchmark_list),
            arch=self.bluehive_benchmark_script_archname)
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                      "--test-timeout", str(120 * 60),
                                      mount_builddir=True, mount_sysroot=True)

    @property
    def bluehive_benchmark_script_archname(self):
        if self.compiling_for_host():
            return "x86"
        if self.compiling_for_cheri():
            return "cheri" + self.config.cheriBitsStr
        else:
            assert self.compiling_for_mips(include_purecap=False), "other arches not supported"
            return "mips-asan" if self.use_asan else "mips"

    def run_benchmarks(self):
        if not self.compiling_for_mips(include_purecap=True):
            self.fatal("Cannot run these benchmarks for non-MIPS yet")
            return
        # TODO: don't bother creating tempdir if --skip-copy is set
        with tempfile.TemporaryDirectory() as td:
            benchmarks_dir = self.create_tests_dir(Path(td))
            num_iterations = self.config.benchmark_iterations or 3
            self.run_fpga_benchmark(benchmarks_dir, output_file=self.default_statcounters_csv_name,
                                    # The benchmarks take a long time to run -> allow up to a 3 hours per iteration
                                    extra_runbench_args=["--timeout", str(60 * 60 * 3 * num_iterations)],
                                    benchmark_script_args=["-d1", "-r" + str(num_iterations),
                                                           "-t", self.config_name,
                                                           "-o", self.default_statcounters_csv_name,
                                                           "-b", commandline_to_str(self.benchmark_list),
                                                           self.bluehive_benchmark_script_archname])


    def __check_valid_benchmark_list(self):
        for x in self.benchmark_list:
            print(x, x in self.complete_benchmark_list)
            if x not in self.complete_benchmark_list:
                self.fatal("Benchmark", x, "is not a valid benchmark. Complete list:",
                           " ".join(sorted(self.complete_benchmark_list)))

    def process(self):
        self.__check_valid_benchmark_list()
        super().process()
