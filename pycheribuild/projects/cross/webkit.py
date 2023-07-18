#
# Copyright (c) 2020 Alex Richardson
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

from enum import Enum

from pycheribuild.projects.cross.crosscompileproject import CrossCompileCMakeProject, DefaultInstallDir, GitRepository
from pycheribuild.projects.simple_project import BoolConfigOption


class JsBackend(Enum):
    CLOOP = "cloop"
    TIER1ASM = "tier1asm"
    TIER2ASM = "tier2asm"


class BuildMorelloWebkit(CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/CTSRD-CHERI/webkit",
                               default_branch="master")
    default_directory_basename = "webkit"
    target = "morello-webkit"
    dependencies = ("icu4c",)
    native_install_dir = DefaultInstallDir.DO_NOT_INSTALL
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    tier2ptrliterals = BoolConfigOption(
        "tier2ptrliterals", default=True, show_help=True,
        help="When true pointers are represented as atomic literals and loaded as data and when false pointers "
             "are represented as numeric values which can be splitted and are encoded into instructions. "
             "This option only affects the non-purecap tier2 backend.")
    jsheapoffsets = BoolConfigOption(
        "jsheapoffsets", default=False, show_help=True,
        help="Use offsets into the JS heap for object references instead of capabilities. "
             "This option only affects the purecap backends.")

    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)
        cls.backend = cls.add_config_option(
            "backend", kind=JsBackend,
            default=JsBackend.CLOOP, enum_choice_strings=[t.value for t in JsBackend],
            show_help=True, help="The JavaScript backend to use for building WebKit")

    @property
    def build_dir_suffix(self):
        # Use a different build dir for the various build options
        result = "-" + str(self.backend.value)
        if self.crosscompile_target.is_cheri_purecap():
            result += "-offsets" if self.jsheapoffsets else "-caps"
        elif self.backend == JsBackend.TIER2ASM and self.tier2ptrliterals:
            result += "-ptrliterals"
        return result

    def setup(self):
        super().setup()

        # Fix build for ICU >= 68
        self.COMMON_FLAGS.append("-DU_DEFINE_FALSE_AND_TRUE")

        if self.crosscompile_target.is_aarch64(include_purecap=True):
            # XXX: Morello hybrid gives relocation errors without this, add to purecap
            # as well for comparability
            self.COMMON_FLAGS.append("-fPIC")

        self.add_cmake_options(
            PORT="JSCOnly",
            DEVENTLOOP_TYPE="None",
            SHOULD_INSTALL_JS_SHELL=True,
            ENABLE_X11_TARGET=False,
            ENABLE_OPENGL=False,
            USE_LIBHYPHEN=False,
            DEVELOPER_MODE=True,
            ENABLE_VIDEO=False,
            ENABLE_XSLT=False,
            ENABLE_GEOLOCATION=False,
            ENABLE_DEVICE_ORIENTATION=False,
            USE_GSTREAMER=False,
            USE_LD_GOLD=False,
            ENABLE_API_TESTS=False,
            ENABLE_PRINT_SUPPORT=False,
            ENABLE_WEBKIT2=False,
            ENABLE_DFG_JIT=False,
            ENABLE_FTL_JIT=False,
            ENABLE_YARR_JIT=False,
            ENABLE_SAMPLING_PROFILER=False,
            CHERI_PURE_CAPABILITY=self.crosscompile_target.is_cheri_purecap(),
            )

        if self.crosscompile_target.is_cheri_purecap():
            # TODO: we can get this from the pre-processor instead
            self.add_cmake_options(CHERI_CAPABILITY_SIZE=self.target_info.capability_size_in_bits)
            self.add_cmake_options(ENABLE_JSHEAP_CHERI_OFFSET_REFS=self.jsheapoffsets)

        # To allow benchmark comparability, we use system malloc and the continuous arena for
        # all configuration options:
        self.add_cmake_options(USE_SYSTEM_MALLOC=True, USE_CONTINUOUS_ARENA=True)

        # Add options for each backend
        if self.backend == JsBackend.CLOOP:
            self.add_cmake_options(ENABLE_C_LOOP=True, ENABLE_ASSEMBLER=False, ENABLE_JIT=False,
                                   ENABLE_JIT_ARM64_EMBED_POINTERS_AS_ALIGNED_LITERALS=False)
        elif self.backend == JsBackend.TIER1ASM:
            self.add_cmake_options(ENABLE_C_LOOP=False, ENABLE_ASSEMBLER=True, ENABLE_JIT=False,
                                   ENABLE_JIT_ARM64_EMBED_POINTERS_AS_ALIGNED_LITERALS=True)
        elif self.backend == JsBackend.TIER2ASM:
            self.add_cmake_options(ENABLE_C_LOOP=False, ENABLE_ASSEMBLER=True, ENABLE_DISASSEMBLER=True,
                                   ENABLE_JIT=True)
            if self.crosscompile_target.is_cheri_purecap():
                if self.jsheapoffsets:
                    self.warning("integer heap offsets are not yet supported for the tier 2 backend!")
                self.add_cmake_options(ENABLE_JIT_ARM64_EMBED_POINTERS_AS_ALIGNED_LITERALS=True,
                                       ENABLE_JSHEAP_CHERI_OFFSET_REFS=False)
            else:
                self.add_cmake_options(ENABLE_JIT_ARM64_EMBED_POINTERS_AS_ALIGNED_LITERALS=self.tier2ptrliterals)

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("Running host tests not implemented")
        else:
            # full disk image to get icu library
            self.target_info.run_cheribsd_test_script("run_morello_webkit_tests.py", mount_sourcedir=True,
                                                      use_full_disk_image=True)
