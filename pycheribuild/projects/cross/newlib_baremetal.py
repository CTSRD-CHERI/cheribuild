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
import tempfile

from .crosscompileproject import *
from ...utils import IS_MAC, runCmd


class BuildNewlibBaremetal(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/newlib")
    target = "newlib"
    project_name = "newlib-baremetal"
    make_kind = MakeCommandKind.GnuMake
    needs_sysroot = False  # We are building newlib so we don't need a sysroot
    add_host_target_build_config_options = False
    defaultOptimizationLevel = ["-O2"]
    _configure_supports_libdir = False
    _configure_supports_variables_on_cmdline = True
    crossInstallDir = CrossInstallDir.SDK
    supported_architectures = [CompilationTargets.BAREMETAL_NEWLIB_MIPS64,
                               CompilationTargets.BAREMETAL_NEWLIB_MIPS64_PURECAP]
    # build_in_source_dir = True  # we have to build in the source directory

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.locale_support = cls.add_bool_option("locale-support", show_help=False, help="Build with locale support")

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._installPrefix = self._installPrefix.parent  # newlib install already appends the triple
        self._installDir = self._installDir.parent  # newlib install already appends the triple
        print("after:", self.installDir, "_=", self._installDir, "dest=", self.destdir, "real=", self.real_install_root_dir)
        #self.configureCommand = Path("/this/path/does/not/exist")
        self.configureCommand = self.sourceDir / "configure"
        # FIXME: how can I force it to run a full configure step (this is needed because it runs the newlib configure
        # step during make all rather than during ./configure
        self.make_args.env_vars["newlib_cv_ldbl_eq_dbl"] = "yes"
        # ensure that we don't fall back to system headers (but do use stddef.h from clang...)
        self.COMMON_FLAGS.extend(["--sysroot", "/this/path/does/not/exist"])
        if IS_MAC:
            self.add_configure_vars(LDFLAGS="-fuse-ld=/usr/bin/ld")

    # def install(self, **kwargs):
    #     # self.runMakeInstall(cwd=self.buildDir / "newlib")
    #     self.runMakeInstall(cwd=self.buildDir / "libgloss")

    # def compile(self, **kwargs):
    #     # super().compile(cwd=self.buildDir / "newlib")
    #     self.make_args.env_vars["MULTILIB"] = self.target_cflags + " -mabicalls"
    #     super().compile(cwd=self.buildDir / "libgloss")

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()

    def add_configure_vars(self, **kwargs):
        # newlib is annoying, we need to pass all these arguments to make as well because it won't run all
        # the configure steps...
        for k, v in kwargs.items():
            self.add_configure_env_arg(k, v)
            # self.make_args.env_vars[k] = str(v)
            if k.endswith("_FOR_BUILD"):
                k2 = k[0:-len("_FOR_BUILD")]
                self.add_configure_env_arg(k2, v)
                # self.make_args.env_vars[k2] = str(v)

    def configure(self):
        target_cflags = commandline_to_str(self.target_info.essential_compiler_and_linker_flags + self.COMMON_FLAGS)
        bindir = self.sdk_bindir

        self.add_configure_vars(
            AS_FOR_TARGET=str(self.CC),  # + target_cflags,
            CC_FOR_TARGET=str(self.CC),  # + target_cflags,
            CXX_FOR_TARGET=str(self.CXX),  # + target_cflags,
            AR_FOR_TARGET=bindir / "ar", STRIP_FOR_TARGET=bindir / "strip",
            OBJCOPY_FOR_TARGET=bindir / "objcopy", RANLIB_FOR_TARGET=bindir / "ranlib",
            OBJDUMP_FOR_TARGET=bindir / "llvm-objdump",
            READELF_FOR_TARGET=bindir / "readelf", NM_FOR_TARGET=bindir / "nm",
            # Set all the flags:
            CFLAGS_FOR_TARGET=target_cflags,
            CCASFLAGS_FOR_TARGET=target_cflags,
            FLAGS_FOR_TARGET=target_cflags,
            # Some build tools are needed:
            CC_FOR_BUILD=self.host_CC,
            CXX_FOR_BUILD=self.host_CXX,
            # long double is the same as double
            newlib_cv_ldbl_eq_dbl="yes",
            LD_FOR_TARGET=str(self.target_info.linker), LDFLAGS_FOR_TARGET="-fuse-ld=" + str(self.target_info.linker),
            )
        self.configureArgs.extend([
            "--enable-malloc-debugging",
            "--enable-newlib-long-time_t",  # we want time_t to be long and not int!
            "--enable-newlib-io-c99-formats",
            "--enable-newlib-io-long-long",
            # --enable-newlib-io-pos-args (probably not needed)
            "--disable-newlib-io-long-double",  # we don't need this, MIPS long double == double
            "--enable-newlib-io-float",
            # "--disable-newlib-supplied-syscalls"
            "--disable-libstdcxx",  # not sure if this is needed

            # we don't have any multithreading support on baremetal
            "--disable-newlib-multithread",

            "--enable-newlib-global-atexit",  # TODO: is this needed?
            # --enable-newlib-nano-malloc (should we do this?)
            "--disable-multilib",

            # TODO: smaller lib? "--enable-target-optspace"

            # FIXME: these don't seem to work
            "--enable-serial-build-configure",
            "--enable-serial-target-configure",
            "--enable-serial-host-configure",
        ])

        if self.locale_support:
            # needed for locale support
            self.configureArgs.append("--enable-newlib-mb")
            self.configureArgs.append("--enable-newlib-iconv")
        else:
            self.configureArgs.append("--disable-newlib-mb")
            self.configureArgs.append("--disable-newlib-iconv")

        # won't work: self.configureArgs.append("--host=" + self.target_info.target_triple)
        self.configureArgs.append("--target=" + self.target_info.target_triple)
        self.configureArgs.append("--disable-multilib")
        self.configureArgs.append("--with-newlib")
        super().configure()

    def install(self, **kwargs):
        super().install(**kwargs)
        if self.compiling_for_cheri():
            # create some symlinks to make the current CMakeProject infrastructure happy
            root_dir = self.installDir / self.target_info.target_triple
            self.makedirs(root_dir / "usr")
            self.createSymlink(root_dir / "lib", root_dir / "usr/libcheri")
            self.createSymlink(root_dir / "lib", root_dir / "libcheri")

    def run_tests(self):
        with tempfile.TemporaryDirectory(prefix="cheribuild-" + self.target + "-") as td:
            self.writeFile(Path(td, "main.c"), contents="""
#include <stdio.h>
int main(int argc, char** argv) {
  for (int i = 0; i < argc; i++) {
    printf("argv[%d] = '%s'\\n", i, argv[i]);
  }
}
""", overwrite=True)
            test_exe = Path(td, "test.exe")
            # FIXME: CHERI helloworld
            compiler_flags = self.target_info.essential_compiler_and_linker_flags + self.COMMON_FLAGS + [
                "-Wl,-T,qemu-malta.ld", "-Wl,-verbose", "--sysroot=" + str(self.sdk_sysroot)]
            runCmd([self.sdk_bindir / "clang", "main.c", "-o", test_exe] + compiler_flags + ["-###"], cwd=td)
            runCmd([self.sdk_bindir / "clang", "main.c", "-o", test_exe] + compiler_flags, cwd=td)
            runCmd(self.sdk_bindir / "llvm-readobj", "-h", test_exe)
            from ..build_qemu import BuildQEMU
            runCmd(self.sdk_sysroot / "bin/run_with_qemu.py", "--qemu", BuildQEMU.qemu_binary(self),
                   "--timeout", "20", test_exe, "HELLO", "WORLD")
