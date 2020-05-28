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
import socket

from .build_qemu import BuildQEMU, BuildCheriOSQEMU
from .cherios import BuildCheriOS
from .cross.bbl import *
from .cross.freertos import BuildFreeRTOS
from .cross.opensbi import BuildOpenSBI
from .cross.rtems import BuildRtems
from .disk_image import *
from .project import *
from ..qemu_utils import QemuOptions, qemu_supports_9pfs
from ..utils import OSInfo


def get_default_ssh_forwarding_port(addend: int):
    # chose a different port for each user (hopefully it isn't in use yet)
    return 9999 + ((os.getuid() - 1000) % 10000) + addend


class LaunchQEMUBase(SimpleProject):
    doNotAddToTargets = True
    _forwardSSHPort = True
    _provide_src_via_smb = False
    sshForwardingPort = None  # type: int
    custom_qemu_smb_mount = None
    # Add a virtio RNG to speed up random number generation
    _add_virtio_rng = True
    _enable_smbfs_support = True

    @classmethod
    def setup_config_options(cls, default_ssh_port: int = None, **kwargs):
        super().setup_config_options(**kwargs)
        cls.extra_qemu_options = cls.add_config_option("extra-options", default=[], kind=list, metavar="QEMU_OPTIONS",
                                               help="Additional command line flags to pass to qemu-system-cheri")
        cls.logfile = cls.add_path_option("logfile", default=None, metavar="LOGFILE",
            help="The logfile that QEMU should use.")
        cls.log_directory = cls.add_path_option("log-directory", default=None, metavar="DIR",
            help="If set QEMU will log to a timestamped file in this directory. Will be "
                 "ignored if the 'logfile' option is set")
        cls.useTelnet = cls.add_config_option("monitor-over-telnet", kind=int, metavar="PORT", show_help=True,
                                            help="If set, the QEMU monitor will be reachable by connecting to localhost"
                                                 "at $PORT via telnet instead of using CTRL+A,C")

        cls.custom_qemu_smb_mount = cls.add_path_option("smb-host-directory", default=None, metavar="DIR",
            help="If set QEMU will provide this directory over smb with the name //10.0.2.4/qemu for use with "
                 "mount_smbfs")
        cls.cvtrace = cls.add_bool_option("cvtrace", help="Use binary trace output instead of textual")
        # TODO: -s will no longer work, not sure anyone uses it though
        if cls._forwardSSHPort:
            cls.sshForwardingPort = cls.add_config_option("ssh-forwarding-port", kind=int,
                default=default_ssh_port, metavar="PORT", show_help=True,
                help="The port on localhost to forward to the QEMU ssh port. You can then use "
                     "`ssh root@localhost -p $PORT` connect to the VM")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.qemuBinary = None  # type: typing.Optional[Path]
        self.currentKernel = None  # type: typing.Optional[Path]
        self.disk_image = None  # type: typing.Optional[Path]
        self.virtioDisk = False
        self._projectSpecificOptions = []
        self.bios_flags = []
        self.qemu_options = QemuOptions(self.crosscompile_target)
        self._qemuUserNetworking = True
        self.rootfs_path = typing.Optional[None]  # type: Path
        self._after_disk_options = []

    def get_riscv_bios(self) -> str:
        return "default"  # Use the default built-in OpenSBI firmware

    def setup(self):
        super().setup()
        xtarget = self.crosscompile_target
        if xtarget.is_riscv(include_purecap=True):
            self._add_virtio_rng = False
            self.bios_flags += ["-bios", self.get_riscv_bios()]
            self.qemuBinary = BuildQEMU.qemu_binary(self)
        elif xtarget.is_mips(include_purecap=True):
            self.qemuBinary = BuildQEMU.qemu_binary(self)
        elif xtarget.is_any_x86() or xtarget.is_aarch64():
            # Use the system QEMU instead of CHERI QEMU (for now)
            # Note: x86_64 can be either CHERI QEMU or system QEMU:
            self.addRequiredSystemTool("qemu-system-" + self.qemu_options.qemu_arch_sufffix)
            self.qemuBinary = Path(
                shutil.which("qemu-system-" + self.qemu_options.qemu_arch_sufffix) or "/could/not/find/qemu")
        else:
            assert False, "Unknown target " + str(xtarget)
        # only CHERI QEMU supports more than one SMB share
        self._provide_src_via_smb = self.compiling_for_mips(include_purecap=True) or self.compiling_for_riscv(
            include_purecap=True)

    def process(self):
        assert self.qemuBinary is not None
        if not self.qemuBinary.exists():
            self.dependencyError("QEMU is missing:", self.qemuBinary,
                                 install_instructions="Run `cheribuild.py qemu` or `cheribuild.py run -d`.")
        if self.currentKernel is not None and not self.currentKernel.exists():
            self.dependencyError("Kernel is missing:", self.currentKernel,
                                 install_instructions="Run `cheribuild.py cheribsd` or `cheribuild.py run -d`.")

        if self._forwardSSHPort and not self.is_port_available(self.sshForwardingPort):
            self.print_port_usage(self.sshForwardingPort)
            self.fatal("SSH forwarding port", self.sshForwardingPort, "is already in use! Make sure you don't ",
                       "already have a QEMU instance running or change the chosen port by setting the config option",
                       self.target + "/ssh-forwarding-port")

        monitor_options = []
        if self.useTelnet:
            monitor_port = self.useTelnet
            monitor_options = ["-monitor", "telnet:127.0.0.1:" + str(monitor_port) + ",server,nowait"]
            if not self.is_port_available(monitor_port):
                warningMessage("Cannot connect QEMU montitor to port", monitor_port)
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
                self.createSymlink(self.log_directory / filename, latest_symlink, relative=True, cwd=self.log_directory)
            logfile_options = ["-D", self.log_directory / filename]

        if self.cvtrace:
            logfile_options += ["-cheri-trace-format", "cvtrace"]
        if self.disk_image is not None and not self.disk_image.exists():
            self.dependencyError("Disk image is missing:", self.disk_image,
                install_instructions="Run `cheribuild.py disk-image` or `cheribuild.py run -d`.")

        user_network_options = ""
        smb_dir_count = 0
        have_9pfs_support = (self.crosscompile_target.is_native() or self.crosscompile_target.is_any_x86()) and qemu_supports_9pfs(self.qemuBinary)
        # Only default to providing the smb mount if smbd exists
        have_smbfs_support = self._provide_src_via_smb and shutil.which("smbd")

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
                guest_cmd = coloured(AnsiColour.yellow, "mkdir -p {target} && mount_smbfs -I 10.0.2.4 -N "
                                     "//10.0.2.4/{share_name} {target}".format(target=target, share_name=share_name))
                statusUpdate("Providing ", coloured(AnsiColour.green, str(directory)),
                             coloured(AnsiColour.cyan, " over SMB to the guest. Use `"), guest_cmd,
                             coloured(AnsiColour.cyan, "` to mount it"), sep="")
            if have_9pfs_support:
                if smb_dir_count > 1:
                    return  # FIXME: 9pfs panics if there is more than one device
                # Also provide it via virtfs:
                qemu_command.append("-virtfs")
                qemu_command.append("local,id=virtfs{n},mount_tag={tag},path={path},security_model=none{ro}".format(
                    n=smb_dir_count, path=directory, tag=share_name, ro=",readonly" if readonly else ""))
                guest_cmd = coloured(AnsiColour.yellow,
                    "mkdir -p {tgt} && mount -t virtfs -o trans=virtio,version=9p2000.L {share_name} {tgt}".format(
                        tgt=target, share_name=share_name))
                statusUpdate("Providing ", coloured(AnsiColour.green, str(directory)),
                    coloured(AnsiColour.cyan, " over 9pfs to the guest. Use `"), guest_cmd,
                    coloured(AnsiColour.cyan, "` to mount it"), sep="")

        if have_smbfs_support or have_9pfs_support:  # for running CheriBSD + FreeBSD
            add_smb_or_9p_dir(self.custom_qemu_smb_mount, "/mnt")
            add_smb_or_9p_dir(self.config.sourceRoot, "/srcroot", share_name="source_root", readonly=True)
            add_smb_or_9p_dir(self.config.buildRoot, "/buildroot", share_name="build_root", readonly=False)
            add_smb_or_9p_dir(self.config.outputRoot, "/outputroot", share_name="output_root", readonly=True)
            add_smb_or_9p_dir(self.rootfs_path, "/rootfs", share_name="rootfs", readonly=False)

        if self._forwardSSHPort:
            user_network_options += ",hostfwd=tcp::" + str(self.sshForwardingPort) + "-:22"
            # bind the qemu ssh port to the hosts port
            # qemu_command += ["-redir", "tcp:" + str(self.sshForwardingPort) + "::22"]
            print(coloured(AnsiColour.green, "\nListening for SSH connections on localhost:", self.sshForwardingPort,
                sep=""))

        # input("Press enter to continue")
        qemu_command = self.qemu_options.get_commandline(qemu_command=self.qemuBinary, kernel_file=self.currentKernel,
            disk_image=self.disk_image, add_network_device=self._qemuUserNetworking, bios_args=self.bios_flags,
            user_network_args=user_network_options, trap_on_unrepresentable=self.config.trap_on_unrepresentable,
            debugger_on_cheri_trap=self.config.debugger_on_cheri_trap, add_virtio_rng=self._add_virtio_rng)
        qemu_command += self._projectSpecificOptions + self._after_disk_options + monitor_options \
                        + logfile_options + self.extra_qemu_options
        statusUpdate("About to run QEMU with image", self.disk_image, "and kernel", self.currentKernel)

        if self.config.wait_for_debugger or self.config.debugger_in_tmux_pane:
            gdb_socket_placeholder = find_free_port()
            gdb_port = gdb_socket_placeholder.port if self.config.gdb_random_port else 1234
            self.info("QEMU is waiting for GDB to attach (using `target remote :{}`)."
                      " Once connected enter 'continue\\n' to continue booting".format(gdb_port))

            def gdb_command(main_binary, breakpoint=None, extra_binary=None) -> str:
                gdb_cmd = BuildGDB.getInstallDir(self, cross_target=CompilationTargets.NATIVE) / "bin/gdb"
                # Set the sysroot to ensure that the .debug file is loaded from $SYSROOT/usr/lib/debug/boot/kernel
                result = [gdb_cmd, main_binary, "--init-eval-command=set sysroot " + str(self.rootfs_path)]
                # Once the file has been loaded set a breakpoint on panic() and connect to the remote host
                if breakpoint:
                    result.append("--eval-command=break " + breakpoint)
                result.append("--eval-command=target remote localhost:{}".format(gdb_port))
                result.append("--eval-command=continue")
                if extra_binary:
                    result.append("--init-eval-command=add-symbol-file -o 0 " + str(extra_binary))
                return commandline_to_str(result)

            self.info("To start and connect GDB run the following command in another terminal:")
            path_to_kernel = self.currentKernel
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
                    start_gdb_in_tmux_pane(gdb_command(self.currentKernel, "panic"))
                except ImportError:
                    self.info(coloured(AnsiColour.red, "libtmux not installed, impossible to automatically start gdb"))
                except Exception as e:
                    self.info(coloured(AnsiColour.red, "Unable to start gdb in tmux: {}".format(e)))

            gdb_socket_placeholder.socket.close()  # the port is now available for qemu
            qemu_command += ["-gdb", "tcp::{}".format(gdb_port),  # wait for gdb on localhost:1234
                             "-S"  # freeze CPU at startup (use 'c' to start execution)
                             ]
        # We want stdout/stderr here even when running with --quiet
        self.run_cmd(qemu_command, stdout=sys.stdout, stderr=sys.stderr, give_tty_control=True)

    @staticmethod
    def print_port_usage(port: int):
        print("Port", port, "usage information:")
        if OSInfo.IS_FREEBSD:
            runCmd("sockstat", "-P", "tcp", "-p", str(port))
        elif OSInfo.IS_LINUX:
            runCmd("sh", "-c", "netstat -tulpne | grep \":" + str(port) + "\"")
        elif OSInfo.IS_MAC:
            runCmd("lsof", "-nP", "-iTCP:" + str(port))

    @staticmethod
    def is_port_available(port: int):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False


class AbstractLaunchFreeBSD(LaunchQEMUBase):
    doNotAddToTargets = True
    _provide_src_via_smb = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        if not IS_FREEBSD:
            cls.remoteKernelPath = cls.add_config_option("remote-kernel-path", show_help=True,
                                                       help="Path to the FreeBSD kernel image on a remote host. "
                                                            "Needed because FreeBSD cannot be cross-compiled.")
            cls.skipKernelUpdate = cls.add_bool_option("skip-kernel-update", show_help=True,
                                                     help="Don't update the kernel from the remote host")

    def __init__(self, config: CheriConfig, source_class: "typing.Type[BuildFreeBSD]" = None,
                 disk_image_class: "typing.Type[BuildFreeBSDImage]" = None, needs_disk_image=True):
        super().__init__(config)
        if source_class is None and disk_image_class is not None:
            # noinspection PyProtectedMember
            source_class = disk_image_class.get_instance(self).source_project
        self.source_class = source_class
        self.currentKernel = source_class.get_installed_kernel_path(self)
        if hasattr(source_class, "rootfsDir"):
            # noinspection PyCallingNonCallable
            self.rootfs_path = source_class.rootfsDir(self, config=config)
        if needs_disk_image:
            self.disk_image = disk_image_class.get_instance(self).disk_image_path
        self.needsRemoteKernelCopy = True
        # no need to copy from remote host if we were crossbuilding
        if IS_FREEBSD or source_class.get_instance(self).crossbuild:
            self.needsRemoteKernelCopy = False
        # same if skip-update was passed
        elif self.skipKernelUpdate or self.config.skipUpdate:
            self.needsRemoteKernelCopy = False

    def _copy_kernel_image_from_remote_host(self):
        statusUpdate("Copying kernel image from FreeBSD build machine")
        if not self.remoteKernelPath:
            self.fatal("Path to the remote disk image is not set, option '--", self.target, "/",
                       "remote-kernel-path' must be set to a path that scp understands",
                       " (e.g. vica:/foo/bar/kernel)", sep="")
            return
        scp_path = os.path.expandvars(self.remoteKernelPath)
        self.makedirs(self.currentKernel.parent)
        self.copyRemoteFile(scp_path, self.currentKernel)

    def process(self):
        if self.needsRemoteKernelCopy:
            self._copy_kernel_image_from_remote_host()
        super().process()


class _RunMultiArchFreeBSDImage(AbstractLaunchFreeBSD):
    doNotAddToTargets = True
    _source_class = None
    _bbl_class = BuildBBLNoPayload

    @classproperty
    def supported_architectures(cls):
        return cls._source_class.supported_architectures

    @classmethod
    def get_cross_target_index(cls):
        for idx, value in enumerate(cls.supported_architectures):
            if cls._xtarget is value:
                return idx
        assert cls._xtarget is CompilationTargets.NONE
        return -1  # return -1 for NONE

    @classproperty
    def default_architecture(cls):
        return cls._source_class.default_architecture

    @classmethod
    def dependencies(cls: "typing.Type[_RunMultiArchFreeBSDImage]", config: CheriConfig):
        xtarget = cls.get_crosscompile_target(config)
        result = []
        if xtarget.is_mips(include_purecap=True) or xtarget.is_riscv(include_purecap=True):
            result.append("qemu")
        result.append(cls._source_class.get_class_for_target(xtarget).target)
        return result

    def __init__(self, config, *, source_class=None, needs_disk_image=True):
        super().__init__(config, needs_disk_image=needs_disk_image, source_class=source_class,
            disk_image_class=self._source_class.get_class_for_target(self.get_crosscompile_target(config)))


class LaunchCheriBSD(_RunMultiArchFreeBSDImage):
    project_name = "run"
    _source_class = BuildCheriBSDDiskImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        if "default_ssh_port" in kwargs:
            # CheribsdMfsRoot case
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
        # if xtarget.is_riscv(include_purecap=True):
        #     if xtarget.is_cheri_purecap():
        #         result.append("opensbi-baremetal-riscv64-purecap")
        #     else:
        #         result.append("opensbi-baremetal-riscv64")
        if xtarget.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            result.append("bbl")
        return result

    @classmethod
    def riscv_bios_arg(cls, xtarget: CrossCompileTarget, caller: SimpleProject, prefer_bbl=True) -> str:
        assert xtarget.is_riscv(include_purecap=True)
        if xtarget.is_hybrid_or_purecap_cheri([CPUArchitecture.RISCV64]):
            # noinspection PyUnreachableCode
            if prefer_bbl:
                return str(BuildBBLNoPayload.get_installed_kernel_path(caller))
            else:
                return str(BuildOpenSBI.get_cheri_bios(caller))
        # For non-CHERI we prefer the OpenSBI bios that is bundled with QEMU
        # return BuildOpenSBI.get_nocap_bios(caller)
        return "default"

    def get_riscv_bios(self) -> str:
        return self.riscv_bios_arg(self.crosscompile_target, self)

    def run_tests(self):
        self.run_cheribsd_test_script("run_cheribsd_tests.py", disk_image_path=self.disk_image,
                                      kernel_path=self.currentKernel)


class LaunchCheriOSQEMU(LaunchQEMUBase):
    target = "run-cherios"
    project_name = "run-cherios"
    dependencies = ["cherios-qemu", "cherios"]
    supported_architectures = [CompilationTargets.CHERIOS_MIPS_PURECAP]
    _forwardSSHPort = False
    _qemuUserNetworking = False
    hide_options_from_help = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(sshPortShortname=None, useTelnetShortName=None,
                                   default_ssh_port=get_default_ssh_forwarding_port(40),
                                   **kwargs)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # FIXME: these should be config options
        cherios = BuildCheriOS.get_instance(self, config)
        self.currentKernel = BuildCheriOS.getBuildDir(self) / "boot/cherios.elf"
        self.disk_image = self.config.outputRoot / "cherios-disk.img"
        self._projectSpecificOptions = ["-no-reboot"]

        if cherios.build_net:
            self._after_disk_options.extend([
                "-netdev", "tap,id=tap0,ifname=cherios_tap,script=no,downscript=no",
                "-device", "virtio-net-device,netdev=tap0",
                ])

        if cherios.smp_cores > 1:
            self._projectSpecificOptions.append("-smp")
            self._projectSpecificOptions.append(str(cherios.smp_cores))

        self.qemu_options.virtio_disk = True  # CheriOS needs virtio
        self._qemuUserNetworking = False

    def setup(self):
        super().setup()
        self.qemuBinary = BuildCheriOSQEMU.qemu_binary(self)

    def process(self):
        if not self.disk_image.exists():
            if self.query_yes_no("CheriOS disk image is missing. Would you like to create a zero-filled 1MB image?"):
                size_flag = "bs=128m" if IS_MAC else "bs=128M"
                runCmd("dd", "if=/dev/zero", "of=" + str(self.disk_image), size_flag, "count=1")
        super().process()


class LaunchRtemsQEMU(LaunchQEMUBase):
    target = "run-rtems"
    project_name = "run-rtems"
    dependencies = ["rtems"]
    supported_architectures = [CompilationTargets.RTEMS_RISCV64_PURECAP]
    _forwardSSHPort = False
    _qemuUserNetworking = False
    _enable_smbfs_support = False
    _add_virtio_rng = False

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(sshPortShortname=None, useTelnetShortName=None,
                                   default_ssh_port=None,
                                   **kwargs)

    def get_riscv_bios(self) -> str:
        # Run a simple RTEMS shell application (run in machine mode using the -bios QEMU argument)
        return str(BuildRtems.getBuildDir(self) / "riscv/rv64xcheri_qemu/testsuites/samples/capture.exe")

    def process(self):
        super().process()

class LaunchFreeRTOSQEMU(LaunchQEMUBase):
    target = "run-freertos"
    project_name = "run-freertos"
    dependencies = ["freertos"]
    supported_architectures = [CompilationTargets.BAREMETAL_NEWLIB_RISCV64_PURECAP,
                               CompilationTargets.BAREMETAL_NEWLIB_RISCV64]
    _forwardSSHPort = False
    _qemuUserNetworking = False
    _enable_smbfs_support = False
    _add_virtio_rng = False

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(sshPortShortname=None, useTelnetShortName=None,
                                   defaultSshPort=None,
                                   **kwargs)

    def get_riscv_bios(self) -> str:
        # Run a simple FreeRTOS blinky demo application (run in machine mode using the -bios QEMU argument)
        return str(BuildFreeRTOS.getInstallDir(self) / "FreeRTOS/Demo/RISC-V-Generic_main_blinky.elf")

    def process(self):
        super().process()


class LaunchFreeBSD(_RunMultiArchFreeBSDImage):
    project_name = "run-freebsd"
    hide_options_from_help = True
    _source_class = BuildFreeBSDImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(sshPortShortname=None, useTelnetShortName=None,
                                   default_ssh_port=get_default_ssh_forwarding_port(10 + add_to_port), **kwargs)


class LaunchFreeBSDWithDefaultOptions(_RunMultiArchFreeBSDImage):
    project_name = "run-freebsd-with-default-options"
    hide_options_from_help = True
    _source_class = BuildFreeBSDWithDefaultOptionsDiskImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(sshPortShortname=None, useTelnetShortName=None,
                                   default_ssh_port=get_default_ssh_forwarding_port(20 + add_to_port), **kwargs)


class LaunchFreeBSDGFE(_RunMultiArchFreeBSDImage):
    project_name = "run-freebsd-gfe"
    hide_options_from_help = True
    _source_class = BuildFreeBSDGFEDiskImage

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(sshPortShortname=None, useTelnetShortName=None,
                                   default_ssh_port=get_default_ssh_forwarding_port(20 + add_to_port), **kwargs)


class LaunchCheriBsdMfsRoot(LaunchCheriBSD):
    project_name = "run-minimal"
    _source_class = BuildCheriBsdMfsKernel

    @classmethod
    def setup_config_options(cls, **kwargs):
        add_to_port = cls.get_cross_target_index()
        super().setup_config_options(sshPortShortname=None, useTelnetShortName=None,
                                     default_ssh_port=get_default_ssh_forwarding_port(20 + add_to_port), **kwargs)

    def __init__(self, config):
        super().__init__(config, source_class=BuildCheriBsdMfsKernel, needs_disk_image=False)
        if self.config.use_minimal_benchmark_kernel:
            self.currentKernel = BuildCheriBsdMfsKernel.get_installed_benchmark_kernel_path(self)
            if str(self.remoteKernelPath).endswith("MFS_ROOT"):
                self.remoteKernelPath += "_BENCHMARK"
        self.rootfs_path = BuildCHERIBSD.rootfsDir(self, config)

    def run_tests(self):
        self.run_cheribsd_test_script("run_cheribsd_tests.py", "--minimal-image")


# Backwards compatibility:
target_manager.add_target_alias("run-cheri", "run-mips-hybrid", deprecated=True)
target_manager.add_target_alias("run-purecap", "run-mips-purecap", deprecated=True)
target_manager.add_target_alias("run-minimal-cheri", "run-minimal-mips-hybrid", deprecated=True)
target_manager.add_target_alias("run-minimal-purecap", "run-minimal-mips-purecap", deprecated=True)
