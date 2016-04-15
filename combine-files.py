#!/usr/bin/env python3

import sys
from pathlib import Path

scriptDir = Path(__file__).resolve().parent / "cheribuild"  # type: Path

imports = []
fromImports = []
lines = []
handledFiles = []
emptyLines = 0

def addFilteredFile(p: Path):
    # print("adding", p, file=sys.stderr)
    handledFiles.append(p)
    # TODO: filter
    with p.open("r") as f:
        for line in f.readlines():
            if line.endswith("# no-combine\n"):
                continue
            if line.startswith("import "):
                imports.append(line)
            elif line.startswith("from "):
                if line.startswith("from ."):
                    continue  # no need to add the local imports
                else:
                    fromImports.append(line)
            # elif line.starts("__all__"):
            #     continue
            else:
                global emptyLines
                if len(line.strip()) == 0:
                    emptyLines += 1
                    if emptyLines > 2:
                        continue  # don't add more than 2 empty lines
                else:
                    emptyLines = 0
                lines.append(line)


# append all the individual files in the right order
addFilteredFile(scriptDir / "colour.py")
addFilteredFile(scriptDir / "utils.py")
addFilteredFile(scriptDir / "configloader.py")
addFilteredFile(scriptDir / "chericonfig.py")
addFilteredFile(scriptDir / "project.py")

# for now keep the original order
addFilteredFile(scriptDir / "projects/build_qemu.py")
addFilteredFile(scriptDir / "projects/binutils.py")
addFilteredFile(scriptDir / "projects/llvm.py")
addFilteredFile(scriptDir / "projects/cheribsd.py")
addFilteredFile(scriptDir / "projects/disk_image.py")
addFilteredFile(scriptDir / "projects/awk.py")
addFilteredFile(scriptDir / "projects/elftoolchain.py")
addFilteredFile(scriptDir / "projects/sdk.py")
addFilteredFile(scriptDir / "projects/run_qemu.py")

# now make sure that all the projects were handled
for path in (scriptDir / "projects").iterdir():
    if path.name == "__pycache__":
        continue
    if path not in handledFiles:
        print("\x1b[1;31m", path, " not added!\x1b[0m", file=sys.stderr, sep="")

# targets must come after all the projects have been defined
addFilteredFile(scriptDir / "targets.py")
# now add the main() function
addFilteredFile(scriptDir / "__main__.py")

# print(len(imports), len(set(imports)), file=sys.stderr)
imports = sorted(set(imports))
fromImports = sorted(set(fromImports))
# print(imports, file=sys.stderr)
# print(fromImports, file=sys.stderr)

fullFile = ("#!/usr/bin/env python3\n" +
            "".join(imports) +
            "".join(fromImports) +
            "\n# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri\n" +
            "".join(lines))
print(fullFile)
