#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright 2023 Alex Richardson
# Copyright 2023 Google LLC
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
import functools
import subprocess
from pathlib import Path

__all__ = [
    "generate_ssh_config_file_for_qemu",
    "ssh_config_parameters",
    "ssh_host_accessible_cached",
    "ssh_host_accessible_uncached",
]

from .filesystemutils import FileSystemUtils
from .processutils import run_command
from .utils import ConfigBase, warning_message


def generate_ssh_config_file_for_qemu(
    *,
    ssh_port: int,
    ssh_key: Path,
    instance_name: str = "cheribsd-test-instance",
    ssh_user="root",
    config: ConfigBase,
) -> str:
    FileSystemUtils(config).makedirs(Path.home() / ".ssh/controlmasters")
    return f"""
    Host {instance_name}
            User {ssh_user}
            HostName localhost
            Port {ssh_port}
            IdentityFile {Path(ssh_key).with_suffix("")}
            # avoid errors due to changed host key:
            UserKnownHostsFile /dev/null
            StrictHostKeyChecking no
            NoHostAuthenticationForLocalhost yes
            # faster connection by reusing the existing one:
            ControlPath {Path.home()}/.ssh/controlmasters/%r@%h:%p
            # ConnectTimeout 20
            # ConnectionAttempts 2
            ControlMaster auto
            # Keep socket open for 10 min (600) or indefinitely (yes)
            ControlPersist 600
    """


@functools.lru_cache(maxsize=20)
def ssh_config_parameters(host: str, config: ConfigBase) -> "dict[str, str]":
    output = run_command(
        ["ssh", "-G", host],
        capture_output=True,
        run_in_pretend_mode=True,
        config=config,
    ).stdout.decode("utf-8")
    lines = output.splitlines()
    return {k: v for k, v in (line.split(maxsplit=1) for line in lines)}


@functools.lru_cache(maxsize=20)
def ssh_host_accessible_cached(
    host: str,
    *,
    ssh_args: "tuple[str, ...]",
    config: ConfigBase,
    run_in_pretend_mode: bool = True,
) -> bool:
    return ssh_host_accessible_uncached(host, ssh_args=ssh_args, config=config, run_in_pretend_mode=run_in_pretend_mode)


def ssh_host_accessible_uncached(
    host: str,
    *,
    ssh_args: "tuple[str, ...]",
    config: ConfigBase,
    run_in_pretend_mode: bool = True,
) -> bool:
    assert host, "Passed empty SSH hostname!"
    try:
        result = run_command(
            ["ssh", host, *ssh_args, "--", "echo", "connection successful"],
            capture_output=True,
            run_in_pretend_mode=run_in_pretend_mode,
            raise_in_pretend_mode=True,
            config=config,
        )
        if config.pretend and not run_in_pretend_mode:
            return True
        output = result.stdout.decode("utf-8").strip()
        return output == "connection successful"
    except subprocess.CalledProcessError as e:
        warning_message(f"SSH host '{host}' is not accessible:", e)
        return False
