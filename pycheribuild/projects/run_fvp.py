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
import subprocess
import tempfile
import typing
from pathlib import Path
from subprocess import CompletedProcess

from .disk_image import BuildCheriBSDDiskImage
from .fvp_firmware import BuildMorelloFlashImages, BuildMorelloScpFirmware, BuildMorelloUEFI
from .project import SimpleProject
from ..config.compilation_targets import CompilationTargets
from ..config.loader import ComputedDefaultValue
from ..processutils import extract_version, popen
from ..utils import AnsiColour, cached_property, coloured, OSInfo


class InstallMorelloFVP(SimpleProject):
    target = "install-morello-fvp"
    container_name = "morello-fvp"
    base_url = "https://developer.arm.com/-/media/Arm%20Developer%20Community/Downloads/OSS/FVP/Morello%20Platform/"
    latest_known_fvp = (0, 11, 3)
    installer_filename = "FVP_Morello_{}.{}_{}.tgz".format(*latest_known_fvp)

    # Seems like docker containers don't get the full amount configured in the settings so subtract a bit from 5GB/8GB
    min_ram_mb = 4900
    warn_ram_mb = 7900

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.use_docker_container:
            self.add_required_system_tool("docker", homebrew="homebrew/cask/docker")
            if self.use_docker_x11_forwarding:
                self.add_required_system_tool("socat", homebrew="socat")
                if OSInfo.IS_MAC:
                    self.add_required_system_tool("Xquartz", homebrew="homebrew/cask/xquartz")
        if self.installer_path is None:
            self.add_required_system_tool("wget")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.installer_path = cls.add_path_option("installer-path", help="Path to the FVP installer.sh or installer.tgz")
        # We can run the FVP on macOS by using docker. FreeBSD might be able to use Linux emulation.
        cls.use_docker_container = cls.add_bool_option("use-docker-container", default=OSInfo.IS_MAC,
                                                       help="Run the FVP inside a docker container")
        # TODO: should add a general option to turn of X11 for CI.
        cls.use_docker_x11_forwarding = cls.add_bool_option("use-docker-x11-forwarding", default=True,
                                                            help="Forward X11 from the docker container to the host")
        cls.i_agree_to_the_contained_eula = cls.add_bool_option("agree-to-the-contained-eula")

    @property
    def install_dir(self):
        return self.config.morello_sdk_dir / "FVP_Morello"

    def process(self):
        downloaded_new_file = False
        if self.installer_path is None:
            # noinspection PyAttributeOutsideInit
            self.installer_path = self.install_dir.parent / self.installer_filename
            downloaded_new_file = self.download_file(
                self.installer_path, url=self.base_url + self.installer_filename,
                sha256="2e52c34b80038fa025c590f49034a39350b8e7f8f3082fe389d5e5ca98f1cfe9")

        # Return early if we didn't download a new file or are running without --clean
        if not downloaded_new_file and not self.config.clean:
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
RUN zypper in -y xterm gzip tar libdbus-1-3 libatomic1 telnet
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
                if self.config.clean:
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

    def _fvp_base_command(self, interactive=True, docker_image=None) -> typing.Tuple[list, Path]:
        model_relpath = "models/Linux64_GCC-6.4/FVP_Morello"
        if self.use_docker_container:
            if docker_image is None:
                docker_image = self.container_name + ":latest"
            return (["docker", "run"] + (["-it"] if interactive else []) + ["--rm", docker_image],
                    Path("/opt/FVP_Morello", model_relpath))
        else:
            return [], self.install_dir / model_relpath

    def execute_fvp(self, args: list, disk_image_path: Path = None, firmware_path: Path = None, x11=True,
                    expose_telnet_ports=True, ssh_port=None, interactive=True, **kwargs) -> CompletedProcess:
        pre_cmd, fvp_path = self._fvp_base_command(interactive=interactive)
        if self.use_docker_container:
            assert pre_cmd[-1] == self.container_name + ":latest", pre_cmd[-1]
            pre_cmd = pre_cmd[0:-1]
            if not self.use_docker_x11_forwarding or os.getenv("DISPLAY", None) is None:
                x11 = False  # Don't bother with the GUI
            if expose_telnet_ports:
                pre_cmd += ["-p", "5000-5007:5000-5007"]
            if ssh_port is not None:
                pre_cmd += ["-p", str(ssh_port) + ":" + str(ssh_port)]
                print(coloured(AnsiColour.green, "Listening for SSH connections on localhost:", ssh_port, sep=""))
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
        base_cmd = pre_cmd + [fvp_path]
        if interactive:
            kwargs["give_tty_control"] = True
        if self.use_docker_container and x11 and OSInfo.IS_MAC and os.getenv("DISPLAY"):
            # To use X11 via docker on macos we need to run socat on port 6000
            socat = popen(["socat", "TCP-LISTEN:6000,reuseaddr,fork", "UNIX-CLIENT:\"" + os.getenv("DISPLAY") + "\""],
                          stdin=subprocess.DEVNULL)
            try:
                return self.run_cmd(base_cmd + self._plugin_args() + args, **kwargs)
            finally:
                socat.terminate()
                socat.kill()
        else:
            return self.run_cmd(base_cmd + self._plugin_args() + args, **kwargs)

    @cached_property
    def fvp_revision(self) -> "typing.Tuple[int, int, int]":
        return self._get_version(result_if_invalid=self.latest_known_fvp)

    def _get_version(self, docker_image=None, *, result_if_invalid) -> "typing.Tuple[int, int, int]":
        pre_cmd, fvp_path = self._fvp_base_command(interactive=False, docker_image=docker_image)
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
    def docker_memory_size(self):
        assert self.use_docker_container
        memtotal = self.run_cmd(["docker", "run", "-it", "--rm", self.container_name, "grep", "MemTotal:",
                                 "/proc/meminfo"], capture_output=True, run_in_pretend_mode=True).stdout
        self.verbose_print("Docker memory total:", memtotal)
        try:
            return int(memtotal.split()[1]) * 1024
        except Exception as e:
            self.warning("Could not determine memory available to docker container:", e)
            return 0

    def run_tests(self):
        self.execute_fvp(["--help"], x11=False, expose_telnet_ports=False, interactive=False)
        self.execute_fvp(["--cyclelimit", "1000"], x11=False, expose_telnet_ports=False, interactive=False)


class LaunchFVPBase(SimpleProject):
    do_not_add_to_targets = True
    _source_class = BuildCheriBSDDiskImage
    dependencies = ["install-morello-fvp", _source_class.target, "morello-firmware"]
    supported_architectures = _source_class.supported_architectures

    def __init__(self, config):
        super().__init__(config)
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
        # Allow using the architectural FVP:
        cls.use_architectureal_fvp = cls.add_bool_option("use-architectural-fvp",
                                                         help="Use the architectural FVP that requires a license.")
        cls.license_server = cls.add_config_option("license-server", help="License server to use for the model")
        cls.arch_model_path = cls.add_path_option("simulator-path", help="Path to the FVP Model",
                                                  default="/usr/local/FVP_Base_RevC-Rainier")
        cls.smp = cls.add_bool_option("smp", help="Simulate multiple CPU cores in the FVP", default=True)

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
        # TODO: tracing support:
        # TARMAC_TRACE="--plugin ${PLUGIN_DIR}/TarmacTrace.so"
        # TARMAC_TRACE="${TARMAC_TRACE} -C TRACE.TarmacTrace.trace-file=${HOME}/rainier/rainier.tarmac.trace"
        # TARMAC_TRACE="${TARMAC_TRACE} -C TRACE.TarmacTrace.quantum-size=0x1"
        # TARMAC_TRACE="${TARMAC_TRACE} -C TRACE.TarmacTrace.start-instruction-count=4400000000" # just after login
        # ARCH_MSG="--plugin ${PLUGIN_DIR}/ArchMsgTrace.so -C
        # ARCH_MSG="${ARCH_MSG} -C TRACE.ArchMsgTrace.trace-file=${HOME}/rainier/rainier.archmsg.trace"
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
            "userNetPorts=" + str(self.ssh_port) + "=22")

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
            self.fvp_project.check_system_dependencies()  # warn if socat/docker is missing
            model_params += [
                "displayController=0",  # won't work yet
                # "css.cache_state_modelled=0",
                ]
            if not self.smp:
                model_params += ["num_clusters=1", "num_cores=1"]
            if self.fvp_project.fvp_revision < (0, 10, 312):
                self.fatal("FVP is too old, please update to latest version")
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
            # Update the Generic Timer counter at a real-time base frequency instead of simulator time
            # This should fix the extremely slow countdown in the loader (30 minutes instead of 10s) and might also
            # improve network reliability
            fvp_args += ["-C", "css.scp.CS_Counter.use_real_time=1"]
            self.fvp_project.execute_fvp(fvp_args, disk_image_path=disk_image, firmware_path=uefi_bin.parent,
                                         ssh_port=self.ssh_port)


class LaunchFVPCheriBSD(LaunchFVPBase):
    target = "run-fvp"
    _source_class = BuildCheriBSDDiskImage
    supported_architectures = [CompilationTargets.CHERIBSD_MORELLO_HYBRID, CompilationTargets.CHERIBSD_MORELLO_PURECAP]
