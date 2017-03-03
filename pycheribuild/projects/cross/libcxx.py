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
from .crosscompileproject import *
from ...utils import statusUpdate


class BuildLibCXX(CrossCompileCMakeProject):
    repository = "https://github.com/RichardsonAlex/libcxx.git"

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.add_cmake_options(
            LIBCXX_ENABLE_SHARED=False,  # not yet
            LIBCXX_ENABLE_STATIC=True,
            LIBCXX_ENABLE_EXPERIMENTAL_LIBRARY=False,  # not yet
            LIBCXX_INCLUDE_TESTS=False,  # unit tests: not yet
            LIBCXX_INCLUDE_BENCHMARKS=False,
            LIBCXX_INCLUDE_DOCS=False,
            LIBCXX_CXX_ABI="none",  # don't use a c++ abi library
            # exceptions and rtti still missing:
            LIBCXX_ENABLE_EXCEPTIONS=False,
            LIBCXX_ENABLE_RTTI=False,
            # TODO: is this needed?
            LIBCXX_SYSROOT=config.sdkDir / "sysroot",

        )

    def install(self):
        statusUpdate("Not installing libc++, not ready yet")
