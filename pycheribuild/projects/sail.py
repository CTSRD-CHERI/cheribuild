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
import shlex
import shutil
from subprocess import CalledProcessError
from typing import Tuple, Dict, Any, Union

from .project import *
from ..utils import runCmd, setEnv, coloured, AnsiColour, commandline_to_str, get_program_version, IS_LINUX


class OpamMixin(object):
    config = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        assert isinstance(self, SimpleProject)
        self.addRequiredSystemTool("opam", homebrew="opam", cheribuild_target="opam-2.0")
        self.required_ocaml_version = "4.06.1"
        self.__using_correct_switch = False
        self.__ignore_switch_version = False

    @property
    def opamroot(self):
        return self.config.sdkDir / "opamroot"

    def checkSystemDependencies(self):
        assert isinstance(self, SimpleProject)
        # noinspection PyUnresolvedReferences
        super().checkSystemDependencies()
        opam_path = shutil.which("opam")
        if opam_path:
            opam_version = get_program_version(Path(opam_path), regex=b"(\\d+)\\.(\\d+)\\.?(\\d+)?")
            if opam_version < (2, 0, 0):
                self.dependencyError("Opam version", opam_version, "is too old. Need at least 2.0.0",
                                     installInstructions="Install opam 2.0 with your system package manager or run "
                                                         "`cheribuild.py opam-2.0` (Linux-only)")

    @property
    def opam_binary(self):
        return shutil.which("opam") or "opam"

    def _opam_cmd(self, command, *args, _add_switch=True):
        cmdline = [self.opam_binary, command, "--root=" + str(self.opamroot)]
        if _add_switch:
            cmdline.append("--switch=" + self.required_ocaml_version)
        cmdline.extend(args)
        return cmdline

    def _opam_cmd_str(self, command, *args, _add_switch):
        return commandline_to_str(self._opam_cmd(command, *args, _add_switch=_add_switch))

    def _ensure_correct_switch(self):
        if not self.__using_correct_switch and not self.__ignore_switch_version:
            self.__ignore_switch_version = True
            try:
                self.run_opam_cmd("switch", self.required_ocaml_version, _add_switch=False)
            except CalledProcessError:
                # create the switch if it doesn't exist
                self.run_opam_cmd("switch", "--verbose", "--debug", "create", self.required_ocaml_version,
                                  _add_switch=False)
            finally:
                self.__ignore_switch_version = False
            self.__using_correct_switch = True

    def run_opam_cmd(self, command, *args, ignoreErrors=False, _add_switch=True, **kwargs):
        self._ensure_correct_switch()
        command_list = self._opam_cmd(command, *args, _add_switch=_add_switch)
        try:
            return self.run_command_in_ocaml_env(command_list, **kwargs)
        except CalledProcessError:
            if ignoreErrors:
                # noinspection PyUnresolvedReferences
                self.verbose_print(
                    "Ignoring non-zero exit code from " + coloured(AnsiColour.yellow, commandline_to_str(command_list)))
            else:
                raise

    def _run_in_ocaml_env_prepare(self, cwd=None) -> "Tuple[Dict[Any, Union[Union[str, int], Any]], Union[str, Any]]":
        if cwd is None:
            # noinspection PyUnresolvedReferences
            cwd = self.sourceDir if getattr(self, "sourceDir") else "/"

        self._ensure_correct_switch()
        opam_env = dict(GIT_TEMPLATE_DIR="",  # see https://github.com/ocaml/opam/issues/3493
                        OPAMROOT=self.opamroot, CCACHE_DISABLE=1,  # https://github.com/ocaml/opam/issues/3395
                        PATH=self.config.dollarPathWithOtherTools)
        if Path(self.opam_binary).is_absolute():
            opam_env["OPAM_USER_PATH_RO"] = Path(self.opam_binary).parent
        if not (self.opamroot / "opam-init").exists():
            runCmd(self.opam_binary, "init", "--root=" + str(self.opamroot), "--no-setup", cwd="/", env=opam_env)
        return opam_env, cwd

    def run_in_ocaml_env(self, command: str, cwd=None, print_verbose_only=False, **kwargs):
        opam_env, cwd = self._run_in_ocaml_env_prepare(cwd=cwd)
        script = "eval `opam config env`\n" + command + "\n"
        assert isinstance(self, Project)
        return self.runShellScript(script, cwd=cwd, print_verbose_only=print_verbose_only, env=opam_env, **kwargs)

    def run_command_in_ocaml_env(self, command: list, cwd=None, print_verbose_only=False, **kwargs):
        self._ensure_correct_switch()
        opam_env, cwd = self._run_in_ocaml_env_prepare(cwd=cwd)
        # for opam commands we don't need to prepend opam exec --
        if command[0] != self.opam_binary:
            command = [self.opam_binary, "exec", "--root=" + str(self.opamroot), "--"] + command
        return runCmd(command, cwd=cwd, print_verbose_only=print_verbose_only, env=opam_env, **kwargs)


class Opam2(SimpleProject):
    target = "opam-2.0"

    def __init__(self, config):
        super().__init__(config)
        if IS_LINUX:
            self.addRequiredSystemTool("wget")
            self.addRequiredSystemTool("bwrap", cheribuild_target="bubblewrap")

    def process(self):
        if IS_LINUX:
            # NOTE: 2.0.2 won't work for me
            runCmd("wget", "https://github.com/ocaml/opam/releases/download/2.0.1/opam-2.0.1-x86_64-linux", "-O",
                   self.config.otherToolsDir / "bin/opam")
            # Make it executable
            if not self.config.pretend:
                (self.config.otherToolsDir / "bin/opam").chmod(0o755)
        else:
            self.fatal("This target is only implement for Linux x86_64, for others operating systems you will have"
                       " to install opam 2.0 manually")


class BuildBubbleWrap(AutotoolsProject):
    projectName = "bubblewrap"
    repository = GitRepository("https://github.com/projectatomic/bubblewrap")
    defaultInstallDir = AutotoolsProject._installToBootstrapTools

    def __init__(self, config):
        super().__init__(config)
        self._addRequiredSystemHeader("sys/capability.h", apt="libcap-dev")
        self.configureCommand = self.sourceDir / "autogen.sh"
        self.configureArgs.append("--with-bash-completion-dir=no")


class ProjectUsingOpam(OpamMixin, Project):
    doNotAddToTargets = True


REMS_OPAM_REPO = "https://github.com/rems-project/opam-repository.git"


class BuildSailFromOpam(OpamMixin, SimpleProject):
    target = "sail-from-opam"

    # repository = GitRepository("https://github.com/rems-project/sail")
    # gitBranch = "sail2"

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.addRequiredSystemTool("z3", homebrew="z3 --without-python@2 --with-python")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.use_git_version = cls.addBoolOption("use-git-version", showHelp=False,
                                                help="Install sail from github instead of using the latest released "
                                                     "version")

    def process(self):
        # self.run_command_in_ocaml_env(["env"])
        repos = self.run_opam_cmd("repository", "list", captureOutput=True)
        if REMS_OPAM_REPO not in repos.stdout.decode("utf-8"):
            self.run_opam_cmd("repository", "add", "rems", REMS_OPAM_REPO)
        else:
            self.info("REMS opam repo already added")

        if not self.config.skipUpdate:
            self.run_opam_cmd("update")

        if self.config.clean:
            self.run_opam_cmd("uninstall", "--verbose", "sail", "--destdir=" + str(self.config.sdkDir / "sailprefix"))
            self.run_opam_cmd("uninstall", "--verbose", "sail")

        # ensure sail isn't pinned
        self.run_opam_cmd("pin", "remove", "sail", "--no-action")
        if self.use_git_version:
            # Force installation from latest git (pin repo now, but pass --no-acton since we need to install with
            # --destdir)
            self.run_opam_cmd("pin", "add", "sail", "https://github.com/rems-project/sail.git", "--verbose",
                              "--no-action")
        try:
            self.run_opam_cmd("install", "-y", "--verbose", "sail", "--destdir=" + str(self.config.sdkDir))
            # I bet this will not work as intended... Probably better to just uninstall and reinstall
            self.run_opam_cmd("upgrade", "-y", "--verbose", "sail")  # "--destdir=" + str(self.config.sdkDir))
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


class BuildSailCheriMips(ProjectUsingOpam):
    target = "sail-cheri-mips"
    projectName = "sail-cheri-mips"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sail-cheri-mips")
    dependencies = ["sail-from-opam"]
    defaultInstallDir = Project._installToSDK
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config):
        super().__init__(config)
        self._addRequiredSystemHeader("gmp.h", homebrew="gmp", apt="libgmp-dev")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.with_trace_support = cls.addBoolOption("trace-support", showHelp=True,
                                                   help="Build sail-cheri-mips simulators with tracing support (they "
                                                        "will be slow but the traces are useful to debug failing "
                                                        "tests)")

    def compile(self, cwd: Path = None):
        # self.make_args.set(SAIL_DIR=self.config.sdkBinDir)
        # self.make_args.set(SAIL_DIR=self.config.sdkDir / "share/sail", SAIL=self.config.sdkBinDir / "sail")
        if self.with_trace_support:
            self.make_args.set(TRACE="yes")
        cmd = [self.make_args.command, self.config.makeJFlag, "all"] + self.make_args.all_commandline_args
        self.run_command_in_ocaml_env(cmd, cwd=self.sourceDir)

    def install(self, **kwargs):
        self.make_args.set(INSTALL_DIR=self.config.sdkDir)
        self.runMake("install")


class BuildSailRISCV(ProjectUsingOpam):
    target = "sail-riscv"
    projectName = "sail-riscv"
    repository = GitRepository("https://github.com/rems-project/sail-riscv")
    dependencies = ["sail-from-opam"]
    defaultInstallDir = Project._installToSDK
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config):
        super().__init__(config)
        self._addRequiredSystemHeader("gmp.h", homebrew="gmp", apt="libgmp-dev")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.with_trace_support = cls.addBoolOption("trace-support", showHelp=True,
                                                   help="Build sail-cheri-mips simulators with tracing support (they "
                                                        "will be slow but"
                                                        "the traces are useful to debug failing tests)")

    def compile(self, cwd: Path = None):
        # self.make_args.set(SAIL_DIR=self.config.sdkBinDir)
        # self.make_args.set(SAIL_DIR=self.config.sdkDir / "share/sail", SAIL=self.config.sdkBinDir / "sail")
        if self.with_trace_support:
            self.make_args.set(TRACE="yes")
        cmd = [self.make_args.command, self.config.makeJFlag, "opam-build"] + self.make_args.all_commandline_args
        self.run_command_in_ocaml_env(cmd, cwd=self.sourceDir)

    def install(self, **kwargs):
        self.make_args.set(INSTALL_DIR=self.config.sdkDir)
        # self.runMake("install")
        self.info("NO INSTALL TARGET YET")


class BuildSailCheriRISCV(ProjectUsingOpam):
    target = "sail-cheri-riscv"
    projectName = "sail-cheri-riscv"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sail-cheri-riscv")
    dependencies = ["sail-from-opam"]
    defaultInstallDir = Project._installToSDK
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake

    def __init__(self, config):
        super().__init__(config)
        self._addRequiredSystemHeader("gmp.h", homebrew="gmp", apt="libgmp-dev")

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.with_trace_support = cls.addBoolOption("trace-support", showHelp=True,
                                                   help="Build sail-cheri-mips simulators with tracing support (they "
                                                        "will be slow but the traces are useful to debug failing "
                                                        "tests)")

    def compile(self, cwd: Path = None):
        # self.make_args.set(SAIL_DIR=self.config.sdkBinDir)
        # self.make_args.set(SAIL_DIR=self.config.sdkDir / "share/sail", SAIL=self.config.sdkBinDir / "sail")
        if self.with_trace_support:
            self.make_args.set(TRACE="yes")
        cmd = [self.make_args.command, self.config.makeJFlag, "opam-build"] + self.make_args.all_commandline_args
        self.run_command_in_ocaml_env(cmd, cwd=self.sourceDir)

    def install(self, **kwargs):
        self.make_args.set(INSTALL_DIR=self.config.sdkDir)
        # self.runMake("install")
        self.info("NO INSTALL TARGET YET")


# Old way of installing sail:
class OcamlProject(OpamMixin, Project):
    doNotAddToTargets = True
    defaultInstallDir = Project._installToSDK
    build_in_source_dir = True
    make_kind = MakeCommandKind.GnuMake
    needed_ocaml_packages = ["ocamlbuild"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # The homebrew version of ocaml doesn't seem compatible -> suggest --without-ocaml --without-aspcud
        # This avoids pulling in incompatible ocaml and the python@2 formula
        # self.addRequiredSystemTool("opam", homebrew="opam --without-ocaml --without-camlp4 --without-aspcud")
        self.addRequiredSystemTool("opam",
                                   homebrew="Installing with hombrew generates a broken ocaml env, use this instead: "
                                            "`wget https://raw.github.com/ocaml/opam/master/shell/opam_installer.sh "
                                            "-O - | sh -s /usr/local/bin`")

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        for pkg in self.needed_ocaml_packages:
            try:
                self.run_in_ocaml_env("ocamlfind query " + shlex.quote(pkg), cwd="/", print_verbose_only=True)
            except CalledProcessError:
                instrs = "Try running `" + self._opam_cmd_str("install", _add_switch=False) + pkg + "`"
                self.dependencyError("missing opam package " + pkg, installInstructions=instrs)

    def install(self, **kwargs):
        pass

    def process(self):
        try:
            # This is run before cloning so the source dir might not exist -> set CWD to /
            self.run_in_ocaml_env("ocamlfind ocamlc -where", cwd="/", print_verbose_only=True)
        except CalledProcessError as e:
            self.warning(e)
            self.warning("stderr was:", e.stderr)
            self.dependencyError("OCaml env seems to be messed up. Note: On MacOS homebrew OCaml "
                                 "is not installed correctly. Try installing it with opam instead:",
                                 installInstructions="Try running `" + self._opam_cmd_str("update",
                                                                                          _add_switch=False) + " && "
                                                     + self._opam_cmd_str(
                                     "switch", _add_switch=False) + " 4.06.0`")
        super().process()


class BuildSailFromSource(OcamlProject):
    target = "sail-from-source"
    repository = GitRepository("https://github.com/rems-project/sail")
    gitBranch = "sail2"
    dependencies = ["lem", "ott", "linksem"]
    needed_ocaml_packages = OcamlProject.needed_ocaml_packages + ["zarith", "lem", "linksem"]

    # TODO: `opam install linenoise` for isail?

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.addRequiredSystemTool("z3", homebrew="z3 --without-python@2 --with-python")

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        try:
            # opam and ocamlfind don't agree for menhir
            self.run_in_ocaml_env("ocamlfind query menhirLib", cwd="/", print_verbose_only=True)
        except CalledProcessError:
            self.dependencyError("missing opam package menhirLib",
                                 installInstructions="Try running `opam install menhir`")

    def compile(self, cwd: Path = None):
        self.run_in_ocaml_env("""
make
make -C mips mips mips_c
make -C cheri cheri cheri_c
make -C cheri cheri128 cheri128_c""")

    def process(self):
        lemdir = BuildLem.getSourceDir(self)
        ottdir = BuildOtt.getSourceDir(self)
        linksemdir = BuildLinksem.getSourceDir(self)
        with setEnv(LEMLIB=lemdir / "library",
                    PATH="{}:{}:".format(ottdir / "bin", lemdir / "bin") + os.environ["PATH"],
                    OCAMLPATH="{}:{}".format(lemdir / "ocaml-lib/local", linksemdir / "src/local")):
            super().process()


class BuildLem(OcamlProject):
    repository = GitRepository("https://github.com/rems-project/lem")
    needed_ocaml_packages = OcamlProject.needed_ocaml_packages + ["zarith"]

    def compile(self, cwd: Path = None):
        # Note: this all breaks on MacOS if ocamlfind is installed via opam
        self.run_in_ocaml_env("make && make -C ocaml-lib local-install")

    def install(self, **kwargs):
        pass


class BuildOtt(OcamlProject):
    repository = GitRepository("https://github.com/ott-lang/ott")

    def compile(self, cwd: Path = None):
        self.run_in_ocaml_env("make")


class BuildLinksem(OcamlProject):
    repository = GitRepository("https://github.com/rems-project/linksem")
    dependencies = ["lem", "ott"]

    def compile(self, cwd: Path = None):
        self.run_in_ocaml_env("""
make USE_OCAMLBUILD=false
make -C src USE_OCAMLBUILD=false local-install
        """)

    def process(self):
        lemdir = BuildLem.getSourceDir(self)
        ottdir = BuildOtt.getSourceDir(self)
        # linksemdir = BuildLinkSem.getSourceDir(self)
        with setEnv(LEMLIB=lemdir / "library",
                    PATH="{}:{}:".format(ottdir / "bin", lemdir / "bin") + os.environ["PATH"],
                    OCAMLPATH=lemdir / "ocaml-lib/local"):
            super().process()
