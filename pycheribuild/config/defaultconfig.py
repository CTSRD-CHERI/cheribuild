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

from .loader import ConfigLoader
from .chericonfig import CheriConfig
from ..utils import latestClangTool, defaultNumberOfMakeJobs

assert ConfigLoader, "ConfigLoader must be initialized before importing defaultchericonfig"


class DefaultCheriConfig(CheriConfig):
    foo = ConfigLoader.addBoolOption("foo", "fff", help="Don't show stdout of the commands that are executed")

    def __init__(self, availableTargets: list):
        # TODO: take ConfigLoader as parameter
        super().__init__(ConfigLoader)
        # boolean flags
        self.quiet = ConfigLoader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
        self.verbose = ConfigLoader.addBoolOption("verbose", "v", help="Print all commmands that are executed")
        self.clean = ConfigLoader.addBoolOption("clean", "c", help="Remove the build directory before build")
        self.force = ConfigLoader.addBoolOption("force", "f", help="Don't prompt for user input but use the default action")
        self.noLogfile = ConfigLoader.addBoolOption("no-logfile", help="Don't write a logfile for the build steps")
        self.skipUpdate = ConfigLoader.addBoolOption("skip-update", help="Skip the git pull step")
        self.skipConfigure = ConfigLoader.addBoolOption("skip-configure", help="Skip the configure step",
                                                   group=ConfigLoader.configureGroup)
        self.forceConfigure = ConfigLoader.addBoolOption("reconfigure", "-force-configure",
                                                    group=ConfigLoader.configureGroup,
                                                    help="Always run the configure step, even for CMake projects with a "
                                                         "valid cache.")
        self.skipInstall = ConfigLoader.addBoolOption("skip-install", help="Skip the install step (only do the build)")
        self.listTargets = ConfigLoader.addBoolOption("list-targets", help="List all available targets and exit")
        self.dumpConfig = ConfigLoader.addBoolOption("dump-configuration", help="Print the current configuration as JSON."
                                                                           " This can be saved to ~/.config/cheribuild.json to make it persistent")
        self.getConfigOption = ConfigLoader.addOption("get-config-option", type=str, metavar="KEY",
                                                 help="Print the value of config option KEY and exit")
        self.includeDependencies = ConfigLoader.addBoolOption("include-dependencies", "d",
                                                         help="Also build the dependencies "
                                                              "of targets passed on the command line. Targets passed on the"
                                                              "command line will be reordered and processed in an order that "
                                                              "ensures dependencies are built before the real target. (run "
                                                              " with --list-targets for more information)")

        # TODO: use action="store_const" for these two options
        self._buildCheri128 = ConfigLoader.cheriBitsGroup.add_argument("--cheri-128", "--128", dest="cheri_bits",
                                                                  action="store_const", const="128",
                                                                  help="Shortcut for --cheri-bits=128")
        self._buildCheri256 = ConfigLoader.cheriBitsGroup.add_argument("--cheri-256", "--256", dest="cheri_bits",
                                                                  action="store_const", const="256",
                                                                  help="Shortcut for --cheri-bits=256")
        self.cheriBits = ConfigLoader.addOption("cheri-bits", type=int, group=ConfigLoader.cheriBitsGroup, default=256,
                                           help="Whether to build the whole software stack for 128 or 256 bit"
                                                " CHERI. The output directories will be suffixed with the number of bits to"
                                                " make sure the right binaries are being used.", choices=["128", "256"])

        self.createCompilationDB = ConfigLoader.addBoolOption("compilation-db", "-cdb",
                                                         help="Create a compile_commands.json file in the build dir "
                                                              "(requires Bear for non-CMake projects)")
        self.crossCompileForMips = ConfigLoader.addBoolOption("cross-compile-for-mips", "-xmips",
                                                         help="Make cross compile projects target MIPS hybrid ABI "
                                                              "instead of CheriABI")
        self.makeWithoutNice = ConfigLoader.addBoolOption("make-without-nice", help="Run make/ninja without nice(1)")

        # configurable paths
        self.sourceRoot = ConfigLoader.addPathOption("source-root", default=Path(os.path.expanduser("~/cheri")),
                                                help="The directory to store all sources")
        self.outputRoot = ConfigLoader.addPathOption("output-root", default=lambda p, cls: (p.sourceRoot / "output"),
                                                help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
        self.buildRoot = ConfigLoader.addPathOption("build-root", default=lambda p, cls: (p.sourceRoot / "build"),
                                               help="The directory for all the builds (default: '<SOURCE_ROOT>/build')")

        self.targets = ConfigLoader.loadTargets(availableTargets)
        # Set CHERI_BITS variable to allow e.g. { cheribsd": { "install-directory": "~/rootfs${CHERI_BITS}" } }
        os.environ["CHERI_BITS"] = self.cheriBitsStr

        # now the derived config options
        self.sdkDir = self.outputRoot / self.sdkDirectoryName  # qemu and binutils (and llvm/clang)
        self.otherToolsDir = self.outputRoot / "bootstrap"
        self.dollarPathWithOtherTools = str(self.otherToolsDir / "bin") + ":" + os.getenv("PATH")
        self.sdkSysrootDir = self.sdkDir / "sysroot"
        self.sysrootArchiveName = "cheri-sysroot.tar.gz"
        self._initialized = True
