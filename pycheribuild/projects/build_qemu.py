#
# Copyright (c) 2016 Alex Richardson
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
import os
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

from .project import *
from ..config.loader import ComputedDefaultValue
from ..config.target_info import NewlibBaremetalTargetInfo
from ..utils import getCompilerInfo


class BuildQEMUBase(AutotoolsProject):
    repository = GitRepository("https://github.com/qemu/qemu.git")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    # QEMU will not work with BSD make, need GNU make
    make_kind = MakeCommandKind.GnuMake
    doNotAddToTargets = True
    is_sdk_target = True
    skipGitSubmodules = True  # we don't need these
    can_build_with_asan = True
    default_targets = "some-invalid-target"
    lto_by_default = True

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options()
        cls.with_sanitizers = cls.add_bool_option("sanitizers", help="Build QEMU with ASAN/UBSAN (very slow)", default=False)
        cls.use_smbd = cls.add_bool_option("use-smbd", show_help=False, default=True,
                                         help="Don't require SMB support when building QEMU (warning: most --test "
                                              "targets will fail without smbd support)")

        cls.gui = cls.add_bool_option("gui", show_help=True, default=False,
                                    help="Build a the graphical UI bits for QEMU (SDL,VNC)")
        cls.qemu_targets = cls.add_config_option("targets",
            show_help=True, help="Build QEMU for the following targets", default=cls.default_targets)
        cls.prefer_full_lto_over_thin_lto = cls.add_bool_option("full-lto", show_help=False, default=True,
            help="Prefer full LTO over LLVM ThinLTO when using LTO")

    @classmethod
    def qemu_binary(cls, caller: SimpleProject):
        raise NotImplementedError()

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.addRequiredSystemTool("glibtoolize" if self.target_info.is_macos else "libtoolize", homebrew="libtool")
        self.addRequiredSystemTool("autoreconf", homebrew="autoconf")
        self.addRequiredSystemTool("aclocal", homebrew="automake")

        self._addRequiredPkgConfig("pixman-1", homebrew="pixman", zypper="libpixman-1-0-devel", apt="libpixman-1-dev",
                                   freebsd="pixman")
        self._addRequiredPkgConfig("glib-2.0", homebrew="glib", zypper="glib2-devel", apt="libglib2.0-dev",
                                   freebsd="glib")
        # Tests require GNU sed
        self.addRequiredSystemTool("sed" if self.target_info.is_linux else "gsed", homebrew="gnu-sed", freebsd="gsed")

        if self.build_type == BuildType.DEBUG:
            self.COMMON_FLAGS.append("-DCONFIG_DEBUG_TCG=1")
            self.COMMON_FLAGS.append("-O0")
        else:
            self.COMMON_FLAGS.append("-O3")
        if shutil.which("pkg-config"):
            glib_includes = self.run_cmd("pkg-config", "--cflags-only-I", "glib-2.0", captureOutput=True,
                                         print_verbose_only=True, runInPretendMode=True).stdout.decode("utf-8").strip()
            self.COMMON_FLAGS.extend(shlex.split(glib_includes))

        # Disable some more unneeded things (we don't usually need the GUI frontends)
        if not self.gui:
            self.configureArgs.extend(["--disable-vnc", "--disable-sdl", "--disable-gtk", "--disable-opengl"])
            if self.target_info.is_macos:
                self.configureArgs.append("--disable-cocoa")

        # QEMU now builds with python3
        self.configureArgs.append("--python=" + sys.executable)
        if self.build_type == BuildType.DEBUG:
            self.configureArgs.extend(["--enable-debug", "--enable-debug-tcg"])
        else:
            # Try to optimize as much as possible:
            self.configureArgs.extend(["--disable-stack-protector"])

        if self.with_sanitizers:
            self.warning("Option --qemu/sanitizers is deprecated, use --qemu/use-asan instead")
        if self.with_sanitizers or self.use_asan:
            self.configureArgs.append("--enable-sanitizers")
            if self.use_lto:
                self.info("Disabling LTO for ASAN instrumented builds")
            self.use_lto = False

        # Having symbol information is useful for debugging and profiling
        self.configureArgs.append("--disable-strip")

        if not self.target_info.is_linux:
            self.configureArgs.extend(["--disable-linux-aio", "--disable-kvm"])

        if self.config.verbose:
            self.make_args.set(V=1)

    def setup(self):
        super().setup()
        compiler = self.CC
        ccinfo = getCompilerInfo(compiler)
        if ccinfo.compiler == "apple-clang" or (ccinfo.compiler == "clang" and ccinfo.version >= (4, 0, 0)):
            # Turn implicit function declaration into an error -Wimplicit-function-declaration
            self.CFLAGS.extend(["-Werror=implicit-function-declaration",
                                "-Werror=incompatible-pointer-types",
                                # Also make discarding const an error:
                                "-Werror=incompatible-pointer-types-discards-qualifiers",
                                # silence this warning that comes lots of times (it's fine on x86)
                                "-Wno-address-of-packed-member",
                                "-Wextra", "-Wno-sign-compare", "-Wno-unused-parameter",
                                "-Wno-missing-field-initializers"
                                ])
        self.COMMON_FLAGS.append("-Wall")
        # This would have cought some problems in the past
        self.CFLAGS.append("-Werror=return-type")
        if self.use_smbd:
            smbd_path = "/usr/sbin/smbd"
            if self.target_info.is_freebsd:
                smbd_path = "/usr/local/sbin/smbd"
            elif self.target_info.is_macos:
                try:
                    prefix = self.run_cmd("brew", "--prefix", "samba", captureOutput=True, runInPretendMode=True,
                                          print_verbose_only=True).stdout.decode("utf-8").strip()
                except subprocess.CalledProcessError:
                    prefix = self.config.otherToolsDir
                smbd_path = Path(prefix, "sbin/smbd")
                print("Guessed samba path", smbd_path)

            if (self.config.otherToolsDir / "sbin/smbd").exists():
                smbd_path = self.config.otherToolsDir / "sbin/smbd"

            self.addRequiredSystemTool(smbd_path, cheribuild_target="samba", freebsd="samba48", apt="samba",
                                       homebrew="samba")

            self.configureArgs.append("--smbd=" + str(smbd_path))
            if not Path(smbd_path).exists():
                if self.target_info.is_macos:
                    # QEMU user networking expects a smbd that accepts the same flags and config files as the samba.org
                    # sources but the macos /usr/sbin/smbd is incompatible with that:
                    self.warning("QEMU user-mode samba shares require the samba.org smbd. You will need to build it "
                                 "from source (using `cheribuild.py samba`) since the /usr/sbin/smbd shipped by MacOS"
                                 " is incompatible with QEMU")
                self.fatal("Could not find smbd -> QEMU SMB shares networking will not work",
                           fixitHint="Either install samba using the system package manager or with cheribuild. "
                                     "If you really don't need QEMU host shares you can disable the samba dependency "
                                     "by setting --qemu/no-use-smbd")

        self.configureArgs.extend([
            "--target-list=" + self.qemu_targets,
            "--disable-linux-user",
            "--disable-bsd-user",
            "--disable-xen",
            "--disable-docs",
            "--disable-rdma",
            # there are some -Wdeprected-declarations, etc. warnings with new libraries/compilers and it builds
            # with -Werror by default but we don't want the build to fail because of that -> add -Wno-error
            "--disable-werror",
            "--disable-pie",  # no need to build as PIE (this just slows down QEMU)
            "--extra-cflags=" + commandline_to_str(self.default_compiler_flags + self.CFLAGS),
            "--cxx=" + str(self.CXX),
            "--cc=" + str(self.CC),
            # Using /usr/bin/make on macOS breaks compilation DB creation with bear since SIP prevents it from
            # injecting shared libraries into any process that is installed as part of the system.
            "--make=" + self.make_args.command,
            ])
        if self.config.create_compilation_db:
            self.make_args.set(V=1)  # Otherwise bear can't parse the compiler output
        ldflags = self.default_ldflags + self.LDFLAGS
        if ldflags:
            self.configureArgs.append("--extra-ldflags=" + commandline_to_str(ldflags))
        cxxflags = self.default_compiler_flags + self.CXXFLAGS
        if cxxflags:
            self.configureArgs.append("--extra-cxxflags=" + commandline_to_str(cxxflags))

    def run_tests(self):
        self.runMake("check", cwd=self.buildDir)

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir() and not self.config.skipUpdate:
            self.run_cmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir, print_verbose_only=True)
        if (self.sourceDir / "pixman/pixman").exists():
            self.warning("QEMU might build the broken pixman submodule, run `git submodule deinit -f pixman` to clean")
        super().update()


class BuildQEMU(BuildQEMUBase):
    repository = GitRepository("https://github.com/CTSRD-CHERI/qemu.git", default_branch="qemu-cheri",
                               force_branch=True)
    default_targets = "cheri256-softmmu,cheri128-softmmu,cheri128magic-softmmu,mips64-softmmu," \
                      "riscv64-softmmu,riscv64cheri-softmmu,riscv32-softmmu"

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options()
        cls.magic128 = cls.add_bool_option("magic-128")
        # Turn on unaligned loads/stores by default
        cls.unaligned = cls.add_bool_option("unaligned", show_help=True, help="Permit un-aligned loads/stores",
                                            default=False)
        cls.statistics = cls.add_bool_option("statistics", show_help=True,
                                           help="Collect statistics on out-of-bounds capability creation.")

    @classmethod
    def qemu_binary(cls, caller: SimpleProject, xtarget: CrossCompileTarget=None):
        if xtarget is None:
            xtarget = caller.get_crosscompile_target(caller.config)
        if xtarget.is_riscv(include_purecap=True):
            # Always use the CHERI qemu even for plain riscv:
            binary_name = "qemu-system-riscv64cheri"
        else:
            assert xtarget.is_mips(include_purecap=True)
            binary_name = "qemu-system-cheri"
            binary_name += caller.config.cheri_bits_str
            if caller.config.cheriBits == 128 and cls.get_instance(caller, cross_target=CompilationTargets.NATIVE).magic128:
               binary_name += "magic"
        return caller.config.qemu_bindir / os.getenv("QEMU_CHERI_PATH", binary_name)

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        if self.unaligned:
            self.COMMON_FLAGS.append("-DCHERI_UNALIGNED")
        if self.statistics:
            self.COMMON_FLAGS.append("-DDO_CHERI_STATISTICS=1")

    def setup(self):
        super().setup()
        if self.build_type == BuildType.DEBUG:
            self.COMMON_FLAGS.append("-DENABLE_CHERI_SANITIY_CHECKS=1")
        # the capstone disassembler doesn't support CHERI instructions:
        self.configureArgs.append("--disable-capstone")
        # TODO: tests:
        if False:
            # Get all the required compilation flags for the TCG tests
            fake_project = SimpleNamespace()
            fake_project.config = self.config
            fake_project.needs_sysroot = False
            fake_project.warning = self.warning
            fake_project.target = "qemu-tcg-tests"
            tgt_info_mips = NewlibBaremetalTargetInfo(CompilationTargets.BAREMETAL_NEWLIB_MIPS64, fake_project)
            tgt_info_riscv64 = NewlibBaremetalTargetInfo(CompilationTargets.BAREMETAL_NEWLIB_RISCV64, fake_project)
            self.configureArgs.extend([
                "--cross-cc-mips=" + str(tgt_info_mips.c_compiler),
                "--cross-cc-cflags-mips=" + commandline_to_str(tgt_info_mips.essential_compiler_and_linker_flags).replace("=", " "),
                "--cross-cc-riscv64=" + str(tgt_info_riscv64.c_compiler),
                "--cross-cc-cflags-riscv64=" + commandline_to_str(tgt_info_riscv64.essential_compiler_and_linker_flags).replace("=", " ")
                ])


class BuildCheriOSQEMU(BuildQEMU):
    repository = GitRepository("https://github.com/CTSRD-CHERI/qemu.git", default_branch="cherios", force_branch=True)
    project_name = "cherios-qemu"
    target = "cherios-qemu"
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, project: config.outputRoot / "cherios-sdk",
        as_string="$INSTALL_ROOT/cherios-sdk")
    skip_misc_llvm_tools = False  # Cannot skip these tools in upstream LLVM

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._qemuTargets = "cheri256-softmmu,cheri128-softmmu"

    @classmethod
    def qemu_binary(cls, caller: SimpleProject, xtarget=None):
        binary_name = "qemu-system-cheri" + caller.config.cheri_bits_str
        return cls.get_instance(caller, caller.config,
                                cross_target=CompilationTargets.NATIVE).installDir / "bin" / binary_name
