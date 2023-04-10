#
# Copyright (c) 2016 Alex Richardson
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
import argparse
import os
import sys
from pathlib import Path

from .chericonfig import CheribuildActionEnum, CheriConfig
from .config_loader_base import ComputedDefaultValue, ConfigLoaderBase
from .loader import JsonAndCommandLineConfigLoader, argcomplete
from ..utils import default_make_jobs_count


class CheribuildAction(CheribuildActionEnum):
    BUILD = ("--build", "Run (usually build+install) chosen targets (default)")
    TEST = ("--test", "Run tests for the passed targets instead of building them", "--run-tests")
    BENCHMARK = ("--benchmark", "Run tests for the passed targets instead of building them")
    BUILD_AND_TEST = ("--build-and-test", "Run chosen targets and then run any tests afterwards", None,
                      # can get the other instances yet -> use strings
                      ["build", "test"])
    LIST_TARGETS = ("--list-targets", "List all available targets and exit")
    DUMP_CONFIGURATION = ("--dump-configuration", "Print the current configuration as JSON. This can be saved to "
                                                  "~/.config/cheribuild.json to make it persistent")

    def __init__(self, option_name, help_message, altname=None, actions=None) -> None:
        self.option_name = option_name
        self.help_message = help_message
        self.altname = altname
        if not actions:
            actions = [self]
        if actions:
            self.actions = actions


class DefaultCheribuildConfigLoader(JsonAndCommandLineConfigLoader):
    def finalize_options(self, available_targets: list, **kwargs) -> None:
        target_option = self._parser.add_argument("targets", metavar="TARGET", nargs=argparse.ZERO_OR_MORE,
                                                  help="The targets to build")
        if argcomplete and self.is_completing_arguments:
            # if OSInfo.IS_FREEBSD: # FIXME: for some reason this won't work
            self.completion_excludes = ["-t", "--skip-dependencies"]
            if sys.platform.startswith("freebsd"):
                self.completion_excludes += ["--freebsd-builder-copy-only", "--freebsd-builder-hostname",
                                             "--freebsd-builder-output-path"]

            visible_targets = available_targets.copy()
            visible_targets.remove("__run_everything__")
            target_completer = argcomplete.completers.ChoicesCompleter(visible_targets)
            target_option.completer = target_completer
            # make sure we get target completion for the unparsed args too by adding another zero_or more options
            # not sure why this works but it's a nice hack
            unparsed = self._parser.add_argument("targets", metavar="TARGET", type=list, nargs=argparse.ZERO_OR_MORE,
                                                 help=argparse.SUPPRESS, choices=available_targets)
            unparsed.completer = target_completer


class DefaultCheriConfig(CheriConfig):
    def __init__(self, loader: ConfigLoaderBase, available_targets: list) -> None:
        super().__init__(loader, action_class=CheribuildAction)
        self.default_action = CheribuildAction.BUILD
        # The run mode:
        self.get_config_option = loader.add_option("get-config-option", type=str, metavar="KEY",
                                                   group=loader.action_group,
                                                   help="Print the value of config option KEY and exit")
        # boolean flags
        self.quiet = loader.add_bool_option("quiet", "q", help="Don't show stdout of the commands that are executed")
        self.verbose = loader.add_bool_option("verbose", "v", help="Print all commmands that are executed")
        self.clean = loader.add_bool_option("clean", "c", help="Remove the build directory before build")
        self.force = loader.add_bool_option("force", "f", help="Don't prompt for user input but use the default action")
        self.write_logfile = loader.add_bool_option("logfile", help="Write a logfile for the build steps",
                                                    default=False)
        self.skip_update = loader.add_bool_option("skip-update", help="Skip the git pull step")
        self.skip_clone = False
        self.confirm_clone = loader.add_bool_option(
            "confirm-clone", help="Ask for confirmation before cloning repositories.")
        self.force_update = loader.add_bool_option("force-update", help="Always update (with autostash) even if there "
                                                                        "are uncommitted changes")

        self.presume_connectivity = loader.add_bool_option(
            "presume-connectivity",
            help="Do not probe for network connectivity and just assume that we are suitably connected")

        # TODO: should replace this group with a tristate value
        configure_group = loader.add_mutually_exclusive_group()
        self.skip_configure = loader.add_bool_option("skip-configure", help="Skip the configure step",
                                                     group=configure_group)
        self.force_configure = loader.add_bool_option("reconfigure", "-force-configure", group=configure_group,
                                                      help="Always run the configure step, even for CMake projects "
                                                           "with a valid cache.")
        self.include_dependencies = loader.add_commandline_only_bool_option(
            "include-dependencies", "d", group=loader.dependencies_group,
            help="Also build the dependencies of targets passed on the command line. Targets passed on the command "
                 "line will be reordered and processed in an order that ensures dependencies are built before the "
                 "real target. (run --list-targets for more information). By default this does not build toolchain "
                 "targets such as LLVM. Pass --include-toolchain-dependencies to also build those.")
        self.include_toolchain_dependencies = loader.add_bool_option(
            "include-toolchain-dependencies", default=True, group=loader.dependencies_group,
            help="Include toolchain targets such as LLVM and QEMU when --include-dependencies is set.")
        self.enable_hybrid_targets = loader.add_bool_option(
            "enable-hybrid-targets", default=False, help_hidden=True,
            help="Enable building hybrid targets. This is highly discouraged, "
                 "only enable if you know what you're doing.")

        start_after_group = loader.dependencies_group.add_mutually_exclusive_group()

        self.start_with = loader.add_commandline_only_option(
            "start-with", metavar="TARGET", group=start_after_group,
            help="Start building at TARGET (useful when resuming an interrupted --include-depedencies build)")
        self.start_after = loader.add_commandline_only_option(
            "start-after", metavar="TARGET", group=start_after_group,
            help="Start building after TARGET (useful when resuming an interrupted --include-depedencies build)")

        self.copy_compilation_db_to_source_dir = loader.add_commandline_only_bool_option(
            "compilation-db-in-source-dir",
            help="Generate a compile_commands.json and also copy it to the source directory")
        self.generate_cmakelists = loader.add_bool_option(
            "generate-cmakelists",
            help="Generate a CMakeLists.txt that just calls cheribuild. Useful for IDEs that only support CMake")

        self.make_without_nice = loader.add_bool_option("make-without-nice", help="Run make/ninja without nice(1)")

        default_make_jobs = default_make_jobs_count()
        default_make_jobs_computed = ComputedDefaultValue(lambda p, cls: default_make_jobs,
                                                          as_string=str(default_make_jobs),
                                                          as_readme_string="<system-dependent>")
        self.make_jobs: int = loader.add_option("make-jobs", "j", type=int, default=default_make_jobs_computed,
                                                help="Number of jobs to use for compiling")

        # configurable paths
        self.source_root = loader.add_path_option("source-root",
                                                  default=Path(os.path.expanduser("~/cheri")), group=loader.path_group,
                                                  help="The directory to store all sources")
        self.output_root = loader.add_path_option("output-root",
                                                  default=lambda p, cls: (p.source_root / "output"),
                                                  group=loader.path_group,
                                                  help="The directory to store all output (default: "
                                                       "'<SOURCE_ROOT>/output')")
        self.build_root = loader.add_path_option("build-root",
                                                 default=lambda p, cls: (p.source_root / "build"),
                                                 group=loader.path_group,
                                                 help="The directory for all the builds (default: "
                                                      "'<SOURCE_ROOT>/build')")
        self.tools_root = loader.add_path_option("tools-root",
                                                 default=lambda p, cls: p.output_root, group=loader.path_group,
                                                 help="The directory to find sdk and bootstrap tools (default: "
                                                      "'<OUTPUT_ROOT>')")
        default_morello_sdk = ComputedDefaultValue(
            function=lambda p, cls: (p.tools_root / p.default_morello_sdk_directory_name),
            as_string="'<TOOLS_ROOT>/morello-sdk'")
        self.morello_sdk_dir = loader.add_path_option("morello-sdk-root",
                                                      default=default_morello_sdk, group=loader.path_group,
                                                      help="The directory to find/install the Morello SDK")
        self.sysroot_output_root = loader.add_path_option("sysroot-install-root", shortname="-sysroot-install-dir",
                                                          default=lambda p, cls: p.tools_root, group=loader.path_group,
                                                          help="Sysroot prefix (default: '<TOOLS_ROOT>')")

        # Hidden option to enable foo-hybrid-for-purecap-rootfs targets for all projects. This option is not actually
        # uses since we have to look at sys.argv[] directly due to depedency cycles. However, we do still need it since
        # we would otherwise get an invalid command line argument error.
        self.enable_hybrid_for_purecap_rootfs_targets = loader.add_commandline_only_bool_option(
            "enable-hybrid-for-purecap-rootfs-targets", default=False, help_hidden=True)
        loader.finalize_options(available_targets)

    def load(self) -> None:
        super().load()
        # now set some generic derived config options
        self.cheri_sdk_dir = self.tools_root / self.default_cheri_sdk_directory_name
        self.other_tools_dir = self.tools_root / "bootstrap"
        self.cheribsd_image_root = self.output_root  # TODO: allow this to be different?

        assert self._ensure_required_properties_set()
