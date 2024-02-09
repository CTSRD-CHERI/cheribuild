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
from .crosscompileproject import CrossCompileAutotoolsProject, GitRepository


class BuildFfmpeg(CrossCompileAutotoolsProject):
    target = "ffmpeg"
    repository = GitRepository(
        "https://github.com/FFmpeg/FFmpeg.git",
        temporary_url_override="https://github.com/arichardson/FFmpeg.git",
        url_override_reason="Needs --disable-neon workarounds",
    )
    ctest_script_extra_args = ["--test-timeout", str(180 * 60)]  # Tests take a long time to run
    add_host_target_build_config_options = False  # doesn't understand --host
    _configure_supports_variables_on_cmdline = False  # not really an autotools project
    _autotools_add_default_compiler_args = False  # not really an autotools project

    def setup(self):
        super().setup()
        cflags = self.default_compiler_flags
        self.configure_args.extend(
            [
                f"--ar={self.target_info.ar}",
                f"--as={self.CC}",
                f"--cc={self.CC}",
                f"--cxx={self.CXX}",
                f"--ld={self.CC}",
                f"--nm={self.target_info.nm}",
                f"--ranlib={self.target_info.ranlib}",
                f"--strip={self.target_info.strip_tool}",
                f"--extra-cflags={self.commandline_to_str(cflags + self.CFLAGS)}",
                f"--extra-cxxflags={self.commandline_to_str(cflags + self.CXXFLAGS)}",
                f"--extra-ldflags={self.commandline_to_str(self.default_ldflags + self.LDFLAGS)}",
                "--enable-pic",
                "--disable-doc",
                "--disable-ffplay",
                "--disable-ffprobe",
            ]
        )
        if not self.compiling_for_host():
            self.configure_args.extend(
                [
                    "--enable-cross-compile",
                    f"--host-cc={self.host_CC}",
                    f"--host-ld={self.host_CC}",
                    f"--arch={self.crosscompile_target.cpu_architecture.value}",
                    f"--target-os={self.target_info.cmake_system_name.lower()}",
                ]
            )

        if self.compiling_for_cheri():
            self.configure_args.append("--disable-neon")  # NEON asm needs some adjustments
            self.configure_args.append("--disable-inline-asm")  # NEON asm needs some adjustments
            self.configure_args.append("--disable-decoder=vp9")  # NEON asm needs some adjustments
            self.configure_args.append("--disable-decoder=dca")  # IMDCT_HALF value needs adjustments
