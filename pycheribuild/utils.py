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
import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import traceback
from .colour import coloured, AnsiColour
from .chericonfig import CheriConfig
from pathlib import Path

try:
    import typing
except ImportError:
    typing = {}

if typing:
    Type_T = typing.TypeVar("T")
else:
    Type_T = {}


# reduce the number of import statements per project  # no-combine
__all__ = ["typing", "CheriConfig", "IS_LINUX", "IS_FREEBSD", "printCommand", "includeLocalFile",  # no-combine
           "runCmd", "statusUpdate", "fatalError", "coloured", "AnsiColour", "setCheriConfig", "setEnv",  # no-combine
           "parseOSRelease", "warningMessage", "Type_T", "typing", "popen_handle_noexec",  # no-combine
           "check_call_handle_noexec", "ThreadJoiner"]  # no-combine


if sys.version_info < (3, 4):
    sys.exit("This script requires at least Python 3.4")
if sys.version_info < (3, 5):
    # copy of python 3.5 subprocess.CompletedProcess
    class CompletedProcess(object):
        def __init__(self, args, returncode: int, stdout: bytes=None, stderr: bytes=None):
            self.args = args
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

        def __repr__(self):
            args = ['args={!r}'.format(self.args),
                    'returncode={!r}'.format(self.returncode)]
            if self.stdout is not None:
                args.append('stdout={!r}'.format(self.stdout))
            if self.stderr is not None:
                args.append('stderr={!r}'.format(self.stderr))
            return "{}({})".format(type(self).__name__, ', '.join(args))
else:
    from subprocess import CompletedProcess
import threading

IS_LINUX = sys.platform.startswith("linux")
IS_FREEBSD = sys.platform.startswith("freebsd")
_cheriConfig = None  # type: CheriConfig


# To make it easier to use this as a module (probably most of these commands should be in Project)
def setCheriConfig(c: "CheriConfig"):
    global _cheriConfig
    _cheriConfig = c


def printCommand(arg1: "typing.Union[str, typing.Sequence[typing.Any]]", *remainingArgs, outputFile=None,
                 colour=AnsiColour.yellow, cwd=None, sep=" ", printVerboseOnly=False, **kwargs):
    if _cheriConfig.quiet or (printVerboseOnly and not _cheriConfig.verbose):
        return
    # also allow passing a single string
    if not type(arg1) is str:
        allArgs = arg1
        arg1 = allArgs[0]
        remainingArgs = allArgs[1:]
    newArgs = ("cd", shlex.quote(str(cwd)), "&&") if cwd else tuple()
    # comma in tuple is required otherwise it creates a tuple of string chars
    newArgs += (shlex.quote(str(arg1)),) + tuple(map(shlex.quote, map(str, remainingArgs)))
    if outputFile:
        newArgs += (">", str(outputFile))
    print(coloured(colour, newArgs, sep=sep), flush=True, **kwargs)


def getInterpreter(cmdline: "typing.Sequence[str]") -> "typing.List[str]":
    """
    :param executable: The path to check
    :return: The interpreter command if the executable does not have execute permissions
    """
    executable = Path(cmdline[0])
    print(executable, os.access(str(executable), os.X_OK), cmdline)
    if not executable.exists():
        executable = Path(shutil.which(str(executable)))
    statusUpdate(executable, "is not executable, looking for shebang:", end=" ")
    with executable.open("r", encoding="utf-8") as f:
        firstLine = f.readline()
        if firstLine.startswith("#!"):
            interpreter = shlex.split(firstLine[2:])
            statusUpdate("Will run", executable, "using", interpreter)
            return interpreter
        else:
            statusUpdate("No shebang found.")
            return None

def check_call_handle_noexec(cmdline: "typing.List[str]", **kwargs):
    try:
        return subprocess.check_call(cmdline, **kwargs)
    except PermissionError as e:
        interpreter = getInterpreter(cmdline)
        if interpreter:
            return subprocess.check_call(interpreter + cmdline, **kwargs)
        raise subprocess.CalledProcessError(e, cmdline)


def popen_handle_noexec(cmdline: "typing.List[str]", **kwargs) -> subprocess.Popen:
    try:
        return subprocess.Popen(cmdline, **kwargs)
    except PermissionError as e:
        interpreter = getInterpreter(cmdline)
        if interpreter:
            return subprocess.Popen(interpreter + cmdline, **kwargs)
        raise subprocess.CalledProcessError(e, cmdline)


def runCmd(*args, captureOutput=False, captureError=False, input: "typing.Union[str, bytes]"=None, timeout=None,
           printVerboseOnly=False, runInPretendMode=False, **kwargs):
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        cmdline = args[0]  # list with parameters was passed
    else:
        cmdline = args
    cmdline = list(map(str, cmdline))  # ensure it's all strings so that subprocess can handle it
    # When running scripts from a noexec filesystem try to read the interpreter and run that
    printCommand(cmdline, cwd=kwargs.get("cwd"), printVerboseOnly=printVerboseOnly)
    kwargs["cwd"] = str(kwargs["cwd"]) if "cwd" in kwargs else os.getcwd()
    if _cheriConfig.pretend and not runInPretendMode:
        return CompletedProcess(args=cmdline, returncode=0, stdout=b"", stderr=b"")
    # actually run the process now:
    if input is not None:
        assert "stdin" not in kwargs  # we need to use stdin here
        kwargs['stdin'] = subprocess.PIPE
        if not isinstance(input, bytes):
            input = str(input).encode("utf-8")
    if captureOutput:
        assert "stdout" not in kwargs  # we need to use stdout here
        kwargs["stdout"] = subprocess.PIPE
    if captureError:
        assert "stderr" not in kwargs  # we need to use stdout here
        kwargs["stderr"] = subprocess.PIPE
    elif _cheriConfig.quiet and "stdout" not in kwargs:
        kwargs["stdout"] = subprocess.DEVNULL
    with popen_handle_noexec(cmdline, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            # TODO py35: pass stderr=stderr as well
            raise subprocess.TimeoutExpired(process.args, timeout, output=stdout)
        except:
            process.kill()
            process.wait()
            raise
        retcode = process.poll()
        if retcode:
            raise subprocess.CalledProcessError(retcode, process.args, output=stdout)
        return CompletedProcess(process.args, retcode, stdout, stderr)


def statusUpdate(*args, sep=" ", **kwargs):
    print(coloured(AnsiColour.cyan, *args, sep=sep), **kwargs)


def fatalError(*args, sep=" ", fixitHint=None, fatalWhenPretending=False):
    # we ignore fatal errors when simulating a run
    if _cheriConfig and _cheriConfig.pretend:
        print(coloured(AnsiColour.red, ("Potential fatal error:",) + args, sep=sep))
        if fatalWhenPretending:
            traceback.print_stack()
            sys.exit(3)
    else:
        print(coloured(AnsiColour.red, ("Fatal error:",) + args, sep=sep))
        if fixitHint:
            print(coloured(AnsiColour.blue, "Possible solution:", fixitHint))
        sys.exit(3)


def warningMessage(*args, sep=" "):
    # we ignore fatal errors when simulating a run
    print(coloured(AnsiColour.magenta, ("Warning:",) + args, sep=sep))


def includeLocalFile(path: str) -> str:
    file = Path(__file__).parent / path
    if not file.is_file():
        fatalError(file, "is missing!")
    with file.open("r", encoding="utf-8") as f:
        return f.read()


def parseOSRelease() -> dict:
    with Path("/etc/os-release").open(encoding="utf-8") as f:
        d = {}
        for line in f:
            k, v = line.rstrip().split("=", maxsplit=1)
            # .strip('"') will remove if there or else do nothing
            d[k] = v.strip('"')
    return d


@contextlib.contextmanager
def setEnv(*, printVerboseOnly=True, **environ):
    """
    Temporarily set the process environment variables.

    >>> with set_env(PLUGINS_DIR=u'test/plugins'):
    ...   "PLUGINS_DIR" in os.environ
    True

    >>> "PLUGINS_DIR" in os.environ
    False

    """
    old_environ = dict(os.environ)
    for k, v in environ.items():
        printCommand("export", k + "=" + v, printVerboseOnly=printVerboseOnly)
    os.environ.update(environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_environ)


class ThreadJoiner(object):
    def __init__(self, thread: "typing.Optional[threading.Thread]"):
        self.thread = thread

    def __enter__(self):
        if self.thread is not None:
            self.thread.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.thread is not None:
            if self.thread.is_alive():
                statusUpdate("Waiting for '", self.thread.name, "' to complete", sep="")
            self.thread.join()
