#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
# -
# Copyright (c) 2016-2017 SRI International
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
# runtests.py - run FreeBSD tests and export them to a tarfile via a disk
# device.
#
import argparse
import atexit
import datetime
import os
import random
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import typing
from pathlib import Path

from ..config.compilation_targets import CompilationTargets, CrossCompileTarget
from ..qemu_utils import QemuOptions, riscv_bios_arguments
from ..utils import find_free_port, keep_terminal_sane

_cheribuild_root = Path(__file__).parent.parent.parent
_pexpect_dir = _cheribuild_root / "3rdparty/pexpect"
assert (_pexpect_dir / "pexpect/__init__.py").exists()
assert str(_pexpect_dir.resolve()) in sys.path, str(_pexpect_dir) + " not found in " + str(sys.path)
import pexpect  # noqa: E402

SUPPORTED_ARCHITECTURES = {x.generic_suffix: x for x in (CompilationTargets.CHERIBSD_MIPS_NO_CHERI,
                                                         CompilationTargets.CHERIBSD_MIPS_HYBRID,
                                                         CompilationTargets.CHERIBSD_MIPS_PURECAP,
                                                         CompilationTargets.CHERIBSD_RISCV_NO_CHERI,
                                                         CompilationTargets.CHERIBSD_RISCV_HYBRID,
                                                         CompilationTargets.CHERIBSD_RISCV_PURECAP,
                                                         CompilationTargets.CHERIBSD_X86_64,
                                                         CompilationTargets.CHERIBSD_AARCH64,
                                                         )}

STARTING_INIT = "start_init: trying /sbin/init"
BOOT_FAILURE = "Enter full pathname of shell or RETURN for /bin/sh"
BOOT_FAILURE2 = "wait for /bin/sh on /etc/rc failed'"
SHELL_OPEN = "exec /bin/sh"
LOGIN = "login:"
INITIAL_PROMPT_CSH = "root@.+:.+# "  # /bin/csh
INITIAL_PROMPT_SH = "# "  # /bin/sh
STOPPED = "Stopped at"
PANIC = "panic: trap"
PANIC_KDB = "KDB: enter: panic"
CHERI_TRAP = "USER_CHERI_EXCEPTION: pid \\d+ tid \\d+ \\(.+\\)"
# SHELL_LINE_CONTINUATION = "\r\r\n> "

# Similar approach to pexpect.replwrap:
# If the user runs 'env', the value of PS1 will be in the output. To avoid seeing that as the next prompt,
# we'll embed the marker characters# for invisible characters in the prompt; these show up when inspecting the
# environment variable, but not when bash displays the prompt.
# Unfortunately FreeBSD sh doesn't handle '\\[\\]', so we rely on FreeBSD sh PS1/PS2 mapping double backslash to
# single backslash. If we embed that in the middle of the prompt string, the regexes won't match for 'env' output.
PEXPECT_PROMPT = "[PEXPECT\\PROMPT]>"
PEXPECT_CONTINUATION_PROMPT = "[++PEXPECT\\PROMPT++]"
PEXPECT_PROMPT_SET_STR = PEXPECT_PROMPT.replace("\\", "\\\\")
PEXPECT_CONTINUATION_PROMPT_SET_STR = PEXPECT_CONTINUATION_PROMPT.replace("\\", "\\\\")
PEXPECT_PROMPT_RE = re.escape(PEXPECT_PROMPT)
PEXPECT_CONTINUATION_PROMPT_RE = re.escape(PEXPECT_CONTINUATION_PROMPT)

FATAL_ERROR_MESSAGES = [CHERI_TRAP]

PRETEND = False
MESSAGE_PREFIX = ""
QEMU_LOGFILE = None  # type: typing.Optional[Path]
# To keep the port available until we start QEMU
_SSH_SOCKET_PLACEHOLDER = None  # type: typing.Optional[socket.socket]
MAX_SMBFS_RETRY = 3


class CheriBSDCommandFailed(Exception):
    def __init__(self, *args, execution_time: datetime.timedelta):
        super().__init__(*args)
        self.execution_time = execution_time

    def __str__(self):
        return "".join(map(str, self.args))


class CheriBSDCommandTimeout(CheriBSDCommandFailed):
    pass


class CheriBSDMatchedErrorOutput(CheriBSDCommandFailed):
    pass


class SmbMount(object):
    def __init__(self, hostdir: str, readonly: bool, in_target: str):
        self.readonly = readonly
        self.hostdir = str(Path(hostdir).absolute())
        self.in_target = in_target

    @property
    def qemu_arg(self):
        if self.readonly:
            return self.hostdir + "@ro"
        return self.hostdir

    def __repr__(self):
        return "<{} ({}) -> {}>".format(self.hostdir, "ro" if self.readonly else "rw", self.in_target)


def parse_smb_mount(arg: str):
    if ":" not in arg:
        failure("Invalid smb_mount string '", arg, "'. Expected format is <HOST_PATH>:<PATH_IN_TARGET>", exit=True)
    host, target = arg.split(":", 2)
    readonly = False
    if host.endswith("@ro"):
        host = host[:-3]
        readonly = True
    return SmbMount(host, readonly, target)


class CheriBSDInstance(pexpect.spawn):
    EXIT_ON_KERNEL_PANIC = True
    smb_dirs = None  # type: typing.List[SmbMount]
    flush_interval = None

    def __init__(self, qemu_config: QemuOptions, *args, ssh_port: typing.Optional[int],
                 ssh_pubkey: typing.Optional[Path], **kwargs):
        super().__init__(*args, **kwargs)
        self.qemu_config = qemu_config
        self.should_quit = False
        self.ssh_port = ssh_port
        assert ssh_pubkey is None or isinstance(ssh_pubkey, Path)
        self.ssh_public_key = ssh_pubkey
        # strip the .pub from the key file
        self.ssh_private_key = Path(ssh_pubkey).with_suffix("") if ssh_pubkey else None
        assert self.ssh_private_key != self.ssh_public_key
        self.ssh_user = "root"
        self.smb_dirs = []  # type: typing.List[SmbMount]
        self.smb_failed = False

    @property
    def xtarget(self) -> CrossCompileTarget:
        return self.qemu_config.xtarget

    def expect(self, pattern: list, timeout=-1, pretend_result=None, timeout_msg="timeout", **kwargs):
        assert isinstance(pattern, list), "expected list and not " + str(pattern)
        return self._expect_and_handle_panic(pattern, timeout=timeout, timeout_msg=timeout_msg, **kwargs)

    def expect_prompt(self, timeout=-1, timeout_msg="timeout", timeout_fatal=True, **kwargs):
        return self.expect_exact([PEXPECT_PROMPT], timeout=timeout, timeout_msg=timeout_msg,
                                 timeout_fatal=timeout_fatal, **kwargs)

    def expect_exact(self, pattern_list, timeout=-1, pretend_result=None, timeout_fatal=True, timeout_msg="timeout",
                     **kwargs):
        assert PANIC not in pattern_list
        assert STOPPED not in pattern_list
        assert PANIC_KDB not in pattern_list
        if not isinstance(pattern_list, list):
            pattern_list = [pattern_list]
        panic_regexes = [PANIC, STOPPED, PANIC_KDB]
        try:
            i = super().expect_exact(panic_regexes + pattern_list, **kwargs)
            if i < len(panic_regexes):
                debug_kernel_panic(self)
                failure("EXITING DUE TO KERNEL PANIC!", exit=self.EXIT_ON_KERNEL_PANIC)
            return i - len(panic_regexes)
        except pexpect.TIMEOUT:
            failure(timeout_msg, ": ", str(self), exit=timeout_fatal)

    def _expect_and_handle_panic(self, options: list, timeout_msg, timeout_fatal=True, **kwargs):
        assert PANIC not in options
        assert STOPPED not in options
        assert PANIC_KDB not in options
        panic_regexes = [PANIC, STOPPED, PANIC_KDB]
        try:
            i = super().expect(panic_regexes + options, **kwargs)
            if i < len(panic_regexes):
                debug_kernel_panic(self)
                failure("EXITING DUE TO KERNEL PANIC!", exit=self.EXIT_ON_KERNEL_PANIC)
            return i - len(panic_regexes)
        except pexpect.TIMEOUT:
            failure(timeout_msg, ": ", str(self), exit=timeout_fatal)

    def run(self, cmd: str, *, expected_output=None, error_output=None, cheri_trap_fatal=True, ignore_cheri_trap=False,
            timeout=60):
        run_cheribsd_command(self, cmd, expected_output=expected_output, error_output=error_output,
                             cheri_trap_fatal=cheri_trap_fatal, ignore_cheri_trap=ignore_cheri_trap, timeout=timeout)

    def checked_run(self, cmd: str, *, timeout=600, ignore_cheri_trap=False, error_output: str = None, **kwargs):
        checked_run_cheribsd_command(self, cmd, timeout=timeout, ignore_cheri_trap=ignore_cheri_trap,
                                     error_output=error_output, **kwargs)

    @staticmethod
    def _ssh_options(use_controlmaster: bool):
        result = ["-o", "UserKnownHostsFile=/dev/null",
                  "-o", "StrictHostKeyChecking=no",
                  "-o", "NoHostAuthenticationForLocalhost=yes",
                  # "-o", "ConnectTimeout=20",
                  # "-o", "ConnectionAttempts=2",
                  ]
        if use_controlmaster:
            # XXX: always use controlmaster for faster connections?
            controlmaster_dir = Path.home() / ".ssh/controlmasters"
            controlmaster_dir.mkdir(exist_ok=True)
            result += ["-o", "ControlPath={control_dir}/%r@%h:%p".format(control_dir=controlmaster_dir),
                       "-o", "ControlMaster=auto",
                       # Keep socket open for 10 min (600) or indefinitely (yes)
                       "-o", "ControlPersist=600"]
        return result

    def run_command_via_ssh(self, command: typing.List[str], *, stdout=None, stderr=None, check=True, verbose=False,
                            use_controlmaster=False, **kwargs) -> subprocess.CompletedProcess:
        assert self.ssh_port is not None
        ssh_command = ["ssh", "{user}@{host}".format(user=self.ssh_user, host="localhost"),
                       "-p", str(self.ssh_port),
                       "-i", str(self.ssh_private_key)]
        if verbose:
            ssh_command.append("-v")
        ssh_command.extend(self._ssh_options(use_controlmaster=use_controlmaster))
        ssh_command.append("--")
        ssh_command.extend(command)
        print_cmd(ssh_command, **kwargs)
        return subprocess.run(ssh_command, stdout=stdout, stderr=stderr, check=check, **kwargs)

    def check_ssh_connection(self, prefix="SSH connection:"):
        connection_test_start = datetime.datetime.utcnow()
        result = self.run_command_via_ssh(["echo", "connection successful"], check=True, stdout=subprocess.PIPE,
                                          verbose=True)
        connection_time = (datetime.datetime.utcnow() - connection_test_start).total_seconds()
        info(prefix, result.stdout)
        if result.stdout != b"connection successful\n":
            failure(prefix, " unexepected output ", result.stdout, " after ", connection_time, " seconds", exit=False)
            return False
        else:
            success(prefix, " successful after ", connection_time, " seconds")
            return True

    def scp_from_guest(self, qemu_dir: str, local_dir: Path):
        assert self.ssh_port is not None
        command = ["scp", "-P", str(self.ssh_port), "-i", str(self.ssh_private_key)]
        command.extend(self._ssh_options(use_controlmaster=False))
        command.append("{user}@{host}:{remote_dir}".format(user=self.ssh_user, host="localhost", remote_dir=qemu_dir))
        if not local_dir.parent.exists():
            failure("Parent dir does't exist: ", local_dir, exit=False)
        command.append(str(local_dir))
        run_host_command(command)


def info(*args, **kwargs):
    print(MESSAGE_PREFIX, "\033[0;34m", *args, "\033[0m", file=sys.stderr, sep="", flush=True, **kwargs)


def success(*args, **kwargs):
    print("\n", MESSAGE_PREFIX, "\033[0;32m", *args, "\033[0m", sep="", file=sys.stderr, flush=True, **kwargs)


def print_cmd(cmd: typing.List[str], **kwargs):
    args_str = " ".join((shlex.quote(i) for i in list(cmd)))
    if kwargs:
        print("\033[0;33mRunning ", args_str, " with ", kwargs.copy(), "\033[0m", sep="", file=sys.stderr, flush=True)
    else:
        print("\033[0;33mRunning ", args_str, "\033[0m", sep="", file=sys.stderr, flush=True)


# noinspection PyShadowingBuiltins
def failure(*args, exit=True, **kwargs):
    print("\n", MESSAGE_PREFIX, "\033[0;31m", *args, "\033[0m", sep="", file=sys.stderr, flush=True, **kwargs)
    if exit:
        # noinspection PyBroadException
        try:
            time.sleep(1)  # to get the remaining output
        except Exception:
            pass
        sys.exit(1)
    return False


def run_host_command(cmd: typing.List[str], **kwargs):
    print_cmd(cmd, **kwargs)
    if PRETEND:
        return
    subprocess.check_call(cmd, **kwargs)


def decompress(archive: Path, force_decompression: bool, *, keep_archive=True, cmd=None) -> Path:
    result = archive.with_suffix("")
    if result.exists():
        if not force_decompression:
            return result
    info("Extracting ", archive)
    if keep_archive:
        cmd += ["-k"]
    run_host_command(cmd + [str(archive)])
    return result


def is_newer(path1: Path, path2: Path):
    # info(path1.stat())
    # info(path2.stat())
    return path1.stat().st_ctime > path2.stat().st_ctime


def prepend_ld_library_path(qemu: CheriBSDInstance, path: str):
    qemu.run("export LD_LIBRARY_PATH=" + path + ":$LD_LIBRARY_PATH", timeout=3)
    qemu.run("export LD_CHERI_LIBRARY_PATH=" + path + ":$LD_LIBRARY_PATH", timeout=3)


def set_ld_library_path_with_sysroot(qemu: CheriBSDInstance):
    non_cheri_libdir = "lib64"
    cheri_libdir = "libcheri"
    purecap_install_prefix = "usr/local/" + qemu.xtarget.get_cheri_purecap_target().generic_suffix
    hybrid_install_prefix = "usr/local/" + qemu.xtarget.get_cheri_hybrid_target().generic_suffix
    nocheri_install_prefix = "usr/local/" + qemu.xtarget.get_non_cheri_target().generic_suffix

    noncheri_ld_lib_path_var = "LD_LIBRARY_PATH" if not qemu.xtarget.is_cheri_purecap() else "LD64_LIBRARY_PATH"
    cheri_ld_lib_path_var = "LD_LIBRARY_PATH" if qemu.xtarget.is_cheri_purecap() else "LD_CHERI_LIBRARY_PATH"
    qemu.run("export {var}=/{lib}:/usr/{lib}:/usr/local/{lib}:/sysroot/{lib}:/sysroot/usr/{lib}:/sysroot/{hybrid}/lib:"
             "/sysroot/{noncheri}/lib".format(lib=non_cheri_libdir, hybrid=hybrid_install_prefix,
                                              noncheri=nocheri_install_prefix,
                                              var=noncheri_ld_lib_path_var), timeout=3)
    qemu.run("export {var}=/{l}:/usr/{l}:/usr/local/{l}:/sysroot/{l}:/sysroot/usr/{l}:/sysroot/{prefix}/lib".format(
        prefix=purecap_install_prefix, l=cheri_libdir, var=cheri_ld_lib_path_var), timeout=3)


def maybe_decompress(path: Path, force_decompression: bool, keep_archive=True, args: argparse.Namespace = None, *,
                     what: str) -> Path:
    # drop the suffix and then try decompressing
    def bunzip(archive):
        return decompress(archive, force_decompression, cmd=["bunzip2", "-v", "-f"], keep_archive=keep_archive)

    def unxz(archive):
        return decompress(archive, force_decompression, cmd=["xz", "-d", "-v", "-f"], keep_archive=keep_archive)

    if args and getattr(args, "internal_shard", None) and not PRETEND:
        assert path.exists()

    if path.suffix == ".bz2":
        return bunzip(path)
    if path.suffix == ".xz":
        return unxz(path)

    bz2_guess = path.with_suffix(path.suffix + ".bz2")
    # try adding the archive suffix suffix
    if bz2_guess.exists():
        if path.is_file() and is_newer(path, bz2_guess):
            info("Not Extracting ", bz2_guess, " since uncompressed image ", path, " is newer")
            return path
        info("Extracting ", bz2_guess, " since it is newer than uncompressed image ", path)
        return bunzip(bz2_guess)

    xz_guess = path.with_suffix(path.suffix + ".xz")
    if xz_guess.exists():
        if path.is_file() and is_newer(path, xz_guess):
            info("Not Extracting ", xz_guess, " since uncompressed image ", path, " is newer")
            return path
        info("Extracting ", xz_guess, " since it is newer than uncompressed image ", path)
        return unxz(xz_guess)

    if not path.exists():
        failure("Could not find " + what + " " + str(path), exit=True)
    assert path.exists(), path
    return path


def debug_kernel_panic(qemu: pexpect.spawn):
    # wait up to 10 seconds for a db prompt
    i = qemu.expect([pexpect.TIMEOUT, "db> "], timeout=10)
    if i == 1:
        qemu.sendline("bt")
    # wait for the backtrace
    qemu.expect([pexpect.TIMEOUT, "db> "], timeout=30)
    failure("GOT KERNEL PANIC!", exit=False)
    # print("\n\npexpect info = ", qemu)


def run_cheribsd_command(qemu: CheriBSDInstance, cmd: str, expected_output=None, error_output=None,
                         cheri_trap_fatal=True, ignore_cheri_trap=False, timeout=60):
    qemu.sendline(cmd)
    # FIXME: allow ignoring CHERI traps
    if expected_output:
        qemu.expect([expected_output], timeout=timeout)

    results = ["/bin/sh: [/\\w\\d_-]+: not found",
               "ld(-cheri)?-elf.so.1: Shared object \".+\" not found, required by \".+\"",
               pexpect.TIMEOUT, PEXPECT_PROMPT_RE, PEXPECT_CONTINUATION_PROMPT_RE]
    error_output_index = -1
    cheri_trap_index = -1
    if error_output:
        error_output_index = len(results)
        results.append(error_output)
    if not ignore_cheri_trap:
        cheri_trap_index = len(results)
        results.append(CHERI_TRAP)
    starttime = datetime.datetime.now()
    i = qemu.expect(results, timeout=timeout, pretend_result=3)
    runtime = datetime.datetime.now() - starttime
    if i == 0:
        raise CheriBSDCommandFailed("/bin/sh: command not found: ", cmd, execution_time=runtime)
    elif i == 1:
        raise CheriBSDCommandFailed("Missing shared library dependencies: ", cmd, execution_time=runtime)
    elif i == 2:
        raise CheriBSDCommandTimeout("timeout running ", cmd, execution_time=runtime)
    elif i == 3:
        success("ran '", cmd, "' successfully (in ", runtime.total_seconds(), "s)")
    elif i == 4:
        raise CheriBSDCommandFailed("Detected line continuation, cannot handle this yet! ", cmd, execution_time=runtime)
    elif i == error_output_index:
        # wait up to 20 seconds for a prompt to ensure the full output has been printed
        qemu.expect_prompt(timeout=20, timeout_fatal=False)
        qemu.flush()
        raise CheriBSDMatchedErrorOutput("Matched error output ", error_output, " in ", cmd, execution_time=runtime)
    elif i == cheri_trap_index:
        # wait up to 20 seconds for a prompt to ensure the dump output has been printed
        qemu.expect_prompt(timeout=20, timeout_fatal=False)
        qemu.flush()
        if cheri_trap_fatal:
            raise CheriBSDCommandFailed("Got CHERI TRAP!", execution_time=runtime)
        else:
            failure("Got CHERI TRAP!", exit=False)


def checked_run_cheribsd_command(qemu: CheriBSDInstance, cmd: str, timeout=600, ignore_cheri_trap=False,
                                 error_output: str = None, **kwargs):
    starttime = datetime.datetime.now()
    qemu.sendline(
        cmd + " ;if test $? -eq 0; then echo '__COMMAND' 'SUCCESSFUL__'; else echo '__COMMAND' 'FAILED__'; fi")
    cheri_trap_index = None
    error_output_index = None
    results = ["__COMMAND SUCCESSFUL__", "__COMMAND FAILED__", PEXPECT_CONTINUATION_PROMPT_RE]
    try:
        if not ignore_cheri_trap:
            cheri_trap_index = len(results)
            results.append(CHERI_TRAP)
        if error_output:
            error_output_index = len(results)
            results.append(error_output)
        i = qemu.expect(results, timeout=timeout, **kwargs)
    except pexpect.TIMEOUT:
        i = -1
    runtime = datetime.datetime.now() - starttime
    if i == -1:  # Timeout
        raise CheriBSDCommandTimeout("timeout after ", runtime, " running '", cmd, "': ", str(qemu),
                                     execution_time=runtime)
    elif i == 0:
        success("ran '", cmd, "' successfully (in ", runtime.total_seconds(), "s)")
        qemu.expect_prompt(timeout=10)
        qemu.flush()
        return True
    elif i == 2:
        raise CheriBSDCommandFailed("Detected line continuation, cannot handle this yet! ", cmd, execution_time=runtime)
    elif i == cheri_trap_index:
        # wait up to 20 seconds for a prompt to ensure the dump output has been printed
        qemu.expect_prompt(timeout=20, timeout_fatal=False)
        qemu.flush()
        raise CheriBSDCommandFailed("Got CHERI trap running '", cmd, "' (after '", runtime.total_seconds(), "s)",
                                    execution_time=runtime)
    elif i == error_output_index:
        # wait up to 20 seconds for the shell prompt
        qemu.expect_prompt(timeout=20, timeout_fatal=False)
        qemu.flush()
        assert isinstance(error_output, str)
        raise CheriBSDMatchedErrorOutput("Matched error output '" + error_output + "' running '", cmd, "' (after '",
                                         runtime.total_seconds(), ")", execution_time=runtime)
    else:
        assert i < len(results), str(i) + " >= len(" + str(results) + ")"
        raise CheriBSDCommandFailed("error running '", cmd, "' (after '", runtime.total_seconds(), "s)",
                                    execution_time=runtime)


def setup_ssh_for_root_login(qemu: CheriBSDInstance):
    pubkey = qemu.ssh_public_key
    assert pubkey is not None
    assert isinstance(pubkey, Path)
    # Ensure that we have permissions set up in a way so that ssh doesn't complain
    qemu.run("mkdir -p /root/.ssh && chmod 700 /root /root/.ssh")
    ssh_pubkey_contents = pubkey.read_text(encoding="utf-8").strip()
    # Handle ssh-pubkeys that might be too long to send as a single line (write 150-char chunks instead):
    chunk_size = 150
    for part in (ssh_pubkey_contents[i:i + chunk_size] for i in range(0, len(ssh_pubkey_contents), chunk_size)):
        qemu.run("printf %s " + shlex.quote(part) + " >> /root/.ssh/authorized_keys")
    # Add a final newline
    qemu.run("printf '\\n' >> /root/.ssh/authorized_keys")
    qemu.run("chmod 600 /root/.ssh/authorized_keys")
    # Allow root login
    qemu.run("echo 'PermitRootLogin without-password' >> /etc/ssh/sshd_config")
    # TODO: check for bluehive images without /sbin/service
    qemu.run("cat /root/.ssh/authorized_keys", expected_output="ssh-")
    checked_run_cheribsd_command(qemu, "grep -n PermitRootLogin /etc/ssh/sshd_config")
    qemu.sendline("service sshd restart")
    try:
        qemu.expect(["service: not found", "Starting sshd.", "Cannot 'restart' sshd."], timeout=120)
    except pexpect.TIMEOUT:
        failure("Timed out setting up SSH keys")
    qemu.expect_prompt(timeout=60)
    time.sleep(2)  # sleep for two seconds to avoid a rejection
    success("===> SSH authorized_keys set up")


def _set_pexpect_sh_prompt(child):
    success("===> setting PS1")
    # Make the prompt match PROMPT
    prompt_change = u"PS1='{0}' PS2='{1}' PROMPT_COMMAND=''".format(PEXPECT_PROMPT_SET_STR,
                                                                    PEXPECT_CONTINUATION_PROMPT_SET_STR)
    child.sendline(prompt_change)
    # Find the prompt
    child.expect_prompt(timeout=60)
    success("===> successfully set PS1/PS2")


# noinspection PyMethodMayBeStatic,PyUnusedLocal
class FakeSpawn(CheriBSDInstance):
    def __init__(self, qemu_config: QemuOptions, *args, **kwargs):
        # Just start cat for --pretend mode
        kwargs["timeout"] = 1
        super().__init__(qemu_config, "cat", use_poll=True, **kwargs)

    def expect(self, *args, pretend_result=None, **kwargs):
        print("Expecting", args, file=sys.stderr, flush=True)
        args_list = args[0]
        assert isinstance(args_list, list)
        if pretend_result:
            return pretend_result
        # Never return TIMEOUT in pretend mode
        if args_list[0] == pexpect.TIMEOUT:
            return 1
        return 0

    def expect_prompt(self, *args, **kwargs):
        print("Expecting prompt")
        return

    def flush(self):
        pass

    def run(self, cmd, **kwargs):
        # noinspection PyTypeChecker
        run_cheribsd_command(self, cmd, **kwargs)

    def checked_run(self, cmd, **kwargs):
        # noinspection PyTypeChecker
        checked_run_cheribsd_command(self, cmd, **kwargs)

    def check_ssh_connection(self, prefix="SSH connection:"):
        success(prefix, "checked SSH connection")
        return True


def start_dhclient(qemu: CheriBSDInstance):
    success("===> Setting up QEMU networking")
    network_iface = qemu.qemu_config.network_interface_name()
    qemu.sendline("ifconfig {network_iface} up && dhclient {network_iface}".format(network_iface=network_iface))
    i = qemu.expect([pexpect.TIMEOUT, "DHCPACK from 10.0.2.2", "dhclient already running",
                     "interface ([\\w\\d]+) does not exist"], timeout=120)
    if i == 0:  # Timeout
        failure("timeout awaiting dhclient ", str(qemu))
    if i == 1:
        i = qemu.expect([pexpect.TIMEOUT, "bound to"], timeout=120)
        if i == 0:  # Timeout
            failure("timeout awaiting dhclient ", str(qemu))
    if i == 3:
        bad_iface = qemu.match.group(1)
        qemu.expect_prompt(timeout=30)
        qemu.run("ifconfig -a")
        failure("Expected network interface ", bad_iface, " does not exist ", str(qemu))

    success("===> {} bound to QEMU networking".format(network_iface))
    qemu.expect_prompt(timeout=30)


def boot_cheribsd(qemu_options: QemuOptions, qemu_command: typing.Optional[Path], kernel_image: Path,
                  disk_image: typing.Optional[Path], ssh_port: typing.Optional[int],
                  ssh_pubkey: typing.Optional[Path], *, smb_dirs: typing.List[SmbMount] = None, kernel_init_only=False,
                  trap_on_unrepresentable=False, skip_ssh_setup=False, bios_path: Path = None) -> CheriBSDInstance:
    user_network_args = ""
    if smb_dirs is None:
        smb_dirs = []
    if smb_dirs:
        for d in smb_dirs:
            if not Path(d.hostdir).exists():
                failure("SMB share directory ", d.hostdir, " doesn't exist!")
        user_network_args += ",smb=" + ":".join(d.qemu_arg for d in smb_dirs)
    if ssh_port is not None:
        user_network_args += ",hostfwd=tcp::" + str(ssh_port) + "-:22"

    if not qemu_options.can_boot_kernel_directly:
        if not disk_image:
            failure("Cannot boot kernel directly and no disk image passed!")
    if bios_path is not None:
        bios_args = ["-bios", str(bios_path)]
    elif qemu_options.xtarget.is_riscv(include_purecap=True):
        bios_args = riscv_bios_arguments(qemu_options.xtarget, None)
    else:
        bios_args = []
    qemu_args = qemu_options.get_commandline(qemu_command=qemu_command, kernel_file=kernel_image, disk_image=disk_image,
                                             bios_args=bios_args, user_network_args=user_network_args,
                                             add_network_device=True,
                                             trap_on_unrepresentable=trap_on_unrepresentable,  # For debugging
                                             add_virtio_rng=True  # faster entropy gathering
                                             )
    kernel_commandline = []
    if kernel_init_only:
        kernel_commandline.append("init_path=/sbin/startup-benchmark.sh")
    if skip_ssh_setup:
        kernel_commandline.append("cheribuild.skip_sshd=1")
        kernel_commandline.append("cheribuild.skip_entropy=1")
    if kernel_commandline:
        qemu_args.append("-append")
        qemu_args.append(" ".join(kernel_commandline))
    success("Starting QEMU: ", " ".join(qemu_args))
    qemu_starttime = datetime.datetime.now()
    global _SSH_SOCKET_PLACEHOLDER
    if _SSH_SOCKET_PLACEHOLDER is not None:
        _SSH_SOCKET_PLACEHOLDER.close()
    qemu_cls = CheriBSDInstance
    if PRETEND:
        qemu_cls = FakeSpawn
    child = qemu_cls(qemu_options, qemu_args[0], qemu_args[1:], ssh_port=ssh_port, ssh_pubkey=ssh_pubkey,
                     encoding="utf-8", echo=False, timeout=60)
    # child.logfile=sys.stdout.buffer
    child.smb_dirs = smb_dirs
    if QEMU_LOGFILE:
        child.logfile = QEMU_LOGFILE.open("w")
    else:
        child.logfile_read = sys.stdout
    return boot_and_login(child, starttime=qemu_starttime, kernel_init_only=kernel_init_only)


def boot_and_login(child: CheriBSDInstance, *, starttime, kernel_init_only=False) -> CheriBSDInstance:
    have_dhclient = False
    # ignore SIGINT for the python code, the child should still receive it
    # signal.signal(signal.SIGINT, signal.SIG_IGN)

    if kernel_init_only:
        # To test kernel startup time
        child.expect_exact("Uptime: ", timeout=60)
        i = child.expect([pexpect.TIMEOUT, "Please press any key to reboot.", pexpect.EOF], timeout=240)
        if i == 0:
            failure("QEMU didn't exit after shutdown!")
        return child
    try:
        # BOOTVERBOSE is off for the amd64 kernel so we don't see the STARTING_INIT message
        bootverbose = child.xtarget.is_mips(include_purecap=True) or child.xtarget.is_riscv(include_purecap=True)
        boot_messages = [STARTING_INIT, "Hit \\[Enter\\] to boot immediately", "Trying to mount root from.+\\r\\n",
                         BOOT_FAILURE, BOOT_FAILURE2] + FATAL_ERROR_MESSAGES
        i = child.expect(boot_messages, timeout=5 * 60, timeout_msg="timeout before /sbin/init")
        # Skip 10s wait from x86 loader if we see the "Hit [Enter] to boot" message
        if i == 1:  # Hit Enter
            success("Got '", child.match.string, "' from loader")
            child.sendline("")
            i = child.expect(boot_messages, timeout=5 * 60, timeout_msg="timeout before /sbin/init")
        if i == 2:
            success("===> mounting rootfs")
            if bootverbose:
                i = child.expect(boot_messages, timeout=5 * 60, timeout_msg="timeout before /sbin/init")
                if i != 0:  # start up scripts failed
                    failure("failed to start init")
                userspace_starttime = datetime.datetime.now()
                success("===> init running (kernel startup time: ", userspace_starttime - starttime, ")")

        userspace_starttime = datetime.datetime.now()
        # TODO: add bad mountroot messages rather than waiting for timeout
        boot_expect_strings = [LOGIN, SHELL_OPEN, BOOT_FAILURE]
        i = child.expect(boot_expect_strings + ["DHCPACK from "] + FATAL_ERROR_MESSAGES, timeout=15 * 60,
                         timeout_msg="timeout awaiting login prompt")
        if i == len(boot_expect_strings):  # DHCPACK from
            have_dhclient = True
            success("===> got DHCPACK")
            # we have a network, keep waiting for the login prompt
            i = child.expect(boot_expect_strings + FATAL_ERROR_MESSAGES, timeout=5 * 60,
                             timeout_msg="timeout awaiting login prompt")
        if i == boot_expect_strings.index(LOGIN):
            success("===> got login prompt")
            child.sendline("root")

            i = child.expect([INITIAL_PROMPT_CSH, INITIAL_PROMPT_SH], timeout=3 * 60,
                             timeout_msg="timeout awaiting command prompt ")  # give CheriABI csh 3 minutes to start
            if i == 0:  # /bin/csh prompt
                success("===> got csh command prompt, starting POSIX sh")
                # csh is weird, use the normal POSIX sh instead
                child.sendline("sh")
                i = child.expect([INITIAL_PROMPT_CSH, INITIAL_PROMPT_SH], timeout=3 * 60,
                                 timeout_msg="timeout starting /bin/sh")  # give CheriABI sh 3 minutes to start
                if i == 0:  # POSIX sh with PS1 set
                    success("===> started POSIX sh (PS1 already set)")
                elif i == 1:  # POSIX sh without PS1
                    success("===> started POSIX sh (PS1 not set)")
                    _set_pexpect_sh_prompt(child)
            if i == 1:  # /bin/sh prompt
                success("===> got /sbin/sh prompt")
                _set_pexpect_sh_prompt(child)
        elif i == boot_expect_strings.index(SHELL_OPEN):  # shell started from /etc/rc:
            child.expect_exact(INITIAL_PROMPT_SH, timeout=30)
            success("===> /etc/rc completed, got command prompt")
            _set_pexpect_sh_prompt(child)
        else:  # BOOT_FAILURE or FATAL_ERROR_MESSAGES
            # If this was a CHEIR trap wait up to 20 seconds to ensure the dump output has been printed
            child.expect(["THIS STRING SHOULD NOT MATCH, JUST WAITING FOR 20 secs", pexpect.TIMEOUT], timeout=20)
            # If this was a failure of init we should get a debugger backtrace
            failure("Error during boot login prompt: ", str(child), "match index=", i)
        # set up network in case dhclient wasn't started yet
        if not have_dhclient:
            info("Did not see DHCPACK message, starting dhclient manually.")
            start_dhclient(child)
        success("===> booted CheriBSD (userspace startup time: ", datetime.datetime.now() - userspace_starttime, ")")
    except KeyboardInterrupt:
        failure("Keyboard interrupt during boot", exit=True)
    return child


def _do_test_setup(qemu: CheriBSDInstance, args: argparse.Namespace, test_archives: list, test_ld_preload_files: list,
                   test_setup_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], None]" = None) -> None:
    smb_dirs = qemu.smb_dirs  # type: typing.List[SmbMount]
    setup_tests_starttime = datetime.datetime.now()
    # disable coredumps, otherwise we get no space left on device errors
    for smb_dir in smb_dirs:
        # If we are mounting /build set kern.corefile to point there:
        if not smb_dir.readonly and smb_dir.in_target == "/build":
            qemu.run("sysctl kern.corefile=/build/%N.%P.core")
    qemu.run("sysctl kern.coredump=0")
    # ensure that /usr/local exists and if not create it as a tmpfs (happens in the minimal image)
    # However, don't do it on the full image since otherwise we would install kyua to the tmpfs on /usr/local
    # We can differentiate the two by checking if /boot/kernel/kernel exists since it will be missing in the minimal
    # image
    qemu.run(
        "if [ ! -e /boot/kernel/kernel ]; then mkdir -p /usr/local && mount -t tmpfs -o size=300m tmpfs /usr/local; fi")
    # Or this: if [ "$(ls -A $DIR)" ]; then echo "Not Empty"; else echo "Empty"; fi
    qemu.run("if [ ! -e /opt ]; then mkdir -p /opt && mount -t tmpfs -o size=500m tmpfs /opt; fi")
    qemu.run("df -ih")
    info("\nWill transfer the following archives: ", test_archives)

    def do_scp(src, dst="/"):
        # CVE-2018-20685 -> Can no longer use '.' See
        # https://superuser.com/questions/1403473/scp-error-unexpected-filename
        scp_cmd = ["scp", "-B", "-r", "-P", str(qemu.ssh_port), "-o", "StrictHostKeyChecking=no",
                   "-o", "UserKnownHostsFile=/dev/null",
                   "-i", str(qemu.ssh_private_key), str(src), "root@localhost:" + dst]
        # use script for a fake tty to get progress output from scp
        if sys.platform.startswith("linux"):
            scp_cmd = ["script", "--quiet", "--return", "--command", " ".join(scp_cmd), "/dev/null"]
        run_host_command(scp_cmd, cwd=str(src))

    for archive in test_archives:
        if smb_dirs:
            run_host_command(["tar", "xJf", str(archive), "-C", str(smb_dirs[0].hostdir)])
        else:
            # Extract to temporary directory and scp over
            with tempfile.TemporaryDirectory(dir=os.getcwd(), prefix="test_files_") as tmp:
                run_host_command(["tar", "xJf", str(archive), "-C", tmp])
                run_host_command(["ls", "-la"], cwd=tmp)
                do_scp(tmp)
    ld_preload_target_paths = []
    for lib in test_ld_preload_files:
        assert isinstance(lib, Path)
        if smb_dirs:
            run_host_command(["mkdir", "-p", str(smb_dirs[0].hostdir) + "/preload"])
            run_host_command(["cp", "-v", str(lib.absolute()), str(smb_dirs[0].hostdir) + "/preload"])
            ld_preload_target_paths.append(str(Path(smb_dirs[0].in_target, "preload", lib.name)))
        else:
            qemu.run("mkdir -p /tmp/preload")
            do_scp(str(lib), "/tmp/preload/" + lib.name)
            ld_preload_target_paths.append(str(Path("/tmp/preload", lib.name)))

    for index, d in enumerate(smb_dirs):
        qemu.run("mkdir -p '{}'".format(d.in_target))
        mount_command = "mount_smbfs -I 10.0.2.4 -N //10.0.2.4/qemu{} '{}'".format(index + 1, d.in_target)
        for trial in range(MAX_SMBFS_RETRY if not PRETEND else 1):  # maximum of 3 trials
            try:
                checked_run_cheribsd_command(qemu, mount_command,
                                             error_output="unable to open connection: syserr = ",
                                             pretend_result=0)
                qemu.smb_failed = False
                break
            except CheriBSDMatchedErrorOutput as e:
                # If the smbfs connection timed out try once more. This can happen when multiple libc++ test jobs are
                # running on the same jenkins slaves so one of them might time out
                failure("QEMU SMBD failed to mount ", d.in_target, " after ", e.execution_time.total_seconds(),
                        " seconds. Trying ", (MAX_SMBFS_RETRY - trial - 1), " more time(s)", exit=False)
                qemu.smb_failed = True
                info("Waiting for 2-10 seconds before retrying mount_smbfs...")
                if not PRETEND:
                    time.sleep(2 + 8 * random.random())  # wait 2-10 seconds, hopefully the server is less busy then.

    if test_archives:
        time.sleep(5)  # wait 5 seconds to make sure the disks have synced
    # See how much space we have after running scp
    qemu.run("df -h")
    # ensure that /tmp is world-writable
    qemu.run("chmod 777 /tmp")

    for lib in ld_preload_target_paths:
        # Ensure that the libraries exist
        checked_run_cheribsd_command(qemu, "test -x '{}'".format(lib))
    if ld_preload_target_paths:
        checked_run_cheribsd_command(qemu, "export '{}={}'".format(args.test_ld_preload_variable,
                                                                   ":".join(ld_preload_target_paths)))
    success("Preparing test enviroment took ", datetime.datetime.now() - setup_tests_starttime)
    if test_setup_function:
        setup_tests_starttime = datetime.datetime.now()
        test_setup_function(qemu, args)
        success("Additional test enviroment setup took ", datetime.datetime.now() - setup_tests_starttime)


def runtests(qemu: CheriBSDInstance, args: argparse.Namespace, test_archives: list, test_ld_preload_files: list,
             test_setup_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], None]" = None,
             test_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], bool]" = None) -> bool:
    try:
        _do_test_setup(qemu, args, test_archives, test_ld_preload_files, test_setup_function)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        failure("Got exception while preparing test environment:", e, exit=True)
        return False

    if args.test_environment_only:
        success("Test environment set up. Skipping tests due to --test-environment-only")
        return True

    run_tests_starttime = datetime.datetime.now()
    # Run the tests (allowing custom test functions)
    if test_function:
        result = False
        try:
            result = test_function(qemu, args)
        except KeyboardInterrupt:
            result = False
            failure("Got CTRL+C while running tests", exit=False)
        except CheriBSDCommandFailed as e:
            testtime = datetime.datetime.now() - run_tests_starttime
            failure("Command failed after ", testtime, " while running tests: ", str(e), "\n", str(qemu), exit=False)
        testtime = datetime.datetime.now() - run_tests_starttime
        if result is True:
            success("Running tests took ", testtime)
        else:
            failure("Tests failed after ", testtime, exit=False)
        return result

    test_command = args.test_command
    timeout = args.test_timeout
    qemu.sendline(test_command +
                  " ;if test $? -eq 0; then echo 'TESTS' 'COMPLETED'; else echo 'TESTS' 'FAILED'; fi")
    i = qemu.expect([pexpect.TIMEOUT, "TESTS COMPLETED", "TESTS UNSTABLE", "TESTS FAILED"], timeout=timeout)
    testtime = datetime.datetime.now() - run_tests_starttime
    if i == 0:  # Timeout
        return failure("timeout after ", testtime, "waiting for tests (command='", test_command, "'): ", str(qemu),
                       exit=False)
    elif i == 1 or i == 2:
        if i == 2:
            success("===> Tests completed (but with FAILURES)!")
        else:
            success("===> Tests completed!")
        success("Running tests took ", testtime)
        qemu.run("df -h", expected_output="/opt")  # see how much space we have now
        return True
    else:
        return failure("error after ", testtime, "while running tests : ", str(qemu), exit=False)


def default_ssh_key():
    for i in ("id_ed25519.pub", "id_rsa.pub"):
        guess = Path(os.path.expanduser("~/.ssh/"), i)
        if guess.exists():
            return str(guess)
    return "/could/not/infer/default/ssh/public/key/path"


def get_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--architecture", help="CPU architecture to be used for this test", required=True,
                        choices=[x for x in SUPPORTED_ARCHITECTURES.keys()])
    parser.add_argument("--qemu-cmd", "--qemu", help="Path to QEMU (default: find matching on in $PATH)", default=None)
    parser.add_argument("--kernel", default=None)
    parser.add_argument("--bios", default=None)
    parser.add_argument("--disk-image", default=None)
    parser.add_argument("--extract-images-to", help="Path where the compressed images should be extracted to")
    parser.add_argument("--reuse-image", action="store_true")
    parser.add_argument("--keep-compressed-images", action="store_true", default=True, dest="keep_compressed_images")
    parser.add_argument("--no-keep-compressed-images", action="store_false", dest="keep_compressed_images")
    parser.add_argument("--make-disk-image-copy", default=True, action="store_true",
                        help="Make a copy of the disk image before running tests")
    parser.add_argument("--no-make-disk-image-copy", action="store_false", dest="disk_image_copy")
    parser.add_argument("--keep-disk-image-copy", default=False, action="store_true",
                        help="Keep the copy of the disk image (if a copy was made)")
    parser.add_argument("--trap-on-unrepresentable", action="store_true",
                        help="CHERI trap on unrepresentable caps instead of detagging")
    parser.add_argument("--ssh-key", default=default_ssh_key())
    parser.add_argument("--ssh-port", type=int, default=None)
    parser.add_argument("--use-smb-instead-of-ssh", action="store_true")
    parser.add_argument("--smb-mount-directory", metavar="HOST_PATH:IN_TARGET",
                        help="Share a host directory with the QEMU guest via smb. This option can be passed multiple "
                             "times "
                             "to share more than one directory. The argument should be colon-separated as follows: "
                             "'<HOST_PATH>:<EXPECTED_PATH_IN_TARGET>'. Appending '@ro' to HOST_PATH will cause the "
                             "directory "
                             "to be mapped as a read-only smb share", action="append",
                        dest="smb_mount_directories", type=parse_smb_mount, default=[])
    parser.add_argument("--test-archive", "-t", action="append", nargs=1)
    parser.add_argument("--test-command", "-c")
    parser.add_argument('--test-ld-preload', action="append", nargs=1, metavar='LIB',
                        help="Copy LIB to the guest andLD_PRELOAD it before running tests")
    parser.add_argument('--test-ld-preload-variable', type=str, default=None,
                        help="The environment variable to set to LD_PRELOAD a library. should be set to either "
                             "LD_PRELOAD or "
                             "LD_CHERI_PRELOAD")
    parser.add_argument("--test-timeout", "-tt", type=int, default=60 * 60)
    # noinspection PyTypeChecker
    parser.add_argument("--qemu-logfile", help="File to write all interactions with QEMU to", type=Path)
    parser.add_argument("--test-environment-only", action="store_true",
                        help="Setup mount paths + SSH for tests but don't actually run the tests (implies --interact)")
    parser.add_argument("--skip-ssh-setup", action="store_true",
                        help="Don't start sshd on boot. Saves a few seconds of boot time if not needed.")
    parser.add_argument("--pretend", "-p", action="store_true",
                        help="Don't actually boot CheriBSD just print what would happen")
    parser.add_argument("--interact", "-i", action="store_true")
    parser.add_argument("--test-kernel-init-only", action="store_true")

    # Ensure that we don't get a race when running multiple shards:
    # If we extract the disk image at the same time we might spawn QEMU just between when the
    # value extracted by one job is unlinked and when it is replaced with a new file
    parser.add_argument("--internal-kernel-override", help=argparse.SUPPRESS)
    parser.add_argument("--internal-disk-image-override", help=argparse.SUPPRESS)
    return parser


def _main(test_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], bool]" = None,
          test_setup_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], None]" = None,
          argparse_setup_callback: "typing.Callable[[argparse.ArgumentParser], None]" = None,
          argparse_adjust_args_callback: "typing.Callable[[argparse.Namespace], None]" = None):
    parser = get_argument_parser()
    if argparse_setup_callback:
        argparse_setup_callback(parser)
    try:
        # noinspection PyUnresolvedReferences
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass

    args = parser.parse_args()
    if args.ssh_port is None:
        temp_ssh_port = find_free_port()
        args.ssh_port = temp_ssh_port.port
        # keep the socket open until just before we start QEMU to prevent other parallel jobs from reusing the same port
        global _SSH_SOCKET_PLACEHOLDER
        _SSH_SOCKET_PLACEHOLDER = temp_ssh_port.socket
    if args.use_smb_instead_of_ssh:
        # Skip all ssh setup by default if we are using smb instead
        args.skip_ssh_setup = True
    if args.internal_kernel_override:
        args.kernel = args.internal_kernel_override
    if args.internal_disk_image_override:
        args.disk_image = args.internal_disk_image_override
    if args.test_environment_only:
        args.interact = True
    xtarget = SUPPORTED_ARCHITECTURES.get(args.architecture, None)
    if xtarget is None:
        failure("Invalid architecture", args.architecture)
    assert isinstance(xtarget, CrossCompileTarget)
    args.xtarget = xtarget
    if argparse_adjust_args_callback:
        argparse_adjust_args_callback(args)

    qemu_options = QemuOptions(xtarget)
    if args.qemu_cmd is not None:
        if not Path(args.qemu_cmd).exists():
            failure("ERROR: Cannot find QEMU binary ", args.qemu_cmd, " doesn't exist", exit=True)
        args.qemu_cmd = Path(args.qemu_cmd).absolute()
    else:
        args.qemu_cmd = qemu_options.get_qemu_binary()
        if args.qemu_cmd is None:
            failure("ERROR: Cannot find QEMU binary for target ", qemu_options.qemu_arch_sufffix, exit=True)

    global PRETEND
    if args.pretend:
        PRETEND = True
    global QEMU_LOGFILE
    if args.qemu_logfile:
        QEMU_LOGFILE = args.qemu_logfile

    starttime = datetime.datetime.now()

    # validate args:
    test_archives = []  # type: list
    test_ld_preload_files = []  # type: list
    if not args.use_smb_instead_of_ssh and not args.skip_ssh_setup:
        if Path(args.ssh_key).suffix != ".pub":
            failure("--ssh-key should point to the public key and not ", args.ssh_key)
        if not Path(args.ssh_key).exists():
            failure("SSH key missing: ", args.ssh_key)
    if args.test_archive or args.test_ld_preload:
        if args.use_smb_instead_of_ssh and not args.smb_mount_directories:
            failure("--smb-mount-directory is required if ssh is disabled")

        if args.test_archive:
            info("Using the following test archives: ", args.test_archive)
            for test_archive in args.test_archive:
                if isinstance(test_archive, list):
                    test_archive = test_archive[0]
                if not Path(test_archive).exists():
                    failure("Test archive is missing: ", test_archive)
                if not test_archive.endswith(".tar.xz"):
                    failure("Currently only .tar.xz archives are supported")
                test_archives.append(test_archive)
        elif args.test_ld_preload:
            info("Preloading the following libraries: ", args.test_ld_preload)
            if not args.test_ld_preload_variable:
                failure("--test-ld-preload-variable must be set of --test-ld-preload is set!")

            for lib in args.test_ld_preload:
                if isinstance(lib, list):
                    lib = lib[0]
                if not Path(lib).exists():
                    failure("PRELOAD library is missing: ", lib)
                test_ld_preload_files.append(Path(lib).resolve())

        if not args.test_command:
            failure("WARNING: No test command specified, tests will fail", exit=False)
            args.test_command = "false"

    force_decompression = not args.reuse_image  # type: bool
    keep_compressed_images = args.keep_compressed_images
    if args.extract_images_to:
        os.makedirs(args.extract_images_to, exist_ok=True)
        new_kernel_path = os.path.join(args.extract_images_to, Path(args.kernel).name)
        shutil.copy(args.kernel, new_kernel_path)
        args.kernel = new_kernel_path
        if args.disk_image:
            new_image_path = os.path.join(args.extract_images_to, Path(args.disk_image).name)
            shutil.copy(args.disk_image, new_image_path)
            args.disk_image = new_image_path

        force_decompression = True
        keep_compressed_images = False
    kernel = maybe_decompress(Path(args.kernel), force_decompression, keep_archive=keep_compressed_images, args=args,
                              what="kernel")
    diskimg = None
    if args.disk_image:
        diskimg = maybe_decompress(Path(args.disk_image), force_decompression, keep_archive=keep_compressed_images,
                                   args=args, what="disk image")

    # Allow running multiple jobs in parallel by making a copy of the disk image
    if diskimg is not None and args.make_disk_image_copy:
        assert isinstance(diskimg, Path)
        str(os.getpid())
        new_img = diskimg.with_suffix(
            ".img.runtests." + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + ".pid" + str(os.getpid()))
        assert not new_img.exists()
        run_host_command(["cp", "-fv", str(diskimg), str(new_img)])
        if not args.keep_disk_image_copy:
            atexit.register(run_host_command, ["rm", "-fv", str(new_img)])
        diskimg = new_img

    boot_starttime = datetime.datetime.now()
    qemu = boot_cheribsd(qemu_options, qemu_command=args.qemu_cmd, kernel_image=kernel, disk_image=diskimg,
                         ssh_port=args.ssh_port, ssh_pubkey=Path(args.ssh_key), smb_dirs=args.smb_mount_directories,
                         kernel_init_only=args.test_kernel_init_only,
                         trap_on_unrepresentable=args.trap_on_unrepresentable, skip_ssh_setup=args.skip_ssh_setup,
                         bios_path=args.bios)
    success("Booting CheriBSD took: ", datetime.datetime.now() - boot_starttime)

    tests_okay = True
    if (test_archives or args.test_command or test_function) and not args.test_kernel_init_only:
        # noinspection PyBroadException
        try:
            if not args.skip_ssh_setup:
                setup_ssh_starttime = datetime.datetime.now()
                setup_ssh_for_root_login(qemu)
                info("Setting up SSH took: ", datetime.datetime.now() - setup_ssh_starttime)
            tests_okay = runtests(qemu, args, test_archives=test_archives, test_function=test_function,
                                  test_setup_function=test_setup_function, test_ld_preload_files=test_ld_preload_files)
        except CheriBSDCommandFailed as e:
            failure("Command failed while runnings tests: ", str(e), "\n", str(qemu), exit=False)
            traceback.print_exc(file=sys.stderr)
            tests_okay = False
        except Exception:
            failure("FAILED to run tests!!\n", str(qemu), exit=False)
            traceback.print_exc(file=sys.stderr)
            tests_okay = False
        except KeyboardInterrupt:
            failure("Tests interrupted!!!", exit=False)
            tests_okay = False

    if args.interact:
        success("===> Interacting with CheriBSD, use CTRL+A,x to exit")
        # interac() prints all input+output -> disable logfile
        qemu.logfile = None
        qemu.logfile_read = None
        qemu.logfile_send = None
        while True:
            try:
                qemu.should_quit = True
                if not qemu.isalive():
                    break
                qemu.interact()
            except KeyboardInterrupt:
                continue

    success("===> DONE")
    info("Total execution time: ", datetime.datetime.now() - starttime)
    if not tests_okay:
        failure("ERROR: Some tests failed!", exit=False)
        sys.exit(2)  # different exit code for test failures


def main(test_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], bool]" = None,
         test_setup_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], None]" = None,
         argparse_setup_callback: "typing.Callable[[argparse.ArgumentParser], None]" = None,
         argparse_adjust_args_callback: "typing.Callable[[argparse.Namespace], None]" = None):
    # Some programs (such as QEMU) can mess up the TTY state if they don't exit cleanly
    with keep_terminal_sane():
        _main(test_function=test_function, test_setup_function=test_setup_function,
              argparse_setup_callback=argparse_setup_callback,
              argparse_adjust_args_callback=argparse_adjust_args_callback)


if __name__ == "__main__":
    main()
