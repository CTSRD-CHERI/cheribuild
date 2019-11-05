#
# Copyright (c) 2017 Alex Richardson
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
import shutil

from .crosscompileproject import *
from ..softboundcets import BuildSoftBoundCETS
from ..effectivesan import BuildEffectiveSan
from ...utils import getCompilerInfo, IS_FREEBSD


class BuildBODiagSuite(CrossCompileCMakeProject):
    projectName = "bodiagsuite"
    repository = GitRepository("https://github.com/CTSRD-CHERI/bodiagsuite",
                               old_urls=[b"https://github.com/nwf/bodiagsuite"])
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    appendCheriBitsToBuildDir = True
    supported_architectures = [CrossCompileTarget.CHERIBSD_MIPS_PURECAP, CrossCompileTarget.NATIVE, CrossCompileTarget.CHERIBSD_MIPS]
    defaultOptimizationLevel = ["-O0"]
    default_build_type = BuildType.DEBUG
    default_use_asan = True
    # _FORTIFY_SOURCE only works with GCC on Linux
    forceDefaultCC = True

    @property
    def build_dir_suffix(self):
        result = ""
        if self.use_stack_protector:
            result += "-stack-protector"
        if self.use_fortify_source:
            result += "-fortify"
        if self.use_valgrind:
            result += "-valgrind"
        if self.use_effectivesan:
            result += "-effectivesan"
        if self.use_softboundcets:
            result += "-softboundcets"
        return result

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.use_valgrind = cls.addBoolOption("use-valgrind", help="Run tests using valgrind (native only)",
                                             only_add_for_targets=[CrossCompileTarget.NATIVE])
        cls.use_stack_protector = cls.addBoolOption("use-stack-protector", help="Compile tests with stack-protector (non-CHERI only)")
        cls.use_fortify_source = cls.addBoolOption("use-fortify-source", help="Compile tests with _DFORTIFY_SOURCE=2 (no effect on FreeBSD)")
        cls.use_softboundcets = cls.addBoolOption("use-softboundcets", help="Compile tests with SoftBoundCETS (native only)", only_add_for_targets=[CrossCompileTarget.NATIVE])
        cls.use_effectivesan = cls.addBoolOption("use-effectivesan", help="Compile tests with EffectiveSan (native only)", only_add_for_targets=[CrossCompileTarget.NATIVE])


    @property
    def CC(self):
        if self.use_effectivesan:
            return BuildEffectiveSan.getInstallDir(self, cross_target=CrossCompileTarget.NATIVE) / "bin/clang"
        if self.use_softboundcets:
            return BuildSoftBoundCETS.getBuildDir(self, cross_target=CrossCompileTarget.NATIVE) / "bin/clang"
        return super().CC

    @property
    def CXX(self):
        if self.use_effectivesan:
            return BuildEffectiveSan.getInstallDir(self, cross_target=CrossCompileTarget.NATIVE) / "bin/clang++"
        if self.use_softboundcets:
            return BuildSoftBoundCETS.getBuildDir(self, cross_target=CrossCompileTarget.NATIVE) / "bin/clang++"
        return super().CXX

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if getCompilerInfo(self.CC).is_clang:
            self.common_warning_flags.append("-Wno-unused-command-line-argument")
        if self.compiling_for_host():
            if [self.use_softboundcets, self.use_effectivesan, self.use_asan, self.use_valgrind].count(True) > 1:
                self.fatal("SoftBoundCETS,EffectiveSaan,ASAN and Valgrind are mutually exclusive options!")
            if self.use_softboundcets:
                self.COMMON_FLAGS.append("-fsoftboundcets")
                self.COMMON_LDFLAGS.append("-lm")
                self.COMMON_LDFLAGS.append("-lrt")
                self.COMMON_LDFLAGS.append("-lsoftboundcets_rt")
                # TODO: would be nice to build the runtime in the build dir and not the source dir..
                self.COMMON_LDFLAGS.append("-L" + str(BuildSoftBoundCETS.getSourceDir(self) / "runtime"))
                # Recent BFD seems unhappy with the softboundcets runtime
                self.COMMON_LDFLAGS.append("-fuse-ld=lld")
            if self.use_effectivesan:
                self.COMMON_FLAGS.append("-fsanitize=effective")
                self.COMMON_FLAGS.extend(["-mllvm", "-effective-warnings"])
                self.COMMON_LDFLAGS.append("-fsanitize=effective")
                self.use_stack_protector = False
                self.use_fortify_source = False
        if self.use_stack_protector:
            if self.use_effectivesan or self.use_softboundcets:
                self.fatal("Stack protector should not be used with effectivesan/softboundcets")
            self.add_cmake_options(WITH_STACK_PROTECTOR=True)
        if self.use_fortify_source:
            if self.use_softboundcets:
                self.fatal("_FORTIFY_SOURCE should not be used with softboundcets")
            self.add_cmake_options(WITH_FORTIFY_SOURCE=True)

    def process(self):
        if self.compiling_for_host() and self.use_softboundcets:
            assert "-fsoftboundcets" in self.default_compiler_flags
            assert "-lsoftboundcets_rt" in self.default_ldflags
        # FIXME: add option to disable FORTIFY_SOURCE
        if self.cross_build_type != BuildType.DEBUG:
            self.warning("BODiagsuite contains undefined behaviour that might be optimized away unless you compile"
                         " at -O0.")
            if not self.queryYesNo("Are you sure you want to continue?"):
                self.fatal("Cannot continue.")
        super().process()

    def compile(self, **kwargs):
        super().compile(**kwargs)
        # TODO: add this copy to the CMakeLists.txt
        self.installFile(self.sourceDir / "Makefile.bsd-run", self.buildDir / "Makefile.bsd-run", force=True)

    def install(*args, **kwargs):
        pass

    def run_tests(self):
        bmake = shutil.which("bmake")
        if bmake is None and IS_FREEBSD:
            # on FreeBSD bmake is
            bmake = shutil.which("make")
        if bmake is None:
            self.fatal("Could not find bmake")
        # Ensure the run directory exists
        self.makedirs(self.buildDir / "run")
        if self.config.clean:
            self.cleanDirectory(self.buildDir / "run", keepRoot=False)
        testsuite_prefix = self.build_configuration_suffix()[1:]
        testsuite_prefix = testsuite_prefix.replace("-build", "")
        extra_args = ["--bmake-path", bmake, "--jobs", str(self.config.makeJobs)] if self.compiling_for_host() else []
        tools = []
        if self.compiling_for_cheri():
            tools.append("cheri")
            if self.config.subobject_bounds and self.config.subobject_bounds != "conservative":
                tools.append("cheri-subobject-bounds")
        if self.use_valgrind:
            assert self.compiling_for_host()
            extra_args.append("--use-valgrind")
            tools.append("valgrind")
        if self.use_softboundcets:
            tools.append("softboundcets")
        if self.use_asan:
            tools.append("asan")
        if self.use_effectivesan:
            tools.append("effectivesan")
        if self.use_fortify_source:
            tools.append("fortify-source")
        if self.use_stack_protector:
            tools.append("stack-protector")
        extra_args.append("--tools")
        extra_args.extend(tools)

        self.run_cheribsd_test_script("run_bodiagsuite.py", "--junit-testsuite-name", testsuite_prefix, *extra_args,
                                      mount_sourcedir=False, mount_builddir=True)
