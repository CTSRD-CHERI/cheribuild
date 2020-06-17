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
from ..utils import commandline_to_str, find_free_port, get_compiler_info, is_jenkins_build, SocketAndPort

if typing.TYPE_CHECKING:
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
    def essential_compiler_and_linker_flags(self) -> typing.List[str]:
        # noinspection PyProtectedMember
        if not self.project._setup_called:
            self.project.fatal("essential_compiler_and_linker_flags should not be called in __init__, use setup()!",
                               fatal_when_pretending=True)
        # However, when cross compiling we need at least -target=
        result = ["-target", self.target_triple, "-pipe"]
        # And usually also --sysroot
        if self.project.needs_sysroot:
            result.append("--sysroot=" + str(self.sysroot_dir))
            if self.project.is_nonexistent_or_empty_dir(self.sysroot_dir):
                self.project.fatal("Project", self.project.target, "needs a sysroot, but", self.sysroot_dir,
                                   " is empty or does not exist.")
        result += ["-B" + str(self._compiler_dir)]

        if self.project.auto_var_init != AutoVarInit.NONE:
            compiler = get_compiler_info(self.c_compiler)
            valid_clang_version = compiler.is_clang and compiler.version >= (8, 0)
            # We should have at least 8.0.0 unless the user explicitly selected an incompatible clang
            if valid_clang_version:
                result += self.project.auto_var_init.clang_flags()
            else:
                self.project.fatal("Requested automatic variable initialization, but don't know how to for", compiler)

        if self.target.is_mips(include_purecap=True):
            result.append("-integrated-as")
            result.append("-G0")  # no small objects in GOT optimization
            # Floating point ABI:
            if self.is_baremetal() or self.is_rtems():
                # The baremetal driver doesn't add -fPIC for CHERI
                if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
                    result.append("-fPIC")
                    # For now use soft-float to avoid compiler crashes
                    result.append(MipsFloatAbi.SOFT.clang_float_flag())
                else:
                    # We don't have a softfloat library baremetal so always compile hard-float
                    result.append(MipsFloatAbi.HARD.clang_float_flag())
                    result.append("-fno-pic")
                    result.append("-mno-abicalls")
            else:
                result.append(self.config.mips_float_abi.clang_float_flag())

            # CPU flags (currently always BERI):
            if self.is_cheribsd():
                result.append("-mcpu=beri")
            if self.target.is_cheri_purecap():
                result.extend(["-mabi=purecap", "-mcpu=beri", "-cheri=" + self.config.mips_cheri_bits_str])
                if self.config.subobject_bounds:
                    result.extend(["-Xclang", "-cheri-bounds=" + str(self.config.subobject_bounds)])
                    if self.config.subobject_debug:
                        result.extend(["-mllvm", "-cheri-subobject-bounds-clear-swperm=2"])
                if self.config.cheri_cap_table_abi:
                    result.append("-cheri-cap-table-abi=" + self.config.cheri_cap_table_abi)
            else:
                assert self.target.is_mips(include_purecap=False)
                # TODO: should we use -mcpu=cheri128?
                result.extend(["-mabi=n64"])
                if self.target.is_cheri_hybrid():
                    result.append("-cheri=" + self.config.mips_cheri_bits_str)
                    result.append("-mcpu=beri")
        elif self.target.is_riscv(include_purecap=True):
            assert self.target.cpu_architecture == CPUArchitecture.RISCV64
            # Use the insane RISC-V arch string to enable CHERI
            result.append("-march=" + self.riscv_arch_string)

            if self.is_baremetal():
                # Baremetal/FreeRTOS only supports softfloat
                result.append("-mabi=" + self.riscv_softfloat_abi)
            else:
                result.append("-mabi=" + self.riscv_abi)

            result.append("-mno-relax")  # Linker relaxations are not supported with clang+lld

            if self.is_baremetal() or self.is_rtems():
                # Both RTEMS and baremetal FreeRTOS are linked above 0x80000000
                result.append("-mcmodel=medium")

        else:
            self.project.warning("Compiler flags might be wong, only native + MIPS checked so far")
        return result

    @property
    def riscv_arch_string(self):
        assert self.target.is_riscv(include_purecap=True)
        # Use the insane RISC-V arch string to enable CHERI
        if self.is_baremetal():
            # Baremetal/FreeRTOS only supports softfloat
            arch_string = "rv64imac"
        else:
            arch_string = "rv64imafdc"

        if self.target.is_hybrid_or_purecap_cheri():
            arch_string += "xcheri"
        return arch_string  # XXX: any more extensions needed?

    @property
    def riscv_abi(self):
        assert self.target.is_riscv(include_purecap=True)
        if self.target.is_cheri_purecap():
            return "l64pc128d"  # 64-bit double-precision hard-float + purecap
        else:
            return "lp64d"  # 64-bit double-precision hard-float

    @property
    def riscv_softfloat_abi(self):
        assert self.target.is_riscv(include_purecap=True)
        if self.target.is_cheri_purecap():
            return "l64pc128"  # 64-bit soft-float purecap
        else:
            return "lp64"  # 64-bit soft-float


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
        return Path(self.sdk_root_dir, "sysroot-freebsd-" + str(self.target.cpu_architecture.value))

    @classmethod
    def is_freebsd(cls):
        return True

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["upstream-llvm"]

    @classmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config: "CheriConfig", include_version: bool):
        common_suffix = "-unknown-freebsd"
        if include_version:
            common_suffix += str(cls.FREEBSD_VERSION)
        # TODO: do we need any special cases here?
        return target.cpu_architecture.value + common_suffix

    @property
    def target_triple(self):
        return self.triple_for_target(self.target, self.config, include_version=True)

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["freebsd"]

    @property
    def pkgconfig_dirs(self) -> str:
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
            return self.config.cheri_sdk_dir / "sysroot"
        return self.get_cheribsd_sysroot_path()

    def get_cheribsd_sysroot_path(self) -> Path:
        """
        :return: The sysroot path
        """
        config = self.config
        if self.target.is_mips(include_purecap=True):
            return self._sysroot_path(config.cheri_sdk_dir, purecap_prefix="-purecap", hybrid_prefix="",
                                      nocheri_name="-mips")
        elif self.target.is_riscv(include_purecap=True):
            return self._sysroot_path(config.cheri_sdk_dir, purecap_prefix="-riscv64-purecap",
                                      hybrid_prefix="-riscv64-hybrid", nocheri_name="-riscv64")
        elif self.target.is_aarch64():
            return config.cheri_sdk_dir / "sysroot-aarch64"
        elif self.target.is_x86_64():
            return config.cheri_sdk_dir / "sysroot-amd64"
        else:
            assert False, "Invalid cross_compile_target: " + str(self.target)

    def _sysroot_path(self, root_dir: Path, *, purecap_prefix: str, hybrid_prefix: str, nocheri_name: str):
        if self.target.is_cheri_hybrid():
            return root_dir / ("sysroot" + hybrid_prefix + self.target.cheri_config_suffix(self.config))
        elif self.target.is_cheri_purecap():
            return root_dir / ("sysroot" + purecap_prefix + self.target.cheri_config_suffix(self.config))
        assert not self.target.is_hybrid_or_purecap_cheri()
        return root_dir / ("sysroot" + nocheri_name)

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
                cheribsd_image = "cheribsd128-cheri128-malta64-mfs-root-minimal-cheribuild-kernel.bz2"
                freebsd_image = "freebsd-malta64-mfs-root-minimal-cheribuild-kernel.bz2"
                if xtarget.is_mips(include_purecap=False) and not xtarget.is_hybrid_or_purecap_cheri():
                    guessed_archive = freebsd_image
                elif xtarget.is_cheri_purecap([CPUArchitecture.MIPS64]):
                    guessed_archive = cheribsd_image
                else:
                    self.project.fatal("Could not guess path to kernel image for CheriBSD")
                    guessed_archive = "invalid path"
                jenkins_kernel_path = self.config.cheribsd_image_root / guessed_archive
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
            qemu_ssh_socket.socket.close()
            self.project.run_cmd(
                [cheribuild_path / "beri-fpga-bsd-boot.py"] + basic_args + ["-vvvvv", "runbench"] + runbench_args)
        else:
            self.project.run_shell_script(beri_fpga_bsd_boot_script, shell="bash")  # the setup script needs bash not sh

    @classmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config: "CheriConfig", include_version):
        if target.is_cheri_purecap():
            # anything over 10 should use libc++ by default
            if target.is_mips(include_purecap=True):
                return "mips64-unknown-freebsd{}".format(cls.FREEBSD_VERSION if include_version else "")
            elif target.is_riscv(include_purecap=True):
                return "riscv64-unknown-freebsd{}".format(cls.FREEBSD_VERSION if include_version else "")
            else:
                assert False, "Unsuported purecap target" + str(cls)
        return super().triple_for_target(target, config, include_version)

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["llvm-native"]

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        # Purecap (currently) builds against the hybrid sysroot:
        if target.is_cheri_purecap():
            if target.is_mips(include_purecap=True):
                return ["cheribsd-mips-hybrid"]
            elif target.is_riscv(include_purecap=True):
                return ["cheribsd-riscv64-hybrid"]
            else:
                assert False, "Logic error"
        # Otherwise pick the matching sysroot
        return ["cheribsd"]

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
        return str(self.sysroot_dir / "usr" / self._sysroot_libdir / "pkgconfig") + ":" + str(
            self.sysroot_dir / self._sysroot_libdir / "pkgconfig") + ":" + str(
            self.sysroot_install_prefix_absolute / "lib/pkgconfig")

    def _get_rootfs_project(self, xtarget: "CrossCompileTarget") -> "Project":
        from ..projects.cross.cheribsd import BuildCHERIBSD
        return BuildCHERIBSD.get_instance(self.project, cross_target=xtarget)


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
        return self.sdk_root_dir / "sysroot"

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

    @property
    def target_triple(self):
        assert self.target.is_riscv(include_purecap=True)
        return "riscv64-unknown-rtems" + str(self.RTEMS_VERSION)

    @property
    def sysroot_dir(self):
        # Install to target triple as RTEMS' LLVM/Clang Driver expects
        return self.sdk_root_dir / ("sysroot-" + self.target.generic_suffix) / self.target_triple

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
        return "Generic"  # Unknown platform, CMake expects the value to be set to Generic

    def _get_sdk_root_dir_lazy(self) -> Path:
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self) -> Path:
        # Install to mips/cheri128 directory
        if self.target.is_cheri_purecap([CPUArchitecture.MIPS64]):
            suffix = "cheri" + self.config.mips_cheri_bits_str
        else:
            suffix = self.target.generic_suffix
        return self.config.cheri_sdk_dir / "baremetal" / suffix / self.target_triple

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

    @property
    def target_triple(self):
        if self.target.is_mips(include_purecap=True):
            if self.target.is_cheri_purecap():
                return "mips64c{}-qemu-elf-purecap".format(self.config.mips_cheri_bits)
            return "mips64-qemu-elf"
        if self.target.is_riscv(include_purecap=True):
            return "riscv64-unknown-elf"
        assert False, "Other baremetal cases have not been tested yet!"

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["newlib", "compiler-rt-builtins"]

    def required_compile_flags(self) -> typing.List[str]:
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


class CompilationTargets(BasicCompilationTargets):
    CHERIBSD_MIPS_NO_CHERI = CrossCompileTarget("mips-nocheri", CPUArchitecture.MIPS64, CheriBSDTargetInfo)
    CHERIBSD_MIPS_HYBRID = CrossCompileTarget("mips-hybrid", CPUArchitecture.MIPS64, CheriBSDTargetInfo,
                                              is_cheri_hybrid=True, check_conflict_with=CHERIBSD_MIPS_NO_CHERI,
                                              non_cheri_target=CHERIBSD_MIPS_NO_CHERI)
    CHERIBSD_MIPS_PURECAP = CrossCompileTarget("mips-purecap", CPUArchitecture.MIPS64, CheriBSDTargetInfo,
                                               is_cheri_purecap=True, check_conflict_with=CHERIBSD_MIPS_NO_CHERI,
                                               hybrid_target=CHERIBSD_MIPS_HYBRID)

    CHERIBSD_RISCV_NO_CHERI = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, CheriBSDTargetInfo)
    CHERIBSD_RISCV_HYBRID = CrossCompileTarget("riscv64-hybrid", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                                               is_cheri_hybrid=True, non_cheri_target=CHERIBSD_RISCV_NO_CHERI)
    CHERIBSD_RISCV_PURECAP = CrossCompileTarget("riscv64-purecap", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                                                is_cheri_purecap=True, hybrid_target=CHERIBSD_RISCV_HYBRID)
    CHERIBSD_AARCH64 = CrossCompileTarget("aarch64", CPUArchitecture.AARCH64, CheriBSDTargetInfo)
    CHERIBSD_X86_64 = CrossCompileTarget("amd64", CPUArchitecture.X86_64, CheriBSDTargetInfo)

    CHERIOS_MIPS_PURECAP = CrossCompileTarget("mips", CPUArchitecture.MIPS64, CheriOSTargetInfo, is_cheri_purecap=True)

    # Baremetal targets
    BAREMETAL_NEWLIB_MIPS64 = CrossCompileTarget("baremetal-mips", CPUArchitecture.MIPS64, NewlibBaremetalTargetInfo)
    BAREMETAL_NEWLIB_MIPS64_PURECAP = CrossCompileTarget("baremetal-mips-purecap", CPUArchitecture.MIPS64,
                                                         NewlibBaremetalTargetInfo, is_cheri_purecap=True,
                                                         non_cheri_target=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV64 = CrossCompileTarget("baremetal-riscv64", CPUArchitecture.RISCV64,
                                                  NewlibBaremetalTargetInfo,
                                                  check_conflict_with=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV64_HYBRID = CrossCompileTarget("baremetal-riscv64-hybrid", CPUArchitecture.RISCV64,
                                                         NewlibBaremetalTargetInfo, is_cheri_hybrid=True,
                                                         non_cheri_target=BAREMETAL_NEWLIB_RISCV64)
    BAREMETAL_NEWLIB_RISCV64_PURECAP = CrossCompileTarget("baremetal-riscv64-purecap", CPUArchitecture.RISCV64,
                                                          NewlibBaremetalTargetInfo, is_cheri_purecap=True,
                                                          hybrid_target=BAREMETAL_NEWLIB_RISCV64_HYBRID)
    # FreeBSD targets
    FREEBSD_MIPS = CrossCompileTarget("mips", CPUArchitecture.MIPS64, FreeBSDTargetInfo)
    FREEBSD_RISCV = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, FreeBSDTargetInfo)
    FREEBSD_I386 = CrossCompileTarget("i386", CPUArchitecture.I386, FreeBSDTargetInfo)
    FREEBSD_AARCH64 = CrossCompileTarget("aarch64", CPUArchitecture.AARCH64, FreeBSDTargetInfo)
    FREEBSD_X86_64 = CrossCompileTarget("amd64", CPUArchitecture.X86_64, FreeBSDTargetInfo)

    # RTEMS targets
    RTEMS_RISCV64 = CrossCompileTarget("rtems-riscv64", CPUArchitecture.RISCV64, RTEMSTargetInfo)
    RTEMS_RISCV64_PURECAP = CrossCompileTarget("rtems-riscv64-purecap", CPUArchitecture.RISCV64, RTEMSTargetInfo,
                                               is_cheri_purecap=True, non_cheri_target=RTEMS_RISCV64)

    # TODO: test RISCV
    ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS = [CHERIBSD_MIPS_PURECAP, CHERIBSD_MIPS_HYBRID, CHERIBSD_MIPS_NO_CHERI,
                                               CHERIBSD_RISCV_PURECAP, CHERIBSD_RISCV_HYBRID, CHERIBSD_RISCV_NO_CHERI,
                                               CHERIBSD_AARCH64, BasicCompilationTargets.NATIVE]
    ALL_CHERIBSD_MIPS_AND_RISCV_TARGETS = [CHERIBSD_MIPS_HYBRID, CHERIBSD_MIPS_NO_CHERI, CHERIBSD_MIPS_PURECAP,
                                           CHERIBSD_RISCV_PURECAP, CHERIBSD_RISCV_HYBRID, CHERIBSD_RISCV_NO_CHERI]
    # Same as above, but the default is purecap RISC-V
    FETT_DEFAULT_ARCHITECTURE = CHERIBSD_RISCV_PURECAP
    FETT_SUPPORTED_ARCHITECTURES = [CHERIBSD_RISCV_PURECAP, CHERIBSD_RISCV_HYBRID, CHERIBSD_RISCV_NO_CHERI,
                                    CHERIBSD_MIPS_HYBRID, CHERIBSD_MIPS_NO_CHERI, CHERIBSD_MIPS_PURECAP]

    ALL_SUPPORTED_BAREMETAL_TARGETS = [BAREMETAL_NEWLIB_MIPS64, BAREMETAL_NEWLIB_MIPS64_PURECAP,
                                       BAREMETAL_NEWLIB_RISCV64, BAREMETAL_NEWLIB_RISCV64_PURECAP]
    ALL_SUPPORTED_RTEMS_TARGETS = [RTEMS_RISCV64, RTEMS_RISCV64_PURECAP]
    ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS = \
        ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS + ALL_SUPPORTED_BAREMETAL_TARGETS
