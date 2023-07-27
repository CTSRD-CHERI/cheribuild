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
import sys
import typing
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

from .project import (
    AutotoolsProject,
    BuildType,
    CheriConfig,
    ComputedDefaultValue,
    CPUArchitecture,
    CrossCompileTarget,
    DefaultInstallDir,
    GitRepository,
    MakeCommandKind,
    Project,
)
from .simple_project import BoolConfigOption, SimpleProject, _cached_get_homebrew_prefix
from ..config.compilation_targets import BaremetalFreestandingTargetInfo, CompilationTargets
from ..utils import OSInfo


class BuildQEMUBase(AutotoolsProject):
    repository = GitRepository("https://github.com/qemu/qemu.git")
    native_install_dir = DefaultInstallDir.CHERI_SDK
    # QEMU will not work with BSD make, need GNU make
    make_kind = MakeCommandKind.GnuMake
    do_not_add_to_targets = True
    is_sdk_target = True
    skip_git_submodules = True  # we don't need these
    can_build_with_asan = True
    default_targets: str = "some-invalid-target"
    default_build_type = BuildType.RELEASE
    lto_by_default = True
    smbd_path: Optional[Path]
    qemu_targets: "str"

    use_smbd = BoolConfigOption(
        "use-smbd",
        show_help=False,
        default=True,
        help="Don't require SMB support when building QEMU (warning: most --test "
        "targets will fail without smbd support)",
    )
    gui = BoolConfigOption(
        "gui", show_help=False, default=False, help="Build a the graphical UI bits for QEMU (SDL,VNC)",
    )
    build_profiler = BoolConfigOption(
        "build-profiler", show_help=False, default=False, help="Enable QEMU internal profiling",
    )
    enable_plugins = BoolConfigOption("enable-plugins", show_help=False, default=False, help="Enable QEMU TCG plugins")
    prefer_full_lto_over_thin_lto = BoolConfigOption(
        "full-lto", show_help=False, default=True, help="Prefer full LTO over LLVM ThinLTO when using LTO",
    )

    @classmethod
    def is_toolchain_target(cls):
        return True

    @property
    def _build_type_basic_compiler_flags(self):
        if self.build_type.is_release:
            return ["-O3"]  # Build with -O3 instead of -O2, we want QEMU to be as fast as possible
        return super()._build_type_basic_compiler_flags

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.qemu_targets = typing.cast(
            str,
            cls.add_config_option(
                "targets", show_help=True, help="Build QEMU for the following targets", default=cls.default_targets,
            ),
        )

    @classmethod
    def qemu_binary(
        cls,
        caller: "Optional[SimpleProject]" = None,
        xtarget: "Optional[CrossCompileTarget]" = None,
        config: "Optional[CheriConfig]" = None,
    ):
        if caller is not None:
            if config is None:
                config = caller.config
            if xtarget is None:
                xtarget = caller.crosscompile_target
        else:
            if xtarget is None:
                xtarget = cls.get_crosscompile_target()
            assert config is not None, "Need either caller or config argument!"
        return cls.qemu_binary_for_target(xtarget, config)

    @classmethod
    def qemu_binary_for_target(cls, xtarget: CrossCompileTarget, config: CheriConfig):
        raise NotImplementedError()

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool(
            "glibtoolize" if self.target_info.is_macos() else "libtoolize", default="libtool",
        )
        self.check_required_system_tool("autoreconf", default="autoconf")
        self.check_required_system_tool("aclocal", default="automake")

        self.check_required_pkg_config(
            "pixman-1", homebrew="pixman", zypper="libpixman-1-0-devel", apt="libpixman-1-dev", freebsd="pixman",
        )
        self.check_required_pkg_config(
            "glib-2.0", homebrew="glib", zypper="glib2-devel", apt="libglib2.0-dev", freebsd="glib",
        )
        # Tests require GNU sed
        self.check_required_system_tool(
            "sed" if self.target_info.is_linux() else "gsed", homebrew="gnu-sed", freebsd="gsed",
        )

    def setup(self):
        super().setup()
        # Disable some more unneeded things (we don't usually need the GUI frontends)
        if not self.gui:
            self.configure_args.extend(["--disable-sdl", "--disable-gtk", "--disable-opengl"])
            if self.target_info.is_macos():
                self.configure_args.append("--disable-cocoa")

        if self.build_profiler:
            self.configure_args.extend(["--enable-profiler"])

        if self.enable_plugins:
            self.configure_args.append("--enable-plugins")

        # QEMU now builds with python3
        self.configure_args.append("--python=" + sys.executable)
        if self.build_type.is_debug:
            self.configure_args.extend(["--enable-debug", "--enable-debug-tcg"])
        else:
            # Try to optimize as much as possible:
            self.configure_args.append("--disable-stack-protector")
            self.configure_args.append("--disable-pie")  # no need to build as PIE (this just slows down QEMU)

        if self.build_type.should_include_debug_info:
            self.configure_args.append("--enable-debug-info")

        if self.use_asan:
            self.configure_args.append("--enable-sanitizers")
            # Ensure that tests crash on UBSan reports
            self.COMMON_FLAGS.append("-fno-sanitize-recover=all")
            if self.use_lto:
                self.info("Disabling LTO for ASAN instrumented builds")
            self.use_lto = False

        # Having symbol information is useful for debugging and profiling
        self.configure_args.append("--disable-strip")

        if not self.target_info.is_linux():
            self.configure_args.extend(["--disable-linux-aio", "--disable-kvm"])

        if self.config.verbose:
            self.make_args.set(V=1)

        compiler = self.CC
        ccinfo = self.get_compiler_info(compiler)
        if ccinfo.compiler == "apple-clang" or (ccinfo.compiler == "clang" and ccinfo.version >= (4, 0, 0)):
            # Turn implicit function declaration into an error -Wimplicit-function-declaration
            self.CFLAGS.extend(
                [
                    "-Werror=implicit-function-declaration",
                    "-Werror=incompatible-pointer-types",
                    # Also make discarding const an error:
                    "-Werror=incompatible-pointer-types-discards-qualifiers",
                    # silence this warning that comes lots of times (it's fine on x86)
                    "-Wno-address-of-packed-member",
                    "-Wextra",
                    "-Wno-sign-compare",
                    "-Wno-unused-parameter",
                    "-Wno-missing-field-initializers",
                ],
            )
        if ccinfo.compiler == "clang" and ccinfo.version >= (13, 0, 0):
            self.CFLAGS.append("-Wno-null-pointer-subtraction")
        # This would have caught some problems in the past
        self.common_warning_flags.append("-Wno-error=return-type")
        if self.use_smbd:
            self.smbd_path = Path("/usr/sbin/smbd")
            if self.target_info.is_freebsd():
                self.smbd_path = Path("/usr/local/sbin/smbd")
            elif self.target_info.is_macos():
                prefix = _cached_get_homebrew_prefix("samba", self.config)
                if prefix:
                    self.smbd_path = prefix / "sbin/samba-dot-org-smbd"
                else:
                    self.smbd_path = self.config.other_tools_dir / "sbin/smbd"
                self.info("Guessed samba path", self.smbd_path)

            # Prefer the self-compiled samba if available.
            if (self.config.other_tools_dir / "sbin/smbd").exists():
                self.smbd_path = self.config.other_tools_dir / "sbin/smbd"

            self.configure_args.append("--smbd=" + str(self.smbd_path))
            if not Path(self.smbd_path).exists():
                if self.target_info.is_macos():
                    # QEMU user networking expects a smbd that accepts the same flags and config files as the samba.org
                    # sources but the macOS /usr/sbin/smbd is incompatible with that:
                    self.warning(
                        "QEMU user-mode samba shares require the samba.org smbd. You will need to install it "
                        "using homebrew (`brew install samba`) or build from source (`cheribuild.py samba`) "
                        "since the /usr/sbin/smbd shipped by macOS is incompatible with QEMU",
                    )
                self.fatal(
                    "Could not find smbd -> QEMU SMB shares networking will not work",
                    fixit_hint="Either install samba using the system package manager or with cheribuild. "
                    "If you really don't need QEMU host shares you can disable the samba dependency "
                    "by setting --" + self.target + "/no-use-smbd",
                )

        self.configure_args.extend(
            [
                "--target-list=" + self.qemu_targets,
                "--disable-xen",
                "--disable-docs",
                "--disable-rdma",
                # there are some -Wdeprected-declarations, etc. warnings with new libraries/compilers and it builds
                # with -Werror by default but we don't want the build to fail because of that -> add -Wno-error
                "--disable-werror",
                "--extra-cflags=" + self.commandline_to_str(self.default_compiler_flags + self.CFLAGS),
                "--cxx=" + str(self.CXX),
                "--cc=" + str(self.CC),
                # Using /usr/bin/make on macOS breaks compilation DB creation with bear since SIP prevents it from
                # injecting shared libraries into any process that is installed as part of the system.
                "--make=" + self.make_args.command,
            ],
        )

        if self.config.create_compilation_db:
            self.make_args.set(V=1)  # Otherwise bear can't parse the compiler output
        ldflags = self.default_ldflags + self.LDFLAGS
        if ldflags:
            self.configure_args.append("--extra-ldflags=" + self.commandline_to_str(ldflags))
        cxxflags = self.default_compiler_flags + self.CXXFLAGS
        if cxxflags:
            self.configure_args.append("--extra-cxxflags=" + self.commandline_to_str(cxxflags))

    def configure(self, **kwargs):
        # We call this here instead of inside setup to make sure the repository has been cloned
        if self.repository.contains_commit(self, "5890258aeeba303704ec1adca415e46067800777", src_dir=self.source_dir):
            # TODO: do we want to check for a minimum version here?
            self.check_required_pkg_config("slirp", apt="libslirp-dev", freebsd="libslirp")
            # QEMU now requires a system installation of slirp.
            self.configure_args.append("--enable-slirp")
        else:
            self.configure_args.append("--enable-slirp=git")
        super().configure(**kwargs)

    def run_tests(self):
        self.run_make("check", cwd=self.build_dir)

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.source_dir / "po").is_dir() and not self.skip_update:
            self.run_cmd("git", "checkout", "HEAD", "po/", cwd=self.source_dir, print_verbose_only=True)
        if (self.source_dir / "pixman/pixman").exists():
            self.warning("QEMU might build the broken pixman submodule, run `git submodule deinit -f pixman` to clean")
        super().update()

    def process(self) -> None:
        if self.use_smbd and self.smbd_path is not None:
            self.check_required_system_tool(
                str(self.smbd_path),
                cheribuild_target="samba",
                freebsd="samba416",
                apt="samba",
                homebrew="samba",
            )
        super().process()


class RunMorelloQEMUTests(Project):
    target = "morello-qemu-tests"
    repository = GitRepository("https://github.com/rems-project/morello-generated-tests.git")
    default_install_dir = DefaultInstallDir.DO_NOT_INSTALL

    def compile(self, **kwargs):
        self.info("No compile step needed, tests are all binaries.")

    def run_tests(self) -> None:
        # TODO: suggest installing pytest-xdist
        qemu = BuildQEMU.get_instance(self)
        qemu_binary = qemu.qemu_binary_for_target(CompilationTargets.FREESTANDING_MORELLO_PURECAP, self.config)
        self.run_cmd(
            sys.executable,
            "-m",
            "pytest",
            qemu.source_dir / "tests/morello",
            f"--morello-tests-dir={self.source_dir}",
            f"--qemu={qemu_binary}",
            f"--junit-xml={self.build_dir}/morello-generated-tests-result.xml",
            cwd=qemu.source_dir / "tests/morello",
        )


# noinspection PyAbstractClass
class BuildUpstreamQEMU(BuildQEMUBase):
    repository = GitRepository("https://github.com/qemu/qemu.git")
    target = "upstream-qemu"
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, project: config.output_root / "upstream-qemu",
        as_string="$INSTALL_ROOT/upstream-qemu",
    )
    if OSInfo.IS_FREEBSD:
        user_targets = ",arm-bsd-user,i386-bsd-user,x86_64-bsd-user"
    elif OSInfo.IS_LINUX:
        user_targets = ",arm-linux-user,aarch64-linux-user,i386-linux-user,x86_64-linux-user,riscv64-linux-user"
    else:
        user_targets = ""
    default_targets = (
        "arm-softmmu,aarch64-softmmu,mips64-softmmu," "riscv64-softmmu,riscv32-softmmu,x86_64-softmmu" + user_targets
    )

    def setup(self):
        super().setup()
        if OSInfo.IS_LINUX:
            self.configure_args.append("--enable-linux-user")
        elif OSInfo.IS_FREEBSD:
            self.configure_args.append("--enable-bsd-user")

    @classmethod
    def qemu_binary_for_target(cls, xtarget: CrossCompileTarget, config: CheriConfig):
        if xtarget.is_hybrid_or_purecap_cheri():
            raise ValueError("Upstream QEMU does not support CHERI")
        if xtarget.is_aarch64():
            binary_name = "qemu-system-aarch64"
        elif xtarget.cpu_architecture == CPUArchitecture.ARM32:
            binary_name = "qemu-system-arm"
        elif xtarget.is_mips():
            binary_name = "qemu-system-mips64"
        elif xtarget.is_riscv32():
            binary_name = "qemu-system-riscv32"
        elif xtarget.is_riscv64():
            binary_name = "qemu-system-riscv64"
        elif xtarget.is_any_x86():
            binary_name = "qemu-system-x86_64"
        else:
            raise ValueError("Invalid xtarget" + str(xtarget))
        return config.output_root / "upstream-qemu/bin" / binary_name


class BuildQEMU(BuildQEMUBase):
    target = "qemu"
    repository = GitRepository("https://github.com/CTSRD-CHERI/qemu.git", default_branch="qemu-cheri")
    default_targets = (
        "aarch64-softmmu,morello-softmmu,"
        "mips64-softmmu,mips64cheri128-softmmu,"
        "riscv64-softmmu,riscv64cheri-softmmu,riscv32-softmmu,riscv32cheri-softmmu,"
        "x86_64-softmmu"
    )
    # Turn on unaligned loads/stores by default
    unaligned = BoolConfigOption("unaligned", show_help=False, help="Permit un-aligned loads/stores", default=False)
    statistics = BoolConfigOption(
        "statistics",
        show_help=True,
        help="Collect statistics on out-of-bounds capability creation.",
    )

    @classmethod
    def qemu_binary_for_target(cls, xtarget: CrossCompileTarget, config: CheriConfig):
        if xtarget.is_riscv(include_purecap=True):
            # Always use the CHERI qemu even for plain riscv:
            binary_name = "qemu-system-riscv64cheri"
        elif xtarget.is_mips(include_purecap=True):
            binary_name = "qemu-system-mips64cheri128"
        elif xtarget.is_aarch64(include_purecap=True):
            # Only use CHERI QEMU for Morello for now, not AArch64 too, until
            # we can rely on builds being up-to-date
            if xtarget.is_hybrid_or_purecap_cheri([CPUArchitecture.AARCH64]):
                binary_name = "qemu-system-morello"
            else:
                binary_name = "qemu-system-aarch64"
        elif xtarget.is_any_x86():
            binary_name = "qemu-system-x86_64"
        else:
            raise ValueError("Invalid xtarget" + str(xtarget))
        return config.qemu_bindir / os.getenv("QEMU_CHERI_PATH", binary_name)

    @classmethod
    def get_firmware_dir(cls, caller: SimpleProject, cross_target: "Optional[CrossCompileTarget]" = None):
        return cls.get_install_dir(caller, cross_target=cross_target) / "share/qemu"

    def setup(self):
        super().setup()
        if self.unaligned:
            self.COMMON_FLAGS.append("-DCHERI_UNALIGNED")
        if self.statistics:
            self.COMMON_FLAGS.append("-DDO_CHERI_STATISTICS=1")
        if self.build_type == BuildType.DEBUG:
            self.COMMON_FLAGS.append("-DENABLE_CHERI_SANITIY_CHECKS=1")
        # the capstone disassembler doesn't support CHERI instructions:
        self.configure_args.append("--disable-capstone")
        # Linux/BSD-user is not supported for CHERI (yet)
        self.configure_args.append("--disable-bsd-user")
        self.configure_args.append("--disable-linux-user")
        # TODO: tests:
        # noinspection PyUnreachableCode
        if False:
            # Get all the required compilation flags for the TCG tests
            fake_project = SimpleNamespace()
            fake_project.config = self.config
            fake_project.needs_sysroot = False
            fake_project.warning = self.warning
            fake_project.target = "qemu-tcg-tests"
            # noinspection PyTypeChecker
            tgt_info_mips = BaremetalFreestandingTargetInfo(CompilationTargets.FREESTANDING_MIPS64, fake_project)
            # noinspection PyTypeChecker
            tgt_info_riscv64 = BaremetalFreestandingTargetInfo(CompilationTargets.FREESTANDING_RISCV64, fake_project)
            self.configure_args.extend(
                [
                    "--cross-cc-mips=" + str(tgt_info_mips.c_compiler),
                    "--cross-cc-cflags-mips="
                    + self.commandline_to_str(tgt_info_mips.get_essential_compiler_and_linker_flags()).replace(
                        "=",
                        " ",
                    ),
                    "--cross-cc-riscv64=" + str(tgt_info_riscv64.c_compiler),
                    "--cross-cc-cflags-riscv64="
                    + self.commandline_to_str(tgt_info_riscv64.get_essential_compiler_and_linker_flags()).replace(
                        "=",
                        " ",
                    ),
                ],
            )

    def install(self, **kwargs):
        super().install(**kwargs)
        # Delete the old Morello-QEMU files
        self._cleanup_old_files(
            self.config.morello_sdk_dir / "share/qemu",
            self.config.morello_sdk_dir / "share/applications/qemu.desktop",
            self.config.morello_sdk_dir / "libexec/qemu-bridge-helper",
            self.config.morello_sdk_dir / "libexec/virtfs-proxy-helper",
            self.config.morello_sdk_dir / "bin/elf2dmp",
            self.config.morello_sdk_dir / "bin/symbolize-cheri-trace.py",
            *(self.config.morello_sdk_dir / "bin").glob("qemu-*"),
            *self.config.morello_sdk_dir.rglob("share/icons/**/qemu.png"),
            *self.config.morello_sdk_dir.rglob("share/icons/**/qemu.bmp"),
            *self.config.morello_sdk_dir.rglob("share/icons/**/qemu.svg"),
        )
