#
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright 2022 Alex Richardson
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
from .crosscompileproject import CrossCompileCMakeProject, GitRepository, BuildType


class BuildOpenCV(CrossCompileCMakeProject):
    target = "opencv"
    repository = GitRepository("https://github.com/opencv/opencv.git")
    dependencies = ["libpng", "libjpeg-turbo", "openjpeg"]
    # Not working yet, debug makes it easier to track down errors
    # Additionally, the Morello compiler crashes when building in Release mode.
    default_build_type = BuildType.DEBUG

    def update(self):
        super().update()
        # Also clone the test data for unit tests.
        test_repo = GitRepository("https://github.com/opencv/opencv_extra.git")
        test_repo.update(self, src_dir=self.source_dir / "opencv_extra")

    def setup(self):
        super().setup()
        # The 3rdparty embedded libpng triggers -Wcheri-capability-misuse, use the patched CHERI version instead
        self.add_cmake_options(BUILD_PNG=False, WITH_PNG=True)
        # Also prefer the external libjpeg-turbo, the bundled version may be too old.
        self.add_cmake_options(BUILD_JPEG=False, WITH_JPEG=True)
        # Building openjpeg from the bundled version should be fine, but using the
        # cheribuild version ensures we have all the dependencies.
        self.add_cmake_options(BUILD_OPENJPEG=False, WITH_JPEG=True)
        # Webp cannot be built for Morello as it uses vector intrinsics in a way that triggers a compiler crash.
        self.add_cmake_options(BUILD_WEBP=False, WITH_WEBP=False)  # doesn't compile for CHERI yet.
        self.add_cmake_options(WITH_PROTOBUF=False)  # doesn't compile for CHERI yet.
        self.add_cmake_options(WITH_ITT=False, BUILD_ITT=False)  # doesn't compile for CHERI yet.
        self.add_cmake_options(OPENCV_TEST_DATA_PATH=self.source_dir / "opencv_extra/testdata")
