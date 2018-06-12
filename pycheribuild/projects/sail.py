#
# Copyright (c) 2018 Alex Richardson
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
from .project import *
from ..utils import runCmd, setEnv, coloured, AnsiColour
from subprocess import CalledProcessError
import shlex


class OcamlProject(Project):
    doNotAddToTargets = True
    defaultInstallDir = Project._installToSDK
    defaultBuildDir = Project.defaultSourceDir
    make_kind = MakeCommandKind.GnuMake
    needed_ocaml_packages = ["ocamlbuild"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # The homebrew version of ocaml doesn't seem compatible -> suggest --without-ocaml --without-aspcud
        # This avoids pulling in incompatible ocaml and the python@2 formula
        # self._addRequiredSystemTool("opam", homebrew="opam --without-ocaml --without-camlp4 --without-aspcud")
        self._addRequiredSystemTool("opam",
            homebrew="Installing with hombrew generates a broken ocaml env, use this instead: "
                     "`wget https://raw.github.com/ocaml/opam/master/shell/opam_installer.sh -O - | sh -s /usr/local/bin`")

    def run_in_ocaml_env(self, command: str, cwd=None, printVerboseOnly=False):
        if cwd is None:
            cwd = self.sourceDir
        script = "set -xe\neval `opam config env`\n" + command + "\n"
        self.verbose_print("Running shell script in ocaml env: ", coloured(AnsiColour.cyan, command))
        runCmd("sh", cwd=cwd, input=script, printVerboseOnly=printVerboseOnly)

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        try:
            # This is run before cloning so the source dir might not exist -> set CWD to /
            self.run_in_ocaml_env("ocamlfind ocamlc -where", cwd="/", printVerboseOnly=True)
        except CalledProcessError as e:
            self.warning(e)
            self.warning("stderr was:", e.stderr)
            self.dependencyError("OCaml env seems to be messed up. Note: On MacOS homebrew OCaml "
                                 "is not installed correctly. Try installing it with opam instead:",
                                 installInstructions="Try running `opam update && opam switch 4.05.0`")
        for pkg in self.needed_ocaml_packages:
            try:
                self.run_in_ocaml_env("ocamlfind query " + shlex.quote(pkg), cwd="/", printVerboseOnly=True)
            except CalledProcessError:
                self.dependencyError("missing opam package" + pkg,
                                     installInstructions="Try running `opam install " + pkg + "`")

    def install(self, **kwargs):
        pass


class BuildSail(OcamlProject):
    repository = "https://github.com/rems-project/sail"
    gitBranch = "sail2"
    dependencies = ["lem", "ott", "linksem"]
    needed_ocaml_packages = OcamlProject.needed_ocaml_packages + ["zarith"]
    # TODO: `opam install linenoise` for isail?

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._addRequiredSystemTool("z3", homebrew="z3 --without-python@2 --with-python")

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        try:
            # opam and ocamlfind don't agree for menhir
            self.run_in_ocaml_env("ocamlfind query menhirLib", cwd="/", printVerboseOnly=True)
        except CalledProcessError:
            self.dependencyError("missing opam package" + pkg,
                                 installInstructions="Try running `opam install menhir`")

    def compile(self, cwd: Path = None):
        self.run_in_ocaml_env("""
make
make -C mips mips
make -C cheri cheri
make -C cheri cheri128""")

    def process(self):
        lemdir = BuildLem.getSourceDir(self, self.config)
        ottdir = BuildOtt.getSourceDir(self, self.config)
        linksemdir = BuildLinksem.getSourceDir(self, self.config)
        with setEnv(LEMLIB= lemdir / "library",
                    PATH="{}:{}:".format(ottdir / "bin", lemdir / "bin") + os.environ["PATH"],
                    OCAMLPATH="{}:{}".format(lemdir / "ocaml-lib/local", linksemdir / "src/local")
                    ):
            super().process()


class BuildLem(OcamlProject):
    repository = "https://github.com/rems-project/lem"
    needed_ocaml_packages = OcamlProject.needed_ocaml_packages + ["zarith"]

    def compile(self, cwd: Path = None):
        # Note: this all breaks on MacOS if ocamlfind is installed via opam
        self.run_in_ocaml_env("make && make -C ocaml-lib local-install")

    def install(self, **kwargs):
        pass


class BuildOtt(OcamlProject):
    repository = "https://github.com/ott-lang/ott"

    def compile(self, cwd: Path = None):
        self.run_in_ocaml_env("make")


class BuildLinksem(OcamlProject):
    repository = "https://github.com/rems-project/linksem"
    dependencies = ["lem", "ott"]

    def compile(self, cwd: Path = None):
        self.run_in_ocaml_env("""
make USE_OCAMLBUILD=false
make -C src USE_OCAMLBUILD=false local-install
        """)

    def process(self):
        lemdir = BuildLem.getSourceDir(self, self.config)
        ottdir = BuildOtt.getSourceDir(self, self.config)
        # linksemdir = BuildLinkSem.getSourceDir(self, self.config)
        with setEnv(LEMLIB= lemdir / "library",
                    PATH="{}:{}:".format(ottdir / "bin", lemdir / "bin") + os.environ["PATH"],
                    OCAMLPATH=lemdir / "ocaml-lib/local"):
            super().process()

"""
'''
eval `opam config env`
opam list
ulimit -s unlimited

#tar xjf binutils.tar.bz2
#tar xJf cheri-multi-master-clang-llvm.tar.xz
export PATH=${WORKSPACE}/ott/bin:${WORKSPACE}/lem/bin:${PATH}
export OCAMLPATH=${WORKSPACE}/lem/ocaml-lib/local:${WORKSPACE}/linksem/src/local
export LEMLIB=${WORKSPACE}/lem/library
'''

"""