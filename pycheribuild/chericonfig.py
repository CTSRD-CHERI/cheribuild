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
import json
import shutil
from .configloader import ConfigLoader
from pathlib import Path


assert ConfigLoader, "ConfigLoader must be initialized before importing chericonfig"

def defaultNumberOfMakeJobs():
    makeJobs = os.cpu_count()
    if makeJobs > 24:
        # don't use up all the resources on shared build systems
        # (you can still override this with the -j command line option)
        makeJobs = 16
    return makeJobs


def defaultClangTool(basename: str):
    # try to find clang 3.7, otherwise fall back to system clang
    for version in [(4, 0), (3, 9), (3, 8), (3, 7)]:
        # FreeBSD installs clang39, Linux uses clang-3.9
        guess = shutil.which(basename + "%d%d" % version)
        if guess:
            return guess
        guess = shutil.which(basename + "-%d.%d" % version)
        if guess:
            return guess
    guess = shutil.which(basename)
    return guess


# custom encoder to handle pathlib.Path objects
class MyJsonEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        # noinspection PyArgumentList
        super().__init__(*args, **kwargs)

    def default(self, o):
        if isinstance(o, Path):
            return str(o)
        return super().default(o)


class CheriConfig(object):
    # boolean flags
    pretend = ConfigLoader.addBoolOption("pretend", "p", help="Only print the commands instead of running them")
    quiet = ConfigLoader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
    verbose = ConfigLoader.addBoolOption("verbose", "v", help="Print all commmands that are executed")
    clean = ConfigLoader.addBoolOption("clean", "c", help="Remove the build directory before build")
    force = ConfigLoader.addBoolOption("force", "f", help="Don't prompt for user input but use the default action")
    noLogfile = ConfigLoader.addBoolOption("no-logfile", help="Don't write a logfile for the build steps")
    skipUpdate = ConfigLoader.addBoolOption("skip-update", help="Skip the git pull step")
    skipConfigure = ConfigLoader.addBoolOption("skip-configure", help="Skip the configure step",
                                               group=ConfigLoader.configureGroup)
    forceConfigure = ConfigLoader.addBoolOption("reconfigure", "-force-configure", group=ConfigLoader.configureGroup,
                                                help="Always run the configure step, even for CMake projects with a "
                                                     "valid cache.")
    skipInstall = ConfigLoader.addBoolOption("skip-install", help="Skip the install step (only do the build)")
    listTargets = ConfigLoader.addBoolOption("list-targets", help="List all available targets and exit")
    dumpConfig = ConfigLoader.addBoolOption("dump-configuration", help="Print the current configuration as JSON."
                                            " This can be saved to ~/.config/cheribuild.json to make it persistent")
    getConfigOption = ConfigLoader.addOption("get-config-option", type=str, metavar="KEY",
                                             help="Print the value of config option KEY and exit")
    includeDependencies = ConfigLoader.addBoolOption("include-dependencies", "d", help="Also build the dependencies "
                                                     "of targets passed on the command line. Targets passed on the"
                                                     "command line will be reordered and processed in an order that "
                                                     "ensures dependencies are built before the real target. (run "
                                                     " with --list-targets for more information)")

    # TODO: use action="store_const" for these two options
    _buildCheri128 = ConfigLoader.cheriBitsGroup.add_argument("--cheri-128", "--128", dest="cheri_bits",
                                                              action="store_const", const="128",
                                                              help="Shortcut for --cheri-bits=128")
    _buildCheri256 = ConfigLoader.cheriBitsGroup.add_argument("--cheri-256", "--256", dest="cheri_bits",
                                                              action="store_const", const="256",
                                                              help="Shortcut for --cheri-bits=256")
    cheriBits = ConfigLoader.addOption("cheri-bits", type=int, group=ConfigLoader.cheriBitsGroup, default=256,
                                       help="Whether to build the whole software stack for 128 or 256 bit"
                                       " CHERI. The output directories will be suffixed with the number of bits to"
                                       " make sure the right binaries are being used.", choices=["128", "256"])

    createCompilationDB = ConfigLoader.addBoolOption("compilation-db", "-cdb",
                                                     help="Create a compile_commands.json file in the build dir "
                                                          "(requires Bear for non-CMake projects)")
    crossCompileForMips = ConfigLoader.addBoolOption("cross-compile-for-mips", "-xmips",
                                                     help="Make cross compile projects target MIPS hybrid ABI "
                                                          "instead of CheriABI")
    makeWithoutNice = ConfigLoader.addBoolOption("make-without-nice", help="Run make/ninja without nice(1)")

    # configurable paths
    sourceRoot = ConfigLoader.addPathOption("source-root", default=Path(os.path.expanduser("~/cheri")),
                                            help="The directory to store all sources")
    outputRoot = ConfigLoader.addPathOption("output-root", default=lambda p, cls: (p.sourceRoot / "output"),
                                            help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
    buildRoot = ConfigLoader.addPathOption("build-root", default=lambda p, cls: (p.sourceRoot / "build"),
                                           help="The directory for all the builds (default: '<SOURCE_ROOT>/build')")
    clangPath = ConfigLoader.addPathOption("clang-path", default=defaultClangTool("clang"),
                                           help="The Clang C compiler to use for compiling LLVM+Clang (must be at "
                                                "least version 3.7)")
    clangPlusPlusPath = ConfigLoader.addPathOption("clang++-path", default=defaultClangTool("clang++"),
                                                   help="The Clang C++ compiler to use for compiling LLVM+Clang (must "
                                                        "be at least version 3.7)")
    # other options
    # TODO: allow overriding per-project?
    makeJobs = ConfigLoader.addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(),
                                      help="Number of jobs to use for compiling")  # type: int

    def loadAllOptions(self):
        for i in ConfigLoader.options.values():
            # noinspection PyProtectedMember
            i.__get__(self, i._owningClass if i._owningClass else self)  # force loading of lazy value

    def dumpOptionsJSON(self):
        self.loadAllOptions()
        # TODO: remove ConfigLoader.values, this just slows down stuff
        print(json.dumps(ConfigLoader.values, sort_keys=True, cls=MyJsonEncoder, indent=4))

    def __init__(self, availableTargets: list):
        ConfigLoader._cheriConfig = self
        self.targets = ConfigLoader.loadTargets(availableTargets)
        self.makeJFlag = "-j" + str(self.makeJobs)

        self.cheriBitsStr = str(self.cheriBits)
        # Set CHERI_BITS variable to allow e.g. { cheribsd": { "install-directory": "~/rootfs${CHERI_BITS}" } }
        os.environ["CHERI_BITS"] = self.cheriBitsStr

        # now the derived config options
        self.sdkDirectoryName = "sdk" + self.cheriBitsStr
        self.sdkDir = self.outputRoot / self.sdkDirectoryName  # qemu and binutils (and llvm/clang)
        self.otherToolsDir = self.outputRoot / "bootstrap"
        self.dollarPathWithOtherTools = str(self.otherToolsDir / "bin") + ":" + os.getenv("PATH")
        self.sdkSysrootDir = self.sdkDir / "sysroot"
        self.sysrootArchiveName = "cheri-sysroot.tar.gz"
