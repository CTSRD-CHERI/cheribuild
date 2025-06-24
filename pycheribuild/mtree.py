#
# Copyright (c) 2018 Alex Richardson
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

import collections
import fnmatch
import io
import os
import shlex
import stat
import sys
import typing
from collections import OrderedDict
from pathlib import Path, PurePath, PurePosixPath
from typing import Optional, Union

from .utils import fatal_error, status_update, warning_message


class MtreePath(PurePosixPath):
    def __str__(self):
        pathstr = super().__str__()
        if pathstr != ".":
            pathstr = "./" + pathstr
        return pathstr


class MtreeEntry:
    def __init__(self, path: MtreePath, attributes: "dict[str, str]"):
        self.path = path
        self.attributes = attributes

    def is_dir(self) -> bool:
        return self.attributes.get("type") == "dir"

    def is_file(self) -> bool:
        return self.attributes.get("type") == "file"

    @classmethod
    def parse(cls, line: str, contents_root: "Optional[Path]" = None) -> "MtreeEntry":
        elements = shlex.split(line)
        path = elements[0]
        # Ensure that the path is normalized:
        if path != ".":
            # print("Before:", path)
            assert path[:2] == "./"
            path = path[:2] + os.path.normpath(path[2:])
            # print("After:", path)
        path = MtreePath(path)
        attr_dict = OrderedDict()  # keep them in insertion order
        for k, v in map(lambda s: s.split(sep="=", maxsplit=1), elements[1:]):
            # ignore some tags that makefs doesn't like
            # sometimes there will be time with nanoseconds in the manifest, makefs can't handle that
            # also the tags= key is not supported
            if k in ("tags", "time"):
                continue
            # convert relative contents=keys to absolute ones
            if contents_root and k == "contents" and not os.path.isabs(v):
                v = str(contents_root / v)
            attr_dict[k] = v
        return MtreeEntry(path, attr_dict)
        # FIXME: use contents=

    @classmethod
    def parse_all_dirs_in_mtree(cls, mtree_file: Path) -> "list[MtreeEntry]":
        with mtree_file.open("r", encoding="utf-8") as f:
            result = []
            for line in f.readlines():
                if " type=dir" in line:
                    try:
                        result.append(MtreeEntry.parse(line))
                    except Exception as e:
                        warning_message("Could not parse line", line, "in mtree file", mtree_file, e)
            return result

    def __str__(self) -> str:
        def escape(s):
            # mtree uses strsvis(3) (in VIS_CSTYLE format) to encode path names containing non-printable characters.
            # Note: we only handle spaces here since we haven't seen any other special characters being use. If they do
            # exist in practise we can just update this code to handle them too.
            return s.replace(" ", "\\s")

        components = [escape(str(self.path))]
        for k, v in self.attributes.items():
            components.append(k + "=" + shlex.quote(v))
        return " ".join(components)

    def __repr__(self) -> str:
        return "<MTREE entry: " + str(self) + ">"


class MtreeSubtree(collections.abc.MutableMapping):
    def __init__(self):
        self.entry: MtreeEntry = None
        self.children: "dict[str, MtreeSubtree]" = OrderedDict()

    @staticmethod
    def _split_key(key):
        if isinstance(key, str):
            key = MtreePath(key)
        elif not isinstance(key, MtreePath):
            if isinstance(key, PurePath):
                key = MtreePath(key)
            else:
                raise TypeError
        if not key.parts:
            return None
        return key.parts[0], MtreePath(*key.parts[1:])

    def __getitem__(self, key):
        split = self._split_key(key)
        if split is None:
            if self.entry is None:
                raise KeyError
            return self.entry
        return self.children[split[0]][split[1]]

    def __setitem__(self, key, value):
        split = self._split_key(key)
        if split is None:
            self.entry = value
            return
        if split[0] not in self.children:
            self.children[split[0]] = MtreeSubtree()
        self.children[split[0]][split[1]] = value

    def __delitem__(self, key):
        split = self._split_key(key)
        if split is None:
            if self.entry is None:
                raise KeyError
            self.entry = None
            return
        del self.children[split[0]][split[1]]

    def __iter__(self):
        if self.entry is not None:
            yield MtreePath()
        for k, v in self.children.items():
            for k2 in v:
                yield MtreePath(k, k2)

    def __len__(self):
        ret = int(self.entry is not None)
        for c in self.children.values():
            ret += len(c)
        return ret

    def _glob(self, patfrags, prefix, *, case_sensitive=False):
        if len(patfrags) == 0:
            if self.entry is not None:
                yield prefix
            return
        patfrag = patfrags[0]
        patfrags = patfrags[1:]
        if len(patfrags) == 0 and len(patfrag) == 0:
            if self.entry is not None and self.entry.attributes["type"] == "dir":
                yield prefix
            return
        for k, v in self.children.items():
            if fnmatch.fnmatch(k, patfrag):
                yield from v._glob(patfrags, prefix / k, case_sensitive=case_sensitive)

    def glob(self, pattern, *, case_sensitive=False):
        if len(pattern) == 0:
            return
        head, tail = os.path.split(pattern)
        patfrags = [tail]
        while head:
            head, tail = os.path.split(head)
            patfrags.insert(0, tail)
        return self._glob(patfrags, MtreePath(), case_sensitive=case_sensitive)

    def _walk(self, top, prefix):
        split = self._split_key(top)
        if split is not None:
            return self.children[split[0]]._walk(split[1], prefix / split[0])
        if self.entry is not None and self.entry.attributes["type"] != "dir":
            return
        files = []
        dirs = []
        for k, v in self.children.items():
            if v.entry is not None and v.entry.attributes["type"] != "dir":
                files.append((k, v))
            else:
                dirs.append((k, v))
        yield (prefix, list([k for k, _ in dirs]), list([k for k, _ in files]))
        return iter([v._walk(MtreePath(), prefix) for _, v in dirs])

    def walk(self, top):
        return self._walk(top, MtreePath())


class MtreeFile:
    def __init__(
        self,
        *,
        verbose: bool,
        file: "Union[io.StringIO, Path, typing.IO, None]" = None,
        contents_root: "Optional[Path]" = None,
    ):
        self.verbose = verbose
        self._mtree = MtreeSubtree()
        if file:
            self.load(file, contents_root=contents_root, append=False)

    def load(self, file: "Union[io.StringIO,Path,typing.IO]", *, append: bool, contents_root: "Optional[Path]" = None):
        if isinstance(file, Path):
            with file.open("r") as f:
                self.load(f, contents_root=contents_root, append=append)
                return
        if not append:
            self._mtree.clear()
        if "_TEST_SKIP_METALOG" in os.environ:
            status_update("Not parsing", file, "in test mode")
            return  # avoid parsing all metalog files in the basic sanity checks
        for line in file.readlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                entry = MtreeEntry.parse(line, contents_root)
                key = entry.path
                keystr = str(key)
                assert keystr == "." or os.path.normpath(keystr[2:]) == keystr[2:]
                if key in self._mtree:
                    # Currently the FreeBSD build system can produce duplicate directory entries in the mtree file
                    # when installing in parallel. Ignore those duplicates by default since it makes the output
                    # rather noisy. There are also a few duplicate files (mostly in /etc), so suppress it for all
                    # duplicates (in non-verbose mode) until the build system has been fixed
                    if self.verbose:  # TODO: or entry.attributes.get("type") != "dir"
                        warning_message("Found duplicate definition for", entry.path)
                self._mtree[key] = entry
            except Exception as e:
                warning_message("Could not parse line", line, "in mtree file", file, ":", e)

    @staticmethod
    def _ensure_mtree_mode_fmt(mode: "Union[str, int]") -> str:
        if not isinstance(mode, str):
            mode = "0" + oct(mode)[2:]
        assert mode.startswith("0")
        return mode

    @staticmethod
    def _ensure_mtree_path_fmt(path: str) -> str:
        # The path in mtree always starts with ./
        assert not path.endswith("/")
        assert path, "PATH WAS EMPTY?"
        mtree_path = path
        if mtree_path != ".":
            # ensure we normalize paths to avoid conflicting duplicates:
            mtree_path = "./" + os.path.normpath(path)
        return MtreePath(mtree_path)

    @staticmethod
    def infer_mode_string(path: Path, should_be_dir) -> str:
        try:
            result = f"0{stat.S_IMODE(path.lstat().st_mode):o}"  # format as octal with leading 0 prefix
        except OSError as e:
            default = "0755" if should_be_dir else "0644"
            warning_message("Failed to stat", path, "assuming mode", default, e)
            result = default
        # make sure that the .ssh config files are installed with the right permissions
        if path.name == ".ssh" and result != "0700":
            warning_message("Wrong file mode", result, "for", path, " --  it should be 0700, fixing it for image")
            return "0700"
        if path.parent.name == ".ssh" and not path.name.endswith(".pub") and result != "0600":
            warning_message("Wrong file mode", result, "for", path, " --  it should be 0600, fixing it for image")
            return "0600"
        return result

    def add_file(
        self,
        file: "Optional[Path]",
        path_in_image,
        mode=None,
        uname="root",
        gname="wheel",
        print_status=True,
        parent_dir_mode=None,
        symlink_dest: "Optional[str]" = None,
    ):
        if isinstance(path_in_image, PurePath):
            path_in_image = str(path_in_image)
        assert not path_in_image.startswith("/")
        assert not path_in_image.startswith("./") and not path_in_image.startswith("..")
        if mode is None:
            if symlink_dest is not None:
                mode = "0755"
            else:
                mode = self.infer_mode_string(file, False)
        mode = self._ensure_mtree_mode_fmt(mode)
        mtree_path = self._ensure_mtree_path_fmt(path_in_image)
        assert str(mtree_path) != ".", "files should not have name ."
        if symlink_dest is not None:
            assert file is None
            reference_dir = None
            mtree_type = "link"
            last_attrib = ("link", str(symlink_dest))
        else:
            assert file is not None
            reference_dir = file.parent
            if file.is_symlink():
                mtree_type = "link"
                last_attrib = ("link", os.readlink(str(file)))
            else:
                mtree_type = "file"
                # now add the actual entry (with contents=/path/to/file)
                contents_path = str(file.absolute())
                last_attrib = ("contents", contents_path)
        self.add_dir(
            str(Path(path_in_image).parent),
            mode=parent_dir_mode,
            uname=uname,
            gname=gname,
            reference_dir=reference_dir,
            print_status=print_status,
        )
        attribs = OrderedDict([("type", mtree_type), ("uname", uname), ("gname", gname), ("mode", mode), last_attrib])
        entry = MtreeEntry(mtree_path, attribs)
        if print_status:
            if "link" in attribs:
                status_update("Adding symlink to", attribs["link"], "to mtree as", entry, file=sys.stderr)
            else:
                status_update("Adding file", file, "to mtree as", entry, file=sys.stderr)
        self._mtree[mtree_path] = entry

    def add_symlink(self, *, src_symlink: "Optional[Path]" = None, symlink_dest=None, path_in_image: str, **kwargs):
        if src_symlink is not None:
            assert symlink_dest is None
            self.add_file(src_symlink, path_in_image, **kwargs)
        else:
            assert src_symlink is None
            self.add_file(None, path_in_image, symlink_dest=str(symlink_dest), **kwargs)

    def add_dir(self, path, mode=None, uname="root", gname="wheel", print_status=True, reference_dir=None) -> None:
        if isinstance(path, PurePath):
            path = str(path)
        assert not path.startswith("/")
        path = path.rstrip("/")  # remove trailing slashes
        mtree_path = self._ensure_mtree_path_fmt(path)
        if mtree_path in self._mtree:
            return
        if mode is None:
            if reference_dir is None or str(mtree_path) == ".":
                mode = "0755"
            else:
                if print_status:
                    status_update("Inferring permissions for", path, "from", reference_dir, file=sys.stderr)
                mode = self.infer_mode_string(reference_dir, True)
        mode = self._ensure_mtree_mode_fmt(mode)
        # Ensure that SSH will work even if the extra-file directory has wrong permissions
        if (path == "root" or path == "root/.ssh") and mode != "0700" and mode != "0755":
            warning_message("Wrong file mode", mode, "for /", path, " --  it should be 0755, fixing it for image")
            mode = "0755"
        # recursively add all parent dirs that don't exist yet
        parent = str(Path(path).parent)
        if parent != path:  # avoid recursion for path == "."
            # print("adding parent", parent, file=sys.stderr)
            if reference_dir is not None:
                self.add_dir(parent, None, uname, gname, print_status=print_status, reference_dir=reference_dir.parent)
            else:
                self.add_dir(parent, mode, uname, gname, print_status=print_status, reference_dir=None)
        # now add the actual entry
        attribs = OrderedDict([("type", "dir"), ("uname", uname), ("gname", gname), ("mode", mode)])
        entry = MtreeEntry(mtree_path, attribs)
        if print_status:
            status_update("Adding dir", path, "to mtree as", entry, file=sys.stderr)
        self._mtree[mtree_path] = entry

    def add_from_mtree(self, mtree_file, path, print_status=True):
        if isinstance(path, PurePath):
            path = str(path)
        assert not path.startswith("/")
        path = path.rstrip("/")  # remove trailing slashes
        mtree_path = self._ensure_mtree_path_fmt(path)
        if mtree_path in self._mtree:
            return
        if mtree_path not in mtree_file._mtree:
            fatal_error("Could not find " + str(mtree_path) + " in source mtree", pretend=True)
            return
        parent = mtree_path.parent
        if parent != mtree_path:
            self.add_from_mtree(mtree_file, parent, print_status=print_status)
        attribs = mtree_file.get(mtree_path).attributes
        entry = MtreeEntry(mtree_path, attribs)
        if print_status:
            if "link" in attribs:
                status_update("Adding symlink to", attribs["link"], "to mtree as", entry, file=sys.stderr)
            else:
                status_update("Adding", attribs["type"], mtree_path, "to mtree as", entry, file=sys.stderr)
        self._mtree[mtree_path] = entry

    def __contains__(self, item) -> bool:
        mtree_path = self._ensure_mtree_path_fmt(str(item))
        return mtree_path in self._mtree

    def exclude_matching(self, globs, exceptions=None, print_status=False) -> None:
        """Remove paths matching any pattern in globs (but not matching any in exceptions)"""
        if exceptions is None:
            exceptions = []
        if isinstance(globs, str):
            globs = [globs]
        for glob in globs + exceptions:
            # glob must be anchored at the root (./) or start with a pattern
            assert glob[:2] == "./" or glob[:1] == "?" or glob[:1] == "*"
        paths_to_remove = set()
        for path, entry in self._mtree.items():
            for glob in globs:
                if fnmatch.fnmatch(path, glob):
                    delete = True
                    for exception in exceptions:
                        if fnmatch.fnmatch(path, exception):
                            delete = False
                            break
                    if delete:
                        paths_to_remove.add(path)
        for path in paths_to_remove:
            if print_status:
                status_update("Deleting", path, "from mtree", file=sys.stderr)
            self._mtree.pop(path)

    def __repr__(self) -> str:
        import pprint

        return "<MTREE: " + pprint.pformat(self._mtree) + ">"

    def write(self, output: "Union[io.StringIO,Path,typing.IO]", *, pretend):
        if pretend:
            return
        if isinstance(output, Path):
            with output.open("w", encoding="utf-8") as f:
                self.write(f, pretend=pretend)
                return
        output.write("#mtree 2.0\n")
        for path in sorted(self._mtree.keys()):
            output.write(str(self._mtree[path]))
            output.write("\n")
        output.write("# END\n")

    def get(self, key):
        return self._mtree.get(key)

    @property
    def root(self):
        return self._mtree

    def glob(self, pattern, *, case_sensitive=False):
        return self._mtree.glob(pattern, case_sensitive=case_sensitive)

    def walk(self, top):
        return self._mtree.walk(top)
