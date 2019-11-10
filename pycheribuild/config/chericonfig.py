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
import getpass
import grp
import json
import os
import typing
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Optional

# Need to import loader here and not `from loader import ConfigLoader` because that copies the reference
from .loader import ConfigLoaderBase
from .target_info import (TargetInfo, CheriBSDTargetInfo, NativeTargetInfo, FreeBSDTargetInfo,
                          NewlibBaremetalTargetInfo, CPUArchitecture)
from ..utils import latestClangTool, warningMessage, statusUpdate, have_working_internet_connection

if typing.TYPE_CHECKING:
    from ..filesystemutils import FileSystemUtils


# custom encoder to handle pathlib.Path objects
class MyJsonEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        # noinspection PyArgumentList
        super().__init__(*args, **kwargs)

    def default(self, o):
        if isinstance(o, Path):
            return str(o)
        return super().default(o)


class CrossCompileTarget(Enum):
    NONE = ("invalid", None, False, None)  # Placeholder
    NATIVE = ("native", CPUArchitecture.X86_64, False, NativeTargetInfo)  # XXX: should probably not harcode x86_64
    CHERIBSD_MIPS = ("mips", CPUArchitecture.MIPS64, False, CheriBSDTargetInfo)
    CHERIBSD_MIPS_PURECAP = ("cheri", CPUArchitecture.MIPS64, True, CheriBSDTargetInfo, CHERIBSD_MIPS)
    CHERIBSD_RISCV = ("riscv", CPUArchitecture.RISCV64, False, CheriBSDTargetInfo)
    CHERIBSD_X86_64 = ("native", CPUArchitecture.X86_64, False, CheriBSDTargetInfo)
    BAREMETAL_NEWLIB_MIPS64 = ("baremetal-mips", CPUArchitecture.MIPS64, False, NewlibBaremetalTargetInfo)
    BAREMETAL_NEWLIB_MIPS64_PURECAP = ("baremetal-purecap-mips", CPUArchitecture.MIPS64, True, NewlibBaremetalTargetInfo,
                                       BAREMETAL_NEWLIB_MIPS64)
    FREEBSD_MIPS = ("mips", CPUArchitecture.MIPS64, False, FreeBSDTargetInfo)
    FREEBSD_RISCV = ("riscv", CPUArchitecture.RISCV64, False, FreeBSDTargetInfo)
    FREEBSD_I386 = ("i386", CPUArchitecture.I386, False, FreeBSDTargetInfo)
    FREEBSD_AARCH64 = ("aarch64", CPUArchitecture.AARCH64, False, FreeBSDTargetInfo)
    FREEBSD_X86_64 = ("x86_64", CPUArchitecture.X86_64, False, FreeBSDTargetInfo)

    def __init__(self, suffix: str, cpu_architecture: CPUArchitecture, is_cheri_purecap: bool,
                 target_info_cls: "typing.Type[TargetInfo]", check_conflict_with: "CrossCompileTarget" = None):
        self.generic_suffix = suffix
        self.cpu_architecture = cpu_architecture
        # TODO: self.operating_system = ...
        self._is_cheri_purecap = is_cheri_purecap
        self.check_conflict_with = check_conflict_with  # Check that we don't reuse install-dir, etc for this target
        self.target_info_cls = target_info_cls

    def create_target_info(self, project: "SimpleProject") -> TargetInfo:
        return self.target_info_cls(self, project)

    def build_suffix(self, config: "CheriConfig", *, build_hybrid=False):
        assert self is not CrossCompileTarget.NONE
        if self is CrossCompileTarget.CHERIBSD_MIPS_PURECAP:
            result = ""  # only -128/-256 for legacy build dir compat
        elif self is CrossCompileTarget.CHERIBSD_MIPS:
            result = "-" + self.generic_suffix
            if build_hybrid:
                result += "-hybrid" + config.cheri_bits_and_abi_str
            if config.mips_float_abi == MipsFloatAbi.HARD:
                result += "-hardfloat"
        else:
            result = "-" + self.generic_suffix

        if self._is_cheri_purecap:
            result += "-" + config.cheri_bits_and_abi_str
        if config.cross_target_suffix:
            result += "-" + config.cross_target_suffix
        return result

    def is_native(self):
        """returns true if we building for the curent host"""
        return self is CrossCompileTarget.NATIVE

    # Querying the CPU architecture:
    def is_mips(self, include_purecap: bool = None):
        if include_purecap is None:
            # Check that cases that want to handle both pass an explicit argument
            assert self is not CrossCompileTarget.CHERIBSD_MIPS_PURECAP, "Should check purecap cases first"
        if not include_purecap and self._is_cheri_purecap:
            return False
        return self.cpu_architecture is CPUArchitecture.MIPS64

    def is_riscv(self, include_purecap: bool = None):
        return self.cpu_architecture is CPUArchitecture.RISCV64

    def is_aarch64(self):
        return self.cpu_architecture is CPUArchitecture.AARCH64

    def is_i386(self):
        return self.cpu_architecture is CPUArchitecture.I386

    def is_x86_64(self):
        return self.cpu_architecture is CPUArchitecture.X86_64

    def is_any_x86(self):
        return self.is_i386() or self.is_x86_64()

    def pointer_size(self, config: "CheriConfig"):
        if self._is_cheri_purecap:
            assert config.cheriBits in (128, 256), "No other cap size supported yet"
            return config.cheriBits / 8
        elif self.is_i386():
            return 4
        # all other architectures we support currently use 64-bit pointers
        return 8

    def is_cheri_purecap(self, valid_cpu_archs: "typing.List[CPUArchitecture]" = None):
        if valid_cpu_archs is None:
            return self._is_cheri_purecap
        if not self._is_cheri_purecap:
            return False
        # Purecap target, but must first check if one of the accepted architectures matches
        for a in valid_cpu_archs:
            if a is self.cpu_architecture:
                return True
        return False

    # def __eq__(self, other):
    #     raise NotImplementedError("Should not compare to CrossCompileTarget, use the is_foo() methods.")


class BuildType(Enum):
    DEFAULT = "Default"
    DEBUG = "Debug"
    RELEASE = "Release"
    RELWITHDEBINFO = "RelWithDebInfo"
    MINSIZEREL = "MinSizeRel"
    MINSIZERELWITHDEBINFO = "MinSizeRelWithDebInfo"

    @property
    def should_include_debug_info(self):
        return self in (BuildType.DEBUG, BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO)


class Linkage(Enum):
    DEFAULT = "default"
    STATIC = "static"
    DYNAMIC = "dynamic"


class MipsFloatAbi(Enum):
    SOFT = ("mips64", "-msoft-float")
    HARD = ("mips64hf", "-mhard-float")

    def freebsd_target_arch(self):
        return self.value[0]

    def clang_float_flag(self):
        return self.value[1]


class CheriConfig(object):
    DEFAULT_CAP_TABLE_ABI = "pcrel"
    DEFAULT_SUBOBJECT_BOUNDS = "conservative"

    def __init__(self, loader: ConfigLoaderBase, action_class):
        loader._cheriConfig = self
        self.loader = loader
        self.pretend = loader.addCommandLineOnlyBoolOption("pretend", "p",
                                                           help="Only print the commands instead of running them")

        # add the actions:
        self.action = loader.addOption("action", default=[], action="append", type=action_class, helpHidden=True,
                                       help="The action to perform by cheribuild", group=loader.actionGroup)
        self.default_action = None
        # Add aliases (e.g. --test = --action=test):
        for action in action_class:
            if action.altname:
                loader.actionGroup.add_argument(action.option_name, action.altname, help=action.help_message,
                                                dest="action", action="append_const", const=action.actions)
            else:
                loader.actionGroup.add_argument(action.option_name, help=action.help_message, dest="action",
                                                action="append_const", const=action.actions)
        self.print_targets_only = loader.addBoolOption("print-targets-only", helpHidden=False, group=loader.actionGroup,
            help="Don't run the build but instead only print the targets that would be executed")

        self.clangPath = loader.addPathOption("clang-path", shortname="-cc-path",
              default=latestClangTool("clang"), group=loader.pathGroup,
              help="The C compiler to use for host binaries (must be compatible with Clang >= 3.7)")
        self.clangPlusPlusPath = loader.addPathOption("clang++-path", shortname="-c++-path",
              default=latestClangTool("clang++"), group=loader.pathGroup,
              help="The C++ compiler to use for host binaries (must be compatible with Clang >= 3.7)")
        self.clangCppPath = loader.addPathOption("clang-cpp-path", shortname="-cpp-path",
              default=latestClangTool("clang-cpp"), group=loader.pathGroup,
              help="The C preprocessor to use for host binaries (must be compatible with Clang >= 3.7)")

        self.passDashKToMake = loader.addCommandLineOnlyBoolOption("pass-k-to-make", "k",
                                                                   help="Pass the -k flag to make to continue after"
                                                                        " the first error")
        self.withLibstatcounters = loader.addBoolOption("with-libstatcounters", group=loader.crossCompileOptionsGroup,
                                                        help="Link cross compiled CHERI project with libstatcounters.")
        self.use_hybrid_sysroot_for_mips = loader.addBoolOption("use-hybrid-sysroot-for-mips",
            group=loader.crossCompileOptionsGroup, default=True,
            help="Build and install MIPS binaries against the hybrid sysroot instead of using a sysroot built without "
                 "CHERI support. Do not unset this option when building benchmarks since memcpy will be slower!")
        self.skipBuildworld = loader.addBoolOption("skip-buildworld", "-skip-world", group=loader.freebsdGroup,
                                                   help="Skip the buildworld step when building FreeBSD or CheriBSD")
        self.freebsd_kernconf = loader.addOption("kernel-config", "-kernconf", group=loader.freebsdGroup, helpHidden=True,
                                                 help="Override default kernel config to use.")
        self.freebsd_subdir = loader.addCommandLineOnlyOption("freebsd-subdir", "-subdir",
            group=loader.freebsdGroup, type=list, metavar="SUBDIRS",
            help="Only build subdirs SUBDIRS of FreeBSD/CheriBSD instead of the full tree. Useful "
            "for quickly rebuilding an individual programs/libraries. If more than one dir is passed they will be "
            "processed in order.  Note: This will break if not all dependencies have been built.")
        self.freebsd_host_tools_only = loader.addCommandLineOnlyBoolOption("freebsd-host-tools-only", helpHidden=True,
            group=loader.freebsdGroup, help="Stop the FreeBSD/CheriBSD build after the host tools have been built")

        self.install_subdir_to_sysroot = loader.addBoolOption("install-subdir-to-sysroot", group=loader.freebsdGroup,
            help="When using the --subdir option for CheriBSD targets also install the built libraries into the sysroot."
                 " This can also be achived by running the cheribsd-sysroot target afterwards but is faster.")

        self.buildenv = loader.addCommandLineOnlyBoolOption("buildenv", group=loader.freebsdGroup,
                                                            help="Open a shell with the right environment for building"
                                                                 " the project. Currently only works for FreeBSD/CheriBSD")
        self.libcheri_buildenv = loader.addCommandLineOnlyBoolOption("libcheri-buildenv", group=loader.freebsdGroup,
             help="Open a shell with the right environment for building CHERI libraries. Currently only works for CheriBSD")

        self.cheri_cap_table_abi = loader.addOption("cap-table-abi", helpHidden=True, default="pcrel",
                                                    choices=("pcrel", "plt", "legacy", "fn-desc"),
                                                    help="The ABI to use for cap-table mode")
        self.cross_target_suffix = loader.addOption("cross-target-suffix", helpHidden=True, default="",
                                                    help="Add a suffix to the cross build and install directories. "
                                                         "With VALUE=-pcrel it will use /opt/cheriXXX-pcrel/$PROJECT")

        # Attributes for code completion:
        self.verbose = None  # type: Optional[bool]
        self.debug_output = loader.addCommandLineOnlyBoolOption("debug-output", "vv", help="Extremely verbose output")
        self.quiet = None  # type: Optional[bool]
        self.clean = None  # type: Optional[bool]
        self.force = None  # type: Optional[bool]
        self.write_logfile = None  # type: Optional[bool]
        self.skipUpdate = None  # type: Optional[bool]
        self.skipClone = None  # type: Optional[bool]
        self.skipConfigure = None  # type: Optional[bool]
        self.forceConfigure = None  # type: Optional[bool]
        self.force_update = None  # type: Optional[bool]
        self.mips_float_abi = loader.addOption("mips-float-abi", default=MipsFloatAbi.SOFT, type=MipsFloatAbi,
                                               group=loader.crossCompileOptionsGroup,
                                               help="The floating point ABI to use for building MIPS+CHERI programs")
        self.crosscompile_linkage = loader.addOption("cross-compile-linkage", default=Linkage.DYNAMIC, type=Linkage,
                                                     group=loader.crossCompileOptionsGroup,
                                                     enum_choices=(Linkage.DYNAMIC, Linkage.STATIC),
                                                     help="Whether to link cross-compile projects static or dynamic by default")
        self.csetbounds_stats = loader.addBoolOption("collect-csetbounds-stats",
                                                     group=loader.crossCompileOptionsGroup, helpHidden=True,
                                                     help="Whether to log CSetBounds statistics in csv format")
        self.subobject_bounds = loader.addOption("subobject-bounds", type=str, group=loader.crossCompileOptionsGroup,
            choices=("conservative", "subobject-safe", "aggressive", "very-aggressive", "everywhere-unsafe"),
            help="Whether to add additional CSetBounds to subobject references/&-operator")
        self.subobject_debug = loader.addBoolOption("subobject-debug", group=loader.crossCompileOptionsGroup,
            default=True, helpHidden=False, help="Clear software permission bit 2 when subobject bounds reduced size"
                                                 " (Note: this should be turned off for benchmarks!)")

        self.clang_colour_diags = loader.addBoolOption("clang-colour-diags", "-clang-color-diags", default=True,
                                                       help="Force CHERI clang to emit coloured diagnostics")
        self.use_sdk_clang_for_native_xbuild = loader.addBoolOption("use-sdk-clang-for-native-xbuild",
                                                                    group=loader.crossCompileOptionsGroup,
                                                                    help="Compile cross-compile project with CHERI "
                                                                         "clang from the SDK instead of host compiler")

        self.configureOnly = loader.addBoolOption("configure-only",
                                                  help="Only run the configure step (skip build and install)")
        self.skipInstall = loader.addBoolOption("skip-install", help="Skip the install step (only do the build)")
        self.skipBuild = loader.addBoolOption("skip-build", help="Skip the build step (only do the install)")
        self.skipSdk = loader.addBoolOption("skip-sdk", help="When building with --include-dependencies ignore the "
                                                             "CHERI sdk dependencies. Saves a lot of time when "
                                                             "building libc++, etc. with dependencies but the sdk "
                                                             "is already up-to-date")

        self.trap_on_unrepresentable = loader.addBoolOption("trap-on-unrepresentable", default=False,
            help="Raise a CHERI exception when capabilities become unreprestable instead of detagging. Useful for "
                 "debugging, but deviates from the spec, and therefore off by default.")
        self.includeDependencies = None  # type: Optional[bool]
        self.crossCompileTarget = None  # type: Optional[CrossCompileTarget]
        self.makeWithoutNice = None  # type: Optional[bool]

        self.cheriBits = None  # type: Optional[int]
        self.makeJobs = None  # type: Optional[int]

        self.sourceRoot = None  # type: Optional[Path]
        self.outputRoot = None  # type: Optional[Path]
        self.buildRoot = None  # type: Optional[Path]
        # Path to kernel/disk images (this is the same as outputRoot by default but different in Jenkins)
        self.cheribsd_image_root = None  # type: Optional[Path]
        self.sdkDir = None  # type: Optional[Path]
        self.otherToolsDir = None  # type: Optional[Path]
        self.docker = loader.addBoolOption("docker", help="Run the build inside a docker container",
                                           group=loader.dockerGroup)
        self.docker_container = loader.addOption("docker-container", help="Name of the docker container to use",
                                                 default="cheribuild-test", group=loader.dockerGroup)
        self.docker_reuse_container = loader.addBoolOption("docker-reuse-container", group=loader.dockerGroup,
            help="Attach to the same container again (note: docker-container option must be an id rather than a container name")

        # compilation db options:
        self.create_compilation_db = loader.addCommandLineOnlyBoolOption(
            "compilation-db", "-cdb", help="Create a compile_commands.json file in the build dir "
                                           "(requires Bear for non-CMake projects)")
        self.copy_compilation_db_to_source_dir = None  # False for jenkins, an option for cheribuild


        # Test options:
        self.test_ssh_key = loader.addPathOption("test-ssh-key", default=os.path.expanduser("~/.ssh/id_ed25519.pub"),
                                                 help="The SSH key to used to connect to the QEMU instance when running"
                                                      " tests on CheriBSD", group=loader.testsGroup)
        # This is currently the default since we don't build a minimal MIPS image yet
        self.run_mips_tests_with_cheri_image = loader.addBoolOption("run-mips-tests-with-cheri-image",
            default=True, help="Use a CHERI kernel+image to run plain MIPS CheriBSD tests. "
                               "This only affects the --test option", group=loader.testsGroup)
        self.use_minimal_benchmark_kernel = loader.addBoolOption("use-minimal-benchmark-kernel",
            help="Use a CHERI BENCHMARK version of the cheribsd-mfs-root-kernel (without INVARIATES) for the "
                 "run-minimal target and for tests. This can speed up longer running tests. This is the default for "
                 "PostgreSQL and libc++ tests (passing use-minimal-benchmark-kernel can force these tests to use "
                 "an INVARIANTS kernel).", group=loader.testsGroup, default=False)

        self.test_extra_args = loader.addCommandLineOnlyOption("test-extra-args", group=loader.testsGroup, type=list,
            metavar="ARGS", help="Additional flags to pass to the test script in --test")
        self.tests_interact = loader.addCommandLineOnlyBoolOption("interact-after-tests", group=loader.testsGroup,
            help="Interact with the CheriBSD instance after running the tests on QEMU (only for --test)")
        self.tests_env_only = loader.addCommandLineOnlyBoolOption("test-environment-only", group=loader.testsGroup,
            help="Don't actually run the tests. Instead setup a QEMU instance with the right paths set up.")
        self.test_ld_preload = loader.addPathOption("test-ld-preload", group=loader.testsGroup,
                                                    help="Preload the given library before running tests")

        self.benchmark_fpga_extra_args = loader.addCommandLineOnlyOption("benchmark-fpga-extra-args", group=loader.benchmarkGroup,
                                                                         type=list, metavar="ARGS",
                                                                         help="Extra options for beri-fpga-bsd-boot.py")
        self.benchmark_clean_boot = loader.addBoolOption("benchmark-clean-boot", group=loader.benchmarkGroup,
            help="Reboot the FPGA with a new bitfile and kernel before running benchmarks. "
                 "If not set, assume the FPGA is running.")
        self.benchmark_extra_args = loader.addCommandLineOnlyOption("benchmark-extra-args", group=loader.benchmarkGroup, type=list,
            metavar="ARGS", help="Additional flags to pass to the beri-fpga-bsd-boot.py script in --benchmark")
        self.benchmark_ssh_host = loader.addOption("benchmark-ssh-host", group=loader.benchmarkGroup, type=str,
                                                   default="cheri-fpga", help="The SSH hostname/IP for the benchmark FPGA")
        self.benchmark_statcounters_suffix = loader.addOption("benchmark-csv-suffix", group=loader.benchmarkGroup,
                                                              help="Add a custom suffix for the statcounters CSV.")
        self.benchmark_ld_preload = loader.addPathOption("benchmark-ld-preload", group=loader.benchmarkGroup,
                                                         help="Preload the given library before running benchmarks")
        self.benchmark_with_debug_kernel = loader.addBoolOption("benchmark-with-debug-kernel", group=loader.benchmarkGroup,
                                                                help="Run the benchmark with a kernel that has assertions enabled.")
        self.benchmark_lazy_binding = loader.addBoolOption("benchmark-lazy-binding", group=loader.benchmarkGroup,
                                                                help="Run the benchmark without setting LD_BIND_NOW.")
        self.benchmark_iterations = loader.addOption("benchmark-iterations", type=int, group=loader.benchmarkGroup,
                                                     help="Override the number of iterations for the benchmark. "
                                                          "Note: not all benchmarks support this option")
        self.benchmark_with_qemu = loader.addBoolOption("benchmark-with-qemu", group=loader.benchmarkGroup,
                                                         help="Run the benchmarks on QEMU instead of the FPGA (only useful to collect instruction counts or test the benchmarks)")
        self.shallow_clone = loader.addBoolOption("shallow-clone", default=True,
            help="Perform a shallow `git clone` when cloning new projects. This can save a lot of time for large"
            "repositories such as FreeBSD or LLVM. Use `git fetch --unshallow` to convert to a non-shallow clone")

        self.targets = None  # type: typing.Optional[typing.List[str]]
        self.FS = None  # type: FileSystemUtils
        self.__optionalProperties = []

    def load(self):
        self.loader.load()
        if self.print_targets_only:
            self.pretend = True
        if self.debug_output:
            self.verbose = True
        self.targets = self.loader.targets
        from ..filesystemutils import FileSystemUtils
        # If there is no clang, default to /usr/bin/cc
        if self.clangCppPath is None and self.clangPlusPlusPath is None and self.clangPath is None:
            self.clangPath = Path("/usr/bin/cc")
            self.clangCppPath = Path("/usr/bin/cpp")
            self.clangPlusPlusPath = Path("/usr/bin/c++")
        if self.clangPath is None or not self.clangPath.exists():
            self.clangPath = Path("/c/compiler/is/missing")
        if self.clangPlusPlusPath is None or not self.clangPlusPlusPath.exists():
            self.clangPlusPlusPath = Path("/c++/compiler/is/missing")
        if self.clangCppPath is None or not self.clangCppPath.exists():
            self.clangCppPath = Path("/cpp/is/missing")
        self.FS = FileSystemUtils(self)

        if self.test_extra_args is None:
            self.test_extra_args = []

        # if we are creating a compilation db in the source that implies creating one in the first place:
        if self.copy_compilation_db_to_source_dir:
            self.create_compilation_db = True

        # flatten the potentially nested list
        if not self.action:
            assert self.default_action is not None
            self.action = [self.default_action]
        else:
            assert isinstance(self.action, list)
            # there doesn't seem to be a flatten() function (and itertools.chain() doesn't work properly)
            real_action = []
            for i in self.action:
                if isinstance(i, list):
                    real_action.extend(i)
                else:
                    real_action.append(i)
            self.action = real_action

        # turn on skip-update if we don't have a working internet connection to avoid errors in git pull
        if not self.skipUpdate and not have_working_internet_connection():
            warningMessage("No internet connection detected, will skip git updates!")
            self.skipUpdate = True

        # CLICOLOR environment variable can confuse ./configure scripts:
        os.unsetenv("CLICOLOR")
        if "CLICOLOR" in os.environ:
            del os.environ["CLICOLOR"]

    def _initializeDerivedPaths(self):
        # Set CHERI_BITS variable to allow e.g. { cheribsd": { "install-directory": "~/rootfs${CHERI_BITS}" } }
        os.environ["CHERI_BITS"] = self.cheriBitsStr
        os.environ["CHERI_CAPTABLE_ABI"] = self.cheri_cap_table_abi
        self.sysrootArchiveName = "cheri-sysroot" + self.cheri_bits_and_abi_str + ".tar.gz"

    @property
    def dollarPathWithOtherTools(self) -> str:
        return str(self.otherToolsDir / "bin") + ":" + os.getenv("PATH")

    @property
    def makeJFlag(self):
        return "-j" + str(self.makeJobs)

    @property
    def cheriBitsStr(self):
        return str(self.cheriBits)

    @property
    def cheri_bits_and_abi_str(self):
        result = str(self.cheriBits)
        if self.cheri_cap_table_abi != self.DEFAULT_CAP_TABLE_ABI:
            result += "-" + str(self.cheri_cap_table_abi)
        if self.subobject_bounds is not None and self.subobject_bounds != self.DEFAULT_SUBOBJECT_BOUNDS:
            result += "-" + str(self.subobject_bounds)
            # TODO: this suffix should not be added. However, it's useful for me right now...
            if not self.subobject_debug:
                result += "-subobject-nodebug"
        if self.mips_float_abi == MipsFloatAbi.HARD:
            result += "-hardfloat"
        return result

    @property
    def sdkDirectoryName(self):
        return "sdk"

    @property
    def sdkBinDir(self):
        return self.sdkDir / "bin"

    @property
    def qemu_bindir(self):
        return self.sdkBinDir

    @property
    def cheriSysrootDir(self):
        return self.sdkDir / ("sysroot" + self.cheri_bits_and_abi_str)

    def get_sysroot_path(self, cross_compile_target: CrossCompileTarget, use_hybrid_sysroot=False):
        if cross_compile_target.is_cheri_purecap():
            return self.cheriSysrootDir
        if cross_compile_target.is_mips():
            if use_hybrid_sysroot or self.use_hybrid_sysroot_for_mips:
                return self.cheriSysrootDir
            if self.mips_float_abi == MipsFloatAbi.HARD:
                return self.sdkDir / "sysroot-mipshf"
            return self.sdkDir / "sysroot-mips"
        elif cross_compile_target.is_riscv():
            return self.sdkDir / "sysroot-riscv"
        elif cross_compile_target.is_x86_64():
            return self.sdkDir / "sysroot-x86_64"
        else:
            assert False, "Invalid cross_compile_target: " + str(cross_compile_target)

    def _ensureRequiredPropertiesSet(self) -> bool:
        for key in self.__dict__.keys():
            if key in self.__optionalProperties:
                continue
            # don't do the descriptor stuff:
            value = object.__getattribute__(self, key)
            if value is None:
                raise RuntimeError("Required property " + key + " is not set!")
        return True

    # FIXME: not sure why this is needed
    def __getattribute__(self, item):
        v = object.__getattribute__(self, item)
        if hasattr(v, '__get__'):
            return v.__get__(self, self.__class__)
        return v

    def getOptionsJSON(self):
        jsonDict = OrderedDict()
        for v in self.loader.options.values():
            # noinspection PyProtectedMember
            jsonDict[v.fullOptionName] = v.__get__(self, v._owningClass if v._owningClass else self)
        return json.dumps(jsonDict, sort_keys=True, cls=MyJsonEncoder, indent=4)

    @classmethod
    def get_user_name(cls) -> str:
        try:
            return getpass.getuser()
        except KeyError:
            # Jenkins runs docker slaves with the jenkins UID which will not have a mapping:
            if os.getenv("JENKINS_NODE_COOKIE"):
                return "jenkins"
            else:
                result = str(os.getgid())
                warningMessage("Could not get group name for GID", result)
                return result

    @classmethod
    def get_group_name(cls) -> str:
        try:
            return grp.getgrgid(os.getgid()).gr_name
        except KeyError:
            # Jenkins runs docker slaves with the jenkins UID which will not have a mapping:
            if os.getenv("JENKINS_NODE_COOKIE"):
                return "jenkins"
            else:
                result = str(os.getgid())
                warningMessage("Could not get group name for GID", result)
                return result

    def debug_message(self, *args, **kwargs):
        if self.debug_output:
            statusUpdate(*args, **kwargs)
