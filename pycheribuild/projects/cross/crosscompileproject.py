#
# Copyright (c) 2017 Alex Richardson
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
import pprint
import re
import shlex
from builtins import issubclass
from enum import Enum
from pathlib import Path

from ..project import *
from ...config.chericonfig import BuildType
from ...config.target_info import CrossCompileTarget, Linkage, CompilationTargets
from ...utils import *
if typing.TYPE_CHECKING:
    from .cheribsd import BuildCHERIBSD

__all__ = ["CheriConfig", "CrossCompileCMakeProject", "CrossCompileAutotoolsProject", "CrossCompileTarget", "BuildType", # no-combine
           "CrossCompileProject", "CrossInstallDir", "MakeCommandKind", "Linkage", "Path",  # no-combine
           "default_cross_install_dir", "CompilationTargets",  # no-combine
           "_INVALID_INSTALL_DIR", "GitRepository", "commandline_to_str", "CrossCompileMixin"]  # no-combine


class CrossInstallDir(Enum):
    NONE = 0
    CHERIBSD_ROOTFS = 1
    SDK = 2
    COMPILER_RESOURCE_DIR = 3
    BOOTSTRAP_TOOLS = 4


_INVALID_INSTALL_DIR = Path("/this/dir/should/be/overwritten/and/not/used/!!!!")


def get_cheribsd_instance_for_install_dir(config: CheriConfig, project: "SimpleProject") -> "BuildCHERIBSD":
    from .cheribsd import BuildCHERIBSD
    cross_target = project.get_crosscompile_target(config)
    # If use_hybrid_sysroot_for_mips is set, install to rootfs128 instead of rootfs-mips
    if cross_target.is_mips(include_purecap=False) and project.mips_build_hybrid:
        cross_target = CompilationTargets.CHERIBSD_MIPS_PURECAP
    return BuildCHERIBSD.get_instance_for_cross_target(cross_target, config)


def default_cross_install_dir(config: CheriConfig, project: "Project", install_dir_name: str = None):
    if project.crossInstallDir == CrossInstallDir.COMPILER_RESOURCE_DIR:
        compiler_for_resource_dir = project.CC
        # For the NATIVE variant we want to install to CHERI clang:
        if project.compiling_for_host():
            compiler_for_resource_dir = config.cheri_sdk_bindir / "clang"
        return getCompilerInfo(compiler_for_resource_dir).get_resource_dir()

    if project.compiling_for_host():
        if project.crossInstallDir == CrossInstallDir.SDK:
            return config.cheri_sdk_dir
        elif project.crossInstallDir == CrossInstallDir.BOOTSTRAP_TOOLS:
            return config.otherToolsDir
        elif project.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
            return _INVALID_INSTALL_DIR
        return _INVALID_INSTALL_DIR
    if project.crossInstallDir in (CrossInstallDir.CHERIBSD_ROOTFS, CrossInstallDir.BOOTSTRAP_TOOLS):
        cheribsd_instance = get_cheribsd_instance_for_install_dir(config, project)
        if hasattr(project, "path_in_rootfs"):
            assert project.path_in_rootfs.startswith("/"), project.path_in_rootfs
            return cheribsd_instance.installDir / project.path_in_rootfs[1:]
        if install_dir_name is None:
            install_dir_name = project.project_name.lower()
        return Path(cheribsd_instance.installDir / "opt" / project.target_info.install_prefix_dirname / install_dir_name)
    elif project.crossInstallDir == CrossInstallDir.SDK:
        return project.sdk_sysroot
    fatalError("Unknown install dir for", project.project_name)


def _installDirMessage(project: "CrossCompileProject"):
    if project.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
        return "$CHERIBSD_ROOTFS/opt/$TARGET/" + project.project_name.lower() + " or $CHERI_SDK for --xhost build"
    elif project.crossInstallDir == CrossInstallDir.SDK:
        return "$CHERI_SDK/sysroot for cross builds or $CHERI_SDK for --xhost build"
    return "UNKNOWN"


# TODO: remove this class:
# noinspection PyUnresolvedReferences
class CrossCompileMixin(object):
    doNotAddToTargets = True
    config = None  # type: CheriConfig
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    supported_architectures = CompilationTargets.ALL_SUPPORTED_CHERIBSD_AND_HOST_TARGETS

    # noinspection PyTypeChecker
    defaultInstallDir = ComputedDefaultValue(function=default_cross_install_dir, as_string=_installDirMessage)
    default_build_type = BuildType.DEFAULT
    forceDefaultCC = False  # If true fall back to /usr/bin/cc there
    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    _should_not_be_instantiated = True
    _check_install_dir_conflict = True
    defaultOptimizationLevel = ("-O2",)
    can_build_with_asan = True

    # noinspection PyProtectedMember
    @property
    def _no_overwrite_allowed(self) -> "typing.Tuple[str]":
        assert isinstance(self, SimpleProject)
        return super()._no_overwrite_allowed + ("baremetal",)

    needs_mxcaptable_static = False     # E.g. for postgres which is just over the limit:
    #﻿warning: added 38010 entries to .cap_table but current maximum is 32768; try recompiling non-performance critical source files with -mllvm -mxcaptable
    # FIXME: postgres would work if I fixed captable to use the negative immediate values
    needs_mxcaptable_dynamic = False    # This might be true for Qt/QtWebkit

    @property
    def baremetal(self):
        return self.target_info.is_baremetal

    @property
    def rootfs_dir(self):
        assert self.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS
        assert self.cheribsd_rootfs == self.destdir
        return self.cheribsd_rootfs

    @property
    def compiler_warning_flags(self):
        if self.compiling_for_host():
            return self.common_warning_flags + self.host_warning_flags
        else:
            return self.common_warning_flags + self.cross_warning_flags

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if self.cross_build_type in (BuildType.DEBUG, BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO):
            assert self.include_debug_info, "Need --" + self.target + "/debug-info if build-type is " + str(self.cross_build_type.value)
        # convert the tuples into mutable lists (this is needed to avoid modifying class variables)
        # See https://github.com/CTSRD-CHERI/cheribuild/issues/33
        self.defaultOptimizationLevel = list(self.defaultOptimizationLevel)
        self.cross_warning_flags = ["-Wall", "-Werror=cheri-capability-misuse", "-Werror=implicit-function-declaration",
                                    "-Werror=format", "-Werror=undefined-internal", "-Werror=incompatible-pointer-types",
                                    "-Werror=mips-cheri-prototypes", "-Werror=cheri-bitwise-operations"]
        # Make underaligned capability loads/stores an error and require an explicit cast:
        self.cross_warning_flags.append("-Werror=pass-failed")
        self.host_warning_flags = []
        self.common_warning_flags = []

        target_arch = self._crossCompileTarget
        # sanity check:
        assert target_arch is not None and target_arch is not CompilationTargets.NONE
        assert self.get_crosscompile_target(config) is target_arch
        assert isinstance(target_arch, CrossCompileTarget)
        # compiler flags:
        self.COMMON_FLAGS = self.target_info.required_compile_flags()
        if self.compiling_for_host():
            if self._installDir == _INVALID_INSTALL_DIR:
                self._installDir = self.buildDir / "test-install-prefix"
        else:
            # Install to SDK if CHERIBSD_ROOTFS is the install dir but we are not building for CheriBSD
            if self.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS and not self.target_info.is_cheribsd:
                self.crossInstallDir = CrossInstallDir.SDK

            if self.crossInstallDir in (CrossInstallDir.SDK, CrossInstallDir.BOOTSTRAP_TOOLS):
                if self.baremetal:
                    self.destdir = self.sdk_sysroot.parent
                    self._installPrefix = Path("/", self.target_info.target_triple)
                else:
                    self._installPrefix = Path("/usr/local", self.crosscompile_target.generic_suffix)
                    self.destdir = self._installDir
            elif self.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
                self.cheribsd_rootfs = get_cheribsd_instance_for_install_dir(self.config, self).installDir
                relative_to_rootfs = os.path.relpath(str(self._installDir), str(self.cheribsd_rootfs))
                if relative_to_rootfs.startswith(os.path.pardir):
                    self.verbose_print("Custom install dir", self._installDir, "-> using / as install prefix")
                    self._installPrefix = Path("/")
                    self.destdir = self._installDir
                else:
                    self._installPrefix = Path("/", relative_to_rootfs)
                    self.destdir = self.cheribsd_rootfs
            elif self.crossInstallDir == CrossInstallDir.COMPILER_RESOURCE_DIR:
                self._installPrefix = self._installDir
                self.destdir = None
            else:
                assert self._installPrefix and self.destdir, "both must be set!"

        if target_arch.is_cheri_purecap([CPUArchitecture.MIPS64]) and self.force_static_linkage:
            # clang currently gets the TLS model wrong:
            # https://github.com/CTSRD-CHERI/cheribsd/commit/f863a7defd1bdc797712096b6778940cfa30d901
            self.COMMON_FLAGS.append("-ftls-model=initial-exec")
            # TODO: remove the data-depedent provenance flag:
            if self.should_use_extra_c_compat_flags():
                self.COMMON_FLAGS.extend(self.extra_c_compat_flags)  # include cap-table-abi flags

        # We might be setting too many flags, ignore this (for now)
        self.COMMON_FLAGS.append("-Wno-unused-command-line-argument")

        assert self.installDir, "must be set"
        statusUpdate(self.target, "INSTALLDIR = ", self._installDir, "INSTALL_PREFIX=", self._installPrefix,
                     "DESTDIR=", self.destdir)

        if self.include_debug_info:
            self.COMMON_FLAGS.append("-ggdb")
        self.CFLAGS = []
        self.CXXFLAGS = []
        self.ASMFLAGS = []
        self.LDFLAGS = []
        self.COMMON_LDFLAGS = []
        # Don't build CHERI with ASAN since that doesn't work or make much sense
        if self.use_asan and not self.compiling_for_cheri():
            self.COMMON_FLAGS.append("-fsanitize=address")
            self.COMMON_LDFLAGS.append("-fsanitize=address")

    def should_use_extra_c_compat_flags(self):
        # TODO: add a command-line option and default to true for
        return self.compiling_for_cheri() and self.baremetal

    @property
    def extra_c_compat_flags(self):
        if not self.compiling_for_cheri():
            return []
        # Build with virtual address interpretation, data-dependent provenance and pcrelative captable ABI
        return ["-cheri-uintcap=addr", "-Xclang", "-cheri-data-dependent-provenance"]

    @property
    def optimizationFlags(self):
        cbt = self.cross_build_type
        if cbt == BuildType.DEFAULT:
            return self.defaultOptimizationLevel + self._optimizationFlags
        elif cbt == BuildType.DEBUG:
            return ["-O0"] + self._optimizationFlags
        elif cbt in (BuildType.RELEASE, BuildType.RELWITHDEBINFO):
            return ["-O2"] + self._optimizationFlags
        elif cbt in (BuildType.MINSIZEREL, BuildType.MINSIZERELWITHDEBINFO):
            return ["-Os"] + self._optimizationFlags

    @property
    def default_compiler_flags(self):
        result = []
        if self.use_lto:
            result.append("-flto")
        if self.use_cfi:
            if not self.use_lto:
                self.fatal("Cannot use CFI without LTO!")
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
            result.append("-fvisibility=hidden")
        if self.compiling_for_host():
            return result + self.COMMON_FLAGS + self.compiler_warning_flags
        result += self.target_info.essential_compiler_and_linker_flags + self.optimizationFlags
        result += self.COMMON_FLAGS + self.compiler_warning_flags
        if self.config.csetbounds_stats:
            result.extend(["-mllvm", "-collect-csetbounds-output=" + str(self.csetbounds_stats_file),
                           "-mllvm", "-collect-csetbounds-stats=csv",
                           # "-Xclang", "-cheri-bounds=everywhere-unsafe"])
                           "-Xclang", "-cheri-bounds=aggressive"])
        # Add mxcaptable for projects that need it
        if self.compiling_for_cheri() and self.config.cheri_cap_table_abi != "legacy":
            if self.force_static_linkage and self.needs_mxcaptable_static:
                result.append("-mxcaptable")
            if self.force_dynamic_linkage and self.needs_mxcaptable_dynamic:
                result.append("-mxcaptable")
        # Do the same for MIPS to get even performance comparisons
        elif self.compiling_for_mips(include_purecap=False):
            if self.force_static_linkage and self.needs_mxcaptable_static:
                result.extend(["-mxgot", "-mllvm", "-mxmxgot"])
            if self.force_dynamic_linkage and self.needs_mxcaptable_dynamic:
                result.extend(["-mxgot", "-mllvm", "-mxmxgot"])
        return result

    @property
    def default_ldflags(self):
        result = list(self.COMMON_LDFLAGS)
        if self.force_static_linkage:
            result.append("-static")
        if self.use_lto:
            result.append("-flto")
        if self.use_cfi:
            assert not self.compiling_for_cheri()
            result.append("-fsanitize=cfi")
        if self.compiling_for_host():
            return result

        # Should work fine without linker emulation (the linker should infer it from input files)
        # if self.compiling_for_cheri():
        #     emulation = "elf64btsmip_cheri_fbsd" if not self.baremetal else "elf64btsmip_cheri"
        # elif self.compiling_for_mips(include_purecap=False):
        #     emulation = "elf64btsmip_fbsd" if not self.baremetal else "elf64btsmip"
        # result.append("-Wl,-m" + emulation)
        result += self.target_info.essential_compiler_and_linker_flags + [
            "-fuse-ld=" + str(self.target_info.linker),
            # Should no longer be needed now that I added a hack for .eh_frame
            # "-Wl,-z,notext",  # needed so that LLD allows text relocations
            ]
        if self.include_debug_info:
            # Add a gdb_index to massively speed up running GDB on CHERIBSD:
            result.append("-Wl,--gdb-index")
        if self.target_info.is_cheribsd and self.config.withLibstatcounters:
            # We need to include the constructor even if there is no reference to libstatcounters:
            # TODO: always include the .a file?
            result += ["-Wl,--whole-archive", "-lstatcounters", "-Wl,--no-whole-archive"]
        return result

    @classmethod
    def setup_config_options(cls, **kwargs):
        assert issubclass(cls, SimpleProject)
        super().setup_config_options(**kwargs)
        cls.use_lto = cls.add_bool_option("use-lto", help="Build with LTO",)
        # cls.use_cfi = cls.add_bool_option("use-cfi", help="Build with CFI",
        #                                 only_add_for_targets=[CompilationTargets.NATIVE, CompilationTargets.CHERIBSD_MIPS])
        cls.use_cfi = False  # doesn't work yet
        cls._optimizationFlags = cls.add_config_option("optimization-flags", kind=list, metavar="OPTIONS",
                                                     default=[])
        cls.cross_build_type = cls.add_config_option("cross-build-type",
            help="Optimization+debuginfo defaults (supports the same values as CMake plus 'DEFAULT' which does not pass"
                 " any additional flags to the configure script). Note: The overrides the CMake --build-type option.",
            default=cls.default_build_type, kind=BuildType, enum_choice_strings=[t.value for t in BuildType])
        cls._linkage = cls.add_config_option("linkage", help="Build static or dynamic (default means for host=dynamic,"
                                                          " CHERI/MIPS=<value of option --cross-compile-linkage>)",
                                           default=Linkage.DEFAULT, kind=Linkage)

    @property
    def include_debug_info(self) -> bool:
        force_debug_info = getattr(self, "_force_debug_info", None)
        if force_debug_info is not None:
            return force_debug_info
        return self.cross_build_type.should_include_debug_info

    def linkage(self):
        if self.target_info.must_link_statically:
            return Linkage.STATIC
        if self._linkage == Linkage.DEFAULT:
            if self.compiling_for_host():
                return Linkage.DEFAULT  # whatever the project chooses as a default
            else:
                return self.config.crosscompile_linkage  # either force static or force dynamic
        return self._linkage

    @property
    def force_static_linkage(self) -> bool:
        return self.linkage() == Linkage.STATIC

    @property
    def force_dynamic_linkage(self) -> bool:
        return self.linkage() == Linkage.DYNAMIC

    def configure(self, **kwargs):
        env = dict()
        if hasattr(self, "_configure_status_message"):
            statusUpdate(self._configure_status_message)
        if not self.compiling_for_host():
            env.update(PKG_CONFIG_LIBDIR=self.target_info.pkgconfig_dirs, PKG_CONFIG_SYSROOT_DIR=self.crossSysrootPath)
        with setEnv(**env):
            super().configure(**kwargs)

    def copy_asan_dependencies(self, dest_libdir):
        # ASAN depends on libraries that are not included in the benchmark image by default:
        assert self.compiling_for_mips(include_purecap=False) and self.use_asan
        self.info("Adding ASAN library depedencies to", dest_libdir)
        self.makedirs(dest_libdir)
        for lib in ("usr/lib/librt.so.1", "usr/lib/libexecinfo.so.1", "lib/libgcc_s.so.1", "lib/libelf.so.2"):
            self.installFile(self.sdk_sysroot / lib, dest_libdir / Path(lib).name, force=True, print_verbose_only=False)

    @property
    def default_statcounters_csv_name(self) -> str:
        assert isinstance(self, Project)
        # Only compute it once since we encode seconds in the file name:
        if hasattr(self, "_statcounters_csv"):
            return self._statcounters_csv
        else:
            suffix = self.build_configuration_suffix()
            assert isinstance(self, CrossCompileMixin)
            if self.config.benchmark_statcounters_suffix:
                user_suffix = self.config.benchmark_statcounters_suffix
                if not user_suffix.startswith("-"):
                    user_suffix = "-" + user_suffix
                suffix += user_suffix
            else:
                # If we explicitly override the linkage model, encode it in the statcounters file
                if self.force_static_linkage:
                    suffix += "-static"
                elif self.force_dynamic_linkage:
                    suffix += "-dynamic"
                if self.config.benchmark_lazy_binding:
                    suffix += "-lazybinding"
            self._statcounters_csv = self.target + "-statcounters{}-{}.csv".format(
                suffix, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
            return self._statcounters_csv

    def strip_elf_files(self, benchmark_dir):
        """
        Strip all ELF binaries to reduce the size of the benchmark directory
        :param benchmark_dir: The directory containing multiple ELF binaries
        """
        assert isinstance(self, Project) and isinstance(self, CrossCompileMixin)
        self.info("Stripping all ELF files in", benchmark_dir)
        self.run_cmd("du", "-sh", benchmark_dir)
        for root, dirnames, filenames in os.walk(str(benchmark_dir)):
            for filename in filenames:
                file = Path(root, filename)
                if file.suffix == ".dump":
                    # TODO: make this an error since we should have deleted them
                    self.warning("Will copy a .dump file to the FPGA:", file)
                # Try to reduce the amount of copied data
                with file.open("rb") as f:
                    if f.read(4) == b"\x7fELF":
                        self.verbose_print("Stripping ELF binary", file)
                        runCmd(self.sdk_bindir / "llvm-strip", file)
        self.run_cmd("du", "-sh", benchmark_dir)

    def run_fpga_benchmark(self, benchmarks_dir: Path, *, output_file: str = None, benchmark_script: str = None,
                           benchmark_script_args: list = None, extra_runbench_args: list = None):
        assert benchmarks_dir is not None
        assert output_file is not None, "output_file must be set to a valid value"
        assert isinstance(self, Project) and isinstance(self, CrossCompileMixin)
        self.strip_elf_files(benchmarks_dir)
        for root, dirnames, filenames in os.walk(str(benchmarks_dir)):
            for filename in filenames:
                file = Path(root, filename)
                if file.suffix == ".dump":
                    # TODO: make this an error since we should have deleted them
                    self.warning("Will copy a .dump file to the FPGA:", file)

        runbench_args = [benchmarks_dir, "--target=" + self.config.benchmark_ssh_host, "--out-path=" + output_file]

        from ..cherisim import BuildCheriSim
        sim_project = BuildCheriSim.get_instance(self, cross_target=CompilationTargets.NATIVE)
        cherilibs_dir = Path(sim_project.sourceDir, "cherilibs")
        cheri_dir = Path(sim_project.sourceDir, "cheri")
        if not cheri_dir.exists() or not cherilibs_dir.exists():
            self.fatal("cheri-cpu repository missing. Run `cheribuild.py berictl` or `git clone {} {}`".format(
                sim_project.repository.url, sim_project.sourceDir))

        if self.config.benchmark_with_qemu:
            from ..build_qemu import BuildQEMU
            qemu_path = BuildQEMU.qemu_binary(self)
            qemu_ssh_socket = find_free_port()
            if not qemu_path.exists():
                self.fatal("QEMU binary", qemu_path, "doesn't exist")
            basic_args = ["--use-qemu-instead-of-fpga",
                          "--qemu-path=" + str(qemu_path),
                          "--qemu-ssh-port=" + str(qemu_ssh_socket.port)]
        else:
            from ..cherisim import BuildBeriCtl
            basic_args = ["--berictl=" + str(BuildBeriCtl.getBuildDir(self, cross_target=CompilationTargets.NATIVE) / "berictl")]

        if self.config.benchmark_ld_preload:
            runbench_args.append("--extra-input-files=" + str(self.config.benchmark_ld_preload))
            env_var = "LD_CHERI_PRELOAD" if self.compiling_for_cheri() else "LD_PRELOAD"
            pre_cmd = "export {}={};".format(env_var, shlex.quote("/tmp/benchdir/" + self.config.benchmark_ld_preload.name))
            runbench_args.append("--pre-command=" + pre_cmd)
        if self.config.benchmark_fpga_extra_args:
            basic_args.extend(self.config.benchmark_fpga_extra_args)
        if self.config.benchmark_extra_args:
            runbench_args.extend(self.config.benchmark_extra_args)
        if self.config.tests_interact:
            runbench_args.append("--interact")

        from .cheribsd import BuildCheriBsdMfsKernel
        if self.config.benchmark_with_qemu:
            # When benchmarking with QEMU we always spawn a new instance
            if self.config.benchmark_with_debug_kernel:
                kernel_image = BuildCheriBsdMfsKernel.get_installed_kernel_path(self)
            else:
                kernel_image = BuildCheriBsdMfsKernel.get_installed_benchmark_kernel_path(self)
            basic_args.append("--kernel-img=" + str(kernel_image))
        elif self.config.benchmark_clean_boot:
            # use a bitfile from jenkins. TODO: add option for overriding
            if self.compiling_for_mips(include_purecap=False):
                basic_args.append("--jenkins-bitfile=cheri128")
            else:
                assert self.compiling_for_cheri()
                basic_args.append("--jenkins-bitfile=cheri" + self.config.cheriBitsStr)
            # TODO: allow using a plain MIPS kernel?
            mfs_kernel = BuildCheriBsdMfsKernel.get_instance_for_cross_target(CompilationTargets.CHERIBSD_MIPS_PURECAP, self.config)
            if self.config.benchmark_with_debug_kernel:
                kernel_config = mfs_kernel.fpga_kernconf
            else:
                kernel_config = mfs_kernel.fpga_kernconf + "_BENCHMARK"
            basic_args.append("--kernel-img=" + str(mfs_kernel.installed_kernel_for_config(self.config, kernel_config)))
        else:
            runbench_args.append("--skip-boot")
        if benchmark_script:
            runbench_args.append("--script-name=" + benchmark_script)
        if benchmark_script_args:
            runbench_args.append("--script-args=" + commandline_to_str(benchmark_script_args))
        if extra_runbench_args:
            runbench_args.extend(extra_runbench_args)

        cheribuild_path = Path(__file__).parent.parent.parent.parent
        beri_fpga_bsd_boot_script = """
set +x
source "{cheri_dir}/setup.sh"
set -x
export PATH="$PATH:{cherilibs_dir}/tools:{cherilibs_dir}/tools/debug"
exec {cheribuild_path}/beri-fpga-bsd-boot.py {basic_args} -vvvvv runbench {runbench_args}
        """.format(cheri_dir=cheri_dir, cherilibs_dir=cherilibs_dir, runbench_args=commandline_to_str(runbench_args),
                   basic_args=commandline_to_str(basic_args), cheribuild_path=cheribuild_path)
        qemu_ssh_socket = None
        if self.config.benchmark_with_qemu:
            # Free the port that we reserved for QEMU before starting beri-fpga-bsd-boot.py
            qemu_ssh_socket.socket.close()
            self.run_cmd([cheribuild_path / "beri-fpga-bsd-boot.py"] + basic_args + ["-vvvvv", "runbench"] + runbench_args)
        else:
            self.runShellScript(beri_fpga_bsd_boot_script, shell="bash")  # the setup script needs bash not sh

    def process(self):
        if self.use_asan and self.compiling_for_mips(include_purecap=False):
            # copy the ASAN lib into the right directory:
            resource_dir = getCompilerInfo(self.CC).get_resource_dir()
            statusUpdate("Copying ASAN libs to", resource_dir)
            expected_path = resource_dir / "lib/freebsd/"
            asan_libdir_candidates = list((self.sdk_sysroot / "usr/lib/clang").glob("*"))
            versions = [a.name for a in asan_libdir_candidates]
            # Find the newest ASAN runtime library versions from the FreeBSD sysroot
            found_asan_lib = None
            from distutils.version import StrictVersion
            libname = "libclang_rt.asan-mips64.a"
            for version in reversed(sorted(versions, key=StrictVersion)):
                asan_libs = self.sdk_sysroot / "usr/lib/clang" / version / "lib/freebsd"
                if (asan_libs / libname).exists():
                    found_asan_lib = asan_libs / libname
                    break
            if not found_asan_lib:
                self.fatal("Cannot find", libname, "library in sysroot dirs", asan_libdir_candidates, "-- Compilation will fail!")
                found_asan_lib = Path("/some/invalid/path/to/lib")
            self.makedirs(expected_path)
            runCmd("cp", "-av", found_asan_lib.parent, expected_path.parent)
            # For some reason they are 644 so we can't overwrite for the next build unless we chmod first
            runCmd("chmod", "-R", "u+w", expected_path.parent)
            if not (expected_path / libname).exists():
                self.fatal("Cannot find", libname, "library in compiler dir", expected_path, "-- Compilation will fail!")

        if self._check_install_dir_conflict:
            xtarget = self._crossCompileTarget  # type: CrossCompileTarget
            # If the conflicting target is also in supported_architectures, check for conficts:
            if xtarget.check_conflict_with is not None and xtarget.check_conflict_with in self.supported_architectures:
                # Check that we are not installing to the same directory as MIPS to avoid conflicts
                assert hasattr(self, "synthetic_base")
                assert issubclass(self.synthetic_base, SimpleProject)
                other_instance = self.synthetic_base.get_instance_for_cross_target(xtarget.check_conflict_with,
                                                                                  self.config, caller=self)
                xtarget = other_instance.get_crosscompile_target(self.config)
                if self.config.verbose:
                    self.info(self.target, "install dir for", xtarget.name, "is", self.installDir)
                    self.info(self.target, "install dir for", xtarget.check_conflict_with.name, "is", self.installDir)
                assert other_instance.installDir != self.installDir, \
                    mips_instance.target + " reuses the same install prefix! This will cause conflicts: " + str(other_instance.installDir)
        super().process()


class CrossCompileProject(CrossCompileMixin, Project):
    doNotAddToTargets = True


class CrossCompileCMakeProject(CrossCompileMixin, CMakeProject):
    doNotAddToTargets = True  # only used as base class
    defaultCMakeBuildType = "RelWithDebInfo"  # default to O2

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

    def __init__(self, config: CheriConfig, generator: CMakeProject.Generator=CMakeProject.Generator.Ninja):
        if self.cross_build_type != BuildType.DEFAULT:
            # no CMake equivalent for MinSizeRelWithDebInfo -> set minsizerel and force debug info
            if self.cross_build_type == BuildType.MINSIZERELWITHDEBINFO:
                self.cmakeBuildType = BuildType.MINSIZEREL.value
                self._force_debug_info = True
            else:
                self.cmakeBuildType = self.cross_build_type.value
        super().__init__(config, generator)
        # This must come first:
        if self.compiling_for_host():
            self._cmakeTemplate = includeLocalFile("files/NativeToolchain.cmake.in")
            self.toolchainFile = self.buildDir / "NativeToolchain.cmake"
        else:
            # Despite the name it should also work for baremetal newlib
            assert self.target_info.is_cheribsd or (self.target_info.is_baremetal and self.target_info.is_newlib)
            self._cmakeTemplate = includeLocalFile("files/CheriBSDToolchain.cmake.in")
            self.toolchainFile = self.buildDir / "CheriBSDToolchain.cmake"
        self.add_cmake_options(CMAKE_TOOLCHAIN_FILE=self.toolchainFile)
        # The toolchain files need at least CMake 3.6
        self.set_minimum_cmake_version(3, 7)

    def _prepareToolchainFile(self, **kwargs):
        configuredTemplate = self._cmakeTemplate
        for key, value in kwargs.items():
            if value is None:
                continue
            if isinstance(value, bool):
                strval = "1" if value else "0"
            elif isinstance(value, list):
                strval = commandline_to_str(value)
            else:
                strval = str(value)
            assert "@" + key + "@" in configuredTemplate, key
            configuredTemplate = configuredTemplate.replace("@" + key + "@", strval)
        # work around jenkins paths that might contain @[0-9]+ in the path:
        configured_jenkins_workaround = re.sub(r"@\d+", "", configuredTemplate)
        assert "@" not in configured_jenkins_workaround, configured_jenkins_workaround
        self.writeFile(contents=configuredTemplate, file=self.toolchainFile, overwrite=True)

    def configure(self, **kwargs):
        if not self.compiling_for_host():
            self.COMMON_FLAGS.append("-B" + str(self.sdk_bindir))

        if self.crosscompile_target.is_cheri_purecap():
            if self._get_cmake_version() < (3, 9, 0) and not (self.sdk_sysroot / "usr/local/lib/cheri").exists():
                warningMessage("Workaround for missing custom lib suffix in CMake < 3.9")
                self.makedirs(self.sdk_sysroot / "usr/lib")
                # create a /usr/lib/cheri -> /usr/libcheri symlink so that cmake can find the right libraries
                self.createSymlink(Path("../libcheri"), self.sdk_sysroot / "usr/lib/cheri", relative=True,
                    cwd=self.sdk_sysroot / "usr/lib")
                self.makedirs(self.sdk_sysroot / "usr/local/cheri/lib")
                self.makedirs(self.sdk_sysroot / "usr/local/cheri/libcheri")
                self.createSymlink(Path("../libcheri"), self.sdk_sysroot / "usr/local/cheri/lib/cheri",
                    relative=True, cwd=self.sdk_sysroot / "usr/local/cheri/lib")
            add_lib_suffix = """
# cheri libraries are found in /usr/libcheri:
if("${CMAKE_VERSION}" VERSION_LESS 3.9)
  # message(STATUS "CMAKE < 3.9 HACK to find libcheri libraries")
  # need to create a <sysroot>/usr/lib/cheri -> <sysroot>/usr/libcheri symlink 
  set(CMAKE_LIBRARY_ARCHITECTURE "cheri")
  set(CMAKE_SYSTEM_LIBRARY_PATH "${CMAKE_FIND_ROOT_PATH}/usr/libcheri;${
  CMAKE_FIND_ROOT_PATH}/usr/local/cheri/lib;${CMAKE_FIND_ROOT_PATH}/usr/local/cheri/libcheri")
else()
    set(CMAKE_FIND_LIBRARY_CUSTOM_LIB_SUFFIX "cheri")
endif()
set(LIB_SUFFIX "cheri" CACHE INTERNAL "")
"""
        else:
            if self.compiling_for_host():
                add_lib_suffix = None
            else:
                add_lib_suffix = "# no lib suffix needed for non-purecap"

        # FIXME: move this to target_info!
        if self.compiling_for_mips(include_purecap=True):
            if self.crosscompile_target.is_cheri_purecap():
                processor = "CHERI (MIPS IV compatible) with {}-bit capabilities".format(self.config.cheriBitsStr)
            else:
                processor = "BERI (MIPS IV compatible)"
        elif self.crosscompile_target.is_native():
            processor = None
        else:
            processor = self.crosscompile_target.cpu_architecture.value

        # FIXME: move this to target_info!
        if self.compiling_for_host():
            system_name = None
        else:
            system_name = "Generic" if self.baremetal else "FreeBSD"
        self._prepareToolchainFile(
            TOOLCHAIN_SDK_BINDIR=self.sdk_bindir if not self.compiling_for_host() else self.config.cheri_sdk_bindir,
            TOOLCHAIN_COMPILER_BINDIR=self.CC.parent,
            TOOLCHAIN_TARGET_TRIPLE=self.target_info.target_triple,
            TOOLCHAIN_COMMON_FLAGS=self.default_compiler_flags,
            TOOLCHAIN_C_FLAGS=self.CFLAGS,
            TOOLCHAIN_LINKER_FLAGS=self.LDFLAGS + self.default_ldflags,
            TOOLCHAIN_CXX_FLAGS=self.CXXFLAGS,
            TOOLCHAIN_ASM_FLAGS=self.ASMFLAGS,
            TOOLCHAIN_C_COMPILER=self.CC,
            TOOLCHAIN_CXX_COMPILER=self.CXX,
            TOOLCHAIN_SYSROOT=self.sdk_sysroot if not self.compiling_for_host() else None,
            ADD_TOOLCHAIN_LIB_SUFFIX=add_lib_suffix,
            TOOLCHAIN_SYSTEM_PROCESSOR=processor,
            TOOLCHAIN_SYSTEM_NAME=system_name,
            TOOLCHAIN_PKGCONFIG_DIRS=self.target_info.pkgconfig_dirs if not self.compiling_for_host() else None,
            TOOLCHAIN_FORCE_STATIC=self.force_static_linkage,
            )

        if self.generator == CMakeProject.Generator.Ninja:
            # Ninja can't change the RPATH when installing: https://gitlab.kitware.com/cmake/cmake/issues/13934
            # TODO: remove once it has been fixed
            self.add_cmake_options(CMAKE_BUILD_WITH_INSTALL_RPATH=True)
        if self.baremetal and not self.compiling_for_host():
            self.add_cmake_options(CMAKE_EXE_LINKER_FLAGS="-Wl,-T,qemu-malta.ld")
        # TODO: BUILD_SHARED_LIBS=OFF?
        super().configure(**kwargs)


class CrossCompileAutotoolsProject(CrossCompileMixin, AutotoolsProject):
    doNotAddToTargets = True  # only used as base class

    add_host_target_build_config_options = True
    _configure_supports_libdir = True  # override in nginx
    _configure_supports_variables_on_cmdline = True  # override in nginx
    _configure_understands_enable_static = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        buildhost = self.get_host_triple()
        if not self.compiling_for_host() and self.add_host_target_build_config_options:
            autotools_triple = self.target_info.target_triple
            # Most scripts don't like the final -purecap component:
            autotools_triple = autotools_triple.replace("-purecap", "")
            # TODO: do we have to remove these too?
            #autotools_triple = autotools_triple.replace("mips64c128-", "cheri-")
            #autotools_triple = autotools_triple.replace("mips64c256-", "cheri-")
            self.configureArgs.extend(["--host=" + autotools_triple, "--target=" + autotools_triple,
                                       "--build=" + buildhost])

    def add_configure_env_arg(self, arg: str, value: "typing.Union[str,Path]"):
        if not value:
            return
        assert not isinstance(value, list), ("Wrong type:", type(value))
        assert not isinstance(value, tuple), ("Wrong type:", type(value))
        self.configureEnvironment[arg] = str(value)
        if self._configure_supports_variables_on_cmdline:
            self.configureArgs.append(arg + "=" + str(value))

    def add_configure_vars(self, **kwargs):
        for k, v in kwargs.items():
            self.add_configure_env_arg(k, v)

    def set_prog_with_args(self, prog: str, path: Path, args: list):
        fullpath = str(path)
        if args:
            fullpath += " " + commandline_to_str(args)
        self.configureEnvironment[prog] = fullpath
        if self._configure_supports_variables_on_cmdline:
            self.configureArgs.append(prog + "=" + fullpath)

    def configure(self, **kwargs):
        if self._configure_understands_enable_static:     # workaround for nginx which isn't really autotools
            if self.force_static_linkage:
                self.configureArgs.extend(["--enable-static", "--disable-shared"])
            elif self.force_dynamic_linkage:
                self.configureArgs.extend(["--disable-static", "--enable-shared"])
            # Otherwise just let the project decide
            # else:
            #    self.configureArgs.extend(["--enable-static", "--enable-shared"])

        # target triple contains a number suffix -> remove it when computing the compiler name
        if self.compiling_for_cheri() and self._configure_supports_libdir:
            # nginx configure script doesn't understand --libdir
            # make sure that we install to the right directory
            # TODO: can we use relative paths?
            self.configureArgs.append("--libdir=" + str(self.installPrefix) + "/libcheri")

        if not self.baremetal:
            CPPFLAGS = self.default_compiler_flags
            for key in ("CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS"):
                assert key not in self.configureEnvironment
            # autotools overrides CFLAGS -> use CC and CXX vars here
            self.set_prog_with_args("CC", self.CC, CPPFLAGS + self.CFLAGS)
            self.set_prog_with_args("CXX", self.CXX, CPPFLAGS + self.CXXFLAGS)
            # self.add_configure_env_arg("CPPFLAGS", commandline_to_str(CPPFLAGS))
            self.add_configure_env_arg("CFLAGS", commandline_to_str(self.optimizationFlags + self.compiler_warning_flags))
            self.add_configure_env_arg("CXXFLAGS", commandline_to_str(self.optimizationFlags + self.compiler_warning_flags))
            # this one seems to work:
            self.add_configure_env_arg("LDFLAGS", commandline_to_str(self.LDFLAGS + self.default_ldflags))

            if not self.compiling_for_host():
                self.set_prog_with_args("CPP", self.CPP, CPPFLAGS)
                self.add_configure_env_arg("LD", self.target_info.linker)

        # remove all empty items from environment:
        env = {k: v for k, v in self.configureEnvironment.items() if v}
        self.configureEnvironment.clear()
        self.configureEnvironment.update(env)
        self.print(coloured(AnsiColour.yellow, "Cross configure environment:",
                            pprint.pformat(self.configureEnvironment, width=160)))
        super().configure(**kwargs)

    def process(self):
        if not self.compiling_for_host():
            # We run all these commands with $PATH containing $CHERI_SDK/bin to ensure the right tools are used
            with setEnv(PATH=str(self.sdk_bindir) + ":" + os.getenv("PATH")):
                super().process()
        else:
            # when building the native target we just rely on the host tools in /usr/bin
            super().process()
