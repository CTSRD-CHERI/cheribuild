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

import os
import subprocess
import threading
from typing import Sequence, Union

from .cheri_microkit import BuildCheriMicrokit
from .crosscompileproject import CompilationTargets, CrossCompileProject, DefaultInstallDir, GitRepository
from ..build_qemu import BuildCheriAllianceQEMU, BuildQEMU
from ..run_qemu import LaunchQEMUBase
from ...config.chericonfig import RiscvCheriISA
from ...qemu_utils import QemuOptions

# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------
AARCH64_LOADER_ADDR = "0x70000000"
AARCH64_LOADER_CPU_NUM = 0


class BuildCheriseL4Excercises(CrossCompileProject):
    """
    Build and test CHERI seL4 exercises and missions (Microkit).
    """

    repository = GitRepository(
        "https://github.com/CHERI-Alliance/CHERI-seL4-Exercises.git",
        force_branch=True,
        default_branch="sel4-microkit",
    )
    target = "cheri-sel4-exercises"
    dependencies = ("cheri-microkit",)
    is_sdk_target = False
    _needs_sysroot = False
    native_install_dir = DefaultInstallDir.CHERI_ALLIANCE_SDK

    _supported_architectures = (
        CompilationTargets.FREESTANDING_RISCV64_PURECAP_093,
        CompilationTargets.FREESTANDING_MORELLO_PURECAP,
    )
    supported_riscv_cheri_standard = RiscvCheriISA.EXPERIMENTAL_STD093

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

    # ------------------------------------------------------------------
    # Project config
    # ------------------------------------------------------------------
    def configure(self) -> None:
        if self.crosscompile_target.is_riscv(include_purecap=True):
            self.board = "qemu_virt_riscv64"
            # Build and test both baseline and purecap variants
            self.targets = ["riscv64", "riscv64-purecap"]
        elif self.crosscompile_target.is_aarch64(include_purecap=True):
            self.board = "morello_qemu"
            self.targets = ["morello-aarch64", "morello-purecap"]

    def needs_configure(self) -> bool:
        return True

    def install(self, **kwargs) -> None:
        # Nothing to install for this project (images output to install dir).
        return

    def clean(self) -> None:
        self.clean_directory(self.source_dir / "build")
        super().clean()

    # ------------------------------------------------------------------
    # Internal helpers (paths/tools)
    # ------------------------------------------------------------------
    def _microkit_bin(self, microkit: BuildCheriMicrokit):
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

    # ------------------------------------------------------------------
    # Small utilities to remove duplication
    # ------------------------------------------------------------------
    def _build_single_elf(
        self,
        ccc,
        target: str,
        sources: Sequence[Union[str, "os.PathLike[str]"]],
        output_elf: Union[str, "os.PathLike[str]"],
    ) -> None:
        """Invoke the ccc helper to build a single ELF for a target."""
        flags: list[str] = []
        if "purecap" in target:
            flags.append("-cheri-bounds=subobject-safe")
        if self.crosscompile_target.is_riscv(include_purecap=True):
            flags.append("-G0")

        cmd: list[Union[str, "os.PathLike[str]"]] = [ccc, target]
        cmd.extend(flags)
        cmd.extend(sources)
        cmd.extend(["-o", output_elf])
        self.run_cmd(cmd)

    def _package_microkit_image(
        self,
        microkit_bin,
        system_file: Union[str, "os.PathLike[str]"],
        image_stem: str,
    ) -> None:
        """Run Microkit tool to produce a bootable image."""
        out_img = self.real_install_root_dir / image_stem
        self.run_cmd(
            [
                microkit_bin,
                system_file,
                "--search-path",
                self.real_install_root_dir,
                "--config",
                "cheri",
                "--board",
                self.board,
                "--output",
                out_img,
            ]
        )

    # ------------------------------------------------------------------
    # Build: Exercises
    # ------------------------------------------------------------------
    def build_exercise(self, exercise: str, microkit: BuildCheriMicrokit) -> None:
        """Build a single exercise for all supported architectures and generate images."""
        microkit_bin = self._microkit_bin(microkit)
        src_dir = self._exercise_src_dir(exercise)
        ccc = self._ccc()

        common_exercises = {
            "buffer-overflow-stack",
            "buffer-overflow-global",
            "cheri-tags",
            "control-flow-pointer",
            "cheri-allocator",
            "subobject-bounds",
            "type-confusion",
        }

        if exercise in common_exercises:
            src_c = src_dir / f"{exercise}.c"
            sys_file = src_dir / f"{exercise}.system"

            for target in self.targets:
                elf = self.real_install_root_dir / f"{exercise}.elf"
                self._build_single_elf(ccc, target, [src_c], elf)

                img_name = f"{exercise}-cheri-sel4-microkit-{target}-{self.board}.img"
                self._package_microkit_image(microkit_bin, sys_file, img_name)

        elif exercise == "compile-and-run":
            self._build_compile_and_run(ccc)
        else:
            self.warning(f"Unknown exercise: {exercise}")

    def _build_compile_and_run(self, ccc) -> None:
        src_dir = self.source_dir / "src" / "exercises" / "compile-and-run"

        for target in self.targets:
            # print-pointer
            elf = self.real_install_root_dir / "print-pointer.elf"
            self.run_cmd([ccc, target, src_dir / "print-pointer.c", "-o", elf])
            self.run_cmd(
                [
                    self._gen_image(),
                    "-a",
                    target,
                    "-o",
                    self.real_install_root_dir / f"print-pointer-cheri-sel4-microkit-{target}-{self.board}.img",
                    elf,
                ],
                cwd=self.real_install_root_dir,
            )

            # print-capability (purecap only)
            if "purecap" in target:
                elf = self.real_install_root_dir / "print-capability.elf"
                self.run_cmd([ccc, target, src_dir / "print-capability.c", "-o", elf])
                self.run_cmd(
                    [
                        self._gen_image(),
                        "-a",
                        target,
                        "-o",
                        self.real_install_root_dir / f"print-capability-cheri-sel4-microkit-{target}-{self.board}.img",
                        elf,
                    ],
                    cwd=self.real_install_root_dir,
                )

    # ------------------------------------------------------------------
    # Build: Missions
    # ------------------------------------------------------------------
    def build_mission(self, mission: str, microkit: BuildCheriMicrokit) -> None:
        """Build a mission for all supported architectures and package images."""
        microkit_bin = self._microkit_bin(microkit)
        src_dir = self._mission_src_dir(mission)
        common_dir = self._common_src_dir()
        ccc = self._ccc()

        if mission == "buffer-overflow-control-flow":
            src_app_c = src_dir / "buffer-overflow.c"
            src_btpalloc_c = src_dir / "btpalloc.c"
            src_serial_server = common_dir / "serial_server.c"
            sys_file = src_dir / f"{mission}.system"

            for target in self.targets:
                elf_app = self.real_install_root_dir / f"{mission}.elf"
                elf_serial = self.real_install_root_dir / "serial_server.elf"

                self._build_single_elf(ccc, target, [src_app_c, src_btpalloc_c], elf_app)
                self._build_single_elf(ccc, target, [src_serial_server], elf_serial)

                img_name = f"{mission}-cheri-sel4-microkit-{target}-{self.board}.img"
                self._package_microkit_image(microkit_bin, sys_file, img_name)

        elif mission == "uninitialized-stack-frame-control-flow":
            src_app_c = src_dir / "stack-mission.c"
            src_serial_server = common_dir / "serial_server.c"
            sys_file = src_dir / f"{mission}.system"

            for target in self.targets:
                elf_app = self.real_install_root_dir / f"{mission}.elf"
                elf_serial = self.real_install_root_dir / "serial_server.elf"

                self._build_single_elf(ccc, target, [src_app_c], elf_app)
                self._build_single_elf(ccc, target, [src_serial_server], elf_serial)

                img_name = f"{mission}-cheri-sel4-microkit-{target}-{self.board}.img"
                self._package_microkit_image(microkit_bin, sys_file, img_name)
        else:
            self.warning(f"Unknown mission: {mission}")

    # ------------------------------------------------------------------
    # Compile phase
    # ------------------------------------------------------------------
    def compile(self, **kwargs) -> None:
        """Build all exercises and missions."""
        self.microkit_project = BuildCheriMicrokit.get_instance(self)
        for exercise in self.supported_exercises:
            self.build_exercise(exercise, self.microkit_project)
        for mission in self.supported_missions:
            self.build_mission(mission, self.microkit_project)

    # ------------------------------------------------------------------
    # Process phase
    # ------------------------------------------------------------------
    def process(self):
        self.microkit_project = BuildCheriMicrokit.get_instance(self)
        if self.crosscompile_target.is_riscv(include_purecap=True):
            os.environ["CHERIBUILD_SDK"] = str(self.config.cheri_alliance_sdk_dir)
            os.environ["MICROKIT_SDK"] = (
                str(self.install_dir) + f"/microkit-sdk-{self.microkit_project.release_version}"
            )
        elif self.crosscompile_target.is_aarch64(include_purecap=True):
            os.environ["CHERIBUILD_SDK"] = str(self.config.morello_sdk_dir)
            os.environ["MICROKIT_SDK"] = (
                str(self.install_dir) + f"/microkit-sdk-{self.microkit_project.release_version}"
            )
        super().process()

    # ------------------------------------------------------------------
    # QEMU helpers
    # ------------------------------------------------------------------
    def _make_riscv_qemu_options(
        self,
        machine: str = "virt",
        memory_size: str = "2G",
        add_network_device: bool = False,
    ) -> QemuOptions:
        options = QemuOptions(self.crosscompile_target)
        bios_args = LaunchQEMUBase.riscv_bios_arguments(self.crosscompile_target, self)
        options.machine_flags = [
            "-M",
            machine,
            "-cpu",
            "codasip-a730,cheri_levels=2",
            "-smp",
            "1",
            *bios_args,
        ]
        options.memory_size = memory_size
        options.add_network_device = add_network_device
        return options

    def _make_morello_qemu_options(
        self,
        machine: str = "virt,virtualization=on",
        memory_size: str = "2G",
        add_network_device: bool = False,
    ) -> QemuOptions:
        options = QemuOptions(self.crosscompile_target)
        options.machine_flags = ["-M", machine, "-cpu", "morello", "-smp", "1"]
        options.memory_size = memory_size
        options.add_network_device = add_network_device
        return options

    def _clone_qemu_options(self, base: QemuOptions) -> QemuOptions:
        """Create a fresh QemuOptions with copied fields to avoid in-loop mutation."""
        new_opts = QemuOptions(self.crosscompile_target)
        # Copy simple fields
        new_opts.memory_size = getattr(base, "memory_size", None)
        new_opts.add_network_device = getattr(base, "add_network_device", False)
        # Copy flags list defensively
        new_opts.machine_flags = list(getattr(base, "machine_flags", []))
        return new_opts

    @staticmethod
    def _add_aarch64_loader(options: QemuOptions, image: Union[str, "os.PathLike[str]"]) -> None:
        options.machine_flags += [
            "-device",
            f"loader,file={image},addr={AARCH64_LOADER_ADDR},cpu-num={AARCH64_LOADER_CPU_NUM}",
        ]

    # ------------------------------------------------------------------
    # Run QEMU and capture expected output
    # ------------------------------------------------------------------
    def run_qemu_and_monitor(
        self,
        cmd: Sequence[Union[str, "os.PathLike[str]"]],
        expected_output: str,
        label: str,
        timeout: int = 30,
    ) -> None:
        """Run a QEMU subprocess and monitor its output for an expected string."""
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        found = False

        def monitor_output() -> None:
            nonlocal found
            assert proc.stdout is not None  # for type checkers
            for line in proc.stdout:
                print(line, end="")
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

    # ------------------------------------------------------------------
    # Test harness
    # ------------------------------------------------------------------
    def run_tests(self) -> None:
        """Boot built images under QEMU and validate expected outputs."""
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
                "cheri-tags": "*r=",
                "compile-and-run": "",
                "cheri-allocator": "VMFault: ip=",
                "control-flow-pointer": "VMFault: ip=",
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

        if self.config.pretend:
            return

        # Select QEMU binary
        qemu_cmd = BuildQEMU.qemu_binary(self)
        if self.config.riscv_cheri_isa == RiscvCheriISA.EXPERIMENTAL_STD093:
            qemu_cmd = BuildCheriAllianceQEMU.qemu_binary(self)
        else:
            qemu_cmd = BuildQEMU.qemu_binary_for_target(CompilationTargets.FREESTANDING_MORELLO_PURECAP, self.config)

        # Base QEMU options per architecture (never mutate these in loops)
        if self.crosscompile_target.is_riscv(include_purecap=True):
            base_options = self._make_riscv_qemu_options()
        elif self.crosscompile_target.is_aarch64(include_purecap=True):
            base_options = self._make_morello_qemu_options()
        else:
            raise RuntimeError("Unsupported crosscompile target")

        # ---------------------------- Exercises -----------------------------
        for ex in self.supported_exercises:
            for target in self.targets:
                boot_img = self.install_dir / f"{ex}-cheri-sel4-microkit-{target}-{self.board}.img"

                if ex == "compile-and-run":
                    # print-pointer
                    ptr_img = self.install_dir / (f"print-pointer-cheri-sel4-microkit-{target}-{self.board}.img")
                    options = self._clone_qemu_options(base_options)
                    if self.crosscompile_target.is_aarch64(include_purecap=True):
                        self._add_aarch64_loader(options, ptr_img)
                    cmd = options.get_commandline(qemu_command=qemu_cmd, kernel_file=ptr_img)
                    expected = "size of pointer: 16" if "purecap" in target else "size of pointer: 8"
                    self.run_qemu_and_monitor(cmd, expected, label="print-pointer")

                    # print-capability (purecap only)
                    if "purecap" in target:
                        cap_img = self.install_dir / (f"print-capability-cheri-sel4-microkit-{target}-{self.board}.img")
                        options = self._clone_qemu_options(base_options)
                        if self.crosscompile_target.is_aarch64(include_purecap=True):
                            self._add_aarch64_loader(options, cap_img)
                        cmd = options.get_commandline(qemu_command=qemu_cmd, kernel_file=cap_img)
                        self.run_qemu_and_monitor(cmd, "cap to cap length: 16", label="print-capability")
                else:
                    options = self._clone_qemu_options(base_options)
                    if self.crosscompile_target.is_aarch64(include_purecap=True):
                        self._add_aarch64_loader(options, boot_img)
                    cmd = options.get_commandline(qemu_command=qemu_cmd, kernel_file=boot_img)

                    print(cmd)
                    expected = expected_exercise_output["cheri" if "purecap" in target else "baseline"][ex]
                    self.run_qemu_and_monitor(cmd, expected, label=ex)

        # ---------------------------- Missions ------------------------------
        for mission in self.supported_missions:
            for target in self.targets:
                boot_img = self.install_dir / f"{mission}-cheri-sel4-microkit-{target}-{self.board}.img"
                options = self._clone_qemu_options(base_options)
                if self.crosscompile_target.is_aarch64(include_purecap=True):
                    self._add_aarch64_loader(options, boot_img)
                cmd = options.get_commandline(qemu_command=qemu_cmd, kernel_file=boot_img)
                expected = expected_mission_output["cheri" if "purecap" in target else "baseline"][mission]
                self.run_qemu_and_monitor(cmd, expected, label=mission)

        return
