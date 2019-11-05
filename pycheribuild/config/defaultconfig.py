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
import os
from enum import Enum
from pathlib import Path

from .loader import ConfigLoaderBase, JsonAndCommandLineConfigLoader
from .chericonfig import CheriConfig, CrossCompileTarget
from ..utils import defaultNumberOfMakeJobs

class CheribuildAction(Enum):
    BUILD = ("--build", "Run (usually build+install) chosen targets (default)")
    TEST = ("--test", "Run tests for the passed targets instead of building them", "--run-tests")
    BENCHMARK = ("--benchmark", "Run tests for the passed targets instead of building them")
    BUILD_AND_TEST = ("--build-and-test", "Run chosen targets and then run any tests afterwards", None,
                      # can get the other instances yet -> use strings
                      ["build", "test"])
    LIST_TARGETS = ("--list-targets", "List all available targets and exit")
    PRINT_CHOSEN_TARGETS = ("--print-chosen-targets", "List all the targets that would be built")
    DUMP_CONFIGURATION = ("--dump-configuration", "Print the current configuration as JSON. This can be saved to "
                                                  "~/.config/cheribuild.json to make it persistent")

    def __init__(self, option_name, help_message, altname=None, actions=None):
        self.option_name = option_name
        self.help_message = help_message
        self.altname = altname
        if not actions:
            actions = [self]
        if actions:
            self.actions = actions

class DefaultCheriConfig(CheriConfig):
    def __init__(self, loader: ConfigLoaderBase, availableTargets: list):
        super().__init__(loader, action_class=CheribuildAction)
        self.default_action = CheribuildAction.BUILD
        assert isinstance(loader, JsonAndCommandLineConfigLoader)
        # The run mode:
        self.getConfigOption = loader.addOption("get-config-option", type=str, metavar="KEY", group=loader.actionGroup,
                                                help="Print the value of config option KEY and exit")
        # boolean flags
        self.quiet = loader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
        self.verbose = loader.addBoolOption("verbose", "v", help="Print all commmands that are executed")
        self.clean = loader.addBoolOption("clean", "c", help="Remove the build directory before build")
        self.force = loader.addBoolOption("force", "f", help="Don't prompt for user input but use the default action")
        self.write_logfile = loader.addBoolOption("logfile", help="Don't write a logfile for the build steps", default=False)
        self.skipUpdate = loader.addBoolOption("skip-update", help="Skip the git pull step")
        self.skipClone = False
        self.force_update = loader.addBoolOption("force-update", help="Always update (with autostash) even if there "
                                                                      "are uncommitted changes")
        self.skipConfigure = loader.addBoolOption("skip-configure", help="Skip the configure step",
                                                  group=loader.configureGroup)
        self.forceConfigure = loader.addBoolOption("reconfigure", "-force-configure",
                                                   group=loader.configureGroup,
                                                   help="Always run the configure step, even for CMake projects with a "
                                                        "valid cache.")
        self.includeDependencies = loader.addBoolOption("include-dependencies", "d",
                                                        help="Also build the dependencies "
                                                             "of targets passed on the command line. Targets passed on the"
                                                             "command line will be reordered and processed in an order that "
                                                             "ensures dependencies are built before the real target. (run "
                                                             " with --list-targets for more information)")

        # TODO: use action="store_const" for these two options
        self._buildCheri128 = loader.cheriBitsGroup.add_argument("--cheri-128", "--128", dest="cheri_bits",
                                                                 action="store_const", const="128",
                                                                 help="Shortcut for --cheri-bits=128")
        self._buildCheri256 = loader.cheriBitsGroup.add_argument("--cheri-256", "--256", dest="cheri_bits",
                                                                 action="store_const", const="256",
                                                                 help="Shortcut for --cheri-bits=256")
        self.cheriBits = loader.addOption("cheri-bits", type=int, group=loader.cheriBitsGroup, default=128,
                                          help="Whether to build the whole software stack for 128 or 256 bit"
                                               " CHERI. The output directories will be suffixed with the number of bits"
                                               " to make sure the right binaries are being used.",
                                          choices=["128", "256"])

        self.copy_compilation_db_to_source_dir = loader.addCommandLineOnlyBoolOption("compilation-db-in-source-dir",
            help="Generate a compile_commands.json and also copy it to the source directory")

        self.crossCompileForMips = loader.addBoolOption("cross-compile-for-mips", "-xmips", group=loader.crossCompileGroup,
                                                        help="Make cross compile projects target MIPS hybrid ABI "
                                                             "instead of CheriABI")
        self.crossCompileForHost = loader.addBoolOption("cross-compile-for-host", "-xhost", group=loader.crossCompileGroup,
                                                        help="Make cross compile projects target the host system and "
                                                             "use cheri clang to compile (tests that we didn't break x86)")

        self.makeWithoutNice = loader.addBoolOption("make-without-nice", help="Run make/ninja without nice(1)")

        self.makeJobs = loader.addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(),
                                         help="Number of jobs to use for compiling")

        # configurable paths
        self.sourceRoot = loader.addPathOption("source-root",
            default=Path(os.path.expanduser("~/cheri")), group=loader.pathGroup,
            help="The directory to store all sources")
        self.outputRoot = loader.addPathOption("output-root",
            default=lambda p, cls: (p.sourceRoot / "output"), group=loader.pathGroup,
            help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
        self.buildRoot = loader.addPathOption("build-root",
            default=lambda p, cls: (p.sourceRoot / "build"), group=loader.pathGroup,
            help="The directory for all the builds (default: '<SOURCE_ROOT>/build')")
        loader.finalizeOptions(availableTargets)

    def load(self):
        super().load()
        if self.crossCompileForHost:
            assert not self.crossCompileForMips
            self.crossCompileTarget = CrossCompileTarget.NATIVE
        elif self.crossCompileForMips:
            assert not self.crossCompileForHost
            self.crossCompileTarget = CrossCompileTarget.MIPS
        else:
            self.crossCompileTarget = CrossCompileTarget.CHERIBSD_MIPS_PURECAP
        # now set some generic derived config options
        self.sdkDir = self.outputRoot / self.sdkDirectoryName  # qemu and binutils (and llvm/clang)
        self.otherToolsDir = self.outputRoot / "bootstrap"
        self.cheribsd_image_root = self.outputRoot  # TODO: allow this to be different?
        self._initializeDerivedPaths()

        assert self._ensureRequiredPropertiesSet()
