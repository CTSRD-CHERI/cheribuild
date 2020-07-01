#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# Copyright (c) 2017 Alexandre Joannou
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
import argparse
import datetime
import os
import os.path as op
import re
import signal
import string
import subprocess
import sys
import tempfile
from argparse import ArgumentDefaultsHelpFormatter, ArgumentParser
from pathlib import Path
from subprocess import CalledProcessError, check_call, check_output, PIPE, Popen
from time import sleep

_cheribuild_root = Path(__file__).resolve().parent
_pexpect_dir = _cheribuild_root / "3rdparty/pexpect"
assert (_pexpect_dir / "pexpect/__init__.py").exists()
sys.path.insert(1, str(_pexpect_dir))
sys.path.insert(1, str(_pexpect_dir.parent / "ptyprocess"))
sys.path.insert(1, str(_cheribuild_root))
from pycheribuild import boot_cheribsd
from pycheribuild.qemu_utils import QemuOptions
from pycheribuild.config.compilation_targets import CompilationTargets
import pexpect

##########################
# Command line arguments #
##########################


def auto_int(x):
    return int(x, 0)


def default_qemu_path(args):
    if args.qemu_path:
        if not op.isfile(args.qemu_path):
            sys.exit("ERROR: seletect --qemu-path " + args.qemu_path + " does not exist!")
        return args.qemu_path
    sdk_bindir = os.getenv("CHERI_SDK")
    if not op.isfile(op.join(sdk_bindir, "clang")):
        sdk_bindir = op.join(sdk_bindir, "bin")
    if not op.isfile(op.join(sdk_bindir, "clang")):
        sys.exit("ERROR: could not infer SDK path for QEMU: Neither $CHERI_SDK/clang nor $CHERI_SDK/bin/clang exist!\n"
                 "Either set $CHERI_SDK to point to the SDK bindir or pass --qemu-path")
    cpu = args.jenkins_bitfile or args.jenkins_kernel_cpu_kind
    if cpu == "mips":
        suffix = "cheri256"
    elif cpu == "cheri256":
        suffix = "cheri256"
    elif cpu == "cheri128":
        suffix = "cheri128"
    else:
        sys.exit("ERROR: could not infer CPU for QEMU path! Pass --qemu-path")
    result = op.join(sdk_bindir, "qemu-system-" + suffix)
    if not op.isfile(result):
        sys.exit("ERROR: Inferred QEMU path " + result + " does not exist! Pass --qemu-path")
    return result


def parse_args() -> argparse.Namespace:
    # noinspection PyTypeChecker
    parser = ArgumentParser(
        prog="beri-fpga-bsd-boot",
        description='A high level script wrapping berictl for interacting with the BERI FPGA softcore.',
        formatter_class=ArgumentDefaultsHelpFormatter)

    # general arguments
    parser.add_argument('-b', '--berictl', type=str, default="berictl", metavar='BERICTL',
                        help="Specify BERICTL as the berictl utility.")
    parser.add_argument('-c', '--cable-id', type=str, default="1", metavar='CABLEID',
                        help="Specify CABLEID as the -c argument to berictl.")
    parser.add_argument('--bitfile', type=str, default="DE4_BERI.sof", metavar='BITFILE',
                        help="Specify BITFILE as the argument to loadsof in berictl.")
    parser.add_argument('--kernel-img', type=str, default="bsd.bz2", metavar='KIMAGE',
                        help="Specify KIMAGE as the file argument to loadbin in berictl.")
    parser.add_argument('--kernel-addr', type=auto_int, default="0x100000", metavar='KADDR',
                        help="Specify KADDR as the address argument to loadbin in berictl.")
    parser.add_argument("--use-qemu-instead-of-fpga", action='store_true',
                        help="Run boot/runbench with QEMU instead of berictl")
    parser.add_argument('--qemu-path', type=str, metavar='QEMU_PATH',
                        help="Path to QEMU (only used if --use-qemu-instead-of-fpga is passed). If not set will guess "
                             "based"
                             "on the value of the $CHERI_SDK environment variable and the cpu kind.")
    parser.add_argument('--qemu-disk-image', type=str, metavar='QEMU_DISK_IMAGE',
                        help="Optional disk image to be used as the -hda parameter for QEMU.")
    parser.add_argument('--qemu-ssh-port', type=auto_int, default="12345", metavar='PORT',
                        help="The localhost port that is used for ssh connections when running with QEMU.")
    parser.add_argument('--network-interface', type=str,
                        help="The network interface that is used on the board (default is atse0 for fpga and le0 for "
                             "QEMU)")
    jenkins_cpus = ["mips", "cheri128", "cheri256"]
    jenkins_kernels = ["mfs-root-singleuser", "mfs-root-net", "mfs-root-smoketest",
                       "mfs-root-benchmark-jenkins_bluehive",
                       "mfs-root-jenkins_bluehive", "usbroot", "usbroot-benchmark", "nfsroot", "sdroot"]
    parser.add_argument('--jenkins-bitfile', type=str, choices=jenkins_cpus,
                        help="Download and flash latest jenkins bitfile for CPU")
    parser.add_argument('--experimental-jenkins-bitfile', action="store_true",
                        help="Use the experimental Jenkins bitfile instead of the stable one")
    parser.add_argument('--jenkins-bitfile-job-number', type=str, default="lastSuccessfulBuild",
                        help="The job number to use when fetching the bitfile from jenkins (defaults to last "
                             "successful "
                             "build)")
    parser.add_argument('--jenkins-kernel', type=str, choices=jenkins_kernels,
                        help="Download and boot latest jenkins TYPE kernel")
    parser.add_argument('--jenkins-kernel-job-number', type=str, default="lastSuccessfulBuild",
                        help="The job number to use when fetching the kernel from jenkins (defaults to last successful "
                             "build)")
    parser.add_argument('--jenkins-kernel-cpu-kind', type=str, choices=jenkins_cpus,
                        help="Which CPU the BITFILE is. Only needed if --jenkins-kernel is passed without "
                             "--jenkins-bitfile")
    parser.add_argument('--jenkins-user', default='readonly', help='The username for jenkins authentication')
    parser.add_argument('--jenkins-password', default=None, help='The password for jenkins authentication')
    parser.add_argument('-k', '--ssh-key', type=str, metavar='SSHKEY', default=str(Path.home() / ".ssh/id_rsa"),
                        help="The ssh private key SSHKEY to use for ssh connection with the board.")
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Increase verbosity level kosby adding more \"v\".")
    subcmds = parser.add_subparsers(dest='subcmd', metavar='sub-command',
                                    help="Individual sub-command help available by invoking it with -h or --help.")
    subcmds.required = True

    # load-bitfile
    subcmds.add_parser('load-bitfile', help="Load the bitfile onto the FPGA.",
                       formatter_class=ArgumentDefaultsHelpFormatter)

    # bootonly
    bootonly = subcmds.add_parser('bootonly', help="Boot KIMAGE on BITFILE.",
                                  formatter_class=ArgumentDefaultsHelpFormatter)
    bootonly.add_argument('-i', '--interact', action='store_true', default=False,
                          help="Get an interactive session once logged in.")
    bootonly.add_argument('--skip-bitfile', action='store_true', default=False,
                          help="Skip feeding the bitfile to the FPGA")

    # runbench
    runbench = subcmds.add_parser('runbench',
                                  help="Boot KIMAGE on BITFILE, scp BENCHDIR over, run SCRIPT and scp OUTPATH back.",
                                  formatter_class=ArgumentDefaultsHelpFormatter)
    runbench.add_argument('benchdir', type=str, metavar='BENCHDIR',
                          help="The benchmark directory to be copied and run (must contain SCRIPT).")
    runbench.add_argument('--timeout', type=int, metavar='TIMEOUT', default="10000",
                          help="The TIMEOUT in seconds to specify when running the benchmarks.")
    runbench.add_argument('-s', '--script-name', type=str, metavar='SCRIPT', default="run_jenkins-bluehive.sh",
                          help="The name SCRIPT of the script to run from whithin BENCHDIR once copied onto the board.")
    runbench.add_argument('-a', '--script-args', type=str, metavar='SCRIPTARGS', default="",
                          help="The arguments to pass to SCRIPT (default: \"%(default)s\"). KNOWN ISSUE: this breaks "
                               "when "
                               "the provided string starts with '-' followed by an option that is also an option to "
                               "this "
                               "script. To work around, prepend a space ' ' to your argument string.")

    runbench.add_argument('--pre-command', type=str, metavar='CMD',
                          help="Run CMD before executing the benchmark script (e.g. to set environmet variables)")
    runbench.add_argument('-o', '--out-path', type=str, metavar='OUTPATH', default="*results*",
                          help="The path OUTPATH (relative to BENCHDIR) to the output file or directory generated by "
                               "the "
                               "benchmarks, to copy out of the board.")
    runbench.add_argument('--local-out-path', type=str, metavar='OUTPATH', default=None,
                          help="The local path into which to copy results; default to $PWD")
    runbench.add_argument('--extra-output-files', nargs=argparse.ZERO_OR_MORE, metavar='FILES', default=[],
                          help="Additional files to copy out of the board.")
    runbench.add_argument('--extra-input-files', nargs=argparse.ZERO_OR_MORE, metavar='FILES', default=[],
                          help="Additional files to copy to the board before running the benchmark.")
    runbench.add_argument('-u', '--user', type=str, metavar='USER', default="ctsrd",
                          help="The user name USER to use for ssh connection with the board.")
    runbench.add_argument('-t', '--target', type=str, metavar='TGT', default="de4",
                          help="The name or IP address TGT of the board to use for ssh connection.")
    runbench.add_argument('--skip-boot', action='store_true', default=False,
                          help="Assume that the FPGA has booted already and just attach to the console instead of "
                               "loading "
                               "the"
                               "bitfile and the kernel.")
    runbench.add_argument('--skip-copy', action='store_true', default=False,
                          help="Assume that benchmark files are already on the FPGA -> skip the scp phase.")
    runbench.add_argument('--skip-bitfile', action='store_true', default=False,
                          help="Skip feeding the bitfile to the FPGA")
    runbench.add_argument('--lazy-binding', action='store_true', default=False,
                          help="Allow the benchmarks to run without LD_BIND_NOW")
    runbench.add_argument('-i', '--interact', action='store_true', default=False,
                          help="Get an interactive session once done running SCRIPT and outputs are transfered.")

    # console
    subcmds.add_parser('console', help="Run \"BERICTL console\". Does not attempt to loadsof or loadbin.")
    # bash completion:
    # activate-global-python-argcomplete --user &&  source ~/.bash_completion.d/python-argcomplete.sh
    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        # noinspection PyUnusedLocal
        argcomplete = {}
    # parse the arguments
    args = parser.parse_args()
    global VERBOSE_LEVEL
    VERBOSE_LEVEL = args.verbose
    global logf
    if args.verbose >= stdout_lvl:
        logf = sys.stdout
    return args


logf = None
VERBOSE_LEVEL = 0

################
# Output utils #
################

# pretty printer
cred = '\x1b[31m'
cgreen = '\x1b[32m'
cyellow = '\x1b[33m'
cblue = '\x1b[34m'
cpurple = '\x1b[35m'
creset = '\x1b[0m'

error_lvl = 0
info_lvl = 1
phase_lvl = 1
hostcmd_lvl = 2
stdout_lvl = 3

error_col = cred
info_col = cpurple
phase_col = cyellow
hostcmd_col = cblue


class PP(string.Formatter):
    def format_field(self, value, spec):
        do_fmt = None
        if spec.startswith('cr_'):
            do_fmt = (cred, 3)
        elif spec.startswith('cg_'):
            do_fmt = (cgreen, 3)
        elif spec.startswith('cb_'):
            do_fmt = (cblue, 3)
        elif spec.startswith('cy_'):
            do_fmt = (cyellow, 3)
        elif spec.startswith('cp_'):
            do_fmt = (cpurple, 3)
        elif spec.startswith('error_'):
            do_fmt = (error_col, 6)
        elif spec.startswith('info_'):
            do_fmt = (info_col, 5)
        elif spec.startswith('phase_'):
            do_fmt = (phase_col, 6)
        elif spec.startswith('hostcmd_'):
            do_fmt = (hostcmd_col, 8)
        if do_fmt:
            return "{}{}{}".format(do_fmt[0], super(PP, self).format(value, spec[do_fmt[1]:]), creset)
        else:
            return super(PP, self).format(value, spec)


def errorprint(msg):
    verboseprint(error_lvl, PP().format("{:error_}", msg))


def infoprint(msg):
    verboseprint(info_lvl, PP().format("{:info_}", msg))


def phaseprint(msg):
    verboseprint(phase_lvl, PP().format("{:phase_}", msg))


def hostcmdprint(msg):
    verboseprint(hostcmd_lvl, PP().format("{:hostcmd_}", msg))


def verboseprint(lvl, msg):
    if VERBOSE_LEVEL >= lvl:
        print(msg)


def die(message):
    sys.exit(PP().format("{:red_}", message))


# For rsync:
"""
[benchdir]
path = /tmp/benchdir
comment = benchmark dir
uid = root
gid = wheel
list = yes
read only = no
refuse options = checksum
"""


# ./bin/rsync --daemon --no-detach --port=22 -v --config=/tmp/rsync.conf
# On host:
# rsync --human-readable --progress -r cheri128-bundle rsync://root@localhost:12374/benchdir/

#################
# Pexpect utils #
#################

def cleanup(args, cable_id=None):
    if cable_id is None:
        cable_id = args.cable_id
    if args.use_qemu_instead_of_fpga:
        return  # no need to cleanup anything
    # get pid of nios2-terminal instance to kill
    p0 = Popen(["ps", "-aux"], stdout=PIPE)
    try:
        p1 = Popen(["grep", "nios2-terminal.*{:s}.*".format(str(cable_id))], stdin=p0.stdout, stdout=PIPE)
        niosterm2pid = int(check_output(["grep", "-v", "grep"], stdin=p1.stdout).split()[1])
        # kill the nios2-terminal instance
        os.kill(niosterm2pid, signal.SIGKILL)
    except CalledProcessError as e:
        if e.returncode == 1:
            infoprint("no nios2-terminal instance found ===> nothing to kill")
        else:
            errorprint("failed to kill nios2-terminal instance in cleanup()")


class MySpawn(pexpect.spawn):
    def __init__(self, *args, cmdline_args, **kwargs):
        self.cmdline_args = cmdline_args
        assert isinstance(args[0], str), args
        super().__init__(args[0], list(args[1:]), **kwargs)

    def checked_expect(self, step, pat, timeout=10, failstr=None):
        try:
            if not failstr:
                return self.expect([pat], timeout)
            else:
                idx = self.expect([pat, failstr], timeout)
                if idx == 1:
                    verboseprint(error_lvl, PP().format("{:error_}{:phase_}{:error_}",
                                                        "Phase ", step, " - Failure detected"))
                    cleanup(self.cmdline_args)
                    exit(1)
                else:
                    return idx
        except pexpect.EOF:
            verboseprint(error_lvl, PP().format("{:error_}{:phase_}{:error_}",
                                                "Phase ", step, " - EOF encountered"))
            cleanup(self.cmdline_args)
            exit(1)
        except pexpect.TIMEOUT:
            verboseprint(error_lvl, PP().format("{:error_}{:phase_}{:error_}",
                                                "Phase ", step, " - TIMEOUT ({:d} sec.)".format(timeout)))
            streamtrace_berictl(self.cmdline_args)
            cleanup(self.cmdline_args)
            exit(1)


class BeriCtlCheriBSDSpawn(boot_cheribsd.CheriBSDInstance):
    def __init__(self, *args, ssh_port: int = None, ssh_pubkey: Path = None, **kwargs):
        qemu_config = QemuOptions(CompilationTargets.CHERIBSD_MIPS_HYBRID)  # unused but needs to be passed
        super().__init__(qemu_config, *args, ssh_port=ssh_port, ssh_pubkey=ssh_pubkey, **kwargs)

    def interact(self, escape_character=chr(29),
                 input_filter=None, output_filter=None):
        print("Interacting with console")
        # otherwise we get the output twice and weird bytes/str errors because __interact doesn't decode ...
        old_log = self.logfile
        old_logfile_read = self.logfile_read
        self.logfile = None
        self.logfile_read = None
        self.sendline()
        super().interact(escape_character=escape_character, input_filter=input_filter, output_filter=output_filter)
        self.logfile = old_log
        self.logfile_read = old_logfile_read


def get_console(cable_id, berictl, logfile=None, *, pubkey: Path) -> boot_cheribsd.CheriBSDInstance:
    cmd = [berictl]
    cmd += ['-c', str(cable_id)]
    cmd += ['-j', 'console']
    hostcmdprint(" ".join(cmd))
    # if we specify encoding=utf-8 then spawn only accepts bytes...
    c = BeriCtlCheriBSDSpawn(" ".join(cmd), ssh_port=22, ssh_pubkey=pubkey, encoding="utf-8", echo=False, timeout=60,
                             logfile=logfile)
    timeout = 30
    res = c.expect([pexpect.TIMEOUT, "Connecting to BERI UART"], timeout=timeout)
    if res == 0:
        raise boot_cheribsd.CheriBSDCommandTimeout("timeout waiting for UART to attach",
                                                   execution_time=datetime.timedelta(seconds=timeout))
    return c


def loadsof(bitfile, cable_id, berictl, args, timeout=30):
    if args.use_qemu_instead_of_fpga:
        return
    if not os.path.isfile(bitfile):
        sys.exit("Bitfile doesn't exist: " + bitfile)
    cmd = [berictl]
    cmd += ['-c', str(cable_id)]
    cmd += ['-j', 'loadsof']
    if bitfile.endswith('.bz2'):
        cmd += ['-z']
    cmd += [bitfile]
    hostcmdprint(" ".join(cmd))
    ldsof = MySpawn(*cmd, encoding="utf-8", logfile=logf, echo=False, cmdline_args=args)
    ldsof.checked_expect("loading bitfile", "Programmer was successful. 0 errors", timeout)
    ldsof.wait()
    ldsof.close()


def loadbin(*, img, addr, cable_id, berictl, args):
    if not os.path.isfile(img):
        sys.exit("IMAGE doesn't exist: " + img)
    if args.use_qemu_instead_of_fpga:
        return
    cmd = [berictl]
    cmd += ['-c', str(cable_id)]
    cmd += ['-j', 'loadbin']
    if img.endswith('.bz2'):
        cmd += ['-z']
    cmd += [img, hex(addr)]
    hostcmdprint(" ".join(cmd))
    ldbin = MySpawn(*cmd, encoding="utf-8", logfile=logf, echo=False, cmdline_args=args)
    ldbin.checked_expect("loading kernel image", "100% of *", 3000)
    ldbin.wait()
    ldbin.close()


def boot_bsd_berictl(args, pubkey: Path) -> boot_cheribsd.CheriBSDInstance:
    # grab the console before booting; this should reduce the chance that we
    # miss boot messages
    console = get_console(cable_id=args.cable_id, berictl=args.berictl, pubkey=pubkey)

    # trigger boot
    unpause_cmd = [args.berictl, '-c', str(args.cable_id), '-j', 'resume']
    hostcmdprint(" ".join(unpause_cmd))
    unpause = MySpawn(*unpause_cmd, encoding="utf-8", logfile=logf, echo=False, cmdline_args=args)
    # boot.checked_expect("booting", pexpect.EOF)
    unpause.wait()
    unpause.close()

    cmd = [args.berictl, '-c', str(args.cable_id), '-j', 'boot']
    hostcmdprint(" ".join(cmd))
    boot = MySpawn(*cmd, encoding="utf-8", logfile=logf, echo=False, cmdline_args=args)
    boot.checked_expect("booting", pexpect.EOF)
    boot.wait()
    boot.close()

    return console


def traceall(*, cable_id, berictl, args):
    if args.use_qemu_instead_of_fpga:
        return
    cmd = [berictl]
    cmd += ['-c', str(cable_id)]
    cmd += ['-j', 'settracefilter']
    hostcmdprint(" ".join(cmd))
    ldbin = MySpawn(*cmd, encoding="utf-8", logfile=logf, echo=False, cmdline_args=args)
    ldbin.checked_expect("Trace Mask", pexpect.EOF)
    ldbin.wait()
    ldbin.close()


def streamtrace_berictl(args):
    if args.use_qemu_instead_of_fpga:
        return
    # trigger streamtrace
    cmd = [args.berictl]
    cmd += ['-c', str(args.cable_id)]
    cmd += ['-j', 'streamtrace']
    hostcmdprint(" ".join(cmd))
    boot = MySpawn(*cmd, encoding="utf-8", logfile=logf, echo=False, cmdline_args=args)
    boot.checked_expect("Leaving processor paused", pexpect.EOF)
    boot.wait()
    boot.close()


def boot_bsd_qemu(disk_image, kernel, args, pubkey: Path, port: int) -> boot_cheribsd.CheriBSDInstance:
    qemu = default_qemu_path(args)

    # if needed extract the kernel/image:
    print("kernel =", kernel, "disk image=", disk_image)
    if kernel.endswith(".bz2"):
        check_call(["bunzip2", kernel])
        check_call(["ls", "-la", op.dirname(kernel)])
        kernel = os.path.splitext(kernel)[0]  # strip .xz
    if disk_image and disk_image.endswith(".xz"):
        check_call(["xz", "-d", disk_image])
        check_call(["ls", "-la", op.dirname(disk_image)])
        disk_image = os.path.splitext(disk_image)[0]  # strip .xz
    # For booting QEMU we (ab)use the bitfile as the QEMU kernel and the FPGA kernel image as the disk image
    qemu_config = QemuOptions(CompilationTargets.CHERIBSD_MIPS_HYBRID)
    cmd = [qemu, "-M", "malta", "-kernel", kernel, "-m", "2048", "-nographic",
           # Add the necessary flags to allow connecting to QEMU via ssh
           # TODO: smb=/foo/bar?
           "-net", "nic", "-net", "user,id=net0,ipv6=off,hostfwd=tcp::" + str(args.qemu_ssh_port) + "-:22"]
    if disk_image:
        cmd.extend(["-hda", disk_image])
    # TODO: ssh host forwarding
    print("Running", " ".join(cmd))
    c = boot_cheribsd.CheriBSDInstance(qemu_config, " ".join(cmd), ssh_port=port, ssh_pubkey=pubkey, encoding="utf-8",
                                       echo=False, timeout=60)
    return c


def boot_bsd(kernel_img, args, ssh_pubkey: Path):
    starttime = datetime.datetime.now()
    if args.use_qemu_instead_of_fpga:
        console = boot_bsd_qemu(args.qemu_disk_image, kernel_img, args, pubkey=ssh_pubkey, port=args.qemu_ssh_port)
    else:
        # bitfile and image are not needed here since they have already been loaded
        console = boot_bsd_berictl(args, pubkey=ssh_pubkey)
    assert isinstance(console, boot_cheribsd.CheriBSDInstance)
    console.logfile_read = sys.stdout
    console = boot_cheribsd.boot_and_login(console, starttime=starttime)
    # ensure that we have the ssh public key set up
    if ssh_pubkey.exists():
        boot_cheribsd.setup_ssh_for_root_login(console)
        console.run(
            "test -e /home/ctsrd/.ssh/authorized_keys && cat /root/.ssh/authorized_keys >> "
            "/home/ctsrd/.ssh/authorized_keys")
    # create the ctsrd user if it doesn't exist yet
    console.run(
        "if ! pw user show ctsrd -q > /dev/null; then pw useradd -n ctsrd ctsrd-test-user -s /bin/sh -m -w none && "
        "mkdir -p /home/ctsrd && cp -a /root/.ssh /home/ctsrd/.ssh && chown -R ctsrd /home/ctsrd/.ssh && echo "
        "\"Created user ctsrd\"; fi")
    return console


# noinspection PyUnusedLocal
def do_scp(src, dst, *, port: int, ssh_privkey, timeout=600):
    cmd = ['scp']
    if port != 22:
        cmd += ['-P', str(port)]
    # For some reason jenkins no longer likes the de4 bluehive host keys so to work around this we
    # completely disable host key checking by setting the known hosts file to /dev/null
    # See https://dustymabe.com/2012/01/09/hi-planet---ssh-disable-checking-host-key-against-known_hosts-file./
    cmd += ['-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null', '-o', 'BatchMode=yes']
    cmd += ['-i', ssh_privkey]
    cmd += ['-r']
    cmd += [src, dst]
    hostcmdprint(" ".join(cmd))
    boot_cheribsd.run_host_command(cmd, stdout=logf)


def get_network_iface(args):
    result = args.network_interface
    if result:
        return result
    if args.use_qemu_instead_of_fpga:
        return "le0"
    else:
        return "atse0"


def do_network_on(console: boot_cheribsd.CheriBSDInstance, args, timeout=300):
    ifc = get_network_iface(args)
    # Note: If we devctl disable le0, we can't enable it anymore
    if not args.use_qemu_instead_of_fpga:
        console.run('/usr/sbin/devctl enable {}'.format(ifc),
                    expected_output='{}: bpf attached'.format(ifc))
    console.run('/sbin/ifconfig {} up'.format(ifc))
    if ifc != "le0":
        # apparently the le0 driver doesn't print this message
        console.expect_exact('{}: link state changed to UP'.format(ifc))
    # Send a newline to ensure a prompt:
    console.sendline()
    console.expect_prompt()
    # No longer needed? console.run('/sbin/ifconfig {} polling'.format(ifc))
    console.sendline('/sbin/dhclient {}'.format(ifc))
    console.expect(["bound to .* -- renewal in .*\\."], timeout=timeout)
    console.expect_prompt()


def do_network_off(console: boot_cheribsd.CheriBSDInstance, args):
    ifc = get_network_iface(args)
    console.run('/sbin/ifconfig {} down'.format(ifc))
    #    console.run('killall dhclient')
    # Note: If we devctl disable le0, we can't enable it anymore
    if not args.use_qemu_instead_of_fpga:
        console.sendline('/usr/sbin/devctl disable {}'.format(ifc))
        console.expect(['{}: detached'.format(ifc),
                        "Failed to disable {}: Device not configured".format(ifc)
                        ])


def get_board_ip_address(console: boot_cheribsd.CheriBSDInstance, args):
    assert not args.use_qemu_instead_of_fpga
    ifc = get_network_iface(args)
    console.sendline('ifconfig {}'.format(ifc))
    idx = console.expect([
        re.compile("inet (.+) netmask "),
        # error cases:
        pexpect.TIMEOUT, "interface " + ifc + " does not exist"], timeout=10)
    if idx == 0:
        print(console.match)
        return console.match.group(1)
    console.expect_prompt()


def do_runbench(console: boot_cheribsd.CheriBSDInstance, tgtdir, script, scriptargs,
                failstr="FAILED RUNNING BENCHMARKS", timeout=None, pre_cmd=None, *, args):
    if timeout is None:
        timeout = args.timeout

    badcmd = "/this/command/does/not/exist"
    runbenchcmd = './{} {} || {}'.format(script, scriptargs, badcmd)
    console.sendline()
    console.expect_prompt()
    console.run('cd {} && ls -la'.format(tgtdir))
    if args.lazy_binding:
        console.run('unset LD_CHERI_BIND_NOW')
        console.run('unset LD_BIND_NOW')
    else:
        # Ensure that we don't use lazy binding for CheriABI since MIPS doesn't support it
        # This can skew the results since we have faster startup on CHERI but slower runtime
        # due to trampolines
        console.run('export LD_CHERI_BIND_NOW=1')
        console.run('export LD_BIND_NOW=1')

    if pre_cmd:
        console.run(pre_cmd)
    # Log the current environment:
    console.run("env")
    console.sendline(runbenchcmd)
    panicstr = "KDB: enter: "

    expects = ["DONE RUNNING BENCHMARKS", ": Command not found.", badcmd + ": not found", failstr, panicstr]
    idx = console.expect(expects, timeout=timeout)
    if idx != 0:
        print("Failed to run benchmark")
    if expects[idx] == panicstr:
        print("Panic!  Extracting backtrace...")
        console.sendline("bt")


def get_jenkins_password():
    pw_file = Path.home() / ".config" / "ctsrd-jenkins-readonly-user.txt"
    try:
        password = pw_file.read_text().strip()  # remove newline
    except OSError:
        sys.exit("Could not read jenkins readonly user password from " + str(pw_file))
    return password


def download_file(url, outfile, args):
    if args.jenkins_user == "readonly" and not args.jenkins_password:
        args.jenkins_password = get_jenkins_password()
    print("Downloading", url)
    cmd = ["curl", "--create-dirs", "--output", str(outfile), "--user"]
    # don't print the password:
    boot_cheribsd.print_cmd(cmd + ["*****:*****", url])
    subprocess.check_call(cmd + ["{user}:{pw}".format(user=args.jenkins_user, pw=args.jenkins_password), url])


def common_boot(stop_after_bitfile=False, skip_bitfile=False, *, ssh_pubkey: Path, args, kernel_img, addr, bitfile,
                cable_id, berictl, jenkins_bitfile, jenkins_kernel):
    bitfile_job_nr = args.jenkins_bitfile_job_number
    kernel_job_nr = args.jenkins_kernel_job_number

    if jenkins_kernel or jenkins_bitfile:
        cpu = jenkins_bitfile or args.jenkins_kernel_cpu_kind
        # FIXME
        kernel_template = "https://ctsrd-build.cl.cam.ac.uk/job/CheriBSD-allkernels-multi/BASE_ABI={ABI},CPU={CPU}," \
                          "ISA=vanilla,label=freebsd/{kernel_job_nr}/artifact/ctsrd/cheribsd/trunk/bsdtools/" \
                          "{BSD_TYPE}-{KERNEL_CPU}-de4-{KERNEL_TYPE}-kernel.bz2"
        bitfile_template = "https://ctsrd-build.cl.cam.ac.uk/job/CPU1-DE4-SYNTH/CPU={DE4_CPU},FLAGS=vanilla," \
                           "FPU=noFPU," \
                           "TSTRUCT=0_256,cheri_dimm=1GB,label=altera/{bitfile_job_nr}/artifact/cheri/boards/" \
                           "terasic_de4/output_files/DE4_BERI.sof"
        experimental_bifile_template = "https://ctsrd-build.cl.cam.ac.uk/job/CPU1-DE4-multi-synth_experimental/" \
                                       "cheri={DE4_CPU},cheri_dimm=1GB,dcache=writethrough,invalidate=push," \
                                       "label=bionic,multi=2/" \
                                       "{bitfile_job_nr}/artifact/cheri/boards/terasic_de4/output_files/DE4_BERI.sof"
        if args.experimental_jenkins_bitfile:
            bitfile_template = experimental_bifile_template
        image_template = None
        if args.use_qemu_instead_of_fpga:
            if args.jenkins_bitfile:
                die("--jenkins-bitfile is invalid with QEMU, set --jenkins-kernel-cpu instead!")
            if not jenkins_kernel:
                die("When fetching from jenkins with QEMU you need to set --jenkins-kernel")
            kernel_template = "https://ctsrd-build.cl.cam.ac.uk/job/CheriBSD-allkernels-multi/BASE_ABI={ABI}," \
                              "CPU={CPU}," \
                              "ISA=vanilla,label=freebsd/{kernel_job_nr}/artifact/ctsrd/cheribsd/trunk/bsdtools/" \
                              "{BSD_TYPE}-{KERNEL_CPU}-malta64-kernel.bz2"
            image_template = "https://ctsrd-build.cl.cam.ac.uk/job/CheriBSD-allkernels-multi/BASE_ABI={ABI}," \
                             "CPU={CPU}," \
                             "ISA=vanilla,label=freebsd/{kernel_job_nr}/artifact/ctsrd/cheribsd/trunk/bsdtools/" \
                             "{BSD_TYPE}-{IMAGE_NAME}.img.xz"
        with tempfile.TemporaryDirectory() as tmpdir:
            if not cpu:
                die(
                    "Cannot determine CPU for jenkins kernel download. Set --jenkins-kernel-cpu-kind or "
                    "--jenkins-bitfile")
            if cpu == "mips":
                bsd_type = "freebsd"
                kernel_cpu = "beri"
            elif cpu == "cheri128":
                bsd_type = "cheribsd128"
                kernel_cpu = "cheri128"
            else:
                bsd_type = "cheribsd"
                kernel_cpu = "cheri"
            if jenkins_kernel:
                outfile = op.join(tmpdir, "kernel.bz2")
                url = kernel_template.format(ABI="n64", CPU=cpu, KERNEL_CPU=kernel_cpu, KERNEL_TYPE=jenkins_kernel,
                                             BSD_TYPE=bsd_type, kernel_job_nr=kernel_job_nr)
                download_file(url, outfile, args)
                kernel_img = outfile
                if os.stat(outfile).st_size < 100000:
                    sys.exit("Downloaded an invalid kernel file. Maybe the download URL is no longer valid: " + url)
            if args.use_qemu_instead_of_fpga:
                if jenkins_kernel:
                    # image name is the part of jenkins-kernel after the last -
                    image_name = jenkins_kernel[jenkins_kernel.rfind("-") + 1:]
                url = image_template.format(ABI="n64", CPU=cpu, KERNEL_CPU=kernel_cpu, KERNEL_TYPE=jenkins_kernel,
                                            BSD_TYPE=bsd_type, IMAGE_NAME=image_name, kernel_job_nr=kernel_job_nr)
                outfile = op.join(tmpdir, image_name + ".img.xz")
                download_file(url, outfile, args)
                if os.stat(outfile).st_size < 100000:
                    sys.exit("Downloaded an invalid disk image. Maybe the download URL is no longer valid: " + url)
                bitfile = outfile  # Hack use bitfile as the disk image
            elif jenkins_bitfile:
                assert cpu
                de4_cpu_name = "cheri256" if cpu == "mips" else cpu
                outfile = op.join(tmpdir, "DE4_" + de4_cpu_name + ".sof")
                url = bitfile_template.format(DE4_CPU=de4_cpu_name, bitfile_job_nr=bitfile_job_nr)
                download_file(url, outfile, args)
                if os.stat(outfile).st_size < 100000:
                    sys.exit("Downloaded an invalid bitfile. Maybe the download URL is no longer valid: " + url)
                bitfile = outfile
            # Do the real boot now (hack to keep the rest of the function inside this with statement)
            return common_boot(kernel_img=kernel_img, addr=addr, bitfile=bitfile, cable_id=cable_id, berictl=berictl,
                               jenkins_kernel=None, jenkins_bitfile=None, skip_bitfile=skip_bitfile,
                               ssh_pubkey=ssh_pubkey, args=args)

    # Loading bitfile onto the board
    if not skip_bitfile:
        phaseprint("loading bitfile")
        loadsof(bitfile=args.bitfile, cable_id=args.cable_id, berictl=args.berictl, timeout=160, args=args)
    if stop_after_bitfile:
        return None
    # Loading kernel image onto the board
    phaseprint("loading kernel image")
    loadbin(img=kernel_img, addr=addr, cable_id=cable_id, berictl=berictl, args=args)
    traceall(cable_id=cable_id, berictl=berictl, args=args)
    # Booting BSD
    phaseprint("booting")
    return boot_bsd(kernel_img, args, ssh_pubkey=ssh_pubkey)


#################
# main function #
#################

def main(args):
    if getattr(args, "interact", False) or args.subcmd == "console":
        # Check that we have a TTY for these commands (otherwise we fail much later on when
        # doing the actual interaction)
        import tty
        stdin_tty = os.isatty(sys.stdin.fileno())
        if not stdin_tty:
            sys.exit("--interact flag requires stdin to be a TTY")
        stdout_tty = os.isatty(sys.stdout.fileno())
        print("stdin tty attrs =", tty.tcgetattr(sys.stdin.fileno()))
        print("stdout is a tty:", stdout_tty)
        if stdout_tty:
            print("stdout tty attrs =", tty.tcgetattr(sys.stdout.fileno()))

    ssh_pubkey = Path(args.ssh_key).with_suffix(".pub")
    ############
    # bitfile #
    ############
    if args.subcmd == "load-bitfile":
        # always print what's going on when running load-bitfile
        args.verbose = 3
        common_boot(args=args, kernel_img=args.kernel_img, addr=args.kernel_addr, bitfile=args.bitfile,
                    cable_id=args.cable_id, berictl=args.berictl, jenkins_bitfile=args.jenkins_bitfile,
                    jenkins_kernel=args.jenkins_kernel, stop_after_bitfile=True, ssh_pubkey=ssh_pubkey)

    ############
    # bootonly #
    ############
    if args.subcmd == "bootonly":
        console = common_boot(args=args, kernel_img=args.kernel_img, addr=args.kernel_addr, bitfile=args.bitfile,
                              cable_id=args.cable_id, berictl=args.berictl, jenkins_bitfile=args.jenkins_bitfile,
                              jenkins_kernel=args.jenkins_kernel, skip_bitfile=args.skip_bitfile, ssh_pubkey=ssh_pubkey)
        assert isinstance(console, boot_cheribsd.CheriBSDInstance)
        if args.interact:
            console.interact()
        console.close()

    ############
    # runbench #
    ############
    elif args.subcmd == "runbench":
        if not op.exists(args.benchdir):
            die("Benchmark dir does not exist: " + str(args.benchdir))
        if args.skip_boot:
            if args.use_qemu_instead_of_fpga:
                die("--skip-boot is not compatible with --use-qemu-instead-of-fpga")
            console = get_console(cable_id=args.cable_id, berictl=args.berictl, logfile=logf, pubkey=ssh_pubkey)
            assert isinstance(console, boot_cheribsd.CheriBSDInstance)
            phaseprint("turn network on")
            do_network_off(console, args)
            do_network_on(console, args)
        else:
            console = common_boot(args=args, kernel_img=args.kernel_img, addr=args.kernel_addr, bitfile=args.bitfile,
                                  cable_id=args.cable_id, berictl=args.berictl, jenkins_bitfile=args.jenkins_bitfile,
                                  jenkins_kernel=args.jenkins_kernel, skip_bitfile=args.skip_bitfile,
                                  ssh_pubkey=ssh_pubkey)
            assert isinstance(console, boot_cheribsd.CheriBSDInstance)
            if not args.use_qemu_instead_of_fpga:
                print("Sleeping for 20 seconds to ensure FPGA is ready")
                sleep(20)

        ssh_port = 22
        if args.use_qemu_instead_of_fpga:
            args.target = "localhost"
            ssh_port = args.qemu_ssh_port
        else:
            # Try to find out the board ip address (since the hostname assignment is flaky)
            board_ip = get_board_ip_address(console, args)
            print("inferred board IP as:", board_ip)
            if board_ip is not None:
                args.target = board_ip
        tgtfs = op.join("/", "tmp", "benchdir")
        tgtdir = op.join(tgtfs, op.basename(args.benchdir))
        print("Will copy", args.benchdir, "to", tgtfs)
        tgtout = op.join(tgtdir, args.out_path)
        locout = Path(args.local_out_path) if args.local_out_path is not None else os.getcwd()
        phaseprint("transfer benchmark")
        if not args.skip_copy:
            do_scp(src=args.benchdir, dst="{}@{}:{}".format(args.user, args.target, tgtfs), port=ssh_port,
                   ssh_privkey=args.ssh_key, timeout=2400)
            # Allow copying additional files to the fpga
            for extra_file in args.extra_input_files:
                do_scp(src=extra_file, dst="{}@{}:{}".format(args.user, args.target, tgtfs), port=ssh_port,
                       ssh_privkey=args.ssh_key)
        phaseprint("turn network off")
        do_network_off(console, args)
        phaseprint("running benchmark")
        do_runbench(console, tgtdir, args.script_name, args.script_args, pre_cmd=args.pre_command, args=args)
        phaseprint("turn network on")
        do_network_on(console, args)
        phaseprint("transfer benchmark result")
        do_scp("{}@{}:{}".format(args.user, args.target, tgtout), str(locout), port=ssh_port, ssh_privkey=args.ssh_key)
        # Allow copying more than one file from the FPGA:
        if args.extra_output_files:
            for extra_file in args.extra_output_files:
                do_scp("{}@{}:{}".format(args.user, args.target, extra_file), str(locout), port=ssh_port,
                       ssh_privkey=args.ssh_key)
        if args.interact:
            console.interact()
        console.close()

    ###########
    # console #
    ###########
    elif args.subcmd == "console":
        console = get_console(cable_id=args.cable_id, berictl=args.berictl, pubkey=ssh_pubkey)
        console.interact()
        console.close()

    #######
    # end #
    #######
    phaseprint("DONE")
    exit(0)


if __name__ == "__main__":
    global_args = parse_args()
    try:
        main(global_args)
    finally:
        cleanup(global_args)
