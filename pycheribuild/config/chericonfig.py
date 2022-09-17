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
import collections
import getpass
import grp
import os
import re
import shutil
import typing
from enum import Enum
from pathlib import Path
from typing import Optional

from .config_loader_base import ConfigLoaderBase
from .computed_default_value import ComputedDefaultValue
from ..processutils import latest_system_clang_tool, run_command
from ..utils import (cached_property, ConfigBase, DoNotUseInIfStmt, have_working_internet_connection, status_update,
                     warning_message)


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

    @property
    def is_release(self):
        return self in (BuildType.RELEASE, BuildType.RELWITHDEBINFO)

    @property
    def is_debug(self):
        return self is BuildType.DEBUG

    def to_meson_args(self) -> dict:
        if self is BuildType.DEFAULT:
            return dict()  # Note: Meson default value is debug
        if self is BuildType.DEBUG:
            return {"buildtype": "debug"}  # -O0 -g
        elif self is BuildType.RELEASE:
            return {"buildtype": "release"}  # -O3 no debug
        elif self is BuildType.RELWITHDEBINFO:
            return {"buildtype": "debugoptimized"}  # -O2 -g
        elif self is BuildType.MINSIZEREL:
            return {"buildtype": "debugoptimized"}  # -Os no debug
        elif self is BuildType.MINSIZERELWITHDEBINFO:
            return {"buildtype": "custom", "optimization": "s", "debug": True}  # -Os -g
        else:
            raise NotImplementedError()


supported_build_type_strings: "list[str]" = [str(t.value) for t in BuildType]


class Linkage(Enum):
    DEFAULT = "default"
    STATIC = "static"
    DYNAMIC = "dynamic"


class MipsFloatAbi(Enum):
    SOFT = ("mips64", "-msoft-float")
    HARD = ("mips64hf", "-mhard-float")

    def freebsd_target_arch(self) -> str:
        return self.value[0]

    def clang_float_flag(self) -> str:
        return self.value[1]


class AArch64FloatSimdOptions(Enum):
    DEFAULT = ("", "")
    NOSIMD = ("-nosimd", "+nosimd")
    SOFT = ("-softfp", "+nofp+nosimd")
    SOFT_SIMD = ("-softfp-with-simd", "+nofp")  # TODO: does it make sense to have this?

    def config_suffix(self) -> str:
        return self.value[0]

    def clang_march_flag(self) -> str:
        return self.value[1]


def _default_arm_none_eabi_prefix(c: "CheriConfig", _):
    # see if the local install exists:
    default_path = c.output_root / c.local_arm_none_eabi_toolchain_relpath
    if (default_path / "bin/arm-none-eabi-gcc").exists():
        return str(default_path / "bin/arm-none-eabi-")
    elif Path("/Applications/ARM/bin/arm-none-eabi-gcc").exists():
        return "/Applications/ARM/bin/arm-none-eabi-"
    else:
        in_path = shutil.which("arm-none-eabi-gcc")
        if in_path is not None:
            return str(Path(in_path).parent / "arm-none-eabi-")
        # Otherwise suggest the non-existent local installation
        return str(default_path / "bin/arm-none-eabi-")


def _skip_dependency_filter_arg(values: "list[str]") -> "list[re.Pattern]":
    result = [re.compile(item) for item in values]
    return result


class CheriConfig(ConfigBase):
    def __init__(self, loader, action_class) -> None:
        super().__init__(pretend=DoNotUseInIfStmt(), verbose=DoNotUseInIfStmt(), quiet=DoNotUseInIfStmt(),
                         force=DoNotUseInIfStmt())
        self._cached_deps = collections.defaultdict(dict)

        assert isinstance(loader, ConfigLoaderBase)
        loader._cheri_config = self
        self.loader = loader
        self.pretend = loader.add_commandline_only_bool_option("pretend", "p",
                                                               help="Only print the commands instead of running them")

        # add the actions:
        self.action = loader.add_option("action", default=[], action="append", type=action_class, help_hidden=True,
                                        help="The action to perform by cheribuild", group=loader.action_group)
        self.default_action = None
        # Add aliases (e.g. --test = --action=test):
        for action in action_class:
            if action.altname:
                loader.action_group.add_argument(action.option_name, action.altname, help=action.help_message,
                                                 dest="action", action="append_const", const=action.actions)
            else:
                loader.action_group.add_argument(action.option_name, help=action.help_message, dest="action",
                                                 action="append_const", const=action.actions)
        self.print_targets_only = loader.add_commandline_only_bool_option(
            "print-targets-only", help_hidden=False, group=loader.action_group,
            help="Don't run the build but instead only print the targets that would be executed")

        self.clang_path = loader.add_path_option("clang-path", shortname="-cc-path",
                                                 default=lambda c, _: latest_system_clang_tool(c, "clang", "cc"),
                                                 group=loader.path_group,
                                                 help="The C compiler to use for host binaries (must be compatible "
                                                      "with Clang >= 3.7)")
        self.clang_plusplus_path = loader.add_path_option("clang++-path", shortname="-c++-path",
                                                          default=lambda c, _: latest_system_clang_tool(c, "clang++",
                                                                                                        "c++"),
                                                          group=loader.path_group,
                                                          help="The C++ compiler to use for host binaries (must be "
                                                               "compatible with Clang >= 3.7)")
        self.clang_cpp_path = loader.add_path_option("clang-cpp-path", shortname="-cpp-path",
                                                     default=lambda c, _: latest_system_clang_tool(c, "clang-cpp",
                                                                                                   "cpp"),
                                                     group=loader.path_group,
                                                     help="The C preprocessor to use for host binaries (must be "
                                                          "compatible with Clang >= 3.7)")

        self.pass_dash_k_to_make = loader.add_commandline_only_bool_option(
            "pass-k-to-make", "k", help="Pass the -k flag to make to continue after the first error")
        self.with_libstatcounters = loader.add_bool_option("with-libstatcounters",
                                                           group=loader.cross_compile_options_group,
                                                           help="Link cross compiled CHERI project with "
                                                                "libstatcounters.")
        self.skip_world = loader.add_bool_option(
            "skip-world", "-skip-buildworld", group=loader.freebsd_group,
            help="Skip the buildworld-related steps when building FreeBSD or CheriBSD")
        self.skip_kernel = loader.add_bool_option(
            "skip-kernel", "-skip-buildkernel", group=loader.freebsd_group,
            help="Skip the buildkernel step when building FreeBSD or CheriBSD")
        self.freebsd_kernconf = loader.add_commandline_only_option(
            "kernel-config", "-kernconf", group=loader.freebsd_group, help_hidden=True,
            help="Override the default FreeBSD/CheriBSD kernel config.")
        self.freebsd_subdir = loader.add_commandline_only_option(
            "freebsd-subdir", "-subdir", group=loader.freebsd_group, type=list, metavar="SUBDIRS",
            help="Only build subdirs SUBDIRS of FreeBSD/CheriBSD instead of the full tree. Useful for quickly "
                 "rebuilding individual programs/libraries. If more than one dir is passed they will be processed in "
                 "order. Note: This will break if not all dependencies have been built.")
        self.freebsd_host_tools_only = loader.add_commandline_only_bool_option(
            "freebsd-host-tools-only", help_hidden=True, group=loader.freebsd_group,
            help="Stop the FreeBSD/CheriBSD build after the host tools have been built")

        self.buildenv = loader.add_commandline_only_bool_option(
            "buildenv", group=loader.freebsd_group,
            help="Open a shell with the right environment for building the project. Currently only works for "
                 "FreeBSD/CheriBSD")
        self.libcompat_buildenv = loader.add_commandline_only_bool_option(
            "libcompat-buildenv", "-libcheri-buildenv", group=loader.freebsd_group,
            help="Open a shell with the right environment for building compat libraries.")

        self.cheri_cap_table_abi = loader.add_option("cap-table-abi", help_hidden=True,
                                                     choices=("pcrel", "plt", "fn-desc"),
                                                     help="The ABI to use for cap-table mode")
        self.cross_target_suffix = loader.add_option("cross-target-suffix", help_hidden=True, default="",
                                                     help="Add a suffix to the cross build and install directories.")
        self.allow_running_as_root = loader.add_bool_option("allow-running-as-root", help_hidden=True, default=False,
                                                            help="Allow running cheribuild as root (not recommended!)")
        # Attributes for code completion:
        self.verbose = None  # type: Optional[bool]
        self.debug_output = loader.add_commandline_only_bool_option("debug-output", "vv",
                                                                    help="Extremely verbose output")
        self.quiet: "Optional[bool] " = None
        self.clean: "Optional[bool] " = None
        self.force: "Optional[bool] " = None
        self.write_logfile: "Optional[bool] " = None
        self.skip_update: "Optional[bool] " = None
        self.skip_clone: "Optional[bool] " = None
        self.confirm_clone: "Optional[bool] " = None
        self.skip_configure: "Optional[bool] " = None
        self.force_configure: "Optional[bool] " = None
        self.force_update: "Optional[bool] " = None
        self.mips_float_abi = loader.add_option("mips-float-abi", default=MipsFloatAbi.SOFT, type=MipsFloatAbi,
                                                group=loader.cross_compile_options_group,
                                                help="The floating point ABI to use for building MIPS+CHERI programs")
        self.aarch64_fp_and_simd_options = loader.add_option(
            "aarch64-fp-and-simd-options", default=AArch64FloatSimdOptions.DEFAULT, type=AArch64FloatSimdOptions,
            group=loader.cross_compile_options_group,
            help="The floating point/SIMD mode to use for building AArch64 programs")
        self.crosscompile_linkage = loader.add_option("cross-compile-linkage", default=Linkage.DEFAULT, type=Linkage,
                                                      group=loader.cross_compile_options_group,
                                                      enum_choices=(Linkage.DEFAULT, Linkage.DYNAMIC, Linkage.STATIC),
                                                      help="Whether to link cross-compile projects static or dynamic "
                                                           "by default")
        self.csetbounds_stats = loader.add_bool_option("collect-csetbounds-stats",
                                                       group=loader.cross_compile_options_group, help_hidden=True,
                                                       help="Whether to log CSetBounds statistics in csv format")
        self.subobject_bounds = loader.add_option("subobject-bounds", type=str,
                                                  group=loader.cross_compile_options_group,
                                                  choices=(
                                                      "conservative", "subobject-safe", "aggressive", "very-aggressive",
                                                      "everywhere-unsafe"),
                                                  help="Whether to add additional CSetBounds to subobject "
                                                       "references/&-operator")
        self.use_cheri_ubsan = loader.add_bool_option(
            "use-cheri-ubsan", group=loader.cross_compile_options_group,
            help="Add compiler flags to detect certain undefined CHERI behaviour at runtime")
        self.use_cheri_ubsan_runtime = loader.add_bool_option(
            "use-cheri-ubsan-runtime", group=loader.cross_compile_options_group, default=False,
            help="Use the UBSan runtime to provide more detailed information on undefined CHERI behaviour."
                 "If false (the default) the compiler will generate a trap instruction instead.")
        self.subobject_debug = loader.add_bool_option("subobject-debug", group=loader.cross_compile_options_group,
                                                      default=True, help_hidden=False,
                                                      help="Clear software permission bit 2 when subobject bounds "
                                                           "reduced size"
                                                           " (Note: this should be turned off for benchmarks!)")

        self.clang_colour_diags = loader.add_bool_option("clang-colour-diags", "-clang-color-diags", default=True,
                                                         help="Force CHERI clang to emit coloured diagnostics")
        self.use_sdk_clang_for_native_xbuild = loader.add_bool_option("use-sdk-clang-for-native-xbuild",
                                                                      group=loader.cross_compile_options_group,
                                                                      help="Compile cross-compile project with CHERI "
                                                                           "clang from the SDK instead of host "
                                                                           "compiler")

        self.configure_only = loader.add_bool_option("configure-only",
                                                     help="Only run the configure step (skip build and install)")
        self.skip_install = loader.add_bool_option("skip-install", help="Skip the install step (only do the build)")
        self.skip_build = loader.add_bool_option("skip-build", help="Skip the build step (only do the install)")
        self.skip_sdk = loader.add_bool_option(
            "skip-sdk", group=loader.dependencies_group,
            help="When building with --include-dependencies ignore the SDK dependencies. Saves a lot of time "
                 "when building libc++, etc. with dependencies but the sdk is already up-to-date. "
                 "This is like --no-include-toolchain-depedencies but also skips the target that builds the sysroot.")
        self.skip_dependency_filters = loader.add_option(
            "skip-dependency-filter", group=loader.dependencies_group, action="append", default=[],
            type=_skip_dependency_filter_arg, metavar="REGEX",
            help="A regular expression to match against to target names that should be skipped when using"
                 "--include-dependency. Can be passed multiple times to add more patterns.")  # type: list[re.Pattern]
        self.trap_on_unrepresentable = loader.add_bool_option(
            "trap-on-unrepresentable", default=False, group=loader.run_group,
            help="Raise a CHERI exception when capabilities become unreprestable instead of detagging. Useful for "
                 "debugging, but deviates from the spec, and therefore off by default.")
        self.debugger_on_cheri_trap = loader.add_bool_option(
            "qemu-gdb-break-on-cheri-trap", default=False, group=loader.run_group,
            help="Drop into GDB attached to QEMU when a CHERI exception is triggered (QEMU only).")
        self.qemu_debug_program = loader.add_option(
            "qemu-gdb-debug-userspace-program", group=loader.run_group,
            help="Print the command to debug the following userspace program in GDB attaced to QEMU")
        self.include_dependencies = None  # type: Optional[bool]
        self.include_toolchain_dependencies = True
        self.enable_hybrid_targets = False
        self.only_dependencies = loader.add_bool_option("only-dependencies",
                                                        help="Only build dependencies of targets, "
                                                             "not the targets themselves")
        self.start_with = None  # type: Optional[str]
        self.start_after = None  # type: Optional[str]
        self.make_without_nice = None  # type: Optional[bool]

        self.mips_cheri_bits = 128  # Backwards compat
        self.make_jobs = None  # type: Optional[int]

        self.source_root = None  # type: Optional[Path]
        self.output_root = None  # type: Optional[Path]
        self.build_root = None  # type: Optional[Path]
        # Path to kernel/disk images (this is the same as output_root by default but different in Jenkins)
        self.cheribsd_image_root = None  # type: Optional[Path]
        self.cheri_sdk_dir = None  # type: Optional[Path]
        self.morello_sdk_dir = None  # type: Optional[Path]
        self.other_tools_dir = None  # type: Optional[Path]
        self.sysroot_output_root = None  # type: Optional[Path]
        self.docker = loader.add_bool_option("docker", help="Run the build inside a docker container",
                                             group=loader.docker_group)
        self.docker_container = loader.add_option("docker-container", help="Name of the docker container to use",
                                                  default="ctsrd/cheribuild-docker", group=loader.docker_group)
        self.docker_reuse_container = loader.add_bool_option("docker-reuse-container", group=loader.docker_group,
                                                             help="Attach to the same container again (note: "
                                                                  "docker-container option must be an id rather than "
                                                                  "a container name")

        # compilation db options:
        self.create_compilation_db = loader.add_commandline_only_bool_option(
            "compilation-db", "-cdb", help="Create a compile_commands.json file in the build dir "
                                           "(requires Bear for non-CMake projects)")
        self.copy_compilation_db_to_source_dir = None  # False for jenkins, an option for cheribuild
        self.generate_cmakelists = False  # False for jenkins, an option for cheribuild

        # Run QEMU options
        self.wait_for_debugger = loader.add_bool_option("wait-for-debugger", group=loader.run_group,
                                                        help="Start QEMU in the 'wait for a debugger' state when"
                                                             "launching CheriBSD,FreeBSD, etc.")

        self.debugger_in_tmux_pane = loader.add_bool_option("debugger-in-tmux-pane", group=loader.run_group,
                                                            help="Start Qemu and gdb in another tmux split")

        self.gdb_random_port = loader.add_bool_option("gdb-random-port", default=True, group=loader.run_group,
                                                      help="Wait for gdb using a random port")

        self.run_under_gdb = loader.add_bool_option("run-under-gdb", group=loader.run_group,
                                                    help="Run tests/benchmarks under GDB. Note: currently most "
                                                         "targets ignore this flag.")

        # Test options:
        self._test_ssh_key = loader.add_path_option("test-ssh-key", default=None, group=loader.tests_group,
                                                    help="The SSH key to used to connect to the QEMU instance when "
                                                         "running tests on CheriBSD. If not specified a key will be "
                                                         "generated in the build-root directory on-demand.")
        self.use_minimal_benchmark_kernel = loader.add_bool_option("use-minimal-benchmark-kernel",
                                                                   help="Use a CHERI BENCHMARK version of the "
                                                                        "cheribsd-mfs-root-kernel (without "
                                                                        "INVARIATES) for the "
                                                                        "run-minimal target and for tests. This can "
                                                                        "speed up longer running tests. This is the "
                                                                        "default for "
                                                                        "PostgreSQL and libc++ tests (passing "
                                                                        "use-minimal-benchmark-kernel can force these "
                                                                        "tests to use "
                                                                        "an INVARIANTS kernel).",
                                                                   group=loader.tests_group, default=False)

        self.test_extra_args = loader.add_commandline_only_option("test-extra-args", group=loader.tests_group,
                                                                  type=list,
                                                                  metavar="ARGS",
                                                                  help="Additional flags to pass to the test script "
                                                                       "in --test")
        self.tests_interact = loader.add_commandline_only_bool_option("interact-after-tests", group=loader.tests_group,
                                                                      help="Interact with the CheriBSD instance after "
                                                                           "running the tests on QEMU (only for "
                                                                           "--test)")
        self.tests_env_only = loader.add_commandline_only_bool_option("test-environment-only", group=loader.tests_group,
                                                                      help="Don't actually run the tests. Instead "
                                                                           "setup a QEMU instance with the right "
                                                                           "paths set up.")
        self.test_ld_preload = loader.add_path_option("test-ld-preload", group=loader.tests_group,
                                                      help="Preload the given library before running tests")

        self.benchmark_fpga_extra_args = loader.add_commandline_only_option(
            "benchmark-fpga-extra-args", group=loader.benchmark_group, type=list, metavar="ARGS",
            help="Extra options for the FPGA management script")
        self.benchmark_clean_boot = loader.add_bool_option("benchmark-clean-boot", group=loader.benchmark_group,
                                                           help="Reboot the FPGA with a new bitfile and kernel before "
                                                                "running benchmarks. "
                                                                "If not set, assume the FPGA is running.")
        self.benchmark_extra_args = loader.add_commandline_only_option(
            "benchmark-extra-args", group=loader.benchmark_group, type=list,
            metavar="ARGS", help="Additional flags to pass to the program executed in --benchmark")
        self.benchmark_ssh_host = loader.add_option(
            "benchmark-ssh-host", group=loader.benchmark_group, type=str,
            default="cheri-fpga", help="The SSH hostname/IP for the benchmark FPGA")
        self.benchmark_statcounters_suffix = loader.add_option(
            "benchmark-csv-suffix", group=loader.benchmark_group,
            help="Add a custom suffix for the statcounters CSV.")
        self.benchmark_ld_preload = loader.add_path_option(
            "benchmark-ld-preload", group=loader.benchmark_group,
            help="Preload the given library before running benchmarks")
        self.benchmark_with_debug_kernel = loader.add_bool_option(
            "benchmark-with-debug-kernel", group=loader.benchmark_group,
            help="Run the benchmark with a kernel that has assertions enabled.")
        self.benchmark_lazy_binding = loader.add_bool_option(
            "benchmark-lazy-binding", group=loader.benchmark_group,
            help="Run the benchmark without setting LD_BIND_NOW.")
        self.benchmark_iterations = loader.add_option(
            "benchmark-iterations", type=int, group=loader.benchmark_group,
            help="Override the number of iterations for the benchmark. "
                 "Note: not all benchmarks support this option")
        self.benchmark_with_qemu = loader.add_bool_option(
            "benchmark-with-qemu", group=loader.benchmark_group,
            help="Run the benchmarks on QEMU instead of the FPGA (only useful to collect instruction counts or test "
                 "the benchmarks)")
        self.shallow_clone = loader.add_bool_option(
            "shallow-clone", default=True,
            help="Perform a shallow `git clone` when cloning new projects. This can save a lot of time for large"
                 "repositories such as FreeBSD or LLVM. Use `git fetch --unshallow` to convert to a non-shallow clone")

        self.fpga_custom_env_setup_script = loader.add_path_option(
            "beri-fpga-env-setup-script", group=loader.path_group,
            help="Custom script to source to setup PATH and quartus, default to using cheri-cpu/cheri/setup.sh")

        self.local_arm_none_eabi_toolchain_relpath = Path("arm-none-eabi-sdk")
        self.arm_none_eabi_toolchain_prefix = loader.add_option(
            "arm-none-eabi-prefix", default=ComputedDefaultValue(_default_arm_none_eabi_prefix, ""),
            group=loader.path_group,
            help="Prefix for arm-none-eabi-gcc binaries (e.g. /usr/bin/arm-none-eabi-). Available at"
                 "https://developer.arm.com/tools-and-software/open-source-software/"
                 "developer-tools/gnu-toolchain/gnu-rm/downloads")

        self.build_morello_firmware_from_source = loader.add_bool_option(
            "build-morello-firmware-from-source", help_hidden=False,
            help="Build the firmware from source instead of downloading the latest release.")

        self.list_kernels = loader.add_bool_option("list-kernels", group=loader.action_group,
                                                   help="List available kernel configs to run and exit")

        self.remote_morello_board = loader.add_option(
            "remote-morello-board", help="SSH hostname of a Morello board. When set, some projects will run their "
                                         "test suites on the remote board instead of QEMU.")

        self.targets = None  # type: typing.Optional[typing.List[str]]
        self.__optional_properties = ["internet_connection_last_checked_at", "start_after", "start_with"]

    def load(self) -> None:
        self.loader.load()
        if self.print_targets_only:
            self.pretend = True
        if self.debug_output:
            self.verbose = True
        self.targets = self.loader.targets()
        assert self.clang_path is not None, "clang_path was None!"
        if not self.clang_path.exists():
            self.clang_path = Path("/c/compiler/is/missing")
        if not self.clang_plusplus_path.exists():
            self.clang_plusplus_path = Path("/c++/compiler/is/missing")
        if not self.clang_cpp_path.exists():
            self.clang_cpp_path = Path("/cpp/is/missing")

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
        if not self.skip_update and not have_working_internet_connection(self):
            warning_message("No internet connection detected, will skip git updates!")
            self.skip_update = True

        # CLICOLOR environment variable can confuse ./configure scripts:
        os.unsetenv("CLICOLOR")
        if "CLICOLOR" in os.environ:
            del os.environ["CLICOLOR"]

        # Check that the skip_dependency_filters arguments are all valid regular expressions. We do it now since
        # otherwise the validation is delayed until the first time the object is used.
        assert isinstance(self.skip_dependency_filters, list)

    @cached_property
    def _other_tools_path_prefix(self) -> str:
        return str(self.other_tools_dir / "bin") + ":"

    @property
    def dollar_path_with_other_tools(self) -> str:
        old_path = os.getenv("PATH", "")
        new_prefix = self._other_tools_path_prefix
        if old_path.startswith(new_prefix):
            return old_path  # $PATH already starts with other_tools, don't add it again
        return new_prefix + old_path

    @property
    def make_j_flag(self):
        return "-j" + str(self.make_jobs)

    @property
    def mips_cheri_bits_str(self):
        return str(self.mips_cheri_bits)

    @property
    def default_cheri_sdk_directory_name(self) -> str:
        return "sdk"

    @property
    def default_morello_sdk_directory_name(self) -> str:
        return "morello-sdk"

    @property
    def cheri_sdk_bindir(self):
        return self.cheri_sdk_dir / "bin"

    @property
    def morello_sdk_bindir(self):
        return self.morello_sdk_dir / "bin"

    @property
    def qemu_bindir(self):
        return self.cheri_sdk_bindir

    @property
    def test_ssh_key(self) -> Path:
        if self._test_ssh_key is not None:
            return self._test_ssh_key
        default_test_ssh_key_path = self.build_root / "insecure_test_ssh_key.pub"
        if not default_test_ssh_key_path.exists():
            status_update("Generating SSH key for testing:", default_test_ssh_key_path)
            run_command(["ssh-keygen", "-t", "ed25519", "-N", "", "-f", default_test_ssh_key_path.with_suffix(""),
                         "-C", "Test SSH key for cheribuild"], config=self)
        return default_test_ssh_key_path

    def _ensure_required_properties_set(self) -> bool:
        for key in self.__dict__.keys():
            if key in self.__optional_properties:
                continue
            # don't do the descriptor stuff:
            value = object.__getattribute__(self, key)
            if value is None:
                raise RuntimeError("Required property " + key + " is not set!")
        assert self.cheri_sdk_dir.is_absolute(), self.cheri_sdk_dir
        assert self.other_tools_dir.is_absolute(), self.other_tools_dir
        assert self.output_root.is_absolute(), self.output_root
        assert self.source_root.is_absolute(), self.source_root
        assert self.build_root.is_absolute(), self.build_root
        return True

    def should_skip_dependency(self, target_name: str, requested_by: str) -> bool:
        filters = self.skip_dependency_filters
        for regex in filters:
            if regex.fullmatch(target_name):
                if self.debug_output:
                    print("Not adding", target_name, "dependency for", requested_by, "due to filter", regex)
                return True
        return False

    # FIXME: not sure why this is needed
    def __getattribute__(self, item) -> "typing.Any":
        v = object.__getattribute__(self, item)
        if hasattr(v, '__get__'):
            # noinspection PyCallingNonCallable
            return v.__get__(self, self.__class__)  # pytype: disable=attribute-error
        return v

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
                warning_message("Could not get group name for GID", result)
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
                warning_message("Could not get group name for GID", result)
                return result

    def debug_message(self, *args, **kwargs) -> None:
        if self.debug_output:
            status_update(*args, **kwargs)
