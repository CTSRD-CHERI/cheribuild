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

from .crosscompileproject import *
from ..project import ReuseOtherProjectRepository
from ...config.loader import ConfigOptionBase
from ...utils import setEnv, IS_FREEBSD
from pathlib import Path
import inspect
import tempfile


class BuildMibench(CrossCompileProject):
    repository = GitRepository("git@github.com:CTSRD-CHERI/mibench")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    projectName = "mibench"
    # Needs bsd make to build
    make_kind = MakeCommandKind.BsdMake
    # and we have to build in the source directory
    build_in_source_dir = True

    @property
    def bunde_name(self):
        if self.compiling_for_host():
            return "x86"
        if self.compiling_for_mips():
            return "mips"
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
            self.make_args.set(ADDITIONAL_CFLAGS=" ".join(self.default_compiler_flags))
            self.make_args.set(VERSION=self.bunde_name)
            if self.compiling_for_mips():
                self.make_args.set(MIPS_SYSROOT=self.config.get_sysroot_path(CrossCompileTarget.MIPS))
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.make_args.set(VERSION="cheri128", CHERI128_SYSROOT=self.config.cheriSysrootDir)
                else:
                    assert self.config.cheriBits == 256
                    self.make_args.set(VERSION="cheri256", CHERI256_SYSROOT=self.config.cheriSysrootDir)
            self.runMake("bundle_dump")

    def install(self, **kwargs):
        pass  # skip install for now...

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd " + self.bunde_name + "-bundle && ./run_jenkins-bluehive.sh -d0 -r1 -s small " + self.bunde_name
        self.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                      "--test-timeout", str(120 * 60),
                                      mount_builddir=True)


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
            self.make_args.set(ADDITIONAL_CFLAGS=" ".join(self.default_compiler_flags))
            if self.compiling_for_host():
                self.runMake("x86")
            if self.compiling_for_mips():
                self.runMake("mips")
            if self.compiling_for_cheri():
                if self.config.cheriBits == 128:
                    self.runMake("cheriabi128")
                else:
                    assert self.config.cheriBits == 256
                    self.runMake("cheriabi256")

    def install(self, **kwargs):
        pass  # skip install for now...


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
        cls.spec_iso = cls.addPathOption("spec-iso", help="Path to the spec ISO image")
        cls.spec_config_dir = cls.addPathOption("spec-config-dir", help="Path to the CHERI spec config files")
        cls.spec_base_dir = cls.addPathOption("spec-base-dir", help="Path to the CHERI spec build scripts")

    def compile(self, cwd: Path = None):
        for attr in ("spec_iso", "spec_config_dir", "spec_base_dir"):
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
            for dir in Path(self.spec_base_dir).iterdir():
                self.run_cmd("cp", "-a", dir, ".", cwd=self.buildDir / "spec")
            self.run_cmd(self.buildDir / "spec/install.sh", "-f", cwd=self.buildDir / "spec")
        # TODO: allow building hardfloat!
        if self.compiling_for_mips():
            config_name =  "freebsd-mips-clang-softfp"
        elif self.compiling_for_cheri():
            config_name = "freebsd-cheri" + self.config.cheriBitsStr + "-clang-softfp"
        else:
            self.fatal("Compiling for host not implemented yet!")
            return
        # TODO: edit config file
        config_file_text = Path(self.spec_config_dir / (config_name + ".cfg")).read_text()
        # TODO: change CFLAGS to allow dynamic linking
        if self.compiling_for_mips():
            config_file_text = config_file_text.replace("@MIPS_CLANG_BIN@", str(self.config.sdkBinDir))
            config_file_text = config_file_text.replace("@MIPS_SYS_BIN@", str(self.config.sdkBinDir))
            config_file_text = config_file_text.replace("@MIPS_SYSROOT@", str(self.sdkSysroot))
        elif self.compiling_for_cheri():
            config_file_text = config_file_text.replace("@CHERI" + self.config.cheriBitsStr + "_CLANG_BIN@", str(self.config.sdkBinDir))
            config_file_text = config_file_text.replace("@CHERI" + self.config.cheriBitsStr + "_SYS_BIN@", str(self.config.sdkBinDir))
            config_file_text = config_file_text.replace("@CHERI" + self.config.cheriBitsStr + "_SYSROOT@", str(self.sdkSysroot))
        else:
            self.fatal("Not supported")
        self.writeFile(self.buildDir / "spec/config/" / (config_name + ".cfg"), contents=config_file_text,
                       overwrite=True, noCommandPrint=True, mode=0o644)
        benchmark_list = "483"
        script = """
source shrc
runspec -c {spec_config_name} --noreportable --make_bundle {spec_config_name} {benchmark_list}
""".format(benchmark_list=benchmark_list, spec_config_name=config_name)
        self.writeFile(self.buildDir / "build.sh", contents=script, mode=0o755, overwrite=True)
        self.run_cmd("sh", "-x", self.buildDir / "build.sh", cwd=self.buildDir / "spec")

    def install(self, **kwargs):
        pass
