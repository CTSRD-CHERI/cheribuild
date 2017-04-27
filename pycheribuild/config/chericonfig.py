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
import json
from collections import OrderedDict
from pathlib import Path
# Need to import loader here and not `from loader import ConfigLoader` because that copies the reference
from . import loader as conf
from ..utils import latestClangTool, defaultNumberOfMakeJobs, warningMessage


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
    def __init__(self, loader: conf.ConfigLoaderBase):
        loader._cheriConfig = self
        self.loader = loader
        self.pretend = loader.addCommandLineOnlyBoolOption("pretend", "p",
                                                           help="Only print the commands instead of running them")
        self.clangPath = loader.addPathOption("clang-path", default=latestClangTool("clang"),
                                              help="The Clang C compiler to use for compiling "
                                                   "LLVM+Clang (must be at least version 3.7)")
        self.clangPlusPlusPath = loader.addPathOption("clang++-path", default=latestClangTool("clang++"),
                                                      help="The Clang C++ compiler to use for compiling "
                                                           "LLVM+Clang (must be at least version 3.7)")
        # Attributes for code completion:
        self.verbose = None  # type: bool
        self.quiet = None  # type: bool
        self.clean = None  # type: bool
        self.force = None  # type: bool
        self.noLogfile = None  # type: bool
        self.skipUpdate = None  # type: bool
        self.skipConfigure = None  # type: bool
        self.forceConfigure = None  # type: bool
        self.skipInstall = None  # type: bool
        self.skipInstall = None  # type: bool
        self.includeDependencies = None  # type: bool
        self.createCompilationDB = None  # type: bool
        self.crossCompileForMips = None  # type: bool
        self.makeWithoutNice = None  # type: bool

        self.cheriBits = None  # type: int
        self.makeJobs = None  # type: int

        self.sourceRoot = None  # type: Path
        self.outputRoot = None  # type: Path
        self.buildRoot = None  # type: Path
        self.sdkDir = None  # type: Path
        self.otherToolsDir = None  # type: Path
        self.dollarPathWithOtherTools = None  # type: Path
        self.sdkSysrootDir = None  # type: Path
        self.sysrootArchiveName = None  # type: Path

        self.targets = None  # type: list

    def load(self):
        self.loader.load()
        self.targets = self.loader.targets

    @property
    def makeJFlag(self):
        return str(self.makeJobs)

    @property
    def cheriBitsStr(self):
        return str(self.cheriBits)

    @property
    def sdkDirectoryName(self):
        return "sdk" + self.cheriBitsStr

    def _ensureRequiredPropertiesSet(self) -> bool:
        for key in self.__dict__.keys():
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

    def dumpOptionsJSON(self):
        jsonDict = OrderedDict()
        for v in self.loader.options.values():
            # noinspection PyProtectedMember
            jsonDict[v.fullOptionName] = v.__get__(self, v._owningClass if v._owningClass else self)
        print(json.dumps(jsonDict, sort_keys=True, cls=MyJsonEncoder, indent=4))
