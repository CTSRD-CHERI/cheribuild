#
# Copyright (c) 2019-2020 Alex Richardson
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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
import inspect
import os
import shlex
import typing
from abc import ABCMeta, abstractmethod
from pathlib import Path

from .loader import ConfigOptionBase
from .target_info import (AutoVarInit, BasicCompilationTargets, CPUArchitecture, CrossCompileTarget, MipsFloatAbi,
                          TargetInfo)
from ..utils import (cached_property, commandline_to_str, find_free_port, get_compiler_info, is_jenkins_build,
                     SocketAndPort)

if typing.TYPE_CHECKING:  # no-combine
    from .chericonfig import CheriConfig  # no-combine
    from ..projects.project import Project, SimpleProject  # no-combine


class _ClangBasedTargetInfo(TargetInfo, metaclass=ABCMeta):
    def __init__(self, target: "CrossCompileTarget", project: "SimpleProject"):
        super().__init__(target, project)
        self._sdk_root_dir = None  # type: typing.Optional[Path]

    @property
    def _compiler_dir(self) -> Path:
        return self.sdk_root_dir / "bin"

    @property
    def sdk_root_dir(self) -> Path:
        if self._sdk_root_dir is not None:
            return self._sdk_root_dir
        self._sdk_root_dir = self._get_sdk_root_dir_lazy()
        return self._sdk_root_dir

    @abstractmethod
    def _get_sdk_root_dir_lazy(self) -> Path:
        ...

    @property
    def c_compiler(self) -> Path:
        return self._compiler_dir / "clang"

    @property
    def cxx_compiler(self) -> Path:
        return self._compiler_dir / "clang++"

    @property
    def c_preprocessor(self) -> Path:
        return self._compiler_dir / "clang-cpp"

    @property
    def linker(self) -> Path:
        return self._compiler_dir / "ld.lld"

    @property
    def ar(self) -> Path:
        return self._compiler_dir / "llvm-ar"

    @property
    def strip_tool(self) -> Path:
        return self._compiler_dir / "llvm-strip"

    @classmethod
    @abstractmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config: "CheriConfig", *, include_version: bool) -> str:
        ...

    @property
    def target_triple(self) -> str:
        return self.triple_for_target(self.target, self.config, include_version=True)

    @classmethod
    def essential_compiler_and_linker_flags_impl(cls, ti: "_ClangBasedTargetInfo", *,
                                                 target_override: "CrossCompileTarget" = None,
                                                 perform_sanity_checks=True, default_flags_only=False):
        target = target_override if target_override is not None else ti.target
        config = ti.config
        project = ti.project  # type: SimpleProject
        # noinspection PyProtectedMember
        if perform_sanity_checks and not project._setup_called:
            project.fatal("essential_compiler_and_linker_flags should not be called in __init__, use setup()!",
                          fatal_when_pretending=True)
        # When cross compiling we need at least -target=
        result = ["-target", cls.triple_for_target(target, project.config, include_version=True)]
        # And usually also --sysroot
        if project.needs_sysroot:
            result.append("--sysroot=" + str(ti.sysroot_dir))
            if perform_sanity_checks and project.is_nonexistent_or_empty_dir(ti.sysroot_dir):
                project.fatal("Project", project.target, "needs a sysroot, but", ti.sysroot_dir,
                              " is empty or does not exist.")
        result += ["-B" + str(ti._compiler_dir)]

        if not default_flags_only and project.auto_var_init != AutoVarInit.NONE:
            compiler = get_compiler_info(ti.c_compiler)
            valid_clang_version = compiler.is_clang and compiler.version >= (8, 0)
            # We should have at least 8.0.0 unless the user explicitly selected an incompatible clang
            if valid_clang_version:
                result += project.auto_var_init.clang_flags()
            else:
                project.fatal("Requested automatic variable initialization, but don't know how to for", compiler)

        if target.is_mips(include_purecap=True):
            result.append("-integrated-as")
            result.append("-G0")  # no small objects in GOT optimization
            # Floating point ABI:
            if ti.is_baremetal() or ti.is_rtems():
                # The baremetal driver doesn't add -fPIC for CHERI
                if target.is_cheri_purecap([CPUArchitecture.MIPS64]):
                    result.append("-fPIC")
                    # For now use soft-float to avoid compiler crashes
                    result.append(MipsFloatAbi.SOFT.clang_float_flag())
                else:
                    # We don't have a softfloat library baremetal so always compile hard-float
                    result.append(MipsFloatAbi.HARD.clang_float_flag())
                    result.append("-fno-pic")
                    result.append("-mno-abicalls")
            else:
                result.append(config.mips_float_abi.clang_float_flag())

            # CPU flags (currently always BERI):
            if ti.is_cheribsd():
                result.append("-mcpu=beri")
            if target.is_cheri_purecap():
                result.extend(["-mabi=purecap", "-mcpu=beri", "-cheri=" + config.mips_cheri_bits_str])
                if config.subobject_bounds:
                    result.extend(["-Xclang", "-cheri-bounds=" + str(config.subobject_bounds)])
                    if config.subobject_debug:
                        result.extend(["-mllvm", "-cheri-subobject-bounds-clear-swperm=2"])
                if config.cheri_cap_table_abi:
                    result.append("-cheri-cap-table-abi=" + config.cheri_cap_table_abi)
            else:
                assert target.is_mips(include_purecap=False)
                # TODO: should we use -mcpu=cheri128?
                result.extend(["-mabi=n64"])
                if target.is_cheri_hybrid():
                    result.append("-cheri=" + config.mips_cheri_bits_str)
                    result.append("-mcpu=beri")
        elif target.is_riscv(include_purecap=True):
            # Use the insane RISC-V arch string to enable CHERI
            result.append("-march=" + ti.riscv_arch_string)

            if ti.is_baremetal():
                # Baremetal/FreeRTOS only supports softfloat
                result.append("-mabi=" + ti.riscv_softfloat_abi)
            else:
                result.append("-mabi=" + ti.riscv_abi)

            result.append("-mno-relax")  # Linker relaxations are not supported with clang+lld

            if ti.is_baremetal() or ti.is_rtems():
                # Both RTEMS and baremetal FreeRTOS are linked above 0x80000000
                result.append("-mcmodel=medium")
        elif target.is_aarch64(include_purecap=True):
            if target.is_cheri_hybrid():
                result += ["-march=morello", "-mabi=aapcs"]
            elif target.is_cheri_purecap():
                result += ["-march=morello+c64", "-mabi=purecap"]
        else:
            project.warning("Compiler flags might be wong, only native + MIPS checked so far")
        return result

    @property
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        return self.essential_compiler_and_linker_flags_impl(self)

    @property
    def riscv_arch_string(self):
        assert self.target.is_riscv(include_purecap=True)
        # Use the insane RISC-V arch string to enable CHERI
        if self.is_baremetal():
            # Baremetal/FreeRTOS only supports softfloat
            if self.target.cpu_architecture == CPUArchitecture.RISCV32:
                arch_string = "rv32imac"
            else:
                arch_string = "rv64imac"
        else:
            if self.target.cpu_architecture == CPUArchitecture.RISCV32:
                arch_string = "rv32imafdc"
            else:
                arch_string = "rv64imafdc"

        if self.target.is_hybrid_or_purecap_cheri():
            arch_string += "xcheri"
        return arch_string  # XXX: any more extensions needed?

    @property
    def riscv_abi(self):
        assert self.target.is_riscv(include_purecap=True)

        if self.is_baremetal():
            return self.riscv_softfloat_abi()  # Baremetal/FreeRTOS only supports softfloat

        if self.target.is_cheri_purecap():
            if self.target.cpu_architecture == CPUArchitecture.RISCV32:
                # 32-bit double-precision hard-float + purecap
                return "il32pc64d"
            else:
                # 64-bit double-precision hard-float + purecap
                return "l64pc128d"
        else:
            if self.target.cpu_architecture == CPUArchitecture.RISCV32:
                # 32-bit double-precision hard-float
                return "ilp32d"
            else:
                # 64-bit double-precision hard-float
                return "lp64d"

    @property
    def riscv_softfloat_abi(self):
        assert self.target.is_riscv(include_purecap=True)
        if self.target.is_cheri_purecap():
            if self.target.cpu_architecture == CPUArchitecture.RISCV32:
                # 32-bit soft-float
                return "ilp32"
            else:
                # 64-bit soft-float
                return "lp64"
        else:
            if self.target.cpu_architecture == CPUArchitecture.RISCV32:
                # 32-bit soft-float
                return "il32pc64"
            else:
                # 64-bit soft-float
                return "l64pc128"


class FreeBSDTargetInfo(_ClangBasedTargetInfo):
    shortname = "FreeBSD"
    FREEBSD_VERSION = 13

    @property
    def cmake_system_name(self) -> str:
        return "FreeBSD"

    def _get_sdk_root_dir_lazy(self):
        from ..projects.llvm import BuildUpstreamLLVM
        return BuildUpstreamLLVM.get_install_dir(self.project, cross_target=CompilationTargets.NATIVE)

    @property
    def sysroot_dir(self):
        return Path(self.config.sysroot_install_dir, "sysroot-freebsd" + self.target.build_suffix(self.config))

    @classmethod
    def is_freebsd(cls):
        return True

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["upstream-llvm"]

    @classmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config: "CheriConfig", *, include_version: bool):
        common_suffix = "-unknown-freebsd"
        if include_version:
            common_suffix += str(cls.FREEBSD_VERSION)
        # TODO: do we need any special cases here?
        return target.cpu_architecture.value + common_suffix

    @property
    def freebsd_target_arch(self):
        mapping = {
            CPUArchitecture.AARCH64: "aarch64",
            CPUArchitecture.ARM32: "armv7",
            CPUArchitecture.I386: "i386",
            CPUArchitecture.MIPS64: self.config.mips_float_abi.freebsd_target_arch(),
            CPUArchitecture.RISCV64: "riscv64",
            CPUArchitecture.X86_64: "amd64",
            }
        return mapping[self.target.cpu_architecture]

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["freebsd"]

    @property
    def pkgconfig_dirs(self) -> str:
        assert self.project.needs_sysroot, "Should not call this for projects that build without a sysroot"
        return str(self.sysroot_dir / "lib/pkgconfig") + ":" + str(
            self.sysroot_install_prefix_absolute / "lib/pkgconfig")

    @property
    def sysroot_install_prefix_relative(self) -> Path:
        return Path("usr/local")

    @property
    def cmake_prefix_paths(self) -> list:
        return [self.sysroot_install_prefix_absolute, self.sysroot_install_prefix_absolute / "libcheri/cmake"]

    def _get_rootfs_project(self, xtarget: "CrossCompileTarget") -> "Project":
        from ..projects.cross.cheribsd import BuildFreeBSD
        return BuildFreeBSD.get_instance(self.project, cross_target=xtarget)


class CheriBSDTargetInfo(FreeBSDTargetInfo):
    shortname = "CheriBSD"
    FREEBSD_VERSION = 13

    def _get_sdk_root_dir_lazy(self):
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self):
        if is_jenkins_build():
            # TODO: currently we need this to be unprefixed since that is what the archives created by jenkins look like
            return self.config.sysroot_install_dir / "sysroot"
        return self.get_cheribsd_sysroot_path()

    def get_cheribsd_sysroot_path(self) -> Path:
        """
        :return: The sysroot path
        """
        return self.config.sysroot_install_dir / ("sysroot" + self.target.build_suffix(self.config))

    @classmethod
    def is_cheribsd(cls):
        return True

    def _get_mfs_root_kernel(self, use_benchmark_kernel: bool) -> Path:
        assert self.is_cheribsd(), "Other cases not handled yet"
        from ..projects.cross.cheribsd import BuildCheriBsdMfsKernel
        if use_benchmark_kernel:
            return BuildCheriBsdMfsKernel.get_installed_benchmark_kernel_path(self.project)
        else:
            return BuildCheriBsdMfsKernel.get_installed_kernel_path(self.project)

    def _get_mfs_kernel_xtarget(self):
        kernel_xtarget = self.target
        if self.is_cheribsd():
            # TODO: allow using non-CHERI kernels? Or the purecap kernel?
            if kernel_xtarget.is_mips(include_purecap=True):
                # Always use CHERI hybrid kernel
                kernel_xtarget = CompilationTargets.CHERIBSD_MIPS_HYBRID
            elif kernel_xtarget.is_riscv(include_purecap=True):
                kernel_xtarget = CompilationTargets.CHERIBSD_RISCV_HYBRID
        return kernel_xtarget

    def run_cheribsd_test_script(self, script_name, *script_args, kernel_path=None, disk_image_path=None,
                                 mount_builddir=True, mount_sourcedir=False, mount_sysroot=False,
                                 mount_installdir=False, use_benchmark_kernel_by_default=False):
        assert self.is_cheribsd(), "Only CheriBSD targets supported right now"
        if typing.TYPE_CHECKING:
            assert isinstance(self.project, Project)
        # mount_sysroot may be needed for projects such as QtWebkit where the minimal image doesn't contain all the
        # necessary libraries
        xtarget = self.target
        if xtarget.cpu_architecture not in (CPUArchitecture.MIPS64, CPUArchitecture.RISCV64,
                                            CPUArchitecture.X86_64, CPUArchitecture.AARCH64):
            self.project.warning("CheriBSD test scripts currently only work for MIPS, RISC-V, AArch64 and x86-64")
            return
        if kernel_path is None and "--kernel" not in self.config.test_extra_args:
            # Use the benchmark kernel by default if the parameter is set and the user didn't pass
            # --no-use-minimal-benchmark-kernel on the command line or in the config JSON
            use_benchmark_kernel_value = self.config.use_minimal_benchmark_kernel  # Load the value first to ensure
            # that it has been loaded
            use_benchmark_config_option = inspect.getattr_static(self.config, "use_minimal_benchmark_kernel")
            assert isinstance(use_benchmark_config_option, ConfigOptionBase)
            want_benchmark_kernel = use_benchmark_kernel_value or (
                    use_benchmark_kernel_by_default and use_benchmark_config_option.is_default_value)
            kernel_path = self._get_mfs_root_kernel(use_benchmark_kernel=want_benchmark_kernel)
            if not kernel_path.exists() and is_jenkins_build():
                jenkins_kernel_path = self.config.cheribsd_image_root / "kernel.xz"
                if jenkins_kernel_path.exists():
                    kernel_path = jenkins_kernel_path
                else:
                    self.project.fatal("Could not find kernel image", kernel_path, "and jenkins path",
                                       jenkins_kernel_path, "is also missing")
            if kernel_path is None or not kernel_path.exists():
                self.project.fatal("Could not find kernel image", kernel_path)
        script = self.project.get_test_script_path(script_name)
        if not script.exists():
            self.project.fatal("Could not find test script", script)

        cmd = [script, "--ssh-key", self.config.test_ssh_key, "--architecture", xtarget.generic_suffix]
        if "--kernel" not in self.config.test_extra_args:
            cmd.extend(["--kernel", kernel_path])
        if "--qemu-cmd" not in self.config.test_extra_args:
            qemu_path = None
            if xtarget.is_riscv(include_purecap=True) or xtarget.is_mips(include_purecap=True):
                from ..projects.build_qemu import BuildQEMU
                qemu_path = BuildQEMU.qemu_cheri_binary(self.project)
                if not qemu_path.exists():
                    self.project.fatal("QEMU binary", qemu_path, "doesn't exist")
            else:
                from ..qemu_utils import QemuOptions
                binary_name = "qemu-system-" + QemuOptions(xtarget).qemu_arch_sufffix
                if (self.config.qemu_bindir / binary_name).is_file():
                    qemu_path = self.config.qemu_bindir / binary_name
            if qemu_path is not None:
                cmd.extend(["--qemu-cmd", qemu_path])
        if mount_builddir and self.project.build_dir and "--build-dir" not in self.config.test_extra_args:
            cmd.extend(["--build-dir", self.project.build_dir])
        if mount_sourcedir and self.project.source_dir and "--source-dir" not in self.config.test_extra_args:
            cmd.extend(["--source-dir", self.project.source_dir])
        if mount_sysroot and "--sysroot-dir" not in self.config.test_extra_args:
            cmd.extend(["--sysroot-dir", self.sysroot_dir])
        if mount_installdir:
            if "--install-destdir" not in self.config.test_extra_args:
                cmd.extend(["--install-destdir", self.project.destdir])
            if "--install-prefix" not in self.config.test_extra_args:
                cmd.extend(["--install-prefix", self.project.install_prefix])
        if disk_image_path and "--disk-image" not in self.config.test_extra_args:
            cmd.extend(["--disk-image", disk_image_path])
        if self.config.tests_interact:
            cmd.append("--interact")
        if self.config.tests_env_only:
            cmd.append("--test-environment-only")
        if self.config.trap_on_unrepresentable:
            cmd.append("--trap-on-unrepresentable")
        if self.config.test_ld_preload:
            cmd.append("--test-ld-preload=" + str(self.config.test_ld_preload))
            if xtarget.is_cheri_purecap():
                cmd.append("--test-ld-preload-variable=LD_CHERI_PRELOAD")
            else:
                cmd.append("--test-ld-preload-variable=LD_PRELOAD")

        cmd += list(script_args)
        if self.config.test_extra_args:
            cmd.extend(map(str, self.config.test_extra_args))
        self.project.run_cmd(cmd)

    def run_fpga_benchmark(self, benchmarks_dir: Path, *, output_file: str = None, benchmark_script: str = None,
                           benchmark_script_args: list = None, extra_runbench_args: list = None):
        assert benchmarks_dir is not None
        assert output_file is not None, "output_file must be set to a valid value"
        if typing.TYPE_CHECKING:
            assert isinstance(self.project, Project)
        self.project.strip_elf_files(benchmarks_dir)
        for root, dirnames, filenames in os.walk(str(benchmarks_dir)):
            for filename in filenames:
                file = Path(root, filename)
                if file.suffix == ".dump":
                    # TODO: make this an error since we should have deleted them
                    self.project.warning("Will copy a .dump file to the FPGA:", file)

        runbench_args = [benchmarks_dir, "--target=" + self.config.benchmark_ssh_host, "--out-path=" + output_file]

        from ..projects.cherisim import BuildCheriSim, BuildBeriCtl
        sim_project = BuildCheriSim.get_instance(self.project, cross_target=CompilationTargets.NATIVE)
        cherilibs_dir = Path(sim_project.source_dir, "cherilibs")
        cheri_dir = Path(sim_project.source_dir, "cheri")
        if not cheri_dir.exists() or not cherilibs_dir.exists():
            self.project.fatal("cheri-cpu repository missing. Run `cheribuild.py berictl` or `git clone {} {}`".format(
                sim_project.repository.url, sim_project.source_dir))

        qemu_ssh_socket = None  # type: typing.Optional[SocketAndPort]

        if self.config.benchmark_with_qemu:
            from ..projects.build_qemu import BuildQEMU
            qemu_path = BuildQEMU.qemu_cheri_binary(self.project)
            qemu_ssh_socket = find_free_port()
            if not qemu_path.exists():
                self.project.fatal("QEMU binary", qemu_path, "doesn't exist")
            basic_args = ["--use-qemu-instead-of-fpga",
                          "--qemu-path=" + str(qemu_path),
                          "--qemu-ssh-port=" + str(qemu_ssh_socket.port)]
        else:
            basic_args = ["--berictl=" + str(
                BuildBeriCtl.get_build_dir(self.project, cross_target=CompilationTargets.NATIVE) / "berictl")]

        if self.config.test_ssh_key.with_suffix("").exists():
            basic_args.extend(["--ssh-key", str(self.config.test_ssh_key.with_suffix(""))])

        if self.config.benchmark_ld_preload:
            runbench_args.append("--extra-input-files=" + str(self.config.benchmark_ld_preload))
            env_var = "LD_CHERI_PRELOAD" if self.target.is_cheri_hybrid() else "LD_PRELOAD"
            pre_cmd = "export {}={};".format(env_var,
                                             shlex.quote("/tmp/benchdir/" + self.config.benchmark_ld_preload.name))
            runbench_args.append("--pre-command=" + pre_cmd)
        if self.config.benchmark_fpga_extra_args:
            basic_args.extend(self.config.benchmark_fpga_extra_args)
        if self.config.benchmark_extra_args:
            runbench_args.extend(self.config.benchmark_extra_args)
        if self.config.tests_interact:
            runbench_args.append("--interact")

        from ..projects.cross.cheribsd import BuildCheriBsdMfsKernel
        if self.config.benchmark_with_qemu:
            # When benchmarking with QEMU we always spawn a new instance
            kernel_image = self._get_mfs_root_kernel(use_benchmark_kernel=not self.config.benchmark_with_debug_kernel)
            basic_args.append("--kernel-img=" + str(kernel_image))
        elif self.config.benchmark_clean_boot:
            # use a bitfile from jenkins. TODO: add option for overriding
            assert self.target.is_mips(include_purecap=True)
            basic_args.append("--jenkins-bitfile=cheri128")
            mfs_kernel = BuildCheriBsdMfsKernel.get_instance_for_cross_target(self._get_mfs_kernel_xtarget(),
                                                                              self.config, caller=self.project)
            if self.config.benchmark_with_debug_kernel:
                kernel_config = mfs_kernel.fpga_kernconf
            else:
                kernel_config = mfs_kernel.fpga_kernconf + "_BENCHMARK"
            basic_args.append(
                "--kernel-img=" + str(mfs_kernel.installed_kernel_for_config(self.project, kernel_config)))
        else:
            runbench_args.append("--skip-boot")
        if benchmark_script:
            runbench_args.append("--script-name=" + benchmark_script)
        if benchmark_script_args:
            runbench_args.append("--script-args=" + commandline_to_str(benchmark_script_args))
        if extra_runbench_args:
            runbench_args.extend(extra_runbench_args)

        cheribuild_path = Path(__file__).absolute().parent.parent.parent
        beri_fpga_bsd_boot_script = """
set +x
source "{cheri_dir}/setup.sh"
set -x
export PATH="$PATH:{cherilibs_dir}/tools:{cherilibs_dir}/tools/debug"
exec {cheribuild_path}/beri-fpga-bsd-boot.py {basic_args} -vvvvv runbench {runbench_args}
            """.format(cheri_dir=cheri_dir, cherilibs_dir=cherilibs_dir,
                       runbench_args=commandline_to_str(runbench_args),
                       basic_args=commandline_to_str(basic_args), cheribuild_path=cheribuild_path)
        if self.config.benchmark_with_qemu:
            # Free the port that we reserved for QEMU before starting beri-fpga-bsd-boot.py
            if qemu_ssh_socket is not None:
                qemu_ssh_socket.socket.close()
            self.project.run_cmd(
                [cheribuild_path / "beri-fpga-bsd-boot.py"] + basic_args + ["-vvvvv", "runbench"] + runbench_args)
        else:
            self.project.run_shell_script(beri_fpga_bsd_boot_script, shell="bash")  # the setup script needs bash not sh

    @classmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config, *, include_version):
        if target.is_cheri_purecap():
            # anything over 10 should use libc++ by default
            if target.is_mips(include_purecap=True):
                return "mips64-unknown-freebsd{}".format(cls.FREEBSD_VERSION if include_version else "")
            elif target.is_riscv(include_purecap=True):
                return "riscv64-unknown-freebsd{}".format(cls.FREEBSD_VERSION if include_version else "")
            else:
                assert False, "Unsuported purecap target" + str(cls)
        return super().triple_for_target(target, config, include_version=include_version)

    @property
    def freebsd_target_arch(self):
        base = super().freebsd_target_arch
        if self.target.is_cheri_purecap():
            purecap_suffix = "c"
            if self.target.is_mips(include_purecap=True):
                purecap_suffix += self.config.mips_cheri_bits_str
        else:
            purecap_suffix = ""
        return base + purecap_suffix

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm-native"]

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["cheribsd"]  # Pick the matching sysroot (-purecap for purecap, -hybrid for hybrid etc.)

    @property
    def sysroot_install_prefix_relative(self) -> Path:
        return Path("usr/local", self.install_prefix_dirname)

    @property
    def _sysroot_libdir(self):
        # For purecap we can unconditionally use libcheri since it is either a real directory (hybrid sysroot) or
        # a symlink to lib (since https://github.com/CTSRD-CHERI/cheribsd/pull/548).
        if self.target.is_cheri_purecap():
            return "libcheri"
        return "lib"

    @property
    def pkgconfig_dirs(self) -> str:
        assert self.project.needs_sysroot, "Should not call this for projects that build without a sysroot"
        return str(self.sysroot_dir / "usr" / self._sysroot_libdir / "pkgconfig") + ":" + str(
            self.sysroot_dir / self._sysroot_libdir / "pkgconfig") + ":" + str(
            self.sysroot_install_prefix_absolute / "lib/pkgconfig")

    def _get_rootfs_project(self, xtarget: "CrossCompileTarget") -> "Project":
        from ..projects.cross.cheribsd import BuildCHERIBSD
        return BuildCHERIBSD.get_instance(self.project, cross_target=xtarget)


class CheriBSDMorelloTargetInfo(CheriBSDTargetInfo):
    shortname = "CheriBSD-Morello"

    def _get_sdk_root_dir_lazy(self):
        return self.config.morello_sdk_dir

    @classmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config, *, include_version):
        if target.is_hybrid_or_purecap_cheri():
            assert target.is_aarch64(include_purecap=True), "AArch64 is the only CHERI target supported " \
                                                            "with the Morello toolchain"
            return "aarch64-unknown-freebsd{}".format(cls.FREEBSD_VERSION if include_version else "")
        return super().triple_for_target(target, config, include_version=include_version)

    def get_cheribsd_sysroot_path(self) -> Path:
        """
        :return: The sysroot path
        """
        return self.config.morello_sdk_dir / ("sysroot" + self.target.build_suffix(self.config))

    @property
    def linker(self) -> Path:
        return self._compiler_dir / "ld.lld"

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig"):
        return ["morello-llvm"]

    @property
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        result = super().essential_compiler_and_linker_flags
        if self.target.is_cheri_purecap([CPUArchitecture.AARCH64]):
            # emulated TLS is currently required for purecap, but breaks hybrid
            result.append("-femulated-tls")
        return result

    @property
    def must_link_statically(self):
        return True  # dynamic linking is still experimental


# FIXME: This is completely wrong since cherios is not cheribsd, but should work for now:
class CheriOSTargetInfo(CheriBSDTargetInfo):
    shortname = "CheriOS"
    FREEBSD_VERSION = 0

    def _get_rootfs_project(self, xtarget: "CrossCompileTarget") -> "Project":
        raise ValueError("Should not be called")

    def _get_sdk_root_dir_lazy(self):
        from ..projects.llvm import BuildCheriOSLLVM
        return BuildCheriOSLLVM.get_install_dir(self.project, cross_target=CompilationTargets.NATIVE)

    @property
    def sysroot_dir(self):
        return self.config.sysroot_install_dir / "sysroot"

    @classmethod
    def is_cheribsd(cls):
        return False

    @classmethod
    def is_freebsd(cls):
        return False

    @classmethod
    def is_baremetal(cls):
        return True

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["cherios-llvm"]

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        # Otherwise pick the matching sysroot
        return ["cherios"]

    @property
    def pkgconfig_dirs(self) -> str:
        assert self.project.needs_sysroot, "Should not call this for projects that build without a sysroot"
        return ""


class RTEMSTargetInfo(_ClangBasedTargetInfo):
    shortname = "RTEMS"
    RTEMS_VERSION = 5

    @property
    def cmake_system_name(self) -> str:
        return "rtems" + str(self.RTEMS_VERSION)

    @classmethod
    def is_rtems(cls):
        return True

    @classmethod
    def is_newlib(cls):
        return True

    @classmethod
    def triple_for_target(cls, target, config, *, include_version: bool) -> str:
        assert target.is_riscv(include_purecap=True)
        result = "riscv64-unknown-rtems"
        if include_version:
            result += str(cls.RTEMS_VERSION)
        return result

    @property
    def sysroot_dir(self):
        # Install to target triple as RTEMS' LLVM/Clang Driver expects
        return self.config.sysroot_install_dir / ("sysroot-" + self.target.generic_suffix) / self.target_triple

    def _get_sdk_root_dir_lazy(self) -> Path:
        return self.config.cheri_sdk_dir

    @property
    def _compiler_dir(self) -> Path:
        return self.config.cheri_sdk_bindir

    @property
    def must_link_statically(self):
        return True  # only static linking works

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm-native"]

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        if target.is_riscv(include_purecap=True):
            return ["newlib", "compiler-rt-builtins", "rtems"]
        else:
            assert False, "No support for building RTEMS for non RISC-V targets yet"


class NewlibBaremetalTargetInfo(_ClangBasedTargetInfo):
    shortname = "Newlib"

    @property
    def cmake_system_name(self) -> str:
        return "Generic"  # CMake requires the value to be set to "Generic" for baremetal targets

    def _get_sdk_root_dir_lazy(self) -> Path:
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self) -> Path:
        # Install to mips/cheri128 directory
        if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            suffix = "cheri" + self.config.mips_cheri_bits_str
        else:
            suffix = self.target.generic_suffix
        return self.config.sysroot_install_dir / "baremetal" / suffix / self.target_triple

    @property
    def must_link_statically(self):
        return True  # only static linking works

    @property
    def _compiler_dir(self) -> Path:
        # TODO: BuildUpstreamLLVM.install_dir?
        return self.config.cheri_sdk_bindir

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm-native"]  # upstream-llvm??

    @classmethod
    def triple_for_target(cls, target, config, include_version: bool) -> str:
        if target.is_mips(include_purecap=True):
            if target.is_cheri_purecap():
                return "mips64c{}-qemu-elf-purecap".format(config.mips_cheri_bits)
            return "mips64-qemu-elf"
        if target.is_riscv(include_purecap=True):
            return target.cpu_architecture.value + "-unknown-elf"
        assert False, "Other baremetal cases have not been tested yet!"

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["newlib", "compiler-rt-builtins"]

    def default_initial_compile_flags(self) -> typing.List[str]:
        # Currently we need these flags to build anything against newlib baremetal
        if self.target.is_mips(include_purecap=True):
            return [
                "-D_GNU_SOURCE=1",  # needed for the locale functions
                "-D_POSIX_TIMERS=1", "-D_POSIX_MONOTONIC_CLOCK=1",  # pretend that we have a monotonic clock
                ]
        else:
            return []

    @property
    def additional_executable_link_flags(self):
        if self.target.is_mips(include_purecap=True):
            """Additional linker flags that need to be passed when building an executable (e.g. custom linker script)"""
            return ["-Wl,-T,qemu-malta.ld"]
        return super().additional_executable_link_flags

    @classmethod
    def is_baremetal(cls):
        return True

    @classmethod
    def is_newlib(cls):
        return True

    def _get_rootfs_project(self, xtarget: CrossCompileTarget) -> "Project":
        from ..projects.cross.newlib import BuildNewlib
        return BuildNewlib.get_instance(self.project, cross_target=xtarget)


class MorelloBaremetalTargetInfo(_ClangBasedTargetInfo):
    shortname = "Morello-Baremetal"

    @property
    def cmake_system_name(self) -> str:
        return "Generic"  # CMake requires the value to be set to "Generic" for baremetal targets

    def _get_sdk_root_dir_lazy(self) -> Path:
        return self.config.morello_sdk_dir

    @property
    def sysroot_dir(self) -> Path:
        raise ValueError("Should not have a valid sysroot")

    @property
    def must_link_statically(self):
        return True  # only static linking works

    @property
    def _compiler_dir(self) -> Path:
        return self.config.morello_sdk_dir / "bin"

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["morello-llvm"]

    @classmethod
    def triple_for_target(cls, target, config, include_version: bool) -> str:
        if target.cpu_architecture == CPUArchitecture.ARM32:
            return "arm-none-eabi"
        assert target.is_aarch64(include_purecap=True)
        if target.is_cheri_hybrid():
            return "aarch64-unknown-elf"
        assert False, "Other baremetal cases have not been tested yet!"

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return []

    @property
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        if (self.target.cpu_architecture == CPUArchitecture.ARM32 or
                self.target.is_cheri_hybrid([CPUArchitecture.AARCH64])):
            return super().essential_compiler_and_linker_flags
        assert False, "Other baremetal cases have not been tested yet!"

    @classmethod
    def is_baremetal(cls):
        return True


class ArmNoneEabiGccTargetInfo(TargetInfo):
    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return []  # TODO: add a target to download the tarball and extract it

    @property
    def target_triple(self) -> str:
        raise ValueError("Should not be used directly")

    @property
    def sysroot_dir(self) -> Path:
        raise ValueError("Should not be used directly")

    @property
    def cmake_system_name(self) -> str:
        return "Generic"  # CMake requires the value to be set to "Generic" for baremetal targets

    @cached_property
    def bindir(self) -> Path:
        result = Path(self.project.config.arm_none_eabi_toolchain_prefix).parent
        assert result is not None
        return result

    @cached_property
    def binary_prefix(self) -> str:
        result = Path(self.project.config.arm_none_eabi_toolchain_prefix).name
        assert result is not None
        return result

    @property
    def sdk_root_dir(self) -> Path:
        return self.bindir.parent

    @property
    def c_compiler(self) -> Path:
        return self.bindir / (self.binary_prefix + "gcc")

    @property
    def cxx_compiler(self) -> Path:
        return self.bindir / (self.binary_prefix + "g++")

    @property
    def c_preprocessor(self) -> Path:
        return self.bindir / (self.binary_prefix + "cpp")

    @property
    def linker(self) -> Path:
        return self.bindir / (self.binary_prefix + "ld.bfd")

    @property
    def ar(self) -> Path:
        return self.bindir / (self.binary_prefix + "ar")

    @property
    def strip_tool(self) -> Path:
        return self.bindir / (self.binary_prefix + "strip")

    @property
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        # This version of GCC should work without any additional flags
        return []

    @classmethod
    def is_baremetal(cls):
        return False

    def must_link_statically(self):
        return True


class CompilationTargets(BasicCompilationTargets):
    CHERIBSD_MIPS_NO_CHERI = CrossCompileTarget("mips64", CPUArchitecture.MIPS64, CheriBSDTargetInfo)
    CHERIBSD_MIPS_HYBRID = CrossCompileTarget("mips64-hybrid", CPUArchitecture.MIPS64, CheriBSDTargetInfo,
                                              is_cheri_hybrid=True, check_conflict_with=CHERIBSD_MIPS_NO_CHERI,
                                              non_cheri_target=CHERIBSD_MIPS_NO_CHERI)
    CHERIBSD_MIPS_PURECAP = CrossCompileTarget("mips64-purecap", CPUArchitecture.MIPS64, CheriBSDTargetInfo,
                                               is_cheri_purecap=True, check_conflict_with=CHERIBSD_MIPS_NO_CHERI,
                                               hybrid_target=CHERIBSD_MIPS_HYBRID)

    CHERIBSD_RISCV_NO_CHERI = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, CheriBSDTargetInfo)
    CHERIBSD_RISCV_HYBRID = CrossCompileTarget("riscv64-hybrid", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                                               is_cheri_hybrid=True, non_cheri_target=CHERIBSD_RISCV_NO_CHERI)
    CHERIBSD_RISCV_PURECAP = CrossCompileTarget("riscv64-purecap", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                                                is_cheri_purecap=True, hybrid_target=CHERIBSD_RISCV_HYBRID)
    CHERIBSD_AARCH64 = CrossCompileTarget("aarch64", CPUArchitecture.AARCH64, CheriBSDTargetInfo)
    # XXX: Do we want a morello-nocheri variant that uses the morello compiler for AArch64 instead of CHERI LLVM?
    CHERIBSD_MORELLO_NO_CHERI = CrossCompileTarget("morello-aarch64", CPUArchitecture.AARCH64,
                                                   CheriBSDMorelloTargetInfo)
    CHERIBSD_MORELLO_HYBRID = CrossCompileTarget("morello-hybrid", CPUArchitecture.AARCH64,
                                                 CheriBSDMorelloTargetInfo, is_cheri_hybrid=True,
                                                 check_conflict_with=CHERIBSD_MORELLO_NO_CHERI,
                                                 non_cheri_target=CHERIBSD_MORELLO_NO_CHERI)
    CHERIBSD_MORELLO_PURECAP = CrossCompileTarget("morello-purecap", CPUArchitecture.AARCH64,
                                                  CheriBSDMorelloTargetInfo, is_cheri_purecap=True,
                                                  check_conflict_with=CHERIBSD_MORELLO_HYBRID,
                                                  hybrid_target=CHERIBSD_MORELLO_HYBRID)
    CHERIBSD_X86_64 = CrossCompileTarget("amd64", CPUArchitecture.X86_64, CheriBSDTargetInfo)

    CHERIOS_MIPS_PURECAP = CrossCompileTarget("mips", CPUArchitecture.MIPS64, CheriOSTargetInfo, is_cheri_purecap=True)

    # Baremetal targets
    BAREMETAL_NEWLIB_MIPS64 = CrossCompileTarget("baremetal-mips", CPUArchitecture.MIPS64, NewlibBaremetalTargetInfo)
    BAREMETAL_NEWLIB_MIPS64_PURECAP = CrossCompileTarget("baremetal-mips64-purecap", CPUArchitecture.MIPS64,
                                                         NewlibBaremetalTargetInfo, is_cheri_purecap=True,
                                                         non_cheri_target=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV32 = CrossCompileTarget("baremetal-riscv32", CPUArchitecture.RISCV32,
                                                  NewlibBaremetalTargetInfo,
                                                  check_conflict_with=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV64 = CrossCompileTarget("baremetal-riscv64", CPUArchitecture.RISCV64,
                                                  NewlibBaremetalTargetInfo,
                                                  check_conflict_with=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV32_HYBRID = CrossCompileTarget("baremetal-riscv32-hybrid", CPUArchitecture.RISCV32,
                                                         NewlibBaremetalTargetInfo, is_cheri_hybrid=True,
                                                         non_cheri_target=BAREMETAL_NEWLIB_RISCV32)
    BAREMETAL_NEWLIB_RISCV64_HYBRID = CrossCompileTarget("baremetal-riscv64-hybrid", CPUArchitecture.RISCV64,
                                                         NewlibBaremetalTargetInfo, is_cheri_hybrid=True,
                                                         non_cheri_target=BAREMETAL_NEWLIB_RISCV64)
    BAREMETAL_NEWLIB_RISCV32_PURECAP = CrossCompileTarget("baremetal-riscv32-purecap", CPUArchitecture.RISCV32,
                                                          NewlibBaremetalTargetInfo, is_cheri_purecap=True,
                                                          hybrid_target=BAREMETAL_NEWLIB_RISCV32_HYBRID)
    BAREMETAL_NEWLIB_RISCV64_PURECAP = CrossCompileTarget("baremetal-riscv64-purecap", CPUArchitecture.RISCV64,
                                                          NewlibBaremetalTargetInfo, is_cheri_purecap=True,
                                                          hybrid_target=BAREMETAL_NEWLIB_RISCV64_HYBRID)

    MORELLO_BAREMETAL_HYBRID = CrossCompileTarget("morello-baremetal", CPUArchitecture.AARCH64,
                                                  MorelloBaremetalTargetInfo, is_cheri_hybrid=True,
                                                  is_cheri_purecap=False)
    ARM_NONE_EABI = CrossCompileTarget("arm-none-eabi", CPUArchitecture.ARM32, ArmNoneEabiGccTargetInfo,
                                       is_cheri_hybrid=False, is_cheri_purecap=False)  # For 32-bit firmrware
    # FreeBSD targets
    FREEBSD_AARCH64 = CrossCompileTarget("aarch64", CPUArchitecture.AARCH64, FreeBSDTargetInfo)
    FREEBSD_AMD64 = CrossCompileTarget("amd64", CPUArchitecture.X86_64, FreeBSDTargetInfo)
    FREEBSD_I386 = CrossCompileTarget("i386", CPUArchitecture.I386, FreeBSDTargetInfo)
    FREEBSD_MIPS64 = CrossCompileTarget("mips64", CPUArchitecture.MIPS64, FreeBSDTargetInfo)
    FREEBSD_RISCV64 = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, FreeBSDTargetInfo)
    ALL_SUPPORTED_FREEBSD_TARGETS = [FREEBSD_AARCH64, FREEBSD_AMD64, FREEBSD_I386, FREEBSD_MIPS64, FREEBSD_RISCV64]

    # RTEMS targets
    RTEMS_RISCV64 = CrossCompileTarget("rtems-riscv64", CPUArchitecture.RISCV64, RTEMSTargetInfo)
    RTEMS_RISCV64_PURECAP = CrossCompileTarget("rtems-riscv64-purecap", CPUArchitecture.RISCV64, RTEMSTargetInfo,
                                               is_cheri_purecap=True, non_cheri_target=RTEMS_RISCV64)

    ALL_CHERIBSD_MIPS_AND_RISCV_TARGETS = [CHERIBSD_RISCV_PURECAP, CHERIBSD_RISCV_HYBRID, CHERIBSD_RISCV_NO_CHERI,
                                           CHERIBSD_MIPS_PURECAP, CHERIBSD_MIPS_HYBRID, CHERIBSD_MIPS_NO_CHERI]
    ALL_CHERIBSD_NON_MORELLO_TARGETS = ALL_CHERIBSD_MIPS_AND_RISCV_TARGETS + [CHERIBSD_AARCH64, CHERIBSD_X86_64]
    ALL_CHERIBSD_MORELLO_TARGETS = [CHERIBSD_MORELLO_PURECAP, CHERIBSD_MORELLO_HYBRID]
    ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS = ALL_CHERIBSD_NON_MORELLO_TARGETS + [BasicCompilationTargets.NATIVE]

    ALL_CHERIBSD_NON_CHERI_TARGETS = [CHERIBSD_MIPS_NO_CHERI, CHERIBSD_RISCV_NO_CHERI, CHERIBSD_AARCH64,
                                      CHERIBSD_X86_64]
    # Same as above, but the default is purecap RISC-V
    FETT_DEFAULT_ARCHITECTURE = CHERIBSD_RISCV_PURECAP
    FETT_SUPPORTED_ARCHITECTURES = [CHERIBSD_RISCV_PURECAP, CHERIBSD_RISCV_HYBRID, CHERIBSD_RISCV_NO_CHERI,
                                    CHERIBSD_MIPS_HYBRID, CHERIBSD_MIPS_NO_CHERI, CHERIBSD_MIPS_PURECAP]

    ALL_SUPPORTED_BAREMETAL_TARGETS = [BAREMETAL_NEWLIB_MIPS64, BAREMETAL_NEWLIB_MIPS64_PURECAP,
                                       BAREMETAL_NEWLIB_RISCV64, BAREMETAL_NEWLIB_RISCV64_PURECAP,
                                       BAREMETAL_NEWLIB_RISCV32, BAREMETAL_NEWLIB_RISCV32_PURECAP]
    ALL_SUPPORTED_RTEMS_TARGETS = [RTEMS_RISCV64, RTEMS_RISCV64_PURECAP]
    ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS = \
        ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS + ALL_SUPPORTED_BAREMETAL_TARGETS
