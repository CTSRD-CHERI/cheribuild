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

        if self.compiling_for_riscv(include_purecap=True):
            self.configure_args.extend(
                [
                    # Use options that we use in CheriBSD ports.
                    "--disable-alsa",
                    "--disable-debug",
                    "--disable-frei0r",
                    "--disable-gcrypt",
                    "--disable-htmlpages",
                    "--disable-indev=v4l",
                    "--disable-ladspa",
                    "--disable-libaom",
                    "--disable-libaribb24",
                    "--disable-libass",
                    "--disable-libbluray",
                    "--disable-libbs2b",
                    "--disable-libcaca",
                    "--disable-libcdio",
                    "--disable-libcelt",
                    "--disable-libcodec2",
                    "--disable-libdavs2",
                    "--disable-libdc1394",
                    "--disable-libfdk-aac",
                    "--disable-libflite",
                    "--disable-libfribidi",
                    "--disable-libglslang",
                    "--disable-libgme",
                    "--disable-libgsm",
                    "--disable-libilbc",
                    "--disable-libjack",
                    "--disable-libjxl",
                    "--disable-libklvanc",
                    "--disable-libkvazaar",
                    "--disable-liblensfun",
                    "--disable-libmodplug",
                    "--disable-libmysofa",
                    "--disable-libopencore-amrnb",
                    "--disable-libopencore-amrwb",
                    "--disable-libopenh264",
                    "--disable-libopenjpeg",
                    "--disable-libopenmpt",
                    "--disable-libopenvino",
                    "--disable-libplacebo",
                    "--disable-libpulse",
                    "--disable-librabbitmq",
                    "--disable-librav1e",
                    "--disable-librist",
                    "--disable-librsvg",
                    "--disable-librtmp",
                    "--disable-librubberband",
                    "--disable-libshaderc",
                    "--disable-libsmbclient",
                    "--disable-libsnappy",
                    "--disable-libsoxr",
                    "--disable-libspeex",
                    "--disable-libsrt",
                    "--disable-libssh",
                    "--disable-libtensorflow",
                    "--disable-libtesseract",
                    "--disable-libtheora",
                    "--disable-libtwolame",
                    "--disable-libuavs3d",
                    "--disable-libv4l2",
                    "--disable-libvidstab",
                    "--disable-libvmaf",
                    "--disable-libvo-amrwbenc",
                    "--disable-libvpx",
                    "--disable-libx265",
                    "--disable-libxavs2",
                    "--disable-libxvid",
                    "--disable-libzimg",
                    "--disable-libzmq",
                    "--disable-libzvbi",
                    "--disable-lto",
                    "--disable-lv2",
                    "--disable-mbedtls",
                    "--disable-nonfree",
                    "--disable-nvenc",
                    "--disable-openal",
                    "--disable-opencl",
                    "--disable-opengl",
                    "--disable-openssl",
                    "--disable-outdev=v4l2",
                    "--disable-outdev=xv",
                    "--disable-pocketsphinx",
                    "--disable-sdl2",
                    "--disable-sndio",
                    "--disable-static",
                    "--disable-vapoursynth",
                    "--disable-vulkan",
                    "--enable-asm",
                    "--enable-gpl",
                    "--enable-iconv",
                    "--enable-network",
                    "--enable-optimizations",
                    "--enable-runtime-cpudetect",
                    "--enable-shared",
                    "--enable-version3",
                    # Disable swscale using assembly.
                    "--disable-swscale",
                    # Disable the spp filter that depends on pixblockdsp, which
                    # is implemented in assembly.
                    "--disable-filter=spp",
                    # Disable all encoders and decoders which might depend on
                    # components implemented in assembly (e.g., pixblockdsp).
                    "--disable-encoders",
                    "--disable-decoders",
                    # We would like to enable the following dependencies, once
                    # they are added to cheribuild for riscv64c.
                    # "--enable-fontconfig",
                    # "--enable-gmp",
                    # "--enable-gnutls",
                    # "--enable-lcms2",
                    # "--enable-libdav1d",
                    # "--enable-libdrm",
                    # "--enable-libfreetype",
                    # "--enable-libmp3lame",
                    # "--enable-libopus",
                    # "--enable-libsvtav1",
                    # "--enable-libvorbis",
                    # "--enable-libwebp",
                    # "--enable-libx264",
                    # "--enable-libxcb",
                    # "--enable-libxml2",
                    # "--enable-vaapi",
                    # "--enable-vdpau",
                ]
            )
        elif self.compiling_for_cheri():
            self.configure_args.append("--disable-neon")  # NEON asm needs some adjustments
            self.configure_args.append("--disable-inline-asm")  # NEON asm needs some adjustments
            self.configure_args.append("--disable-decoder=vp9")  # NEON asm needs some adjustments
            self.configure_args.append("--disable-decoder=dca")  # IMDCT_HALF value needs adjustments
