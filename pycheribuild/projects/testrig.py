#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright 2022 Alex Richardson
# Copyright 2022 Google LLC
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
import subprocess
import time
import typing
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from .build_qemu import BuildQEMU
from .project import DefaultInstallDir, MakefileProject, Project
from .repository import GitRepository
from .sail import BuildSailCheriRISCV
from .simple_project import BoolConfigOption, IntConfigOption, OptionalIntConfigOption, SimpleProject
from ..processutils import FakePopen, commandline_to_str, popen
from ..utils import cached_property, find_free_port


# This repository contains various implementations and QuickCheckVEngine
class BuildTestRig(MakefileProject):
    do_not_add_to_targets = True
    repository = GitRepository("https://github.com/CTSRD-CHERI/TestRIG")
    default_directory_basename = "TestRIG"
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    build_in_source_dir = False  # Don't run git clean as part of clean

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("cabal", apt="cabal-install", homebrew="cabal-install")

    def clean(self):
        self.run_make("clean-vengines")

    def compile(self, **kwargs) -> None:
        self.run_make("vengines")


class BuildQuickCheckVengine(Project):
    target = "quickcheckvengine"
    repository = GitRepository("https://github.com/CTSRD-CHERI/QuickCheckVEngine")
    default_directory_basename = "QuickCheckVEngine"
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    build_in_source_dir = True

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("cabal", apt="cabal-install", homebrew="cabal-install")
        # TODO: check for at min version?
        # cabal_version = get_program_version(Path("cabal"), config=self.config, program_name=b"cabal-install")

    def clean(self):
        self.run_cmd("cabal", "v2-clean", cwd=self.build_dir)

    def compile(self, **kwargs) -> None:
        self.run_cmd("cabal", "v2-update", cwd=self.build_dir)
        self.run_cmd("cabal", "v2-build", cwd=self.build_dir)

    def run_qcvengine(self, *args: str, cwd: "Optional[Path]" = None, **kwargs):
        self.run_cmd(self.get_qcv_path(), *args, cwd=cwd or self.build_dir, **kwargs)

    def get_qcv_path(self) -> Path:
        result = (
            self.run_cmd(["cabal", "list-bin", "QCVEngine"], capture_output=True, cwd=self.build_dir)
            .stdout.decode("utf-8")
            .strip()
        )
        return Path(result or "/invalid/path/to/QCVEngine")

    def run_tests(self):
        self.run_qcvengine("--help", expected_exit_code=1)


class TestRigTraces(Project):
    target = "testrig-traces-repository"
    default_directory_basename = "TestRIG-traces"
    repository = GitRepository("https://github.com/CTSRD-CHERI/TestRIG-traces.git")
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL

    def compile(self, **kwargs):
        pass  # This class only exists to provide the source repository


class RunTestRIGBase(SimpleProject):
    do_not_add_to_targets = True
    dependencies = ("quickcheckvengine", "testrig-traces-repository")
    verification_archstring: "typing.ClassVar[str]"
    existing_test_impl_port = OptionalIntConfigOption(
        "test-implementation-port",
        help="Use a running test implementation instead.",
    )

    @property
    def extra_vengine_args(self) -> "list[str]":
        return self.vengine_options

    @property
    def log_dir_name(self) -> str:
        return self.target

    @abstractmethod
    def get_reference_implementation_command(self, port: int) -> "list[str]":
        ...

    @abstractmethod
    def get_test_implementation_command(self, port: int) -> "list[str]":
        ...

    @abstractmethod
    def _get_vengine_action_args(self, log_dir: Path) -> "list[str]":
        ...

    @cached_property
    def run_implementations_with_tracing(self):
        return self.config.debug_output  # Only print traces in extremely verbose mode

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        super().setup_config_options(**kwargs)
        cls.vengine_options = cls.add_list_option(
            "extra-vengine-options",
            metavar="OPTIONS",
            help="Additional command line options to pass to QCVEngine",
        )

    def get_test_impl(self, port: int):
        if self.existing_test_impl_port is not None:
            return FakePopen()
        else:
            return popen(
                self.get_test_implementation_command(port),
                config=self.config,
                stdin=subprocess.DEVNULL,
                cwd="/",
            )

    def run_testrig(self) -> None:
        reference_impl_tmpsock = find_free_port()
        reference_impl_tmpsock.socket.close()  # allow sail to use the socket
        reference_impl_port = reference_impl_tmpsock.port
        trace_base_dir = TestRigTraces.get_instance(self).source_dir
        if not trace_base_dir.is_dir():
            self.dependency_error("Missing TestRIG-traces source directory", cheribuild_target=TestRigTraces.target)
        log_dir = trace_base_dir / self.log_dir_name
        self.makedirs(log_dir)
        if self.existing_test_impl_port is not None:
            test_impl_port = self.existing_test_impl_port
        else:
            tmp = find_free_port()
            tmp.socket.close()  # allow test implementation to use the socket
            test_impl_port = tmp.port
        with popen(
            self.get_reference_implementation_command(reference_impl_port),
            config=self.config,
            stdin=subprocess.DEVNULL,
            cwd=log_dir,
        ) as reference_cmd:
            with self.get_test_impl(test_impl_port) as test_cmd:
                if not self.config.pretend:
                    time.sleep(1)  # wait 1 second for the implementations to start up.
                if reference_cmd.poll() is not None:
                    test_cmd.kill()  # kill the other implementation so that the with statement can complete.
                    self.fatal(
                        "Reference implementation failed to start correctly. Command was:",
                        commandline_to_str(self.get_reference_implementation_command(reference_impl_port)),
                    )
                    return
                elif self.existing_test_impl_port is not None:
                    self.info("Attaching to implementation running on port", self.existing_test_impl_port)
                elif test_cmd.poll() is not None:
                    reference_cmd.kill()  # kill the other implementation so that the with statement can complete.
                    self.fatal(
                        "Test implementation failed to start correctly. Command was:",
                        commandline_to_str(self.get_test_implementation_command(test_impl_port)),
                    )
                    return
                vengine_instance = BuildQuickCheckVengine.get_instance(self)
                vengine_args = [
                    "-a",
                    str(reference_impl_port),
                    "-b",
                    str(test_impl_port),
                    "-r",
                    self.verification_archstring,
                ]
                vengine_args.extend(self._get_vengine_action_args(log_dir))
                vengine_instance.run_qcvengine(*vengine_args, *self.extra_vengine_args, cwd=log_dir)
                # Kill the simulators if they didn't shut down cleanly after 3 seconds
                if not self.config.pretend:
                    reference_cmd.wait(timeout=3)
                    reference_cmd.kill()
                    test_cmd.wait(timeout=3)
                    test_cmd.kill()

    def process(self):
        self.run_testrig()

    def run_tests(self):
        self.run_testrig()


class RunTestRIGFuzz(RunTestRIGBase, ABC):
    do_not_add_to_targets = True
    noninteractive = BoolConfigOption("non-interactive", help="Run without user interaction")
    stop_on_error = BoolConfigOption("stop-on-error", help="Stop the run once the first error is encountered")
    rerun_last_failure = BoolConfigOption("rerun-last-failure", help="Re-run last failure instead of fuzzing")
    reduce_last_failure = BoolConfigOption("reduce-last-failure", help="Try to shrink the last failure")
    replay_current_traces = BoolConfigOption(
        "replay-current-traces",
        help="Replay traces captured in the default output directory",
    )
    number_of_runs = IntConfigOption("number-of-runs", default=20, help="Number of QCVEngine runs")
    _replay_trace_path: Optional[Path]

    @classmethod
    def setup_config_options(cls, **kwargs) -> None:
        super().setup_config_options(**kwargs)
        if getattr(cls, "_replay_trace_path", None) is None:
            cls._replay_trace_path = cls.add_optional_path_option(
                "replay-trace", help="Run QCV trace from file/directory")

    @cached_property
    def run_implementations_with_tracing(self) -> bool:
        if self.rerun_last_failure:
            return True
        if self._replay_trace_path and self._replay_trace_path.is_file():
            return True  # Also print trace output for single-file traces
        return super().run_implementations_with_tracing

    def _get_vengine_action_args(self, log_dir: Path) -> "list[str]":
        vengine_args = []
        selected_replay_trace_path = None
        if self.rerun_last_failure:
            selected_replay_trace_path = log_dir / "last_failure.S"
        elif self.replay_current_traces:
            selected_replay_trace_path = log_dir
        elif self._replay_trace_path is not None:
            selected_replay_trace_path = self._replay_trace_path

        if selected_replay_trace_path is not None:
            if selected_replay_trace_path.is_file():
                # If we are rerunning a single file, run with verbose output
                vengine_args.extend([f"--trace-file={selected_replay_trace_path}", "--verbose=2"])
            else:
                vengine_args.extend([f"--trace-directory={selected_replay_trace_path}", "--verbose=1"])
            if not self.stop_on_error:
                vengine_args.append("--continue-on-fail")
            if not self.reduce_last_failure:
                vengine_args.extend(["--no-save", "--disable-shrink"])
        else:
            if self.noninteractive:
                vengine_args.extend(["--save-dir", str(log_dir), "--continue-on-fail"])
            vengine_args.extend(["-n", str(self.number_of_runs)])
            vengine_args.append("--verbose=1")
        return vengine_args


class RunTestRIGRegression(RunTestRIGFuzz):
    do_not_add_to_targets = True
    noninteractive = True
    replay_current_traces = False
    number_of_runs = 1

    @property
    def _replay_trace_path(self) -> Path:
        trace_dir = TestRigTraces.get_instance(self).source_dir / "QEMU"  # TODO: run all of them
        if not trace_dir.is_dir():
            self.dependency_error("Missing TestRIG-traces source directory", cheribuild_target=TestRigTraces.target)
        return trace_dir


class _TestRigQEMURV64Base:
    target = "testrig-sail-qemu-cheri-rv64"
    dependencies = (*RunTestRIGBase.dependencies, "sail-cheri-riscv", "qemu")
    # NB: can't use GC here since that implicitly enables ihpm in QCVengine and QEMU does not support mcountinhibit
    # util we have updated to b1675eeb3e6e38b042a23a9647559c9c548c733d.
    verification_archstring = "rv64imafdc_s_xcheri_zicsr_zifencei"

    def get_reference_implementation_command(self, port: int) -> "list[str]":
        assert isinstance(self, RunTestRIGBase)
        result = [
            str(BuildSailCheriRISCV.get_build_dir(self) / "c_emulator/cheri_riscv_rvfi_RV64"),
            "--disable-writable-misa",
            "--mtval-has-illegal-inst-bits",
            "--rvfi-dii",
            str(port),
            "--enable-misaligned",
        ]  # QEMU always enabled misaligned accesses
        if self.run_implementations_with_tracing:
            result.extend(["--trace", "--no-trace=rvfi"])
        else:
            result.append("--no-trace")
        return result

    def get_test_implementation_command(self, port: int) -> "list[str]":
        assert isinstance(self, RunTestRIGBase)
        qemu_cpu_options = [
            "rv64",
            "g=true",
            "c=true",
            "Counters=false",
            "Zifencei=true",
            "s=true",
            "u=true",
            "Zicsr=true",
            "Xcheri=true",
            "Xcheri_v9=true",  # Needs https://github.com/CTSRD-CHERI/qemu/pull/226 to enable ISAv9 semantics
        ]
        result = [
            str(BuildQEMU.get_build_dir(self) / "qemu-system-riscv64cheri"),
            "--rvfi-dii-port",
            str(port),
            "-cpu",
            ",".join(qemu_cpu_options),
            "-bios",
            "none",
        ]
        if self.run_implementations_with_tracing:
            result.extend(["-d", "instr,int"])
        return result


class TestRigSailQemuRV64(_TestRigQEMURV64Base, RunTestRIGFuzz):
    target = "testrig-sail-qemu-cheri-rv64"
    test_cheri_only = BoolConfigOption("test-cheri-only", help="Only run the CHERI-specific passes")

    @property
    def extra_vengine_args(self):
        if self.test_cheri_only:
            return ["--test-include-regex=cap.*", *super().extra_vengine_args]
        else:
            # CClear/FPClear are not implemented in QEMU
            return ["--test-exclude-regex=cclear|fpclear", *super().extra_vengine_args]


# Run traces that were gathered for previous divergences.
class TestRigRegressionTestSailQemuRV64(_TestRigQEMURV64Base, RunTestRIGRegression):
    target = "testrig-regression-sail-qemu-cheri-rv64"

    @property
    def log_dir_name(self) -> str:
        return TestRigSailQemuRV64.target
