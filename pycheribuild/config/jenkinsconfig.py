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
from .config_loader_base import ComputedDefaultValue, ConfigLoaderBase
from .target_info import CompilerType
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

    def __init__(self, option_name, help_message, altname=None, actions=None) -> None:
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


def _infer_compiler_output_path(config: "JenkinsConfig", _):
    if config.compiler_type == CompilerType.CHERI_LLVM:
        return config.cheri_sdk_dir
    elif config.compiler_type == CompilerType.MORELLO_LLVM:
        return config.morello_sdk_dir
    elif config.compiler_type == CompilerType.UPSTREAM_LLVM:
        return config.workspace / "upstream-llvm-sdk"
    else:
        raise ValueError("Unsupported compiler type: {}".format(config.compiler_type))


class JenkinsConfig(CheriConfig):
    def __init__(self, loader: ConfigLoaderBase, available_targets: list) -> None:
        super().__init__(loader, action_class=JenkinsAction)
        self.default_action = ""  # error if no action set

        self.cpu = loader.add_commandline_only_option(
            "cpu", default=os.getenv("CPU", "default"),
            help="Only used for backwards compatibility with old jenkins jobs")  # type: str
        self.workspace = loader.add_commandline_only_option(
            "workspace", default=os.getenv("WORKSPACE"), type=Path,
            help="The root directory for building (defaults to $WORKSPACE)")  # type: Path
        self.compiler_archive_name = loader.add_commandline_only_option(
            "compiler-archive", type=str, default="cheri-clang-llvm.tar.xz",
            help="The name of the archive containing the compiler")  # type: str
        self.compiler_archive_output_path = loader.add_commandline_only_option(
            "compiler-archive-output-path", type=Path, default=_infer_compiler_output_path,
            help="The path where to extract the compiler")  # type: Path
        self.compiler_type = loader.add_commandline_only_option(
            "compiler-type", type=CompilerType, default=CompilerType.CHERI_LLVM,
            enum_choices=[CompilerType.CHERI_LLVM, CompilerType.MORELLO_LLVM, CompilerType.UPSTREAM_LLVM],
            help="The type of the compiler to extract (used to infer the output "
                 " path)")  # type: typing.Optional[CompilerType]
        self.sysroot_archive_name = loader.add_commandline_only_option(
            "sysroot-archive", type=str, default="cheribsd-sysroot.tar.xz",
            help="The name of the archive containing the sysroot")  # type: str
        self.sysroot_archive_output_path = loader.add_commandline_only_option(
            "sysroot-archive-output-path", type=Path,
            default=ComputedDefaultValue(lambda c, _: c.compiler_archive_output_path / "sysroot",
                                         as_string="<compiler_path>/sysroot"),
            help="The path where to extract the sysroot (default=")  # type: typing.Optional[Path]
        self.keep_install_dir = loader.add_commandline_only_bool_option(
            "keep-install-dir", help="Don't delete the install dir prior to build")  # type: bool
        self.keep_sdk_dir = loader.add_commandline_only_bool_option(
            "keep-sdk-dir", help="Don't delete existing SDK dir even if there is a newer archive")  # type: bool
        self.force_update = loader.add_commandline_only_bool_option(
            "force-update", help="Do the updating (not recommended in jenkins!)")  # type: bool
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
        self.use_system_compiler_for_native = loader.add_commandline_only_bool_option(
            "use-system-compiler-for-native", "-without-sdk",
            help="Don't use the CHERI SDK -> only /usr (for native builds)")
        self.strip_elf_files = loader.add_commandline_only_bool_option(
            "strip-elf-files", help="Strip ELF files before creating the tarball", default=True, negatable=True)
        self._cheri_sdk_dir_override = loader.add_commandline_only_option(
            "cheri-sdk-path", default=None, type=Path,
            help="Override the path to the CHERI SDK (default is $WORKSPACE/cherisdk)")  # type: Path
        self._morello_sdk_dir_override = loader.add_commandline_only_option(
            "morello-sdk-path", default=None, type=Path,
            help="Override the path to the Morello SDK (default is $WORKSPACE/morello-sdk)")  # type: Path
        self.extract_compiler_only = loader.add_commandline_only_bool_option(
            "extract-compiler-only", help="Don't attempt to extract a sysroot")
        self.tarball_name = loader.add_commandline_only_option(
            "tarball-name", default=lambda conf, cls: conf.targets[0] + "-" + conf.cpu + ".tar.xz")

        self.default_output_path = "tarball"
        default_output = ComputedDefaultValue(lambda c, _: c.workspace / c.default_output_path,
                                              "$WORKSPACE/" + self.default_output_path)
        self.output_root = loader.add_commandline_only_option("output-path", default=default_output, type=Path,
                                                              help="Path for the output (relative to $WORKSPACE)")
        self.sysroot_output_root = loader.add_commandline_only_option(
            "sysroot-output-path",
            default=ComputedDefaultValue(function=lambda c, _: c.workspace, as_string="$WORKSPACE"),
            type=Path, help="Path for the installed sysroot (defaults to the same value as --output-path)")
        # self.strip_install_prefix_from_archive = loader.add_commandline_only_bool_option(
        # "strip-install-prefix-from-archive",
        #    help="Only put the files inside the install prefix into the tarball (stripping the leading
        #    directories)")  # type: bool
        self.skip_update = True
        self.skip_clone = True
        self.confirm_clone = False
        self.verbose = True
        self.quiet = False
        self.clean = loader.add_commandline_only_bool_option("clean", default=True, negatable=True,
                                                             help="Clean build directory before building")
        self.force = True  # no user input in jenkins
        self.write_logfile = False  # jenkins stores the output anyway
        self.skip_configure = loader.add_bool_option("skip-configure", help="Skip the configure step")
        self.force_configure = True
        self.include_dependencies = False
        self.enable_hybrid_targets = True
        self.allow_more_than_one_target = loader.add_commandline_only_bool_option(
            "allow-more-than-one-target",  # help_hidden=True, Note: setting this to True seems to break argparse
            help="Allow more than one target on the command line. This should only be used for testing since "
                 "dependencies are not resolved!")
        loader.finalize_options(available_targets)
        self.FS = FileSystemUtils(self)

    @property
    def default_cheri_sdk_directory_name(self):
        # FIXME: remove this difference between jenkins and non-jenkins builds
        return "cherisdk"

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

    def load(self) -> None:
        super().load()

        if not self.workspace or not self.workspace.is_dir():
            fatal_error("WORKSPACE is not set to a valid directory:", self.workspace, pretend=self.pretend,
                        fatal_when_pretending=True)
        self.source_root = self.workspace
        self.build_root = self.workspace
        if self.output_root != self.workspace / self.default_output_path:
            if not self.keep_install_dir:
                print("Not cleaning non-default output path", self.output_root)
            self.keep_install_dir = True
        if os.path.relpath(str(self.output_root), str(self.workspace)).startswith(".."):
            fatal_error("Output path", self.output_root, "must be inside workspace", self.workspace)
        if os.path.relpath(str(self.sysroot_output_root), str(self.workspace)).startswith(".."):
            fatal_error("Sysroot output path", self.sysroot_output_root, "must be inside workspace", self.workspace,
                        pretend=False, fatal_when_pretending=True)

        # expect the CheriBSD disk images in the workspace root
        self.cheribsd_image_root = self.workspace

        self.other_tools_dir = self.workspace / "bootstrap"

        if self._cheri_sdk_dir_override is not None:
            self.cheri_sdk_dir = self._cheri_sdk_dir_override
        elif Path("/cheri-sdk/bin/clang").exists():  # check for ctsrd/cheri-sdk docker image
            self.cheri_sdk_dir = Path("/cheri-sdk")
        else:
            self.cheri_sdk_dir = self.workspace / self.default_cheri_sdk_directory_name

        if self._morello_sdk_dir_override is not None:
            self.morello_sdk_dir = self._morello_sdk_dir_override
        elif Path("/morello-sdk/bin/clang").exists():  # check for docker image
            self.morello_sdk_dir = Path("/morello-sdk")
        else:
            self.morello_sdk_dir = self.workspace / self.default_morello_sdk_directory_name

        if self.cpu != "default":
            warning_message("--cpu parameter passed(", self.cpu, "), this is deprecated!", sep="")

        if self.force_update:
            self.skip_update = False
            self.skip_clone = False

        if self.use_system_compiler_for_native:
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
            # always use the CHERI clang built by jenkins (if available)
            # Prefix $WORKSPACE/native-sdk, but fall back to CHERI/Morello LLVM if that does not exist
            compiler_dir_override = None
            if Path(self.workspace, "native-sdk/bin/clang").exists():
                compiler_dir_override = Path(self.workspace, "native-sdk/bin")
            elif (self.cheri_sdk_bindir / "clang").exists():
                compiler_dir_override = self.cheri_sdk_bindir
            elif (self.morello_sdk_dir / "bin/clang").exists():
                compiler_dir_override = self.morello_sdk_dir / "bin"
            if compiler_dir_override is not None:
                self.clang_path = compiler_dir_override / "clang"
                self.clang_plusplus_path = compiler_dir_override / "clang++"
                self.clang_cpp_path = compiler_dir_override / "clang-cpp"

        if self._cheri_sdk_dir_override is not None:
            assert self.cheri_sdk_bindir == self._cheri_sdk_dir_override / "bin"
        if self._morello_sdk_dir_override is not None:
            assert self.morello_sdk_dir == self._morello_sdk_dir_override

        assert self._ensure_required_properties_set()
        if os.getenv("DEBUG") is not None:
            import pprint
            for k, v in self.__dict__.items():
                if hasattr(v, "__get__"):
                    # noinspection PyCallingNonCallable
                    setattr(self, k, v.__get__(self, self.__class__))  # pytype: disable=attribute-error

            pprint.pprint(vars(self))
