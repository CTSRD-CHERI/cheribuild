#!/usr/bin/env python3

import sys
import re
from pathlib import Path


scriptDir = Path(__file__).resolve().parent / "pycheribuild"  # type: Path

imports = []
fromImports = []
lines = []
handledFiles = []
emptyLines = 0


def insertLocalFile(line: str, srcFile: Path):
    if "def includeLocalFile(" in line:
        lines.append(line)
        return  # don't replace the function definition

    pattern = re.compile('includeLocalFile\\("(.*)"\\)')
    match = re.search(pattern, line)
    if not match or len(match.groups()) < 1:
        sys.exit("Invalid includeLocalFile:", line, file=sys.stderr)
        return
    relativePath = match.groups()[0]
    # print("Including file", relativePath, "from", srcFile.relative_to(scriptDir), file=sys.stderr)
    targetFile = scriptDir / relativePath
    newLine = line[0:match.start()] + "R\"\"\"\n"  # start raw string
    # print("New line is '", newLine, "'", sep="", file=sys.stderr)
    lines.append(newLine)
    with targetFile.open() as f:
        for line in f.readlines():
            lines.append(line)
    lines.append("\"\"\"\n")


def handleLine(line: str, srcFile: Path):
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
    if len(line.strip()) == 0:
        emptyLines += 1
        if emptyLines > 2:
            return  # don't add more than 2 empty lines
    else:
        emptyLines = 0

    if "includeLocalFile" in line:
        insertLocalFile(line, srcFile)
    else:
        lines.append(line)


def addFilteredFile(p: Path):
    # print("adding", p, file=sys.stderr)
    handledFiles.append(p)
    # TODO: filter
    with p.open("r") as f:
        for line in f.readlines():
            handleLine(line, p)


# append all the individual files in the right order
addFilteredFile(scriptDir / "colour.py")
addFilteredFile(scriptDir / "utils.py")
addFilteredFile(scriptDir / "configloader.py")
addFilteredFile(scriptDir / "chericonfig.py")
addFilteredFile(scriptDir / "targets.py")
addFilteredFile(scriptDir / "project.py")

# for now keep the original order
addFilteredFile(scriptDir / "projects/build_qemu.py")
addFilteredFile(scriptDir / "projects/binutils.py")
addFilteredFile(scriptDir / "projects/llvm.py")
addFilteredFile(scriptDir / "projects/cheribsd.py")
addFilteredFile(scriptDir / "projects/disk_image.py")
addFilteredFile(scriptDir / "projects/awk.py")
addFilteredFile(scriptDir / "projects/cmake.py")
addFilteredFile(scriptDir / "projects/cherios.py")
addFilteredFile(scriptDir / "projects/elftoolchain.py")
addFilteredFile(scriptDir / "projects/sdk.py")
addFilteredFile(scriptDir / "projects/run_qemu.py")
addFilteredFile(scriptDir / "projects/gnustep.py")
addFilteredFile(scriptDir / "projects/cheritrace.py")
addFilteredFile(scriptDir / "projects/cherivis.py")
addFilteredFile(scriptDir / "projects/pseudotargets.py")

# now make sure that all the projects were handled
for path in (scriptDir / "projects").iterdir():
    if path.name == "__pycache__":
        continue
    if path.name == "__init__.py":
        continue  # only needed when building as a module
    if path not in handledFiles:
        print("\x1b[1;31m", path, " not added!\x1b[0m", file=sys.stderr, sep="")

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
