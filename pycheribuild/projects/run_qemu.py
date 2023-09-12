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
from enum import Enum
from pathlib import Path
from typing import Optional

from .build_qemu import BuildQEMU, BuildQEMUBase, BuildUpstreamQEMU
from .cherios import BuildCheriOS
from .cross.cheribsd import BuildCHERIBSD, BuildCheriBsdMfsKernel, BuildFreeBSD, ConfigPlatform, KernelABI
from .cross.gdb import BuildGDB
from .cross.u_boot import BuildUBoot
from .disk_image import (
    BuildCheriBSDDiskImage,
    BuildDiskImageBase,
    BuildFreeBSDImage,
    BuildFreeBSDWithDefaultOptionsDiskImage,
    BuildMinimalCheriBSDDiskImage,
)
from .project import CheriConfig, ComputedDefaultValue, CPUArchitecture, Project
from .simple_project import BoolConfigOption, SimpleProject, TargetAliasWithDependencies
from ..config.compilation_targets import CompilationTargets
from ..config.target_info import CrossCompileTarget
from ..qemu_utils import QemuOptions, qemu_supports_9pfs, riscv_bios_arguments
from ..utils import AnsiColour, OSInfo, classproperty, coloured, fatal_error, find_free_port, is_jenkins_build


def get_default_ssh_forwarding_port(addend: int):
    # chose a different port for each user (hopefully it isn't in use yet)
    return 9999 + ((os.getuid() - 1000) % 10000) + addend


class QEMUType(Enum):
    DEFAULT = "default"
    CHERI = "cheri"
    MORELLO = "morello"
    UPSTREAM = "upstream"
    SYSTEM = "system"
    CUSTOM = "custom"


class ChosenQEMU:
    def __init__(self, cls: "Optional[type[BuildQEMUBase]]", binary: Optional[Path],
                 can_provide_src_via_smb: Optional[bool]):
        self.cls = cls
        self._binary = binary
        self._can_provide_src_via_smb = can_provide_src_via_smb
        self._setup = False

    @property
    def binary(self) -> Path:
        assert self._setup, "Cannot get binary before LaunchQEMUBase has called our setup"
        return self._binary

    @property
    def can_provide_src_via_smb(self) -> bool:
        assert self._setup, "Cannot get SMBD status before LaunchQEMUBase has called our setup"
        return self._can_provide_src_via_smb

    def setup(self, launch):
        assert not self._setup, "Called setup twice"
        self._setup = True

        if self.cls is not None:
            assert self._binary is not None, "cheribuild-built QEMU should be known"

        if self._binary is not None:
            assert self._can_provide_src_via_smb is not None, "Known binary should have known SMBD status"
            return

        if self._binary is None:
            assert self._can_provide_src_via_smb is None, "Unknown binary cannot have known SMBD status"

        # No cheribuild class and everything unknown, must be a system binary,
        # either explicitly or as the default. If using the default QEMU we
        # prefer CHERI QEMU's corresponding non-CHERI binary if it exists, in
        # part due to its SMBD support
        assert launch.use_qemu == QEMUType.SYSTEM or launch.use_qemu == QEMUType.DEFAULT, \
               "Unexpected use_qemu for lazy binary location: " + str(launch.use_qemu)
        binary_name = "qemu-system-" + launch.qemu_options.qemu_arch_sufffix
        if (launch.config.qemu_bindir / binary_name).is_file() and launch.use_qemu != QEMUType.SYSTEM:
            # Only CHERI QEMU supports more than one SMB share
            self._can_provide_src_via_smb = True
            self._binary = launch.config.qemu_bindir / binary_name
        else:
            # Only CHERI QEMU supports more than one SMB share; conservatively
            # guess what kind of QEMU this is
            self._can_provide_src_via_smb = launch.crosscompile_target.is_hybrid_or_purecap_cheri()
            launch.check_required_system_tool(binary_name)
            binary_path = shutil.which(binary_name)
            if not binary_path:
                launch.fatal("Could not find system QEMU", binary_name)
                binary_path = "/could/not/find/qemu"
            self._binary = Path(binary_path)


class LaunchQEMUBase(SimpleProject):
    do_not_add_to_targets = True
    forward_ssh_port = True
    _can_provide_src_via_smb = False
    ssh_forwarding_port: Optional[int] = None
    custom_qemu_smb_mount = None
    needs_sysroot = False
    # Add a virtio RNG to speed up random number generation
    _add_virtio_rng = True
    _enable_smbfs_support = True
    _cached_chosen_qemu: Optional[ChosenQEMU] = None
    use_qemu: QEMUType
    custom_qemu_path: Optional[Path]
    kernel_project: Optional[Project] = None
    disk_image_project: Optional[Project] = None
    _uses_disk_image = True

    use_uboot = BoolConfigOption("use-u-boot", default=False,
                                 help="Boot using U-Boot for UEFI if supported (only RISC-V)")
    cvtrace = BoolConfigOption("cvtrace", help="Use binary trace output instead of textual")

    @classmethod
    def setup_config_options(cls, default_ssh_port: "Optional[int]" = None, **kwargs):
        super().setup_config_options(**kwargs)
        cls.use_qemu = typing.cast(QEMUType, cls.add_config_option(
            "use-qemu", kind=QEMUType, default=QEMUType.DEFAULT, enum_choice_strings=[t.value for t in QEMUType],
            help="The QEMU type to run with. When set to 'custom', the 'custom-qemu-path' option must also be set."))
        cls.custom_qemu_path = cls.add_optional_path_option("custom-qemu-path", help="Path to the custom QEMU binary")
        cls.extra_qemu_options = cls.add_list_option("extra-options", metavar="QEMU_OPTIONS",
                                                     help="Additional command line flags to pass to qemu-system")
        cls.logfile = cls.add_optional_path_option("logfile", metavar="LOGFILE",
                                                   help="The logfile that QEMU should use.")
        cls.log_directory = cls.add_optional_path_option(
            "log-directory", metavar="DIR",
            help="If set QEMU will log to a timestamped file in this directory. "
                 "Will be ignored if the 'logfile' option is set")
        cls.use_telnet = cls.add_config_option("monitor-over-telnet", kind=int, metavar="PORT", show_help=False,
                                               help="If set, the QEMU monitor will be reachable by connecting to "
                                                    "localhost at $PORT via telnet instead of using CTRL+A,C")

        cls.custom_qemu_smb_mount = cls.add_optional_path_option(
            "smb-host-directory", metavar="DIR",
            help="If set QEMU will provide this directory over smb with the name //10.0.2.4/qemu for use with "
                 "mount_smbfs")
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
        cls.ephemeral = False
        if cls._uses_disk_image:
            cls.ephemeral = cls.add_bool_option(
                "ephemeral", show_help=True,
                help="Run qemu in 'snapshot' mode, changes to the disk image are non-persistent")

        # TODO: add a shortcut for vnc?
        cls.extra_tcp_forwarding = cls.add_list_option(
            "extra-tcp-forwarding",
            help="Additional TCP bridge ports beyond ssh/22; list of [hostip:]port=[guestip:]port")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_kernel: Optional[Path] = None
        self.disk_image: Optional[Path] = None
        self.disk_image_format = "raw"
        self._project_specific_options = []
        self.bios_flags = []
        self.qemu_options = QemuOptions(self.crosscompile_target, want_debugger=self.config.wait_for_debugger)
        self.qemu_user_networking = True
        self.rootfs_path: Optional[Path] = None
        self._after_disk_options = []

    def get_riscv_bios_args(self) -> "list[str]":
        # Explicit bios args no longer needed now that qemu defaults to a different file name for CHERI
        return riscv_bios_arguments(self.crosscompile_target, self)

    @classmethod
    def targets_reset(cls):
        super().targets_reset()
        cls._cached_chosen_qemu = None

    @classmethod
    def get_chosen_qemu(cls, config: CheriConfig):
        if cls._cached_chosen_qemu:
            return cls._cached_chosen_qemu

        xtarget = cls.get_crosscompile_target()
        can_provide_src_via_smb = False
        supported_qemu_classes = []
        if xtarget.is_mips(include_purecap=True) or xtarget.is_riscv(include_purecap=True):
            can_provide_src_via_smb = True
            supported_qemu_classes += [BuildQEMU]
            if not xtarget.is_hybrid_or_purecap_cheri():
                supported_qemu_classes += [BuildUpstreamQEMU, None]
        elif xtarget.is_aarch64(include_purecap=True):
            can_provide_src_via_smb = True
            # Prefer CHERI QEMU for AArch64 like other architectures.
            supported_qemu_classes += [BuildQEMU]
            if not xtarget.is_hybrid_or_purecap_cheri():
                supported_qemu_classes += [BuildUpstreamQEMU, None]
        elif xtarget.is_any_x86() or xtarget.is_aarch64(include_purecap=False):
            # Default to CHERI QEMU instead of the system QEMU (for now)
            # Note: x86_64 can be either CHERI QEMU or system QEMU:
            supported_qemu_classes += [BuildQEMU, BuildUpstreamQEMU, None]
        else:
            assert False, "Unknown target " + str(xtarget)

        if cls.use_qemu == QEMUType.CUSTOM:
            # Only CHERI QEMU supports more than one SMB share; conservatively
            # guess what kind of QEMU this is
            can_provide_src_via_smb = xtarget.is_hybrid_or_purecap_cheri()
            if not cls.custom_qemu_path:
                fatal_error("Must specify path to custom QEMU with --" + cls.target + "/custom-qemu-path",
                            pretend=config.pretend)
                qemu_binary = Path("/no/custom/path/to/qemu")
            else:
                qemu_binary = Path(cls.custom_qemu_path)
            if not qemu_binary.is_file():
                fatal_error("Custom QEMU", cls.custom_qemu_path, "is not a file", pretend=config.pretend)
            qemu_class = None
        else:
            if cls.use_qemu == QEMUType.DEFAULT:
                qemu_class = supported_qemu_classes[0]
                qemu_binary = None
            elif cls.use_qemu in (QEMUType.CHERI, QEMUType.MORELLO, QEMUType.UPSTREAM):
                qemu_class = {
                    QEMUType.CHERI: BuildQEMU,
                    QEMUType.UPSTREAM: BuildUpstreamQEMU,
                }[cls.use_qemu]
                if qemu_class not in supported_qemu_classes:
                    fatal_error("Cannot use", cls.use_qemu.value, "QEMU with target", xtarget.generic_target_suffix,
                                pretend=config.pretend)
                    qemu_class = None
                    qemu_binary = Path("/target/not/supported/with/this/qemu")
                else:
                    qemu_binary = None
            else:
                assert cls.use_qemu == QEMUType.SYSTEM, "Unknown use_qemu " + str(cls.use_qemu)
                qemu_class = None
                qemu_binary = None

            # None means determine it from qemu_class; non-None is used for a
            # dummy value when pretending and the requested combination is not
            # supported.
            if qemu_binary is None:
                if qemu_class is None:
                    # Deferred until setup time when we have an instance (need
                    # qemu_options member and check_required_system_tool)
                    can_provide_src_via_smb = None
                    qemu_binary = None
                else:
                    # Only CHERI QEMU supports more than one SMB share
                    can_provide_src_via_smb = qemu_class == BuildQEMU
                    qemu_binary = qemu_class.qemu_binary(None, xtarget=xtarget, config=config)

        cls._cached_chosen_qemu = ChosenQEMU(qemu_class, qemu_binary, can_provide_src_via_smb)
        return cls._cached_chosen_qemu

    @property
    def chosen_qemu(self):
        return self.get_chosen_qemu(self.config)

    def setup(self):
        super().setup()
        if self.crosscompile_target.is_riscv(include_purecap=True):
            self.bios_flags += self.get_riscv_bios_args()
        self.chosen_qemu.setup(self)

    def process(self):
        if not self.chosen_qemu.binary.exists():
            self.dependency_error("QEMU is missing:", self.chosen_qemu.binary,
                                  cheribuild_target=self.chosen_qemu.cls.target if self.chosen_qemu.cls else None,
                                  cheribuild_xtarget=CompilationTargets.NATIVE)

        qemu_loader_or_kernel = self.current_kernel
        if self.use_uboot:
            xtarget = self.crosscompile_target
            uboot_xtarget = None
            if xtarget.cpu_architecture == CPUArchitecture.RISCV64:
                if xtarget.is_hybrid_or_purecap_cheri():
                    uboot_xtarget = CompilationTargets.FREESTANDING_RISCV64_HYBRID
                else:
                    uboot_xtarget = CompilationTargets.FREESTANDING_RISCV64

            if uboot_xtarget is not None:
                qemu_loader_or_kernel = BuildUBoot.get_firmware_path(self, self.config, cross_target=uboot_xtarget)
            else:
                self.warning("Unsupported U-Boot QEMU target", xtarget.generic_target_suffix,
                             "- falling back on kernel")

        if qemu_loader_or_kernel is not None and not qemu_loader_or_kernel.exists():
            kernel_target_name = self.kernel_project.target if self.kernel_project is not None else None
            kernel_xtarget = self.kernel_project.crosscompile_target if self.kernel_project is not None else None
            self.dependency_error("Loader/kernel is missing:", qemu_loader_or_kernel,
                                  cheribuild_target=kernel_target_name, cheribuild_xtarget=kernel_xtarget)

        if self.forward_ssh_port and not self.is_port_available(self.ssh_forwarding_port):
            self.print_port_usage(self.ssh_forwarding_port)
            self.fatal("SSH forwarding port", self.ssh_forwarding_port, "is already in use! Make sure you don't "
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
            disk_image_target_name = self.disk_image_project.target if self.disk_image_project is not None else None
            disk_image_xtarget = (
                self.disk_image_project.crosscompile_target if self.disk_image_project is not None else None)
            self.dependency_error("Disk image is missing:", self.disk_image, cheribuild_target=disk_image_target_name,
                                  cheribuild_xtarget=disk_image_xtarget)

        user_network_options = ""
        smb_dir_count = 0
        have_9pfs_support = (
            self.crosscompile_target.is_native() or self.crosscompile_target.is_any_x86()
        ) and qemu_supports_9pfs(self.chosen_qemu.binary, config=self.config)
        # Only default to providing the smb mount if smbd exists
        have_smbfs_support = self.chosen_qemu.can_provide_src_via_smb and shutil.which("smbd")

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
                    share_name = f"qemu{smb_dir_count}"
                user_network_options += str(directory) + share_name_option + ("@ro" if readonly else "")
                guest_cmd = coloured(AnsiColour.yellow,
                                     f"mkdir -p {target} && mount_smbfs -I 10.0.2.4 -N //10.0.2.4/{share_name}"
                                     f" {target}")
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
        qemu_command = self.qemu_options.get_commandline(qemu_command=self.chosen_qemu.binary,
                                                         kernel_file=qemu_loader_or_kernel,
                                                         disk_image=self.disk_image,
                                                         disk_image_format=self.disk_image_format,
                                                         add_network_device=self.qemu_user_networking,
                                                         bios_args=self.bios_flags,
                                                         user_network_args=user_network_options,
                                                         trap_on_unrepresentable=self.config.trap_on_unrepresentable,
                                                         debugger_on_cheri_trap=self.config.debugger_on_cheri_trap,
                                                         add_virtio_rng=self._add_virtio_rng)
        qemu_command += self._project_specific_options + self._after_disk_options + monitor_options
        qemu_command += logfile_options + self.extra_qemu_options + virtfs_args
        if self.disk_image is None:
            assert not self._uses_disk_image, "No disk image, should not have --ephemeral flag"
            self.info("About to run QEMU with loader/kernel", qemu_loader_or_kernel)
        else:
            self.info("About to run QEMU with image", self.disk_image, "and loader/kernel", qemu_loader_or_kernel)

        if self.config.wait_for_debugger or self.config.debugger_in_tmux_pane:
            gdb_socket_placeholder = find_free_port(preferred_port=1234)
            gdb_port = gdb_socket_placeholder.port if self.config.gdb_random_port else 1234
            self.info(f"QEMU is waiting for GDB to attach (using `target remote :{gdb_port}`)."
                      " Once connected enter 'continue\\n' to continue booting")

            def gdb_command(main_binary, bp=None, extra_binary=None) -> str:
                gdb_cmd = BuildGDB.get_install_dir(self, cross_target=CompilationTargets.NATIVE) / "bin/gdb"
                result = [gdb_cmd, main_binary]
                if self.target_info.is_freebsd():
                    # Set the sysroot to ensure that the .debug file is loaded from <ROOTFS>/usr/lib/debug/boot/kernel
                    # It seems this does not always work as expected, so also set substitute-path and
                    # debug-file-directory.
                    assert self.rootfs_path is not None
                    result.extend(["--init-eval-command=set sysroot " + str(self.rootfs_path),
                                   "--init-eval-command=set substitute-path " + str(self.rootfs_path) + " /",
                                   "--init-eval-command=set debug-file-directory " + str(
                                       self.rootfs_path / "usr/lib/debug")])
                # Once the file has been loaded set a breakpoint on panic() and connect to the remote host
                if bp:
                    result.append("--eval-command=break " + bp)
                result.append(f"--eval-command=target remote localhost:{gdb_port}")
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
                program = Path(self.rootfs_path or "/", self.config.qemu_debug_program)
                self.info("\t", coloured(AnsiColour.red, gdb_command(program, "main", path_to_kernel)), sep="")
            else:
                self.info("\t", coloured(AnsiColour.red, gdb_command(path_to_kernel, "panic")), sep="")
                if self.rootfs_path is not None:
                    self.info("If you would like to debug /sbin/init (or any other statically linked program) run this"
                              " inside GDB:")
                    self.info(coloured(AnsiColour.red, "\tadd-symbol-file -o 0", str(self.rootfs_path / "sbin/init")))
                    self.info("For dynamically linked programs you will have to add libraries at the correct offset."
                              " For example:")
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
                window: libtmux.Window = session.attached_window
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
                    self.info(coloured(AnsiColour.red, f"Unable to start gdb in tmux: {e}"))

            gdb_socket_placeholder.socket.close()  # the port is now available for qemu
            qemu_command += ["-gdb", f"tcp::{gdb_port}",  # wait for gdb on localhost:1234
                             "-S",  # freeze CPU at startup (use 'c' to start execution)
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
    kernel_project: Optional[BuildFreeBSD]
    disk_image_project: Optional[BuildDiskImageBase]

    kernel_config: Optional[str]

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

    def __init__(self, config: CheriConfig, *, freebsd_class: "Optional[type[BuildFreeBSD]]" = None,
                 disk_image_class: "Optional[type[BuildDiskImageBase]]" = None, **kwargs):
        super().__init__(config, **kwargs)
        self.freebsd_class = freebsd_class
        self.disk_image_class = disk_image_class

    def setup(self) -> None:
        super().setup()
        if self.freebsd_class is None and self.disk_image_class is not None:
            # noinspection PyProtectedMember
            disk_image_instance = self.disk_image_class.get_instance(self)
            self.disk_image_project = disk_image_instance
            self.kernel_project = disk_image_instance.source_project
            if disk_image_instance.use_qcow2:
                self.disk_image_format = "qcow2"
        else:
            self.kernel_project = self.freebsd_class.get_instance(self)

        if self.kernel_config:
            if self.kernel_config not in self._valid_kernel_configs():
                self.fatal("Selected kernel configuration", self.kernel_config, "is not available")
                self._list_kernel_configs()
        else:
            config_filters = {}
            if self.kernel_abi:
                if self.crosscompile_target.is_hybrid_or_purecap_cheri():
                    config_filters["kernel_abi"] = self.kernel_abi
                else:
                    self.warning("Can not select kernel ABI to run for non-CHERI target, ignoring --kernel-abi")
            self.kernel_config = self.kernel_project.default_kernel_config(ConfigPlatform.QEMU, **config_filters)

        if self.qemu_options.can_boot_kernel_directly and self.current_kernel is None:
            self.current_kernel = self.kernel_project.get_kernel_install_path(self.kernel_config)
            kern_module_path_arg = self.kernel_project.get_kern_module_path_arg(self.kernel_config)
            if kern_module_path_arg:
                self._project_specific_options += ["-append", kern_module_path_arg]
        self.rootfs_path = self.kernel_project.get_rootfs_dir(self)
        if self._uses_disk_image:
            self.disk_image = self.disk_image_class.get_instance(self).disk_image_path

    def _valid_kernel_configs(self):
        return self.kernel_project.get_kernel_configs(platform=ConfigPlatform.QEMU)

    def _list_kernel_configs(self):
        self.info("Available kernels for qemu:")
        for conf in self._valid_kernel_configs():
            path = self.kernel_project.get_kernel_install_path(conf)
            if conf == self.kernel_project.kernel_config:
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
    _freebsd_class: Optional[BuildFreeBSD] = None
    _disk_image_class: Optional[BuildDiskImageBase] = None
    kyua_test_files = ("/usr/tests/Kyuafile",)

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        if self._freebsd_class is not None:
            return self._freebsd_class.supported_architectures
        return self._disk_image_class.supported_architectures

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
        if self._freebsd_class is not None:
            return self._freebsd_class.default_architecture
        return self._disk_image_class.default_architecture

    @classmethod
    def dependencies(cls: "type[_RunMultiArchFreeBSDImage]", config: CheriConfig) -> "tuple[str, ...]":
        xtarget = cls.get_crosscompile_target()
        result = tuple()
        chosen_qemu = cls.get_chosen_qemu(config)
        if chosen_qemu.cls:
            result += (chosen_qemu.cls.target,)
        if cls._freebsd_class is not None:
            result += (cls._freebsd_class.get_class_for_target(xtarget).target,)
        if cls._disk_image_class is not None:
            result += (cls._disk_image_class.get_class_for_target(xtarget).target,)
        return result

    def __init__(self, *args, **kwargs):
        super().__init__(*args, freebsd_class=self._freebsd_class, disk_image_class=self._disk_image_class, **kwargs)

    @property
    def _extra_test_args(self) -> "list[str]":
        return []

    def run_tests(self):
        rootfs_kernel_bootdir = None
        if not self.qemu_options.can_boot_kernel_directly:
            rootfs_kernel_bootdir = self.kernel_project.get_kern_module_path(self.kernel_config)
        extra_args = self._extra_test_args
        if self.kyua_test_files and "--kyua-tests-files" not in self.config.test_extra_args:
            extra_args.extend("--kyua-tests-files=" + x for x in self.kyua_test_files)
        if not is_jenkins_build():
            # Jenkins expects the test outputs to be saved to the CWD, otherwise we save them in the build root
            tests_dir = self.config.build_root / "test-results" / self.target
            self.makedirs(tests_dir)
            extra_args.append(f"--test-output-dir={tests_dir}")
        if self.kernel_abi is not None and self.crosscompile_target.is_hybrid_or_purecap_cheri():
            extra_args.append(f"--expected-kernel-abi={self.kernel_abi.value}")
        self.target_info.run_cheribsd_test_script("run_cheribsd_tests.py", *extra_args,
                                                  disk_image_path=self.disk_image, kernel_path=self.current_kernel,
                                                  rootfs_alternate_kernel_dir=rootfs_kernel_bootdir)


class LaunchCheriBSD(_RunMultiArchFreeBSDImage):
    target = "run"
    _disk_image_class = BuildCheriBSDDiskImage
    kyua_test_files = tuple()  # don't run kyua tests by default for CheriBSD

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
    def dependencies(cls, config: CheriConfig) -> "tuple[str, ...]":
        result = super().dependencies(config)
        # RISCV needs OpenSBI/BBL to run:
        # Note: QEMU 4.2+ embeds opensbi, for CHERI, we have to use BBL (for now):
        if cls.get_crosscompile_target().is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            result += ("bbl-baremetal-riscv64-purecap",)
        return result


class LaunchCheriOSQEMU(LaunchQEMUBase):
    target = "run-cherios"
    dependencies = ("qemu", "cherios")
    supported_architectures = (CompilationTargets.CHERIOS_MIPS_PURECAP, CompilationTargets.CHERIOS_RISCV_PURECAP)
    forward_ssh_port = False
    qemu_user_networking = False
    hide_options_from_help = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(40), **kwargs)

    @property
    def source_project(self):
        return BuildCheriOS.get_instance(self, self.config)

    def setup(self):
        super().setup()
        # FIXME: these should be config options
        cherios = BuildCheriOS.get_instance(self, self.config)
        self.current_kernel = cherios.build_dir / "boot/cherios.elf"
        self.disk_image = self.config.output_root / "cherios-disk.img"
        self._project_specific_options = ["-no-reboot", "-global", "virtio-mmio.force-legacy=false"]

        if cherios.build_net:
            self._after_disk_options.extend([
                "-netdev", "tap,id=tap0,ifname=cherios_tap,script=no,downscript=no",
                "-device", "virtio-net-device,netdev=tap0",
            ])

        if cherios.smp_cores > 1:
            self._project_specific_options.append("-smp")
            self._project_specific_options.append(str(cherios.smp_cores))

        self.qemu_options.virtio_disk = True  # CheriOS needs virtio
        self.qemu_options.force_virtio_blk_device = True
        self.qemu_user_networking = False

    def process(self):
        if not self.disk_image.exists():
            if self.query_yes_no("CheriOS disk image is missing. Would you like to create a zero-filled 1MB image?"):
                size_flag = "bs=128m" if OSInfo.IS_MAC else "bs=128M"
                self.run_cmd("dd", "if=/dev/zero", "of=" + str(self.disk_image), size_flag, "count=1")
        super().process()

    def get_riscv_bios_args(self) -> "list[str]":
        # CheriOS bundles its kernel with its own bootloader
        return ["-bios", "none"]


class LaunchDmQEMU(LaunchCheriBSD):
    target = "run-dm"
    forward_ssh_port = False
    _enable_smbfs_support = False
    _add_virtio_rng = False
    hide_options_from_help = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.qemu_user_networking = False

    def process(self):
        super().process()


class LaunchFreeBSD(_RunMultiArchFreeBSDImage):
    target = "run-freebsd"
    hide_options_from_help = True
    _disk_image_class = BuildFreeBSDImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(10 + add_to_port), **kwargs)


class LaunchFreeBSDWithDefaultOptions(_RunMultiArchFreeBSDImage):
    target = "run-freebsd-with-default-options"
    hide_options_from_help = True
    _disk_image_class = BuildFreeBSDWithDefaultOptionsDiskImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(20 + add_to_port), **kwargs)


class LaunchMinimalCheriBSD(LaunchCheriBSD):
    target = "run-minimal"
    _disk_image_class = BuildMinimalCheriBSDDiskImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(default_ssh_port=get_default_ssh_forwarding_port(20 + add_to_port), **kwargs)

    @property
    def _extra_test_args(self) -> "list[str]":
        return ["--minimal-image"]


class LaunchCheriBsdMfsRoot(LaunchMinimalCheriBSD):
    target = "run-mfs-root"
    _freebsd_class = BuildCheriBsdMfsKernel
    _disk_image_class = None
    _uses_disk_image = False

    # XXX: Existing code isn't reqdy to run these but we want to support building them
    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return tuple(set(super().supported_architectures) -
                     {CompilationTargets.CHERIBSD_AARCH64, *CompilationTargets.ALL_CHERIBSD_MORELLO_TARGETS})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.config.use_minimal_benchmark_kernel:
            kernel_config = self.kernel_project.default_kernel_config(ConfigPlatform.QEMU, benchmark=True)
            self.current_kernel = self.kernel_project.get_kernel_install_path(kernel_config)
            if str(self.remote_kernel_path).endswith("MFS_ROOT"):
                self.remote_kernel_path += "_BENCHMARK"
        self.rootfs_path = BuildCHERIBSD.get_rootfs_dir(self)


class BuildAndRunCheriBSD(TargetAliasWithDependencies):
    target = "build-and-run-cheribsd"
    include_os_in_target_suffix = False
    dependencies = ("cheribsd", "disk-image", "run")
    direct_dependencies_only = True  # only rebuild toolchain, bbl or GDB if --include-dependencies is passed

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return LaunchCheriBSD.supported_architectures


class BuildAndRunFreeBSD(TargetAliasWithDependencies):
    target = "build-and-run-freebsd"
    include_os_in_target_suffix = False
    dependencies = ("freebsd", "disk-image-freebsd", "run-freebsd")
    direct_dependencies_only = True  # only rebuild toolchain, bbl or GDB if --include-dependencies is passed

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return LaunchFreeBSD.supported_architectures


class BuildAll(TargetAliasWithDependencies):
    target = "all"
    dependencies = ("qemu", "sdk", "disk-image", "run")

    @classproperty
    def supported_architectures(self) -> "tuple[CrossCompileTarget, ...]":
        return LaunchCheriBSD.supported_architectures
