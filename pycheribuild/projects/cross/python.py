#
# Copyright (c) 2019 Alex Richardson
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

import os

from .crosscompileproject import (
    BuildType,
    CompilationTargets,
    CrossCompileAutotoolsProject,
    DefaultInstallDir,
    GitRepository,
)
from ...utils import is_case_sensitive_dir


class BuildPython(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/cpython.git", default_branch="3.8", force_branch=True)
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    default_build_type = BuildType.RELWITHDEBINFO
    needs_native_build_for_crosscompile = True

    # build_in_source_dir = True  # Cannot build out-of-source

    def configure(self, **kwargs):
        # maybe interesting:   --with(out)-pymalloc    disable/enable specialized mallocs
        if self.should_include_debug_info:
            self.configure_args.append("--with-pydebug")
            # XXXAR: always add assertions?
            self.configure_args.append("--with-assertions")

        if self.compiling_for_cheri():
            # computed gotos currently crash the compiler...
            self.configure_args.append("--without-computed-gotos")
            self.configure_args.append("--without-pymalloc")  # use system malloc
        else:
            self.configure_args.append("--with-computed-gotos")

        # fails to cross-compile and does weird stuff on host (uses wrong python version?)
        self.configure_args.append("--without-ensurepip")
        if self.compiling_for_host() and self.compiling_for_cheri():
            self.check_required_system_tool("/usr/local64/bin/python3.8", freebsd="python38", compat_abi=True)
            # Can't use the local python build for bootstrapping tasks yet:
            self.add_configure_vars(PYTHON_FOR_BUILD="/usr/local64/bin/python3.8")
            self.add_configure_vars(PYTHON_FOR_REGEN="/usr/local64/bin/python3.8")

        if not self.compiling_for_host():
            self.configure_args.append("--without-doc-strings")  # should reduce size
            native_python = (
                self.get_instance_for_cross_target(CompilationTargets.NATIVE_NON_PURECAP, self.config).install_dir
                / "bin/python3"
            )
            if not native_python.exists():
                self.dependency_error(
                    "Native python3 doesn't exist, you must build the `python-native` target first.",
                    cheribuild_target="python",
                    cheribuild_xtarget=CompilationTargets.NATIVE_NON_PURECAP,
                )
            self.add_configure_vars(
                ac_cv_buggy_getaddrinfo="no",
                # Doesn't work since that remove all flags, need to set PATH instead
                # PYTHON_FOR_BUILD=str(native_python),
                # PYTHON_FOR_REGEN=str(native_python),
                PATH=str(native_python.parent) + ":" + os.getenv("PATH"),
                READELF=str(self.sdk_bindir / "llvm-readelf"),
                AR=str(self.sdk_bindir / "llvm-ar"),
                ac_cv_file__dev_ptmx="no",  # no /dev/ptmx file on cheribsd
                ac_cv_file__dev_ptc="no",  # no /dev/ptc file on cheribsd
            )
            # self.configure_environment["ac_cv_file__dev_ptmx+set"] = "set"
            # self.configure_environment["ac_cv_file__dev_ptc+set"] = "set"
            # TODO: do I need to set? ac_sys_release=13.0
        super().configure(**kwargs)

    def run_tests(self):
        # python build system adds .exe for case-insensitive dirs
        suffix = "" if is_case_sensitive_dir(self.build_dir) else ".exe"
        if self.compiling_for_host():
            self.run_cmd(
                self.build_dir / ("python" + suffix),
                "-m",
                "test",
                "-w",
                "--junit-xml=python-tests.xml",
                self.config.make_j_flag,
                cwd=self.build_dir,
            )
        else:
            # Python executes tons of system calls, hopefully using the benchmark kernel helps
            self.target_info.run_cheribsd_test_script(
                "run_python_tests.py",
                "--buildexe-suffix=" + suffix,
                mount_installdir=True,
                mount_sourcedir=True,
                use_benchmark_kernel_by_default=True,
            )
