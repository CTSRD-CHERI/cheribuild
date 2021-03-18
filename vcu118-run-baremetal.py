#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2020 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
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
import argparse
import datetime
import os
import shlex
import shutil
import sys
import tempfile
import time
import typing
import pexpect
from threading import Thread
from abc import abstractmethod
from pathlib import Path


_cheribuild_root = Path(__file__).resolve().parent
_pexpect_dir = _cheribuild_root / "3rdparty/pexpect"
assert (_pexpect_dir / "pexpect/__init__.py").exists()
sys.path.insert(1, str(_pexpect_dir))
sys.path.insert(1, str(_pexpect_dir.parent / "ptyprocess"))
sys.path.insert(1, str(_cheribuild_root))
from pycheribuild.colour import AnsiColour, coloured
from pycheribuild.utils import ConfigBase, fatal_error, get_global_config, init_global_config
from pycheribuild.config.compilation_targets import CompilationTargets
from pycheribuild.processutils import print_command
from pycheribuild.filesystemutils import FileSystemUtils

from serial.tools.list_ports import comports
from serial.tools.list_ports_common import ListPortInfo

VIVADO_SCRIPT = b"""
# Setup some variables
#

if { [llength $argv] != 2 } {
    puts "ERROR!! Did not pass proper number of arguments to this script."
    puts "args: <bitfile path> <ltxfile path>"
    exit -1
}
set bitfile [lindex $argv 0]
set probfile [lindex $argv 1]

open_hw
connect_hw_server
open_hw_target
current_hw_device [get_hw_devices xcvu9p_0]
# refresh_hw_device -update_hw_probes false [lindex [get_hw_devices xcvu9p_0] 0]
set_property PROBES.FILE $probfile [get_hw_devices xcvu9p_0]
set_property FULL_PROBES.FILE $probfile [get_hw_devices xcvu9p_0]
set_property PROGRAM.FILE $bitfile [get_hw_devices xcvu9p_0]
puts "---------------------"
puts "Program Configuration"
puts "---------------------"
puts "Bitstream : $bitfile"
puts "Probe Info: $probfile"
puts ""
puts "Programming..."
program_hw_devices [get_hw_devices xcvu9p_0]
close_hw_target
disconnect_hw_server
close_hw
puts "Done!"
exit 0
"""

def generate_openocd_script(num_cores: int):
    openocd_script = """
interface ftdi
transport select jtag
bindto 0.0.0.0
adapter_khz 2000

ftdi_tdo_sample_edge falling

ftdi_vid_pid 0x0403 0x6014

ftdi_channel 0
ftdi_layout_init 0x00e8 0x60eb

reset_config none

set _CHIPNAME riscv
jtag newtap $_CHIPNAME cpu -irlen 18 -ignore-version -expected-id 0x04B31093
"""

    for core in range(num_cores):
        openocd_script += "\nset _TARGETNAME_{0:d} $_CHIPNAME.cpu{0:d}".format(core)
        openocd_script += "\ntarget create $_TARGETNAME_{0:d} riscv -chain-position $_CHIPNAME.cpu -coreid {0:d}".format(core)
        if core == 0:
            openocd_script += " -rtos hwthread"
        openocd_script += "\n"

    if num_cores > 0:
        openocd_script += "\ntarget smp"
        for core in range(num_cores):
            openocd_script += " $_TARGETNAME_{:d}".format(core)

    openocd_script += """

riscv set_ir dtmcs 0x022924
riscv set_ir dmi 0x003924

init

halt
reset halt
"""
    return openocd_script.encode()

def load_bitfile(bitfile: Path, ltxfile: Path, fu: FileSystemUtils):
    if shutil.which("vivado") is None:
        fatal_error("vivado not in $PATH, cannot continue")
    if bitfile is None or not bitfile.exists():
        fatal_error("Missing bitfile:", bitfile)
    if ltxfile is None or not ltxfile.exists():
        fatal_error("Missing ltx file:", ltxfile)
    with tempfile.NamedTemporaryFile() as t:
        t.write(VIVADO_SCRIPT)
        t.flush()
        args = ["vivado", "-nojournal", "-notrace", "-nolog",
                "-source", t.name, "-mode", "batch", "-tclargs",
                str(bitfile), str(ltxfile)]
        print_command(args, config=get_global_config())
        if get_global_config().pretend:
            vivado = PretendSpawn(args[0], args[1:])
        else:
            vivado = pexpect.spawn(args[0], args[1:], logfile=sys.stdout, encoding="utf-8")
        vivado_exit_str = "Exiting Vivado at"
        if vivado.expect_exact(["****** Vivado", vivado_exit_str]) != 0:
            failure("Vivado failed to start", exit=True)
        print("Vivado started")
        if vivado.expect_exact(["Programming...", vivado_exit_str]) != 0:
            failure("Vivado failed to start programming", exit=True)
        print("Vivado started programming FPGA")
        # 5 minutes should be enough time to programt the FPGA
        if vivado.expect_exact(["Done!", vivado_exit_str], timeout=5 * 60) != 0:
            failure("Vivado failed to program FPGA", exit=True)
        print("Vivado finished programming FPGA")
        vivado.expect_exact([vivado_exit_str])
        vivado.wait()
        fu.delete_file(Path("webtalk.log"), print_verbose_only=True)
        fu.delete_file(Path("webtalk.jou"), print_verbose_only=True)
        if not get_global_config().pretend:
            # wait for 3 seconds to avoid 'Error: libusb_claim_interface() failed with LIBUSB_ERROR_BUSY'
            time.sleep(3)


def abspath_arg(s) -> Path:
    return Path(os.path.abspath(os.path.expandvars(os.path.expanduser(s))))


class SerialConnection:
    def __init__(self, executable, args):
        if get_global_config().pretend:
            self.program = None
        else:
            print_command([executable, *args])
            self.program = pexpect.spawn(executable, args)

    @abstractmethod
    def show_help_message(self): ...


class PicoComConnection(SerialConnection):
    def __init__(self, tty_info: ListPortInfo):
        # We need --nolock so that openocd can access the device
        # TODO: should probably
        super().__init__("picocom", ["--baud", "115200", tty_info.device])


    def show_help_message(self):
        pass


class PySerialConnection(SerialConnection):
    def __init__(self, tty_info: ListPortInfo):
        # Note: use --eol LF to avoid two prompts being printed on <Enter> (default seems to be CRLF)
        super().__init__(sys.executable, ["-m", "serial.tools.miniterm", tty_info.device, "115200", "--eol", "LF"])

    def show_help_message(self):
        pass


class FpgaConnection:
    """Access to openOCD+GDB+Serial port connection"""

    def __init__(self, gdb: pexpect.spawn, openocd: pexpect.spawn, serial: SerialConnection):
        self.gdb = gdb
        self.openocd = openocd
        self.serial = serial


def reset_soc(conn: FpgaConnection):
    # On the rare occasion you need to reset SoC stuff not just the core, set *(0x6fff0000)=1 does a write to a GPIO
    # block whose output is connected to the SoC's reset so that lets you reset the whole SoC
    conn.gdb.sendline("set *(0x6fff0000)=1")
    # though the core will then be running so you'll need to c and then ^C in GDB to get things back in sync
    conn.gdb.sendline("continue")
    conn.gdb.sendintr()


def start_openocd(openocd_cmd: Path, num_cores: int) -> typing.Tuple[pexpect.spawn, int]:
    with tempfile.NamedTemporaryFile() as t:
        t.write(generate_openocd_script(num_cores))
        t.flush()
        cmdline = [str(openocd_cmd), "-f", t.name]
        print_command(cmdline, config=get_global_config())
        if get_global_config().pretend:
            openocd = PretendSpawn(cmdline[0], cmdline[1:])
        else:
            openocd = pexpect.spawn(cmdline[0], cmdline[1:], logfile=sys.stdout, encoding="utf-8")
        openocd.expect_exact(["Open On-Chip Debugger"])
        print("openocd started")
        gdb_port = 3333
        openocd.expect(["Info : Listening on port (\\d+) for gdb connections"])
        if openocd.match is not None:
            gdb_port = int(openocd.match.group(1))
        openocd.expect_exact(["Info : Listening on port 4444 for telnet connections"])
        print("openocd waiting for GDB connection")
        return openocd, gdb_port


def get_console(tty_info: ListPortInfo) -> SerialConnection:
    # We fall back to using the miniterm command bundled with PySerial as the interactive prompt.
    # This means that we don't depend on minicom/picocom being installed.
    print("Connecting to TTY...")
    if shutil.which("picocom"):
        return PicoComConnection(tty_info)
    return PySerialConnection(tty_info)


def load_and_start_exe(*, gdb_cmd: Path, openocd_cmd: Path, bios_image: Path,
                          tty_info: ListPortInfo, num_cores: int,
                          expected_output, expected_output_timeout: int) -> FpgaConnection:
    # Open the serial connection first to check that it's available:
    serial_conn = get_console(tty_info)
    print("Connected to TTY")
    if bios_image is None or not bios_image.exists():
        failure("Missing bios image: ", bios_image)
    # First start openocd
    gdb_start_time = datetime.datetime.utcnow()
    openocd, openocd_gdb_port = start_openocd(openocd_cmd, num_cores)
    # openocd is running, now start GDB
    args = [str(Path(bios_image).absolute()),
            "-ex", "target extended-remote :" + str(openocd_gdb_port)]
    args += ["-ex", "set confirm off"]  # avoid interactive prompts
    args += ["-ex", "set pagination off"]  # avoid paginating output, requiring input
    args += ["-ex", "set style enabled off"]  # disable colours since they break the matcher strings
    args += ["-ex", "monitor reset init"]  # reset and go back to boot room
    #args += ["-ex", "si 5"]  # we need to run the first few instructions to get a valid DTB
    args += ["-ex", "set disassemble-next-line on"]
    args += ["-ex", "load " + shlex.quote(str(Path(bios_image).absolute()))]
    # args += ["-ex", "set $pc=boot"] # Record the entry point to the bios
    print_command(str(gdb_cmd), *args, config=get_global_config())
    if get_global_config().pretend:
        gdb = PretendSpawn(str(gdb_cmd), args, timeout=60)
    else:
        gdb = pexpect.spawn(str(gdb_cmd), args, timeout=60, logfile=sys.stdout, encoding="utf-8")
    gdb.expect_exact(["Reading symbols from"])
    # openOCD should acknowledge the GDB connection:
    openocd.expect_exact(["Info : accepting 'gdb' connection on tcp/{}".format(openocd_gdb_port)])
    print("openocd accepted GDB connection")
    gdb.expect_exact(["Remote debugging using :" + str(openocd_gdb_port)])
    print("GDB connected to openocd")
    # XXX: doesn't match with recent GDB:  gdb.expect_exact(["0x0000000070000000 in ??"])
    #gdb.expect_exact(["0x0000000070000000 in ??"])
    #gdb.expect_exact(["0x0000000070000000"])
    #gdb.expect_exact(["0x0000000044000000 in ??"])
    print("PC set to bootrom")
    # XXX: doesn't match with recent GDB: gdb.expect_exact(["0x0000000044000000 in ??"])
    #gdb.expect_exact(["0x0000000044000000"])
    print("Done executing bootrom")
    # Now load the ELF image
    gdb.expect_exact(["Loading section .text"])
    load_start_time = datetime.datetime.utcnow()
    print("Started loading ELF image")
    gdb.expect_exact(["Transfer rate:"], timeout=10 * 60)  # XXX: is 10 minutes a sensible timeout?
    load_end_time = datetime.datetime.utcnow()
    print("Finished loading ELF image in ", load_end_time - load_start_time)
    gdb_finish_time = load_end_time
    gdb.sendline("continue")

    serial_conn.program.expect_exact(expected_output, timeout=expected_output_timeout)
    print(serial_conn.program.before.decode('utf-8'))

    return FpgaConnection(gdb, openocd, serial_conn)


def find_vcu118_tty(pretend: bool) -> ListPortInfo:
    # find the serial port:
    expected_vendor_id = 0x10c4
    expected_product_id = 0xea70
    for info in comports(include_links=True):
        assert isinstance(info, ListPortInfo)
        if info.pid == expected_product_id and info.vid == expected_vendor_id:
            return info
    if pretend:
        return ListPortInfo("/dev/fakeTTY")
    raise ValueError("Could not find USB TTY with VID", hex(expected_vendor_id), "PID", hex(expected_product_id))


def main():
    # noinspection PyTypeChecker
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--bitfile", help="The bitfile to load", type=abspath_arg)
    parser.add_argument("--ltxfile", help="The LTX file to use", type=abspath_arg)
    parser.add_argument("--bios", help="The machine-mode program to load", type=abspath_arg)
    parser.add_argument("--gdb", default=shutil.which("gdb") or "gdb", help="Path to GDB binary", type=Path)
    parser.add_argument("--openocd", default=shutil.which("openocd") or "openocd", help="Path to openocd binary",
                        type=abspath_arg)
    parser.add_argument("--num-cores", type=int, default=1, help="Number of harts on bitstream")
    parser.add_argument("--pretend", help="Don't actually run the commands just show what would happen",
                        action="store_true")
    parser.add_argument("action", choices=["all", "bitfile", "boot", "console"],
                        default="all", nargs=argparse.OPTIONAL)
    parser.add_argument("--expected-output", default="", help="Expected outout from the loaded software image")
    parser.add_argument("--expected-output-timeout", type=int, default=60, help="Second to wait for expected output")
    try:
        # noinspection PyUnresolvedReferences
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    args = parser.parse_args()
    print(args)
    init_global_config(ConfigBase(pretend=args.pretend, verbose=True, quiet=False))
    if (args.action == "all" and args.bitfile is not None) or args.action == "bitfile":
        if args.ltxfile is None:
            args.ltxfile = Path(args.bitfile).with_suffix(".ltx")
        load_bitfile(args.bitfile, args.ltxfile, FileSystemUtils(get_global_config()))
        if args.action == "bitfile":
            sys.exit(0)

    tty_info = find_vcu118_tty(args.pretend)
    print("Found TTY:", tty_info)
    conn = load_and_start_exe(gdb_cmd=args.gdb, openocd_cmd=args.openocd, bios_image=args.bios,
                                 tty_info=tty_info, num_cores=args.num_cores,
                                 expected_output=args.expected_output,
                                 expected_output_timeout=args.expected_output_timeout)
    sys.exit(0)


if __name__ == "__main__":
    main()
