#!/usr/bin/env python3
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
import re
import sys
import typing
from pathlib import Path

scriptDir = Path(__file__).resolve().parent / "pycheribuild"  # type: Path

imports = []  # type: typing.List[str]
fromImports = []  # type: typing.List[str]
lines = []  # type: typing.List[str]
handledFiles = []  # type: typing.List[Path]
ignoredFiles = [scriptDir / "jenkins.py", scriptDir / "config/jenkinsconfig.py"]
emptyLines = 0


def insertLocalFile(line: str, srcFile: Path):
    if "def include_local_file(" in line:
        lines.append(line)
        return  # don't replace the function definition

    pattern = re.compile('include_local_file\\("(.*)"\\)')
    match = re.search(pattern, line)
    if not match or len(match.groups()) < 1:
        sys.exit("Invalid include_local_file: " + line)
    relative_path = match.groups()[0]
    # print("Including file", relative_path, "from", src_file.relative_to(scriptDir), file=sys.stderr)
    target_file = scriptDir / relative_path
    new_line = line[0:match.start()] + "R\"\"\"\n"  # start raw string
    # print("New line is '", new_line, "'", sep="", file=sys.stderr)
    lines.append(new_line)
    with target_file.open() as f:
        for includedline in f.readlines():
            lines.append(includedline)
    lines.append("\"\"\"" + line[match.end():])


def handleLine(line: str, src_file: Path):
    global emptyLines
    if line.endswith("# no-combine\n"):
        return
    if line.startswith("import "):
        imports.append(line)
        return
    if line.startswith("from "):
        # no need to add the local imports if we are combining
        if not line.startswith("from ."):
            fromImports.append(line)
        return
    elif line.lstrip().startswith("from ."):
        return  # skip relative imports inside functions
    if len(line.strip()) == 0:
        emptyLines += 1
        if emptyLines > 2:
            return  # don't add more than 2 empty lines
    else:
        emptyLines = 0

    if "include_local_file" in line:
        insertLocalFile(line, src_file)
    else:
        lines.append(line)


def addFilteredFile(p: Path):
    # print("adding", p, file=sys.stderr)
    handledFiles.append(p)
    # TODO: filter
    with p.open("r") as f:
        for line in f.readlines():
            handleLine(line, p)

def checkAllFilesUsed(directory: Path):
    for p in directory.iterdir():
        if not p.is_file():
            continue
        if p.name == "__init__.py" or p.name == "__main__.py":
            continue  # only needed when building as a module
        if p not in handledFiles and p not in ignoredFiles:
            print("\x1b[1;31m", p, " not added!\x1b[0m", file=sys.stderr, sep="")


# append all the individual files in the right order
addFilteredFile(scriptDir / "colour.py")
addFilteredFile(scriptDir / "utils.py")
addFilteredFile(scriptDir / "mtree.py")
addFilteredFile(scriptDir / "config/loader.py")
addFilteredFile(scriptDir / "config/target_info.py")
addFilteredFile(scriptDir / "config/chericonfig.py")
addFilteredFile(scriptDir / "config/defaultconfig.py")
addFilteredFile(scriptDir / "targets.py")
addFilteredFile(scriptDir / "filesystemutils.py")
addFilteredFile(scriptDir / "projects/project.py")

# for now keep the original order
addFilteredFile(scriptDir / "projects/build_qemu.py")
addFilteredFile(scriptDir / "projects/binutils.py")
addFilteredFile(scriptDir / "projects/llvm.py")

addFilteredFile(scriptDir / "projects/bmake.py")
addFilteredFile(scriptDir / "projects/bsdtar.py")
addFilteredFile(scriptDir / "projects/cmake.py")
addFilteredFile(scriptDir / "projects/cherios.py")
addFilteredFile(scriptDir / "projects/cherisim.py")
addFilteredFile(scriptDir / "projects/elftoolchain.py")
addFilteredFile(scriptDir / "projects/cheritrace.py")
addFilteredFile(scriptDir / "projects/makefs_linux.py")
addFilteredFile(scriptDir / "projects/qtcreator.py")
addFilteredFile(scriptDir / "projects/kdevelop.py")
addFilteredFile(scriptDir / "projects/bear.py")
addFilteredFile(scriptDir / "projects/cherivis.py")
addFilteredFile(scriptDir / "projects/gnustep.py")
addFilteredFile(scriptDir / "projects/go.py")
addFilteredFile(scriptDir / "projects/sail.py")
addFilteredFile(scriptDir / "projects/soaap.py")
addFilteredFile(scriptDir / "projects/effectivesan.py")
addFilteredFile(scriptDir / "projects/softboundcets.py")
addFilteredFile(scriptDir / "projects/valgrind.py")
addFilteredFile(scriptDir / "projects/ninja.py")
addFilteredFile(scriptDir / "projects/cheri_afl.py")

# cross compilation targets
addFilteredFile(scriptDir / "projects/cross/cheribsd.py")
addFilteredFile(scriptDir / "projects/cross/crosscompileproject.py")

# First three need to be in order, then add all others
cross_files = [
    (scriptDir / "projects/cross/cheribsd.py").resolve(),
    (scriptDir / "projects/cross/crosscompileproject.py").resolve(),
]
for file in sorted((scriptDir / "projects/cross").glob("*.py")):
    path = file.resolve()
    if path not in cross_files:
        cross_files.append(path)

for file in cross_files:
    addFilteredFile(file)

# disk-image, sdk and run_qemu must come after cheribsd as they use CheriBSD.rootfs_dir
addFilteredFile(scriptDir / "projects/disk_image.py")
addFilteredFile(scriptDir / "projects/syzkaller.py")
addFilteredFile(scriptDir / "projects/sdk.py")
addFilteredFile(scriptDir / "projects/spike.py")
addFilteredFile(scriptDir / "projects/run_qemu.py")
addFilteredFile(scriptDir / "projects/run_fpga.py")

# this one should not be needed
addFilteredFile(scriptDir / "projects/samba.py")

# now make sure that all the projects were handled
checkAllFilesUsed(scriptDir)
checkAllFilesUsed(scriptDir / "projects")
checkAllFilesUsed(scriptDir / "config")
checkAllFilesUsed(scriptDir / "projects/cross")

# now add the main() function
addFilteredFile(scriptDir / "__main__.py")

# print(len(imports), len(set(imports)), file=sys.stderr)
imports = sorted(set(imports))
fromImports = sorted(set(fromImports))
# print(imports, file=sys.stderr)
# print(fromImports, file=sys.stderr)

fullFile = ("#!/usr/bin/env python3\n" +
            "# PYTHON_ARGCOMPLETE_OK\n" +
            "".join(imports) +
            "".join(fromImports) +
            "\n# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri\n" +
            "".join(lines))
print(fullFile)
