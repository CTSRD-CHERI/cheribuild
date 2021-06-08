#
# Copyright (c) 2021 George V. Neville-Neil
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
import tempfile
from pathlib import Path
from typing import Optional

from pycheribuild.processutils import run_command
from pycheribuild.utils import default_make_jobs_count
from .cherisim import BuildBeriCtl, BuildCheriSim
from .project import CheriConfig, SimpleProject
from ..config.compilation_targets import CompilationTargets


repos = ["cheribuild", "cheribsd", "gdb",
         "morello-llvm-project", "morello-qemu",
         "morello-trusted-firmware-a", "qemu"]

class Tag(SimpleProject):
    project_name = "tag"
    
    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        cls.gittag = cls.add_config_option("gittag", help="Tag to apply")

    def __init__(self, config: CheriConfig):
        super().__init__(config)

    def process(self):
        source_root = self.config.source_root
        for repo in repos:
            run_command("git", "-C", repo, "tag", self.gittag, cwd=source_root)

class UnTag(SimpleProject):
    project_name = "untag"
    
    @classmethod
    def setup_config_options(cls, **kwargs):
        super().setup_config_options(**kwargs)

        cls.gittag = cls.add_config_option("gittag", help="Tag to apply")

    def __init__(self, config: CheriConfig):
        super().__init__(config)

    def process(self):
        source_root = self.config.source_root
        for repo in repos:
            run_command("git", "-C", repo, "tag", "-d", self.gittag, cwd=source_root)

class Release(SimpleProject):
    project_name = "release"
    do_not_add_to_targets = True

#    dependencies = ["cheribsd-mfs-root-kernel-mips64-hybrid"]
#    supported_architectures = [CompilationTargets.CHERIBSD_MIPS_HYBRID]

#    @classmethod
#    def setup_config_options(cls, **kwargs):
#        super().setup_config_options()
#        print("SETUP")
        
    def __init__(self, config: CheriConfig):
        super().__init__(config)

class MorelloRelease(Release):
    target = "morello-release"
    dependencies = ["morello-llvm", "cheribsd-morello-purecap",
                    "gdb-native",
                    "gdb-morello-hybrid-for-purecap-rootfs",
                    "arm-none-eabi-toolchain", "morello-acpica",
                    "morello-scp-firmware",
                    "morello-trusted-firmware",
                    "morello-flash-images",
                    "disk-image-morello-purecap"]
    def __init__(self, config: CheriConfig):
        super().__init__(config)
        install_script = Path(output_root, "install_and_run_fvp.sh")
        install_script.write_text("""#!/bin/sh
        dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
        exec "${dir}/cheribuild.py" install-morello-fvp run-fvp-morello-purecap "$@""")
        install_script.chmod(0o755)


    def process(self):
        output_root = self.config.output_root
        run_command("bsdtar", "-cavf", output_root / "release.tar.xz", "-C", output_root,
            "--options=xz:threads=" + str(default_make_jobs_count()),
            "--options=compression-level=9",  # reduce size a bit more
            "morello-sdk/firmware",
            "cheribsd-morello-purecap.img",
            "sources/cheribuild",
            "cheribuild.py",
            cwd="/")

        run_command("sha256sum", output_root / "release.tar.xz")

class RISCV64Release(Release):
    target = "riscv64-release"
    dependencies = ["gdb-native", "qemu",
                    "disk-image-riscv64-purecap"]
    
    def __init__(self, config: CheriConfig):
        super().__init__(config)

    def process(self):
        output_root = self.config.output_root
        source_root = self.config.source_root

        run_command("cp", "-a", source_root / "cheribuild", output_root)

        run_command("bsdtar", "-cavf", output_root / "release.tar.xz", "-C", output_root,
            "--options=xz:threads=" + str(default_make_jobs_count()),
            "--options=compression-level=9",  # reduce size a bit more
            "--exclude=*.git",
            "cheribsd-riscv64-purecap.img",
            "cheribuild",
            cwd="/")

        run_command("sha256sum", output_root / "release.tar.xz")
