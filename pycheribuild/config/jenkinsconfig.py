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

import os
import typing
from enum import Enum
from pathlib import Path

from .chericonfig import CheriConfig
from .compilation_targets import CompilationTargets, CrossCompileTarget
from .loader import ConfigLoaderBase
from ..filesystemutils import FileSystemUtils
from ..utils import default_make_jobs_count, fatal_error, OSInfo, warning_message


def default_install_prefix(conf: "JenkinsConfig", _):
    return "/opt/" + conf.targets[0]


def default_jenkins_make_jobs_count(conf: "JenkinsConfig", _):
    if conf.use_all_cores:
        return os.cpu_count()
    return default_make_jobs_count()


class JenkinsAction(Enum):
    BUILD = ("--build", "Run (usually build+install) chosen targets (default)")
    CREATE_TARBALL = ("--create-tarball", "Create an archive of the installed files", "--tarball")
    TEST = ("--test", "Run tests")
    EXTRACT_SDK = ("--extract-sdk", "Extract the SDK archive and then exit")

    # TODO: TEST = ("--test", "Run tests for the passed targets instead of building them", "--run-tests")

    def __init__(self, option_name, help_message, altname=None, actions=None):
        self.option_name = option_name
        self.help_message = help_message
        self.altname = altname
        if not actions:
            actions = [self]
        if actions:
            self.actions = actions


def absolute_path_only(p: str) -> Path:
    expanded = os.path.expanduser(os.path.expandvars(str(p)))
    # print("Expanding env vars in", result, "->", expanded, os.environ)
    result = Path(expanded)
    if not result.is_absolute():
        raise ValueError("Must be an absolute path but was: " + repr(result))
    return result


class JenkinsConfig(CheriConfig):
    def __init__(self, loader: ConfigLoaderBase, available_targets: list):
        super().__init__(loader, action_class=JenkinsAction)
        self.default_action = ""  # error if no action set

        self.cpu = loader.add_commandline_only_option("cpu", default=os.getenv("CPU", "default"),
                                                      help="The target to build the software for (defaults to $CPU).",
                                                      choices=["default", "cheri128", "mips", "hybrid-cheri128",
                                                               "riscv64", "riscv64-hybrid", "riscv64-purecap",
                                                               "native", "x86", "amd64"])  # type: str
        self.workspace = loader.add_commandline_only_option("workspace", default=os.getenv("WORKSPACE"), type=Path,
                                                            help="The root directory for building (defaults to "
                                                                 "$WORKSPACE)")  # type: Path
        self.sdk_archive_name = loader.add_commandline_only_option("sdk-archive", type=str,
                                                                   default=os.getenv("SDK_ARCHIVE"),
                                                                   help="The name of the sdk archive")  # type: str
        self.keep_install_dir = loader.add_commandline_only_bool_option("keep-install-dir",
                                                                        help="Don't delete the install dir prior to "
                                                                             "build")  # type: bool
        self.keep_sdk_dir = loader.add_commandline_only_bool_option("keep-sdk-dir",
                                                                    help="Don't delete existing SDK dir even"
                                                                         " if there is a newer archive")  # type: bool
        self.force_update = loader.add_commandline_only_bool_option("force-update",
                                                                    help="Do the updating (not recommended in "
                                                                         "jenkins!)")  # type: bool
        self.copy_compilation_db_to_source_dir = False
        self.make_without_nice = False

        self.make_jobs = loader.add_commandline_only_option("make-jobs", "j", type=int,
                                                            default=default_jenkins_make_jobs_count,
                                                            help="Number of jobs to use for compiling")
        self.use_all_cores = loader.add_commandline_only_bool_option("use-all-cores",
                                                                     help="Use all available cores for building ("
                                                                          "Note: Should only be used for LLVM or "
                                                                          "short-running jobs!)")
        self.installation_prefix = loader.add_commandline_only_option(
            "install-prefix", type=absolute_path_only, default=default_install_prefix,
            help="The install prefix for cross compiled projects (the path in the install image)")  # type: Path
        self.without_sdk = loader.add_commandline_only_bool_option(
            "without-sdk", help="Don't use the CHERI SDK -> only /usr (for native builds)")
        self.strip_elf_files = loader.add_commandline_only_bool_option(
            "strip-elf-files", help="Strip ELF files before creating the tarball", default=True)
        self.cheri_sdk_path = loader.add_commandline_only_option("cheri-sdk-path", default=None, type=Path,
                                                                 help="Override the path to the CHERI SDK (default is "
                                                                      "$WORKSPACE/cherisdk)")  # type: Path
        self.extract_compiler_only = loader.add_commandline_only_bool_option("extract-compiler-only",
                                                                             help="Don't attempt to extract the "
                                                                                  "CheriBSD sysroot")
        self.tarball_name = loader.add_commandline_only_option(
            "tarball-name", default=lambda conf, cls: conf.targets[0] + "-" + conf.cpu + ".tar.xz")

        self.default_output_path = "tarball"
        self.output_path = loader.add_commandline_only_option("output-path", default=self.default_output_path,
                                                              help="Path for the output (relative to $WORKSPACE)")

        # self.strip_install_prefix_from_archive = loader.add_commandline_only_bool_option(
        # "strip-install-prefix-from-archive",
        #    help="Only put the files inside the install prefix into the tarball (stripping the leading
        #    directories)")  # type: bool
        self.skip_update = True
        self.skip_clone = True
        self.verbose = True
        self.quiet = False
        self.clean = loader.add_commandline_only_bool_option("clean", default=True,
                                                             help="Clean build directory before building")
        self.force = True  # no user input in jenkins
        self.write_logfile = False  # jenkins stores the output anyway
        self.skip_configure = loader.add_bool_option("skip-configure", help="Skip the configure step")
        self.force_configure = True
        self.include_dependencies = False
        loader.finalize_options(available_targets)
        self.FS = FileSystemUtils(self)

    @property
    def cheri_sdk_directory_name(self):
        return "cherisdk"

    @property
    def sdk_cpu(self) -> str:
        sdk_cpu = os.getenv("SDK_CPU")
        if not sdk_cpu:
            if self.cpu in ("cheri128", "mips"):
                return self.cpu
            if self.cpu == "hybrid-cheri128":
                return "cheri128"
            else:
                warning_message("SDK_CPU variable not set, cannot infer the name of the SDK archive")
                return "unknown-cpu"
        return sdk_cpu

    @property
    def sdk_archive_path(self):
        if self.sdk_archive_name is None:
            self.sdk_archive_name = "{}-{}-sdk.tar.xz".format(self.sdk_cpu, self.cheri_sdk_isa_name)
        assert isinstance(self.sdk_archive_name, str)
        return self.workspace / self.sdk_archive_name

    @property
    def cheri_sdk_isa_name(self):
        guessed_abi_suffix = ""
        if self.cheri_cap_table_abi:
            guessed_abi_suffix = "cap-table-" + self.cheri_cap_table_abi
        return os.getenv("ISA", guessed_abi_suffix)

    @property
    def qemu_bindir(self):
        for i in self.cheri_sdk_bindir.glob("qemu-system-*"):
            if self.verbose:
                print("Found QEMU binary", i, "in SDK dir -> using that for QEMU binaries")
            # If one qemu-system-foo exists in the cheri_sdk_bindir use that instead of $WORKSPACE/qemu-<OS>
            return self.cheri_sdk_bindir
        if OSInfo.IS_LINUX:
            os_suffix = "linux"
        elif OSInfo.IS_FREEBSD:
            os_suffix = "freebsd"
        elif OSInfo.IS_MAC:
            os_suffix = "mac"
        else:
            os_suffix = "unknown-os"
        return self.workspace / ("qemu-" + os_suffix) / "bin"

    def load(self):
        super().load()

        if not self.workspace or not self.workspace.is_dir():
            fatal_error("WORKSPACE is not set to a valid directory:", self.workspace)
        self.source_root = self.workspace
        self.build_root = self.workspace
        if self.output_path != self.default_output_path:
            if not self.keep_install_dir:
                print("Not cleaning non-default output path", self.workspace / self.output_path)
            self.keep_install_dir = True
        self.output_root = self.workspace / self.output_path

        # expect the CheriBSD disk images in the workspace root
        self.cheribsd_image_root = self.workspace

        self.other_tools_dir = self.workspace / "bootstrap"
        # check for ctsrd/cheri-sdk-{cheri128,mips} docker image
        if self.cheri_sdk_path is not None:
            self.cheri_sdk_dir = self.cheri_sdk_path
        elif Path("/cheri-sdk/bin/cheri-unknown-freebsd-clang").exists():
            self.cheri_sdk_dir = Path("/cheri-sdk")
        else:
            self.cheri_sdk_dir = self.workspace / self.cheri_sdk_directory_name
        self.preferred_xtarget = None  # type: typing.Optional[CrossCompileTarget]
        if self.cpu == "default":
            self.preferred_xtarget = None
        elif self.cpu == "cheri128":
            self.preferred_xtarget = CompilationTargets.CHERIBSD_MIPS_PURECAP
        elif self.cpu in ("mips", "hybrid-cheri128"):  # MIPS with CHERI memcpy
            if self.cpu == "mips" and self.sdk_cpu == "cheri128":
                self.cpu = "hybrid-" + self.sdk_cpu
            if self.cpu == "hybrid-cheri128":
                self.preferred_xtarget = CompilationTargets.CHERIBSD_MIPS_HYBRID
            else:
                assert self.cpu == "mips"
                self.preferred_xtarget = CompilationTargets.CHERIBSD_MIPS_NO_CHERI
        elif self.cpu == "riscv64":
            self.preferred_xtarget = CompilationTargets.CHERIBSD_RISCV_NO_CHERI
        elif self.cpu == "riscv64-hybrid":
            self.preferred_xtarget = CompilationTargets.CHERIBSD_RISCV_HYBRID
        elif self.cpu == "riscv64-purecap":
            self.preferred_xtarget = CompilationTargets.CHERIBSD_RISCV_PURECAP
        elif self.cpu in ("x86", "x86_64", "amd64", "host", "native"):
            self.preferred_xtarget = CompilationTargets.NATIVE
        else:
            fatal_error("CPU is not set to a valid value:", self.cpu)

        if self.force_update:
            self.skip_update = False
            self.skip_clone = False

        if self.without_sdk:
            self.cheri_sdk_dir = self.output_root / str(self.installation_prefix).strip('/')
            # allow overriding the clang/clang++ paths with HOST_CC/HOST_CXX
            self.clang_path = Path(os.getenv("HOST_CC", self.clang_path))
            self.clang_plusplus_path = Path(os.getenv("HOST_CXX", self.clang_plusplus_path))
            self.clang_cpp_path = Path(os.getenv("HOST_CPP", self.clang_cpp_path))
            if not self.clang_path.exists():
                fatal_error("C compiler", self.clang_path, "does not exit. Pass --clang-path or set $HOST_CC")
            if not self.clang_plusplus_path.exists():
                fatal_error("C++ compiler", self.clang_plusplus_path,
                            "does not exit. Pass --clang++-path or set $HOST_CXX")
            if not self.clang_cpp_path.exists():
                fatal_error("C pre-processor", self.clang_cpp_path,
                            "does not exit. Pass --clang-cpp-path or set $HOST_CPP")
        else:
            # always use the CHERI clang built by jenkins
            self.clang_path = self.cheri_sdk_bindir / "clang"
            self.clang_plusplus_path = self.cheri_sdk_bindir / "clang++"

        if self.cheri_sdk_path is not None:
            assert self.cheri_sdk_bindir == self.cheri_sdk_path / "bin"

        assert self._ensure_required_properties_set()
        if os.getenv("DEBUG") is not None:
            import pprint
            for k, v in self.__dict__.items():
                if hasattr(v, "__get__"):
                    # noinspection PyCallingNonCallable
                    setattr(self, k, v.__get__(self, self.__class__))  # pytype: disable=attribute-error

            pprint.pprint(vars(self))
