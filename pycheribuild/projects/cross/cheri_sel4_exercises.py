# SPDX-License-Identifier: BSD-2-Clause
#
# Author: Hesham Almatary <Hesham.Almatary@cl.cam.ac.uk>
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
# THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import subprocess
import threading
import typing

from .crosscompileproject import (
    CompilationTargets,
    CrossCompileAutotoolsProject,
    DefaultInstallDir,
    GitRepository,
)
from ..build_qemu import BuildCheriAllianceQEMU
from ..run_qemu import LaunchQEMUBase
from .cheri_microkit import BuildCheriMicrokit
from .opensbi import BuildAllianceOpenSBI
from ...qemu_utils import QemuOptions


class BuildCheriseL4Excercises(CrossCompileAutotoolsProject):
    repository = GitRepository(
        "https://github.com/heshamelmatary/cheri-exercises.git",
        force_branch=True,
        default_branch="sel4-microkit",
    )
    target = "cheri-sel4-exercises"
    dependencies = ("cheri-microkit",)
    is_sdk_target = False
    needs_sysroot = False
    native_install_dir = DefaultInstallDir.CHERI_ALLIANCE_SDK

    supported_architectures = (
        CompilationTargets.FREESTANDING_RISCV64_ZPURECAP,
        CompilationTargets.FREESTANDING_RISCV64,
    )

    supported_exercises = [
        "buffer-overflow-stack",
        "buffer-overflow-global",
        "cheri-tags",
        "cheri-allocator",
        "control-flow-pointer",
        "subobject-bounds",
        "type-confusion",
        "compile-and-run",
    ]

    supported_missions = [
        "buffer-overflow-control-flow",
        "uninitialized-stack-frame-control-flow",
    ]

    supported_exercises_archs = ["riscv64", "riscv64-purecap"]

    # ----------------------------------------------------------------------------------
    # Project config options
    # ----------------------------------------------------------------------------------
    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        cls.board: str = typing.cast(
            str,
            cls.add_config_option(
                "board",
                metavar="BOARD",
                show_help=True,
                default="qemu_virt_riscv64",
                help="CHERI-Microkit board to build the exercise for.",
            ),
        )

        cls.exercise: str = typing.cast(
            str,
            cls.add_config_option(
                "exercise",
                metavar="EXERCISE",
                show_help=True,
                default="buffer-overflow-stack",
                help="Name of a single CHERI exercise to build.",
            ),
        )

        cls.mission: str = typing.cast(
            str,
            cls.add_config_option(
                "mission",
                metavar="MISSION",
                show_help=True,
                default="buffer-overflow-control-flow",
                help="Name of a single CHERI mission to build.",
            ),
        )

        cls.build_all: bool = typing.cast(
            bool,
            cls.add_bool_option(
                "build_all",
                show_help=True,
                default=False,
                help="Build all CHERI exercises *and* missions.",
            ),
        )

    def setup(self):
        super().setup()

    def configure(self):
        pass

    def install(self, **kwargs):
        pass

    def clean(self) -> None:
        self.clean_directory(self.source_dir / "build")
        super().clean()

    # ----------------------------------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------------------------------
    def _microkit_bin(self, microkit: BuildCheriMicrokit):
        """
        Return the path to the Microkit tool for the *release version* we
        matched from the dependency project.
        """
        sdk = microkit.install_dir / f"microkit-sdk-{self.microkit_project.release_version}"
        return sdk / "bin" / "microkit"

    def _exercise_src_dir(self, exercise: str):
        return self.source_dir / "src" / "exercises" / exercise

    def _mission_src_dir(self, mission: str):
        return self.source_dir / "src" / "missions" / mission

    def _common_src_dir(self):
        return self.source_dir / "src" / "common"

    def _ccc(self):
        return self.source_dir / "tools" / "ccc"

    def _gen_image(self):
        return self.source_dir / "tools" / "gen_image"

    # ----------------------------------------------------------------------------------
    # Build: Exercises
    # ----------------------------------------------------------------------------------
    def build_exercise(self, exercise: str, microkit: BuildCheriMicrokit) -> None:
        """
        Build a single exercise for all supported architectures and generate Microkit images.
        """
        microkit_bin = self._microkit_bin(microkit)
        src_dir = self._exercise_src_dir(exercise)
        ccc = self._ccc()

        match exercise:
            case (
                "buffer-overflow-stack"
                | "buffer-overflow-global"
                | "cheri-tags"
                | "control-flow-pointer"
                | "cheri-allocator"
                | "subobject-bounds"
                | "type-confusion"
            ):
                src_c = src_dir / f"{exercise}.c"
                sys_file = src_dir / f"{exercise}.system"
                for target in self.supported_exercises_archs:
                    # Build ELF
                    app_elf = self.real_install_root_dir / f"{exercise}.elf"
                    cmd = [ccc, target, "-G0", src_c, "-o", app_elf]
                    if "purecap" in target:
                        # Add CHERI subobject bounds tightening only for purecap builds.
                        cmd.insert(3, "-cheri-bounds=subobject-safe")
                    self.run_cmd(cmd)

                    # Package a bootable Microkit image
                    img_name = f"{exercise}-cheri-sel4-microkit-{target}-{self.board}.img"
                    self.run_cmd(
                        [
                            microkit_bin,
                            sys_file,
                            "--search-path",
                            self.real_install_root_dir,
                            "--config",
                            "cheri",
                            "--board",
                            self.board,
                            "--output",
                            self.real_install_root_dir / img_name,
                        ]
                    )

            case "compile-and-run":
                # Two small print demos: pointer (always) and capability (purecap only)
                for target in self.supported_exercises_archs:
                    # print-pointer
                    elf = self.real_install_root_dir / "print-pointer.elf"
                    self.run_cmd(
                        [
                            ccc,
                            target,
                            self.source_dir
                            / "src"
                            / "exercises"
                            / "compile-and-run"
                            / "print-pointer.c",
                            "-o",
                            elf,
                        ]
                    )
                    self.run_cmd(
                        [
                            self._gen_image(),
                            "-o",
                            self.real_install_root_dir
                            / f"print-pointer-cheri-sel4-microkit-{target}-{self.board}.img",
                            elf,
                        ],
                        cwd=self.real_install_root_dir,
                    )

                    # print-capability (purecap only)
                    if "purecap" in target:
                        elf = self.real_install_root_dir / "print-capability.elf"
                        self.run_cmd(
                            [
                                ccc,
                                target,
                                self.source_dir
                                / "src"
                                / "exercises"
                                / "compile-and-run"
                                / "print-capability.c",
                                "-o",
                                elf,
                            ]
                        )
                        self.run_cmd(
                            [
                                self._gen_image(),
                                "-o",
                                self.real_install_root_dir
                                / f"print-capability-cheri-sel4-microkit-{target}-{self.board}.img",
                                elf,
                            ],
                            cwd=self.real_install_root_dir,
                        )

            case _:
                self.warning(f"Unknown exercise: {exercise}")

    # ----------------------------------------------------------------------------------
    # Build: Missions
    # ----------------------------------------------------------------------------------
    def build_mission(self, mission: str, microkit: BuildCheriMicrokit) -> None:
        """
        Build a mission for all supported architectures.
        """
        microkit_bin = self._microkit_bin(microkit)
        src_dir = self._mission_src_dir(mission)
        common_dir = self._common_src_dir()
        ccc = self._ccc()

        match mission:
            case "buffer-overflow-control-flow":
                src_app_c = src_dir / "buffer-overflow.c"
                src_btpalloc_c = src_dir / "btpalloc.c"
                src_serial_server = common_dir / "serial_server.c"
                sys_file = src_dir / f"{mission}.system"

                for target in self.supported_exercises_archs:
                    elf_app = self.real_install_root_dir / f"{mission}.elf"
                    elf_serial_server = self.real_install_root_dir / "serial_server.elf"

                    # App
                    self.run_cmd([ccc, target, src_app_c, src_btpalloc_c, "-o", elf_app])
                    # Serial server
                    self.run_cmd([ccc, target, src_serial_server, "-o", elf_serial_server])

                    # Package
                    img_name = f"{mission}-cheri-sel4-microkit-{target}-{self.board}.img"
                    self.run_cmd(
                        [
                            microkit_bin,
                            sys_file,
                            "--search-path",
                            self.real_install_root_dir,
                            "--config",
                            "cheri",
                            "--board",
                            self.board,
                            "--output",
                            self.real_install_root_dir / img_name,
                        ]
                    )

            case "uninitialized-stack-frame-control-flow":
                src_app_c = src_dir / "stack-mission.c"
                src_serial_server = common_dir / "serial_server.c"
                sys_file = src_dir / f"{mission}.system"

                for target in self.supported_exercises_archs:
                    elf_app = self.real_install_root_dir / f"{mission}.elf"
                    elf_serial_server = self.real_install_root_dir / "serial_server.elf"

                    # App
                    self.run_cmd([ccc, target, src_app_c, "-o", elf_app])
                    # Serial server
                    self.run_cmd([ccc, target, src_serial_server, "-o", elf_serial_server])

                    # Package
                    img_name = f"{mission}-cheri-sel4-microkit-{target}-{self.board}.img"
                    self.run_cmd(
                        [
                            microkit_bin,
                            sys_file,
                            "--search-path",
                            self.real_install_root_dir,
                            "--config",
                            "cheri",
                            "--board",
                            self.board,
                            "--output",
                            self.real_install_root_dir / img_name,
                        ]
                    )

            case _:
                self.warning(f"Unknown mission: {mission}")

    # ----------------------------------------------------------------------------------
    # Compile phase
    # ----------------------------------------------------------------------------------
    def compile(self, **kwargs) -> None:  # noqa: D401
        """
        Build either a single exercise (default) or everything if --build-all is set.
        """
        # Cache the dependency project instance (used to locate Microkit SDK bins, etc.)
        self.microkit_project = BuildCheriMicrokit.get_instance(self)

        if self.build_all:
            for exercise in self.supported_exercises:
                self.build_exercise(exercise, self.microkit_project)
            for mission in self.supported_missions:
                self.build_mission(mission, self.microkit_project)
        else:
            # Build *either* the requested exercise or mission (depending on what the user passed).
            if self.exercise in self.supported_exercises:
                self.build_exercise(self.exercise, self.microkit_project)
            elif self.mission in self.supported_missions:
                self.build_mission(self.mission, self.microkit_project)
            else:
                self.fatal(
                    f"Neither exercise '{self.exercise}' nor mission '{self.mission}' is valid. ")

    # ----------------------------------------------------------------------------------
    # QEMU helpers
    # ----------------------------------------------------------------------------------
    def _opensbi_firmware_path(self, opensbi_project: BuildAllianceOpenSBI):
        """
        Path to the OpenSBI firmware used when booting QEMU. Adjust if layout changes.
        """
        return (
            opensbi_project.install_dir
            / "share"
            / "opensbi"
            / "l64pc128"
            / "generic"
            / "firmware"
            / "fw_jump.elf"
        )

    def _make_qemu_options(
        self,
        opensbi_project: BuildAllianceOpenSBI,
        machine: str = "virt",
        memory_size: str = "2G",
        add_network_device: bool = False,
    ) -> QemuOptions:
        """
        Create and return a configured QemuOptions instance used for test runs.
        """
        options = QemuOptions(self.crosscompile_target)
        options.machine_flags = [
            "-M",
            machine,
            "-cpu",
            "codasip-a730,cheri_levels=2",
            "-smp",
            "1",
            "-bios",
            self._opensbi_firmware_path(opensbi_project),
        ]
        options.memory_size = memory_size
        options.add_network_device = add_network_device
        return options

    # ----------------------------------------------------------------------------------
    # Run QEMU and capture expected output
    # ----------------------------------------------------------------------------------
    def run_qemu_and_monitor(
        self,
        cmd: typing.Sequence[typing.Union[str, "os.PathLike[str]"]],
        expected_output: str,
        label: str,
        timeout: int = 30,
    ) -> None:
        """
        Run a QEMU subprocess and monitor its output for an expected string.

        :param cmd: Command list to invoke QEMU.
        :param expected_output: Substring to look for in captured stdout.
        :param label: Friendly label for reporting success/failure.
        :param timeout: Seconds to wait before aborting (per run).
        :raises RuntimeError: If expected output not seen within timeout.
        """
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )

        found = False

        def monitor_output():
            nonlocal found
            assert proc.stdout is not None  # for type checkers
            for line in proc.stdout:
                print(line, end="")  # optionally show live
                if expected_output in line:
                    found = True
                    proc.terminate()
                    break

        monitor_thread = threading.Thread(target=monitor_output, daemon=True)
        monitor_thread.start()
        monitor_thread.join(timeout=timeout)

        if monitor_thread.is_alive():
            proc.terminate()
            monitor_thread.join()

        try:
            proc.wait(timeout=2)
        except Exception:
            proc.kill()

        if found:
            print(f"✅ CHERI Exercise: {label} succeeded.")
        else:
            raise RuntimeError(f"❌ CHERI Exercise: {label} failed (expected '{expected_output}').")

    # ----------------------------------------------------------------------------------
    # Test harness
    # ----------------------------------------------------------------------------------
    def run_tests(self) -> None:
        """
        Boot the built images under QEMU and validate that each produces the
        *expected* output string (crash, violation, printout, etc.).
        """
        expected_exercise_output = {
            "cheri": {
                "buffer-overflow-stack": "Bounds violation",
                "buffer-overflow-global": "Bounds violation",
                "cheri-tags": "Tag violation",
                "compile-and-run": "",
                "cheri-allocator": "Bounds violation",
                "control-flow-pointer": "Bounds violation",
                "subobject-bounds": "Bounds violation",
                "type-confusion": "Tag violation",
            },
            "baseline": {
                "buffer-overflow-stack": "upper[0] = b",
                "buffer-overflow-global": "c = b",
                "cheri-tags": "*r=57",
                "compile-and-run": "",
                "cheri-allocator": "Load access fault",
                "control-flow-pointer": "Instruction access fault",
                "subobject-bounds": "b.i = b",
                "type-confusion": "lp.ptr ello World!",
            },
        }

        expected_mission_output = {
            "cheri": {
                "buffer-overflow-control-flow": "Returning alloc =",
                "uninitialized-stack-frame-control-flow": "provide some cookies",
            },
            "baseline": {
                "buffer-overflow-control-flow": "Returning alloc =",
                "uninitialized-stack-frame-control-flow": "provide some cookies",
        },
        }

        self.opensbi_project = BuildAllianceOpenSBI.get_instance(self)

        # Board override
        machine = "hobgoblin-vcu118" if self.board == "hobgoblin_vcu118" else "virt"

        options = self._make_qemu_options(
            opensbi_project=self.opensbi_project,
            machine=machine,
            memory_size="2G",
            add_network_device=False,
        )

        # ------------------------------------------------------------------
        # Build-all: iterate across all exercises/missions + arch variants
        # ------------------------------------------------------------------
        if self.build_all:
            # Exercises
            for ex in self.supported_exercises:
                for target in self.supported_exercises_archs:
                    if ex == "compile-and-run":
                        # print-pointer
                        cmd = options.get_commandline(
                            qemu_command=BuildCheriAllianceQEMU.qemu_binary(self),
                            kernel_file=self.install_dir
                            / f"print-pointer-cheri-sel4-microkit-{target}-{self.board}.img",
                        )
                        expected_output = (
                            "size of pointer: 16"
                            if "purecap" in target
                            else "size of pointer: 8"
                        )
                        self.run_qemu_and_monitor(cmd, expected_output, label="print-pointer")

                        # print-capability (purecap only)
                        if "purecap" in target:
                            cmd = options.get_commandline(
                                qemu_command=BuildCheriAllianceQEMU.qemu_binary(self),
                                kernel_file=self.install_dir
                                / f"print-capability-cheri-sel4-microkit-{target}-{self.board}.img",
                            )
                            self.run_qemu_and_monitor(
                                cmd, "cap to cap length: 16", label="print-capability"
                            )
                    else:
                        cmd = options.get_commandline(
                            qemu_command=BuildCheriAllianceQEMU.qemu_binary(self),
                            kernel_file=self.install_dir
                            / f"{ex}-cheri-sel4-microkit-{target}-{self.board}.img",
                        )
                        expected_output = expected_exercise_output[
                            "cheri" if "purecap" in target else "baseline"
                        ][ex]
                        self.run_qemu_and_monitor(cmd, expected_output, label=ex)

            # Missions
            for mission in self.supported_missions:
                for target in self.supported_exercises_archs:
                    cmd = options.get_commandline(
                        qemu_command=BuildCheriAllianceQEMU.qemu_binary(self),
                        kernel_file=self.install_dir
                        / f"{mission}-cheri-sel4-microkit-{target}-{self.board}.img",
                    )
                    expected_output = expected_mission_output[
                        "cheri" if "purecap" in target else "baseline"
                    ][mission]
                    self.run_qemu_and_monitor(cmd, expected_output, label=mission)

            return

        # ------------------------------------------------------------------
        # Single build case (default)
        # ------------------------------------------------------------------
        # Prefer exercise; fall back to mission if user asked for that.
        if self.exercise in self.supported_exercises:
            kernel = (
                self.install_dir
                / f"{self.exercise}-cheri-sel4-microkit-riscv64-purecap-{self.board}.img"
            )
            cmd = options.get_commandline(
                qemu_command=BuildCheriAllianceQEMU.qemu_binary(self),
                kernel_file=kernel,
            )
            self.run_cmd(cmd)
        elif self.mission in self.supported_missions:
            kernel = (
                self.install_dir
                / f"{self.mission}-cheri-sel4-microkit-riscv64-purecap-{self.board}.img"
            )
            cmd = options.get_commandline(
                qemu_command=BuildCheriAllianceQEMU.qemu_binary(self),
                kernel_file=kernel,
            )
            self.run_cmd(cmd)
        else:
            self.fatal(
                f"Can't run tests: neither valid exercise '{self.exercise}' nor mission '{self.mission}'."
            )

    def process(self) -> None:
        super().process()
