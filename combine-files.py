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
from pathlib import Path

script_dir: Path = Path(__file__).resolve().parent / "pycheribuild"

imports: "list[str]" = []
from_imports: "list[str]" = []
lines: "list[str]" = []
handled_files: "list[Path]" = []
ignored_files = [script_dir / "jenkins.py", script_dir / "config/jenkinsconfig.py"]
empty_lines = 0


def insert_local_file(line: str, _: Path):
    if "def include_local_file(" in line:
        lines.append(line)
        return  # don't replace the function definition

    pattern = re.compile('include_local_file\\("(.*)"\\)')
    match = re.search(pattern, line)
    if not match or len(match.groups()) < 1:
        sys.exit("Invalid include_local_file: " + line)
    relative_path = match.groups()[0]
    # print("Including file", relative_path, "from", src_file.relative_to(script_dir), file=sys.stderr)
    target_file = script_dir / relative_path
    new_line = line[0 : match.start()] + 'R"""\n'  # start raw string
    # print("New line is '", new_line, "'", sep="", file=sys.stderr)
    lines.append(new_line)
    with target_file.open() as f:
        for includedline in f.readlines():
            lines.append(includedline)
    lines.append('"""' + line[match.end() :])


def handle_line(line: str, src_file: Path, continued_import: bool):
    if line.startswith("#"):
        # TODO: ignore all comments?
        return False  # ignore top-level comments (e.g. copyright headers)
    global empty_lines  # noqa: PLW0603
    if continued_import:
        if line.strip().endswith(")"):
            return False
        return True  # continued import line
    if line.endswith("# no-combine\n"):
        return False
    if line.startswith("import "):
        imports.append(line)
        return False
    if line.startswith("from "):
        # no need to add the local imports if we are combining
        if not line.startswith("from ."):
            from_imports.append(line)
        return "import (" in line and not line.strip().endswith(")")  # continued if there is an opening paren
    elif line.lstrip().startswith("from ."):
        # skip relative imports inside functions
        return "import (" in line and not line.strip().endswith(")")  # continued if there is an opening paren
    if len(line.strip()) == 0:
        empty_lines += 1
        if empty_lines > 2:
            return False  # don't add more than 2 empty lines
    else:
        empty_lines = 0

    if "include_local_file" in line:
        insert_local_file(line, src_file)
    else:
        lines.append(line)
    return False


def add_filtered_file(p: Path):
    # print("adding", p, file=sys.stderr)
    handled_files.append(p)
    # TODO: filter
    with p.open("r") as f:
        continued_import = False
        for line in f.readlines():
            continued_import = handle_line(line, p, continued_import)


def check_all_files_used(directory: Path):
    for p in directory.iterdir():
        if not p.is_file():
            continue
        if p.name == "__init__.py" or p.name == "__main__.py":
            continue  # only needed when building as a module
        if p not in handled_files and p not in ignored_files:
            print("\x1b[1;31m", p, " not added!\x1b[0m", file=sys.stderr, sep="")


# append all the individual files in the right order
add_filtered_file(script_dir / "colour.py")
add_filtered_file(script_dir / "utils.py")
add_filtered_file(script_dir / "mtree.py")
add_filtered_file(script_dir / "config/loader.py")
add_filtered_file(script_dir / "config/target_info.py")
add_filtered_file(script_dir / "config/chericonfig.py")
add_filtered_file(script_dir / "config/defaultconfig.py")
add_filtered_file(script_dir / "targets.py")
add_filtered_file(script_dir / "filesystemutils.py")
add_filtered_file(script_dir / "projects/project.py")
add_filtered_file(script_dir / "qemu_utils.py")
add_filtered_file(script_dir / "config/compilation_targets.py")

# for now keep the original order
add_filtered_file(script_dir / "projects/build_qemu.py")
add_filtered_file(script_dir / "projects/binutils.py")
add_filtered_file(script_dir / "projects/llvm.py")

add_filtered_file(script_dir / "projects/bmake.py")
add_filtered_file(script_dir / "projects/bsdtar.py")
add_filtered_file(script_dir / "projects/cmake.py")
add_filtered_file(script_dir / "projects/cherios.py")
add_filtered_file(script_dir / "projects/cherisim.py")
add_filtered_file(script_dir / "projects/cheritrace.py")
add_filtered_file(script_dir / "projects/makefs_linux.py")
add_filtered_file(script_dir / "projects/qtcreator.py")
add_filtered_file(script_dir / "projects/kdevelop.py")
add_filtered_file(script_dir / "projects/bear.py")
add_filtered_file(script_dir / "projects/go.py")
add_filtered_file(script_dir / "projects/sail.py")
add_filtered_file(script_dir / "projects/soaap.py")
add_filtered_file(script_dir / "projects/effectivesan.py")
add_filtered_file(script_dir / "projects/softboundcets.py")
add_filtered_file(script_dir / "projects/valgrind.py")
add_filtered_file(script_dir / "projects/ninja.py")
add_filtered_file(script_dir / "projects/openradtool.py")
add_filtered_file(script_dir / "projects/cheri_afl.py")

# First three need to be in order, then add all others
cross_files = [
    (script_dir / "projects/cross/cheribsd.py").resolve(),
    (script_dir / "projects/cross/crosscompileproject.py").resolve(),
]
for file in sorted((script_dir / "projects/cross").glob("*.py")):
    path = file.resolve()
    if path not in cross_files:
        cross_files.append(path)

for file in cross_files:
    add_filtered_file(file)

# disk-image, sdk and run_qemu must come after cheribsd as they use CheriBSD.rootfs_dir
add_filtered_file(script_dir / "projects/disk_image.py")
add_filtered_file(script_dir / "projects/syzkaller.py")
add_filtered_file(script_dir / "projects/sdk.py")
add_filtered_file(script_dir / "projects/spike.py")
add_filtered_file(script_dir / "projects/run_qemu.py")
add_filtered_file(script_dir / "projects/run_fpga.py")

# this one should not be needed
add_filtered_file(script_dir / "projects/samba.py")

# now make sure that all the projects were handled
check_all_files_used(script_dir)
check_all_files_used(script_dir / "projects")
check_all_files_used(script_dir / "config")
check_all_files_used(script_dir / "projects/cross")

# now add the main() function
add_filtered_file(script_dir / "__main__.py")

# print(len(imports), len(set(imports)), file=sys.stderr)
imports = sorted(set(imports))
from_imports = sorted(set(from_imports))
# print(imports, file=sys.stderr)
# print(from_imports, file=sys.stderr)

full_file = (
    "#!/usr/bin/env python3\n"
    + "# PYTHON_ARGCOMPLETE_OK\n"
    + "".join(imports)
    + "".join(from_imports)
    + "\n# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri\n"
    + "".join(lines)
)
print(full_file)
