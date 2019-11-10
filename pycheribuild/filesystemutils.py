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

import os
import threading
import shutil
import subprocess

from pathlib import Path
from .config.chericonfig import CheriConfig
from .utils import *


class FileSystemUtils(object):
    def __init__(self, config: CheriConfig):
        self.config = config

    def makedirs(self, path: Path):
        if not self.config.pretend and not path.is_dir():
            printCommand("mkdir", "-p", path, print_verbose_only=True)
            os.makedirs(str(path), exist_ok=True)

    def _deleteDirectories(self, *dirs):
        # http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
        # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
        runCmd("rm", "-rf", *dirs)

    def cleanDirectory(self, path: Path, keepRoot=False, ensure_dir_exists=True) -> None:
        """ After calling this function path will be an empty directory
        :param path: the directory to delete
        :param keepRoot: Whether to keep the root directory (e.g. for NFS exported mountpoints)
        """
        if path.is_dir():
            # If the root dir is used e.g. as an NFS mount we mustn't remove it, but only the subdirectories
            entries = list(map(str, path.iterdir())) if keepRoot else [path]
            self._deleteDirectories(*entries)
        # always make sure the path exists
        if ensure_dir_exists:
            self.makedirs(path)

    class DeleterThread(threading.Thread):
        def __init__(self, parent: "FileSystemUtils", path: Path):
            super().__init__(name="Deleting " + str(path))
            self.path = path
            self.parent = parent

        def run(self):
            try:
                if self.parent.config.verbose:
                    statusUpdate("Deleting", self.path, "asynchronously")
                self.parent._deleteDirectories(self.path)
                if self.parent.config.verbose:
                    statusUpdate("Async delete of", self.path, "finished")
            except Exception as e:
                warningMessage("Could not remove directory", self.path, e)

    def asyncCleanDirectory(self, path: Path, *, keepRoot=False, keep_dirs: list=None) -> ThreadJoiner:
        """
        Delete a directory in the background (e.g. deleting the cheribsd build directory delays the build
        with self.asyncCleanDirectory("foo")
            # foo has been moved to foo.tmp and foo is now and empty dir:
            do_something()
        # now foo.tpt no longer exists
        :param path: the directory to clean
        :param keepRoot: currently not supported
        :return:
        """
        deleterThread = None
        tempdir = path.with_suffix(".delete-me-pls")
        if not path.is_dir():
            self.makedirs(path)
        elif len(list(path.iterdir())) == 0:
            statusUpdate("Not cleaning", path, "it is already empty")
        else:
            if tempdir.is_dir():
                warningMessage("Previous async cleanup of ", path, "failed. Cleaning up now")
                self._deleteDirectories(tempdir)
            if keepRoot:
                # Move all subdirectories/files to a temp directory and delete that
                self.makedirs(tempdir)
                if not self.config.pretend:
                    assert tempdir.is_dir()
                    assert len(list(tempdir.iterdir())) == 0, list(tempdir.iterdir())
                all_entries = list(path.iterdir())
                if keep_dirs:
                    all_entries_new = []
                    for i in all_entries:
                        if i.name in keep_dirs:
                            statusUpdate("Not deleting", i, "- If you really want it removed, delete it manually.")
                        else:
                            all_entries_new.append(i)
                    all_entries = all_entries_new
                all_entries = list(map(str, all_entries))
                if all_entries:
                    runCmd(["mv"] + all_entries + [str(tempdir)], print_verbose_only=True)
            else:
                # rename the directory, create a new dir and then delete it in a background thread
                runCmd("mv", path, tempdir)
                self.makedirs(path)
        if not self.config.pretend:
            assert path.is_dir()
            if not (keep_dirs and keepRoot):
                assert len(list(path.iterdir())) == 0, list(path.iterdir())
        if tempdir.is_dir() or self.config.pretend:
            # we now have an empty directory, start background deleter and return to caller
            deleterThread = FileSystemUtils.DeleterThread(self, tempdir)
        return ThreadJoiner(deleterThread)

    def deleteFile(self, file: Path, print_verbose_only=False):
        if not file.is_file():
            return
        printCommand("rm", "-f", file, print_verbose_only=print_verbose_only)
        if self.config.pretend:
            return
        file.unlink()

    def copyRemoteFile(self, remotePath: str, targetFile: Path):
        # if we have rsync we can skip the copy if file is already up-to-date
        if shutil.which("rsync"):
            try:
                runCmd("rsync", "-aviu", "--progress", remotePath, targetFile)
            except subprocess.CalledProcessError as err:
                if err.returncode == 127:
                    warningMessage("rysnc doesn't seem to be installed on remote machine, trying scp")
                    runCmd("scp", remotePath, targetFile)
                else:
                    raise err
        else:
            runCmd("scp", remotePath, targetFile)

    def readFile(self, file: Path) -> str:
        # just return an empty string in pretend mode
        if self.config.pretend and not file.is_file():
            return "\n"
        with file.open("r", encoding="utf-8") as f:
            return f.read()

    def writeFile(self, file: Path, contents: str, *, overwrite: bool, noCommandPrint=False, mode=None) -> None:
        """
        :param file: The target path to write contents to
        :param contents: the contents of the new file
        :param mode: The file mode for the resulting file (octal number or string)
        :param overwrite: If true the file will be overwritten, otherwise it will cause an error if the file exists
        :param noCommandPrint: don't ever print the echo commmand (even in verbose)
        """
        if not noCommandPrint:
            printCommand("echo", contents, colour=AnsiColour.green, outputFile=file, print_verbose_only=True)
        if self.config.pretend:
            return
        if not overwrite and file.exists():
            fatalError("File", file, "already exists!")
        self.makedirs(file.parent)
        with file.open("w", encoding="utf-8") as f:
            f.write(contents)
        if mode:
            file.chmod(mode)

    def createSymlink(self, src: Path, dest: Path, *, relative=True, cwd: Path = None, print_verbose_only = True):
        assert dest.is_absolute() or cwd is not None
        if not cwd:
            cwd = dest.parent
        if relative:
            if src.is_absolute():
                src = os.path.relpath(str(src), str(dest.parent if dest.is_absolute() else cwd))
            if cwd is not None and cwd.is_dir():
                dest = dest.relative_to(cwd)
            runCmd("ln", "-fsn", src, dest, cwd=cwd, print_verbose_only=print_verbose_only)
        else:
            runCmd("ln", "-fsn", src, dest, cwd=cwd, print_verbose_only=print_verbose_only)

    def moveFile(self, src: Path, dest: Path, force=False, createDirs=True):
        if not src.exists():
            fatalError(src, "doesn't exist")
        cmd = ["mv", "-f"] if force else ["mv"]
        if createDirs and not dest.parent.exists():
            self.makedirs(dest.parent)
        runCmd(cmd + [str(src), str(dest)])

    def installFile(self, src: Path, dest: Path, *, force=False, createDirs=True, print_verbose_only=True, mode=None):
        if force:
            printCommand("cp", "-f", src, dest, print_verbose_only=print_verbose_only)
        else:
            printCommand("cp", src, dest, print_verbose_only=print_verbose_only)
        if self.config.pretend:
            if mode is not None:
                printCommand("chmod", oct(mode), dest, print_verbose_only=print_verbose_only)
            return
        assert not dest.is_dir(), "installFile: target is a directory and not a file: " + str(dest)
        if (dest.is_symlink() or dest.exists()) and force:
            dest.unlink()
        if not src.exists():
            fatalError("Required file", src, "does not exist")
        if createDirs and not dest.parent.exists():
            self.makedirs(dest.parent)
        if dest.is_symlink():
            dest.unlink()
        # noinspection PyArgumentList
        shutil.copy(str(src), str(dest), follow_symlinks=False)
        if mode is not None:
            printCommand("chmod", oct(mode), dest, print_verbose_only=print_verbose_only)
            if not self.config.pretend:
                dest.chmod(mode)

    @staticmethod
    def createBuildtoolTargetSymlinks(tool: Path, toolName: str = None, createUnprefixedLink: bool = False,
                                      cwd: str = None):
        """
        Create mips4-unknown-freebsd, cheri-unknown-freebsd and mips64-unknown-freebsd prefixed symlinks
        for build tools like clang, ld, etc.
        :param createUnprefixedLink: whether to create a symlink toolName -> tool.name
        (in case the real tool is prefixed)
        :param cwd: the working directory
        :param tool: the binary for which the symlinks will be created
        :param toolName: the unprefixed name of the tool (defaults to tool.name) such as e.g. "ld", "ar"
        """
        # if the actual tool we are linking to make sure we link to the destinations so we don't create symlink loops
        cwd = cwd or tool.parent  # set cwd before resolving potential symlink
        if not toolName:
            toolName = tool.name
        if not tool.is_file():
            fatalError("Attempting to create symlink to non-existent build tool:", tool)

        # a prefixed tool was installed -> create link such as mips4-unknown-freebsd-ld -> ld
        if createUnprefixedLink:
            assert tool.name != toolName
            runCmd("ln", "-fsn", tool.name, toolName, cwd=cwd, print_verbose_only=True)

        for target in ("mips4-unknown-freebsd-", "cheri-unknown-freebsd-", "mips64-unknown-freebsd-"):
            link = tool.parent / (target + toolName)  # type: Path
            if link == tool:  # happens for binutils, where prefixed tools are installed
                # if self.config.verbose:
                #    print(coloured(AnsiColour.yellow, "Not overwriting", link, "because it is the target"))
                continue
            runCmd("ln", "-fsn", tool.name, target + toolName, cwd=cwd, print_verbose_only=True)
