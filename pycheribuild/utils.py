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
import fcntl
import functools
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import termios
import threading
import traceback
import typing
from pathlib import Path
from subprocess import CompletedProcess

from .colour import AnsiColour, coloured

# reduce the number of import statements per project  # no-combine
__all__ = ["typing", "print_command", "include_local_file", "CompilerInfo",  # no-combine
           "run_command", "status_update", "fatal_error", "coloured", "AnsiColour", "set_env",  # no-combine
           "init_global_config", "warning_message", "popen_handle_noexec", "extract_version",  # no-combine
           "check_call_handle_noexec", "ThreadJoiner", "get_compiler_info", "latest_system_clang_tool",  # no-combine
           "get_program_version", "SafeDict", "keep_terminal_sane",  # no-combine
           "default_make_jobs_count", "commandline_to_str", "OSInfo", "is_jenkins_build",  # no-combine
           "get_version_output", "classproperty", "find_free_port", "have_working_internet_connection",  # no-combine
           "is_case_sensitive_dir", "SocketAndPort"]  # no-combine
Type_T = typing.TypeVar("Type_T")


class GlobalConfig:
    TEST_MODE = False
    PRETEND_MODE = False
    VERBOSE_MODE = False
    QUIET_MODE = False


def init_global_config(*, test_mode: bool, pretend_mode: bool, verbose_mode: bool, quiet_mode: bool):
    assert not (verbose_mode and quiet_mode), "mutually exclusive"
    GlobalConfig.TEST_MODE = test_mode
    GlobalConfig.PRETEND_MODE = pretend_mode
    GlobalConfig.VERBOSE_MODE = verbose_mode
    GlobalConfig.QUIET_MODE = quiet_mode


# noinspection PyPep8Naming
class classproperty(object):
    def __init__(self, f):
        self.f = f

    def __get__(self, obj, owner):
        return self.f(owner)


if sys.version_info < (3, 5, 2):
    sys.exit("This script requires at least Python 3.5.2")


def is_jenkins_build() -> bool:
    return os.getenv("_CHERIBUILD_JENKINS_BUILD") is not None


def __filter_env(env: dict) -> dict:
    result = dict()
    for k, v in env.items():
        if k not in os.environ or os.environ[k] != v:
            result[k] = v
    return result


def print_command(arg1: "typing.Union[str, typing.Sequence[typing.Any]]", *remaining_args, output_file=None,
                  colour=AnsiColour.yellow, cwd=None, env=None, sep=" ", print_verbose_only=False, **kwargs):
    if GlobalConfig.QUIET_MODE or (print_verbose_only and not GlobalConfig.VERBOSE_MODE):
        return
    # also allow passing a single string
    if not type(arg1) is str:
        all_args = arg1
        arg1 = all_args[0]
        remaining_args = all_args[1:]
    prefix = ("cd", shlex.quote(str(cwd)), "&&") if cwd else tuple()
    if env:
        # only print the changed environment entries
        new_env_vars = __filter_env(env)
        if new_env_vars:
            envvars = coloured(AnsiColour.cyan, commandline_to_str(k + "=" + str(v) for k, v in new_env_vars.items()))
            prefix += ("env", envvars)
    # comma in tuple is required otherwise it creates a tuple of string chars
    new_args = (shlex.quote(str(arg1)),) + tuple(map(shlex.quote, map(str, remaining_args)))
    if output_file:
        new_args += (">", str(output_file))
    # Avoid a space before the actual command if there is no prefic:
    if not prefix:
        print(coloured(colour, new_args, sep=sep), flush=True, **kwargs)
    else:
        print(coloured(colour, prefix, sep=sep), coloured(colour, new_args, sep=sep), flush=True, **kwargs)


def get_interpreter(cmdline: "typing.Sequence[str]") -> "typing.Optional[typing.List[str]]":
    """
    :param cmdline: The command to check
    :return: The interpreter command if the executable does not have execute permissions
    """
    executable = Path(cmdline[0])
    print(executable, os.access(str(executable), os.X_OK), cmdline)
    if not executable.exists():
        executable = Path(shutil.which(str(executable)))
    status_update(executable, "is not executable, looking for shebang:", end=" ")
    with executable.open("r", encoding="utf-8") as f:
        first_line = f.readline()
        if first_line.startswith("#!"):
            interpreter = shlex.split(first_line[2:])
            status_update("Will run", executable, "using", interpreter)
            return interpreter
        else:
            status_update("No shebang found.")
            return None


def _make_called_process_error(retcode, args, *, stdout=None, stderr=None, cwd=None):
    err = subprocess.CalledProcessError(retcode, args, output=stdout, stderr=stderr)
    err.cwd = cwd
    return err


def check_call_handle_noexec(cmdline: "typing.List[str]", **kwargs):
    try:
        with keep_terminal_sane():
            return subprocess.check_call(cmdline, **kwargs)
    except PermissionError as e:
        interpreter = get_interpreter(cmdline)
        if interpreter:
            with keep_terminal_sane():
                return subprocess.check_call(interpreter + cmdline, **kwargs)
        raise _make_called_process_error(e.errno, cmdline, cwd=kwargs.get("cwd", None), stderr=str(e).encode("utf-8"))
    except FileNotFoundError as e:
        raise _make_called_process_error(e.errno, cmdline, cwd=kwargs.get("cwd", None), stderr=str(e).encode("utf-8"))


def popen_handle_noexec(cmdline: "typing.List[str]", **kwargs) -> subprocess.Popen:
    try:
        return subprocess.Popen(cmdline, **kwargs)
    except PermissionError as e:
        interpreter = get_interpreter(cmdline)
        if interpreter:
            return subprocess.Popen(interpreter + cmdline, **kwargs)
        raise _make_called_process_error(e.errno, cmdline, cwd=kwargs.get("cwd", None), stderr=str(e).encode("utf-8"))
    except FileNotFoundError as e:
        raise _make_called_process_error(e.errno, cmdline, cwd=kwargs.get("cwd", None), stderr=str(e).encode("utf-8"))


# https://stackoverflow.com/a/15257702/894271
def _become_tty_foreground_process():
    os.setpgrp()
    hdlr = signal.signal(signal.SIGTTOU, signal.SIG_IGN)
    tty = os.open('/dev/tty', os.O_RDWR)
    os.tcsetpgrp(tty, os.getpgrp())
    signal.signal(signal.SIGTTOU, hdlr)


# noinspection PyShadowingBuiltins
def run_command(*args, capture_output=False, capture_error=False, input: "typing.Union[str, bytes]" = None,
                timeout=None,
                print_verbose_only=False, run_in_pretend_mode=False, raise_in_pretend_mode=False, no_print=False,
                replace_env=False, give_tty_control=False, expected_exit_code=0, allow_unexpected_returncode=False,
                **kwargs):
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        cmdline = args[0]  # list with parameters was passed
    else:
        cmdline = args
    assert "_ARGCOMPLETE" not in os.environ, "Should not execute any programs as part of bash completion!"
    cmdline = list(map(str, cmdline))  # ensure it's all strings so that subprocess can handle it
    # When running scripts from a noexec filesystem try to read the interpreter and run that
    if not no_print:
        print_command(cmdline, cwd=kwargs.get("cwd"), env=kwargs.get("env"), print_verbose_only=print_verbose_only)
    if "cwd" in kwargs:
        kwargs["cwd"] = str(kwargs["cwd"])
    else:
        # os.getcwd() raises an exception if the cwd was deleted
        try:
            kwargs["cwd"] = os.getcwd()
        except FileNotFoundError:
            kwargs["cwd"] = tempfile.gettempdir()
    if not run_in_pretend_mode and GlobalConfig.PRETEND_MODE:
        return CompletedProcess(args=cmdline, returncode=0, stdout=b"", stderr=b"")
    # actually run the process now:
    if input is not None:
        assert "stdin" not in kwargs  # we need to use stdin here
        kwargs['stdin'] = subprocess.PIPE
        if not isinstance(input, bytes):
            input = str(input).encode("utf-8")
    if capture_output:
        assert "stdout" not in kwargs  # we need to use stdout here
        kwargs["stdout"] = subprocess.PIPE
    if capture_error:
        assert "stderr" not in kwargs  # we need to use stdout here
        kwargs["stderr"] = subprocess.PIPE
    elif GlobalConfig.QUIET_MODE and "stdout" not in kwargs:
        kwargs["stdout"] = subprocess.DEVNULL

    if "env" in kwargs:
        env_arg = kwargs["env"]  # type: typing.Dict[str, str]
        if not replace_env:
            new_env = os.environ.copy()
            env = {k: str(v) for k, v in env_arg.items()}  # make sure everything is a string
            new_env.update(env)
            kwargs["env"] = new_env
        else:
            kwargs["env"] = dict((k, str(v)) for k, v in env_arg.items())
    if give_tty_control:
        kwargs["preexec_fn"] = _become_tty_foreground_process
    stdout = b""
    stderr = b""
    # Some programs (such as QEMU) can mess up the TTY state if they don't exit cleanly
    with keep_terminal_sane():
        with popen_handle_noexec(cmdline, **kwargs) as process:
            try:
                stdout, stderr = process.communicate(input, timeout=timeout)
            except KeyboardInterrupt:
                process.send_signal(signal.SIGINT)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                assert timeout is not None
                raise subprocess.TimeoutExpired(process.args, timeout, output=stdout, stderr=stderr)
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
            if retcode != expected_exit_code and not allow_unexpected_returncode:
                if GlobalConfig.PRETEND_MODE and not raise_in_pretend_mode:
                    cwd = (". Working directory was ", kwargs["cwd"]) if "cwd" in kwargs else ()
                    fatal_error("Command ", "`" + commandline_to_str(process.args) +
                                "` failed with unexpected exit code ", retcode, *cwd, sep="")
                else:
                    raise _make_called_process_error(retcode, process.args, stdout=stdout, stderr=stderr,
                                                     cwd=kwargs["cwd"])
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
            if not self.path.exists() and GlobalConfig.PRETEND_MODE:
                return Path("/unknown/resource/dir")  # avoid failing in jenkins
            # pretend to compile an existing source file and capture the -resource-dir output
            cc1_cmd = run_command(self.path, "-###", "-xc", "-c", "/dev/null",
                                  capture_error=True, print_verbose_only=True, run_in_pretend_mode=True)
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
        # Try to find a binutil with the same version suffix first
        real_compiler_path = self.path.resolve()
        result = real_compiler_path.parent / (binutil + version_suffix)
        if result.exists():
            return result
        else:
            status_update("Could not find version-suffixed", binutil, "in expected path", result)
        if real_compiler_path != self.path.parent:
            # Clang is installed in a different directory (e.g. /usr/lib/llvm-7) -> should be unversioned
            result = real_compiler_path.parent / binutil
            if not result.exists():
                warning_message("Could not find", binutil, "in expected path", result)
                result = None
        if not result:
            result = shutil.which(binutil)  # fall back to the default and assume clang can find the right one
        return result

    @property
    def is_clang(self):
        return self.compiler in ("clang", "apple-clang")

    @property
    def is_apple_clang(self):
        return self.compiler == "apple-clang"

    def __repr__(self):
        return "{} ({} {})".format(self.path, self.compiler, ".".join(map(str, self.version)))


_cached_compiler_infos = dict()  # type: typing.Dict[Path, CompilerInfo]


def get_compiler_info(compiler: "typing.Union[str, Path]") -> CompilerInfo:
    assert compiler is not None
    if compiler not in _cached_compiler_infos:
        clang_version_pattern = re.compile(b"clang version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        gcc_version_pattern = re.compile(b"gcc version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        apple_llvm_version_pattern = re.compile(b"Apple (?:clang|LLVM) version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # TODO: could also use -dumpmachine to get the triple
        target_pattern = re.compile(b"Target: (.+)")
        # clang prints this output to stderr
        try:
            # Use -v instead of --version to support both gcc and clang
            # Note: for clang-cpp/cpp we need to have stdin as devnull
            version_cmd = run_command(compiler, "-v", capture_error=True, print_verbose_only=True,
                                      run_in_pretend_mode=True,
                                      stdin=subprocess.DEVNULL, capture_output=True)
        except subprocess.CalledProcessError as e:
            stderr = e.stderr if e.stderr else b"FAILED: " + str(e).encode("utf-8")
            version_cmd = CompletedProcess(e.cmd, e.returncode, e.output, stderr)

        clang_version = clang_version_pattern.search(version_cmd.stderr)
        apple_llvm_version = apple_llvm_version_pattern.search(version_cmd.stderr)
        gcc_version = gcc_version_pattern.search(version_cmd.stderr)
        target = target_pattern.search(version_cmd.stderr)
        kind = "unknown compiler"
        version = (0, 0, 0)
        target_string = target.group(1).decode("utf-8") if target else ""
        if gcc_version:
            kind = "gcc"
            version = tuple(map(int, gcc_version.groups()))
        elif apple_llvm_version:
            kind = "apple-clang"
            version = tuple(map(int, apple_llvm_version.groups()))
        elif clang_version:
            kind = "clang"
            version = tuple(map(int, clang_version.groups()))
        else:
            warning_message("Could not detect compiler info for", compiler, "- output was", version_cmd.stderr)
        if GlobalConfig.VERBOSE_MODE:
            print(compiler, "is", kind, "version", version, "with default target", target_string)
        _cached_compiler_infos[compiler] = CompilerInfo(compiler, kind, version, target_string)
    return _cached_compiler_infos[compiler]


# Cache the versions
@functools.lru_cache(maxsize=20)
def get_version_output(program: Path, command_args: tuple = None) -> "bytes":
    if command_args is None:
        command_args = ["--version"]
    prog = run_command([str(program)] + list(command_args), stdin=subprocess.DEVNULL,
                       stderr=subprocess.STDOUT, capture_output=True, run_in_pretend_mode=True)
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


def latest_system_clang_tool(basename: str, fallback_basename: str) -> Path:
    if "_ARGCOMPLETE" in os.environ:  # Avoid expensive lookup when tab-completing
        return Path(fallback_basename)

    # Only search in /usr/bin/ and /usr/local/bin by default.
    # If users want to use other versions they should explicitly pass --cc-path, etc
    search_path = [Path("/usr/local/bin"), Path("/usr/bin")]
    valid_regex = re.compile(re.escape(basename) + r"[-\d.]*$")
    results = []
    for search_dir in search_path:
        if not search_dir.exists():
            continue
        # Note: os.listdir is faster than path.glob("*") since we don't have to stat all files
        for candidate_name in os.listdir(str(search_dir)):
            if not candidate_name.startswith(basename) or not valid_regex.match(candidate_name):
                continue
            # print("Checking compiler candidate", candidate)
            candidate = search_dir / candidate_name
            info = get_compiler_info(candidate)
            if OSInfo.IS_MAC and not info.is_apple_clang:
                # print("Ignoring", candidate, "since it is not apple clang and won't be able to build host binaries")
                continue
            # Minimum version is 4.0
            if info.version < (4, 0, 0) and not info.is_apple_clang:
                # print("Ignoring", basename, "candidate", candidate, "since it is too old:", info.version)
                continue
            results.append((candidate, info.is_apple_clang, info.version))
    if not results:
        fullpath = shutil.which(fallback_basename)
        return Path(fullpath) if fullpath else Path(basename)
    # Find the newest version (and prefer apple-clang to non-apple clang
    # since it is required on macOS to build any binary
    # print("Candidates for", basename, results)
    newest = max(results, key=lambda p: (p[1], p[2]))
    return newest[0]


def default_make_jobs_count():
    make_jobs = os.cpu_count()
    if make_jobs > 24:
        # don't use up all the resources on shared build systems
        # (you can still override this with the -j command line option)
        make_jobs /= 2
    return make_jobs


def maybe_add_space(msg, sep) -> tuple:
    if sep == "":
        return msg, " "
    return (msg,)


def status_update(*args, sep=" ", **kwargs):
    print(coloured(AnsiColour.cyan, *args, sep=sep), **kwargs)


def warning_message(*args, sep=" "):
    # we ignore fatal errors when simulating a run
    print(coloured(AnsiColour.magenta, maybe_add_space("Warning:", sep) + args, sep=sep), file=sys.stderr, flush=True)


def fatal_error(*args, sep=" ", fixit_hint=None, fatal_when_pretending=False, exit_code=3):
    # we ignore fatal errors when simulating a run
    if GlobalConfig.PRETEND_MODE:
        print(coloured(AnsiColour.red, maybe_add_space("Potential fatal error:", sep) + args, sep=sep), file=sys.stderr,
              flush=True)
        if fixit_hint:
            print(coloured(AnsiColour.blue, "Possible solution:", fixit_hint), file=sys.stderr, flush=True)
        if fatal_when_pretending:
            traceback.print_stack()
            sys.exit(exit_code)
    else:
        print(coloured(AnsiColour.red, maybe_add_space("Fatal error:", sep) + args, sep=sep), file=sys.stderr,
              flush=True)
        if fixit_hint:
            print(coloured(AnsiColour.blue, "Possible solution:", fixit_hint), file=sys.stderr, flush=True)
        sys.exit(exit_code)


def include_local_file(path: str) -> str:
    file = Path(__file__).parent / path  # type: Path
    if not file.is_file():
        fatal_error(file, "is missing!")
    with file.open("r", encoding="utf-8") as f:
        return f.read()


def have_working_internet_connection():
    if GlobalConfig.TEST_MODE:
        return True
    # Try to connect to google DNS server at 8.8.8.8 to check if we have a working internet connection
    # Don't make a DNS request since that could be broken for other reasons!
    # From https://stackoverflow.com/questions/3764291/checking-network-connection/33117579#33117579
    host = "8.8.8.8"
    port = 53
    timeout = 3
    x = None
    try:
        x = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        x.settimeout(timeout)
        x.connect((host, port))
        return True
    except OSError:
        return False
    except Exception as ex:
        fatal_error("Something went wrong  while checking for internet connection", ex)
        return False
    finally:
        if x:
            x.close()


def is_case_sensitive_dir(d: Path):
    if not d.exists():
        # assume true for macos:
        if OSInfo.IS_MAC:
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
    def is_ubuntu(cls):
        return cls.__is_linux_distribution("ubuntu")

    @classmethod
    def is_suse(cls):
        return cls.__is_linux_distribution("suse") or cls.__is_linux_distribution("opensuse")

    @classmethod
    def is_debian(cls):
        return cls.__is_linux_distribution("debian")

    @classmethod
    def __is_linux_distribution(cls, kind):
        if not cls.IS_LINUX:
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
                        return "Could not find package for program " + name + ". Maybe `zypper in " + name + "` will " \
                                                                                                             "work."

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
        return cls.is_debian() or cls.is_ubuntu()

    @classmethod
    def uses_zypper(cls):
        return cls.is_suse()


@contextlib.contextmanager
def set_env(*, print_verbose_only=True, **environ):
    """
    Temporarily set the process environment variables.

    >>> with set_env(PLUGINS_DIR=u'test/plugins'):
    ...   "PLUGINS_DIR" in os.environ
    True

    >>> "PLUGINS_DIR" in os.environ
    False

    """
    old_environ = dict(os.environ)
    # make sure all environment variables are converted to string
    str_environ = dict((str(k), str(v)) for k, v in environ.items())
    for k, v in str_environ.items():
        print_command("export", k + "=" + v, print_verbose_only=print_verbose_only)
    os.environ.update(str_environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_environ)


class TtyState:
    # noinspection PyBroadException
    def __init__(self, fd: "typing.TextIO"):
        self.fd = fd
        try:
            self.attrs = termios.tcgetattr(fd)
        except Exception:
            # Can happen if sys.stdin/sys.stdout/sys.stderr is not a TTY
            self.attrs = None
        try:
            self.flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        except Exception:
            # Can happen if sys.stdin/sys.stdout/sys.stderr is not a real file.  When running tests with pytest, this
            # will raise UnsupportedOperation("redirected stdin is pseudofile, has no fileno()")
            self.flags = None

    def _restore_attrs(self):
        new_attrs = termios.tcgetattr(self.fd)
        if new_attrs == self.attrs:
            return
        warning_message("TTY flags for", self.fd.name, "changed, resetting them")
        print("Previous state", self.attrs)
        print("New state", new_attrs)
        termios.tcsetattr(self.fd, termios.TCSANOW, self.attrs)
        termios.tcdrain(self.fd)
        new_attrs = termios.tcgetattr(self.fd)
        if new_attrs != self.attrs:
            warning_message("Failed to restore TTY flags for", self.fd.name)
            print("Previous state", self.attrs)
            print("New state", new_attrs)

    def _restore_flags(self):
        new_flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        if new_flags == self.flags:
            return
        warning_message("FD flags for", self.fd.name, "changed, resetting them")
        print("Previous flags", self.flags)
        print("New flags", new_flags)
        fcntl.fcntl(self.fd, fcntl.F_SETFL, self.flags)
        new_flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        if new_flags != self.flags:
            warning_message("Failed to restore TTY flags for", self.fd.name)
            print("Previous flags", self.flags)
            print("New flags", new_flags)

    def restore(self):
        if self.attrs is not None:  # Not a TTY
            self._restore_attrs()
        if self.flags is not None:  # Not a real file?
            self._restore_flags()


@contextlib.contextmanager
def keep_terminal_sane():
    # Programs such as QEMU can change the terminal state and if they don't exit cleanly this state is
    # propagated to the shell that invoked cheribuild.
    # This function attempts to restore the stdin/stdout/stderr state in those cases:
    stdin_state = TtyState(sys.stdin)
    stdout_state = TtyState(sys.stdout)
    stderr_state = TtyState(sys.stderr)
    try:
        yield
    finally:
        stdin_state.restore()
        stdout_state.restore()
        stderr_state.restore()


class ThreadJoiner(object):
    def __init__(self, thread: "typing.Optional[threading.Thread]"):
        self.thread = thread

    def __enter__(self):
        if self.thread is not None:
            self.thread.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.thread is not None:
            if self.thread.is_alive():
                status_update("Waiting for '", self.thread.name, "' to complete", sep="")
            self.thread.join()


# A dictionary for string formatting (format_map) that preserves values not
# provided for later expansion
#
# https://stackoverflow.com/questions/17215400/python-format-string-unused-named-arguments
class SafeDict(dict):
    def __missing__(self, key): return '{' + key + '}'
