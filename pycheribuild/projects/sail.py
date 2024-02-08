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
import shutil
import tempfile
import typing
from pathlib import Path
from subprocess import CalledProcessError
from typing import Any, Dict, Tuple, Union

from .project import AutotoolsProject, DefaultInstallDir, GitRepository, MakeCommandKind, Project
from .simple_project import BoolConfigOption, SimpleProject
from ..processutils import get_program_version
from ..targets import target_manager
from ..utils import AnsiColour, OSInfo, ThreadJoiner, coloured

if typing.TYPE_CHECKING:
    _MixinBase = Project
else:
    _MixinBase = object


class OpamMixin(_MixinBase):
    config = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.required_ocaml_version = "4.13.1"
        self.__using_correct_switch = False
        self.__ignore_switch_version = False

    @property
    def opamroot(self):
        return self.config.cheri_sdk_dir / "opamroot"

    def process(self):
        super().process()
        self.check_required_system_tool("opam", homebrew="opam", apt="opam", cheribuild_target="opam-2.0")
        opam_path = shutil.which("opam")
        if opam_path:
            opam_version = get_program_version(Path(opam_path), regex=b"(\\d+)\\.(\\d+)\\.?(\\d+)?", config=self.config)
            min_version = (2, 0, 8)
            if opam_version < min_version:
                install_inst = OSInfo.install_instructions(
                    "opam", False, apt="opam", cheribuild_target="opam-2.0" if OSInfo.IS_LINUX else None
                )
                self.dependency_error(
                    "Opam version",
                    ".".join(map(str, opam_version)),
                    "is too old. Need at least",
                    ".".join(map(str, min_version)),
                    install_instructions=install_inst,
                )

    @property
    def opam_binary(self):
        return shutil.which("opam") or "opam"

    def _opam_cmd(self, command, *args, _add_switch=True):
        cmdline = [self.opam_binary, command, "--cli=2.1", "--root=" + str(self.opamroot)]
        if _add_switch:
            cmdline.append("--switch=" + self.required_ocaml_version)
        cmdline.extend(args)
        return cmdline

    def _opam_cmd_str(self, command, *args, _add_switch):
        return self.commandline_to_str(self._opam_cmd(command, *args, _add_switch=_add_switch))

    def _ensure_correct_switch(self):
        if not self.__using_correct_switch and not self.__ignore_switch_version:
            self.__ignore_switch_version = True
            try:
                self.run_opam_cmd("switch", self.required_ocaml_version, _add_switch=False)
            except CalledProcessError:
                # create the switch if it doesn't exist
                self.run_opam_cmd(
                    "switch", "--verbose", "--debug", "create", self.required_ocaml_version, _add_switch=False
                )
            finally:
                self.__ignore_switch_version = False
            self.__using_correct_switch = True

    def run_opam_cmd(self, command, *args, ignore_errors=False, _add_switch=True, **kwargs):
        self._ensure_correct_switch()
        command_list = self._opam_cmd(command, *args, _add_switch=_add_switch)
        try:
            return self.run_command_in_ocaml_env(command_list, **kwargs)
        except CalledProcessError:
            if ignore_errors:
                # noinspection PyUnresolvedReferences
                self.verbose_print(
                    "Ignoring non-zero exit code from",
                    coloured(AnsiColour.yellow, self.commandline_to_str(command_list)),
                )
            else:
                raise

    def _run_in_ocaml_env_prepare(self, cwd=None) -> "Tuple[Dict[Any, Union[Union[str, int], Any]], Union[str, Any]]":
        if cwd is None:
            cwd = self.source_dir if getattr(self, "source_dir") else "/"

        self._ensure_correct_switch()
        opam_env = dict(
            GIT_TEMPLATE_DIR="",  # see https://github.com/ocaml/opam/issues/3493
            OPAMROOT=self.opamroot,
            CCACHE_DISABLE=1,  # https://github.com/ocaml/opam/issues/3395
            PATH=self.config.dollar_path_with_other_tools,
        )
        if Path(self.opam_binary).is_absolute():
            opam_env["OPAM_USER_PATH_RO"] = Path(self.opam_binary).parent
        if not (self.opamroot / "opam-init").exists():
            self.run_cmd(
                self.opam_binary,
                "init",
                "--cli=2.1",
                "--disable-sandboxing",
                "--root=" + str(self.opamroot),
                "--no-setup",
                cwd="/",
                env=opam_env,
            )
        return opam_env, cwd

    def run_in_ocaml_env(self, command: str, cwd=None, print_verbose_only=False, **kwargs):
        opam_env, cwd = self._run_in_ocaml_env_prepare(cwd=cwd)
        script = "eval `opam config env`\n" + command + "\n"
        return self.run_shell_script(script, cwd=cwd, print_verbose_only=print_verbose_only, env=opam_env, **kwargs)

    def run_command_in_ocaml_env(self, command: "list[Union[str, Path]]", cwd=None, print_verbose_only=False, **kwargs):
        self._ensure_correct_switch()
        opam_env, cwd = self._run_in_ocaml_env_prepare(cwd=cwd)
        # for opam commands we don't need to prepend opam exec --
        if command[0] != self.opam_binary:
            command = [self.opam_binary, "--cli=2.1", "exec", "--root=" + str(self.opamroot), "--", *command]
        assert isinstance(self, SimpleProject)
        return self.run_cmd(command, cwd=cwd, print_verbose_only=print_verbose_only, env=opam_env, **kwargs)


class Opam2(SimpleProject):
    target = "opam-2.0"

    def check_system_dependencies(self):
        super().check_system_dependencies()
        if OSInfo.IS_LINUX:
            self.check_required_system_tool("bwrap", cheribuild_target="bubblewrap")

    def process(self):
        if OSInfo.IS_LINUX and self.crosscompile_target.is_x86_64():
            self.makedirs(self.config.other_tools_dir / "bin")
            with tempfile.TemporaryDirectory() as td:
                base_url = "https://github.com/ocaml/opam/releases/download/"
                self.download_file(
                    Path(td, "opam"),
                    url=base_url + "2.0.8/opam-2.0.8-x86_64-linux",
                    sha256="95365a873d9e3ae6fb48e6109b5fc5df3b4e526c9d65d20652a78e263f745a35",
                )
                self.install_file(
                    Path(td, "opam"),
                    self.config.other_tools_dir / "bin/opam",
                    force=True,
                    print_verbose_only=False,
                    mode=0o755,
                )
                self.delete_file(self.config.other_tools_dir / "bin/opam.downloaded", print_verbose_only=False)
        else:
            self.fatal(
                "This target is only implement for Linux x86_64, for others operating systems you will have"
                " to install opam 2.0 manually"
            )


class BuildBubbleWrap(AutotoolsProject):
    target = "bubblewrap"
    repository = GitRepository("https://github.com/projectatomic/bubblewrap")
    native_install_dir = DefaultInstallDir.BOOTSTRAP_TOOLS

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_header("sys/capability.h", apt="libcap-dev")

    def setup(self):
        super().setup()
        self.configure_args.append("--with-bash-completion-dir=no")


class ProjectUsingOpam(OpamMixin, Project):
    do_not_add_to_targets = True


class BuildSailFromOpam(ProjectUsingOpam):
    target = "sail"
    repository = GitRepository("https://github.com/rems-project/sail", default_branch="sail2")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake
    use_git_version = BoolConfigOption(
        "use-git-version", help="Install sail from github instead of using the latest released version"
    )

    def check_system_dependencies(self):
        super().check_system_dependencies()
        self.check_required_system_tool("z3", homebrew="z3 --without-python@2 --with-python")

    def clean(self) -> ThreadJoiner:
        return ThreadJoiner(None)

    def update(self):
        self.run_opam_cmd("update")
        if not self.use_git_version:
            return
        super().update()

    def compile(self, **kwargs):
        pass

    def install(self, **kwargs):
        destdir_flag = "--destdir=" + str(self.install_dir / "sailprefix")
        # Remove the old sail installation
        self.run_opam_cmd("uninstall", "--verbose", "sail", destdir_flag)
        self.run_opam_cmd("uninstall", "--verbose", "sail")
        # Remove libsail+other subpackages for sail 0.15+
        self.run_opam_cmd("uninstall", "--verbose", "libsail", destdir_flag)
        self.run_opam_cmd("uninstall", "-y", "--verbose", "libsail", ignore_errors=True)
        # ensure sail isn't pinned
        self.run_opam_cmd("pin", "remove", "sail", "--no-action")

        if not self.skip_update:
            self.run_opam_cmd("update")

        install_flags = ["-y", "--verbose", "--keep-build-dir", "--with-test", destdir_flag]
        if not self.with_clean:
            install_flags.append("--reuse-build-dir")
        if self.use_git_version:
            self.run_opam_cmd("install", *install_flags, ".")  # Force installation from latest git
        else:
            self.run_opam_cmd("install", *install_flags, "sail")
        opamroot_sail_binary = self.opamroot / self.required_ocaml_version / "bin/sail"
        self.run_cmd(opamroot_sail_binary, "-v")
        self.create_symlink(opamroot_sail_binary, self.config.cheri_sdk_bindir / opamroot_sail_binary.name)


target_manager.add_target_alias("sail-from-opam", "sail", deprecated=True)


class BuildSailCheriMips(ProjectUsingOpam):
    target = "sail-cheri-mips"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sail-cheri-mips")
    dependencies = ("sail",)
    native_install_dir = DefaultInstallDir.CHERI_SDK
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake
    with_trace_support = BoolConfigOption(
        "trace-support",
        help="Build sail-cheri-mips simulators with tracing support (slow, but useful to debug failing tests)",
    )

    def check_system_dependencies(self):
        super().check_system_dependencies()
        self.check_required_system_header("gmp.h", homebrew="gmp", apt="libgmp-dev")

    def compile(self, **kwargs):
        if self.with_trace_support:
            self.make_args.set(TRACE="yes")
        cmd = [
            self.make_args.command,
            self.config.make_j_flag,
            "all",
            *self.make_args.all_commandline_args(self.config),
        ]
        self.run_command_in_ocaml_env(cmd, cwd=self.source_dir)

    def install(self, **kwargs):
        self.make_args.set(INSTALL_DIR=self.config.cheri_sdk_dir)
        self.run_make_install()


class RunSailShell(OpamMixin, SimpleProject):
    target = "sail-env"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sail-cheri-mips")
    dependencies = ("sail",)
    native_install_dir = DefaultInstallDir.CHERI_SDK

    def process(self):
        shell = os.getenv("SHELL", "bash")
        self.info(f"Starting sail shell (using {shell})... ")
        import subprocess

        try:
            prompt_env = {}
            if "_P9K_TTY" in os.environ or "P9K_TTY" in os.environ:
                # Set a variable that shows we are in the sail env for the default powerlevel10k prompt.
                # We could also use various other right hand side prompts, but toolbox seems unlikely to conflict.
                prompt_env["P9K_TOOLBOX_NAME"] = "sail-opam-env"
            else:
                # Otherwise set the VIRTUAL_ENV environment variable if not already present (this should hopefully
                # be visualized by many custom shell prompts)
                prompt_env["VIRTUAL_ENV"] = os.getenv("VIRTUAL_ENV", "sail-opam-env")
            with self.set_env(**prompt_env):
                self.run_command_in_ocaml_env(
                    [shell, "-c", f"echo 'Entering sail environment, send CTRL+D to exit'; exec {shell} -i"],
                    cwd=os.getcwd(),
                )
        except subprocess.CalledProcessError as e:
            if e.returncode == 130:
                return  # User pressed Ctrl+D to exit shell, don't print an error
            raise


class BuildSailRISCV(ProjectUsingOpam):
    target = "sail-riscv"
    repository = GitRepository("https://github.com/rems-project/sail-riscv")
    dependencies = ("sail",)
    native_install_dir = DefaultInstallDir.CHERI_SDK
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake

    def check_system_dependencies(self):
        super().check_system_dependencies()
        self.check_required_system_header("gmp.h", homebrew="gmp", apt="libgmp-dev")

    def compile(self, **kwargs):
        for arch in ("RV64", "RV32"):
            cmd = [
                self.make_args.command,
                self.config.make_j_flag,
                "ARCH=" + arch,
                "csim",
                "osim",
                "rvfi",
                *self.make_args.all_commandline_args(self.config),
            ]
            self.run_command_in_ocaml_env(cmd, cwd=self.source_dir)

    def install(self, **kwargs):
        self.make_args.set(INSTALL_DIR=self.config.cheri_sdk_dir)
        # self.run_make_install()
        self.info("NO INSTALL TARGET YET")


class BuildSailCheriRISCV(ProjectUsingOpam):
    target = "sail-cheri-riscv"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sail-cheri-riscv")
    dependencies = ("sail",)
    native_install_dir = DefaultInstallDir.CHERI_SDK
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake

    def check_system_dependencies(self):
        super().check_system_dependencies()
        self.check_required_system_header("gmp.h", homebrew="gmp", apt="libgmp-dev")

    def compile(self, **kwargs):
        for arch in ("RV64", "RV32"):
            cmd = [
                self.make_args.command,
                self.config.make_j_flag,
                "ARCH=" + arch,
                "csim",
                "osim",
                "rvfi",
                *self.make_args.all_commandline_args(self.config),
            ]
            self.run_command_in_ocaml_env(cmd, cwd=self.source_dir)

    def install(self, **kwargs):
        self.make_args.set(INSTALL_DIR=self.config.cheri_sdk_dir)
        # self.run_make_install()
        self.info("NO INSTALL TARGET YET")


class BuildSailMorello(ProjectUsingOpam):
    target = "sail-morello"
    repository = GitRepository("https://github.com/CTSRD-CHERI/sail-morello")
    dependencies = ("sail",)
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    build_in_source_dir = True  # Cannot build out-of-source
    make_kind = MakeCommandKind.GnuMake

    def check_system_dependencies(self):
        super().check_system_dependencies()
        self.check_required_system_header("gmp.h", homebrew="gmp", apt="libgmp-dev")

    def compile(self, **kwargs):
        cmd = [
            self.make_args.command,
            self.config.make_j_flag,
            "gen_c",
            "check_sail",
            *self.make_args.all_commandline_args(self.config),
        ]
        self.run_command_in_ocaml_env(cmd, cwd=self.source_dir)
