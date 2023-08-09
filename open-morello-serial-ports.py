#!/usr/bin/python3
import argparse
import logging
import os
import shlex
import shutil
import sys
from enum import Enum
from typing import Optional

from serial.tools.list_ports import comports
from serial.tools.list_ports_common import ListPortInfo


def find_morello_board_ttys(pretend: bool, board_index: "Optional[int]" = None) -> "list[ListPortInfo]":
    # find the serial port:
    # Run `ioreg -p IOUSB -l -w 0` on macOS to find the right VID/PID
    expected_vendor_id = 0x0403
    expected_product_id = 0x6011
    ttys = []
    for portinfo in comports(include_links=True):
        logging.debug("Checking %s", portinfo)
        assert isinstance(portinfo, ListPortInfo)
        if portinfo.pid == expected_product_id and portinfo.vid == expected_vendor_id:
            ttys.append(portinfo)
    if pretend:
        for i in range(16):
            port = ListPortInfo(f"/dev/fakeTTY{i:02d}")
            port.location = str(90 + i // 8) + "-1.3." + str(2 if i % 8 < 4 else 1)
            ttys.append(port)
    if len(ttys) < 8:
        raise ValueError("Could not find 8 USB TTYs with VID", hex(expected_vendor_id), "PID", hex(expected_product_id))
    # Sort by location, then device name, since B0-3 actually are ports 0-3 and A0-3 are 4-7
    ttys = list(sorted(ttys, key=lambda x: (x.location, x.device)))
    logging.debug("Found the following serial ports: %s", [x.device for x in ttys])
    if len(ttys) > 8:
        # Multiple boards attached, select the appropriate one
        if len(ttys) % 8 != 0:
            raise ValueError(f"Unexpected number of serial ports ({len(ttys)}), expected 8 per board!")
        if board_index is None:
            raise ValueError(f"Found more than 8 Morello serial ports ({len(ttys)}), please pass --board-index")
        if board_index >= len(ttys) / 8:
            raise ValueError(f"Board index {board_index} is too large, found {len(ttys) // 8} boards")
        return ttys[8 * board_index : 8 * board_index + 8]
    return ttys


# List of Morello board uarts, see
# https://git.morello-project.org/morello/docs/-/blob/morello/mainline/user-guide.rst#setting-up-the-morello-board
# FIXME: documentation says AP2 is +7, but it appears to be +5 instead
class MorelloUART(Enum):
    MCC = "Motherboard Configuration Controller (MCC)"
    PCC = "Platform Controller Chip (PCC)"
    AP = "Application Processor (AP) 0"
    SCP = "System Control Processor (SCP)"
    MCP = "Manageability Control Processor (MCP)"
    AP2 = "Application Processor (AP) 2"
    FPGA0 = "Field Programmable Gate Array (FPGA) 0"
    FPGA1 = "Field Programmable Gate Array (FPGA) 1"

    @classmethod
    def get(cls, index) -> "MorelloUART":
        return list(cls)[index]

    @property
    def index(self):
        return list(MorelloUART).index(self)

    def serial_command(self, morello_ports: "list[ListPortInfo]") -> "list[str]":
        # TODO: Handle programs other than picocom
        if not shutil.which("picocom"):
            sys.exit("FATAL: Could not find picocom command, please install it using you system package manager.")
        return [
            "picocom",
            str(morello_ports[self.index].device),
            "--baud=115200",
            "--parity=none",
            "--stopbits=1",
            "--databits=8",
            "--flow=none",
        ]


def open_tmux_windows(morello_ports: "list[ListPortInfo]", force: bool, pretend: bool, minimal: bool):
    try:
        import libtmux
        import libtmux.exc
    except ImportError:
        sys.exit("Missing `libtmux` python package, cannot continue. Please install it using pip.")

    server = libtmux.Server()
    if server is None:
        raise Exception("Tmux server not found")
    try:
        session = server.new_session(
            "morello-serial-ports",
            attach=False,
            kill_session=force and not pretend,
            start_directory="/",
        )
    except libtmux.exc.TmuxSessionExists:
        sys.exit("tmux session already exists, if you would like to replace it re-run with `--force`")
    # ensure we get advanced colour/font features
    session.set_option("default-terminal", "tmux-256color", _global=True)

    # We need 2 or 8 panes, create 1 or 2 windows with 4 80x24 panes -> 161x81 total tmux window size:
    if not minimal:
        session.new_window(attach=False, window_name="tty4-7", start_directory="/")
    for window in session.windows:
        window.cmd("resize-window", "-x", "161", "-y", "81")
    # refresh window properties after resize (libtmux does not do this automatically
    win1 = session.windows[0]
    logging.debug("Tmux window 1 size: %sx%s", win1.width, win1.height)

    if minimal:
        pane1 = win1.attached_pane
        pane2 = pane1.split_window(vertical=True, start_directory="/")
        uarts_and_panes = ((MorelloUART.MCC, pane1), (MorelloUART.AP, pane2))
    else:
        win2 = session.windows[1]
        win1.rename_window("tty0-3")
        win2.rename_window("tty4-7")

        logging.debug("Tmux window 2 size: %sx%s", win2.width, win2.height)
        win1_0 = win1.attached_pane
        win1_1 = win1_0.split_window(vertical=False, start_directory="/")
        win1_2 = win1_0.split_window(vertical=True, start_directory="/")
        win1_3 = win1_1.split_window(vertical=True, start_directory="/")
        win2_0 = win2.attached_pane
        win2_1 = win2_0.split_window(vertical=False, start_directory="/")
        win2_2 = win2_0.split_window(vertical=True, start_directory="/")
        win2_3 = win2_1.split_window(vertical=True, start_directory="/")

        uarts_and_panes = zip(MorelloUART, [win1_0, win1_1, win1_2, win1_3, win2_0, win2_1, win2_2, win2_3])

    ap_pane = None
    for uart, pane in uarts_and_panes:
        pane.send_keys(f"echo 'Attaching to UART{uart.index}, which should be the {uart.value}'")
        cmd = uart.serial_command(morello_ports)
        cmd_str = " ".join(map(shlex.quote, cmd))
        print("Running `", cmd_str, "`", sep="")
        if pretend:
            cmd_str = "echo " + cmd_str
        pane.send_keys(cmd_str)
        if uart == MorelloUART.AP:
            ap_pane = pane

    # Make the AP pane active (since that is the one that is most likely to be useful
    # TODO: it would be nice if we could select the AP pane, but this does not appear to do anything.
    ap_pane.window.select_window()
    ap_pane.select_pane()


def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--pretend", "-p", action="store_true", help="Don't actually do anything")
    parser.add_argument("--debug", action="store_true", help="Print debug output")
    parser.add_argument(
        "--board-index",
        "-b",
        type=int,
        help="The Morello board to connect to (only needed if more than one is attached)",
    )
    action_group = parser.add_mutually_exclusive_group(required=True)
    action_group.add_argument(
        "--list-serial-ports",
        "--list",
        "-l",
        action="store_true",
        help="List all serial ports for the chosen board",
    )
    action_group.add_argument("--tmux", action="store_true", help="Connect to all UARTS in a tmux session")
    action_group.add_argument(
        "--tmux-minimal",
        action="store_true",
        help="Connect to AP and MCC UARTS in a tmux session",
    )
    action_group.add_argument(
        "--uart",
        "-u",
        help="Connect to a single Morello board UART",
        choices=[str(s) for s in range(8)] + [s.name for s in MorelloUART],
    )
    parser.add_argument("--force", "-f", action="store_true", help="Kill an existing Morello tmux session if present")
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    try:
        morello_ports = find_morello_board_ttys(pretend=args.pretend, board_index=args.board_index)
        assert len(morello_ports) == 8
    except ValueError as e:
        sys.exit(f"Fatal error: {e}")
    print("Found the following device nodes for the Morello UARTs:")
    print("\t", "\n\t".join(x.device for x in morello_ports), sep="")
    if args.tmux or args.tmux_minimal:
        open_tmux_windows(morello_ports, force=args.force, pretend=args.pretend, minimal=args.tmux_minimal)
        print("Created tmux session connecting to the morello board UART.")
        print("Run `tmux a -t morello-serial-ports` to connect.")
        print("Or if you are using iTerm2, attach using tmux integration with: `tmux -CC a -t morello-serial-ports`")
    elif args.uart:
        # We accept both string an integer arguments for the UART:
        try:
            uart = MorelloUART[args.uart]
        except KeyError:
            uart = MorelloUART.get(int(args.uart))
        print(
            "Will connect to UART",
            uart.index,
            f"({morello_ports[uart.index].device}) which should be the",
            uart.value,
        )
        cmd = uart.serial_command(morello_ports)
        print("Running `", " ".join(map(shlex.quote, cmd)), "`", sep="")
        if not args.pretend:
            os.execvp(cmd[0], cmd)


if __name__ == "__main__":
    main()
