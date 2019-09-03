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
import pexpect
import shlex
import shutil
import socket
import subprocess
import sys
import time
import tempfile
import traceback
import typing
from pathlib import Path
from contextlib import closing
from ..utils import find_free_port

STARTING_INIT = "start_init: trying /sbin/init"
BOOT_FAILURE = "Enter full pathname of shell or RETURN for /bin/sh"
SHELL_OPEN = "exec /bin/sh"
LOGIN = "login:"
PROMPT = "root@.+:.+# "  # /bin/csh
PROMPT_SH = "# "  # /bin/sh
STOPPED = "Stopped at"
PANIC = "panic: trap"
PANIC_KDB = "KDB: enter: panic"
CHERI_TRAP = "USER_CHERI_EXCEPTION: pid \\d+ tid \\d+ \(.+\)"
SHELL_LINE_CONTINUATION = "\r\r\n> "

FATAL_ERROR_MESSAGES = [CHERI_TRAP]

PRETEND = False
MESSAGE_PREFIX = ""
QEMU_LOGFILE = None # type: Optional[Path]
# To keep the port available until we start QEMU
_SSH_SOCKET_PLACEHOLDER = None  # type: typing.Optional[socket.socket]


class CheriBSDCommandFailed(Exception):
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

    def expect(self, pattern: list, timeout=-1, pretend_result=None, **kwargs):
        assert isinstance(pattern, list), "expected list and not " + str(pattern)
        return self._expect_and_handle_panic(pattern, timeout=timeout, **kwargs)

    def expect_exact(self, pattern_list, timeout=-1, pretend_result=None, **kwargs):
        assert PANIC not in pattern_list
        assert STOPPED not in pattern_list
        assert PANIC_KDB not in pattern_list
        if not isinstance(pattern_list, list):
            pattern_list = [pattern_list]
        panic_regexes = [PANIC, STOPPED, PANIC_KDB]
        i = super().expect_exact(panic_regexes + pattern_list, **kwargs)
        if i < len(panic_regexes):
            debug_kernel_panic(self)
            failure("EXITING DUE TO KERNEL PANIC!", exit=self.EXIT_ON_KERNEL_PANIC)
        return i - len(panic_regexes)

    def _expect_and_handle_panic(self, options: list, **kwargs):
        assert PANIC not in options
        assert STOPPED not in options
        assert PANIC_KDB not in options
        panic_regexes = [PANIC, STOPPED, PANIC_KDB]
        i = super().expect(panic_regexes + options, **kwargs)
        if i < len(panic_regexes):
            debug_kernel_panic(self)
            failure("EXITING DUE TO KERNEL PANIC!", exit=self.EXIT_ON_KERNEL_PANIC)
        return i - len(panic_regexes)

    def run(self, cmd: str, *, expected_output=None, error_output=None, cheri_trap_fatal=True, ignore_cheri_trap=False, timeout=60):
        run_cheribsd_command(self, cmd, expected_output=expected_output, error_output=error_output,
                             cheri_trap_fatal=cheri_trap_fatal, ignore_cheri_trap=ignore_cheri_trap, timeout=timeout)

    def checked_run(self, cmd: str, *, timeout=600, ignore_cheri_trap=False, error_output: str=None, **kwargs):
        checked_run_cheribsd_command(self, cmd, timeout=timeout, ignore_cheri_trap=ignore_cheri_trap,
                                     error_output=error_output, **kwargs)

def info(*args, **kwargs):
    print(MESSAGE_PREFIX, "\033[0;34m", *args, "\033[0m", file=sys.stderr, sep="", flush=True, **kwargs)


def success(*args, **kwargs):
    print("\n", MESSAGE_PREFIX, "\033[0;32m", *args, "\033[0m", sep="", file=sys.stderr, flush=True, **kwargs)


# noinspection PyShadowingBuiltins
def failure(*args, exit=True, **kwargs):
    print("\n", MESSAGE_PREFIX, "\033[0;31m", *args, "\033[0m", sep="", file=sys.stderr, flush=True, **kwargs)
    if exit:
        time.sleep(1)  # to get the remaining output
        sys.exit(1)
    return False


def run_host_command(*args, **kwargs):
    args_str = " ".join((shlex.quote(i) for i in list(*args)))
    if kwargs:
        info("\033[0;33mRunning ", args_str, " with ", kwargs.copy(), "\033[0m")
    else:
        info("\033[0;33mRunning ", args_str, "\033[0m")
    if PRETEND:
        return
    subprocess.check_call(*args, **kwargs)


def decompress(archive: Path, force_decompression: bool, *, keep_archive=True, cmd=None, args=None) -> Path:
    result = archive.with_suffix("")
    if result.exists():
        if not force_decompression:
            return result
    info("Extracting ", archive)
    if keep_archive:
        cmd = cmd + ["-k"]
    run_host_command(cmd + [str(archive)])
    return result


def is_newer(path1: Path, path2: Path):
    # info(path1.stat())
    # info(path2.stat())
    return path1.stat().st_ctime > path2.stat().st_ctime


def set_ld_library_path(qemu: CheriBSDInstance):
    qemu.run("export LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib:/sysroot/lib:/sysroot/usr/lib:/sysroot/usr/local/mips/lib", timeout=3)
    qemu.run("export LD_CHERI_LIBRARY_PATH=/usr/libcheri:/usr/local/libcheri:/sysroot/libcheri:/sysroot/usr/libcheri:/sysroot/usr/local/cheri/lib:/sysroot/usr/local/cheri/libcheri", timeout=3)


def maybe_decompress(path: Path, force_decompression: bool, keep_archive=True, args: argparse.Namespace = None, what: str = None) -> Path:
    # drop the suffix and then try decompressing
    def bunzip(archive):
        return decompress(archive, force_decompression, cmd=["bunzip2", "-v", "-f"], keep_archive=keep_archive, args=args)

    def unxz(archive):
        return decompress(archive, force_decompression, cmd=["xz", "-d", "-v", "-f"], keep_archive=keep_archive, args=args)

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
               pexpect.TIMEOUT, PROMPT, SHELL_LINE_CONTINUATION]
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
        raise CheriBSDCommandFailed("/bin/sh: command not found: ", cmd)
    elif i == 1:
        raise CheriBSDCommandFailed("Missing shared library dependencies: ", cmd)
    elif i == 2:
        raise CheriBSDCommandTimeout("timeout running ", cmd)
    elif i == 3:
        success("ran '", cmd, "' successfully (in ", runtime.total_seconds(), "s)")
    elif i == 4:
        raise CheriBSDCommandFailed("Detected line continuation, cannot handle this yet! ", cmd)
    elif i == error_output_index:
        # wait up to 5 seconds for a prompt to ensure the full output has been printed
        qemu.expect([PROMPT], timeout=5)
        qemu.flush()
        raise CheriBSDMatchedErrorOutput("Matched error output ", error_output, " in ", cmd)
    elif i == cheri_trap_index:
        # wait up to 20 seconds for a prompt to ensure the dump output has been printed
        qemu.expect([pexpect.TIMEOUT, PROMPT], timeout=20)
        qemu.flush()
        if cheri_trap_fatal:
            raise CheriBSDCommandFailed("Got CHERI TRAP!")
        else:
            failure("Got CHERI TRAP!", exit=False)


def checked_run_cheribsd_command(qemu: CheriBSDInstance, cmd: str, timeout=600, ignore_cheri_trap=False,
                                 error_output: str=None, **kwargs):
    starttime = datetime.datetime.now()
    qemu.sendline(cmd + " ;if test $? -eq 0; then echo '__COMMAND' 'SUCCESSFUL__'; else echo '__COMMAND' 'FAILED__'; fi")
    cheri_trap_index = None
    error_output_index = None
    results = ["__COMMAND SUCCESSFUL__", "__COMMAND FAILED__", SHELL_LINE_CONTINUATION]
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
        raise CheriBSDCommandTimeout("timeout after ", runtime, " running '", cmd, "': ", str(qemu))
    elif i == 0:
        success("ran '", cmd, "' successfully (in ", runtime.total_seconds(), "s)")
        qemu.expect([PROMPT])
        qemu.flush()
        return True
    elif i == 2:
        raise CheriBSDCommandFailed("Detected line continuation, cannot handle this yet! ", cmd)
    elif i == cheri_trap_index:
        # wait up to 20 seconds for a prompt to ensure the dump output has been printed
        qemu.expect([pexpect.TIMEOUT, PROMPT], timeout=20)
        qemu.flush()
        raise CheriBSDCommandFailed("Got CHERI trap running '", cmd, "' (after '", runtime.total_seconds(), "s)")
    elif i == error_output_index:
        # wait up to 20 seconds for the shell prompt
        qemu.expect([pexpect.TIMEOUT, PROMPT], timeout=20)
        qemu.flush()
        raise CheriBSDMatchedErrorOutput("Matched error output '" + error_output + "' running '", cmd, "' (after '", runtime.total_seconds(), ")")
    else:
        assert i < len(results), str(i) + " >= len(" + str(results) + ")"
        raise CheriBSDCommandFailed("error running '", cmd, "' (after '", runtime.total_seconds(), "s)")


def setup_ssh(qemu: CheriBSDInstance, pubkey: Path):
    run_cheribsd_command(qemu, "mkdir -p /root/.ssh")
    ssh_pubkey_contents = pubkey.read_text(encoding="utf-8").strip()
    # Handle ssh-pubkeys that might be too long to send as a single line (write 150-char chunks instead):
    chunk_size = 150
    for part in (ssh_pubkey_contents[i:i + chunk_size] for i in range(0, len(ssh_pubkey_contents), chunk_size)):
        run_cheribsd_command(qemu, "printf %s " + shlex.quote(part) + " >> /root/.ssh/authorized_keys")
    # Add a final newline
    run_cheribsd_command(qemu, "printf '\\n' >> /root/.ssh/authorized_keys")
    run_cheribsd_command(qemu, "chmod 600 /root/.ssh/authorized_keys")
    # Ensure that we have permissions set up in a way so that ssh doesn't complain
    run_cheribsd_command(qemu, "chmod 700 /root /root/.ssh/")
    run_cheribsd_command(qemu, "echo 'PermitRootLogin without-password' >> /etc/ssh/sshd_config")
    # TODO: check for bluehive images without /sbin/service
    run_cheribsd_command(qemu, "cat /root/.ssh/authorized_keys", expected_output="ssh-")
    checked_run_cheribsd_command(qemu, "grep -n PermitRootLogin /etc/ssh/sshd_config")
    qemu.sendline("service sshd restart")
    try:
        qemu.expect(["service: not found", "Starting sshd.", "Cannot 'restart' sshd."], timeout=120)
    except pexpect.TIMEOUT:
        failure("Timed out setting up SSH keys")
    qemu.expect([PROMPT])
    time.sleep(2)  # sleep for two seconds to avoid a rejection
    success("===> SSH authorized_keys set up")


def set_posix_sh_prompt(child):
    success("===> setting PS1")
    # Make the prompt match PROMPT
    child.sendline("export PS1=\"{}\"".format("root@qemu-test:~ \\\\$ "))
    # No need to eat the echoed command since we end the prompt with \$ (expands to # or $) instead of #
    # Find the prompt
    j = child.expect([pexpect.TIMEOUT, PROMPT], timeout=60)
    if j == 0:  # timeout
        failure("timeout after setting command prompt ", str(child))
    success("===> successfully set PS1")

class FakeSpawn(object):
    pid = -1
    should_quit = False

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

    def sendline(self, msg):
        print("RUNNING '", msg, "'", sep="", file=sys.stderr, flush=True)

    def flush(self):
        pass

    def isalive(self):
        return not self.should_quit

    def interact(self):
        pass

    def run(self, cmd, **kwargs):
        run_cheribsd_command(self, cmd, **kwargs)

    def checked_run(self, cmd, **kwargs):
        checked_run_cheribsd_command(self, cmd, **kwargs)


def start_dhclient(qemu: CheriBSDInstance):
    success("===> Setting up QEMU networking")
    qemu.sendline("ifconfig le0 up && dhclient le0")
    i = qemu.expect([pexpect.TIMEOUT, "DHCPACK from 10.0.2.2", "dhclient already running"], timeout=120)
    if i == 0:  # Timeout
        failure("timeout awaiting dhclient ", str(child))
    if i == 1:
        i = qemu.expect([pexpect.TIMEOUT, "bound to"], timeout=120)
        if i == 0:  # Timeout
            failure("timeout awaiting dhclient ", str(child))
    success("===> le0 bound to QEMU networking")
    qemu.expect_exact(PROMPT_SH, timeout=30)


def boot_cheribsd(qemu_cmd: str, kernel_image: str, disk_image: str, ssh_port: typing.Optional[int], *, smb_dirs: typing.List[SmbMount]=None,
                  kernel_init_only=False, trap_on_unrepresentable=False, skip_ssh_setup=False) -> CheriBSDInstance:
    user_network_args = "user,id=net0,ipv6=off"
    if smb_dirs is None:
        smb_dirs = []
    if smb_dirs:
        for d in smb_dirs:
            if not Path(d.hostdir).exists():
                failure("SMB share directory ", d.hostdir, " doesn't exist!")
        user_network_args += ",smb=" + ":".join(d.qemu_arg for d in smb_dirs)
    if ssh_port is not None:
        user_network_args += ",hostfwd=tcp::" + str(ssh_port) + "-:22"
    qemu_args = ["-M", "malta", "-kernel", kernel_image, "-m", "2048", "-nographic",
                 "-device", "virtio-rng-pci",  # faster entropy gathering
                 #  ssh forwarding:
                 "-net", "nic", "-net", user_network_args]
    if trap_on_unrepresentable:
        qemu_args.append("-cheri-c2e-on-unrepresentable")  # trap on unrepresetable instead of detagging
    if skip_ssh_setup:
        qemu_args.append("-append")
        qemu_args.append("cheribuild.skip_sshd=1 cheribuild.skip_entropy=1")
    if disk_image:
        qemu_args += ["-hda", disk_image]
    success("Starting QEMU: ", qemu_cmd, " ", " ".join(qemu_args))
    qemu_starttime = datetime.datetime.now()
    global _SSH_SOCKET_PLACEHOLDER  # type: socket.socket
    if _SSH_SOCKET_PLACEHOLDER is not None:
        _SSH_SOCKET_PLACEHOLDER.close()
    if PRETEND:
        child = FakeSpawn()
    else:
        # child = pexpect.spawnu(qemu_cmd, qemu_args, echo=False, timeout=60)
        child = CheriBSDInstance(qemu_cmd, qemu_args, encoding="utf-8", echo=False, timeout=60)
    # child.logfile=sys.stdout.buffer
    child.smb_dirs = smb_dirs
    if QEMU_LOGFILE:
        child.logfile = QEMU_LOGFILE.open("w")
    else:
        child.logfile_read = sys.stdout
    have_dhclient = False
    # ignore SIGINT for the python code, the child should still receive it
    # signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        i = child.expect([pexpect.TIMEOUT, STARTING_INIT, BOOT_FAILURE] + FATAL_ERROR_MESSAGES, timeout=5 * 60)
        if i == 0:  # Timeout
            failure("timeout before booted: ", str(child))
        elif i != 1:  # start up scripts failed
            failure("start up scripts failed to run")
        userspace_starttime = datetime.datetime.now()
        success("===> init running (kernel startup time: ", userspace_starttime - qemu_starttime, ")")
        if kernel_init_only:
            # To test kernel startup time
            return child

        boot_expect_strings = [pexpect.TIMEOUT, LOGIN, SHELL_OPEN, BOOT_FAILURE]
        i = child.expect(boot_expect_strings + ["DHCPACK from "] + FATAL_ERROR_MESSAGES, timeout=15 * 60)
        if i == len(boot_expect_strings):
            have_dhclient = True
            # we have a network, keep waiting for the login prompt
            i = child.expect(boot_expect_strings + FATAL_ERROR_MESSAGES, timeout=15 * 60)
        if i == 0:  # Timeout
            failure("timeout awaiting login prompt: ", str(child))
        elif i == 1:
            success("===> got login prompt")
            child.sendline("root")

            i = child.expect([pexpect.TIMEOUT, PROMPT, PROMPT_SH],
                             timeout=3 * 60)  # give CheriABI csh 3 minutes to start
            if i == 0:  # Timeout
                failure("timeout awaiting command prompt ")
            if i == 1:  # /bin/csh prompt
                success("===> got csh command prompt, starting POSIX sh")
                # csh is weird, use the normal POSIX sh instead
                child.sendline("sh")
                i = child.expect([pexpect.TIMEOUT, PROMPT, PROMPT_SH], timeout=3 * 60) # give CheriABI sh 3 minutes to start
                if i == 0:  # Timeout
                    failure("timeout starting /bin/sh")
                elif i == 1:  # POSIX sh with PS1 set
                    success("===> started POSIX sh (PS1 already set)")
                elif i == 2:  # POSIX sh without PS1
                    success("===> started POSIX sh (PS1 not set)")
                    set_posix_sh_prompt(child)
            if i == 2:  # /bin/sh prompt
                success("===> got /sbin/sh prompt")
                set_posix_sh_prompt(child)
        elif i == 2:  # shell started from /etc/rc:
            child.expect_exact(PROMPT_SH, timeout=30)
            success("===> /etc/rc completed, got command prompt")
            # set up network (bluehive image tries to use atse0)
            if not have_dhclient:
                start_dhclient(qemu)
            set_posix_sh_prompt(child)
        else:
            # If this was a failure of init we should get a debugger backtrace
            debug_kernel_panic(child)
            failure("error during boot login prompt: ", str(child))
        success("===> booted CheriBSD (userspace startup time: ", datetime.datetime.now() - userspace_starttime, ")")
    except KeyboardInterrupt:
        failure("Keyboard interrupt during boot", exit=True)
    return child


def runtests(qemu: CheriBSDInstance, args: argparse.Namespace, test_archives: list, test_ld_preload_files: list,
             test_setup_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], None]" = None,
             test_function: "typing.Callable[[CheriBSDInstance, argparse.Namespace], bool]" = None) -> bool:
    test_command = args.test_command
    ssh_keyfile = args.ssh_key
    ssh_port = args.ssh_port
    timeout = args.test_timeout
    smb_dirs = qemu.smb_dirs  # type: typing.List[SmbMount]
    setup_tests_starttime = datetime.datetime.now()
    # disable coredumps, otherwise we get no space left on device errors
    for dir in smb_dirs:
        # If we are mounting /build set kern.corefile to point there:
        if not dir.readonly and dir.in_target == "/build":
            run_cheribsd_command(qemu, "sysctl kern.corefile=/build/%N.%P.core")
    run_cheribsd_command(qemu, "sysctl kern.coredump=0")
    # ensure that /usr/local exists and if not create it as a tmpfs (happens in the minimal image)
    # However, don't do it on the full image since otherwise we would install kyua to the tmpfs on /usr/local
    # We can differentiate the two by checking if /boot/kernel/kernel exists since it will be missing in the minimal image
    run_cheribsd_command(qemu, "if [ ! -e /boot/kernel/kernel ]; then mkdir -p /usr/local && mount -t tmpfs -o size=300m tmpfs /usr/local; fi")
    # Or this: if [ "$(ls -A $DIR)" ]; then echo "Not Empty"; else echo "Empty"; fi
    run_cheribsd_command(qemu, "if [ ! -e /opt ]; then mkdir -p /opt && mount -t tmpfs -o size=500m tmpfs /opt; fi")
    run_cheribsd_command(qemu, "df -ih")
    info("\nWill transfer the following archives: ", test_archives)

    def do_scp(src, dst="/"):
        # strip the .pub from the key file
        private_key = str(Path(ssh_keyfile).with_suffix(""))
        # CVE-2018-20685 -> Can no longer use '.' See https://superuser.com/questions/1403473/scp-error-unexpected-filename
        scp_cmd = ["scp", "-B", "-r", "-P", str(ssh_port), "-o", "StrictHostKeyChecking=no",
                   "-o", "UserKnownHostsFile=/dev/null",
                   "-i", shlex.quote(private_key), str(src), "root@localhost:" + dst]
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
            run_cheribsd_command(qemu, "mkdir -p /tmp/preload")
            do_scp(str(lib), "/tmp/preload/" + lib.name)
            ld_preload_target_paths.append(str(Path("/tmp/preload", lib.name)))

    for index, d in enumerate(smb_dirs):
        run_cheribsd_command(qemu, "mkdir -p '{}'".format(d.in_target))
        mount_command = "mount_smbfs -I 10.0.2.4 -N //10.0.2.4/qemu{} '{}'".format(index + 1, d.in_target)
        try:
            checked_run_cheribsd_command(qemu, mount_command, error_output="unable to open connection: syserr = Operation timed out", pretend_result=0)
        except CheriBSDMatchedErrorOutput:
            failure("QEMU SMBD timed out while mounting ", d.in_target, ". Trying one more time.", exit=False)
            info("Waiting for 5 seconds before retrying mount_smbfs...")
            if not PRETEND:
                time.sleep(5) # wait 5 seconds, hopefully the server is less busy then.
            # If the smbfs connection timed out try once more. This can happen when multiple libc++ test jobs are running
            # on the same jenkins slaves so one of them might time out
            checked_run_cheribsd_command(qemu, mount_command)

    if test_archives:
        time.sleep(5)  # wait 5 seconds to make sure the disks have synced
    # See how much space we have after running scp
    run_cheribsd_command(qemu, "df -h")
    # ensure that /tmp is world-writable
    run_cheribsd_command(qemu, "chmod 777 /tmp")


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

    qemu.sendline(test_command +
                  " ;if test $? -eq 0; then echo 'TESTS' 'COMPLETED'; else echo 'TESTS' 'FAILED'; fi")
    i = qemu.expect([pexpect.TIMEOUT, "TESTS COMPLETED", "TESTS UNSTABLE", "TESTS FAILED"], timeout=timeout)
    testtime = datetime.datetime.now() - run_tests_starttime
    if i == 0:  # Timeout
        return failure("timeout after ", testtime, "waiting for tests (command='", test_command, "'): ", str(qemu), exit=False)
    elif i == 1 or i == 2:
        if i == 2:
            success("===> Tests completed (but with FAILURES)!")
        else:
            success("===> Tests completed!")
        success("Running tests took ", testtime)
        run_cheribsd_command(qemu, "df -h", expected_output="/opt")  # see how much space we have now
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
    parser.add_argument("--qemu-cmd", "--qemu", default="qemu-system-cheri")
    parser.add_argument("--kernel", default="/usr/local/share/cheribsd/cheribsd-malta64-kernel")
    parser.add_argument("--disk-image", default=None,  # default="/usr/local/share/cheribsd/cheribsd-full.img"
                        )
    parser.add_argument("--extract-images-to", help="Path where the compressed images should be extracted to")
    parser.add_argument("--reuse-image", action="store_true")
    parser.add_argument("--keep-compressed-images", action="store_true", default=True, dest="keep_compressed_images")
    parser.add_argument("--no-keep-compressed-images", action="store_false", dest="keep_compressed_images")
    parser.add_argument("--make-disk-image-copy", default=True, action="store_true", help="Make a copy of the disk image before running tests")
    parser.add_argument("--no-make-disk-image-copy", action="store_false", dest="disk_image_copy")
    parser.add_argument("--keep-disk-image-copy", default=False, action="store_true", help="Keep the copy of the disk image (if a copy was made)")
    parser.add_argument("--trap-on-unrepresentable", action="store_true", help="CHERI trap on unrepresentable caps instead of detagging")
    parser.add_argument("--ssh-key", default=default_ssh_key())
    parser.add_argument("--ssh-port", type=int, default=None)
    parser.add_argument("--use-smb-instead-of-ssh", action="store_true")
    parser.add_argument("--smb-mount-directory", metavar="HOST_PATH:IN_TARGET",
                        help="Share a host directory with the QEMU guest via smb. This option can be passed multiple times "
                             "to share more than one directory. The argument should be colon-separated as follows: "
                             "'<HOST_PATH>:<EXPECTED_PATH_IN_TARGET>'. Appending '@ro' to HOST_PATH will cause the directory "
                             "to be mapped as a read-only smb share", action="append",
                        dest="smb_mount_directories", type=parse_smb_mount, default=[])
    parser.add_argument("--test-archive", "-t", action="append", nargs=1)
    parser.add_argument("--test-command", "-c")
    parser.add_argument('--test-ld-preload', action="append", nargs=1, metavar='LIB',
                        help="Copy LIB to the guest andLD_PRELOAD it before running tests")
    parser.add_argument('--test-ld-preload-variable', type=str, default=None,
                        help="The environment variable to set to LD_PRELOAD a library. should be set to either LD_PRELOAD or LD_CHERI_PRELOAD")
    parser.add_argument("--test-timeout", "-tt", type=int, default=60 * 60)
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

def main(test_function:"typing.Callable[[CheriBSDInstance, argparse.Namespace], bool]"=None,
         test_setup_function:"typing.Callable[[CheriBSDInstance, argparse.Namespace], None]"=None,
         argparse_setup_callback: "typing.Callable[[argparse.ArgumentParser], None]"=None,
         argparse_adjust_args_callback: "typing.Callable[[argparse.Namespace], None]"=None):
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
    if argparse_adjust_args_callback:
        argparse_adjust_args_callback(args)
    if shutil.which(args.qemu_cmd) is None:
        failure("ERROR: QEMU binary ", args.qemu_cmd, " doesn't exist", exit=True)

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
    if args.test_archive or args.test_ld_preload:
        if args.use_smb_instead_of_ssh and not args.smb_mount_directories:
            failure("--smb-mount-directory is required if ssh is disabled")
        if not args.use_smb_instead_of_ssh:
            if Path(args.ssh_key).suffix != ".pub":
                failure("--ssh-key should point to the public key and not ", args.ssh_key)
            if not Path(args.ssh_key).exists():
                failure("SSH key missing: ", args.ssh_key)

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
    kernel = str(maybe_decompress(Path(args.kernel), force_decompression, keep_archive=keep_compressed_images, args=args, what="kernel"))
    diskimg = None
    if args.disk_image:
        diskimg = str(maybe_decompress(Path(args.disk_image), force_decompression, keep_archive=keep_compressed_images, args=args, what="kernel"))

    # Allow running multiple jobs in parallel by making a copy of the disk image
    if args.make_disk_image_copy:
        str(os.getpid())
        new_img = Path(diskimg).with_suffix(".img.runtests." + datetime.datetime.now().strftime("%Y%m%d%H%M%S") + ".pid" + str(os.getpid()))
        assert not new_img.exists()
        run_host_command(["cp", "-fv", diskimg, str(new_img)])
        if not args.keep_disk_image_copy:
            atexit.register(run_host_command, ["rm", "-fv", str(new_img)])
        diskimg = str(new_img)

    boot_starttime = datetime.datetime.now()
    qemu = boot_cheribsd(args.qemu_cmd, kernel, diskimg, args.ssh_port, smb_dirs=args.smb_mount_directories,
                         kernel_init_only=args.test_kernel_init_only, trap_on_unrepresentable=args.trap_on_unrepresentable,
                         skip_ssh_setup=args.skip_ssh_setup)
    success("Booting CheriBSD took: ", datetime.datetime.now() - boot_starttime)

    tests_okay = True
    if (test_archives or args.test_command or test_function) and not args.test_kernel_init_only:
        # noinspection PyBroadException
        try:
            if not args.skip_ssh_setup:
                setup_ssh_starttime = datetime.datetime.now()
                setup_ssh(qemu, Path(args.ssh_key))
                info("Setting up SSH took: ", datetime.datetime.now() - setup_ssh_starttime)
            tests_okay = runtests(qemu, args, test_archives=test_archives, test_function=test_function,
                                  test_setup_function=test_setup_function, test_ld_preload_files=test_ld_preload_files)
        except CheriBSDCommandFailed as e:
            failure("Command failed while runnings tests: ", str(e), "\n", str(qemu), exit=False)
            traceback.print_exc(file=sys.stderr)
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
        failure("ERROR: Some tests failed!", exit=True)


if __name__ == "__main__":
    main()
