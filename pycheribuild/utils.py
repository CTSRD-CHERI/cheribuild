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
import functools
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import traceback
import typing
from pathlib import Path

from .colour import coloured, AnsiColour, statusUpdate, warningMessage
if typing.TYPE_CHECKING:
    from .config.chericonfig import CheriConfig

Type_T = typing.TypeVar("Type_T")

# reduce the number of import statements per project  # no-combine
__all__ = ["typing", "IS_LINUX", "IS_FREEBSD", "IS_MAC", "printCommand", "includeLocalFile", "CompilerInfo",   # no-combine
           "runCmd", "statusUpdate", "fatalError", "coloured", "AnsiColour", "setCheriConfig", "setEnv",  # no-combine
           "warningMessage", "Type_T", "typing", "popen_handle_noexec", "extract_version", "get_program_version",  # no-combine
           "check_call_handle_noexec", "ThreadJoiner", "getCompilerInfo", "latestClangTool", "SafeDict",  # no-combine
           "defaultNumberOfMakeJobs", "commandline_to_str", "OSInfo", "is_jenkins_build", "get_global_config",  # no-combine
           "get_version_output", "classproperty", "find_free_port", "have_working_internet_connection", # no-combine
           "is_case_sensitive_dir"]  # no-combine


_TEST_MODE = False

class classproperty(object):
    def __init__(self, f):
        self.f = f
    def __get__(self, obj, owner):
        return self.f(owner)


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

IS_LINUX = sys.platform.startswith("linux")
IS_FREEBSD = sys.platform.startswith("freebsd")
IS_MAC = sys.platform.startswith("darwin")
_cheriConfig = None  # type: typing.Optional[CheriConfig]


def is_jenkins_build() -> bool:
    return os.getenv("_CHERIBUILD_JENKINS_BUILD") is not None

# To make it easier to use this as a module (probably most of these commands should be in Project)
def setCheriConfig(c: "CheriConfig"):
    global _cheriConfig
    _cheriConfig = c


def get_global_config() -> "CheriConfig":
    global _cheriConfig
    assert _cheriConfig is not None
    return _cheriConfig


def __filterEnv(env: dict) -> dict:
    result = dict()
    for k, v in env.items():
        if k not in os.environ or os.environ[k] != v:
            result[k] = v
    return result


def printCommand(arg1: "typing.Union[str, typing.Sequence[typing.Any]]", *remaining_args, outputFile=None,
                 colour=AnsiColour.yellow, cwd=None, env=None, sep=" ", print_verbose_only=False, **kwargs):
    if not _cheriConfig or (_cheriConfig.quiet or (print_verbose_only and not _cheriConfig.verbose)):
        return
    # also allow passing a single string
    if not type(arg1) is str:
        all_args = arg1
        arg1 = all_args[0]
        remaining_args = all_args[1:]
    prefix = ("cd", shlex.quote(str(cwd)), "&&") if cwd else tuple()
    if env:
        # only print the changed environment entries
        new_env_vars = __filterEnv(env)
        if new_env_vars:
            envvars = coloured(AnsiColour.cyan, commandline_to_str(k + "=" + str(v) for k, v in new_env_vars.items()))
            prefix += ("env", envvars)
    # comma in tuple is required otherwise it creates a tuple of string chars
    new_args = (shlex.quote(str(arg1)),) + tuple(map(shlex.quote, map(str, remaining_args)))
    if outputFile:
        new_args += (">", str(outputFile))
    # Avoid a space before the actual command if there is no prefic:
    if not prefix:
        print(coloured(colour, new_args, sep=sep), flush=True, **kwargs)
    else:
        print(coloured(colour, prefix, sep=sep), coloured(colour, new_args, sep=sep), flush=True, **kwargs)



def getInterpreter(cmdline: "typing.Sequence[str]") -> "typing.Optional[typing.List[str]]":
    """
    :param cmdline: The command to check
    :return: The interpreter command if the executable does not have execute permissions
    """
    executable = Path(cmdline[0])
    print(executable, os.access(str(executable), os.X_OK), cmdline)
    if not executable.exists():
        executable = Path(shutil.which(str(executable)))
    statusUpdate(executable, "is not executable, looking for shebang:", end=" ")
    with executable.open("r", encoding="utf-8") as f:
        first_line = f.readline()
        if first_line.startswith("#!"):
            interpreter = shlex.split(first_line[2:])
            statusUpdate("Will run", executable, "using", interpreter)
            return interpreter
        else:
            statusUpdate("No shebang found.")
            return None


def _make_called_process_error(retcode, args, *, stdout=None, stderr=None, cwd=None):
    if sys.version_info < (3, 5):
        err = subprocess.CalledProcessError(retcode, args, output=stdout)
        err.stderr = stderr
    else:
        err = subprocess.CalledProcessError(retcode, args, output=stdout, stderr=stderr)
    err.cwd = cwd
    return err


def check_call_handle_noexec(cmdline: "typing.List[str]", **kwargs):
    try:
        return subprocess.check_call(cmdline, **kwargs)
    except PermissionError as e:
        interpreter = getInterpreter(cmdline)
        if interpreter:
            return subprocess.check_call(interpreter + cmdline, **kwargs)
        raise _make_called_process_error(e.errno, cmdline, cwd=kwargs.get("cwd", None), stderr=str(e).encode("utf-8"))
    except FileNotFoundError as e:
        raise _make_called_process_error(e.errno, cmdline, cwd=kwargs.get("cwd", None), stderr=str(e).encode("utf-8"))


def popen_handle_noexec(cmdline: "typing.List[str]", **kwargs) -> subprocess.Popen:
    try:
        return subprocess.Popen(cmdline, **kwargs)
    except PermissionError as e:
        interpreter = getInterpreter(cmdline)
        if interpreter:
            return subprocess.Popen(interpreter + cmdline, **kwargs)
        raise _make_called_process_error(e.errno, cmdline, cwd=kwargs.get("cwd", None), stderr=str(e).encode("utf-8"))
    except FileNotFoundError as e:
        raise _make_called_process_error(e.errno, cmdline, cwd=kwargs.get("cwd", None), stderr=str(e).encode("utf-8"))


def runCmd(*args, captureOutput=False, captureError=False, input: "typing.Union[str, bytes]"=None, timeout=None,
           print_verbose_only=False, runInPretendMode=False, raiseInPretendMode=False, no_print=False,
           replace_env=False, **kwargs):
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        cmdline = args[0]  # list with parameters was passed
    else:
        cmdline = args
    cmdline = list(map(str, cmdline))  # ensure it's all strings so that subprocess can handle it
    # When running scripts from a noexec filesystem try to read the interpreter and run that
    if not no_print:
        printCommand(cmdline, cwd=kwargs.get("cwd"), env=kwargs.get("env"), print_verbose_only=print_verbose_only)
    if "cwd" in kwargs:
        kwargs["cwd"] = str(kwargs["cwd"])
    else:
        # os.getcwd() raises an exception if the cwd was deleted
        try:
            kwargs["cwd"] = os.getcwd()
        except FileNotFoundError:
            kwargs["cwd"] = tempfile.gettempdir()
    if not runInPretendMode and _cheriConfig and _cheriConfig.pretend:
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
    elif _cheriConfig and _cheriConfig.quiet and "stdout" not in kwargs:
        kwargs["stdout"] = subprocess.DEVNULL

    if "env" in kwargs:
        if not replace_env:
            new_env = os.environ.copy()
            env = {k: str(v) for k, v in kwargs["env"].items()}  # make sure everything is a string
            new_env.update(env)
            kwargs["env"] = new_env
        else:
            kwargs["env"] = dict((k, str(v)) for k, v in kwargs["env"].items())
    with popen_handle_noexec(cmdline, **kwargs) as process:
        try:
            stdout, stderr = process.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            # TODO py35: pass stderr=stderr as well
            raise subprocess.TimeoutExpired(process.args, timeout, output=stdout)
        except BrokenPipeError:
            # just return the exit code
            process.kill()
            retcode = process.wait()
            raise _make_called_process_error(retcode, process.args, stdout=b"", cwd=kwargs["cwd"])
        except Exception:
            process.kill()
            process.wait()
            raise
        retcode = process.poll()
        if retcode:
            if _cheriConfig and _cheriConfig.pretend and not raiseInPretendMode:
                cwd = (". Working directory was ", kwargs["cwd"]) if "cwd" in kwargs else ()
                fatalError("Command ", "`" + commandline_to_str(process.args) +
                           "` failed with non-zero exit code ", retcode, *cwd, sep="")
            else:
                raise _make_called_process_error(retcode, process.args, stdout=stdout, cwd=kwargs["cwd"])
        return CompletedProcess(process.args, retcode, stdout, stderr)


def commandline_to_str(args: "typing.Iterable[str]") -> str:
    return " ".join((shlex.quote(str(s)) for s in args))


class SocketAndPort(object):
    def __init__(self, sock: socket.socket, port: int):
        self.socket = sock
        self.port = port


def find_free_port() -> SocketAndPort:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    return SocketAndPort(s, s.getsockname()[1])


class CompilerInfo(object):
    def __init__(self, path: Path, compiler, version, default_target):
        self.path = path
        self.compiler = compiler
        self.version = version
        self.default_target = default_target
        self._resource_dir = None
        assert compiler in ("unknown compiler", "clang", "apple-clang", "gcc"), "unknown type: " + compiler

    def get_resource_dir(self):
        # assert self.is_clang, self.compiler
        if not self._resource_dir:
            if not self.path.exists() and _cheriConfig.pretend:
                return Path("/unknown/resource/dir")  # avoid failing in jenkins
            # pretend to compile an existing source file and capture the -resource-dir output
            cc1_cmd = runCmd(self.path, "-###", "-xc", "-c", "/dev/null",
                             captureError=True, print_verbose_only=True, runInPretendMode=True)
            resource_dir_pat = re.compile(b'"-cc1".+"-resource-dir" "([^"]+)"')
            self._resource_dir = Path(resource_dir_pat.search(cc1_cmd.stderr).group(1).decode("utf-8"))
        return self._resource_dir

    def get_matching_binutil(self, binutil):
        assert self.is_clang
        name = self.path.name
        version_suffix = ""
        for basename in ("clang++", "clang-cpp", "clang"):
            if name.startswith(basename):
                version_suffix = name[len(basename):]
        result = None
        real_compiler_path = self.path.resolve()
        suffixed_binutil = real_compiler_path.parent / (binutil + version_suffix)
        if suffixed_binutil.exists():
            return suffixed_binutil
        else:
            statusUpdate("Could not find version-suffixed", binutil, "in expected path", result)
        if real_compiler_path != self.path.parent:
            # Clang is installed in a different directory (e.g. /usr/lib/llvm-7) -> should be unversioned
            result = real_compiler_path.parent / binutil
            if not result.exists():
                warningMessage("Could not find", binutil, "in expected path", result)
                result = None
        if not result:
            result = shutil.which(binutil)  # fall back to the default and assume clang can find the right one
        return result

    @property
    def is_clang(self):
        return self.compiler in ("clang", "apple-clang")

_cached_compiler_infos = dict()  # type: typing.Dict[Path, CompilerInfo]


def getCompilerInfo(compiler: "typing.Union[str, Path]") -> CompilerInfo:
    assert compiler is not None
    if compiler not in _cached_compiler_infos:
        clangVersionPattern = re.compile(b"clang version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        gccVersionPattern = re.compile(b"gcc version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        appleLlvmVersionPattern = re.compile(b"Apple LLVM version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # TODO: could also use -dumpmachine to get the triple
        targetPattern = re.compile(b"Target: (.+)")
        # clang prints this output to stderr
        try:
            # Use -v instead of --version to support both gcc and clang
            # Note: for clang-cpp/cpp we need to have stdin as devnull
            versionCmd = runCmd(compiler, "-v", captureError=True, print_verbose_only=True, runInPretendMode=True,
                                stdin=subprocess.DEVNULL, captureOutput=True)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr if e.stderr else b"FAILED: " + str(e).encode("utf-8")
            versionCmd = CompletedProcess(e.cmd, e.returncode, e.output, stderr)

        clangVersion = clangVersionPattern.search(versionCmd.stderr)
        appleLlvmVersion = appleLlvmVersionPattern.search(versionCmd.stderr)
        gccVersion = gccVersionPattern.search(versionCmd.stderr)
        target = targetPattern.search(versionCmd.stderr)
        # if _cheriConfig and _cheriConfig.pretend:
        kind = "unknown compiler"
        version = (0, 0, 0)
        targetString = target.group(1).decode("utf-8") if target else ""
        if gccVersion:
            kind = "gcc"
            version = tuple(map(int, gccVersion.groups()))
        elif clangVersion:
            kind = "clang"
            version = tuple(map(int, clangVersion.groups()))
        elif appleLlvmVersion:
            kind = "apple-clang"
            # TODO: parse #define __VERSION__ "4.2.1 Compatible Apple LLVM 8.1.0 (clang-802.0.42)"
            version = tuple(map(int, appleLlvmVersion.groups()))
        else:
            warningMessage("Could not detect compiler info for", compiler, "- output was", versionCmd.stderr)
        if _cheriConfig and _cheriConfig.verbose:
            print(compiler, "is", kind, "version", version, "with default target", targetString)
        _cached_compiler_infos[compiler] = CompilerInfo(compiler, kind, version, targetString)
    return _cached_compiler_infos[compiler]


# Cache the versions
@functools.lru_cache(maxsize=20)
def get_version_output(program: Path, command_args: tuple = None) -> "bytes":
    if command_args is None:
        command_args = ["--version"]
    prog = runCmd([str(program)] + list(command_args), stdin=subprocess.DEVNULL,
                  stderr=subprocess.STDOUT, captureOutput=True, runInPretendMode=True)
    return prog.stdout


@functools.lru_cache(maxsize=20)
def get_program_version(program: Path, command_args: tuple = None, component_kind: "typing.Type[Type_T]" = int,
                        regex=None, program_name: bytes = None) -> "typing.Tuple[Type_T, Type_T, Type_T]":
    if program_name is None:
        program_name = program.name.encode("utf-8")
    stdout = get_version_output(program, command_args=command_args)
    return extract_version(stdout, component_kind, regex, program_name)


# extract the version component from program output such as "git version 2.7.4"
def extract_version(output: bytes, component_kind: "typing.Type[Type_T]" = int, regex: "typing.Pattern" = None,
                    program_name: bytes = b"") -> "typing.Tuple[Type_T, Type_T, Type_T]":
    if regex is None:
        prefix = program_name + b" " if program_name else b""
        regex = re.compile(prefix + b"version\\s+(\\d+)\\.(\\d+)\\.?(\\d+)?")
    elif isinstance(regex, bytes):
        regex = re.compile(regex)
    match = regex.search(output)
    if not match:
        print(output)
        raise ValueError("Expected to match regex " + str(regex))
    # noinspection PyTypeChecker
    return tuple(map(component_kind, match.groups()))


def latestClangTool(basename: str):
    # try to find at least clang 3.7, otherwise fall back to system clang
    found_versioned_clang = (None, None)
    versions = [(i, 0) for i in range(10, 3, -1)] + [(3, 9), (3, 8), (3, 7)]
    for version in versions:
        # FreeBSD installs clang39 and clang70, Linux uses clang-3.9 and clang-7
        suffix1 = ("%d%d" % version)
        if version[0] >= 7:
            # version after 7.0 don't include the minor component anymore on Linux:
            suffix2 = "-" + str(version[0])
        else:
            suffix2 = ("-%d.%d" % version)
        guess = shutil.which(basename + suffix1)
        if guess:
            found_versioned_clang = (guess, version)
            break
        guess = shutil.which(basename + suffix2)
        if guess:
            found_versioned_clang = (guess, version)
            break
    guess = shutil.which(basename)
    if guess is None and basename == "clang-cpp":
        guess = shutil.which("cpp")
    if guess:
        if found_versioned_clang[0] is None:
            return guess
        # Otherwise check if the versioned clang install is newer than the unsuffixed one:
        info = getCompilerInfo(guess)
        # print("default clang is ", info, "found clang is", found_versioned_clang[1])
        return guess if info.version > found_versioned_clang[1] else found_versioned_clang[0]
    return found_versioned_clang[0]


def defaultNumberOfMakeJobs():
    makeJobs = os.cpu_count()
    if makeJobs > 24:
        # don't use up all the resources on shared build systems
        # (you can still override this with the -j command line option)
        makeJobs /= 2
    return makeJobs


def fatalError(*args, sep=" ", fixitHint=None, fatalWhenPretending=False):
    # we ignore fatal errors when simulating a run
    if _cheriConfig and _cheriConfig.pretend:
        print(coloured(AnsiColour.red, ("Potential fatal error:",) + args, sep=sep), file=sys.stderr)
        if fixitHint:
            print(coloured(AnsiColour.blue, "Possible solution:", fixitHint), file=sys.stderr)
        if fatalWhenPretending:
            traceback.print_stack()
            sys.exit(3)
    else:
        print(coloured(AnsiColour.red, ("Fatal error:",) + args, sep=sep), file=sys.stderr)
        if fixitHint:
            print(coloured(AnsiColour.blue, "Possible solution:", fixitHint), file=sys.stderr)
        sys.exit(3)


def includeLocalFile(path: str) -> str:
    file = Path(__file__).parent / path  # type: Path
    if not file.is_file():
        fatalError(file, "is missing!")
    with file.open("r", encoding="utf-8") as f:
        return f.read()


def have_working_internet_connection():
    if _TEST_MODE:
        return True
    # Try to connect to google DNS server at 8.8.8.8 to check if we have a working internet connection
    # Don't make a DNS request since that could be broken for other reasons!
    # From https://stackoverflow.com/questions/3764291/checking-network-connection/33117579#33117579
    host = "8.8.8.8"
    port = 53
    timeout = 3
    try:
        x = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        x.settimeout(timeout)
        x.connect((host, port))
        return True
    except OSError:
        return False
    except Exception as ex:
        fatalError("Something went wrong  while checking for internet connection", ex)
        return False


def is_case_sensitive_dir(d: Path):
    if not d.exists():
        # assume true for macos:
        if IS_MAC:
            return False
        return True  # XXX: exception?
    path_upper = d / "TestDirCaseSensitive"
    path_lower = d / "testdircasesensitive"
    if path_upper.exists():
        path_upper.rmdir()
    if path_lower.exists():
        path_lower.rmdir()
    path_upper.mkdir()
    if path_lower.exists():
        # Lowercase dir found -> case insensitive
        path_lower.rmdir()
        return False
    path_upper.rmdir()
    return True


class OSInfo(object):
    IS_LINUX = sys.platform.startswith("linux")
    IS_FREEBSD = sys.platform.startswith("freebsd")
    IS_MAC = sys.platform.startswith("darwin")
    __os_release_cache = None

    @classmethod
    def isUbuntu(cls):
        return cls.__is_linux_distribution("ubuntu")

    @classmethod
    def isSuse(cls):
        return cls.__is_linux_distribution("suse") or cls.__is_linux_distribution("opensuse")

    @classmethod
    def isDebian(cls):
        return cls.__is_linux_distribution("debian")

    @classmethod
    def __is_linux_distribution(cls, kind):
        if not IS_LINUX:
            return False
        return kind in cls.etc_os_release().get("ID", "") or kind in cls.etc_os_release().get("ID_LIKE", "")

    @staticmethod
    def etc_os_release() -> dict:
        if OSInfo.__os_release_cache is None:
            OSInfo.__os_release_cache = OSInfo.__parse_etc_os_release()
        return OSInfo.__os_release_cache

    @staticmethod
    def __parse_etc_os_release() -> dict:
        if not Path("/etc/os-release").exists():
            return {}
        with Path("/etc/os-release").open(encoding="utf-8") as f:
            d = {}
            for line in f:
                k, v = line.rstrip().split("=", maxsplit=1)
                # .strip('"') will remove if there or else do nothing
                d[k] = v.strip('"')
        return d

    @classmethod
    def package_manager(cls):
        if cls.IS_MAC:
            return "brew"
        elif cls.IS_FREEBSD:
            return "pkg"
        elif cls.IS_LINUX:
            if cls.uses_zypper():
                return "zypper"
            elif cls.uses_apt():
                return "apt"
        return "<system package manager>"

    @classmethod
    def install_instructions(cls, name, is_lib, homebrew=None, apt=None, zypper=None, freebsd=None,
                             cheribuild_target=None) -> "typing.Union[str, typing.Callable[[], str]]":
        if cheribuild_target:
            return "Run `cheribuild.py " + cheribuild_target + "`"
        guessed_package = False
        if cls.IS_MAC and homebrew:
            install_name = homebrew
        elif cls.IS_FREEBSD and freebsd:
            install_name = freebsd
        elif cls.uses_apt():
            if apt:
                install_name = apt
            else:
                guessed_package = True
                install_name = "lib" + name + "-dev" if is_lib else name
        elif cls.uses_zypper():
            if zypper:
                install_name = zypper
            else:
                if not is_lib and shutil.which("command-not-found"):
                    # for programs we can use the command-not-found tool to get detailed install instructions
                    def command_not_found():
                        hint = subprocess.getoutput(shutil.which("command-not-found") + " " + name)
                        print(hint)
                        if hint and not name + ": command not found" in hint:
                            msg_start = hint.find("The program")
                            if msg_start:
                                hint = hint[msg_start:]
                            return hint
                        return "Could not find package for program " + name + ". Maybe `zypper in " + name + "` will work."
                    return command_not_found
                guessed_package = True
                install_name = "lib" + name + "-devel" if is_lib else name
        else:
            guessed_package = True
            install_name = name
        if guessed_package:
            # not sure if the package name is correct:
            return "Possibly running `" + cls.package_manager() + " install " + install_name + \
                                  "` fixes this. Note: package name may not be correct."
        else:
            return "Run `" + cls.package_manager() + " install " + install_name + "`"

    @classmethod
    def uses_apt(cls):
        return cls.isDebian() or cls.isUbuntu()

    @classmethod
    def uses_zypper(cls):
        return cls.isSuse()


@contextlib.contextmanager
def setEnv(*, print_verbose_only=True, **environ):
    """
    Temporarily set the process environment variables.

    >>> with setEnv(PLUGINS_DIR=u'test/plugins'):
    ...   "PLUGINS_DIR" in os.environ
    True

    >>> "PLUGINS_DIR" in os.environ
    False

    """
    old_environ = dict(os.environ)
    # make sure all environment variables are converted to string
    str_environ = dict((str(k),str(v)) for k,v in environ.items())
    for k, v in str_environ.items():
        printCommand("export", k + "=" + v, print_verbose_only=print_verbose_only)
    os.environ.update(str_environ)
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

# A dictionary for string formatting (format_map) that preserves values not
# provided for later expansion
#
# https://stackoverflow.com/questions/17215400/python-format-string-unused-named-arguments
class SafeDict(dict):
    def __missing__(self, key): return '{' + key + '}'
