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
import fcntl
import json
import os
import shutil
import subprocess
import sys
import traceback
from collections import OrderedDict

# noinspection PyUnresolvedReferences
from pathlib import Path

from .config.defaultconfig import CheribuildAction, DefaultCheribuildConfigLoader, DefaultCheriConfig

# First thing we need to do is set up the config loader (before importing anything else!)
# We can't do from .configloader import ConfigLoader here because that will only update the local copy!
# https://stackoverflow.com/questions/3536620/how-to-change-a-module-variable-from-another-module
from .config.loader import ConfigOptionBase, MyJsonEncoder
from .processutils import get_program_version, print_command, run_and_kill_children_on_exit, run_command

# make sure all projects are loaded so that target_manager gets populated
# noinspection PyUnresolvedReferences
from .projects import *  # noqa: F401, F403, RUF100

# noinspection PyUnresolvedReferences
from .projects.cross import *  # noqa: F401, F403, RUF100
from .projects.repository import GitRepository
from .projects.simple_project import SimpleProject
from .targets import Target, target_manager
from .utils import (
    AnsiColour,
    coloured,
    fatal_error,
    have_working_internet_connection,
    init_global_config,
    query_yes_no,
    status_update,
)

DIRS_TO_CHECK_FOR_UPDATES: "list[Path]" = [Path(__file__).parent.parent]


def update_check(config: DefaultCheriConfig) -> None:
    for d in DIRS_TO_CHECK_FOR_UPDATES:
        _update_check(config, d)


def _update_check(config: DefaultCheriConfig, d: Path) -> None:
    if not shutil.which("git"):
        return
    # Avoid update check if we don't have an internet connection
    if not have_working_internet_connection(config):
        return
    # check if new commits are available
    project_dir = str(d)
    run_command(["git", "fetch"], cwd=project_dir, timeout=5, config=config)
    branch_info = GitRepository.get_branch_info(d)
    if branch_info is not None and branch_info.upstream_branch == "master":
        if query_yes_no(config, f"The local {branch_info.local_branch} branch is tracking the obsolete remote 'master'"
                                f" branch, would you like to switch to 'main'?", force_result=False):
            # Update the remote ref to point to "main".
            run_command("git", "branch", f"--set-upstream-to={branch_info.remote_name}/main", cwd=d)
            if branch_info.local_branch == "master":
                # And rename master to main if possible.
                run_command("git", "branch", "-m", "main", cwd=d, allow_unexpected_returncode=True)

    output = run_command(["git", "status", "-uno"], cwd=project_dir, config=config, capture_output=True,
                         print_verbose_only=True).stdout
    behind_index = output.find(b"Your branch is behind ")
    if behind_index > 0:
        msg_end = output.find(b"\n  (use \"git pull\" to update your local branch)")
        if msg_end > 0:
            output = output[behind_index:msg_end]
        status_update("Current", d.name, "checkout can be updated:", output.decode("utf-8"))
        if query_yes_no(config, "Would you like to update before continuing?", force_result=False):
            git_version = get_program_version(Path(shutil.which("git") or "git"), config=config)
            # Use the autostash flag for Git >= 2.14
            # https://stackoverflow.com/a/30209750/894271
            autostash_flag = ["--autostash"] if git_version >= (2, 14) else []
            run_command(["git", "pull", "--rebase", *autostash_flag], cwd=project_dir, config=config)
            os.execv(sys.argv[0], sys.argv)


def ensure_fd_is_blocking(fd) -> None:
    flag = fcntl.fcntl(fd, fcntl.F_GETFL)
    if flag & os.O_NONBLOCK:
        # Try to unset the flag (this appears to happen on macOS sometimes):
        fcntl.fcntl(fd, fcntl.F_SETFL, flag & ~os.O_NONBLOCK)
    flag = fcntl.fcntl(fd, fcntl.F_GETFL)
    if flag & os.O_NONBLOCK:
        fatal_error("fd", fd, "is set to nonblocking and could not unset flag", pretend=False)


def check_not_root() -> None:
    if os.geteuid() == 0:
        fatal_error("You are running cheribuild as root. This is dangerous, bad practice and can cause builds to fail."
                    " Please re-run as a non-root user.", pretend=False)


# noinspection PyProtectedMember
def get_config_option_value(option: ConfigOptionBase, config: DefaultCheriConfig) -> str:
    if option._owning_class is not None:
        project_cls: "type[SimpleProject]" = option._owning_class
        Target.instantiating_targets_should_warn = False
        t = target_manager.get_target(project_cls.target, None, config, caller="get_config_option")
        obj = t._get_or_create_project_no_setup(None, config, caller=None)
        return option.__get__(obj, option._owning_class)
    # otherwise it must be a config option on CheriConfig:
    return option.__get__(config, type(config))


def real_main() -> None:
    # avoid weird errors with macos terminal:
    ensure_fd_is_blocking(sys.stdin.fileno())
    ensure_fd_is_blocking(sys.stdout.fileno())
    ensure_fd_is_blocking(sys.stderr.fileno())

    config_loader = DefaultCheribuildConfigLoader()
    # Don't suggest deprecated names when tab-completing
    if config_loader.is_completing_arguments:
        all_target_names = list(sorted(target_manager.non_deprecated_target_names(None)))
    else:
        all_target_names = list(sorted(target_manager.target_names(None)))
    run_everything_target = "__run_everything__"
    # Register all command line options
    cheri_config = DefaultCheriConfig(config_loader, [*all_target_names, run_everything_target])
    # Make sure nothing other than the config loader uses this as it will include disabled target names
    del all_target_names
    SimpleProject._config_loader = config_loader
    target_manager.register_command_line_options()
    # load them from JSON/cmd line
    cheri_config.load()
    if not cheri_config.allow_running_as_root:
        check_not_root()
    init_global_config(cheri_config)
    if config_loader.get_config_prefix() == "docker-":
        cheri_config.docker = True

    if CheribuildAction.LIST_TARGETS in cheri_config.action:
        # Skip target aliases to avoid printing too much output
        names = list(target_manager.non_alias_target_names(cheri_config))
        print("There are", len(names), "available targets:\n ", "\n  ".join(names))
        sys.exit()
    elif CheribuildAction.DUMP_CONFIGURATION in cheri_config.action:
        json_dict = OrderedDict()
        cheri_config.pretend = True
        cheri_config.quiet = True
        for v in cheri_config.loader.options.values():
            try:
                json_dict[v.full_option_name] = get_config_option_value(v, cheri_config)
            except LookupError:
                if cheri_config.debug_output:
                    print("Skipping alias config option", v.full_option_name, file=sys.stderr)
                continue
        json.dump(json_dict, sys.stdout, sort_keys=True, cls=MyJsonEncoder, indent=4)
        sys.exit()
    elif cheri_config.get_config_option:
        if cheri_config.get_config_option not in config_loader.options:
            fatal_error("Unknown config key", cheri_config.get_config_option, pretend=False)
        cheri_config.pretend = True
        cheri_config.quiet = True
        option = config_loader.options[cheri_config.get_config_option]
        print(get_config_option_value(option, cheri_config))
        sys.exit()

    assert any(x in cheri_config.action for x in (CheribuildAction.TEST, CheribuildAction.BUILD,
                                                  CheribuildAction.BENCHMARK))

    if cheri_config.docker:
        cheribuild_dir = str(Path(__file__).absolute().parent.parent)
        # we can't pass all args
        filtered_cheribuild_args = ["--source-root", "/source", "--build-root", "/build", "--output-root", "/output"]
        skip_next = False
        excluded_args = ("--source-root", "--build-root", "--output-root", "--docker-container")
        for arg in sys.argv[1:]:
            if skip_next:
                skip_next = False
                continue
            if arg in excluded_args:
                skip_next = True
                continue
            if any(arg.startswith(s + "=") for s in excluded_args):
                continue
            if arg == "--docker" or arg == "--docker-reuse-container":
                continue
            filtered_cheribuild_args.append(arg)
        try:
            docker_dir_mappings = [
                # map cheribuild and the sources read-only into the container
                "-v", cheribuild_dir + ":/cheribuild:ro",
                "-v", str(cheri_config.source_root.absolute()) + ":/source",
                # build and output are read-write:
                "-v", str(cheri_config.build_root.absolute()) + ":/build",
                "-v", str(cheri_config.output_root.absolute()) + ":/output",
                ]
            cheribuild_args = ["/cheribuild/cheribuild.py", "--skip-update", *filtered_cheribuild_args]
            if cheri_config.docker_reuse_container:
                # Use docker restart + docker exec instead of docker run
                # FIXME: docker restart doesn't work for some reason
                stop_cmd = ["docker", "stop", cheri_config.docker_container]
                print_command(stop_cmd)
                subprocess.check_call(stop_cmd)
                start_cmd = ["docker", "start", cheri_config.docker_container]
                print_command(start_cmd)
                subprocess.check_call(start_cmd)
                docker_run_cmd = ["docker", "exec", cheri_config.docker_container, *cheribuild_args]
            else:
                docker_run_cmd = ["docker", "run", "--user", str(os.getuid()) + ":" + str(os.getgid()), "--rm",
                                  "--interactive", "--tty", *docker_dir_mappings]
                docker_run_cmd += [cheri_config.docker_container, *cheribuild_args]
            run_command(docker_run_cmd, config=cheri_config, give_tty_control=True)
        except subprocess.CalledProcessError as e:
            # if the image is missing print a helpful error message:
            if e.returncode == 125:
                status_update("It seems like the docker image", cheri_config.docker_container, "was not found.")
                status_update("In order to build the default docker image for cheribuild (cheribuild-docker) run:")
                print(
                    coloured(AnsiColour.blue, "cd",
                             cheribuild_dir + "/docker && docker build --tag cheribuild-docker ."))
                sys.exit(coloured(AnsiColour.red, "Failed to start docker!"))
            raise
        sys.exit()

    if run_everything_target in cheri_config.targets:
        cheri_config.targets = list(sorted(target_manager.target_names(cheri_config)))
    if not cheri_config.targets:
        # Make --libcheri-buildenv and --buildenv without any targets imply cheribsd
        if cheri_config.libcompat_buildenv or cheri_config.buildenv:
            cheri_config.targets.append("cheribsd")
        else:
            fatal_error("At least one target name is required (see --list-targets).", pretend=False)

    if not cheri_config.quiet:
        print("Sources will be stored in", cheri_config.source_root)
        print("Build artifacts will be stored in", cheri_config.output_root)

    # create the required directories
    for d in (cheri_config.source_root, cheri_config.output_root, cheri_config.build_root):
        if d.exists():
            continue
        if not cheri_config.pretend:
            if cheri_config.verbose:
                print_command("mkdir", "-p", str(d))
            d.mkdir(parents=True, exist_ok=True)

    # Don't do the update check when tab-completing (otherwise it freezes)
    if "_ARGCOMPLETE" not in os.environ and not cheri_config.skip_update:  # no-combine
        try:  # no-combine
            update_check(cheri_config)  # no-combine
        except Exception as e:  # no-combine
            print("Failed to check for updates:", e)  # no-combine
    chosen_targets = target_manager.get_all_chosen_targets(cheri_config)
    if cheri_config.print_targets_only:
        print("Will execute the following", len(chosen_targets), "targets:\n  ",
              "\n   ".join(t.name for t in chosen_targets))
        # If --verbose is passed, also print the dependencies for each target
        if cheri_config.verbose:
            needed_by = {k.name: [] for k in chosen_targets}
            direct_deps = dict()
            for target in chosen_targets:
                # noinspection PyProtectedMember
                direct_deps[target.name] = [t.name for t in target.project_class._direct_dependencies(
                    cheri_config, include_sdk_dependencies=True, include_toolchain_dependencies=True,
                    explicit_dependencies_only=False)]
                for dep in direct_deps[target.name]:
                    needed_by[dep].append(target.name)
            for target in chosen_targets:
                status_update("Will build target", coloured(AnsiColour.yellow, target.name))
                print("    Needed by:", needed_by[target.name])
                print("    Direct dependencies:", direct_deps[target.name])
        return
    if CheribuildAction.BUILD in cheri_config.action:
        target_manager.run(cheri_config, chosen_targets)
    if CheribuildAction.TEST in cheri_config.action:
        for target in chosen_targets:
            target.run_tests(cheri_config)
    if CheribuildAction.BENCHMARK in cheri_config.action:
        for target in chosen_targets:
            target.run_benchmarks(cheri_config)


def main() -> None:
    try:
        run_and_kill_children_on_exit(real_main)
    except Exception as e:
        # If we are currently debugging, raise the exception to allow e.g. PyCharm's
        # "break on exception that terminates execution" feature works.
        debugger_attached = getattr(sys, 'gettrace', lambda: None)() is not None
        if debugger_attached:
            raise e
        else:
            traceback.print_exc()
            fatal_error("Unhandled exception:", e, fatal_when_pretending=True, pretend=False)


if __name__ == "__main__":
    main()
