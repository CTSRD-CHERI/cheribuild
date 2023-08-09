#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

do_push = os.getenv("DO_PUSH", None) is not None
OLD_NAMES = ["morello-20.10"]
TAG_NAME = "morello-2020.10"


def add_tag_and_push(repo, tag_name=None):
    if tag_name is None:
        tag_name = TAG_NAME
    # remote = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "--symbolic-full-name",
    #                                     "@{upstream}"]).strip().decode("utf-8").split("/")[2]
    subprocess.check_call(["git", "-C", str(repo), "tag", "-f", tag_name, "HEAD"])
    for old_name in OLD_NAMES:
        subprocess.call(["git", "-C", str(repo), "tag", "--delete", old_name])
    subprocess.check_call(["git", "--no-pager", "-C", str(repo), "log", "-1"])
    if do_push:
        err = subprocess.call(["git", "-C", str(repo), "push", "origin", tag_name, "--no-verify"])
        if err == 0:
            for old_name in OLD_NAMES:
                subprocess.call(["git", "-C", str(repo), "push", "--delete", "origin", old_name, "--no-verify"])
        # fall back to ctsrd
        if err != 0:
            err = subprocess.call(["git", "-C", str(repo), "push", "ctsrd", tag_name, "--no-verify"])
            if err == 0:
                for old_name in OLD_NAMES:
                    subprocess.call(["git", "-C", str(repo), "push", "--delete", "ctsrd", old_name, "--no-verify"])
        if err != 0:
            sys.exit("Failed to push tag to " + str(repo))


add_tag_and_push(Path(__file__).parent.parent, tag_name="morello-2020.10")
for r in [
    "cheribsd",
    "gdb",
    # firmware forks where we can't use the morello/release-1.0 tag:
    "morello-trusted-firmware-a",
    "morello-edk2/edk2-platforms",
]:
    add_tag_and_push(Path.home() / "cheri", r)
