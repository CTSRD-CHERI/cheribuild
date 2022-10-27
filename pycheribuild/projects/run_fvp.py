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
import os
import re
import signal
import subprocess
import tempfile
import typing
from pathlib import Path
from subprocess import CompletedProcess

from .disk_image import BuildCheriBSDDiskImage, BuildDiskImageBase, BuildFreeBSDImage
from .fvp_firmware import BuildMorelloFlashImages, BuildMorelloScpFirmware, BuildMorelloUEFI
from .simple_project import SimpleProject
from ..config.chericonfig import CheriConfig, ComputedDefaultValue
from ..config.compilation_targets import CompilationTargets
from ..processutils import extract_version, popen
from ..utils import (AnsiColour, cached_property, classproperty, coloured, fatal_error, find_free_port, OSInfo,
                     SocketAndPort)


class InstallMorelloFVP(SimpleProject):
    target = "install-morello-fvp"
    container_name = "morello-fvp"
    base_url = "https://developer.arm.com/-/media/Arm%20Developer%20Community/Downloads/OSS/FVP/Morello%20Platform/"
    latest_known_fvp = (0, 11, 27)  # value reported by --version.
    installer_filename = "FVP_Morello_{}.{}_{}.tgz".format(*latest_known_fvp)
    installer_sha256 = "bb44d006b59b38da56ffa1ca8d31fa19567e8060bcc5e348f158f1ba35b756e1"
    # Seems like docker containers don't get the full amount configured in the settings so subtract a bit from 5GB/8GB
    min_ram_mb = 4900
    warn_ram_mb = 7900

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        if self.use_docker_container:
            self.check_required_system_tool("docker", homebrew="homebrew/cask/docker")
            self.check_required_system_tool("socat", homebrew="socat")
            if OSInfo.IS_MAC:
                self.check_required_system_tool("Xquartz", homebrew="homebrew/cask/xquartz")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.installer_path = cls.add_path_option("installer-path", help="Path to the FVP installer.sh or installer.tgz")
        # We can run the FVP on macOS by using docker. FreeBSD might be able to use Linux emulation.
        cls.use_docker_container = cls.add_bool_option("use-docker-container", default=OSInfo.IS_MAC,
                                                       help="Run the FVP inside a docker container")
        cls.i_agree_to_the_contained_eula = cls.add_bool_option("agree-to-the-contained-eula")

    @property
    def install_dir(self):
        return self.config.morello_sdk_dir / "FVP_Morello"

    @property
    def plugin_dir(self):
        return self.install_dir / "plugins" / "Linux64_GCC-6.4"

    def process(self):
        downloaded_new_file = False
        if self.installer_path is None:
            # noinspection PyAttributeOutsideInit
            self.installer_path = self.install_dir.parent / self.installer_filename
            downloaded_new_file = self.download_file(
                self.installer_path, url=self.base_url + self.installer_filename, sha256=self.installer_sha256)

        # Return early if we didn't download a new file or are running without --clean
        if not downloaded_new_file and not self.with_clean:
            # Check if it is already installed:
            existing_version = self._get_version(result_if_invalid=(0, 0, 0))
            version_str = ".".join(map(str, existing_version))
            if existing_version == self.latest_known_fvp:
                self.info("Expected FVP version", version_str, "is already installed. Run with --clean to reinstall.")
                return
            elif existing_version >= self.latest_known_fvp:
                self.info("Newer FVP version", version_str, "is installed. Run with --clean to reinstall.")
                return
            else:
                self.info("Found old FVP version ", version_str, ". Will install ", self.installer_path, sep="")

        if not self.installer_path.is_file():
            self.fatal("Specified path to installer does not exist:", self.installer_path)

        with tempfile.TemporaryDirectory() as td:
            # If the installer is a tgz archive, extract it to the temporary directory first
            if self.installer_path.suffix == ".tgz":
                self.run_cmd("tar", "xf", self.installer_path, "-C", td)
                installer_sh = (list(Path(td).glob("*.sh")) or [Path(td, "FVP_Morello.sh")])[0]
            else:
                installer_sh = self.installer_path
            if installer_sh.suffix != ".sh":
                self.warning("Incorrect path to installer? Expected installer to be a .sh file:", installer_sh)

            if self.i_agree_to_the_contained_eula:
                eula_args = ["--i-agree-to-the-contained-eula", "--no-interactive"]
            else:
                eula_args = ["--force"]  # accept all other values
            # always delete the old FVP files to avoid mismatched libraries (and ensure the directory does not exist
            # to avoid prompts from the installer)
            self.clean_directory(self.install_dir, ensure_dir_exists=False)
            # Even when using docker, we extract on the host first to show the EULA and install the documentation
            self.run_cmd([installer_sh, "--destination", self.install_dir] + eula_args,
                         print_verbose_only=False)
            if self.use_docker_container:
                if installer_sh.parent != Path(td):
                    self.install_file(installer_sh, Path(td, installer_sh.name))
                # When building the docker container we have to pass --i-agree-to-the-contained-eula since it does
                # not seem possible to allow interactive prompts
                self.write_file(Path(td, "Dockerfile"), contents="""
FROM opensuse/leap:15.2
RUN zypper in -y xterm gzip tar libdbus-1-3 libatomic1 telnet socat
COPY {installer_name} .
RUN ./{installer_name} --i-agree-to-the-contained-eula --no-interactive --destination=/opt/FVP_Morello && \
    rm ./{installer_name}
# Run as non-root user to allow X11 to work
RUN useradd fvp-user
USER fvp-user
VOLUME /diskimg
""".format(installer_name=installer_sh.name), overwrite=True)
                build_flags = []
                if not self.config.skip_update:
                    build_flags.append("--pull")
                if self.with_clean:
                    build_flags.append("--no-cache")
                image_latest = self.container_name + ":latest"
                self.run_cmd(["docker", "build"] + build_flags + ["-t", image_latest, "."], cwd=td,
                             print_verbose_only=False)
                # get the version from the newly-built image (don't use the cached_property)
                version = self._get_version(docker_image=image_latest, result_if_invalid=None)
                # Also create a morello-fvp:0.11.3 tag to allow running speicific versions
                self.run_cmd("docker", "image", "tag", image_latest,
                             self.container_name + ":" + ".".join(map(str, version)), print_verbose_only=False)

    def _plugin_args(self):
        if self.fvp_revision >= (0, 10, 312):
            return []  # plugin no longer needed
        plugin_path = "plugins/Linux64_GCC-6.4/MorelloPlugin.so"
        if self.use_docker_container:
            return ["--plugin", Path("/opt/FVP_Morello", plugin_path)]
        return ["--plugin", self.ensure_file_exists("Morello FVP plugin", self.install_dir / plugin_path)]

    def _fvp_base_command(self, need_tty=True, docker_image=None) -> typing.Tuple[list, Path]:
        model_relpath = "models/Linux64_GCC-6.4/FVP_Morello"
        if self.use_docker_container:
            if docker_image is None:
                docker_image = self.container_name + ":latest"
            return (["docker", "run"] + (["-it"] if need_tty else []) + ["--rm", docker_image],
                    Path("/opt/FVP_Morello", model_relpath))
        else:
            return [], self.install_dir / model_relpath

    def execute_fvp(self, args: list, disk_image_path: Path = None, firmware_path: Path = None, x11=True,
                    tcp_ports: "typing.List[int]" = None, interactive=True, **kwargs) -> CompletedProcess:
        if tcp_ports is None:
            tcp_ports = []
        display = os.getenv("DISPLAY", None)
        if not display or not interactive:
            x11 = False  # Don't bother with the GUI
        interactive_headless = interactive and not x11
        # Interactive headless puts the FVP in the background with telnet as the point of interaction
        pre_cmd, fvp_path = self._fvp_base_command(need_tty=not interactive_headless)
        default_ap_port = 5003
        docker_host_ap_port = None
        if self.use_docker_container:
            assert pre_cmd[-1] == self.container_name + ":latest", pre_cmd[-1]
            pre_cmd = pre_cmd[0:-1]
            if interactive_headless:
                docker_host_ap_port = find_free_port().port
                # Should always get the preferred port inside the container,
                # and no way to recover otherwise anyway given we can't change
                # port forwarding after running.
                pre_cmd += ["-p", str(docker_host_ap_port) + ":" + str(default_ap_port)]
            for p in tcp_ports:
                pre_cmd += ["-p", str(p) + ":" + str(p)]
            if disk_image_path is not None:
                pre_cmd += ["-v", str(disk_image_path) + ":" + str(disk_image_path)]
                docker_settings_fixit = ""
                if OSInfo.IS_MAC:
                    docker_settings_fixit = " This setting can be changed under \"Preferences > Resources > Advanced\"."
                # If we are actually running a disk image, check the docker memory size first
                if self.docker_memory_size < self.min_ram_mb * 1024 * 1024:
                    fixit = "Change the docker settings to allow at least 5GB (8GB recommended) of RAM for containers."
                    self.fatal("Docker container has less than ", self.min_ram_mb, "MB of RAM (",
                               self.docker_memory_size // 1024 // 1024, "MB), this is not enough to run the FVP!",
                               sep="", fixit_hint=fixit + docker_settings_fixit)
                elif self.docker_memory_size < self.warn_ram_mb * 1024 * 1024:
                    fixit = "Change the docker settings to allow at least 8GB of RAM for containers."
                    self.warning("Docker container has less than ", self.warn_ram_mb, "MB of RAM (",
                                 self.docker_memory_size // 1024 // 1024, "MB), this may not enough to run the FVP",
                                 sep="", fixit_hint=fixit + docker_settings_fixit)

            if firmware_path is not None:
                pre_cmd += ["-v", str(firmware_path) + ":" + str(firmware_path)]
            if x11:
                pre_cmd += ["-e", "DISPLAY=host.docker.internal:0"]
            pre_cmd += [self.container_name]
        if interactive:
            kwargs["give_tty_control"] = True
        bg_processes = []
        if self.use_docker_container and x11 and OSInfo.IS_MAC:
            # To use X11 via docker on macos we need to run socat on port 6000
            socat_cmd = ["socat", "TCP-LISTEN:6000,reuseaddr,fork", "UNIX-CLIENT:\"" + display + "\""]
            socat = popen(socat_cmd, stdin=subprocess.DEVNULL)
            bg_processes.append((socat, False))
        try:
            extra_args = []

            def fvp_cmdline():
                return pre_cmd + [fvp_path] + self._plugin_args() + args + extra_args

            if not interactive_headless:
                return self.run_cmd(fvp_cmdline(), **kwargs)
            else:
                if self.use_docker_container and not self.docker_has_socat:
                    self.fatal("Docker container needs updating to include socat for headless operation.",
                               fixit_hint="Re-run `cheribuild.py --clean " + self.target + "`")

                def disable_uart(param_base):
                    nonlocal extra_args
                    extra_args.extend([
                        "-C", param_base + ".start_telnet=0",
                        "-C", param_base + ".quiet=1",
                    ])

                disable_uart("board.terminal_uart0_board")
                disable_uart("board.terminal_uart1_board")
                disable_uart("css.mcp.terminal_uart0")
                disable_uart("css.mcp.terminal_uart1")
                disable_uart("css.scp.terminal_uart_aon")
                disable_uart("css.terminal_sec_uart_ap")
                disable_uart("css.terminal_uart1_ap")

                extra_args.extend([
                    "-q",
                    "-C", "disable_visualisation=true",
                    "-C", "css.terminal_uart_ap.quiet=1",
                ])

                # Although we know nothing else is using ports in the container
                # and thus know what port will be allocated, we still need to
                # be able to wait until the FVP has started listening inside
                # the container otherwise telnet will fail to connect. So we
                # might as well also read the port out for robustness, and if
                # it doesn't match what we're expecting we can't do anything
                # but can print an error. Ideally we'd just bind-mount the same
                # FIFO as is used for the normal case, but that doesn't work
                # with Docker for Mac.
                #
                # As for FreeBSD, we'd like to use a named FIFO rather than
                # relying on the FVP keeping file descriptors open, but
                # Linuxulator wants to make the write point at /tmp inside the
                # compat chroot, so just use an anonymous pipe and hope Arm
                # never break passing file descriptors through.
                ap_servsock = None  # type: typing.Optional[SocketAndPort]
                try:
                    fvp_kwargs = {}
                    if self.use_docker_container:
                        ap_servsock = find_free_port()
                        ap_servsock.socket.listen(1)

                        socat_cmd = "socat - TCP:host.docker.internal:" + str(ap_servsock.port)
                        extra_args.extend([
                            "-C", "css.terminal_uart_ap.terminal_command=echo %port | " + socat_cmd,
                        ])

                        def get_ap_port():
                            (ap_sock, _) = ap_servsock.socket.accept()
                            with open(ap_sock.fileno(), 'r') as f:
                                # Check the port in the container is the
                                # expected default.
                                port = int(f.readline())
                                if port != default_ap_port:
                                    fatal_error("Unexpected port " + str(port) + " used by FVP in container")
                                return docker_host_ap_port
                    else:
                        ap_pipe_rfd, ap_pipe_wfd = os.pipe()
                        extra_args.extend([
                            "-C", "css.terminal_uart_ap.terminal_command=echo %port >&" + str(ap_pipe_wfd),
                        ])
                        fvp_kwargs['pass_fds'] = [ap_pipe_wfd]

                        def get_ap_port():
                            with open(ap_pipe_rfd, 'r') as f:
                                return int(f.readline())

                    # Pass os.setsid to create a new process group so signals
                    # are passed to children; docker exec does not seem to want
                    # to behave so we have to signal its child too.
                    fvp = popen(fvp_cmdline(), stdin=subprocess.DEVNULL, preexec_fn=os.setsid, **fvp_kwargs)
                    bg_processes.append((fvp, True))
                    self.info("Waiting for FVP to start...")
                    # Don't call get_ap_port() in --pretend mode since it will hang forever
                    ap_port = default_ap_port if self.config.pretend else get_ap_port()
                finally:
                    if ap_servsock is not None:
                        try:
                            ap_servsock.socket.close()
                        finally:
                            pass

                self.info("Connecting to the FVP using telnet. Press", coloured(AnsiColour.yellow, "CTRL+]"),
                          coloured(AnsiColour.cyan, "followed by"), coloured(AnsiColour.yellow, "q<ENTER>"),
                          coloured(AnsiColour.cyan, "to exit telnet and kill the FVP."))

                # FVP only seems to listen on IPv4 so specify manually to avoid
                # messages about first trying to connect to ::1.
                return self.run_cmd(["telnet", "127.0.0.1", str(ap_port)], **kwargs)
        finally:
            while len(bg_processes):
                (p, is_fvp) = bg_processes.pop()
                try:
                    if p.poll() is not None:
                        continue
                    if is_fvp:
                        pgrp = os.getpgid(p.pid)
                        for i in range(5):
                            if i == 0:
                                self.info("Stopping FVP... (this can sometimes take a while)")
                            os.killpg(pgrp, signal.SIGTERM)
                            try:
                                p.wait(timeout=15)
                                break
                            except KeyboardInterrupt:
                                os.killpg(pgrp, signal.SIGKILL)
                                break
                            except Exception as e:
                                if isinstance(e, subprocess.TimeoutExpired):
                                    self.warning("Timed out waiting for FVP to exit, retrying")
                                else:
                                    self.warning("Got exception while stopping FVP, retrying:", e)
                                continue
                        else:
                            self.warning("FVP did not exit in time; killing")
                            os.killpg(pgrp, signal.SIGKILL)
                    else:
                        p.send_signal(signal.SIGTERM)
                        try:
                            p.wait(timeout=5)
                        except Exception as e:
                            self.warning("FVP did not terminate 5s after SIGTERM:", e)
                            p.kill()
                except Exception as e:
                    self.warning("Error killing background process:", e)

    @cached_property
    def fvp_revision(self) -> "typing.Tuple[int, ...]":
        return self._get_version(result_if_invalid=self.latest_known_fvp)

    def _get_version(self, docker_image=None, *, result_if_invalid) -> "tuple[int, ...]":
        pre_cmd, fvp_path = self._fvp_base_command(need_tty=False, docker_image=docker_image)
        try:
            version_out = self.run_cmd(pre_cmd + [fvp_path, "--version"], capture_output=True, run_in_pretend_mode=True)
            result = extract_version(version_out.stdout,
                                     regex=re.compile(rb"Fast Models \[(\d+)\.(\d+)\.?(\d+)? \(.+\)]"))
            self.info("Morello FVP version detected as", result)
            return result
        except Exception as e:
            if result_if_invalid is None:
                self.fatal("Failed to detect FVP revision: ", e)
                return self.latest_known_fvp  # for --pretend mode
            self.warning("Could not determine FVP revision, assuming ", result_if_invalid, ": ", e, sep="")
            return result_if_invalid

    @cached_property
    def docker_has_socat(self):
        assert self.use_docker_container
        try:
            has_socat = self.run_cmd(["docker", "run", "--rm", self.container_name, "sh", "-c",
                                      "command -v socat >/dev/null 2>&1 && printf true || printf false"],
                                     capture_output=True, run_in_pretend_mode=True).stdout
        except Exception as e:
            self.fatal("Could not determine whether container has socat:", e)
            return True
        self.verbose_print("Has socat:", has_socat)
        if has_socat == b'true':
            return True
        elif has_socat == b'false':
            return False
        else:
            self.fatal("Could not determine whether container has socat:", has_socat)
            return True

    @cached_property
    def docker_memory_size(self):
        assert self.use_docker_container
        # try docker info first, and if that fails read /proc/meminfo in the container
        try:
            try:
                memtotal = self.run_cmd(["docker", "info", "-f", "{{json .MemTotal}}"], capture_output=True,
                                        run_in_pretend_mode=True).stdout
                self.verbose_print("Docker memory total:", memtotal.strip())
                return int(memtotal.strip())
            except Exception as e:
                self.warning("docker info failed:", e)
                memtotal = self.run_cmd(["docker", "run", "--rm", self.container_name, "grep", "MemTotal:",
                                         "/proc/meminfo"], capture_output=True, run_in_pretend_mode=True).stdout
                self.verbose_print("Docker memory total:", memtotal)
                return int(memtotal.split()[1]) * 1024
        except Exception as e:
            self.warning("Could not determine memory available to docker container:", e)
            return 0

    def run_tests(self):
        self.execute_fvp(["--version"], x11=False, interactive=False)
        self.execute_fvp(["--help"], x11=False, interactive=False)
        self.execute_fvp(["--cyclelimit", "1000"], x11=False, interactive=False)


class LaunchFVPBase(SimpleProject):
    do_not_add_to_targets = True
    _source_class = None  # type: BuildDiskImageBase
    required_fvp_version = (0, 11, 19)

    @classmethod
    def dependencies(cls, _: CheriConfig) -> "list[str]":
        return ["install-morello-fvp", cls._source_class.target, "morello-firmware"]

    @classproperty
    def supported_architectures(self):
        return self._source_class.supported_architectures

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fvp_project = None

    def setup(self):
        super().setup()
        assert self.crosscompile_target.is_aarch64(include_purecap=True)
        if not self.use_architectureal_fvp:
            self.fvp_project = InstallMorelloFVP.get_instance(self, cross_target=CompilationTargets.NATIVE)

    @staticmethod
    def default_ssh_port():
        # chose a different port for each user (hopefully it isn't in use yet)
        return 12345 + ((os.getuid() - 1000) % 10000)

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        fw_default = ComputedDefaultValue(function=lambda c, _: c.morello_sdk_dir / "firmware/morello-fvp",
                                          as_string="<MORELLO_SDK>/firmware/morello-fvp")
        cls.firmware_path = cls.add_path_option("firmware-path", default=fw_default,
                                                help="Path to the UEFI firmware binaries")
        cls.remote_disk_image_path = cls.add_config_option("remote-disk-image-path",
                                                           help="When set rsync will be used to update the image from "
                                                                "the remote server prior to running it.")
        cls.ssh_port = cls.add_config_option("ssh-port", default=cls.default_ssh_port(), kind=int)
        cls.extra_tcp_forwarding: "list[str]" = cls.add_config_option(
            "extra-tcp-forwarding", kind=list, default=[],
            help="Additional TCP bridge ports beyond ssh/22; list of [hostip:]port=[guestip:]port")
        # Allow using the architectural FVP:
        cls.use_architectureal_fvp = cls.add_bool_option("use-architectural-fvp",
                                                         help="Use the architectural FVP that requires a license.")
        cls.license_server = cls.add_config_option("license-server", help="License server to use for the model")
        cls.arch_model_path = cls.add_path_option("simulator-path", help="Path to the FVP Model",
                                                  default="/usr/local/FVP_Base_RevC-Rainier")
        cls.smp = cls.add_bool_option("smp", help="Simulate multiple CPU cores in the FVP", default=True)
        cls.force_headless = cls.add_bool_option("force-headless", default=False,
                                                 help="Force headless use of the FVP")
        cls.fvp_trace = cls.add_path_option("trace", help="Enable FVP tracing plugin to output to the given file")
        cls.fvp_trace_mmu = cls.add_bool_option("trace-mmu", default=False, help="Emit FVP MMU trace events")
        cls.fvp_trace_icount = cls.add_config_option("trace-start-icount",
                                                     help="Instruction count from which to start Tarmac trace")

    @property
    def use_virtio_net(self):
        # VirtIO network device first available in 0.10.345
        return self.fvp_project is not None and self.fvp_project.fvp_revision >= (0, 10, 345)

    # noinspection PyAttributeOutsideInit
    def process(self):
        if not self.firmware_path.exists():
            self.fatal("Firmware path", self.firmware_path, " is invalid, set the",
                       "--" + self.get_config_option_name("firmware_path"), "config option!")
        disk_image_project = self._source_class.get_instance(self)
        if self.remote_disk_image_path:
            self.copy_remote_file(self.remote_disk_image_path, disk_image_project.disk_image_path)
        disk_image = self.ensure_file_exists("disk image", disk_image_project.disk_image_path,
                                             fixit_hint="Run `cheribuild.py " + disk_image_project.target + "`")
        model_params = []

        def add_board_params(*params):
            prefix = "bp." if self.use_architectureal_fvp else "board."
            model_params.extend([prefix + p for p in params])

        def add_hostbridge_params(*params):
            prefix = "virtio_net." if self.use_virtio_net else ""
            prefix += "hostbridge."
            add_board_params(*[prefix + p for p in params])

        if self.use_virtio_net:
            add_board_params("virtio_net.enabled=1")
        else:
            add_board_params("smsc_91c111.enabled=1")

        add_hostbridge_params(
            "userNetworking=true",
            "userNetPorts=" + ",".join([str(self.ssh_port) + "=22"] + self.extra_tcp_forwarding))

        # NB: Set transport even if virtio_net is disabled since it still shows
        # up and is detected, just doesn't have any queues.
        add_board_params(
            "virtio_net.transport=legacy",
            "virtio_rng.transport=legacy",
            "virtioblockdevice.image_path=" + str(disk_image))

        if self.use_architectureal_fvp:
            if not self.license_server:
                self.license_server = "unknown.license.server"  # for --pretend
                self.fatal("License server info unknown, set the",
                           "--" + self.get_config_option_name("license_server"),
                           "config option!")
            if not self.arch_model_path.is_dir():
                self.fatal("FVP path", self.arch_model_path, "does not exist, set the",
                           "--" + self.get_config_option_name("simulator_path"), "config option!")

            with self.set_env(ARMLMD_LICENSE_FILE=self.license_server, print_verbose_only=False):
                sim_binary = self.ensure_file_exists("Model binary",
                                                     self.arch_model_path /
                                                     "models/Linux64_GCC-6.4/FVP_Base_RevC-Rainier")
                plugin = self.ensure_file_exists("Morello FVP plugin",
                                                 self.arch_model_path / "plugins/Linux64_GCC-6.4/MorelloPlugin.so")
                # prepend -C to each of the parameters:
                bl1_bin = self.ensure_file_exists("bl1.bin firmware", self.firmware_path / "bl1.bin")
                fip_bin = self.ensure_file_exists("fip.bin firmware", self.firmware_path / "fip.bin")
                model_params += [
                    "pctl.startup=0.0.0.0",
                    "bp.secure_memory=0",
                    "cache_state_modelled=0",
                    "cluster0.NUM_CORES=1",
                    "bp.flashloader0.fname=" + str(fip_bin),
                    "bp.secureflashloader.fname=" + str(bl1_bin),
                ]
                fvp_args = [x for param in model_params for x in ("-C", param)]
                self.run_cmd([sim_binary, "--plugin", plugin, "--print-port-number"] + fvp_args)
        else:
            if self.fvp_project.fvp_revision < self.required_fvp_version:
                self.dependency_error("FVP is too old, please update to the latest version",
                                      problem="outdated", cheribuild_target="install-morello-fvp",
                                      cheribuild_action="update")
                del self.fvp_project.fvp_revision  # reset cached value
                if self.fvp_project.fvp_revision < self.required_fvp_version:
                    self.fatal("FVP update failed, version is reported as", self.fvp_project.fvp_revision,
                               "but needs to be at least", self.required_fvp_version)
            elif self.fvp_project.fvp_revision < self.fvp_project.latest_known_fvp:
                self.dependency_warning("FVP is old, it is recommended to update to the latest version",
                                        problem="outdated", cheribuild_target="install-morello-fvp",
                                        cheribuild_action="update")
                del self.fvp_project.fvp_revision  # reset cached value

            self.fvp_project.check_system_dependencies()  # warn if socat/docker is missing
            model_params += [
                "displayController=0",  # won't work yet
                # "css.cache_state_modelled=0",
            ]
            if not self.smp:
                model_params += ["num_clusters=1", "num_cores=1"]

            # virtio-rng supported in 0.10.312
            model_params += [
                "board.virtio_rng.enabled=1",
                "board.virtio_rng.seed=0",
                "board.virtio_rng.generator=2",
            ]
            # prepend -C to each of the parameters:
            fvp_args = [x for param in model_params for x in ("-C", param)]
            # mcp_romfw_elf = self.ensure_file_exists("MCP ROM ELF firmware",
            #                                         self.firmware_path / "morello/components/morello/mcp_romfw.elf")
            # scp_romfw_elf = self.ensure_file_exists("SCP ROM ELF firmware",
            #                                         self.firmware_path / "morello/components/morello/scp_romfw.elf")
            firmware_fixit = "Run `cheribuild.py morello-fvp-firmware`"
            mcp_rom_bin = self.ensure_file_exists("MCP ROM ELF firmware", BuildMorelloScpFirmware.mcp_rom_bin(self),
                                                  fixit_hint=firmware_fixit)
            scp_rom_bin = self.ensure_file_exists("SCP ROM ELF firmware", BuildMorelloScpFirmware.scp_rom_bin(self),
                                                  fixit_hint=firmware_fixit)
            uefi_bin = self.ensure_file_exists("UEFI firmware", BuildMorelloUEFI.uefi_bin(self),
                                               fixit_hint=firmware_fixit)
            flash_images = BuildMorelloFlashImages.get_instance(self, cross_target=CompilationTargets.NATIVE)
            scp_fw_bin = self.ensure_file_exists("SCP/AP firmware", flash_images.scp_ap_ram_firmware_image,
                                                 fixit_hint=firmware_fixit)
            mcp_fw_bin = self.ensure_file_exists("MCP firmware", flash_images.mcp_ram_firmware_image,
                                                 fixit_hint=firmware_fixit)
            assert uefi_bin.parent == scp_fw_bin.parent and scp_fw_bin.parent == scp_rom_bin.parent, "Different dirs?"
            fvp_args += [
                # "-a", "Morello_Top.css.scp.armcortexm7ct=" + str(scp_romfw_elf),
                # "-a", "Morello_Top.css.mcp.armcortexm7ct=" + str(mcp_romfw_elf),
                # "-C", "css.scp.ROMloader.fname=" + str(scp_rom_bin),
                # "-C", "css.mcp.ROMloader.fname=" + str(mcp_rom_bin),
                "--data", "Morello_Top.css.scp.armcortexm7ct=" + str(scp_rom_bin) + "@0x0",
                "--data", "Morello_Top.css.mcp.armcortexm7ct=" + str(mcp_rom_bin) + "@0x0",
                "-C", "css.scp.armcortexm7ct.INITVTOR=0x0",
                "-C", "css.mcp.armcortexm7ct.INITVTOR=0x0",
                "--data", str(uefi_bin) + "@0x14200000",
                # "-C", "Morello_Top.soc.scp_qspi_loader.fname=" + str(scp_fw_bin),
                # "-C", "Morello_Top.soc.mcp_qspi_loader.fname=" + str(mcp_fw_bin),
                "-C", "soc.scp_qspi_loader.fname=" + str(scp_fw_bin),
                "-C", "soc.mcp_qspi_loader.fname=" + str(mcp_fw_bin),

                # "-C", "css.nonTrustedROMloader.fname=" + str(uefi_bin),
                # "-C", "css.trustedBootROMloader.fname=" + str(trusted_fw),
                "-C", "css.pl011_uart_ap.unbuffered_output=1",
            ]

            gdb_port = None
            if self.config.wait_for_debugger:
                gdb_port = find_free_port(preferred_port=1234).port if self.config.gdb_random_port else 1234
                fvp_args += [
                    "--allow-debug-plugin",
                    "--plugin", self.fvp_project.plugin_dir / "GDBRemoteConnection.so",
                    "-C", "REMOTE_CONNECTION.GDBRemoteConnection.listen_address=127.0.0.1",
                    "-C", "REMOTE_CONNECTION.GDBRemoteConnection.port={}".format(gdb_port)]

            if self.fvp_trace:
                fvp_args += [
                    "--plugin", self.fvp_project.plugin_dir / "TarmacTrace.so",
                    "-C", "TRACE.TarmacTrace.trace-file={}".format(self.fvp_trace),
                    "-C", "TRACE.TarmacTrace.quantum-size=0x1",
                    "-C", "TRACE.TarmacTrace.trace_mmu={}".format("true" if self.fvp_trace_mmu else "false"),
                    "-C", "TRACE.TarmacTrace.trace_loads_stores=false",
                    "-C", "TRACE.TarmacTrace.trace_ete=false",
                    "-C", "TRACE.TarmacTrace.trace_dap=false",
                    "-C", "TRACE.TarmacTrace.trace_cache=false",
                    "-C", "TRACE.TarmacTrace.trace_atomic=false",
                    "-C", "TRACE.TarmacTrace.trace_instructions=true"]
                # Enable the ToggleMTI plugin to be able to programmaticaly start/stop traces
                # with magic nops as we do in qemu
                fvp_args += [
                    "--plugin", self.fvp_project.plugin_dir / "ToggleMTIPlugin.so",
                    "-C", "TRACE.ToggleMTIPlugin.diagnostics=false",
                    "-C", "TRACE.ToggleMTIPlugin.disable_mti_from_start=true",
                    "-C", "TRACE.ToggleMTIPlugin.use_hlt=true",
                    "-C", "TRACE.ToggleMTIPlugin.hlt_imm16=0xbeef",
                    "-C", "css.cluster0.cpu0.enable_trace_special_hlt_imm16=1",
                    "-C", "css.cluster0.cpu1.enable_trace_special_hlt_imm16=1",
                    "-C", "css.cluster1.cpu0.enable_trace_special_hlt_imm16=1",
                    "-C", "css.cluster1.cpu1.enable_trace_special_hlt_imm16=1",
                    "-C", "css.cluster0.cpu0.trace_special_hlt_imm16=0xbeef",
                    "-C", "css.cluster0.cpu1.trace_special_hlt_imm16=0xbeef",
                    "-C", "css.cluster1.cpu0.trace_special_hlt_imm16=0xbeef",
                    "-C", "css.cluster1.cpu1.trace_special_hlt_imm16=0xbeef"
                ]
                if self.fvp_trace_icount:
                    fvp_args += ["-C", "TRACE.TarmacTrace.start-instruction-count={}".format(self.fvp_trace_icount)]

            # Update the Generic Timer counter at a real-time base frequency instead of simulator time
            # This should fix the extremely slow countdown in the loader (30 minutes instead of 10s) and might also
            # improve network reliability
            fvp_args += ["-C", "css.scp.CS_Counter.use_real_time=1"]
            # With newer FVP version (starting with 0.11.13) we hav to pass another flag to allow the bootloader
            # countdown to roughly match real time since otherwise each second of countdown takes around 2 minutes:
            fvp_args += ["-C", "board.rtc_clk_frequency=300"]

            tcp_ports = []

            # Expose to the real host all TCP ports exposed by the FVP
            if self.ssh_port is not None:
                tcp_ports += [self.ssh_port]
                print(coloured(AnsiColour.green, "Listening for SSH connections on localhost:", self.ssh_port, sep=""))
            if gdb_port is not None:
                tcp_ports += [gdb_port]
            # XXX this matches on any host address; that may not be quite right
            for x in self.extra_tcp_forwarding:
                if x == "":
                    self.fatal("Bad extra-tcp-forwarding (empty forward?)")
                    continue
                hg = x.split("=")
                if len(hg) != 2:
                    self.fatal("Bad extra-tcp-forwarding (not just one '=' in '%s')" % x)
                    continue
                gaddrport = hg[1].split(":")
                if len(gaddrport) > 2:
                    self.fatal("Bad extra-tcp-forwarding (excess ':' in '%s')" % x)
                    continue
                tcp_ports += gaddrport[-1]  # either just port or last in "host:port".split(":")

            self.fvp_project.execute_fvp(fvp_args, disk_image_path=disk_image, firmware_path=uefi_bin.parent,
                                         x11=not self.force_headless, tcp_ports=tcp_ports)


class LaunchFVPCheriBSD(LaunchFVPBase):
    target = "run-fvp"
    _source_class = BuildCheriBSDDiskImage
    supported_architectures = [CompilationTargets.CHERIBSD_MORELLO_HYBRID, CompilationTargets.CHERIBSD_MORELLO_PURECAP]


class LaunchFVPFreeBsd(LaunchFVPBase):
    target = "run-fvp-freebsd"
    _source_class = BuildFreeBSDImage
    supported_architectures = [CompilationTargets.FREEBSD_AARCH64]
