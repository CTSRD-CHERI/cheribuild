#
# Copyright (c) 2024 Jessica Clarke
# All rights reserved.
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

from .project import AutotoolsProject, ComputedDefaultValue, GitRepository


class BuildOpenOCDBase(AutotoolsProject):
    do_not_add_to_targets: bool = True  # base class only
    _default_install_dir_fn = ComputedDefaultValue(
        function=lambda config, project: config.output_root / project.target,
        as_string=lambda cls: "$INSTALL_ROOT/" + cls.target,
    )

    def setup(self):
        super().setup()
        self.configure_args.extend(
            [
                "--enable-remote-bitbang",
                "--enable-jtag_vpi",
                "--enable-ftdi",
                "--enable-internal-jimtcl",
            ]
        )

    def configure(self, **kwargs):
        self.run_cmd("./bootstrap", cwd=self.source_dir)
        super().configure(**kwargs)


class BuildOpenOCD(BuildOpenOCDBase):
    repository = GitRepository("https://github.com/openocd-org/openocd.git")


# noinspection PyPep8Naming
class BuildRISCV_OpenOCD(BuildOpenOCDBase):  # noqa: N801
    repository = GitRepository("https://github.com/riscv-collab/riscv-openocd.git")


class BuildAlliance_RISCV_OpenOCD(BuildOpenOCDBase):
    repository = GitRepository("https://github.com/CHERI-Alliance/openocd.git")
