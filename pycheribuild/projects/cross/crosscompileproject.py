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
import inspect
import pprint
import re
import shutil
from builtins import issubclass
from enum import Enum
from pathlib import Path

from ...config.loader import ComputedDefaultValue, ConfigOptionBase
from ...config.chericonfig import CrossCompileTarget, MipsFloatAbi, Linkage, BuildType
from .multiarchmixin import MultiArchBaseMixin
from ..llvm import BuildCheriLLVM
from ..project import *
from ...utils import *

__all__ = ["CheriConfig", "CrossCompileCMakeProject", "CrossCompileAutotoolsProject", "CrossCompileTarget", "BuildType", # no-combine
           "CrossCompileProject", "CrossInstallDir", "MakeCommandKind", "Linkage", "Path", "crosscompile_dependencies",  # no-combine
           "_INVALID_INSTALL_DIR", "GitRepository", "commandline_to_str"]  # no-combine

class CrossInstallDir(Enum):
    NONE = 0
    CHERIBSD_ROOTFS = 1
    SDK = 2
    COMPILER_RESOURCE_DIR = 3


_INVALID_INSTALL_DIR = Path("/this/dir/should/be/overwritten/and/not/used/!!!!")


def get_cheribsd_instance_for_install_dir(config: CheriConfig, project: "CrossCompileMixin") -> "BuildCHERIBSD":
    from .cheribsd import BuildCHERIBSD
    cross_target = project.get_crosscompile_target(config)
    # If use_hybrid_sysroot_for_mips is set, install to rootfs128 instead of rootfs-mips
    if project.compiling_for_mips() and (project.mips_build_hybrid or config.use_hybrid_sysroot_for_mips):
        cross_target = CrossCompileTarget.CHERI
    return BuildCHERIBSD.get_instance_for_cross_target(cross_target, config)

def _installDir(config: CheriConfig, project: "CrossCompileProject"):
    assert isinstance(project, CrossCompileMixin)
    if project.compiling_for_host():
        if project.crossInstallDir == CrossInstallDir.SDK:
            return config.sdkDir
        elif project.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
            return _INVALID_INSTALL_DIR
        return _INVALID_INSTALL_DIR
    if project.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
        cheribsd_instance = get_cheribsd_instance_for_install_dir(config, project)
        if hasattr(project, "path_in_rootfs"):
            assert project.path_in_rootfs.startswith("/"), project.path_in_rootfs
            return cheribsd_instance.installDir / project.path_in_rootfs[1:]
        if project.compiling_for_cheri():
            targetName = "cheri" + config.cheriBitsStr
        else:
            assert project.compiling_for_mips()
            targetName = "mips"
        if config.cross_target_suffix:
            targetName += "-" + config.cross_target_suffix
        return Path(cheribsd_instance.installDir / "opt" / targetName / project.projectName.lower())
    elif project.crossInstallDir == CrossInstallDir.SDK:
        return project.sdkSysroot
    elif project.crossInstallDir == CrossInstallDir.COMPILER_RESOURCE_DIR:
        return getCompilerInfo(config.sdkBinDir / "clang").get_resource_dir()
    fatalError("Unknown install dir for", project.projectName)


def _installDirMessage(project: "CrossCompileProject"):
    if project.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
        return "$CHERIBSD_ROOTFS/opt/$TARGET/" + project.projectName.lower() + " or $CHERI_SDK for --xhost build"
    elif project.crossInstallDir == CrossInstallDir.SDK:
        return "$CHERI_SDK/sysroot for cross builds or $CHERI_SDK for --xhost build"
    return "UNKNOWN"


def crosscompile_dependencies(cls: "typing.Type[CrossCompileProject]", config: CheriConfig):
    # TODO: can I avoid instantiating all cross-compile targets here? The hack below might work
    target = cls.get_crosscompile_target(config)
    if target == CrossCompileTarget.NATIVE:
        return ["freestanding-sdk"] if config.use_sdk_clang_for_native_xbuild else []
    else:
        return ["cheribsd-sdk"] if cls.needs_cheribsd_sysroot(target) else ["freestanding-sdk"]


class CrossCompileMixin(MultiArchBaseMixin):
    doNotAddToTargets = True
    config = None  # type: CheriConfig
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS

    defaultInstallDir = ComputedDefaultValue(function=_installDir, asString=_installDirMessage)
    default_build_type = BuildType.DEFAULT
    dependencies = crosscompile_dependencies
    baremetal = False
    forceDefaultCC = False  # for some reason ICU binaries build during build crash -> fall back to /usr/bin/cc there
    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    _should_not_be_instantiated = True
    _check_install_dir_conflict = True
    defaultOptimizationLevel = ("-O2",)
    can_build_with_asan = True

    # noinspection PyProtectedMember
    _no_overwrite_allowed = MultiArchBaseMixin._no_overwrite_allowed + ("baremetal",)  # type: typing.Tuple[str]

    needs_mxcaptable_static = False     # E.g. for postgres which is just over the limit:
    #﻿warning: added 38010 entries to .cap_table but current maximum is 32768; try recompiling non-performance critical source files with -mllvm -mxcaptable
    # FIXME: postgres would work if I fixed captable to use the negative immediate values
    needs_mxcaptable_dynamic = False    # This might be true for Qt/QtWebkit

    @classmethod
    def needs_cheribsd_sysroot(cls, target: CrossCompileTarget):
        # Native projects never need the cheribsd sysroot
        if target == CrossCompileTarget.NATIVE:
            return False
        # Baremetal projects don't need cheribsd, they need newlib instead
        if cls.baremetal:
            return False
        # Otherwise we assume we are targetting CheriBSD so we need the sysroot
        return True

    @property
    def compiler_warning_flags(self):
        if self.compiling_for_host():
            return self.common_warning_flags + self.host_warning_flags
        else:
            return self.common_warning_flags + self.cross_warning_flags

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        if self.cross_build_type in (BuildType.DEBUG, BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO):
            assert self.debugInfo, "Need --" + self.target + "/debug-info if build-type is " + str(self.cross_build_type.value)
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

        target_arch = inspect.getattr_static(self, "_crossCompileTarget")
        if isinstance(target_arch, CrossCompileTarget):
            # Should only be set for the foo-native/foo-cheri, etc. targets
            assert hasattr(self, "synthetic_base")
        else:
            # This should be configurable on the command line
            assert isinstance(target_arch, ConfigOptionBase)
        target_arch = self._crossCompileTarget
        # sanity check:
        assert target_arch is not None
        assert self.get_crosscompile_target(config) == target_arch
        self.compiler_dir = self.config.sdkBinDir
        # Use the compiler from the build directory for native builds to get stddef.h (which will be deleted)
        if self.compiling_for_host():
            llvm_build_dir = BuildCheriLLVM.get_instance(self, config).buildDir
            if (llvm_build_dir / "bin/clang").exists():
                self.compiler_dir = llvm_build_dir / "bin"

        self.targetTriple = None
        # compiler flags:
        if self.compiling_for_host():
            self.COMMON_FLAGS = []
            self.targetTriple = self.get_host_triple()
            if self._installDir == _INVALID_INSTALL_DIR:
                self._installDir = self.buildDir / "test-install-prefix"
        else:
            self.COMMON_FLAGS = ["-integrated-as", "-pipe", "-G0"]
            # clang currently gets the TLS model wrong:
            # https://github.com/CTSRD-CHERI/cheribsd/commit/f863a7defd1bdc797712096b6778940cfa30d901
            self.COMMON_FLAGS.append("-ftls-model=initial-exec")
            # use *-*-freebsd13 to default to libc++
            if self.compiling_for_cheri():
                self.targetTriple = "mips64c" + self.config.cheriBitsStr + ("-unknown-freebsd13-purecap" if not self.baremetal else "-qemu-elf-purecap")
                # This break e.g. compiler_rt: self.targetTriple = "cheri-unknown-freebsd" if not self.baremetal else "cheri-qemu-elf-cheri" + self.config.cheriBitsStr
                if self.should_use_extra_c_compat_flags():
                    self.COMMON_FLAGS.extend(self.extra_c_compat_flags)  # include cap-table-abi flags
                elif self.config.cheri_cap_table_abi:
                    self.COMMON_FLAGS.append("-cheri-cap-table-abi=" + self.config.cheri_cap_table_abi)
            else:
                assert self.compiling_for_mips()
                self.targetTriple = "mips64-unknown-freebsd13" if not self.baremetal else "mips64-qemu-elf"
                self.COMMON_FLAGS.append("-integrated-as")
                self.COMMON_FLAGS.append("-Wno-unused-command-line-argument")
                if not self.baremetal:
                    self.COMMON_FLAGS.append("-stdlib=libc++")
                else:
                    self.COMMON_FLAGS.append("-fno-pic")
                    self.COMMON_FLAGS.append("-mno-abicalls")

            if self.links_against_newlib_baremetal():
                assert self.baremetal
                # Currently we need these flags to build anything against newlib baremetal
                self.COMMON_FLAGS.append("-D_GNU_SOURCE=1")  # needed for the locale functions
                self.COMMON_FLAGS.append("-D_POSIX_MONOTONIC_CLOCK=1")  # pretend that we have a monotonic clock
                self.COMMON_FLAGS.append("-D_POSIX_TIMERS=1")  # pretend that we have a monotonic clock

            if self.crossInstallDir == CrossInstallDir.SDK:
                if self.baremetal:
                    self.destdir = self.sdkSysroot.parent
                    self._installPrefix = Path("/", self.targetTriple)
                else:
                    self._installPrefix = Path("/usr/local", self._crossCompileTarget.value)
                    self.destdir = self._installDir
            elif self.crossInstallDir == CrossInstallDir.CHERIBSD_ROOTFS:
                cheribsd_rootfs = get_cheribsd_instance_for_install_dir(self.config, self).installDir
                relative_to_rootfs = os.path.relpath(str(self._installDir), str(cheribsd_rootfs))
                if relative_to_rootfs.startswith(os.path.pardir):
                    self.verbose_print("Custom install dir", self._installDir, "-> using / as install prefix")
                    self._installPrefix = Path("/")
                    self.destdir = self._installDir
                else:
                    self._installPrefix = Path("/", relative_to_rootfs)
                    self.destdir = cheribsd_rootfs
            elif self.crossInstallDir == CrossInstallDir.COMPILER_RESOURCE_DIR:
                self._installPrefix = self._installDir
                self.destdir = None
            else:
                assert self._installPrefix and self.destdir, "both must be set!"

        assert self.installDir, "must be set"
        statusUpdate(self.target, "INSTALLDIR = ", self._installDir, "INSTALL_PREFIX=", self._installPrefix,
                     "DESTDIR=", self.destdir)

        if self.debugInfo:
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

    @property
    def triple_arch(self):
        return self.targetTriple[:self.targetTriple.find("-")]

    @property
    def sdkSysroot(self) -> Path:
        assert isinstance(self, Project)
        if self.baremetal:
            # Install to mips/cheri128/cheri256 directory
            if self.compiling_for_cheri():
                suffix = "cheri" + self.config.cheriBitsStr
            else:
                suffix = self._crossCompileTarget.value
            return self.config.sdkDir / "baremetal" / suffix / self.targetTriple
        return self.crossSysrootPath

    def links_against_newlib_baremetal(self):
        # This needs to be fixed once we have RTEMS
        return self.baremetal and self.projectName != "newlib-baremetal"

    def should_use_extra_c_compat_flags(self):
        # TODO: add a command-line option and default to true for
        return self.compiling_for_cheri() and self.baremetal

    @property
    def extra_c_compat_flags(self):
        if not self.compiling_for_cheri():
            return []
        # Build with virtual address interpretation, data-dependent provenance and pcrelative captable ABI
        return ["-cheri-uintcap=addr", "-Xclang", "-cheri-data-dependent-provenance",
                "-cheri-cap-table-abi=pcrel"
                # "-cheri-cap-table-abi=legacy" # for now
                ]

    @property
    def targetTripleWithVersion(self):
        # we need to append the FreeBSD version to pick up the correct C++ standard library
        if self.compiling_for_host() or self.baremetal:
            return self.targetTriple
        else:
            # anything over 10 should use libc++ by default
            if self.targetTriple.endswith("-freebsd"):
                return self.targetTriple + "13"
            return self.targetTriple.replace("-freebsd-", "-freebsd13-")

    @property
    def sizeof_void_ptr(self):
        if self._crossCompileTarget in (CrossCompileTarget.MIPS, CrossCompileTarget.NATIVE):
            return 8
        elif self.config.cheriBits == 128:
            return 16
        else:
            assert self.config.cheriBits == 256
            return 32

    @property
    def _essential_compiler_and_linker_flags(self):
        """
        :return: flags such as -target + -mabi which are needed for both compiler and linker
        """
        if self.compiling_for_host():
            return []  # no special flags should be needed
        # However, when cross compiling we need at least -target=
        result = ["-target", self.targetTripleWithVersion]
        if self.baremetal:
            # Also the baremetal driver doesn't add -fPIC for CHERI
            if self.compiling_for_cheri():
                result.append("-fPIC")
                # For now use soft-float to avoid compiler crashes
                result.append(MipsFloatAbi.SOFT.clang_float_flag())
            else:
                # We don't have a softfloat library baremetal so always compile hard-float
                result.append(MipsFloatAbi.HARD.clang_float_flag())
        else:
            result.append(self.config.mips_float_abi.clang_float_flag())

        if self.compiling_for_cheri():
            # TODO: should we use -mcpu=cheri128/256?
            result.extend(["-mabi=purecap", "-mcpu=beri", "-cheri=" + self.config.cheriBitsStr])
            if self.config.subobject_bounds:
                result.extend(["-Xclang", "-cheri-bounds=" + str(self.config.subobject_bounds)])
                if self.config.subobject_debug:
                    result.extend(["-mllvm", "-cheri-subobject-bounds-clear-swperm=2"])
        else:
            assert self.compiling_for_mips()
            # TODO: should we use -mcpu=cheri128/256?
            result.extend(["-mabi=n64", "-mcpu=beri"])
            if self.mips_build_hybrid:
                result.append("-cheri=" + self.config.cheriBitsStr)
        if not self.baremetal:
            result.append("--sysroot=" + str(self.sdkSysroot))
        result += ["-B" + str(self.config.sdkBinDir)]
        return result

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
        if self.compiling_for_host():
            return self.COMMON_FLAGS + self.compiler_warning_flags
        result = self._essential_compiler_and_linker_flags + self.optimizationFlags
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
        if self.compiling_for_mips():
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
        if self.compiling_for_host():
            # return ["-fuse-ld=" + self.linker]
            return result
        elif self.compiling_for_cheri():
            emulation = "elf64btsmip_cheri_fbsd" if not self.baremetal else "elf64btsmip_cheri"
        elif self.compiling_for_mips():
            emulation = "elf64btsmip_fbsd" if not self.baremetal else "elf64btsmip"
        else:
            self.fatal("Logic error!")
            return []
        result += self._essential_compiler_and_linker_flags + [
            "-Wl,-m" + emulation,
            "-fuse-ld=lld",  # TODO: use absolute path?
            # Should no longer be needed now that I added a hack for .eh_frame
            # "-Wl,-z,notext",  # needed so that LLD allows text relocations
        ]
        if self.debugInfo:
            # Add a gdb_index to massively speed up running GDB on CHERIBSD:
            result.append("-Wl,--gdb-index")
        if self.config.withLibstatcounters:
            # We need to include the constructor even if there is no reference to libstatcounters:
            # TODO: always include the .a file?
            result += ["-Wl,--whole-archive", "-lstatcounters", "-Wl,--no-whole-archive"]
        return result

    @property
    def CC(self):
        if not self.should_use_sdk_clang:
            return self.config.clangPath if not self.forceDefaultCC else Path("cc")
        return self.compiler_dir / "clang"

    @property
    def CXX(self):
        if not self.should_use_sdk_clang:
            return self.config.clangPlusPlusPath if not self.forceDefaultCC else Path("c++")
        return self.compiler_dir / "clang++"

    @property
    def CPP(self):
        if not self.should_use_sdk_clang:
            if not self.should_use_sdk_clang:
                return self.config.clangCppPath if not self.forceDefaultCC else Path("c++")
        return self.compiler_dir / "clang-cpp"

    @property
    def should_use_sdk_clang(self):
        # on MacOS compiling with the SDK clang doesn't seem to work as expected (it picks the wrong linker)
        if self.compiling_for_host() and (not self.config.use_sdk_clang_for_native_xbuild or IS_MAC):
            return False
        return True

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        assert issubclass(cls, SimpleProject)
        super().setupConfigOptions(**kwargs)
        cls._debugInfo = cls.addBoolOption("debug-info",
            help="build with debug info by default (Note: this only affects --cross-build-type=DEFAULT)", default=True)
        cls._optimizationFlags = cls.addConfigOption("optimization-flags", kind=list, metavar="OPTIONS",
                                                     default=[])
        cls.cross_build_type = cls.addConfigOption("cross-build-type",
            help="Optimization+debuginfo defaults (supports the same values as CMake plus 'DEFAULT' which does not pass"
                 " any additional flags to the configure script). Note: The overrides the CMake --build-type option.",
            default=cls.default_build_type, kind=BuildType, enum_choice_strings=[t.value for t in BuildType])
        cls._linkage = cls.addConfigOption("linkage", help="Build static or dynamic (default means for host=dynamic,"
                                                          " CHERI/MIPS=<value of option --cross-compile-linkage>)",
                                           default=Linkage.DEFAULT, kind=Linkage)

    @property
    def debugInfo(self):
        # Add debug info by default (to disable set --cross-build-type=Release
        if self.cross_build_type == BuildType.DEBUG:
            return self._debugInfo
        return self.cross_build_type in (BuildType.RELWITHDEBINFO, BuildType.MINSIZERELWITHDEBINFO, BuildType.DEBUG)

    def linkage(self):
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

    @property
    def pkgconfig_dirs(self):
        if self.compiling_for_mips():
            return str(self.sdkSysroot / "usr/lib/pkgconfig") + ":" + \
                   str(self.sdkSysroot / "usr/local/mips/lib/pkgconfig")
        if self.compiling_for_cheri():
            return str(self.sdkSysroot / "usr/libcheri/pkgconfig") + ":" + \
                   str(self.sdkSysroot / "usr/local/cheri/lib/pkgconfig") + ":" +\
                   str(self.sdkSysroot / "usr/local/cheri/libcheri/pkgconfig")
        return None

    def configure(self, **kwargs):
        env = dict()
        if hasattr(self, "_configure_status_message"):
            statusUpdate(self._configure_status_message)
        if not self.compiling_for_host():
            env.update(PKG_CONFIG_LIBDIR=self.pkgconfig_dirs, PKG_CONFIG_SYSROOT_DIR=self.crossSysrootPath)
        with setEnv(**env):
            super().configure(**kwargs)

    def copy_asan_dependencies(self, dest_libdir):
        # ASAN depends on libraries that are not included in the benchmark image by default:
        assert self.compiling_for_mips() and self.use_asan
        self.info("Adding ASAN library depedencies to", dest_libdir)
        self.makedirs(dest_libdir)
        for lib in ("usr/lib/librt.so.1", "usr/lib/libexecinfo.so.1", "lib/libgcc_s.so.1", "lib/libelf.so.2"):
            self.installFile(self.sdkSysroot / lib, dest_libdir / Path(lib).name, force=True, printVerboseOnly=False)

    @property
    def default_statcounters_csv_name(self) -> str:
        assert isinstance(self, Project)
        # Only compute it once since we encode seconds in the file name:
        if hasattr(self, "_statcounters_csv"):
            return self._statcounters_csv
        else:
            self._statcounters_csv = self.target + "-statcounters{}-{}.csv".format(
                self.build_configuration_suffix(), datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
            return self._statcounters_csv

    def run_fpga_benchmark(self, benchmarks_dir: Path, *, output_file: str = None, benchmark_script: str = None,
                           benchmark_script_args: list = None, extra_runbench_args: list = None):
        assert benchmarks_dir is not None
        assert output_file is not None, "output_file must be set to a valid value"
        assert isinstance(self, Project) and isinstance(self, CrossCompileMixin)
        self.info("Stripping all ELF files before copying to the FPGA")
        self.run_cmd("du", "-sh", benchmarks_dir)
        for root, dirnames, filenames in os.walk(str(benchmarks_dir)):
            for filename in filenames:
                file = Path(root, filename)
                if file.suffix == ".dump":
                    # TODO: make this an error since we should have deleted them
                    self.warning("Will copy a .dump file to the FPGA:", file)
                # Try to reduce the amount of copied data
                with file.open("rb") as f:
                    if f.read(4) == b"\x7fELF":
                        self.verbose_print("Stripping ELF binary", file)
                        runCmd(self.config.sdkBinDir / "llvm-strip", file)
        self.run_cmd("du", "-sh", benchmarks_dir)
        runbench_args = [benchmarks_dir, "--target=" + self.config.benchmark_ssh_host, "--out-path=" + output_file]
        basic_args = []
        if self.config.benchmark_fpga_extra_args:
            basic_args.extend(self.config.benchmark_fpga_extra_args)
        if self.config.benchmark_extra_args:
            runbench_args.extend(self.config.benchmark_extra_args)
        if self.config.tests_interact:
            runbench_args.append("--interact")
        if self.config.benchmark_clean_boot:
            # use a bitfile from jenkins. TODO: add option for overriding
            if self.compiling_for_mips():
                # TODO: use a MIPS kernel?
                basic_args.append("--jenkins-bitfile=cheri128")
            else:
                assert self.compiling_for_cheri()
                basic_args.append("--jenkins-bitfile=cheri" + self.config.cheriBitsStr)
            from .cheribsd import BuildCheriBsdMfsKernel
            mfs_kernel = BuildCheriBsdMfsKernel.get_instance(self, self.config)
            basic_args.append("--kernel-img=" + str(mfs_kernel.installed_kernel_for_config(
                self.config, mfs_kernel.fpga_kernconf + "_BENCHMARK")))
        else:
            runbench_args.append("--skip-boot")
        if benchmark_script:
            runbench_args.append("--script-name=" + benchmark_script)
        if benchmark_script_args:
            runbench_args.append("--script-args=" + commandline_to_str(benchmark_script_args))
        if extra_runbench_args:
            runbench_args.extend(extra_runbench_args)
        beri_fpga_bsd_boot_script = """
set +x
source "{cheri_svn}/setup.sh"
set -x
export PATH="$PATH:{cherilibs_svn}/tools:{cherilibs_svn}/tools/debug"
exec beri-fpga-bsd-boot.py {basic_args} -vvvvv runbench {runbench_args}
""".format(cheri_svn=self.config.cheri_svn_checkout, cherilibs_svn=self.config.cherilibs_svn_checkout,
           runbench_args=commandline_to_str(runbench_args), basic_args=commandline_to_str(basic_args))
        self.runShellScript(beri_fpga_bsd_boot_script, shell="bash")  # the setup script needs bash not sh

    def process(self):
        if self.use_asan and self.compiling_for_mips():
            # copy the ASAN lib into the right directory:
            resource_dir = getCompilerInfo(self.CC).get_resource_dir()
            statusUpdate("Copying ASAN libs to", resource_dir)
            expected_path = resource_dir / "lib/freebsd/"
            asan_libdir_candidates = list((self.sdkSysroot / "usr/lib/clang").glob("*"))
            versions = [a.name for a in asan_libdir_candidates]
            # Find the newest ASAN runtime library versions from the FreeBSD sysroot
            found_asan_lib = None
            from distutils.version import StrictVersion
            libname = "libclang_rt.asan-mips64.a"
            for version in reversed(sorted(versions, key=StrictVersion)):
                asan_libs = self.sdkSysroot / "usr/lib/clang" / version / "lib/freebsd"
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

        if self._check_install_dir_conflict and self.compiling_for_cheri() and CrossCompileTarget.MIPS in self.supported_architectures:
            # Check that we are not installing to the same directory as MIPS to avoid conflicts
            assert hasattr(self, "synthetic_base")
            mips_instance = self.synthetic_base.get_instance_for_cross_target(CrossCompileTarget.MIPS, self.config)
            assert mips_instance.get_crosscompile_target(self.config) == CrossCompileTarget.MIPS, mips_instance.get_crosscompile_target(self.config)
            self.info(self.target, self.installDir)
            self.info(mips_instance.target, mips_instance.installDir)
            assert mips_instance.installDir != self.installDir, mips_instance.target + " reuses the same install prefix! This will cause conflicts: " + str(mips_instance.installDir)

        super().process()


class CrossCompileProject(CrossCompileMixin, Project):
    doNotAddToTargets = True


class CrossCompileCMakeProject(CrossCompileMixin, CMakeProject):
    doNotAddToTargets = True  # only used as base class
    defaultCMakeBuildType = "RelWithDebInfo"  # default to O2

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)

    def __init__(self, config: CheriConfig, generator: CMakeProject.Generator=CMakeProject.Generator.Ninja):
        if self.cross_build_type != BuildType.DEFAULT:
            # no CMake equivalent for MinSizeRelWithDebInfo -> set minsizerel and force debug info
            if self.cross_build_type == BuildType.MINSIZERELWITHDEBINFO:
                self.cmakeBuildType = BuildType.MINSIZEREL.value
                self.debugInfo = True
            else:
                self.cmakeBuildType = self.cross_build_type.value
        super().__init__(config, generator)
        # This must come first:
        if self.compiling_for_host():
            self._cmakeTemplate = includeLocalFile("files/NativeToolchain.cmake.in")
            self.toolchainFile = self.buildDir / "NativeToolchain.cmake"
        else:
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
            self.COMMON_FLAGS.append("-B" + str(self.config.sdkBinDir))

        if self.compiling_for_cheri():
            if self._get_cmake_version() < (3, 9, 0) and not (self.sdkSysroot / "usr/local/lib/cheri").exists():
                warningMessage("Workaround for missing custom lib suffix in CMake < 3.9")
                self.makedirs(self.sdkSysroot / "usr/lib")
                # create a /usr/lib/cheri -> /usr/libcheri symlink so that cmake can find the right libraries
                self.createSymlink(Path("../libcheri"), self.sdkSysroot / "usr/lib/cheri", relative=True,
                                   cwd=self.sdkSysroot / "usr/lib")
                self.makedirs(self.sdkSysroot / "usr/local/cheri/lib")
                self.makedirs(self.sdkSysroot / "usr/local/cheri/libcheri")
                self.createSymlink(Path("../libcheri"), self.sdkSysroot / "usr/local/cheri/lib/cheri",
                                   relative=True, cwd=self.sdkSysroot / "usr/local/cheri/lib")
            add_lib_suffix = """
# cheri libraries are found in /usr/libcheri:
if("${CMAKE_VERSION}" VERSION_LESS 3.9)
  # message(STATUS "CMAKE < 3.9 HACK to find libcheri libraries")
  # need to create a <sysroot>/usr/lib/cheri -> <sysroot>/usr/libcheri symlink 
  set(CMAKE_LIBRARY_ARCHITECTURE "cheri")
  set(CMAKE_SYSTEM_LIBRARY_PATH "${CMAKE_FIND_ROOT_PATH}/usr/libcheri;${CMAKE_FIND_ROOT_PATH}/usr/local/cheri/lib;${CMAKE_FIND_ROOT_PATH}/usr/local/cheri/libcheri")
else()
    set(CMAKE_FIND_LIBRARY_CUSTOM_LIB_SUFFIX "cheri")
endif()
set(LIB_SUFFIX "cheri" CACHE INTERNAL "")
"""
            processor = "CHERI (MIPS IV compatible) with {}-bit capabilities".format(self.config.cheriBitsStr)
        elif self.compiling_for_mips():
            add_lib_suffix = "# no lib suffix for mips libraries"
            processor = "BERI (MIPS IV compatible)"
        else:
            add_lib_suffix = None
            processor = None

        if self.compiling_for_host():
            system_name = None
        else:
            system_name = "Generic" if self.baremetal else "FreeBSD"
        self._prepareToolchainFile(
            TOOLCHAIN_SDK_BINDIR=self.config.sdkBinDir,
            TOOLCHAIN_COMPILER_BINDIR=self.compiler_dir,
            TOOLCHAIN_TARGET_TRIPLE=self.targetTriple,
            TOOLCHAIN_COMMON_FLAGS=self.default_compiler_flags,
            TOOLCHAIN_C_FLAGS=self.CFLAGS,
            TOOLCHAIN_LINKER_FLAGS=self.LDFLAGS + self.default_ldflags,
            TOOLCHAIN_CXX_FLAGS=self.CXXFLAGS,
            TOOLCHAIN_ASM_FLAGS=self.ASMFLAGS,
            TOOLCHAIN_C_COMPILER=self.CC,
            TOOLCHAIN_CXX_COMPILER=self.CXX,
            TOOLCHAIN_SYSROOT=self.sdkSysroot if not self.compiling_for_host() else None,
            ADD_TOOLCHAIN_LIB_SUFFIX=add_lib_suffix,
            TOOLCHAIN_SYSTEM_PROCESSOR=processor,
            TOOLCHAIN_SYSTEM_NAME=system_name,
            TOOLCHAIN_PKGCONFIG_DIRS=self.pkgconfig_dirs,
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
            autotools_triple = self.targetTriple
            # Most scripts don't like the final -purecap component:
            autotools_triple = autotools_triple.replace("-purecap", "")
            # TODO: do we have to remove these too?
            #autotools_triple = autotools_triple.replace("mips64c128-", "cheri-")
            #autotools_triple = autotools_triple.replace("mips64c256-", "cheri-")
            self.configureArgs.extend(["--host=" + autotools_triple, "--target=" + autotools_triple,
                                       "--build=" + buildhost])

    def add_configure_env_arg(self, arg: str, value: str):
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
                self.add_configure_env_arg("LD", str(self.compiler_dir / "ld.lld"))

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
            with setEnv(PATH=str(self.config.sdkDir / "bin") + ":" + os.getenv("PATH")):
                super().process()
        else:
            # when building the native target we just rely on the host tools in /usr/bin
            super().process()
