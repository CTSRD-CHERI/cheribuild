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
from pathlib import Path

from .loader import ConfigLoaderBase, JsonAndCommandLineConfigLoader
from .chericonfig import CheriConfig
from ..utils import defaultNumberOfMakeJobs


class DefaultCheriConfig(CheriConfig):
    def __init__(self, loader: ConfigLoaderBase, availableTargets: list):
        super().__init__(loader)
        assert isinstance(loader, JsonAndCommandLineConfigLoader)
        # boolean flags
        self.quiet = loader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
        self.verbose = loader.addBoolOption("verbose", "v", help="Print all commmands that are executed")
        self.clean = loader.addBoolOption("clean", "c", help="Remove the build directory before build")
        self.force = loader.addBoolOption("force", "f", help="Don't prompt for user input but use the default action")
        self.noLogfile = loader.addBoolOption("no-logfile", help="Don't write a logfile for the build steps")
        self.skipUpdate = loader.addBoolOption("skip-update", help="Skip the git pull step")
        self.skipConfigure = loader.addBoolOption("skip-configure", help="Skip the configure step",
                                                  group=loader.configureGroup)
        self.forceConfigure = loader.addBoolOption("reconfigure", "-force-configure",
                                                   group=loader.configureGroup,
                                                   help="Always run the configure step, even for CMake projects with a "
                                                        "valid cache.")
        self.listTargets = loader.addBoolOption("list-targets", help="List all available targets and exit")
        self.dumpConfig = loader.addBoolOption("dump-configuration", help="Print the current configuration as JSON."
                                                                          " This can be saved to ~/.config/cheribuild.json to make it persistent")
        self.getConfigOption = loader.addOption("get-config-option", type=str, metavar="KEY",
                                                help="Print the value of config option KEY and exit")
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
        self.cheriBits = loader.addOption("cheri-bits", type=int, group=loader.cheriBitsGroup, default=256,
                                          help="Whether to build the whole software stack for 128 or 256 bit"
                                               " CHERI. The output directories will be suffixed with the number of bits"
                                               " to make sure the right binaries are being used.",
                                          choices=["128", "256"])

        self.createCompilationDB = loader.addBoolOption("compilation-db", "-cdb",
                                                        help="Create a compile_commands.json file in the build dir "
                                                             "(requires Bear for non-CMake projects)")
        self.crossCompileForMips = loader.addBoolOption("cross-compile-for-mips", "-xmips",
                                                        help="Make cross compile projects target MIPS hybrid ABI "
                                                             "instead of CheriABI")
        self.makeWithoutNice = loader.addBoolOption("make-without-nice", help="Run make/ninja without nice(1)")

        self.makeJobs = loader.addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(),
                                               help="Number of jobs to use for compiling")

        # configurable paths
        self.sourceRoot = loader.addPathOption("source-root", default=Path(os.path.expanduser("~/cheri")),
                                               help="The directory to store all sources")
        self.outputRoot = loader.addPathOption("output-root", default=lambda p, cls: (p.sourceRoot / "output"),
                                               help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
        self.buildRoot = loader.addPathOption("build-root", default=lambda p, cls: (p.sourceRoot / "build"),
                                              help="The directory for all the builds (default: '<SOURCE_ROOT>/build')")

        loader.finalizeOptions(availableTargets)

    def load(self):
        super().load()
        # Set CHERI_BITS variable to allow e.g. { cheribsd": { "install-directory": "~/rootfs${CHERI_BITS}" } }
        os.environ["CHERI_BITS"] = self.cheriBitsStr
        self.sysrootArchiveName = "cheri-sysroot.tar.gz"
        # now set some generic derived config options
        self.sdkDir = self.outputRoot / self.sdkDirectoryName  # qemu and binutils (and llvm/clang)
        self.otherToolsDir = self.outputRoot / "bootstrap"
        self._initializeDerivedPaths()

        assert self._ensureRequiredPropertiesSet()
