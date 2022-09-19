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
import copy
import inspect
import os
import re
import sys
import typing
from abc import ABCMeta, abstractmethod
from pathlib import Path

from .chericonfig import CheriConfig
from .config_loader_base import ConfigOptionBase, ConfigLoaderBase
from .target_info import (AutoVarInit, BasicCompilationTargets, CPUArchitecture, CrossCompileTarget, MipsFloatAbi,
                          TargetInfo, AArch64FloatSimdOptions)
from ..projects.project import Project
from ..utils import cached_property, is_jenkins_build


if typing.TYPE_CHECKING:  # no-combine
    from ..projects.run_qemu import AbstractLaunchFreeBSD  # no-combine
    from ..projects.cross.llvm import BuildLLVMBase  # no-combine


class _ClangBasedTargetInfo(TargetInfo, metaclass=ABCMeta):
    uses_morello_llvm: bool = False

    def __init__(self, target, project) -> None:
        super().__init__(target, project)
        self._sdk_root_dir: typing.Optional[Path] = None

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
    def ranlib(self) -> Path:
        return self._compiler_dir / "llvm-ranlib"

    @property
    def nm(self) -> Path:
        return self._compiler_dir / "llvm-nm"

    @property
    def strip_tool(self) -> Path:
        return self._compiler_dir / "llvm-strip"

    @classmethod
    @abstractmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config: "CheriConfig", *, include_version: bool) -> str:
        ...

    def get_target_triple(self, *, include_version: bool) -> str:
        return self.triple_for_target(self.target, self.config, include_version=include_version)

    @classmethod
    def essential_compiler_and_linker_flags_impl(cls, instance: "_ClangBasedTargetInfo", *,
                                                 xtarget: "CrossCompileTarget", softfloat: bool = None,
                                                 perform_sanity_checks=True, default_flags_only=False):
        assert xtarget is not None
        config = instance.config
        project = instance.project
        # noinspection PyProtectedMember
        if perform_sanity_checks and not project._setup_called:
            project.fatal("essential_compiler_and_linker_flags should not be called in __init__, use setup()!",
                          fatal_when_pretending=True)
        # When cross compiling we need at least -target=
        result = ["-target", cls.triple_for_target(xtarget, project.config, include_version=True)]
        # And usually also --sysroot
        if project.needs_sysroot:
            result.append("--sysroot=" + str(instance.sysroot_dir))
            if perform_sanity_checks and project.is_nonexistent_or_empty_dir(instance.sysroot_dir):
                project.fatal("Project", project.target, "needs a sysroot, but", instance.sysroot_dir,
                              " is empty or does not exist.")
        result += ["-B" + str(instance._compiler_dir)]

        if not default_flags_only and project.auto_var_init != AutoVarInit.NONE:
            compiler = project.get_compiler_info(instance.c_compiler)
            valid_clang_version = compiler.is_clang and compiler.version >= (8, 0)
            # We should have at least 8.0.0 unless the user explicitly selected an incompatible clang
            if valid_clang_version:
                result += project.auto_var_init.clang_flags()
            else:
                project.fatal("Requested automatic variable initialization, but don't know how to for", compiler)

        if xtarget.is_mips(include_purecap=True):
            result.append("-integrated-as")
            result.append("-G0")  # no small objects in GOT optimization
            # Floating point ABI:
            if cls.is_baremetal() or cls.is_rtems():
                # The baremetal driver doesn't add -fPIC for CHERI
                if xtarget.is_cheri_purecap([CPUArchitecture.MIPS64]):
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
            if cls.is_cheribsd():
                result.append("-mcpu=beri")
            if xtarget.is_cheri_purecap():
                result.extend(["-mabi=purecap", "-mcpu=beri", "-cheri=" + config.mips_cheri_bits_str])
                if config.subobject_bounds:
                    result.extend(["-Xclang", "-cheri-bounds=" + str(config.subobject_bounds)])
                    if config.subobject_debug:
                        result.extend(["-mllvm", "-cheri-subobject-bounds-clear-swperm=2"])
                if config.cheri_cap_table_abi:
                    result.append("-cheri-cap-table-abi=" + config.cheri_cap_table_abi)
            else:
                assert xtarget.is_mips(include_purecap=False)
                # TODO: should we use -mcpu=cheri128?
                result.extend(["-mabi=n64"])
                if xtarget.is_cheri_hybrid():
                    result.append("-cheri=" + config.mips_cheri_bits_str)
                    result.append("-mcpu=beri")
        elif xtarget.is_riscv(include_purecap=True):
            # Note: Baremetal/FreeRTOS currently only supports softfloat
            if softfloat is None:
                softfloat = cls.is_baremetal()  # assume softfloat for baremetal
            # Use the insane RISC-V arch string to enable CHERI
            result.append("-march=" + cls.get_riscv_arch_string(xtarget, softfloat=softfloat))
            result.append("-mabi=" + cls.get_riscv_abi(xtarget, softfloat=softfloat))
            result.append("-mno-relax")  # Linker relaxations are not supported with clang+lld

            if cls.is_baremetal() or cls.is_rtems():
                # Both RTEMS and baremetal FreeRTOS are linked above 0x80000000
                result.append("-mcmodel=medium")
        elif xtarget.is_aarch64(include_purecap=True):
            fp_simd_option = AArch64FloatSimdOptions.SOFT if softfloat else config.aarch64_fp_and_simd_options
            march_suffix = fp_simd_option.clang_march_flag()
            if cls.uses_morello_llvm:
                # When building with the Morello compiler, we use the Morello CPU as the basline.
                # This makes a noticeable difference for plain aarch64 (v8.2 instead of v8.0) and also enables a few
                # extensions that are not enabled by -march=morello (crypto+crc32)
                result.append("-mcpu=rainier")
            if xtarget.is_cheri_hybrid():
                result += [f"-march=morello{march_suffix}", "-mabi=aapcs"]
            elif xtarget.is_cheri_purecap():
                result += [f"-march=morello+c64{march_suffix}", "-mabi=purecap"]
            else:
                if cls.uses_morello_llvm:
                    # -mcpu=rainier enables capabilities unless -march=morello+noa64c is also passed
                    result.append(f"-march=morello+noa64c{march_suffix}")
                else:
                    result += [f"-march=armv8{march_suffix}"]
        elif xtarget.is_x86_64():
            pass  # No additional flags needed for x86_64.
        else:
            project.warning("Compiler flags might be wong, only native + MIPS checked so far")

        # This needs to be checked last since we depend on the --target/-mabi flags for the -fsanitize= check.
        if config.use_cheri_ubsan and xtarget.is_hybrid_or_purecap_cheri():
            compiler = project.get_compiler_info(instance.c_compiler)
            if compiler.supports_sanitizer_flag("-fsanitize=cheri", result):
                result.append("-fsanitize=cheri")
                if not config.use_cheri_ubsan_runtime:
                    result.append("-fsanitize-trap=cheri")
            else:
                project.warning("Compiler", compiler.path, "does not support -fsanitize=cheri, please update your SDK")
        return result

    @classmethod
    def get_riscv_arch_string(cls, xtarget: CrossCompileTarget, softfloat: bool) -> str:
        assert xtarget.is_riscv(include_purecap=True)
        # Use the insane RISC-V arch string to enable CHERI
        arch_string = "rv" + str(xtarget.cpu_architecture.word_bits()) + "ima"
        if not softfloat:
            arch_string += "fd"
        arch_string += "c"
        if xtarget.is_hybrid_or_purecap_cheri():
            arch_string += "xcheri"
        return arch_string

    @classmethod
    def get_riscv_abi(cls, xtarget: CrossCompileTarget, *, softfloat: bool) -> str:
        assert xtarget.is_riscv(include_purecap=True)
        xlen = xtarget.cpu_architecture.word_bits()
        purecap = xtarget.is_cheri_purecap()
        abi = ""
        if xlen == 32:
            abi += "i"
        abi += "l"
        if not purecap:
            abi += "p"
        abi += str(xlen)
        if purecap:
            abi += "pc" + str(xlen * 2)
        if not softfloat:
            abi += "d"
        return abi


class FreeBSDTargetInfo(_ClangBasedTargetInfo):
    shortname: str = "FreeBSD"
    FREEBSD_VERSION: int = 13

    @property
    def cmake_system_name(self) -> str:
        return "FreeBSD"

    @property
    def toolchain_system_version(self) -> str:
        return str(self.FREEBSD_VERSION) + ".0"

    def _get_sdk_root_dir_lazy(self) -> Path:
        from ..projects.cross.cheribsd import BuildFreeBSD, FreeBSDToolchainKind
        # Determine the toolchain based on --freebsd/toolchain=<>
        fbsd = self._get_rootfs_project(self.target.get_rootfs_target())
        assert isinstance(fbsd, BuildFreeBSD)
        configured_path = fbsd.build_toolchain_root_dir
        if configured_path is None:
            # If we couldn't find a working system compiler, default to cheribuild-compiled upstream LLVM.
            assert fbsd.build_toolchain == FreeBSDToolchainKind.DEFAULT_COMPILER
            # noinspection PyUnresolvedReferences
            return self._get_compiler_project().get_native_install_path(self.config)
        return configured_path

    @property
    def sysroot_dir(self):
        if is_jenkins_build():
            # Jenkins builds compile against a sysroot that was extracted to sdk/sysroot directory and not the
            # full rootfs
            return self.get_non_rootfs_sysroot_dir()
        return self.get_rootfs_project(t=Project).install_dir

    def get_non_rootfs_sysroot_dir(self) -> Path:
        if is_jenkins_build():
            dirname = "sysroot"
        else:
            dirname = "sysroot" + self.target.get_rootfs_target().build_suffix(self.config, include_os=True)
        return Path(self.config.sysroot_output_root / self.config.default_cheri_sdk_directory_name, dirname)

    @classmethod
    def is_freebsd(cls) -> bool:
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
    def freebsd_target(self):
        mapping = {
            CPUArchitecture.AARCH64: "arm64",
            CPUArchitecture.ARM32: "arm",
            CPUArchitecture.I386: "i386",
            CPUArchitecture.MIPS64: "mips",
            CPUArchitecture.RISCV64: "riscv",
            CPUArchitecture.X86_64: "amd64",
        }
        return mapping[self.target.cpu_architecture]

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
    def pkgconfig_dirs(self) -> "typing.List[str]":
        assert self.project.needs_sysroot, "Should not call this for projects that build without a sysroot"
        # FreeBSD uses /usr/libdata/pkgconfig for the native ABI.
        return [str(self.sysroot_dir / "usr/libdata/pkgconfig"),
                str(self.sysroot_install_prefix_absolute / "lib/pkgconfig"),
                str(self.sysroot_install_prefix_absolute / "share/pkgconfig"),
                str(self.sysroot_install_prefix_absolute / "libdata/pkgconfig")]

    @property
    def sysroot_install_prefix_relative(self) -> Path:
        return Path("usr/local")

    def cmake_prefix_paths(self, config: "CheriConfig") -> "list[Path]":
        return [self.sysroot_install_prefix_absolute]

    @property
    def libcompat_suffix(self) -> str:
        assert self.target.is_libcompat_target()
        result = str(self.target.cpu_architecture.word_bits())
        if self.target.is_cheri_purecap():
            result += "c"
        return result

    @cached_property
    def default_libdir(self):
        if self.target.is_libcompat_target():
            return "lib" + self.libcompat_suffix
        return "lib"

    @cached_property
    def localbase(self) -> Path:
        if self.target.is_libcompat_target():
            return Path("usr/local" + self.libcompat_suffix)
        return Path("usr/local")

    @classmethod
    def _get_compiler_project(cls) -> "typing.Type[BuildLLVMBase]":
        from ..projects.cross.llvm import BuildUpstreamLLVM
        return BuildUpstreamLLVM

    def _get_rootfs_project(self, xtarget: "CrossCompileTarget") -> "Project":
        from ..projects.cross.cheribsd import BuildFreeBSD
        return BuildFreeBSD.get_instance(self.project, cross_target=xtarget)

    def _get_mfs_root_kernel(self, platform, use_benchmark_kernel: bool) -> Path:
        raise NotImplementedError("Only implemented for CheriBSD")

    def _get_run_project(self) -> "typing.Type[AbstractLaunchFreeBSD]":
        from ..projects.run_qemu import LaunchFreeBSD
        return LaunchFreeBSD

    def run_cheribsd_test_script(self, script_name, *script_args, kernel_path=None, disk_image_path=None,
                                 mount_builddir=True, mount_sourcedir=False, mount_sysroot=False,
                                 use_full_disk_image=False, mount_installdir=False,
                                 use_benchmark_kernel_by_default=False,
                                 rootfs_alternate_kernel_dir=None) -> None:
        if typing.TYPE_CHECKING:
            assert isinstance(self.project, Project)
        # mount_sysroot may be needed for projects such as QtWebkit where the minimal image doesn't contain all the
        # necessary libraries
        xtarget = self.target
        rootfs_xtarget = xtarget.get_rootfs_target()
        from ..qemu_utils import QemuOptions
        qemu_options = QemuOptions(rootfs_xtarget)
        run_instance = self._get_run_project().get_instance(self.project,
                                                            cross_target=rootfs_xtarget)  # type: AbstractLaunchFreeBSD
        if rootfs_xtarget.cpu_architecture not in (CPUArchitecture.MIPS64, CPUArchitecture.RISCV64,
                                                   CPUArchitecture.X86_64, CPUArchitecture.AARCH64):
            self.project.warning("CheriBSD test scripts currently only work for MIPS, RISC-V, AArch64, and x86-64")
            return
        if use_full_disk_image:
            assert self.is_cheribsd(), "Not supported for FreeBSD yet"
            if qemu_options.can_boot_kernel_directly:
                if kernel_path is None and "--kernel" not in self.config.test_extra_args:
                    kernel_path = run_instance.current_kernel
            if disk_image_path is None and "--disk-image" not in self.config.test_extra_args:
                disk_image_path = run_instance.disk_image
                if not disk_image_path.exists():
                    self.project.dependency_error("Missing disk image",
                                                  cheribuild_target=run_instance.disk_image_project.target)
        elif not qemu_options.can_boot_kernel_directly:
            # We need to boot the disk image instead of running the kernel directly (amd64)
            assert rootfs_xtarget.is_any_x86() or rootfs_xtarget.is_aarch64(
                include_purecap=True), "All other architectures can boot directly"
            if disk_image_path is None and "--disk-image" not in self.config.test_extra_args:
                assert self.is_cheribsd(), "Not supported for FreeBSD yet"
                from ..projects.disk_image import BuildMinimalCheriBSDDiskImage
                instance = BuildMinimalCheriBSDDiskImage.get_instance(self.project, cross_target=rootfs_xtarget)
                disk_image_path = instance.disk_image_path
                if not disk_image_path.exists():
                    self.project.dependency_error("Missing disk image", cheribuild_target=instance.target)
        elif kernel_path is None and "--kernel" not in self.config.test_extra_args:
            from ..projects.cross.cheribsd import ConfigPlatform
            # Use the benchmark kernel by default if the parameter is set and the user didn't pass
            # --no-use-minimal-benchmark-kernel on the command line or in the config JSON
            use_benchmark_kernel_value = self.config.use_minimal_benchmark_kernel  # Load the value first to ensure
            # that it has been loaded
            use_benchmark_config_option = inspect.getattr_static(self.config, "use_minimal_benchmark_kernel")
            assert isinstance(use_benchmark_config_option, ConfigOptionBase)
            want_benchmark_kernel = use_benchmark_kernel_value or (
                    use_benchmark_kernel_by_default and use_benchmark_config_option.is_default_value)
            kernel_path = self._get_mfs_root_kernel(ConfigPlatform.QEMU, want_benchmark_kernel)
            if (kernel_path is None or not kernel_path.exists()) and is_jenkins_build():
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

        cmd = [script, "--architecture", rootfs_xtarget.base_arch_suffix]
        if self.config.test_ssh_key is not None:
            cmd.extend(["--ssh-key", self.config.test_ssh_key])
        if kernel_path and "--kernel" not in self.config.test_extra_args:
            cmd.extend(["--kernel", kernel_path])
        if "--qemu-cmd" not in self.config.test_extra_args:
            chosen_qemu = run_instance.chosen_qemu
            # FIXME: this is rather ugly: In order to access the binary property we have to call setup() first, but
            #  we can't call setup() on the run_instance since that might result in multiple calls to setup().
            # noinspection PyProtectedMember
            if not chosen_qemu._setup:
                chosen_qemu = copy.deepcopy(chosen_qemu)  # avoid modifying the object referenced by run_instance
                chosen_qemu.setup(run_instance)
            cmd.extend(["--qemu-cmd", chosen_qemu.binary])
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
            if not disk_image_path.exists():
                self.project.fatal("Could not find disk image", disk_image_path)
        if self.config.tests_interact:
            cmd.append("--interact")
        if self.config.tests_env_only:
            cmd.append("--test-environment-only")
        if self.config.trap_on_unrepresentable:
            cmd.append("--trap-on-unrepresentable")
        if self.config.test_ld_preload:
            cmd.append("--test-ld-preload=" + str(self.config.test_ld_preload))
            if xtarget.is_cheri_purecap() and not rootfs_xtarget.is_cheri_purecap():
                cmd.append("--test-ld-preload-variable=LD_CHERI_PRELOAD")
            elif not xtarget.is_cheri_purecap() and rootfs_xtarget.is_cheri_purecap():
                cmd.append("--test-ld-preload-variable=LD_64_PRELOAD")
            else:
                cmd.append("--test-ld-preload-variable=LD_PRELOAD")
        if rootfs_alternate_kernel_dir and not qemu_options.can_boot_kernel_directly:
            cmd.extend(["--alternate-kernel-rootfs-path", rootfs_alternate_kernel_dir])

        cmd.extend(map(str, script_args))
        if self.config.test_extra_args:
            cmd.extend(map(str, self.config.test_extra_args))
        self.project.run_cmd(cmd, give_tty_control=True)


class CheriBSDTargetInfo(FreeBSDTargetInfo):
    shortname: str = "CheriBSD"
    os_prefix: str = ""  # CheriBSD is the default target, so we omit the OS prefix from target names
    FREEBSD_VERSION: int = 13

    @classmethod
    def _get_compiler_project(cls) -> "typing.Type[BuildLLVMBase]":
        from ..projects.cross.llvm import BuildCheriLLVM
        return BuildCheriLLVM

    def _get_run_project(self) -> "typing.Type[AbstractLaunchFreeBSD]":
        from ..projects.run_qemu import LaunchCheriBSD
        return LaunchCheriBSD

    @classmethod
    def is_cheribsd(cls) -> bool:
        return True

    def _get_mfs_root_kernel(self, platform, use_benchmark_kernel: bool) -> Path:
        assert self.is_cheribsd(), "Other cases not handled yet"
        from ..projects.cross.cheribsd import BuildCheriBsdMfsKernel
        xtarget = self.target.get_rootfs_target()
        if xtarget not in BuildCheriBsdMfsKernel.supported_architectures:
            self.project.fatal("No MFS kernel for target", xtarget)
            raise ValueError()
        mfs_kernel = BuildCheriBsdMfsKernel.get_instance_for_cross_target(
            xtarget, self.config, caller=self.project)
        kernconf = mfs_kernel.default_kernel_config(platform, benchmark=use_benchmark_kernel)
        return mfs_kernel.get_kernel_install_path(kernconf)

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
    def additional_rpath_directories(self) -> "list[str]":
        # /usr/local/<arch>/lib is not part of the default linker search path, add it here for build systems that
        # don't infer it automatically.
        result = [str(Path("/", self.sysroot_install_prefix_relative, self.default_libdir))]
        if self.default_libdir != "lib":
            result.append(str(Path("/", self.sysroot_install_prefix_relative, "lib")))
        return result

    @property
    def pkgconfig_dirs(self) -> "typing.List[str]":
        assert self.project.needs_sysroot, "Should not call this for projects that build without a sysroot"
        # For CheriBSD we install most packages to /usr/local/<arch>/, but some packages installed by pkg
        # need to be in the default search path under /usr/local or /usr/local64.
        # NB: FreeBSD uses /usr/libdata/pkgconfig for the native ABI.
        pkgconfig_dirname = self.default_libdir if self.target.is_libcompat_target() else "libdata"
        return [str(self.sysroot_dir / f"usr/{pkgconfig_dirname}/pkgconfig"),
                str(self.sysroot_install_prefix_absolute / "lib/pkgconfig"),
                str(self.sysroot_install_prefix_absolute / "share/pkgconfig"),
                str(self.sysroot_install_prefix_absolute / "libdata/pkgconfig"),
                str(self.sysroot_dir / f"{self.localbase}/lib/pkgconfig"),
                str(self.sysroot_dir / f"{self.localbase}/share/pkgconfig"),
                str(self.sysroot_dir / f"{self.localbase}/libdata/pkgconfig")]

    def _get_rootfs_project(self, xtarget: "CrossCompileTarget") -> "Project":
        from ..projects.cross.cheribsd import BuildCHERIBSD
        return BuildCHERIBSD.get_instance(self.project, cross_target=xtarget)

    def cheribsd_version(self) -> "typing.Optional[int]":
        pattern = re.compile(r"#define\s+__CheriBSD_version\s+([0-9]+)")
        try:
            with open(self.sysroot_dir / "usr/include/sys/param.h") as f:
                for line in f:
                    match = pattern.match(line)
                    if match:
                        return int(match.groups()[0])
        except FileNotFoundError:
            return None
        return 0


class CheriBSDMorelloTargetInfo(CheriBSDTargetInfo):
    shortname: str = "CheriBSD-Morello"
    uses_morello_llvm: bool = True

    @classmethod
    def _get_compiler_project(cls) -> "typing.Type[BuildLLVMBase]":
        from ..projects.cross.llvm import BuildMorelloLLVM
        return BuildMorelloLLVM

    @classmethod
    def triple_for_target(cls, target: "CrossCompileTarget", config, *, include_version):
        if target.is_hybrid_or_purecap_cheri():
            assert target.is_aarch64(include_purecap=True), "AArch64 is the only CHERI target supported " \
                                                            "with the Morello toolchain"
            return "aarch64-unknown-freebsd{}".format(cls.FREEBSD_VERSION if include_version else "")
        return super().triple_for_target(target, config, include_version=include_version)

    def get_non_rootfs_sysroot_dir(self) -> Path:
        if is_jenkins_build():
            dirname = "sysroot"
        else:
            dirname = "sysroot" + self.target.get_rootfs_target().build_suffix(self.config, include_os=True)
        return Path(self.config.sysroot_output_root / self.config.default_morello_sdk_directory_name, dirname)

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig"):
        return ["morello-llvm-native"]

    @classmethod
    def essential_compiler_and_linker_flags_impl(cls, instance: "CheriBSDTargetInfo", *args, xtarget, **kwargs):
        result = super().essential_compiler_and_linker_flags_impl(instance, *args, xtarget=xtarget, **kwargs)
        version = instance.cheribsd_version()
        # NB: If version is None, no CheriBSD tree exists, so we assume the new
        # ABI will be used when CheriBSD is eventually built. This ensures the
        # LLVM config files for the SDK utilities get the right flags in the
        # common case as otherwise there is a circular dependency.
        if version is None or version >= 20220511:
            # Use new var-args ABI
            result.extend(["-Xclang", "-morello-vararg=new"])
        if xtarget.is_cheri_purecap([CPUArchitecture.AARCH64]):
            if version is not None and version < 20220511:
                # Use emulated TLS on older purecap
                result.append("-femulated-tls")
        return result


# FIXME: This is completely wrong since cherios is not cheribsd, but should work for now:
class CheriOSTargetInfo(CheriBSDTargetInfo):
    shortname: str = "CheriOS"
    FREEBSD_VERSION: int = 0

    def _get_rootfs_project(self, xtarget: "CrossCompileTarget") -> "Project":
        raise LookupError("Should not be called")

    def _get_sdk_root_dir_lazy(self) -> Path:
        from ..projects.cross.llvm import BuildCheriOSLLVM
        return BuildCheriOSLLVM.get_install_dir(self.project, cross_target=CompilationTargets.NATIVE)

    @property
    def sysroot_dir(self):
        return Path("/this/path/should/not/be/used")

    @classmethod
    def is_cheribsd(cls) -> bool:
        return False

    @classmethod
    def is_freebsd(cls) -> bool:
        return False

    @classmethod
    def is_baremetal(cls) -> bool:
        return True

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["cherios-llvm"]

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        # Otherwise pick the matching sysroot
        return ["cherios"]

    @property
    def pkgconfig_dirs(self) -> "typing.List[str]":
        return []


class RTEMSTargetInfo(_ClangBasedTargetInfo):
    shortname: str = "RTEMS"
    RTEMS_VERSION: int = 5

    @property
    def cmake_system_name(self) -> str:
        return "rtems" + str(self.RTEMS_VERSION)

    @classmethod
    def is_rtems(cls) -> bool:
        return True

    @classmethod
    def is_newlib(cls) -> bool:
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
        return self.config.sysroot_output_root / self.config.default_cheri_sdk_directory_name / (
                "sysroot-" + self.target.get_rootfs_target().generic_arch_suffix) / self.target_triple

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
    shortname: str = "Newlib"
    os_prefix: str = "baremetal-"

    @property
    def cmake_system_name(self) -> str:
        return "Generic"  # CMake requires the value to be set to "Generic" for baremetal targets

    def _get_sdk_root_dir_lazy(self) -> Path:
        return self.config.cheri_sdk_dir

    @property
    def sysroot_dir(self) -> Path:
        # Install to mips/cheri128 directory
        if self.target.get_rootfs_target().is_cheri_purecap([CPUArchitecture.MIPS64]):
            suffix = "cheri" + self.config.mips_cheri_bits_str
        else:
            suffix = self.target.get_rootfs_target().generic_arch_suffix
        sysroot_dir = self.config.sysroot_output_root / self.config.default_cheri_sdk_directory_name
        return sysroot_dir / "baremetal" / suffix / self.target_triple

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
    def is_baremetal(cls) -> bool:
        return True

    @classmethod
    def is_newlib(cls) -> bool:
        return True

    def _get_rootfs_project(self, xtarget: CrossCompileTarget) -> "Project":
        from ..projects.cross.newlib import BuildNewlib
        return BuildNewlib.get_instance(self.project, cross_target=xtarget)


class MorelloBaremetalTargetInfo(_ClangBasedTargetInfo):
    shortname: str = "Morello-Baremetal"
    os_prefix: str = "baremetal-"
    uses_morello_llvm: bool = True

    @property
    def cmake_system_name(self) -> str:
        return "Generic"  # CMake requires the value to be set to "Generic" for baremetal targets

    def _get_sdk_root_dir_lazy(self) -> Path:
        return self.config.morello_sdk_dir

    @property
    def sysroot_dir(self) -> Path:
        suffix = self.target.get_rootfs_target().generic_arch_suffix
        sysroot_dir = self.config.sysroot_output_root / self.config.default_morello_sdk_directory_name
        return sysroot_dir / "baremetal" / suffix / self.target_triple

    @property
    def must_link_statically(self):
        return True  # only static linking works

    @property
    def _compiler_dir(self) -> Path:
        return self.config.morello_sdk_dir / "bin"

    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return ["morello-llvm-native"]

    @classmethod
    def triple_for_target(cls, target, config, include_version: bool) -> str:
        if target.cpu_architecture == CPUArchitecture.ARM32:
            return "arm-none-eabi"
        if target.is_aarch64(include_purecap=True):
            return "aarch64-unknown-elf"
        assert False, "Other baremetal cases have not been tested yet!"

    @classmethod
    def base_sysroot_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return []

    @classmethod
    def essential_compiler_and_linker_flags_impl(cls, *args, xtarget, **kwargs) -> typing.List[str]:
        if xtarget.cpu_architecture == CPUArchitecture.ARM32 or xtarget.is_aarch64(include_purecap=True):
            return super().essential_compiler_and_linker_flags_impl(*args, xtarget=xtarget, **kwargs)
        raise ValueError("Other baremetal cases have not been tested yet!")

    @classmethod
    def is_baremetal(cls) -> bool:
        return True


class ArmNoneEabiGccTargetInfo(TargetInfo):
    @classmethod
    def toolchain_targets(cls, target: "CrossCompileTarget", config: "CheriConfig") -> typing.List[str]:
        return []  # TODO: add a target to download the tarball and extract it

    def get_target_triple(self, *, include_version: bool) -> str:
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
    def ranlib(self) -> Path:
        return self.bindir / (self.binary_prefix + "ranlib")

    @property
    def nm(self) -> Path:
        return self.bindir / (self.binary_prefix + "nm")

    @property
    def strip_tool(self) -> Path:
        return self.bindir / (self.binary_prefix + "strip")

    @classmethod
    def essential_compiler_and_linker_flags_impl(cls, *args, **kwargs) -> "list[str]":
        # This version of GCC should work without any additional flags
        return []

    @classmethod
    def is_baremetal(cls) -> bool:
        return False

    def must_link_statically(self) -> bool:
        return True


def enable_hybrid_for_purecap_rootfs_targets() -> bool:
    # Checking sys.argv here is rather ugly, but we can't make this depend on parsing arguments first since the list of
    # command line options depends on the supported targets.
    if os.getenv("CHERIBUILD_ENABLE_HYBRID_FOR_PURECAP_ROOTFS_TARGETS", None) is not None:
        return True
    argv = sys.argv
    if ConfigLoaderBase.is_completing_arguments:
        argv = os.getenv("COMP_LINE", "").split()
    return "--enable-hybrid-for-purecap-rootfs-targets" in argv


class CompilationTargets(BasicCompilationTargets):
    CHERIBSD_RISCV_NO_CHERI = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, CheriBSDTargetInfo)
    CHERIBSD_RISCV_HYBRID = CrossCompileTarget("riscv64-hybrid", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                                               is_cheri_hybrid=True, non_cheri_target=CHERIBSD_RISCV_NO_CHERI)
    CHERIBSD_RISCV_PURECAP = CrossCompileTarget("riscv64-purecap", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                                                is_cheri_purecap=True, hybrid_target=CHERIBSD_RISCV_HYBRID)
    CHERIBSD_RISCV_NO_CHERI_FOR_HYBRID_ROOTFS = \
        CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                           extra_target_suffix="-for-hybrid-rootfs", rootfs_target=CHERIBSD_RISCV_HYBRID,
                           non_cheri_target=CHERIBSD_RISCV_NO_CHERI)
    CHERIBSD_RISCV_NO_CHERI_FOR_PURECAP_ROOTFS = \
        CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                           extra_target_suffix="-for-purecap-rootfs", rootfs_target=CHERIBSD_RISCV_PURECAP,
                           non_cheri_target=CHERIBSD_RISCV_NO_CHERI)
    CHERIBSD_RISCV_HYBRID_FOR_PURECAP_ROOTFS = \
        CrossCompileTarget("riscv64-hybrid", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                           extra_target_suffix="-for-purecap-rootfs", is_cheri_hybrid=True,
                           rootfs_target=CHERIBSD_RISCV_PURECAP,
                           non_cheri_for_hybrid_rootfs_target=CHERIBSD_RISCV_NO_CHERI_FOR_HYBRID_ROOTFS)
    CHERIBSD_RISCV_PURECAP_FOR_HYBRID_ROOTFS = \
        CrossCompileTarget("riscv64-purecap", CPUArchitecture.RISCV64, CheriBSDTargetInfo,
                           extra_target_suffix="-for-hybrid-rootfs", is_cheri_purecap=True,
                           rootfs_target=CHERIBSD_RISCV_HYBRID,
                           hybrid_for_purecap_rootfs_target=CHERIBSD_RISCV_HYBRID_FOR_PURECAP_ROOTFS)

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
    CHERIBSD_MORELLO_NO_CHERI_FOR_HYBRID_ROOTFS = \
        CrossCompileTarget("morello-aarch64", CPUArchitecture.AARCH64, CheriBSDMorelloTargetInfo,
                           extra_target_suffix="-for-hybrid-rootfs", rootfs_target=CHERIBSD_MORELLO_HYBRID,
                           non_cheri_target=CHERIBSD_MORELLO_NO_CHERI)
    CHERIBSD_MORELLO_NO_CHERI_FOR_PURECAP_ROOTFS = \
        CrossCompileTarget("morello-aarch64", CPUArchitecture.AARCH64, CheriBSDMorelloTargetInfo,
                           extra_target_suffix="-for-purecap-rootfs", rootfs_target=CHERIBSD_MORELLO_PURECAP,
                           non_cheri_target=CHERIBSD_MORELLO_NO_CHERI)
    CHERIBSD_MORELLO_HYBRID_FOR_PURECAP_ROOTFS = \
        CrossCompileTarget("morello-hybrid", CPUArchitecture.AARCH64, CheriBSDMorelloTargetInfo,
                           extra_target_suffix="-for-purecap-rootfs", is_cheri_hybrid=True,
                           rootfs_target=CHERIBSD_MORELLO_PURECAP,
                           non_cheri_for_hybrid_rootfs_target=CHERIBSD_MORELLO_NO_CHERI_FOR_HYBRID_ROOTFS)
    CHERIBSD_MORELLO_PURECAP_FOR_HYBRID_ROOTFS = \
        CrossCompileTarget("morello-purecap", CPUArchitecture.AARCH64, CheriBSDMorelloTargetInfo,
                           extra_target_suffix="-for-hybrid-rootfs", is_cheri_purecap=True,
                           rootfs_target=CHERIBSD_MORELLO_HYBRID,
                           hybrid_for_purecap_rootfs_target=CHERIBSD_MORELLO_HYBRID_FOR_PURECAP_ROOTFS)
    CHERIBSD_X86_64 = CrossCompileTarget("amd64", CPUArchitecture.X86_64, CheriBSDTargetInfo)

    CHERIOS_MIPS_PURECAP = CrossCompileTarget("mips64", CPUArchitecture.MIPS64, CheriOSTargetInfo,
                                              is_cheri_purecap=True)
    CHERIOS_RISCV_PURECAP = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, CheriOSTargetInfo,
                                               is_cheri_purecap=True)

    # Baremetal targets
    BAREMETAL_NEWLIB_MIPS64 = CrossCompileTarget("mips64", CPUArchitecture.MIPS64, NewlibBaremetalTargetInfo)
    BAREMETAL_NEWLIB_MIPS64_PURECAP = CrossCompileTarget("mips64-purecap", CPUArchitecture.MIPS64,
                                                         NewlibBaremetalTargetInfo, is_cheri_purecap=True,
                                                         non_cheri_target=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV32 = CrossCompileTarget("riscv32", CPUArchitecture.RISCV32,
                                                  NewlibBaremetalTargetInfo)
    BAREMETAL_NEWLIB_RISCV32_HYBRID = CrossCompileTarget("riscv32-hybrid", CPUArchitecture.RISCV32,
                                                         NewlibBaremetalTargetInfo, is_cheri_hybrid=True,
                                                         non_cheri_target=BAREMETAL_NEWLIB_RISCV32)
    BAREMETAL_NEWLIB_RISCV32_PURECAP = CrossCompileTarget("riscv32-purecap", CPUArchitecture.RISCV32,
                                                          NewlibBaremetalTargetInfo, is_cheri_purecap=True,
                                                          hybrid_target=BAREMETAL_NEWLIB_RISCV32_HYBRID)
    BAREMETAL_NEWLIB_RISCV64 = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64,
                                                  NewlibBaremetalTargetInfo,
                                                  check_conflict_with=BAREMETAL_NEWLIB_MIPS64)
    BAREMETAL_NEWLIB_RISCV64_HYBRID = CrossCompileTarget("riscv64-hybrid", CPUArchitecture.RISCV64,
                                                         NewlibBaremetalTargetInfo, is_cheri_hybrid=True,
                                                         non_cheri_target=BAREMETAL_NEWLIB_RISCV64)
    BAREMETAL_NEWLIB_RISCV64_PURECAP = CrossCompileTarget("riscv64-purecap", CPUArchitecture.RISCV64,
                                                          NewlibBaremetalTargetInfo, is_cheri_purecap=True,
                                                          hybrid_target=BAREMETAL_NEWLIB_RISCV64_HYBRID)

    MORELLO_BAREMETAL_NO_CHERI = CrossCompileTarget("morello-aarch64", CPUArchitecture.AARCH64,
                                                    MorelloBaremetalTargetInfo, is_cheri_hybrid=False,
                                                    is_cheri_purecap=False)
    MORELLO_BAREMETAL_HYBRID = CrossCompileTarget("morello-hybrid", CPUArchitecture.AARCH64,
                                                  MorelloBaremetalTargetInfo, is_cheri_hybrid=True,
                                                  is_cheri_purecap=False)
    MORELLO_BAREMETAL_PURECAP = CrossCompileTarget("morello-purecap", CPUArchitecture.AARCH64,
                                                   MorelloBaremetalTargetInfo, is_cheri_hybrid=False,
                                                   is_cheri_purecap=True)
    ARM_NONE_EABI = CrossCompileTarget("arm-none-eabi", CPUArchitecture.ARM32, ArmNoneEabiGccTargetInfo,
                                       is_cheri_hybrid=False, is_cheri_purecap=False)  # For 32-bit firmrware
    # FreeBSD targets
    FREEBSD_AARCH64 = CrossCompileTarget("aarch64", CPUArchitecture.AARCH64, FreeBSDTargetInfo)
    FREEBSD_AMD64 = CrossCompileTarget("amd64", CPUArchitecture.X86_64, FreeBSDTargetInfo)
    FREEBSD_I386 = CrossCompileTarget("i386", CPUArchitecture.I386, FreeBSDTargetInfo)
    FREEBSD_MIPS64 = CrossCompileTarget("mips64", CPUArchitecture.MIPS64, FreeBSDTargetInfo)
    FREEBSD_RISCV64 = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, FreeBSDTargetInfo)
    ALL_SUPPORTED_FREEBSD_TARGETS = [FREEBSD_AARCH64, FREEBSD_AMD64, FREEBSD_I386, FREEBSD_RISCV64]

    # RTEMS targets
    RTEMS_RISCV64 = CrossCompileTarget("riscv64", CPUArchitecture.RISCV64, RTEMSTargetInfo)
    RTEMS_RISCV64_PURECAP = CrossCompileTarget("riscv64-purecap", CPUArchitecture.RISCV64, RTEMSTargetInfo,
                                               is_cheri_purecap=True, non_cheri_target=RTEMS_RISCV64)

    ALL_CHERIBSD_RISCV_TARGETS = [CHERIBSD_RISCV_PURECAP, CHERIBSD_RISCV_HYBRID, CHERIBSD_RISCV_NO_CHERI]
    ALL_CHERIBSD_NON_MORELLO_TARGETS = ALL_CHERIBSD_RISCV_TARGETS + [CHERIBSD_AARCH64, CHERIBSD_X86_64]
    ALL_CHERIBSD_MORELLO_TARGETS = [CHERIBSD_MORELLO_PURECAP, CHERIBSD_MORELLO_HYBRID]
    ALL_CHERIBSD_HYBRID_TARGETS = [CHERIBSD_RISCV_HYBRID, CHERIBSD_MORELLO_HYBRID]
    ALL_CHERIBSD_PURECAP_TARGETS = [CHERIBSD_RISCV_PURECAP, CHERIBSD_MORELLO_PURECAP]
    ALL_CHERIBSD_TARGETS_WITH_HYBRID = ALL_CHERIBSD_NON_MORELLO_TARGETS + ALL_CHERIBSD_MORELLO_TARGETS
    ALL_CHERIBSD_NON_CHERI_TARGETS = [CHERIBSD_RISCV_NO_CHERI, CHERIBSD_AARCH64,
                                      CHERIBSD_X86_64]  # does not include i386
    ALL_CHERIBSD_CHERI_TARGETS_WITH_HYBRID = list(
                set(ALL_CHERIBSD_TARGETS_WITH_HYBRID) - set(ALL_CHERIBSD_NON_CHERI_TARGETS))

    # Special targets for specific uses only, not part of any of the above
    ALL_CHERIBSD_NON_CHERI_FOR_HYBRID_ROOTFS_TARGETS = [CHERIBSD_MORELLO_NO_CHERI_FOR_HYBRID_ROOTFS,
                                                        CHERIBSD_RISCV_NO_CHERI_FOR_HYBRID_ROOTFS]
    ALL_CHERIBSD_NON_CHERI_FOR_PURECAP_ROOTFS_TARGETS = [CHERIBSD_MORELLO_NO_CHERI_FOR_PURECAP_ROOTFS,
                                                         CHERIBSD_RISCV_NO_CHERI_FOR_PURECAP_ROOTFS]
    ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS = [CHERIBSD_MORELLO_HYBRID_FOR_PURECAP_ROOTFS,
                                                      CHERIBSD_RISCV_HYBRID_FOR_PURECAP_ROOTFS]
    ALL_CHERIBSD_PURECAP_FOR_HYBRID_ROOTFS_TARGETS = [CHERIBSD_MORELLO_PURECAP_FOR_HYBRID_ROOTFS,
                                                      CHERIBSD_RISCV_PURECAP_FOR_HYBRID_ROOTFS]

    ALL_SUPPORTED_CHERIBSD_TARGETS = ALL_CHERIBSD_NON_CHERI_TARGETS + ALL_CHERIBSD_PURECAP_TARGETS
    ALL_CHERIBSD_TARGETS_WITH_HYBRID_FOR_PURECAP_ROOTFS = (ALL_SUPPORTED_CHERIBSD_TARGETS +
                                                           ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS)
    if enable_hybrid_for_purecap_rootfs_targets():
        ALL_SUPPORTED_CHERIBSD_TARGETS.extend(ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS)
    ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS = ALL_SUPPORTED_CHERIBSD_TARGETS + [BasicCompilationTargets.NATIVE]
    ALL_FREEBSD_AND_CHERIBSD_TARGETS = ALL_SUPPORTED_CHERIBSD_TARGETS + ALL_SUPPORTED_FREEBSD_TARGETS

    ALL_SUPPORTED_BAREMETAL_TARGETS = [BAREMETAL_NEWLIB_MIPS64,
                                       BAREMETAL_NEWLIB_MIPS64_PURECAP,
                                       BAREMETAL_NEWLIB_RISCV32,
                                       BAREMETAL_NEWLIB_RISCV32_HYBRID,
                                       BAREMETAL_NEWLIB_RISCV32_PURECAP,
                                       BAREMETAL_NEWLIB_RISCV64,
                                       BAREMETAL_NEWLIB_RISCV64_HYBRID,
                                       BAREMETAL_NEWLIB_RISCV64_PURECAP,
                                       MORELLO_BAREMETAL_NO_CHERI,
                                       MORELLO_BAREMETAL_HYBRID,
                                       MORELLO_BAREMETAL_PURECAP]
    ALL_SUPPORTED_RTEMS_TARGETS = [RTEMS_RISCV64, RTEMS_RISCV64_PURECAP]
    ALL_SUPPORTED_CHERIBSD_AND_BAREMETAL_AND_HOST_TARGETS = \
        ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS + ALL_SUPPORTED_BAREMETAL_TARGETS

    @staticmethod
    def _dump_cheribsd_target_relations() -> None:
        for target in CompilationTargets.ALL_CHERIBSD_TARGETS_WITH_HYBRID + \
                      CompilationTargets.ALL_CHERIBSD_NON_CHERI_FOR_HYBRID_ROOTFS_TARGETS + \
                      CompilationTargets.ALL_CHERIBSD_NON_CHERI_FOR_PURECAP_ROOTFS_TARGETS + \
                      CompilationTargets.ALL_CHERIBSD_HYBRID_FOR_PURECAP_ROOTFS_TARGETS + \
                      CompilationTargets.ALL_CHERIBSD_PURECAP_FOR_HYBRID_ROOTFS_TARGETS:
            target._dump_target_relations()

# Uncomment for debugging target relations
# CompilationTargets._dump_cheribsd_target_relations()
