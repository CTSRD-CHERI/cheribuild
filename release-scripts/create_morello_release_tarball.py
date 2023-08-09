#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause
#
# Copyright (c) 2020 Alex Richardson
#
# This work was supported by Innovate UK project 105694, "Digital Security by
# Design (DSbD) Technology Platform Prototype".
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
import argparse
import sys
import tempfile
from pathlib import Path

# noinspection PyProtectedMember
module_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(module_dir))
# noinspection PyProtectedMember
from pycheribuild.processutils import run_command  # noqa: E402
from pycheribuild.utils import GlobalConfig, default_make_jobs_count, warning_message  # noqa: E402


def fixme(tag):
    warning_message("need final tag not", tag)
    return tag


class Target:
    def __init__(self, name, tag, extra_args=None):
        self.name = name
        self.tag = tag
        self.extra_args = [] if extra_args is None else extra_args


# https://git.morello-project.org/morello/manifest/-/commit/9ef473098c0787883aa2c7a7cac498ce380e9c8f
targets = [
    Target("morello-llvm", "054c38b78badede5f9264c0f12200172f5eefefc"),  # should be "morello/release-1.0"
    Target("cheribsd-morello-purecap", "b126ea817bbeb7369b4dfe5fed62f9cebb4f2e62"),
    # XXX: build the morello commit for ld.bfd instead of mips-cheri gdb?
    Target("gdb-native", "99492d2e8abd5c50708577ce4eeaa91bfcaae30f"),
    Target("gdb-morello-hybrid-for-purecap-rootfs", "875e6a8c672668669c518534e27ee90ed1874689"),
    # Firmware
    Target("arm-none-eabi-toolchain", None),  # no git repo
    Target("morello-acpica", None),  # Already hardcoded
    Target(
        "morello-uefi",
        "c70448e3c078dd05d202e770e3c69d53fcabb4df",  # should be "morello/release-1.0",
        ["--morello-uefi/edk2-platforms-git-revision", "387acd10643b5682398ef9e7c342ad19db6b4dd8"],
    ),
    Target("morello-scp-firmware", "6fad1d3e2f82b2ef51e55928ac3a678a75f64ef4"),  # should be "morello/release-1.0"
    Target("morello-trusted-firmware", "89bfc6f40d3195f48e379def8017ce74ba1120ec"),
    Target("morello-flash-images", None),  # no git repo
    # disk image
    Target("disk-image-morello-purecap", None),
]

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
parser.add_argument("--pretend", "-p", action="store_true")
parser.add_argument("--skip-build", action="store_true")
cmdline, remaining = parser.parse_known_args()

args = []

if cmdline.pretend:
    GlobalConfig.pretend = True
    print("Pretend mode enabled")
    args.append("--pretend")

for target in targets:
    args.append(target.name)
    if target.tag:
        args.append("--" + target.name + "/git-revision=" + target.tag)
    args.extend(target.extra_args)

args.append("--output-root")
output_root = Path(cmdline.output).absolute()
args.append(output_root / "output")
args.append("--build-root")
args.append(output_root / "build")
args.append("--disk-image/extra-files")
args.append(output_root / "extra-files")

command = [str((Path(__file__).parent / "cheribuild.py").absolute()), *args, *remaining]
with tempfile.NamedTemporaryFile() as tf:
    Path(tf.name).write_text("{}")
    command.append("--config-file=" + tf.name)  # default values please
    if not cmdline.skip_build:
        run_command([sys.executable, "-u", *command], give_tty_control=True)

# Add missing files to tarball
cheribuild_dir = Path(output_root, "sources/cheribuild")
if not Path(cheribuild_dir, ".git").exists():
    run_command(["git", "clone", "https://github.com/CTSRD-CHERI/cheribuild", str(cheribuild_dir)])
run_command(["git", "-C", str(cheribuild_dir), "pull", "--rebase"])
run_command(["git", "-C", str(cheribuild_dir), "reset", "--hard", "morello-20.10.1"])

run_command("ln", "-sfn", "sources/cheribuild/cheribuild.py", "cheribuild.py", cwd=output_root)
# TODO: download the fvp installer first?
install_script = Path(output_root, "install_and_run_fvp.sh")
install_script.write_text(
    """#!/bin/sh
dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${dir}/cheribuild.py" install-morello-fvp run-fvp-morello-purecap "$@"
""",
)
install_script.chmod(0o755)

# Install a config file that will be used by default:
Path(output_root, "sources/cheribuild/cheribuild.json").write_text(
    """{
   "source-root": "../../sources",
   "build-root": "../../build",
   "output-root": "../../output",
   "skip-update": true
}
""",
)
# Create the tarball
run_command(
    "bsdtar",
    "-cavf",
    output_root / "release.tar.xz",
    "-C",
    output_root,
    "--options=xz:threads=" + str(default_make_jobs_count()),
    "--options=compression-level=9",  # reduce size a bit more
    "output/morello-sdk/firmware",
    "output/cheribsd-morello-purecap.img",
    "sources/cheribuild",
    "cheribuild.py",
    install_script.relative_to(output_root),
    cwd="/",
)

run_command("sha256sum", output_root / "release.tar.xz")

print("DONE!")
