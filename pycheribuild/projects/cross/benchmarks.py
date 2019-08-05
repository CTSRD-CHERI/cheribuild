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

from .crosscompileproject import *
from ..project import ReuseOtherProjectRepository
from ...config.loader import ConfigOptionBase
from ...utils import setEnv, IS_FREEBSD, commandline_to_str, is_jenkins_build
from pathlib import Path
import inspect
import datetime
import tempfile


class BuildMibench(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/mibench")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    projectName = "mibench"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True
    # Keep the old bundles when cleaning
    _extra_git_clean_excludes = ["--exclude=*-bundle"]

    @property
    def bundle_dir(self):
        return Path(self.buildDir, self.get_crosscompile_target(self.config).value +
                    self.build_configuration_suffix() + "-bundle")

    @property
    def benchmark_version(self):
        if self.compiling_for_host():
            return "x86"
        if self.compiling_for_mips():
            return "mips-asan" if self.compiling_for_mips() else "mips"
        if self.compiling_for_cheri():
            return "cheri" + self.config.cheriBitsStr
        raise ValueError("Unsupported target architecture!")

    def compile(self, **kwargs):
        with setEnv(MIPS_SDK=self.config.sdkDir,
                    CHERI128_SDK=self.config.sdkDir,
                    CHERI256_SDK=self.config.sdkDir,
                    CHERI_SDK=self.config.sdkDir):
            # We can't fall back to /usr/bin/ar here since that breaks on MacOS
            self.make_args.set(AR=str(self.config.sdkBinDir / "ar") + " rc")
            self.make_args.set(AR2=str(self.config.sdkBinDir / "ranlib"))
            self.make_args.set(RANLIB=str(self.config.sdkBinDir / "ranlib"))
            self.make_args.set(ADDITIONAL_CFLAGS=commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=commandline_to_str(self.default_ldflags))
            self.make_args.set(VERSION=self.benchmark_version)
            if self.compiling_for_mips():
                self.make_args.set(MIPS_SYSROOT=self.config.get_sysroot_path(CrossCompileTarget.MIPS))
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.make_args.set(VERSION="cheri128", CHERI128_SYSROOT=self.config.cheriSysrootDir)
                else:
                    assert self.config.cheriBits == 256
                    self.make_args.set(VERSION="cheri256", CHERI256_SYSROOT=self.config.cheriSysrootDir)
            self.makedirs(self.buildDir / "bundle")
            self.make_args.set(BUNDLE_DIR=self.buildDir / self.bundle_dir)
            self.runMake("bundle_dump", cwd=self.sourceDir)
            if self.compiling_for_mips() and self.use_asan:
                self.copy_asan_dependencies(self.buildDir / "bundle/lib")

    def install(self, **kwargs):
        if is_jenkins_build():
            self.makedirs(self.installDir)
            self.run_cmd("cp", "-av", self.bundle_dir, self.installDir, cwd=self.buildDir)
            self.run_cmd("du", "-sh", self.installDir)
            # Remove all the .dump files from the tarball
            self.run_cmd("find", self.installDir, "-name", "*.dump", "-delete")
            self.run_cmd("du", "-sh", self.installDir)
        else:
            self.info("Not installing MiBench for non-Jenkins builds")

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd '/build/" + self.bundle_dir.name + "' && ./run_jenkins-bluehive.sh -d0 -r1 -s small " + self.benchmark_version
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                      "--test-timeout", str(120 * 60),
                                      mount_builddir=True)

    def run_benchmarks(self):
        self.run_fpga_benchmark(self.buildDir / self.bundle_dir.name,
                                output_file=self.default_statcounters_csv_name,
                                benchmark_script_args=["-d1", "-r5", "-s", "small",
                                                       "-o", self.default_statcounters_csv_name,
                                                       self.benchmark_version])

class BuildOlden(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/olden")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    projectName = "olden"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True

    def compile(self, **kwargs):
        with setEnv(MIPS_SDK=self.config.sdkDir,
                    CHERI128_SDK=self.config.sdkDir,
                    CHERI256_SDK=self.config.sdkDir,
                    CHERI_SDK=self.config.sdkDir):
            self.make_args.set(SYSROOT_DIRNAME=self.crossSysrootPath.name)
            self.make_args.add_flags("-f", "Makefile.jenkins")
            self.make_args.set(ADDITIONAL_CFLAGS=commandline_to_str(self.default_compiler_flags))
            self.make_args.set(ADDITIONAL_LDFLAGS=commandline_to_str(self.default_ldflags))
            if self.compiling_for_host():
                self.runMake("x86")
            if self.compiling_for_mips():
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
        if self.compiling_for_mips() and self.use_asan:
            self.copy_asan_dependencies(self.buildDir / "bin/lib")

    @property
    def test_arch_suffix(self):
        if self.compiling_for_host():
            return "x86"
        if self.compiling_for_cheri():
            return "cheri" + self.config.cheriBitsStr
        else:
            assert self.compiling_for_mips(), "other arches not support"
            return "mips-asan" if self.use_asan else "mips"

    def install(self, **kwargs):
        self.makedirs(self.installDir)
        for script in ("run_micro2016.sh", "run_isca2017.sh", "run_jenkins-bluehive.sh"):
            self.installFile(self.sourceDir / script, self.installDir / script, force=True)
        if Path(self.sourceDir / "bin").exists():
            for file in Path(self.sourceDir / "bin").iterdir():
                if file.is_file() and file.name.endswith(".bench"):
                    self.installFile(file, self.installDir / file.name, force=True)
        if is_jenkins_build():
            if self.compiling_for_mips() and self.use_asan:
                self.copy_asan_dependencies(self.installDir / "lib")
            # Remove all the .dump files from the tarball
            self.run_cmd("find", self.installDir, "-name", "*.dump", "-delete")
            self.run_cmd("du", "-sh", self.installDir)

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd /build/bin && ./run_jenkins-bluehive.sh -d0 -r1 {tgt}".format(tgt=self.test_arch_suffix)
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                      "--test-timeout", str(120 * 60),
                                      mount_builddir=True)

    def run_benchmarks(self):
        self.run_fpga_benchmark(self.buildDir / "bin", output_file=self.default_statcounters_csv_name,
                                benchmark_script_args=["-d1", "-r5", "-o", self.default_statcounters_csv_name,
                                                       self.test_arch_suffix])

class BuildSpec2006(CrossCompileProject):
    target = "spec2006"
    projectName = "spec2006"
    # No repository to clone (just hack around this):
    repository = ReuseOtherProjectRepository(BuildOlden, ".")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.ctsrd_evaluation_trunk = cls.addPathOption("ctsrd-evaluation-trunk", help="Path to the CTSRD evaluation/trunk svn checkout")
        cls.ctsrd_evaluation_vendor = cls.addPathOption("ctsrd-evaluation-vendor", help="Path to the CTSRD evaluation/vendor svn checkout")

    @property
    def config_name(self):
        if self.compiling_for_mips():
            build_arch = "mips-" + self.linkage().value
            float_abi = self.config.mips_float_abi.name.lower() + "fp"
            return "freebsd-" + build_arch + "-" + float_abi
        elif self.compiling_for_cheri():
            build_arch = "cheri" + self.config.cheri_bits_and_abi_str + "-" + self.linkage().value
            float_abi = self.config.mips_float_abi.name.lower() + "fp"
            return "freebsd-" + build_arch + "-" + float_abi
        else:
            self.fatal("NOT SUPPORTED YET")
            return "EROROR"

    @property
    def hw_cpu(self):
        if self.compiling_for_mips():
            return "BERI"
        elif self.compiling_for_cheri():
            return "CHERI" + self.config.cheri_bits_and_abi_str
        return "unknown"

    @property
    def spec_config_dir(self) -> Path:
        assert self.ctsrd_evaluation_trunk is not None, "Set --spec2006/ctsrd-evaluation-trunk config option!"
        return self.ctsrd_evaluation_trunk / "201603-spec2006/config"

    @property
    def spec_run_scripts(self) -> Path:
        assert self.ctsrd_evaluation_trunk is not None, "Set --spec2006/ctsrd-evaluation-trunk config option!"
        return self.ctsrd_evaluation_trunk / "spec-cpu2006-v1.1/cheri-scripts/CPU2006"

    @property
    def spec_iso(self) -> Path:
        assert self.ctsrd_evaluation_vendor is not None, "Set --spec2006/ctsrd-evaluation-vendor config option!"
        return self.ctsrd_evaluation_vendor / "SPEC_CPU2006_v1.1/SPEC_CPU2006v1.1.iso"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Worst case benchmarks: 471.omnetpp 483.xalancbmk 400.perlbench (which won't compile)
        self.benchmark_list = ["483.xalancbmk"]

    def compile(self, cwd: Path = None):
        for attr in ("ctsrd_evaluation_trunk", "ctsrd_evaluation_vendor"):
            if not getattr(self, attr):
                option = inspect.getattr_static(self, attr)
                assert isinstance(option, ConfigOptionBase)
                self.fatal("Required SPEC path is not set! Please set", option.fullOptionName)
                return
        self.makedirs(self.buildDir / "spec")
        if not (self.buildDir / "spec/README-CTSRD.txt").exists():
            self.cleanDirectory(self.buildDir / "spec")  # clean up partial builds
            self.run_cmd("bsdtar", "xf", self.spec_iso, "-C", "spec", cwd=self.buildDir)
            self.run_cmd("chmod", "-R", "u+w", "spec/", cwd=self.buildDir)
            self.run_cmd(self.buildDir / "spec/install.sh", "-f", cwd=self.buildDir / "spec")
            #for dir in Path(self.ctsrd_evaluation_trunk / "spec-cpu2006-v1.1").iterdir():
            #    self.run_cmd("cp", "-a", dir, ".", cwd=self.buildDir / "spec")

        config_file_text = Path(self.spec_config_dir / "freebsd-cheribuild.cfg").read_text()
        # FIXME: this should really not be needed....
        self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse") # FIXME: cannot patch xalanbmk
        self.cross_warning_flags.append("-Wno-c++11-narrowing") # FIXME: cannot patch xalanbmk
        self.cross_warning_flags.append("-Wno-logical-op-parentheses") # so noisy in  xalanbmk

        config_file_text = config_file_text.replace("@HW_CPU@", self.hw_cpu)
        config_file_text = config_file_text.replace("@CONFIG_NAME@", self.config_name)
        config_file_text = config_file_text.replace("@CLANG@", str(self.CC))
        config_file_text = config_file_text.replace("@CLANGXX@", str(self.CXX))
        config_file_text = config_file_text.replace("@CFLAGS@", commandline_to_str(self.default_compiler_flags + self.CFLAGS))
        config_file_text = config_file_text.replace("@CXXFLAGS@", commandline_to_str(self.default_compiler_flags + self.CXXFLAGS))
        config_file_text = config_file_text.replace("@LDFLAGS@", commandline_to_str(self.default_ldflags + self.LDFLAGS))
        config_file_text = config_file_text.replace("@SYSROOT@", str(self.sdkSysroot))
        config_file_text = config_file_text.replace("@SYS_BIN@", str(self.config.sdkBinDir))

        self.writeFile(self.buildDir / "spec/config/" / (self.config_name + ".cfg"), contents=config_file_text,
                       overwrite=True, noCommandPrint=False, mode=0o644)

        script = """
source shrc
runspec -c {spec_config_name} --noreportable --make_bundle {spec_config_name} {benchmark_list}
""".format(benchmark_list=commandline_to_str(self.benchmark_list), spec_config_name=self.config_name)
        self.writeFile(self.buildDir / "build.sh", contents=script, mode=0o755, overwrite=True)
        self.run_cmd("bash", "-x", self.buildDir / "build.sh", cwd=self.buildDir / "spec")

    def install(self, **kwargs):
        pass

    def create_tests_dir(self, output_dir: Path) -> Path:
        self.run_cmd("tar", "-xvjf", self.buildDir / "spec/{}.cpu2006bundle.bz2".format(self.config_name),
                     cwd=output_dir)
        self.run_cmd("find", output_dir)
        spec_root = output_dir / "benchspec/CPU2006"
        if spec_root.exists():
            for dir in spec_root.iterdir():
                # Copy run scripts for the benchmarks that we built
                if (self.spec_run_scripts / dir.name).exists():
                    self.run_cmd("cp", "-av", self.spec_run_scripts / dir.name, str(spec_root) + "/")
        run_script = spec_root / "run_jenkins-bluehive.sh"
        self.installFile(self.spec_run_scripts / "run_jenkins-bluehive.sh", run_script, mode=0o755, printVerboseOnly=False)
        if not self.config.pretend:
            assert run_script.stat().st_mode & stat.S_IXUSR

        # To copy all of them:
        # self.run_cmd("cp", "-av", self.spec_run_scripts, output_dir / "benchspec/")
        self.cleanDirectory(output_dir / "config", ensure_dir_exists=False)
        if self.config.verbose:
            self.run_cmd("find", output_dir)
            self.run_cmd("du", "-h", output_dir)
        return output_dir / "benchspec/CPU2006/"

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running host tests is not implemented yet")
        # self.makedirs(self.buildDir / "test")
        #self.run_cmd("tar", "-xvjf", self.buildDir / "spec/{}.cpu2006bundle.bz2".format(self.config_name),
        #             cwd=self.buildDir / "test")
        #self.run_cmd("find", ".", cwd=self.buildDir / "test")
        self.cleanDirectory(self.buildDir / "spec-test-dir")
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
            assert self.compiling_for_mips(), "other arches not support"
            return "mips-asan" if self.use_asan else "mips"

    def run_benchmarks(self):
        # TODO: don't bother creating tempdir if --skip-copy is set
        with tempfile.TemporaryDirectory() as td:
            benchmarks_dir = self.create_tests_dir(Path(td))
            self.run_fpga_benchmark(benchmarks_dir, output_file=self.default_statcounters_csv_name,
                                    benchmark_script_args=["-d0", "-r3",
                                                           "-t", self.config_name,
                                                           "-o", self.default_statcounters_csv_name,
                                                           "-b", commandline_to_str(self.benchmark_list),
                                                           self.bluehive_benchmark_script_archname])
