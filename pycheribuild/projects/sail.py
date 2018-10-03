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
from ..utils import runCmd, setEnv, coloured, AnsiColour, commandline_to_str, printCommand
from subprocess import CalledProcessError
import shlex

class OpamMixin(object):
    @property
    def opamroot(self):
        return self.config.sdkDir / "opamroot"

    def _opam_cmd(self, command, *args):
        cmdline = ["opam", command, "--root=" + str(self.opamroot)]
        cmdline.extend(args)
        return commandline_to_str(cmdline)

    def run_opam_cmd(self, command, *args, ignoreErrors=False, **kwargs):
        command_str = self._opam_cmd(command, *args)
        try:
            return self.run_in_ocaml_env(command_str, **kwargs)
        except CalledProcessError:
            if ignoreErrors:
                self.verbose_print("Ignoring non-zero exit code from " + coloured(AnsiColour.yellow, command_str))
            else:
                raise

    def run_in_ocaml_env(self, command: str, cwd=None, printVerboseOnly=False, **kwargs):
        if cwd is None:
            cwd = self.sourceDir if getattr(self, "sourceDir") else "/"
        script = "eval `opam config env`\n" + command + "\n"
        self.verbose_print("Running shell script in ocaml env:", coloured(AnsiColour.cyan, command))
        with setEnv(GIT_TEMPLATE_DIR="", # see https://github.com/ocaml/opam/issues/3493
                    OPAMROOT=self.opamroot):
            if not (self.opamroot / "opam-init").exists():
                runCmd("opam", "init", "--no-setup")
            flags = "-xe" if self.config.verbose else "-e"
            printCommand("run-in-cheribuild-opam-env", command, cwd=cwd, printVerboseOnly=printVerboseOnly)
            return runCmd("sh", flags, cwd=cwd, input=script, printVerboseOnly=True, **kwargs)

REMS_OPAM_REPO = "https://github.com/rems-project/opam-repository.git"

class BuildSailFromOpam(SimpleProject, OpamMixin):
    target = "sail-from-opam"
    # repository = "https://github.com/rems-project/sail"
    # gitBranch = "sail2"

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._addRequiredSystemTool("z3", homebrew="z3 --without-python@2 --with-python")
        self._addRequiredSystemTool("opam", homebrew="opam")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.use_git_version = cls.addBoolOption("use-git-version", showHelp=False, default=False,
                                                help="Install sail from github instead of using the latest released version")

    def process(self):
        self.run_in_ocaml_env(self._opam_cmd("switch", "4.06.0") + " || " + self._opam_cmd("switch", "create", "4.06.0"))
        repos = self.run_opam_cmd("repository", "list", captureOutput=True)
        if REMS_OPAM_REPO not in repos.stdout.decode("utf-8"):
            self.run_opam_cmd("repository", "add", "rems", REMS_OPAM_REPO)
        else:
            self.info("REMS opam repo already added")
        if self.config.clean:
            self.run_opam_cmd("uninstall", "--verbose", "sail", "--destdir=" + str(self.config.sdkDir / "sailprefix"))
            self.run_opam_cmd("uninstall", "--verbose", "sail")

        # ensure sail isn't pinned
        self.run_opam_cmd("pin", "remove", "sail", "--no-action")
        if self.use_git_version:
            # Force installation from latest git (pin repo now, but pass --no-acton since we need to install with --destdir)
            self.run_opam_cmd("pin", "add", "sail", "https://github.com/rems-project/sail.git", "--verbose", "--no-action")
        try:
            self.run_opam_cmd("install", "-y", "--verbose", "sail", "--destdir=" + str(self.config.sdkDir))
        finally:
            # reset the pin status even if the pinning failed
            self.run_opam_cmd("pin", "remove", "sail", "--no-action")

        if False:
            self.run_opam_cmd("install", "-y", "--verbose", "sail")
            opamroot_sail_binary = self.opamroot / "4.06.0/bin/sail"
            runCmd(opamroot_sail_binary, "-v")
            self.createSymlink(opamroot_sail_binary, self.config.sdkBinDir / opamroot_sail_binary.name)


class BuildSail(TargetAliasWithDependencies):
    # alias target to build both sail and the CHERI-MIPS model
    target = "sail"
    dependencies = ["sail-from-opam", "sail-cheri-mips"]


class BuildSailCheriMips(Project, OpamMixin):
    target = "sail-cheri-mips"
    projectName = "sail-cheri-mips"
    repository = "https://github.com/CTSRD-CHERI/sail-cheri-mips"
    dependencies = ["sail-from-opam"]
    defaultInstallDir = Project._installToSDK
    defaultBuildDir = Project.defaultSourceDir  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.with_trace_support = cls.addBoolOption("trace-support", showHelp=True,
                                help="Build sail-cheri-mips simulators with tracing support (they will be slow but"
                                     "the traces are useful to debug failing tests)", default=False)

    def compile(self, cwd: Path = None):
        # self.make_args.set(SAIL_DIR=self.config.sdkBinDir)
        # self.make_args.set(SAIL_DIR=self.config.sdkDir / "share/sail", SAIL=self.config.sdkBinDir / "sail")
        if self.with_trace_support:
            self.make_args.set(TRACE="yes")
        cmd = [self.make_args.command, self.config.makeJFlag, "all"] + self.make_args.all_commandline_args
        self.run_in_ocaml_env(commandline_to_str(cmd), cwd=self.sourceDir)

    def install(self, **kwargs):
        self.make_args.set(INSTALL_DIR=self.config.sdkDir)
        self.runMake("install")

# Old way of installing sail:
class OcamlProject(Project, OpamMixin):
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

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        for pkg in self.needed_ocaml_packages:
            try:
                self.run_in_ocaml_env("ocamlfind query " + shlex.quote(pkg), cwd="/", printVerboseOnly=True)
            except CalledProcessError:
                self.dependencyError("missing opam package " + pkg,
                                     installInstructions="Try running `" + self._opam_cmd("install") + pkg + "`")

    def install(self, **kwargs):
        pass

    def process(self):
        try:
            # This is run before cloning so the source dir might not exist -> set CWD to /
            self.run_in_ocaml_env("ocamlfind ocamlc -where", cwd="/", printVerboseOnly=True)
        except CalledProcessError as e:
            self.warning(e)
            self.warning("stderr was:", e.stderr)
            self.dependencyError("OCaml env seems to be messed up. Note: On MacOS homebrew OCaml "
                                 "is not installed correctly. Try installing it with opam instead:",
                                 installInstructions="Try running `" + self._opam_cmd("update") + " && " +
                                                     self._opam_cmd("switch") + " 4.06.0`")
        super().process()


class BuildSailFromSource(OcamlProject):
    target = "sail-from-source"
    repository = "https://github.com/rems-project/sail"
    gitBranch = "sail2"
    dependencies = ["lem", "ott", "linksem"]
    needed_ocaml_packages = OcamlProject.needed_ocaml_packages + ["zarith", "lem", "linksem"]
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
            self.dependencyError("missing opam package " + pkg,
                                 installInstructions="Try running `opam install menhir`")

    def compile(self, cwd: Path = None):
        self.run_in_ocaml_env("""
make
make -C mips mips mips_c
make -C cheri cheri cheri_c
make -C cheri cheri128 cheri128_c""")

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
