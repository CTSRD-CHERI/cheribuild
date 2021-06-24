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
import datetime
import os
import shutil
import socket
import sys
import typing
from pathlib import Path

from .build_qemu import BuildCheriOSQEMU, BuildMorelloQEMU, BuildQEMU
from .cherios import BuildCheriOS
from .cross.cheribsd import BuildCHERIBSD, BuildCheriBsdMfsKernel, BuildFreeBSD, ConfigPlatform, KernelABI
from .cross.freertos import BuildFreeRTOS
from .cross.gdb import BuildGDB
from .cross.rtems import BuildRtems
from .cross.u_boot import BuildUBoot
from .disk_image import (BuildCheriBSDDiskImage, BuildDiskImageBase, BuildFreeBSDImage,
                         BuildFreeBSDWithDefaultOptionsDiskImage, BuildMinimalCheriBSDDiskImage)
from .project import CheriConfig, CPUArchitecture, SimpleProject, TargetAliasWithDependencies
from ..config.compilation_targets import CompilationTargets
from ..config.loader import ComputedDefaultValue
from ..qemu_utils import qemu_supports_9pfs, QemuOptions, riscv_bios_arguments
from ..targets import target_manager
from ..utils import AnsiColour, classproperty, coloured, find_free_port, OSInfo


def get_default_ssh_forwarding_port(addend: int):
    # chose a different port for each user (hopefully it isn't in use yet)
    return 9999 + ((os.getuid() - 1000) % 10000) + addend


class LaunchQEMUBase(SimpleProject):
    do_not_add_to_targets = True
    forward_ssh_port = True
    _can_provide_src_via_smb = False
    ssh_forwarding_port = None  # type: int
    custom_qemu_smb_mount = None
    # Add a virtio RNG to speed up random number generation
    _add_virtio_rng = True
    _enable_smbfs_support = True

    @classmethod
    def setup_config_options(cls, default_ssh_port: int = None, **kwargs):
        super().setup_config_options(**kwargs)
        cls.use_uboot = cls.add_bool_option("use-u-boot", default=False,
                                            help="Boot using U-Boot for UEFI if supported (only RISC-V)")
        cls.extra_qemu_options = cls.add_config_option("extra-options", default=[], kind=list, metavar="QEMU_OPTIONS",
                                                       help="Additional command line flags to pass to qemu-system")
        cls.logfile = cls.add_path_option("logfile", default=None, metavar="LOGFILE",
                                          help="The logfile that QEMU should use.")
        cls.log_directory = cls.add_path_option("log-directory", default=None, metavar="DIR",
                                                help="If set QEMU will log to a timestamped file in this directory. "
                                                     "Will be ignored if the 'logfile' option is set")
        cls.use_telnet = cls.add_config_option("monitor-over-telnet", kind=int, metavar="PORT", show_help=False,
                                               help="If set, the QEMU monitor will be reachable by connecting to "
                                                    "localhost at $PORT via telnet instead of using CTRL+A,C")

        cls.custom_qemu_smb_mount = cls.add_path_option("smb-host-directory", default=None, metavar="DIR",
                                                        help="If set QEMU will provide this directory over smb with "
                                                             "the name //10.0.2.4/qemu for use with mount_smbfs")
        cls.cvtrace = cls.add_bool_option("cvtrace", help="Use binary trace output instead of textual")
        # TODO: -s will no longer work, not sure anyone uses it though
        if cls.forward_ssh_port:
            default_ssh_port_computed = ComputedDefaultValue(function=lambda p, _: default_ssh_port,
                                                             as_string=str(default_ssh_port),
                                                             as_readme_string="<UID-dependent>")
            cls.ssh_forwarding_port = cls.add_config_option("ssh-forwarding-port", kind=int,
                                                            default=default_ssh_port_computed, metavar="PORT",
                                                            show_help=True,
                                                            help="The port on localhost to forward to the QEMU ssh "
                                                                 "port. You can then use `ssh root@localhost -p $PORT` "
                                                                 "to connect to the VM")
        cls.ephemeral = cls.add_bool_option("ephemeral", show_help=True,
                                            help="Run qemu in 'snapshot' mode, changes to the disk image "
                                                 "are non-persistent")

        cls.extra_tcp_forwarding = cls.add_config_option("extra-tcp-forwarding", kind=list, default=(),
                                                         help="Additional TCP bridge ports beyond ssh/22; "
                                                              "list of [hostip:]port=[guestip:]port")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.qemu_binary = None  # type: typing.Optional[Path]
        self.current_kernel = None  # type: typing.Optional[Path]
        self.disk_image = None  # type: typing.Optional[Path]
        self._project_specific_options = []
        self.bios_flags = []
        self.qemu_options = QemuOptions(self.crosscompile_target, want_debugger=self.config.wait_for_debugger)
        self.qemu_user_networking = True
        self.rootfs_path = None  # type:typing.Optional[Path]
        self._after_disk_options = []

    def get_riscv_bios_args(self) -> typing.List[str]:
        # Explicit bios args no longer needed now that qemu defaults to a different file name for CHERI
        return riscv_bios_arguments(self.crosscompile_target, self)

    def setup(self):
        super().setup()
        xtarget = self.crosscompile_target
        self._can_provide_src_via_smb = False
        if xtarget.is_riscv(include_purecap=True):
            self.bios_flags += self.get_riscv_bios_args()
            self.qemu_binary = BuildQEMU.qemu_cheri_binary(self)
            self._can_provide_src_via_smb = True
        elif xtarget.is_mips(include_purecap=True):
            self.qemu_binary = BuildQEMU.qemu_cheri_binary(self)
            self._can_provide_src_via_smb = True
        elif xtarget.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            # Only use Morello QEMU for Morello for now, not AArch64 too, as we
            # don't want to force everyone to build Morello QEMU while it's in
            # a separate branch.
            self.qemu_binary = BuildMorelloQEMU.qemu_cheri_binary(self)
            self._can_provide_src_via_smb = True
        elif xtarget.is_any_x86() or xtarget.is_aarch64(include_purecap=False):
            # Use the system QEMU instead of CHERI QEMU (for now)
            # Note: x86_64 can be either CHERI QEMU or system QEMU:
            self.add_required_system_tool("qemu-system-" + self.qemu_options.qemu_arch_sufffix)
        else:
            assert False, "Unknown target " + str(xtarget)
        if self.qemu_binary is None:
            # only CHERI QEMU supports more than one SMB share
            self._can_provide_src_via_smb = True
            binary_name = "qemu-system-" + self.qemu_options.qemu_arch_sufffix
            if (self.config.qemu_bindir / binary_name).is_file():
                self.qemu_binary = self.config.qemu_bindir / binary_name
            else:
                self.qemu_binary = Path(shutil.which(binary_name) or "/could/not/find/qemu")

    def process(self):
        assert self.qemu_binary is not None
        if not self.qemu_binary.exists():
            self.dependency_error("QEMU is missing:", self.qemu_binary, cheribuild_target="qemu")

        qemu_loader_or_kernel = self.current_kernel
        if self.use_uboot:
            xtarget = self.crosscompile_target
            uboot_xtarget = None
            if xtarget.cpu_architecture == CPUArchitecture.RISCV64:
                if xtarget.is_hybrid_or_purecap_cheri():
                    uboot_xtarget = CompilationTargets.BAREMETAL_NEWLIB_RISCV64_HYBRID
                else:
                    uboot_xtarget = CompilationTargets.BAREMETAL_NEWLIB_RISCV64

            if uboot_xtarget is not None:
                qemu_loader_or_kernel = BuildUBoot.get_firmware_path(self, self.config, cross_target=uboot_xtarget)
            else:
                self.warning("Unsupported U-Boot QEMU target", xtarget.generic_suffix, "- falling back on kernel")

        if qemu_loader_or_kernel is not None and not qemu_loader_or_kernel.exists():
            self.dependency_error("Loader/kernel is missing:", qemu_loader_or_kernel,
                                  install_instructions="Run `cheribuild.py cheribsd` or `cheribuild.py run -d`.")

        if self.forward_ssh_port and not self.is_port_available(self.ssh_forwarding_port):
            self.print_port_usage(self.ssh_forwarding_port)
            self.fatal("SSH forwarding port", self.ssh_forwarding_port, "is already in use! Make sure you don't ",
                       "already have a QEMU instance running or change the chosen port by setting the config option",
                       self.get_config_option_name("ssh_forwarding_port"))

        monitor_options = []
        if self.use_telnet:
            monitor_port = self.use_telnet
            monitor_options = ["-monitor", "telnet:127.0.0.1:" + str(monitor_port) + ",server,nowait"]
            if not self.is_port_available(monitor_port):
                self.warning("Cannot connect QEMU montitor to port", monitor_port)
                self.print_port_usage(monitor_port)
                if self.query_yes_no("Will connect the QEMU monitor to stdio instead. Continue?"):
                    monitor_options = []
                else:
                    self.fatal("Monitor port not available and stdio is not acceptable.")
                    return
        logfile_options = []
        if self.logfile:
            logfile_options = ["-D", self.logfile]
        elif self.log_directory:
            if not self.log_directory.is_dir():
                self.makedirs(self.log_directory)
            filename = "qemu-cheri-" + datetime.datetime.now().strftime("%Y%m%d_%H-%M-%S") + ".log"
            latest_symlink = self.log_directory / "qemu-cheri-latest.log"
            if latest_symlink.is_symlink():
                latest_symlink.unlink()
            if not latest_symlink.exists():
                self.create_symlink(self.log_directory / filename, latest_symlink, relative=True,
                                    cwd=self.log_directory)
            logfile_options = ["-D", self.log_directory / filename]

        if self.cvtrace:
            logfile_options += ["-cheri-trace-format", "cvtrace"]
        if self.disk_image is not None and not self.disk_image.exists():
            self.dependency_error("Disk image is missing:", self.disk_image,
                                  install_instructions="Run `cheribuild.py disk-image` or `cheribuild.py run -d`.")

        user_network_options = ""
        smb_dir_count = 0
        have_9pfs_support = (self.crosscompile_target.is_native() or
                             self.crosscompile_target.is_any_x86()) and qemu_supports_9pfs(self.qemu_binary)
        # Only default to providing the smb mount if smbd exists
        have_smbfs_support = self._can_provide_src_via_smb and shutil.which("smbd")

        def add_smb_or_9p_dir(directory, target, share_name=None, readonly=False):
            if not directory:
                return
            nonlocal user_network_options
            nonlocal smb_dir_count
            nonlocal have_9pfs_support
            nonlocal have_smbfs_support
            nonlocal qemu_command
            smb_dir_count += 1
            if have_smbfs_support and self._enable_smbfs_support:
                if smb_dir_count > 1:
                    user_network_options += ":"
                else:
                    user_network_options += ",smb="
                share_name_option = ""
                if share_name is not None:
                    share_name_option = "<<<" + share_name
                else:
                    share_name = "qemu{}".format(smb_dir_count)
                user_network_options += str(directory) + share_name_option + ("@ro" if readonly else "")
                guest_cmd = coloured(AnsiColour.yellow,
                                     "mkdir -p {target} && mount_smbfs -I 10.0.2.4 -N //10.0.2.4/{share_name}"
                                     " {target}".format(target=target, share_name=share_name))
                self.info("Providing ", coloured(AnsiColour.green, str(directory)),
                          coloured(AnsiColour.cyan, " over SMB to the guest. Use `"), guest_cmd,
                          coloured(AnsiColour.cyan, "` to mount it"), sep="")
            if have_9pfs_support:
                if smb_dir_count > 1:
                    return  # FIXME: 9pfs panics if there is more than one device
                # Also provide it via virtfs:
                virtfs_args.append("-virtfs")
                virtfs_args.append("local,id=virtfs{n},mount_tag={tag},path={path},security_model=none{ro}".format(
                    n=smb_dir_count, path=directory, tag=share_name, ro=",readonly" if readonly else ""))
                guest_cmd = coloured(AnsiColour.yellow,
                                     "mkdir -p {tgt} && mount -t virtfs -o trans=virtio,version=9p2000.L {share_name} "
                                     "{tgt}".format(tgt=target, share_name=share_name))
                self.info("Providing ", coloured(AnsiColour.green, str(directory)),
                          coloured(AnsiColour.cyan, " over 9pfs to the guest. Use `"), guest_cmd,
                          coloured(AnsiColour.cyan, "` to mount it"), sep="")

        virtfs_args = []
        if have_smbfs_support or have_9pfs_support:  # for running CheriBSD + FreeBSD
            add_smb_or_9p_dir(self.custom_qemu_smb_mount, "/mnt")
            add_smb_or_9p_dir(self.config.source_root, "/srcroot", share_name="source_root", readonly=True)
            add_smb_or_9p_dir(self.config.build_root, "/buildroot", share_name="build_root", readonly=False)
            add_smb_or_9p_dir(self.config.output_root, "/outputroot", share_name="output_root", readonly=True)
            add_smb_or_9p_dir(self.rootfs_path, "/rootfs", share_name="rootfs", readonly=False)

        if self.forward_ssh_port:
            user_network_options += ",hostfwd=tcp::" + str(self.ssh_forwarding_port) + "-:22"
            # bind the qemu ssh port to the hosts port
            # qemu_command += ["-redir", "tcp:" + str(self.ssh_forwarding_port) + "::22"]
            print(coloured(AnsiColour.green, "\nListening for SSH connections on localhost:", self.ssh_forwarding_port,
                           sep=""))

        for x in self.extra_tcp_forwarding:
            # QEMU insists on having : field delimeters; add if not given
            hg = x.split('=')
            if len(hg) != 2:
                self.fatal("Bad extra-tcp-forwarding (not just one '=' in '%s')" % x)
            (h, g) = hg
            if ':' not in h:
                h = ':' + h
            if ':' not in g:
                g = ':' + g

            user_network_options += ",hostfwd=tcp:" + h + "-" + g

        if self.ephemeral:
            self._after_disk_options += ["-snapshot"]

        # input("Press enter to continue")
        qemu_command = self.qemu_options.get_commandline(qemu_command=self.qemu_binary,
                                                         kernel_file=qemu_loader_or_kernel,
                                                         disk_image=self.disk_image,
                                                         add_network_device=self.qemu_user_networking,
                                                         bios_args=self.bios_flags,
                                                         user_network_args=user_network_options,
                                                         trap_on_unrepresentable=self.config.trap_on_unrepresentable,
                                                         debugger_on_cheri_trap=self.config.debugger_on_cheri_trap,
                                                         add_virtio_rng=self._add_virtio_rng)
        qemu_command += self._project_specific_options + self._after_disk_options + monitor_options
        qemu_command += logfile_options + self.extra_qemu_options + virtfs_args
        self.info("About to run QEMU with image", self.disk_image, "and loader/kernel", qemu_loader_or_kernel)

        if self.config.wait_for_debugger or self.config.debugger_in_tmux_pane:
            gdb_socket_placeholder = find_free_port(preferred_port=1234)
            gdb_port = gdb_socket_placeholder.port if self.config.gdb_random_port else 1234
            self.info("QEMU is waiting for GDB to attach (using `target remote :{}`)."
                      " Once connected enter 'continue\\n' to continue booting".format(gdb_port))

            def gdb_command(main_binary, bp=None, extra_binary=None) -> str:
                gdb_cmd = BuildGDB.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/gdb"
                # Set the sysroot to ensure that the .debug file is loaded from <ROOTFS>/usr/lib/debug/boot/kernel
                # It seems this does not always work as expected, so also set substitute-path and debug-file-directory.
                assert self.rootfs_path is not None
                result = [gdb_cmd, main_binary,
                          "--init-eval-command=set sysroot " + str(self.rootfs_path),
                          "--init-eval-command=set substitute-path " + str(self.rootfs_path) + " /",
                          "--init-eval-command=set debug-file-directory " + str(self.rootfs_path / "usr/lib/debug")]
                # Once the file has been loaded set a breakpoint on panic() and connect to the remote host
                if bp:
                    result.append("--eval-command=break " + bp)
                result.append("--eval-command=target remote localhost:{}".format(gdb_port))
                result.append("--eval-command=continue")
                if extra_binary:
                    result.append("--init-eval-command=add-symbol-file -o 0 " + str(extra_binary))
                return self.commandline_to_str(result)

            self.info("To start and connect GDB run the following command in another terminal:")
            path_to_kernel = self.current_kernel
            if path_to_kernel is None:
                path_to_kernel = self.rootfs_path / "boot/kernel/kernel"
            # Prefer the file with debug info
            kernel_full_guess = path_to_kernel.with_name(path_to_kernel.name + ".full")
            if kernel_full_guess.exists():
                path_to_kernel = kernel_full_guess
            if self.config.qemu_debug_program:
                self.info("\t", coloured(AnsiColour.red, gdb_command(self.rootfs_path / self.config.qemu_debug_program,
                                                                     "main", path_to_kernel)), sep="")
            else:
                self.info("\t", coloured(AnsiColour.red, gdb_command(path_to_kernel, "panic")), sep="")
                self.info("If you would like to debug /sbin/init (or any other statically linked program) run this"
                          " inside GDB:")
                self.info(coloured(AnsiColour.red, "\tadd-symbol-file -o 0", str(self.rootfs_path / "sbin/init")))
                self.info("For dynamically linked programs you will have to add libraries at the correct offset. For "
                          "example:")
                self.info(coloured(AnsiColour.red, "\tadd-symbol-file -o 0x40212000",
                                   str(self.rootfs_path / "lib/libc.so.7")))
                self.info("If you would like to debug a userspace program (e.g. sbin/init):")
                self.info("\t", coloured(AnsiColour.red, gdb_command(self.rootfs_path / "sbin/init", "main",
                                                                     path_to_kernel)), sep="")
            self.info("Launching QEMU in suspended state...")

            def start_gdb_in_tmux_pane(command):
                import libtmux
                server = libtmux.Server()
                if server is None:
                    raise Exception("Tmux server not found")
                sessions = server.list_sessions()
                if len(sessions) != 1:
                    raise Exception("There should be only one tmux session running")
                session = server.list_sessions()[0]
                window = session.attached_window  # type: libtmux.Window
                pane = window.attached_pane
                # Note: multiply by two since most monospace fonts are taller than wide
                vertical = int(pane.height) * 2 > int(pane.width)
                self.verbose_print("Current window h =", window.height, "w =", window.width)
                self.verbose_print("Current pane h =", window.attached_pane.height, "w =", window.attached_pane.width)
                if self.config.pretend:
                    self.info("Would have split current tmux pane", "vertically." if vertical else "horizontally.")
                    self.info("Would have run", coloured(AnsiColour.yellow, command), "in new pane.")
                else:
                    pane = pane.split_window(vertical=vertical, attach=False)
                    pane.send_keys(command)

            if self.config.debugger_in_tmux_pane:
                try:
                    if "TMUX" not in os.environ:
                        raise Exception("--debugger-in-tmux-pane set, but not in a tmux session")
                    start_gdb_in_tmux_pane(gdb_command(path_to_kernel, "panic"))
                except ImportError:
                    self.info(coloured(AnsiColour.red, "libtmux not installed, impossible to automatically start gdb"))
                except Exception as e:
                    self.info(coloured(AnsiColour.red, "Unable to start gdb in tmux: {}".format(e)))

            gdb_socket_placeholder.socket.close()  # the port is now available for qemu
            qemu_command += ["-gdb", "tcp::{}".format(gdb_port),  # wait for gdb on localhost:1234
                             "-S"  # freeze CPU at startup (use 'c' to start execution)
                             ]
        # We want stdout/stderr here even when running with --quiet
        # FIXME: it seems like QEMU often breaks the line wrapping state: https://bugs.launchpad.net/qemu/+bug/1857449
        self.run_cmd(qemu_command, stdout=sys.stdout, stderr=sys.stderr, give_tty_control=True)

    def print_port_usage(self, port: int):
        print("Port", port, "usage information:")
        if OSInfo.IS_FREEBSD:
            self.run_cmd("sockstat", "-P", "tcp", "-p", str(port))
        elif OSInfo.IS_LINUX:
            if shutil.which("ss"):
                self.run_cmd("sh", "-c", "ss -tulpne | grep \":" + str(port) + "\"")
            elif shutil.which("netstat"):
                self.run_cmd("sh", "-c", "netstat -tulpne | grep \":" + str(port) + "\"")
            else:
                self.info(coloured(AnsiColour.yellow, "Missing ss and netstat; unable to report port usage"))
        elif OSInfo.IS_MAC:
            self.run_cmd("lsof", "-nP", "-iTCP:" + str(port))
        else:
            self.info(coloured(AnsiColour.yellow, "Don't know how to report port usage on this OS"))

    @staticmethod
    def is_port_available(port: int):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False


class AbstractLaunchFreeBSD(LaunchQEMUBase):
    do_not_add_to_targets = True
    _can_provide_src_via_smb = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.remote_kernel_path = cls.add_config_option(
            "remote-kernel-path", show_help=True,
            help="When set rsync will be used to update the kernel image from a remote host before launching QEMU. "
                 "Useful when building and running on separate machines.")
        cls.kernel_config = cls.add_config_option(
            "alternative-kernel", show_help=True,
            help="Select the kernel to run by specifying the kernel build configuration name."
                 "The list of available kernel configurations is given by --list-kernels")
        cls.kernel_abi = cls.add_config_option(
            "kernel-abi", show_help=True,
            kind=KernelABI, enum_choices=[KernelABI.HYBRID, KernelABI.PURECAP],
            help="Select extra kernel variant with the given ABI to run.")

    def __init__(self, config: CheriConfig, freebsd_class: "typing.Type[BuildFreeBSD]" = None,
                 disk_image_class: "typing.Type[BuildFreeBSDImage]" = None, needs_disk_image=True):
        super().__init__(config)
        if freebsd_class is None and disk_image_class is not None:
            # noinspection PyProtectedMember
            self.source_project = disk_image_class.get_instance(self).source_project
        else:
            self.source_project = freebsd_class.get_instance(self)

        if self.kernel_config:
            if self.kernel_config not in self._valid_kernel_configs():
                self.fatal("Selected kernel configuration", self.kernel_config, "is not available")
                self._list_kernel_configs()
        else:
            config_filters = {}
            if self.kernel_abi:
                if self.crosscompile_target.is_hybrid_or_purecap_cheri():
                    config_filters["kABI"] = self.kernel_abi
                else:
                    self.warning("Can not select kernel ABI to run for non-CHERI target, ignoring --kernel-abi")
            self.kernel_config = self.source_project.default_kernel_config(ConfigPlatform.QEMU, **config_filters)

        self.current_kernel = self.source_project.get_kernel_install_path(self.kernel_config)

        if self.qemu_options.can_boot_kernel_directly:
            kern_module_path_arg = self.source_project.get_kern_module_path_arg(self.kernel_config)
            if kern_module_path_arg:
                self._project_specific_options += ["-append", kern_module_path_arg]
        self.rootfs_path = self.source_project.get_rootfs_dir(self, config=config)
        if needs_disk_image:
            self.disk_image = disk_image_class.get_instance(self).disk_image_path

    def _valid_kernel_configs(self):
        return self.source_project.get_kernel_configs(platform=ConfigPlatform.QEMU)

    def _list_kernel_configs(self):
        self.info("Available kernels for qemu:")
        for conf in self._valid_kernel_configs():
            path = self.source_project.get_kernel_install_path(conf)
            if conf == self.source_project.kernel_config:
                self.info("*", conf, path)
            else:
                self.info(conf, path)

    def _copy_kernel_image_from_remote_host(self):
        scp_path = os.path.expandvars(self.remote_kernel_path)
        self.info("Copying kernel image from build machine:", scp_path)
        self.makedirs(self.current_kernel.parent)
        self.copy_remote_file(scp_path, self.current_kernel)

    def process(self):
        if self.config.list_kernels:
            self._list_kernel_configs()
            return
        if self.remote_kernel_path is not None:
            self._copy_kernel_image_from_remote_host()
        super().process()


class _RunMultiArchFreeBSDImage(AbstractLaunchFreeBSD):
    do_not_add_to_targets = True
    include_os_in_target_suffix = False
    _freebsd_class = None  # type: typing.Type[BuildFreeBSD]
    _source_class = None  # type: typing.Type[BuildDiskImageBase]

    @classproperty
    def supported_architectures(self):
        return self._source_class.supported_architectures

    @classmethod
    def get_cross_target_index(cls, **kwargs):
        xtarget = kwargs.get('xtarget', cls._xtarget)
        for idx, value in enumerate(cls.supported_architectures):
            if xtarget is value:
                return idx
        assert xtarget is None
        return -1  # return -1 for NONE

    @classproperty
    def default_architecture(self):
        return self._source_class.default_architecture

    @classmethod
    def dependencies(cls: "typing.Type[_RunMultiArchFreeBSDImage]", config: CheriConfig):
        xtarget = cls.get_crosscompile_target(config)
        qemu = "qemu"
        if xtarget.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
            qemu = "morello-qemu"
        result = [qemu, cls._source_class.get_class_for_target(xtarget).target]
        return result

    def __init__(self, config, *, needs_disk_image=True):
        super().__init__(config, needs_disk_image=needs_disk_image, freebsd_class=self._freebsd_class,
                         disk_image_class=self._source_class.get_class_for_target(self.get_crosscompile_target(config)))


class LaunchCheriBSD(_RunMultiArchFreeBSDImage):
    target = "run"
    _source_class = BuildCheriBSDDiskImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        if "default_ssh_port" in kwargs:
            # Subclass case
            super().setup_config_options(**kwargs)
        else:
            add_to_port = cls.get_cross_target_index()
            if add_to_port != 0:  # 1 is used by run-purecap
                add_to_port += 1
            super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(add_to_port), **kwargs)

    @classmethod
    def dependencies(cls, config: CheriConfig):
        result = super().dependencies(config)
        # RISCV needs OpenSBI/BBL to run:
        # Note: QEMU 4.2+ embeds opensbi, for CHERI, we have to use BBL (for now):
        xtarget = cls.get_crosscompile_target(config)
        if xtarget.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            result.append("bbl-baremetal-riscv64-purecap")
        return result

    def run_tests(self):
        self.target_info.run_cheribsd_test_script("run_cheribsd_tests.py", disk_image_path=self.disk_image,
                                                  kernel_path=self.current_kernel)


class LaunchCheriOSQEMU(LaunchQEMUBase):
    target = "run-cherios"
    dependencies = ["cherios-qemu", "cherios"]
    supported_architectures = [CompilationTargets.CHERIOS_MIPS_PURECAP]
    forward_ssh_port = False
    qemu_user_networking = False
    hide_options_from_help = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(40), **kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # FIXME: these should be config options
        cherios = BuildCheriOS.get_instance(self, config)
        self.current_kernel = BuildCheriOS.get_build_dir(self) / "boot/cherios.elf"
        self.disk_image = self.config.output_root / "cherios-disk.img"
        self._project_specific_options = ["-no-reboot"]

        if cherios.build_net:
            self._after_disk_options.extend([
                "-netdev", "tap,id=tap0,ifname=cherios_tap,script=no,downscript=no",
                "-device", "virtio-net-device,netdev=tap0",
                ])

        if cherios.smp_cores > 1:
            self._project_specific_options.append("-smp")
            self._project_specific_options.append(str(cherios.smp_cores))

        self.qemu_options.virtio_disk = True  # CheriOS needs virtio
        self.qemu_user_networking = False

    def setup(self):
        super().setup()
        self.qemu_binary = BuildCheriOSQEMU.qemu_binary(self)

    def process(self):
        if not self.disk_image.exists():
            if self.query_yes_no("CheriOS disk image is missing. Would you like to create a zero-filled 1MB image?"):
                size_flag = "bs=128m" if OSInfo.IS_MAC else "bs=128M"
                self.run_cmd("dd", "if=/dev/zero", "of=" + str(self.disk_image), size_flag, "count=1")
        super().process()


class LaunchRtemsQEMU(LaunchQEMUBase):
    target = "run-rtems"
    dependencies = ["rtems"]
    supported_architectures = [CompilationTargets.RTEMS_RISCV64_PURECAP]
    forward_ssh_port = False
    qemu_user_networking = False
    _enable_smbfs_support = False
    _add_virtio_rng = False

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(default_ssh_port=None, **kwargs)

    def get_riscv_bios_args(self) -> typing.List[str]:
        # Run a simple RTEMS shell application (run in machine mode using the -bios QEMU argument)
        return ["-bios", str(BuildRtems.get_build_dir(self) / "riscv/rv64xcheri_qemu/testsuites/samples/capture.exe")]

    def process(self):
        super().process()


class LaunchFreeRTOSQEMU(LaunchQEMUBase):
    target = "run-freertos"
    dependencies = ["freertos"]
    supported_architectures = [CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP,
                               CompilationTargets.BAREMETAL_NEWLIB_RISCV64]
    forward_ssh_port = False
    qemu_user_networking = False
    _enable_smbfs_support = False
    _add_virtio_rng = False

    default_demo = "RISC-V-Generic"
    default_demo_app = "main_blinky"

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(defaultSshPort=None, **kwargs)

        cls.demo = cls.add_config_option(
            "demo", metavar="DEMO", show_help=True,
            default=cls.default_demo,
            help="The FreeRTOS Demo to run.")  # type: str

        cls.demo_app = cls.add_config_option(
            "prog", metavar="PROG", show_help=True,
            default=cls.default_demo_app,
            help="The FreeRTOS program to run.")  # type: str

        cls.demo_bsp = cls.add_config_option(
            "bsp", metavar="BSP", show_help=True,
            default=ComputedDefaultValue(function=lambda _, p: p.default_demo_bsp(),
                                         as_string="target-dependent default"),
            help="The FreeRTOS BSP to run. This is only valid for the "
                 "paramterized RISC-V-Generic. The BSP option chooses "
                 "platform, RISC-V arch and RISC-V abi in the "
                 "$platform-$arch-$abi format. See RISC-V-Generic/README for more details")

    def default_demo_bsp(self):
        return "qemu_virt-" + self.target_info.get_riscv_arch_string(self.crosscompile_target, softfloat=True) + "-" + \
               self.target_info.get_riscv_abi(self.crosscompile_target, softfloat=True)

    def get_riscv_bios_args(self) -> typing.List[str]:
        # Run a FreeRTOS demo application (run in machine mode using the -bios QEMU argument)
        return ["-bios", str(BuildFreeRTOS.get_install_dir(self)) + "/FreeRTOS/Demo/" +
                self.demo + "_" + self.demo_app + ".elf"]

    def process(self):
        super().process()


class LaunchFreeBSD(_RunMultiArchFreeBSDImage):
    target = "run-freebsd"
    hide_options_from_help = True
    _source_class = BuildFreeBSDImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(10 + add_to_port), **kwargs)


class LaunchFreeBSDWithDefaultOptions(_RunMultiArchFreeBSDImage):
    target = "run-freebsd-with-default-options"
    hide_options_from_help = True
    _source_class = BuildFreeBSDWithDefaultOptionsDiskImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(20 + add_to_port), **kwargs)


class LaunchMinimalCheriBSD(LaunchCheriBSD):
    target = "run-minimal"
    _source_class = BuildMinimalCheriBSDDiskImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(20 + add_to_port), **kwargs)

    def run_tests(self):
        self.target_info.run_cheribsd_test_script("run_cheribsd_tests.py", "--minimal-image")


class LaunchCheriBsdMfsRoot(LaunchMinimalCheriBSD):
    target = "run-mfs-root"
    _freebsd_class = BuildCheriBsdMfsKernel
    _source_class = BuildCheriBsdMfsKernel   # no disk image, ignore type checker error

    def __init__(self, config):
        # noinspection PyTypeChecker
        super().__init__(config, needs_disk_image=False)
        if self.config.use_minimal_benchmark_kernel:
            kernel_config = self.source_project.default_kernel_config(ConfigPlatform.QEMU, benchmark=True)
            self.current_kernel = self.source_project.get_kernel_install_path(kernel_config)
            if str(self.remote_kernel_path).endswith("MFS_ROOT"):
                self.remote_kernel_path += "_BENCHMARK"
        self.rootfs_path = BuildCHERIBSD.get_rootfs_dir(self, config)


# Backwards compatibility:
target_manager.add_target_alias("run-cheri", "run-mips64-hybrid", deprecated=True)
target_manager.add_target_alias("run-purecap", "run-mips64-purecap", deprecated=True)
target_manager.add_target_alias("run-minimal-cheri", "run-minimal-mips64-hybrid", deprecated=True)
target_manager.add_target_alias("run-minimal-purecap", "run-minimal-mips64-purecap", deprecated=True)
target_manager.add_target_alias("run-native", "run-amd64", deprecated=True)
target_manager.add_target_alias("run-x86_64", "run-amd64", deprecated=True)


class BuildAndRunCheriBSD(TargetAliasWithDependencies):
    target = "build-and-run-cheribsd"
    include_os_in_target_suffix = False
    dependencies = ["cheribsd", "disk-image", "run"]
    direct_dependencies_only = True  # only rebuild toolchain, bbl or GDB if --include-dependencies is passed

    @classproperty
    def supported_architectures(self):
        return LaunchCheriBSD.supported_architectures


class BuildAndRunFreeBSD(TargetAliasWithDependencies):
    target = "build-and-run-freebsd"
    include_os_in_target_suffix = False
    dependencies = ["freebsd", "disk-image-freebsd", "run-freebsd"]
    direct_dependencies_only = True  # only rebuild toolchain, bbl or GDB if --include-dependencies is passed

    @classproperty
    def supported_architectures(self):
        return LaunchFreeBSD.supported_architectures


class BuildAll(TargetAliasWithDependencies):
    target = "all"
    dependencies = ["qemu", "sdk", "disk-image", "run"]

    @classproperty
    def supported_architectures(self):
        return LaunchCheriBSD.supported_architectures
