#!/usr/bin/env python3
import argparse
import contextlib
import datetime
import functools
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import OrderedDict
from enum import Enum
from pathlib import Path

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri


class AnsiColour(Enum):
    black = 30
    red = 31
    green = 32
    yellow = 33
    blue = 34
    magenta = 35
    cyan = 36
    white = 37


def coloured(colour: AnsiColour, *args, sep=" "):
    startColour = "\x1b[1;" + str(colour.value) + "m"
    endColour = "\x1b[0m"  # reset
    if len(args) == 1:
        if isinstance(args[0], str):
            return startColour + args[0] + endColour
        return startColour + sep.join(map(str, args[0])) + endColour
    else:
        return startColour + sep.join(map(str, args)) + endColour


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

# type hinting for IDE
try:
    import typing
except ImportError:
    typing = None

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


def runCmd(*args, captureOutput=False, captureError=False, input: "typing.Union[str, bytes]"=None, timeout=None,
           printVerboseOnly=False, **kwargs):
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        cmdline = args[0]  # list with parameters was passed
    else:
        cmdline = args
    cmdline = list(map(str, cmdline))  # make sure they are all strings
    printCommand(cmdline, cwd=kwargs.get("cwd"), printVerboseOnly=printVerboseOnly)
    kwargs["cwd"] = str(kwargs["cwd"]) if "cwd" in kwargs else os.getcwd()
    if _cheriConfig.pretend:
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
    with subprocess.Popen(cmdline, **kwargs) as process:
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


def fatalError(*args, sep=" ", fixitHint=None):
    # we ignore fatal errors when simulating a run
    if _cheriConfig.pretend:
        print(coloured(AnsiColour.red, ("Potential fatal error:",) + args, sep=sep))
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
def setEnv(**environ):
    """
    Temporarily set the process environment variables.

    >>> with set_env(PLUGINS_DIR=u'test/plugins'):
    ...   "PLUGINS_DIR" in os.environ
    True

    >>> "PLUGINS_DIR" in os.environ
    False

    :type environ: dict[str, unicode]
    :param environ: Environment variables to set
    """
    old_environ = dict(os.environ)
    for k, v in environ.items():
        printCommand("export", k + "=" + v, printVerboseOnly=True)
    os.environ.update(environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_environ)


class ConfigLoader(object):
    _parser = argparse.ArgumentParser(formatter_class=
                                      lambda prog: argparse.HelpFormatter(prog, width=shutil.get_terminal_size()[0]))
    options = []
    _parsedArgs = None
    _JSON = {}  # type: dict
    values = OrderedDict()
    # argument groups:
    revisionGroup = _parser.add_argument_group("Specifying git revisions", "Useful if the current HEAD of a repository "
                                               "does not work but an older one did.")
    remoteBuilderGroup = _parser.add_argument_group("Specifying a remote FreeBSD build server",
                                                    "Useful if you want to create a CHERI SDK on a Linux or OS X host"
                                                    " to allow cross compilation to a CHERI target.")

    cheriBitsGroup = _parser.add_mutually_exclusive_group()

    @classmethod
    def loadTargets(cls) -> list:
        """
        Loads the configuration from the command line and the JSON file
        :return The targets to build
        """
        cls._parser.add_argument("targets", metavar="TARGET", type=str, nargs="*",
                                 help="The targets to build", default=["all"])
        configdir = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        defaultConfigPath = Path(configdir, "cheribuild.json")
        cls._parser.add_argument("--config-file", metavar="FILE", type=str, default=str(defaultConfigPath),
                                 help="The config file that is used to load the default settings (default: '" +
                                      str(defaultConfigPath) + "')")
        try:
            import argcomplete
            argcomplete.autocomplete(cls._parser)
        except ImportError:
            pass
        cls._parsedArgs = cls._parser.parse_args()
        try:
            cls._configPath = Path(os.path.expanduser(cls._parsedArgs.config_file)).absolute()
            if cls._configPath.exists():
                with cls._configPath.open("r") as f:
                    cls._JSON = json.load(f, encoding="utf-8")
            else:
                print("Configuration file", cls._configPath, "does not exist, using only command line arguments.")
        except Exception as e:
            print(coloured(AnsiColour.red, "Could not load config file", cls._configPath, "-", e))
        return cls._parsedArgs.targets

    @classmethod
    def addOption(cls, name: str, shortname=None, default=None, type=None, group=None, **kwargs):
        if default and not callable(default) and "help" in kwargs:
            # only add the default string if it is not lambda
            kwargs["help"] = kwargs["help"] + " (default: \'" + str(default) + "\')"
        parserObj = group if group else cls._parser
        if shortname:
            action = parserObj.add_argument("--" + name, "-" + shortname, **kwargs)
        else:
            action = parserObj.add_argument("--" + name, **kwargs)
        assert isinstance(action, argparse.Action)
        assert not action.default  # we handle the default value manually
        assert not action.type  # we handle the type of the value manually
        result = cls(action, default, type)
        cls.options.append(result)
        return result

    @classmethod
    def addBoolOption(cls, name: str, shortname=None, **kwargs) -> bool:
        kwargs["default"] = False
        return cls.addOption(name, shortname, action="store_true", type=bool, **kwargs)

    @classmethod
    def addPathOption(cls, name: str, shortname=None, **kwargs) -> Path:
        # we have to make sure we resolve this to an absolute path because otherwise steps where CWD is different fail!
        return cls.addOption(name, shortname, type=lambda s: Path(s).absolute(), **kwargs)

    def __init__(self, action: argparse.Action, default, valueType):
        self.action = action
        self.default = default
        self.valueType = valueType
        self._cached = None
        pass

    def _loadOption(self, config: "CheriConfig"):
        assert self._parsedArgs  # load() must have been called before using this object
        assert hasattr(self._parsedArgs, self.action.dest)
        isDefault = False
        result = getattr(self._parsedArgs, self.action.dest)
        if not result:
            isDefault = True
            # allow lambdas as default values
            if callable(self.default):
                result = self.default(config)
            else:
                result = self.default
        # override default options from the JSON file
        assert self.action.option_strings[0].startswith("--")
        jsonKey = self.action.option_strings[0][2:]  # strip the initial --
        fromJSON = self._JSON.get(jsonKey, None)
        if not fromJSON:
            # also check action.dest (as a fallback so I don't have to update all my config files right now)
            fromJSON = self._JSON.get(self.action.dest, None)
            if fromJSON:
                print(coloured(AnsiColour.cyan, "Old JSON key", self.action.dest, "used, please use",
                               jsonKey, "instead"))
        if fromJSON and isDefault:
            print(coloured(AnsiColour.blue, "Overriding default value for", jsonKey,
                           "with value from JSON:", fromJSON))
            result = fromJSON
        if result:
            # make sure we don't call str(None) which would result in "None"
            result = self.valueType(result)  # make sure it has the right type (e.g. Path, int, bool, str)

        ConfigLoader.values[jsonKey] = result  # just for debugging
        return result

    def __get__(self, instance: "CheriConfig", owner):
        if not self._cached:
            self._cached = self._loadOption(instance)
        return self._cached


def defaultNumberOfMakeJobs():
    makeJobs = os.cpu_count()
    if makeJobs > 24:
        # don't use up all the resources on shared build systems
        # (you can still override this with the -j command line option)
        makeJobs = 16
    return makeJobs


def defaultSshForwardingPort():
    # chose a different port for each user (hopefully it isn't in use yet)
    return 9999 + ((os.getuid() - 1000) % 10000)


def defaultDiskImagePath(conf: "CheriConfig"):
    if conf.cheriBits == 128:
        return conf.outputRoot / "cheri128-disk.qcow2"
    return conf.outputRoot / "cheri256-disk.qcow2"


def defaultClang37Tool(basename: str):
    # TODO: also accept 3.8, 3.9, etc binaries
    # TODO: search through path and list all clang++.* and clang.* binaries
    # try to find clang 3.7, otherwise fall back to system clang
    guess = shutil.which(basename + "37")
    if not guess:
        guess = shutil.which(basename + "-3.7")
    if not guess:
        guess = shutil.which(basename)
    return guess


class CheriConfig(object):
    # boolean flags
    pretend = ConfigLoader.addBoolOption("pretend", "p", help="Only print the commands instead of running them")
    quiet = ConfigLoader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
    verbose = ConfigLoader.addBoolOption("verbose", "v", help="Print all commmands that are executed")
    clean = ConfigLoader.addBoolOption("clean", "c", help="Remove the build directory before build")
    force = ConfigLoader.addBoolOption("force", "f", help="Don't prompt for user input but use the default action")
    skipUpdate = ConfigLoader.addBoolOption("skip-update", help="Skip the git pull step")
    skipConfigure = ConfigLoader.addBoolOption("skip-configure", help="Skip the configure step")
    skipInstall = ConfigLoader.addBoolOption("skip-install", help="Skip the install step (only do the build)")
    skipBuildworld = ConfigLoader.addBoolOption("skip-buildworld", help="Skip the FreeBSD buildworld step -> only build"
                                                " and install the kernel")
    listTargets = ConfigLoader.addBoolOption("list-targets", help="List all available targets and exit")
    dumpConfig = ConfigLoader.addBoolOption("dump-configuration", help="Print the current configuration as JSON."
                                            " This can be saved to ~/.config/cheribuild.json to make it persistent")
    skipDependencies = ConfigLoader.addBoolOption("skip-dependencies", "t",
                                                  help="This option no longer does anything and is only included to"
                                                       "allow running existing command lines")
    includeDependencies = ConfigLoader.addBoolOption("include-dependencies", "d", help="Also build the dependencies "
                                                     "of targets passed on the command line. Targets passed on the"
                                                     "command line will be reordered and processed in an order that "
                                                     "ensures dependencies are built before the real target. (run "
                                                     " with --list-targets for more information)")
    disableTMPFS = ConfigLoader.addBoolOption("disable-tmpfs", help="Don't make /tmp a TMPFS mount in the CHERIBSD system image. This is a workaround in case TMPFS is not working correctly")
    noLogfile = ConfigLoader.addBoolOption("no-logfile", help="Don't write a logfile for the build steps")

    _buildCheri128 = ConfigLoader.addBoolOption("cheri-128", "-128", group=ConfigLoader.cheriBitsGroup,
                                                help="Shortcut for --cheri-bits=128")
    _buildCheri256 = ConfigLoader.addBoolOption("cheri-256", "-256", group=ConfigLoader.cheriBitsGroup,
                                                help="Shortcut for --cheri-bits=256")
    _cheriBits = ConfigLoader.addOption("cheri-bits", type=int, group=ConfigLoader.cheriBitsGroup, choices=["128", "256"],
                                        default=256, help="Whether to build the whole software stack for 128 or 256 bit"
                                        " CHERI. The output directories will be suffixed with the number of bits to"
                                        " make sure the right binaries are being used."
                                        " WARNING: 128-bit CHERI is still very unstable.")

    # configurable paths
    sourceRoot = ConfigLoader.addPathOption("source-root", default=Path(os.path.expanduser("~/cheri")),
                                            help="The directory to store all sources")
    outputRoot = ConfigLoader.addPathOption("output-root", default=lambda p: (p.sourceRoot / "output"),
                                            help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
    extraFiles = ConfigLoader.addPathOption("extra-files", default=lambda p: (p.sourceRoot / "extra-files"),
                                            help="A directory with additional files that will be added to the image "
                                                 "(default: '<OUTPUT_ROOT>/extra-files')")
    clangPath = ConfigLoader.addPathOption("clang-path", default=defaultClang37Tool("clang"),
                                           help="The Clang C compiler to use for compiling LLVM+Clang (must be at "
                                                "least version 3.7)")
    clangPlusPlusPath = ConfigLoader.addPathOption("clang++-path", default=defaultClang37Tool("clang++"),
                                                   help="The Clang C++ compiler to use for compiling LLVM+Clang (must "
                                                        "be at least version 3.7)")
    # TODO: only create a qcow2 image?
    diskImage = ConfigLoader.addPathOption("disk-image-path", default=defaultDiskImagePath, help="The output path for"
                                           " the QEMU disk image (default: '<OUTPUT_ROOT>/cheri256-disk.qcow2')")

    # other options
    makeJobs = ConfigLoader.addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(),
                                      help="Number of jobs to use for compiling")  # type: int
    sshForwardingPort = ConfigLoader.addOption("ssh-forwarding-port", "s", type=int, default=defaultSshForwardingPort(),
                                               help="The port to use on localhost to forward the QEMU ssh port. "
                                                    "You can then use `ssh root@localhost -p $PORT` connect to the VM",
                                               metavar="PORT")  # type: int
    extraMakeOptions = " ".join([
        "-DWITHOUT_TESTS",  # seems to break the creation of disk-image (METALOG is invalid)
        "-DWITHOUT_HTML",  # should not be needed
        "-DWITHOUT_SENDMAIL", "-DWITHOUT_MAIL",  # no need for sendmail
        "-DWITHOUT_SVNLITE",  # no need for SVN
        # "-DWITHOUT_GAMES",  # not needed
        # "-DWITHOUT_MAN",  # seems to be a majority of the install time
        # "-DWITH_FAST_DEPEND",  # no separate make depend step, do it while compiling
        # "-DWITH_INSTALL_AS_USER", should be enforced by -DNO_ROOT
        # "-DWITH_DIRDEPS_BUILD", "-DWITH_DIRDEPS_CACHE",  # experimental fast build options
        # "-DWITH_LIBCHERI_JEMALLOC"  # use jemalloc instead of -lmalloc_simple
    ])

    cheribsdExtraMakeOptions = ConfigLoader.addOption("cheribsd-make-options", type=str, default=extraMakeOptions,
                                                      help="Additional options to be passed to make when "
                                                      "building CHERIBSD. See man src.conf for more info")
    # allow overriding the git revisions in case there is a regression
    cheriBsdRevision = ConfigLoader.addOption("cheribsd-revision", type=str, metavar="GIT_COMMIT_ID",
                                              help="The git revision or branch of CHERIBSD to check out",
                                              group=ConfigLoader.revisionGroup)  # type: str
    llvmRevision = ConfigLoader.addOption("llvm-revision", type=str, metavar="GIT_COMMIT_ID",
                                          help="The git revision or branch of LLVM to check out",
                                          group=ConfigLoader.revisionGroup)  # type: str
    clangRevision = ConfigLoader.addOption("clang-revision", type=str, metavar="GIT_COMMIT_ID",
                                           help="The git revision or branch of clang to check out",
                                           group=ConfigLoader.revisionGroup)  # type: str
    lldbRevision = ConfigLoader.addOption("lldb-revision", type=str, metavar="GIT_COMMIT_ID",
                                          help="The git revision or branch of clang to check out",
                                          group=ConfigLoader.revisionGroup)  # type: str
    qemuRevision = ConfigLoader.addOption("qemu-revision", type=str, metavar="GIT_COMMIT_ID",
                                          help="The git revision or branch of QEMU to check out",
                                          group=ConfigLoader.revisionGroup)  # type: str

    # To allow building CHERI software on non-FreeBSD systems
    freeBsdBuildMachine = ConfigLoader.addOption("freebsd-builder-hostname", type=str, metavar="SSH_HOSTNAME",
                                                 help="This string will be passed to ssh and be something like "
                                                      "user@hostname of a FreeBSD system that can be used to build "
                                                      "CHERIBSD. Can also be the name of a host in  ~/.ssh/config.",
                                                 group=ConfigLoader.remoteBuilderGroup)  # type: str
    # TODO: query this from the remote machine instead of needed an options
    freeBsdBuilderOutputPath = ConfigLoader.addOption("freebsd-builder-output-path", type=str, metavar="PATH",
                                                      help="The path where the cheribuild output is stored on the"
                                                           " FreeBSD build server.",
                                                      group=ConfigLoader.remoteBuilderGroup)  # type: str
    freeBsdBuilderCopyOnly = ConfigLoader.addBoolOption("freebsd-builder-copy-only", help="Only scp the SDK from the"
                                                        "FreeBSD build server and don't build the SDK first.",
                                                        group=ConfigLoader.remoteBuilderGroup)

    def __init__(self):
        self.targets = ConfigLoader.loadTargets()
        self.makeJFlag = "-j" + str(self.makeJobs)

        if self._buildCheri128:
            self.cheriBits = 128
        elif self._buildCheri256:
            self.cheriBits = 256
        else:
            self.cheriBits = self._cheriBits
        self.cheriBitsStr = str(self.cheriBits)

        if not self.quiet:
            print("Sources will be stored in", self.sourceRoot)
            print("Build artifacts will be stored in", self.outputRoot)
            print("Extra files for disk image will be searched for in", self.extraFiles)
            print("Disk image will saved to", self.diskImage)

        # now the derived config options
        self.cheribsdRootfs = self.outputRoot / ("rootfs" + self.cheriBitsStr)
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / ("cheribsd-obj-" + self.cheriBitsStr)
        self.sdkDirectoryName = "sdk" + self.cheriBitsStr
        self.sdkDir = self.outputRoot / self.sdkDirectoryName  # qemu and binutils (and llvm/clang)
        self.otherToolsDir = self.outputRoot / "bootstrap"
        self.dollarPathWithOtherTools = str(self.otherToolsDir / "bin") + ":" + os.getenv("PATH")
        self.sdkSysrootDir = self.sdkDir / "sysroot"
        self.sysrootArchiveName = "cheri-sysroot.tar.gz"

        # for debugging purposes print all the options
        for i in ConfigLoader.options:
            i.__get__(self, CheriConfig)  # for loading of lazy value
        if self.verbose:
            print("cheribuild.py configuration:", dict(ConfigLoader.values))


class Project(object):
    # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
    clearLineSequence = b"\x1b[2K\r"

    cmakeInstallInstructions = ("Use your package manager to install CMake > 3.4 or run "
                                "`cheribuild.py cmake` to install the latest version locally")

    def __init__(self, config: CheriConfig, *, projectName: str=None, sourceDir: Path=None, buildDir: Path=None,
                 installDir: Path=None, gitUrl="", gitRevision=None, appendCheriBitsToBuildDir=False):
        className = self.__class__.__name__
        if className.startswith("Build"):
            self.projectName = className[len("Build"):].replace("_", "-")
        elif not projectName:
            fatalError("Project name is not set and cannot infer from class", className)
        else:
            self.projectName = projectName
        self.projectNameLower = self.projectName.lower()

        self.gitUrl = gitUrl
        self.gitRevision = gitRevision
        self.gitBranch = ""
        self.config = config
        self.sourceDir = Path(sourceDir if sourceDir else config.sourceRoot / self.projectNameLower)
        # make sure we have different build dirs for LLVM/CHERIBSD/QEMU 128 and 256,
        buildDirSuffix = "-" + config.cheriBitsStr + "-build" if appendCheriBitsToBuildDir else "-build"
        self.buildDir = Path(buildDir if buildDir else config.outputRoot / (self.projectNameLower + buildDirSuffix))
        self.installDir = installDir
        self.makeCommand = "make"
        self.configureCommand = ""
        self._systemDepsChecked = False
        # non-assignable variables:
        self.commonMakeArgs = []
        self.configureArgs = []  # type: typing.List[str]
        self.configureEnvironment = {}  # type: typing.Dict[str,str]
        self.__requiredSystemTools = {}  # type: typing.Dict[str, Any]
        self._preventAssign = True

    # Make sure that API is used properly
    def __setattr__(self, name, value):
        # if self.__dict__.get("_locked") and name == "x":
        #     raise AttributeError, "MyClass does not allow assignment to .x member"
        # self.__dict__[name] = value
        if self.__dict__.get("_preventAssign") and name in ("configureArgs", "configureEnvironment", "commonMakeArgs"):
            fatalError("Project." + name + " mustn't be set, only modification is allowed.", "Called from",
                       self.__class__.__name__)
        self.__dict__[name] = value

    def _addRequiredSystemTool(self, executable: str, installInstructions=None):
        self.__requiredSystemTools[executable] = installInstructions

    def queryYesNo(self, message: str="", *, defaultResult=False, forceResult=True) -> bool:
        yesNoStr = " [Y]/n " if defaultResult else " y/[N] "
        if self.config.pretend:
            print(message + yesNoStr)
            return True  # in pretend mode we always return true
        if self.config.force:
            # in force mode we always return the forced result without prompting the user
            print(message + yesNoStr, "y")
            return forceResult
        if not sys.__stdin__.isatty():
            return defaultResult  # can't get any input -> return the default
        result = input(message + yesNoStr)
        if defaultResult:
            return not result.startswith("n")  # if default is yes accept anything other than strings starting with "n"
        return str(result).lower().startswith("y")  # anything but y will be treated as false

    def runGitCmd(self, *args, cwd=None, **kwargs):
        if not cwd:
            cwd = self.sourceDir
        return runCmd("git", *args, cwd=cwd, **kwargs)

    def _ensureGitRepoIsCloned(self, *, srcDir: Path, remoteUrl, initialBranch=None):
        if not (srcDir / ".git").is_dir():
            print(srcDir, "is not a git repository. Clone it from' " + remoteUrl + "'?", end="")
            if not self.queryYesNo(defaultResult=False):
                fatalError("Sources for", str(srcDir), " missing!")
            if initialBranch:
                runCmd("git", "clone", "--recurse-submodules", "--branch", initialBranch, remoteUrl, srcDir)
            else:
                runCmd("git", "clone", "--recurse-submodules", remoteUrl, srcDir)

    def _updateGitRepo(self, srcDir: Path, remoteUrl, *, revision=None, initialBranch=None):
        self._ensureGitRepoIsCloned(srcDir=srcDir, remoteUrl=remoteUrl, initialBranch=initialBranch)
        # make sure we run git stash if we discover any local changes
        hasChanges = len(runCmd("git", "diff", captureOutput=True, cwd=srcDir, printVerboseOnly=True).stdout) > 1
        if hasChanges:
            runCmd("git", "stash", cwd=srcDir, printVerboseOnly=True)
        runCmd("git", "pull", "--recurse-submodules", "--rebase", cwd=srcDir, printVerboseOnly=True)
        runCmd("git", "submodule", "update", "--recursive", cwd=srcDir, printVerboseOnly=True)
        if hasChanges:
            runCmd("git", "stash", "pop", cwd=srcDir, printVerboseOnly=True)
        if revision:
            runCmd("git", "checkout", revision, cwd=srcDir, printVerboseOnly=True)

    def _makedirs(self, path: Path):
        printCommand("mkdir", "-p", path, printVerboseOnly=True)
        if not self.config.pretend:
            os.makedirs(str(path), exist_ok=True)

    # removes a directory tree if --clean is passed (or force=True parameter is passed)
    def _cleanDir(self, path: Path, force=False):
        if (self.config.clean or force) and path.is_dir():
            # http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
            # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
            runCmd("rm", "-rf", str(path))

        # make sure the dir is empty afterwards
        self._makedirs(path)

    def readFile(self, file: Path) -> str:
        # just return an empty string in pretend mode
        if self.config.pretend and not file.is_file():
            return "\n"
        with file.open("r", encoding="utf-8") as f:
            return f.read()

    def writeFile(self, file: Path, contents: str, *, overwrite: bool, noCommandPrint=False) -> None:
        """
        :param file: The target path to write contents to
        :param contents: the contents of the new file
        :param overwrite: If true the file will be overwritten, otherwise it will cause an error if the file exists
        :param noCommandPrint: don't ever print the echo commmand (even in verbose)
        """
        if not noCommandPrint:
            printCommand("echo", contents, colour=AnsiColour.green, outputFile=file, printVerboseOnly=True)
        if self.config.pretend:
            return
        if not overwrite and file.exists():
            fatalError("File", file, "already exists!")
        self._makedirs(file.parent)
        with file.open("w", encoding="utf-8") as f:
            f.write(contents)

    def copyFile(self, src: Path, dest: Path, *, force=False):
        if force:
            printCommand("cp", "-f", src, dest, printVerboseOnly=True)
        else:
            printCommand("cp", src, dest, printVerboseOnly=True)
        if self.config.pretend:
            return
        if dest.exists() and force:
            dest.unlink()
        shutil.copy(str(src), str(dest), follow_symlinks=False)

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        # by default we don't keep any line persistent, just have updating output
        sys.stdout.buffer.write(Project.clearLineSequence)
        sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
        sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
        sys.stdout.buffer.flush()

    @staticmethod
    def _handleStdErr(outfile, stream, fileLock, noLogfile):
        for errLine in stream:
            with fileLock:
                sys.stderr.buffer.write(errLine)
                sys.stderr.buffer.flush()
                if not noLogfile:
                    outfile.write(errLine)

    def runMake(self, args: "typing.List[str]", makeTarget="", *, makeCommand: str=None, logfileName: str=None,
                cwd: Path=None, env=None) -> None:
        if not makeCommand:
            makeCommand = self.makeCommand
        if not cwd:
            cwd = self.buildDir

        if makeTarget:
            allArgs = args + [makeTarget]
            if not logfileName:
                logfileName = self.makeCommand + "." + makeTarget
        else:
            allArgs = args
            if not logfileName:
                logfileName = makeCommand
        allArgs = [makeCommand] + allArgs
        starttime = time.time()
        self.runWithLogfile(allArgs, logfileName=logfileName, stdoutFilter=self._makeStdoutFilter, cwd=cwd, env=env)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", self.makeCommand, makeTarget, "took", time.time() - starttime, "seconds")

    def runWithLogfile(self, args: "typing.Sequence[str]", logfileName: str, *, stdoutFilter=None, cwd: Path = None,
                       env: dict=None) -> None:
        """
        Runs make and logs the output
        config.quiet doesn't display anything, normal only status updates and config.verbose everything
        :param args: the command to run (e.g. ["make", "-j32"])
        :param logfileName: the name of the logfile (e.g. "build.log")
        :param cwd the directory to run make in (defaults to self.buildDir)
        :param stdoutFilter a filter to use for standard output (a function that takes a single bytes argument)
        :param env the environment to pass to make
        """
        printCommand(args, cwd=cwd)
        # make sure that env is either None or a os.environ with the updated entries entries
        newEnv = None
        if env:
            newEnv = os.environ.copy()
            newEnv.update(env)
        else:
            newEnv = None
        assert not logfileName.startswith("/")
        if self.config.noLogfile:
            logfilePath = Path(os.devnull)
        else:
            logfilePath = self.buildDir / (logfileName + ".log")
        print("Saving build log to", logfilePath)
        if self.config.pretend:
            return
        if self.config.verbose:
            stdoutFilter = None

        if logfilePath.is_file():
            logfilePath.unlink()  # remove old logfile
        args = list(map(str, args))  # make sure all arguments are strings
        cmdStr = " ".join([shlex.quote(s) for s in args])
        # open file in append mode
        with logfilePath.open("ab") as logfile:
            # print the command and then the logfile
            logfile.write(cmdStr.encode("utf-8") + b"\n\n")
            if self.config.quiet:
                # a lot more efficient than filtering every line
                subprocess.check_call(args, cwd=str(cwd), stdout=logfile, stderr=logfile, env=newEnv)
                return
            make = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=newEnv)
            # use a thread to print stderr output and write it to logfile (not using a thread would block)
            logfileLock = threading.Lock()  # we need a mutex so the logfile line buffer doesn't get messed up
            stderrThread = threading.Thread(target=self._handleStdErr,
                                            args=(logfile, make.stderr, logfileLock, self.config.noLogfile))
            stderrThread.start()
            for line in make.stdout:
                with logfileLock:  # make sure we don't interleave stdout and stderr lines
                    if not self.config.noLogfile:
                        # will be /dev/null with noLogfile anyway but saves a syscall per line
                        logfile.write(line)
                    if stdoutFilter:
                        stdoutFilter(line)
                    else:
                        sys.stdout.buffer.write(line)
                        sys.stdout.buffer.flush()
            retcode = make.wait()
            remainingErr, remainingOut = make.communicate()
            sys.stderr.buffer.write(remainingErr)
            logfile.write(remainingErr)
            sys.stdout.buffer.write(remainingOut)
            logfile.write(remainingOut)
            if stdoutFilter:
                # add the final new line after the filtering
                sys.stdout.buffer.write(b"\n")
            stderrThread.join()
            if retcode:
                raise SystemExit("Command \"%s\" failed with exit code %d.\nSee %s for details." %
                                 (cmdStr, retcode, logfile.name))

    @staticmethod
    def createBuildtoolTargetSymlinks(tool: Path, toolName: str=None):
        """
        Create mips4-unknown-freebsd, cheri-unknown-freebsd and mips64-unknown-freebsd prefixed symlinks
        for build tools like clang, ld, etc.
        :param tool: the binary for which the symlinks will be created
        :param toolName: the unprefixed name of the tool (defaults to tool.name)
        """
        if not tool.is_file():
            fatalError("Attempting to creat symlink to non-existent build tool:", tool)
        if not toolName:
            toolName = tool.name
        for target in ("mips4-unknown-freebsd-", "cheri-unknown-freebsd-", "mips64-unknown-freebsd-"):
            if (target + toolName) == tool.name:
                continue  # happens for binutils, where prefixed tools are installed
            runCmd("ln", "-fsn", tool.name, target + toolName, cwd=tool.parent, printVerboseOnly=True)

    def dependencyError(self, *args, installInstructions: str=None):
        self._systemDepsChecked = True  # make sure this is always set
        fatalError(*args, fixitHint=installInstructions)

    def checkSystemDependencies(self) -> None:
        """
        Checks that all the system dependencies (required tool, etc) are available
        :return: Throws an error if dependencies are missing
        """
        for (tool, installInstructions) in self.__requiredSystemTools.items():
            if not shutil.which(tool):
                if callable(installInstructions):
                    installInstructions = installInstructions()
                if not installInstructions:
                    installInstructions = "Try installing `" + tool + "` using your system package manager."
                self.dependencyError("Required program", tool, "is missing!", installInstructions=installInstructions)
        self._systemDepsChecked = True

    def update(self):
        self._updateGitRepo(self.sourceDir, self.gitUrl, revision=self.gitRevision, initialBranch=self.gitBranch)

    def clean(self):
        # TODO: never use the source dir as a build dir
        # will have to check how well binutils and qemu work there
        if (self.buildDir / ".git").is_dir():
            # just use git clean for cleanup
            runCmd("git", "clean", "-dfx", cwd=self.buildDir)
        else:
            self._cleanDir(self.buildDir)

    def configure(self):
        if self.configureCommand:
            self.runWithLogfile([self.configureCommand] + self.configureArgs,
                                logfileName="configure", cwd=self.buildDir, env=self.configureEnvironment)

    def compile(self):
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag])

    def install(self):
        self.runMake(self.commonMakeArgs, "install")

    def process(self):
        if not self.config.skipUpdate:
            self.update()
        if not self._systemDepsChecked:
            self.checkSystemDependencies()
        assert self._systemDepsChecked, "self._systemDepsChecked must be set by now!"
        if self.config.clean:
            self.clean()
        # always make sure the build dir exists
        if not self.buildDir.is_dir():
            self._makedirs(self.buildDir)
        if not self.config.skipConfigure:
            statusUpdate("Configuring", self.projectName, "... ")
            self.configure()
        statusUpdate("Building", self.projectName, "... ")
        self.compile()
        if not self.config.skipInstall:
            statusUpdate("Installing", self.projectName, "... ")
            self.install()


class CMakeProject(Project):
    """
    Like Project but automatically sets up the defaults for CMake projects
    Sets configure command to CMake, adds -DCMAKE_INSTALL_PREFIX=installdir
    and checks that CMake is installed
    """
    class Generator(Enum):
        Default = 0
        Ninja = 1
        Makefiles = 2

    def __init__(self, *args, generator=Generator.Ninja, buildType="Release", **kwargs):
        super().__init__(*args, **kwargs)
        self.configureCommand = "cmake"
        self._addRequiredSystemTool("cmake", installInstructions=self.cmakeInstallInstructions)
        self.generator = generator
        self.configureArgs.append(str(self.sourceDir))  # TODO: use undocumented -H and -B options?
        if self.generator == CMakeProject.Generator.Ninja:
            self.configureArgs.append("-GNinja")
            self.makeCommand = "ninja"
            self._addRequiredSystemTool("ninja")
        if self.generator == CMakeProject.Generator.Makefiles:
            self.configureArgs.append("-GUnix Makefiles")
        self.configureArgs.append("-DCMAKE_INSTALL_PREFIX=" + str(self.installDir))
        self.configureArgs.append("-DCMAKE_BUILD_TYPE=" + buildType)

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        Project._makeStdoutFilter(line)


class AutotoolsProject(Project):
    """
    Like Project but automatically sets up the defaults for autotools like projects
    Sets configure command to ./configure, adds --prefix=installdir
    """
    def __init__(self, *args, configureScript="configure", **kwargs):
        super().__init__(*args, **kwargs)
        self.configureCommand = self.sourceDir / configureScript
        self.configureArgs.append("--prefix=" + str(self.installDir))
        self.makeCommand = "make"


class BuildQEMU(AutotoolsProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/CTSRD-CHERI/qemu.git", gitRevision=config.qemuRevision)
        self.gitBranch = "qemu-cheri"

        self._addRequiredSystemTool("pkg-config")
        # QEMU will not work with BSD make, need GNU make
        if IS_FREEBSD:
            self._addRequiredSystemTool("gmake")
            self.makeCommand = "gmake"
        else:
            self.makeCommand = "make"

        # TODO: suggest on Ubuntu install libglib2.0-dev libpixman-1-dev libsdl2-dev libgtk2.0-dev

        # there are some -Wdeprected-declarations, etc. warnings with new libraries/compilers and it builds
        # with -Werror by default but we don't want the build to fail because of that -> add -Wno-error
        extraCFlags = "-g -Wno-error"

        if config.cheriBits == 128:
            # enable QEMU 128 bit capabilities
            # https://github.com/CTSRD-CHERI/qemu/commit/40a7fc2823e2356fa5ffe1ee1d672f1d5ec39a12
            extraCFlags += " -DCHERI_128=1"
        self.configureArgs.extend([
            "--target-list=cheri-softmmu",
            "--disable-linux-user",
            "--disable-bsd-user",
            "--disable-xen",
            "--disable-docs",
            "--extra-cflags=" + extraCFlags,
        ])
        if IS_LINUX:
            # "--enable-libnfs", # version on Ubuntu 14.04 is too old? is it needed?
            # self.configureArgs += ["--enable-kvm", "--enable-linux-aio", "--enable-vte", "--enable-sdl",
            #                        "--with-sdlabi=2.0", "--enable-virtfs"]
            self.configureArgs.extend(["--disable-stack-protector"])  # seems to be broken on some Ubuntu 14.04 systems
        else:
            self.configureArgs.extend(["--disable-linux-aio", "--disable-kvm"])

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir():
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir, printVerboseOnly=True)
        super().update()


class BuildBinutils(AutotoolsProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, gitUrl="https://github.com/CTSRD-CHERI/binutils.git")
        # http://marcelog.github.io/articles/cross_freebsd_compiler_in_linux.html
        self.gitBranch = "cheribsd"  # the default branch "cheri" won't work for cross-compiling

        # If we don't use a patched binutils version on linux we get an ld binary that is
        # only able to handle 32 bit mips:
        # GNU ld (GNU Binutils) 2.18
        # Supported emulations:
        #     elf32ebmip

        # The version from the FreeBSD source tree supports the right targets:
        # GNU ld 2.17.50 [FreeBSD] 2007-07-03
        # Supported emulations:
        #    elf64btsmip_fbsd
        #    elf32btsmip_fbsd
        #    elf32ltsmip_fbsd
        #    elf64btsmip_fbsd
        #    elf64ltsmip_fbsd
        #    elf32btsmipn32_fbsd
        #    elf32ltsmipn32_fbsd
        self.configureArgs.extend([
            # on cheri gcc -dumpmachine returns mips64-undermydesk-freebsd, however this is not accepted by BFD
            # if we just pass --target=mips64 this apparently defaults to mips64-unknown-elf on freebsd
            # and also on Linux, but let's be explicit in case it assumes ELF binaries to target linux
            # "--target=mips64-undermydesk-freebsd",  # binutils for MIPS64/CHERI
            "--target=mips64-unknown-freebsd",  # binutils for MIPS64/FreeBSD
            "--disable-werror",  # -Werror won't work with recent compilers
            "--enable-ld",  # enable linker (is default, but just be safe)
            "--enable-libssp",  # not sure if this is needed
            "--enable-64-bit-bfd",  # Make sure we always have 64 bit support
            # "--enable-targets=" + enabledTargets,
            # TODO: --with-sysroot doesn't work properly so we need to tell clang not to pass the --sysroot option
            "--with-sysroot=" + str(self.config.sdkSysrootDir),  # as we pass --sysroot to clang we need this option
            "--disable-info",
            #  "--program-prefix=cheri-unknown-freebsd-",
            "MAKEINFO=missing",  # don't build docs, this will fail on recent Linux systems
        ])
        # newer compilers will default to -std=c99 which will break binutils:
        self.configureEnvironment["CFLAGS"] = "-std=gnu89 -O2"

    def update(self):
        self._ensureGitRepoIsCloned(srcDir=self.sourceDir, remoteUrl=self.gitUrl, initialBranch=self.gitBranch)
        # Make sure we have the version that can compile FreeBSD binaries
        status = self.runGitCmd("status", "-b", "-s", "--porcelain", "-u", "no",
                                captureOutput=True, printVerboseOnly=True)
        if not status.stdout.startswith(b"## cheribsd"):
            branches = self.runGitCmd("branch", "--list", captureOutput=True, printVerboseOnly=True).stdout
            if b" cheribsd" not in branches:
                self.runGitCmd("checkout", "-b", "cheribsd", "--track", "origin/cheribsd")
        self.runGitCmd("checkout", "cheribsd")
        super().update()

    def install(self):
        super().install()
        bindir = self.installDir / "bin"
        for tool in "addr2line ld ranlib strip ar nm readelf as objcopy size c++filt objdump strings".split():
            prefixedName = "mips64-unknown-freebsd-" + tool
            if not (bindir / prefixedName).is_file():
                fatalError("Binutils binary", prefixedName, "is missing!")
            # create the right symlinks to the tool (ld -> mips64-unknown-elf-ld, etc)
            runCmd("ln", "-fsn", prefixedName, tool, cwd=bindir)
            # Also symlink cheri-unknown-freebsd-ld -> ld (and the other targets)
            self.createBuildtoolTargetSymlinks(bindir / prefixedName, toolName=tool)


class BuildLLVM(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True)
        self.cCompiler = config.clangPath
        self.cppCompiler = config.clangPlusPlusPath
        # this must be added after checkSystemDependencies
        self.configureArgs.append("-DCMAKE_CXX_COMPILER=" + str(self.cppCompiler))
        self.configureArgs.append("-DCMAKE_C_COMPILER=" + str(self.cCompiler))
        # TODO: add another search for newer clang compilers? Probably not required as we can override it on cmdline
        self.configureArgs.extend([
            "-DLLVM_TOOL_LLDB_BUILD=OFF",  # disable LLDB for now
            # saves a bit of time and but might be slightly broken in current clang:
            "-DCLANG_ENABLE_STATIC_ANALYZER=OFF",  # save some build time by skipping the static analyzer
            "-DCLANG_ENABLE_ARCMT=OFF",  # need to disable ARCMT to disable static analyzer
        ])
        if IS_FREEBSD:
            self.configureArgs.append("-DDEFAULT_SYSROOT=" + str(self.config.sdkSysrootDir))
            self.configureArgs.append("-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd")

        if self.config.cheriBits == 128:
            self.configureArgs.append("-DLLVM_CHERI_IS_128=ON")

    def clang37InstallHint(self):
        if IS_FREEBSD:
            return "Try running `pkg install clang37`"
        osRelease = self.readFile(Path("/etc/os-release")) if Path("/etc/os-release").is_file() else ""
        if "Ubuntu" in osRelease:
            return """Try following the instructions on http://askubuntu.com/questions/735201/installing-clang-3-8-on-ubuntu-14-04-3:
            wget -O - http://llvm.org/apt/llvm-snapshot.gpg.key|sudo apt-key add -
            sudo apt-add-repository "deb http://llvm.org/apt/trusty/ llvm-toolchain-trusty-3.7 main"
            sudo apt-get update
            sudo apt-get install clang-3.7"""
        return "Try installing clang 3.7 or newer using your system package manager"

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        if not self.cCompiler or not self.cppCompiler:
            self.dependencyError("Could not find clang", installInstructions=self.clang37InstallHint())
        # make sure we have at least version 3.7
        versionPattern = re.compile(b"clang version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # clang prints this output to stderr
        versionString = runCmd(self.cCompiler, "-v", captureError=True, printVerboseOnly=True).stderr
        match = versionPattern.search(versionString)
        versionComponents = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if versionComponents < (3, 7):
            versionStr = ".".join(map(str, versionComponents))
            self.dependencyError(self.cCompiler, "version", versionStr, "is too old. Version 3.7 or newer is required.",
                                 installInstructions=self.clang37InstallHint())

    def update(self):
        self._updateGitRepo(self.sourceDir, "https://github.com/CTSRD-CHERI/llvm.git",
                            revision=self.config.llvmRevision)
        self._updateGitRepo(self.sourceDir / "tools/clang", "https://github.com/CTSRD-CHERI/clang.git",
                            revision=self.config.clangRevision)
        self._updateGitRepo(self.sourceDir / "tools/lldb", "https://github.com/CTSRD-CHERI/lldb.git",
                            revision=self.config.lldbRevision, initialBranch="master")

    def install(self):
        super().install()
        # delete the files incompatible with cheribsd
        incompatibleFiles = list(self.installDir.glob("lib/clang/3.*/include/std*"))
        incompatibleFiles += self.installDir.glob("lib/clang/3.*/include/limits.h")
        if len(incompatibleFiles) == 0:
            fatalError("Could not find incompatible builtin includes. Build system changed?")
        print("Removing incompatible builtin includes...")
        for i in incompatibleFiles:
            printCommand("rm", shlex.quote(str(i)), printVerboseOnly=True)
            if not self.config.pretend:
                i.unlink()
        # create a symlink for the target
        self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang")
        self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang++")


class BuildCHERIBSD(Project):
    def __init__(self, config: CheriConfig, *, projectName="cheribsd", kernelConfig="CHERI_MALTA64"):
        super().__init__(config, projectName=projectName, sourceDir=config.sourceRoot / "cheribsd",
                         installDir=config.cheribsdRootfs, buildDir=config.cheribsdObj, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/CTSRD-CHERI/cheribsd.git", gitRevision=config.cheriBsdRevision)
        self.kernelConfig = kernelConfig
        if self.config.cheriBits == 128:
            # make sure we use a kernel with 128 bit CPU features selected
            self.kernelConfig = kernelConfig.replace("CHERI_", "CHERI128_")
        self.binutilsDir = self.config.sdkDir / "mips64/bin"
        self.cheriCC = self.config.sdkDir / "bin/clang"
        self.cheriCXX = self.config.sdkDir / "bin/clang++"
        self.installAsRoot = os.getuid() == 0
        self.commonMakeArgs.extend([
            "CHERI=" + self.config.cheriBitsStr,
            # "-dCl",  # add some debug output to trace commands properly
            "CHERI_CC=" + str(self.cheriCC),
            # "CPUTYPE=mips64", # mipsfpu for hardware float
            # (apparently no longer supported: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            "-DNO_ROOT",  # use this even if current user is root, as without it the METALOG file is not created
            "DEBUG_FLAGS=-g",  # enable debug stuff
            # "CROSS_BINUTILS_PREFIX=" + str(self.binutilsDir),  # use the CHERI-aware binutils and not the builtin ones
            # TODO: once clang can build the kernel:
            #  "-DCROSS_COMPILER_PREFIX=" + str(self.config.sdkDir / "bin")
            "KERNCONF=" + self.kernelConfig,
        ])
        self.commonMakeArgs.extend(shlex.split(self.config.cheribsdExtraMakeOptions))
        if not (self.config.verbose or self.config.quiet):
            # By default we only want to print the status updates -> use make -s so we have to do less filtering
            self.commonMakeArgs.append("-s")

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        if line.startswith(b">>> "):  # major status update
            sys.stdout.buffer.write(Project.clearLineSequence)
            sys.stdout.buffer.write(line)
        elif line.startswith(b"===> "):  # new subdirectory
            # clear the old line to have a continuously updating progress
            sys.stdout.buffer.write(Project.clearLineSequence)
            sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
            sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
            sys.stdout.buffer.flush()
        elif line.startswith(b"-----------"):
            pass  # useless separator
        else:
            sys.stdout.buffer.write(line)

    def _removeSchgFlag(self, *paths: "typing.Iterable[str]"):
        for i in paths:
            file = self.installDir / i
            if file.exists():
                runCmd("chflags", "noschg", str(file))

    def setupEnvironment(self):
        if not self.cheriCC.is_file():
            fatalError("CHERI CC does not exist: ", self.cheriCC)
        if not self.cheriCXX.is_file():
            fatalError("CHERI CXX does not exist: ", self.cheriCXX)
        # if not (self.binutilsDir / "as").is_file():
        #     fatalError("CHERI MIPS binutils are missing. Run 'cheribuild.py binutils'?")
        if not self.config.skipBuildworld:
            if self.installAsRoot:
                # we need to remove the schg flag as otherwise rm -rf will fail to remove these files
                self._removeSchgFlag(
                    "lib/libc.so.7", "lib/libcrypt.so.5", "lib/libthr.so.3", "libexec/ld-cheri-elf.so.1",
                    "libexec/ld-elf.so.1", "sbin/init", "usr/bin/chpass", "usr/bin/chsh", "usr/bin/ypchpass",
                    "usr/bin/ypchfn", "usr/bin/ypchsh", "usr/bin/login", "usr/bin/opieinfo", "usr/bin/opiepasswd",
                    "usr/bin/passwd", "usr/bin/yppasswd", "usr/bin/su", "usr/bin/crontab", "usr/lib/librt.so.1",
                    "var/empty"
                )
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # if we installed as root remove the schg flag from files before cleaning (otherwise rm will fail)
            self._cleanDir(self.installDir, force=True)
        else:
            self._makedirs(self.installDir)

    def clean(self):
        if self.config.skipBuildworld:
            # TODO: only clean the kernel build directory
            fatalError("Not implemented yet!")
        else:
            super().clean()

    def compile(self):
        self.setupEnvironment()
        if not self.config.skipBuildworld:
            self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildworld", cwd=self.sourceDir)
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildkernel", cwd=self.sourceDir)

    def install(self):
        # don't use multiple jobs here
        installArgs = self.commonMakeArgs + ["DESTDIR=" + str(self.installDir)]
        self.runMake(installArgs, "installkernel", cwd=self.sourceDir)
        if not self.config.skipBuildworld:
            self.runMake(installArgs, "installworld", cwd=self.sourceDir)
            self.runMake(installArgs, "distribution", cwd=self.sourceDir)

    def process(self):
        if not IS_FREEBSD:
            statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need to scp",
                         "the required files from another server (see --frebsd-build-server options)")
            return
        # make sure the new clang and other tool are picked up
        # TODO: this shouldn't be needed, we build binutils as part of cheribsd
        path = os.getenv("PATH")
        if not path.startswith(str(self.config.sdkDir)):
            path = str(self.config.sdkDir / "bin") + ":" + path
        with setEnv(MAKEOBJDIRPREFIX=str(self.buildDir), PATH=path):
            super().process()


# Notes:
# Mount the filesystem of a BSD VM: guestmount -a /foo/bar.qcow2 -m /dev/sda1:/:ufstype=ufs2:ufs --ro /mnt/foo
# ufstype=ufs2 is required as the Linux kernel can't automatically determine which UFS filesystem is being used
# Same thing is possible with qemu-nbd, but needs root (might be faster)

class BuildDiskImage(Project):
    def __init__(self, config):
        super().__init__(config, projectName="disk-image")
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifestFile = None  # type: Path
        self.userGroupDbDir = self.config.cheribsdSources / "etc"
        self.extraFiles = []  # type: typing.List[Path]
        self._addRequiredSystemTool("ssh-keygen")
        self._addRequiredSystemTool("makefs")

    def addFileToImage(self, file: Path, targetDir: str, user="root", group="wheel", mode="0644"):
        assert not targetDir.startswith("/")
        # e.g. "install -N /home/alr48/cheri/cheribsd/etc -U -M /home/alr48/cheri/output/rootfs//METALOG
        # -D /home/alr48/cheri/output/rootfs -o root -g wheel -m 444 alarm.3.gz
        # /home/alr48/cheri/output/rootfs/usr/share/man/man3/"
        parentDir = self.config.cheribsdRootfs / targetDir
        commonArgs = [
            "-N", str(self.userGroupDbDir),  # Use a custom user/group database text file
            "-U",  # Indicate that install is running unprivileged (do not change uid/gid)
            "-M", str(self.manifestFile),  # the mtree manifest to write the entry to
            "-D", str(self.config.cheribsdRootfs),  # DESTDIR (will be stripped from the start of the mtree file
            "-o", user, "-g", group,  # uid and gid
            "-m", mode,  # access rights
        ]
        # install -d: Create directories. Missing parent directories are created as required.
        # If we only create the parent directory if it doesn't exist yet we might break the build if rootfs wasn't
        # cleaned before running disk-image. We get errors like this:
        #   makefs: ./root/.ssh: missing directory in specification
        #   makefs: failed at line 27169 of the specification
        # Having the directory in the spec multiple times is fine, so we just do that instead
        runCmd(["install", "-d"] + commonArgs + [str(parentDir)], printVerboseOnly=True)
        # need to pass target file and destination dir so that METALOG can be filled correctly
        runCmd(["install"] + commonArgs + [str(file), str(parentDir)], printVerboseOnly=True)
        if file in self.extraFiles:
            self.extraFiles.remove(file)  # remove it from extraFiles so we don't install it twice

    def createFileForImage(self, outDir: Path, pathInImage: str, *, contents: str="\n", showContentsByDefault=True):
        if pathInImage.startswith("/"):
            pathInImage = pathInImage[1:]
        assert not pathInImage.startswith("/")
        userProvided = self.config.extraFiles / pathInImage
        if userProvided.is_file():
            print("Using user provided /", pathInImage, " instead of generating default", sep="")
            self.extraFiles.remove(userProvided)
            targetFile = userProvided
        else:
            assert userProvided not in self.extraFiles
            targetFile = outDir / pathInImage
            if self.config.verbose or (showContentsByDefault and not self.config.quiet):
                print("Generating /", pathInImage, " with the following contents:\n",
                      coloured(AnsiColour.green, contents), sep="", end="")
            self.writeFile(targetFile, contents, noCommandPrint=True, overwrite=False)
        self.addFileToImage(targetFile, str(Path(pathInImage).parent))

    def prepareRootfs(self, outDir: Path):
        self.manifestFile = outDir / "METALOG"
        self.copyFile(self.config.cheribsdRootfs / "METALOG", self.manifestFile)

        # we need to add /etc/fstab and /etc/rc.conf as well as the SSH host keys to the disk-image
        # If they do not exist in the extra-files directory yet we generate a default one and use that
        # Additionally all other files in the extra-files directory will be added to the disk image
        for root, dirnames, filenames in os.walk(str(self.config.extraFiles)):
            for filename in filenames:
                self.extraFiles.append(Path(root, filename))

        # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
        # Overlay extra-files over additional stuff over cheribsd rootfs dir

        if self.config.disableTMPFS:
            self.createFileForImage(outDir, "/etc/fstab", contents="/dev/ada0 / ufs rw 1 1\n")
        else:
            self.createFileForImage(outDir, "/etc/fstab", contents="/dev/ada0 / ufs rw 1 1\ntmpfs /tmp tmpfs rw 0 0\n")
        # enable ssh and set hostname
        # TODO: use separate file in /etc/rc.conf.d/ ?
        rcConfContents = """hostname="qemu-cheri-{username}"
ifconfig_le0="DHCP"  # use DHCP on the standard QEMU usermode nic
sshd_enable="YES"
sendmail_enable="NONE"  # completely disable sendmail
# disable cron, as this removes errors like: cron[600]: _secure_path: cannot stat /etc/login.conf: Permission denied
# it should also speed up boot a bit
cron_enable="NO"
# tmpmfs="YES" only creates a 20 MB ramdisk for /tmp, use /etc/fstab and tmpfs instead
# the extra m in tmpmfs is not a typo: it means mount /tmp as a memory filesystem (MFS)
# tmpmfs="YES"
""".format(username=os.getlogin())
        self.createFileForImage(outDir, "/etc/rc.conf", contents=rcConfContents)

        # make sure that the disk image always has the same SSH host keys
        # If they don't exist the system will generate one on first boot and we have to accept them every time
        self.generateSshHostKeys()

        print("Adding 'PermitRootLogin without-password' to /etc/ssh/sshd_config")
        # make sure we can login as root with pubkey auth:
        sshdConfig = self.config.cheribsdRootfs / "etc/ssh/sshd_config"
        newSshdConfigContents = self.readFile(sshdConfig)
        newSshdConfigContents += "\n# Allow root login with pubkey auth:\nPermitRootLogin without-password\n"
        self.createFileForImage(outDir, "/etc/ssh/sshd_config", contents=newSshdConfigContents,
                                showContentsByDefault=False)

        # now try adding the right ~/.ssh/authorized_keys
        authorizedKeys = self.config.extraFiles / "root/.ssh/authorized_keys"
        if not authorizedKeys.is_file():
            sshKeys = list(Path(os.path.expanduser("~/.ssh/")).glob("id_*.pub"))
            if len(sshKeys) > 0:
                print("Found the following ssh keys:", list(map(str, sshKeys)))
                if self.queryYesNo("Should they be added to /root/.ssh/authorized_keys?", defaultResult=True):
                    contents = ""
                    for pubkey in sshKeys:
                        contents += self.readFile(pubkey)
                    self.createFileForImage(outDir, "/root/.ssh/authorized_keys", contents=contents)
                    if self.queryYesNo("Should this authorized_keys file be used by default? (You can always change them by editing/deleting '" +
                                       str(authorizedKeys) + "')?", defaultResult=False):
                        self._makedirs(authorizedKeys.parent)
                        self.copyFile(outDir / "root/.ssh/authorized_keys", authorizedKeys)

    def makeImage(self):
        # check that qemu-img exists before starting the potentially long-running makefs command
        qemuImgCommand = self.config.sdkDir / "bin/qemu-img"
        if not qemuImgCommand.is_file():
            systemQemuImg = shutil.which("qemu-img")
            if systemQemuImg:
                print("qemu-img from CHERI SDK not found, falling back to system qemu-img")
                qemuImgCommand = Path(systemQemuImg)
            else:
                fatalError("qemu-img command was not found!", fixitHint="Make sure to build target qemu first")

        rawDiskImage = Path(str(self.config.diskImage).replace(".qcow2", ".img"))
        runCmd([
            "makefs",
            "-b", "70%",  # minimum 70% free blocks
            "-f", "30%",  # minimum 30% free inodes
            "-M", "4g",  # minimum image size = 4GB
            "-B", "be",  # big endian byte order
            "-F", self.manifestFile,  # use METALOG as the manifest for the disk image
            "-N", self.userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file
            # which makes sure that the numeric UID values are correct
            rawDiskImage,  # output file
            self.config.cheribsdRootfs  # directory tree to use for the image
        ])
        # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
        if self.config.verbose:
            runCmd(qemuImgCommand, "info", rawDiskImage)
        runCmd("rm", "-f", self.config.diskImage, printVerboseOnly=True)
        # create a qcow2 version from the raw image:
        runCmd(qemuImgCommand, "convert",
               "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
               "-O", "qcow2",  # convert to qcow2 format
               rawDiskImage,  # input file
               self.config.diskImage)  # output file
        if self.config.verbose:
            runCmd(qemuImgCommand, "info", self.config.diskImage)

    def process(self):
        if not (self.config.cheribsdRootfs / "METALOG").is_file():
            fatalError("mtree manifest", self.config.cheribsdRootfs / "METALOG", "is missing")
        if not (self.userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in ", self.userGroupDbDir)

        if self.config.diskImage.is_file():
            # only show prompt if we can actually input something to stdin
            print("An image already exists (" + str(self.config.diskImage) + "). ", end="")
            if not self.queryYesNo("Overwrite?", defaultResult=True):
                return  # we are done here
            printCommand("rm", self.config.diskImage)
            self.config.diskImage.unlink()

        with tempfile.TemporaryDirectory() as outDir:
            self.prepareRootfs(Path(outDir))
            # now add all the user provided files to the image:
            # we have to make a copy as we modify self.extraFiles in self.addFileToImage()
            for p in self.extraFiles.copy():
                pathInImage = p.relative_to(self.config.extraFiles)
                print("Adding user provided file /", pathInImage, " to disk image.", sep="")
                self.addFileToImage(p, str(pathInImage.parent))
            # finally create the disk image
            self.makeImage()

    def generateSshHostKeys(self):
        # do the same as "ssh-keygen -A" just with a different output directory as it does not allow customizing that
        sshDir = self.config.extraFiles / "etc/ssh"
        self._makedirs(sshDir)
        # -t type Specifies the type of key to create.  The possible values are "rsa1" for protocol version 1
        #  and "dsa", "ecdsa","ed25519", or "rsa" for protocol version 2.

        for keyType in ("rsa1", "rsa", "dsa", "ecdsa", "ed25519"):
            # SSH1 protocol uses just /etc/ssh/ssh_host_key without the type
            privateKeyName = "ssh_host_key" if keyType == "rsa1" else "ssh_host_" + keyType + "_key"
            privateKey = sshDir / privateKeyName
            publicKey = sshDir / (privateKeyName + ".pub")
            if not privateKey.is_file():
                runCmd("ssh-keygen", "-t", keyType,
                       "-N", "",  # no passphrase
                       "-f", str(privateKey))
            self.addFileToImage(privateKey, "etc/ssh", mode="0600")
            self.addFileToImage(publicKey, "etc/ssh", mode="0644")


class BuildAwk(Project):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, gitUrl="https://github.com/danfuzz/one-true-awk.git")
        self.buildDir = self.sourceDir
        self.commonMakeArgs.extend(["CC=cc", "CFLAGS=-O2 -Wall", "YACC=yacc -y -d"])

    def compile(self):
        self.runMake(self.commonMakeArgs, "a.out", cwd=self.sourceDir / "latest")

    def install(self):
        self.runMake(self.commonMakeArgs, "names", cwd=self.sourceDir / "latest")
        self._makedirs(self.installDir / "bin")
        self.copyFile(self.sourceDir / "latest/a.out", self.installDir / "bin/nawk")
        runCmd("ln", "-sfn", "nawk", "awk", cwd=self.installDir / "bin")

    def process(self):
        if not IS_LINUX:
            statusUpdate("Skipping awk as this is only needed on Linux hosts")
        else:
            super().process()


# Not really autotools but same sequence of commands (other than the script being call bootstrap instead of configure)
class BuildCMake(AutotoolsProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir, configureScript="bootstrap",
                         # gitUrl="https://cmake.org/cmake.git")
                         gitUrl="https://github.com/Kitware/CMake")  # a lot faster than the official repo
        self.gitBranch = "maint"  # track the stable release branch - which is not "release" (see CMake wiki))
        # TODO: do we need to use gmake on FreeBSD?


class BuildCheriOS(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.outputRoot / ("cherios" + config.cheriBitsStr), buildType="Debug",
                         gitUrl="https://github.com/CTSRD-CHERI/cherios.git", appendCheriBitsToBuildDir=True)
        self.configureArgs.append("-DCHERI_SDK_DIR=" + str(self.config.sdkDir))

    # TODO: move to CMakeProject
    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        # try to find cmake 3.4 or newer
        versionPattern = re.compile(b"cmake version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # cmake prints this output to stdout
        versionString = runCmd("cmake", "--version", captureOutput=True, printVerboseOnly=True).stdout
        match = versionPattern.search(versionString)
        versionComponents = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if versionComponents < (3, 5):
            versionStr = ".".join(map(str, versionComponents))
            self.dependencyError("CMake version", versionStr, "is too old (need at least 3.4)",
                                 installInstructions=self.cmakeInstallInstructions)

    def install(self):
        pass  # nothing to install yet


class BuildElfToolchain(Project):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir,
                         gitUrl="https://github.com/emaste/elftoolchain.git")
        self.buildDir = self.sourceDir
        if IS_LINUX:
            self._addRequiredSystemTool("bmake")
            self.makeCommand = "bmake"
        else:
            self.makeCommand = "make"

        self.gitBranch = "master"
        # self.makeArgs = ["WITH_TESTS=no", "-DNO_ROOT"]
        # TODO: build static?
        self.commonMakeArgs.append("WITH_TESTS=no")
        self.commonMakeArgs.append("LDSTATIC=-static")

    def compile(self):
        targets = ["common", "libelf", "libelftc"]
        # tools that we want to build:
        targets += ["brandelf"]
        for tgt in targets:
            self.runMake(self.commonMakeArgs + [self.config.makeJFlag],
                         "all", cwd=self.sourceDir / tgt, logfileName="build." + tgt)

    def install(self):
        # self.runMake([self.makeCommand, self.config.makeJFlag, "DESTDIR=" + str(self.installDir)] + self.makeArgs,
        #              "install", cwd=self.sourceDir)
        # make install requires root, just build binaries statically and copy them
        self.copyFile(self.sourceDir / "brandelf/brandelf", self.installDir / "bin/brandelf", force=True)


class BuildSDK(Project):
    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # if we pass a string starting with a slash to Path() it will reset to that absolute path
        # luckily we have to prepend mips.mips64, so it works out fine
        # expands to e.g. /home/alr48/cheri/output/cheribsd-obj/mips.mips64/home/alr48/cheri/cheribsd
        cheribsdBuildRoot = Path(self.config.cheribsdObj, "mips.mips64" + str(self.config.cheribsdSources))
        self.CHERITOOLS_OBJ = cheribsdBuildRoot / "tmp/usr/bin/"
        self.CHERIBOOTSTRAPTOOLS_OBJ = cheribsdBuildRoot / "tmp/legacy/usr/bin/"
        self.CHERILIBEXEC_OBJ = cheribsdBuildRoot / "tmp/usr/libexec/"

    def fixSymlinks(self):
        # copied from the build_sdk.sh script
        # TODO: we could do this in python as well, but this method works
        fixlinksSrc = R"""
#include <sys/types.h>
#include <sys/stat.h>
#include <dirent.h>
#include <err.h>
#include <errno.h>
#include <stdio.h>
#include <sysexits.h>
#include <unistd.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    DIR *dir = opendir(".");
    struct dirent *file;
    char *dirname;
    int links = 0, fixed = 0;

    while ((file = readdir(dir)) != NULL)
    {
        char target[1024];
        ssize_t index =
            readlink(file->d_name, target, sizeof(target) - 1);

        if (index < 0) {
            // Not a symlink?
            if (errno == EINVAL)
                continue;

            err(EX_OSERR, "error in readlink('%s')", file->d_name);
        }

        links++;

        // Fix absolute paths.
        if (target[0] == '/') {
            target[index] = 0;

            char *newName;
            asprintf(&newName, "../..%s", target);

            if (unlink(file->d_name))
                err(EX_OSERR, "Failed to remove old link");

            if (symlink(newName, file->d_name))
                err(EX_OSERR, "Failed to create link");
            free(newName);
            fixed++;
        }
    }
    closedir(dir);

    if (links == 0)
        errx(EX_USAGE, "no symbolic links in %s", getwd(NULL));

    printf("fixed %d/%d symbolic links\n", fixed, links);
}
"""
        runCmd("cc", "-x", "c", "-", "-o", self.config.sdkDir / "bin/fixlinks", input=fixlinksSrc)
        runCmd(self.config.sdkDir / "bin/fixlinks", cwd=self.config.sdkSysrootDir / "usr/lib")

    def buildCheridis(self):
        # Compile the cheridis helper (TODO: add it to the LLVM repo instead?)
        cheridisSrc = R"""
#include <stdio.h>
#include <string.h>

int main(int argc, char** argv)
{
    int i;
    int byte;

    FILE *dis = popen(LLVM_PATH "llvm-mc -disassemble -triple=cheri-unknown-freebsd", "w");
    for (i=1 ; i<argc ; i++)
    {
        char *inst = argv[i];
        if (strlen(inst) == 10)
        {
            if (inst[0] != '0' || inst[1] != 'x') continue;
            inst += 2;
        }
        else if (strlen(inst) != 8) continue;
        for (byte=0 ; byte<8 ; byte+=2)
        {
            fprintf(dis, "0x%.2s ", &inst[byte]);
        }
    }
    pclose(dis);
}"""
        runCmd("cc", "-DLLVM_PATH=\"%s\"" % str(self.config.sdkDir / "bin"), "-x", "c", "-",
               "-o", self.config.sdkDir / "bin/cheridis", input=cheridisSrc)

    def createSdkNotOnFreeBSD(self):
        if not self.config.freeBsdBuilderOutputPath or not self.config.freeBsdBuildMachine:
            # TODO: improve this information
            fatalError("SDK files must be copied from a FreeBSD server. See --help for more info")
            return
        remoteSysrootPath = os.path.join(self.config.freeBsdBuilderOutputPath, self.config.sdkDirectoryName,
                                         self.config.sysrootArchiveName)
        remoteSysrootPath = self.config.freeBsdBuildMachine + ":" + remoteSysrootPath
        statusUpdate("Will build SDK on", self.config.freeBsdBuildMachine, "and copy the sysroot files from",
                     remoteSysrootPath)
        if not self.queryYesNo("Continue?"):
            return

        if not self.config.freeBsdBuilderCopyOnly:
            # build the SDK on the remote machine:
            remoteRunScript = Path(__file__).parent.resolve() / "py3-run-remote.sh"
            if not remoteRunScript.is_file():
                remoteRunScript = Path(__file__).parent.parent.parent.resolve() / "py3-run-remote.sh"
            if not remoteRunScript.is_file():
                fatalError("Could not find py3-run-remote.sh script. Should be in this directory!")
            runCmd(remoteRunScript, self.config.freeBsdBuildMachine, __file__,
                   "--cheri-bits", self.config.cheriBits,  # make sure we build for the right number of cheri bits
                   "sdk")  # run target SDK with dependencies

        # now copy the files
        self._makedirs(self.config.sdkSysrootDir)
        runCmd("rm", "-f", self.config.sdkDir / self.config.sysrootArchiveName, printVerboseOnly=True)
        runCmd("scp", remoteSysrootPath, self.config.sdkDir)
        runCmd("rm", "-rf", self.config.sdkSysrootDir)
        runCmd("tar", "xzf", self.config.sdkDir / self.config.sysrootArchiveName, cwd=self.config.sdkDir)
        self.installCMakeConfig()

    def installCMakeConfig(self):
        date = datetime.datetime.now()
        microVersion = str(date.year) + str(date.month) + str(date.day)
        versionFile = R"""
set(PACKAGE_VERSION 0.1.@SDK_BUILD_DATE@)

# Check whether the requested PACKAGE_FIND_VERSION is compatible
if("${PACKAGE_VERSION}" VERSION_LESS "${PACKAGE_FIND_VERSION}")
    set(PACKAGE_VERSION_COMPATIBLE FALSE)
else()
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if ("${PACKAGE_VERSION}" VERSION_EQUAL "${PACKAGE_FIND_VERSION}")
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()"""
        versionFile.replace("@SDK_BUILD_DATE@", microVersion)
        configFile = R"""

get_filename_component(_cherisdk_rootdir ${CMAKE_CURRENT_LIST_DIR}/../../../ REALPATH)

set(CheriSDK_TOOLCHAIN_DIR "${_cherisdk_rootdir}/bin")
set(CheriSDK_SYSROOT_DIR "${_cherisdk_rootdir}/sysroot")

set(CheriSDK_CC "${CheriSDK_TOOLCHAIN_DIR}/clang")
set(CheriSDK_CXX "${CheriSDK_TOOLCHAIN_DIR}/clang++")

if(NOT EXISTS ${CheriSDK_CC})
    message(FATAL_ERROR "CHERI clang is missing! Expected it to be at ${CheriSDK_CC}")
endif()
"""
        cmakeConfigDir = self.config.sdkDir / "share/cmake/CheriSDK"
        self._makedirs(cmakeConfigDir)
        self.writeFile(cmakeConfigDir / "CheriSDKConfig.cmake", configFile, overwrite=True)
        self.writeFile(cmakeConfigDir / "CheriSDKConfigVersion.cmake", versionFile, overwrite=True)

    def process(self):
        if not IS_FREEBSD:
            self.createSdkNotOnFreeBSD()
            return

        for i in (self.CHERIBOOTSTRAPTOOLS_OBJ, self.CHERITOOLS_OBJ, self.CHERITOOLS_OBJ, self.config.cheribsdRootfs):
            if not i.is_dir():
                fatalError("Directory", i, "is missing!")
        # make sdk a link to the 256 bit sdk
        if (self.config.outputRoot / "sdk").is_dir():
            # remove the old sdk directory from previous versions of this script
            runCmd("rm", "-rf", self.config.outputRoot / "sdk", printVerboseOnly=True)
        if not self.config.pretend and not (self.config.outputRoot / "sdk").exists():
            runCmd("ln", "-sf", "sdk256", "sdk", cwd=self.config.outputRoot)
        # we need to add include files and libraries to the sysroot directory
        self._cleanDir(self.config.sdkSysrootDir, force=True)  # make sure the sysroot is cleaned
        self._makedirs(self.config.sdkSysrootDir / "usr")
        # use tar+untar to copy all necessary files listed in metalog to the sysroot dir
        archiveCmd = ["tar", "cf", "-", "--include=./lib/", "--include=./usr/include/",
                      "--include=./usr/lib/", "--include=./usr/libcheri", "--include=./usr/libdata/",
                      # only pack those files that are mentioned in METALOG
                      "@METALOG"]
        printCommand(archiveCmd, cwd=self.config.cheribsdRootfs)
        if not self.config.pretend:
            with subprocess.Popen(archiveCmd, stdout=subprocess.PIPE, cwd=str(self.config.cheribsdRootfs)) as tar:
                runCmd(["tar", "xf", "-"], stdin=tar.stdout, cwd=self.config.sdkSysrootDir)
        if not (self.config.sdkSysrootDir / "lib/libc.so.7").is_file():
            fatalError(self.config.sdkSysrootDir, "is missing the libc library, install seems to have failed!")

        # install tools:
        tools = "as objdump strings addr2line crunchide gcc gcov nm strip ld objcopy size brandelf elfcopy".split()
        for tool in tools:
            if (self.CHERITOOLS_OBJ / tool).is_file():
                self.copyFile(self.CHERITOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)
            elif (self.CHERIBOOTSTRAPTOOLS_OBJ / tool).is_file():
                self.copyFile(self.CHERIBOOTSTRAPTOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)
            else:
                fatalError("Required tool", tool, "is missing!")

        # GCC wants the cc1 and cc1plus tools to be in the directory specified by -B.
        # We must make this the same directory that contains ld for linking and
        # compiling to both work...
        for tool in ("cc1", "cc1plus"):
            self.copyFile(self.CHERILIBEXEC_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)

        tools += "clang clang++ llvm-mc llvm-objdump llvm-readobj llvm-size llc".split()
        for tool in tools:
            self.createBuildtoolTargetSymlinks(self.config.sdkDir / "bin" / tool)

        self.buildCheridis()
        # fix symbolic links in the sysroot:
        print("Fixing absolute paths in symbolic links inside lib directory...")
        self.fixSymlinks()
        # create an archive to make it easier to copy the sysroot to another machine
        runCmd("rm", "-f", self.config.sdkDir / self.config.sysrootArchiveName)
        runCmd("tar", "-czf", self.config.sdkDir / self.config.sysrootArchiveName, "sysroot",
               cwd=self.config.sdkDir)
        print("Successfully populated sysroot")
        self.installCMakeConfig()


class LaunchQEMU(Project):
    def __init__(self, config):
        super().__init__(config, projectName="run-qemu")

    def process(self):
        qemuBinary = self.config.sdkDir / "bin/qemu-system-cheri"
        currentKernel = self.config.cheribsdRootfs / "boot/kernel/kernel"

        if not self.isForwardingPortAvailable():
            print("Port usage information:")
            if IS_FREEBSD:
                runCmd("sockstat", "-P", "tcp", "-p", str(self.config.sshForwardingPort))
            elif IS_LINUX:
                runCmd("sh", "-c", "netstat -tulpne | grep \":" + str(str(self.config.sshForwardingPort)) + "\"")
            fatalError("SSH forwarding port", self.config.sshForwardingPort, "is already in use!")

        print("About to run QEMU with image", self.config.diskImage, "and kernel", currentKernel,
              coloured(AnsiColour.green, "\nListening for SSH connections on localhost:" +
                       str(self.config.sshForwardingPort)))
        # input("Press enter to continue")
        runCmd([qemuBinary, "-M", "malta",  # malta cpu
                "-kernel", currentKernel,  # assume the current image matches the kernel currently build
                "-nographic",  # no GPU
                "-m", "2048",  # 2GB memory
                "-hda", self.config.diskImage,
                "-net", "nic", "-net", "user",
                # bind the qemu ssh port to the hosts port 9999
                "-redir", "tcp:" + str(self.config.sshForwardingPort) + "::22",
                ], stdout=sys.stdout)  # even with --quiet we want stdout here

    def isForwardingPortAvailable(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", self.config.sshForwardingPort))
                return True
        except OSError:
            return False


# http://wiki.gnustep.org/index.php/GNUstep_under_Ubuntu_Linux

class BuildLibObjC2(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir,
                         gitUrl="https://github.com/gnustep/libobjc2.git")
        # self.gitBranch = "1.8.1"  # track the stable release branch
        self.configureArgs.extend([
            "-DCMAKE_C_COMPILER=clang",
            "-DCMAKE_CXX_COMPILER=clang++",
            "-DCMAKE_ASM_COMPILER=clang",
            "-DCMAKE_ASM_COMPILER_ID=Clang",  # For some reason CMake doesn't detect the ASM compiler ID for clang
            "-DCMAKE_ASM_FLAGS=-c",  # required according to docs when using clang as ASM compiler
            # "-DLLVM_OPTS=OFF",  # For now don't build the LLVM plugin, it will break when clang is updated
            "-DTESTS=OFF",
            # Don't install in the location that gnustep-config says, it might be a directory that is not writable by
            # the current user:
            "-DGNUSTEP_INSTALL_TYPE=NONE",
        ])
        # TODO: require libdispatch?
        self._addRequiredSystemTool("clang")
        self._addRequiredSystemTool("clang++")


class BuildGnuStep_Make(AutotoolsProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir,
                         gitUrl="https://github.com/gnustep/make.git")
        self.configureArgs.extend([
            "--with-layout=fhs",  # more traditional file system layout
            "--with-library-combo=ng-gnu-gnu",  # use the new libobjc2 that supports ARC
            "--enable-objc-nonfragile-abi",  # not sure if required but given in install guide
            "CC=clang",  # TODO: find the most recent clang
            "CXX=clang++",  # TODO: find the most recent clang++
            "LDFLAGS=-Wl,-rpath," + str(self.installDir / "lib")  # add rpath, otherwise everything breaks
        ])


# FIXME: do we need to source Makefiles/GNUstep.sh before building?
class GnuStepModule(AutotoolsProject):
    def __init__(self, config: CheriConfig, *args, moduleName: str, **kwargs):
        super().__init__(config, installDir=config.otherToolsDir,
                         gitUrl="https://github.com/gnustep/" + moduleName + " .git", *args, **kwargs)
        self.buildDir = self.sourceDir  # out of source builds don't seem to work!

    def configure(self):
        if not shutil.which("gnustep-config"):
            self.dependencyError("gnustep-config should have been installed in the last build step!")
        gnustepLibdir = runCmd("gnustep-config", "--variable=GNUSTEP_SYSTEM_LIBRARIES",
                               captureOutput=True, printVerboseOnly=True).stdout.strip().decode("utf-8")
        # Just to confirm that we have set up the -rpath flag correctly
        expectedLibdir = self.installDir / "lib"
        if not expectedLibdir.is_dir():
            fatalError("Expected gnustep libdir", expectedLibdir, "doesn't exist")
        if not Path(gnustepLibdir).is_dir():
            fatalError("GNUSTEP_SYSTEM_LIBRARIES directory", gnustepLibdir, "doesn't exist")
        if Path(gnustepLibdir).resolve() != expectedLibdir.resolve():
            fatalError("GNUSTEP_SYSTEM_LIBRARIES was", gnustepLibdir, "but expected ", expectedLibdir)

        # print(coloured(AnsiColour.green, "LDFLAGS=-L" + gnustepLibdir))
        # TODO: what about spaces??
        # self.configureArgs.append("LDFLAGS=-L" + gnustepLibdir + " -Wl,-rpath," + gnustepLibdir)
        super().configure()


class BuildGnuStep_Base(GnuStepModule):
    def __init__(self, config: CheriConfig):
        super().__init__(config, moduleName="base")
        self.configureArgs.extend([
            "--disable-mixedabi",
            # TODO: "--enable-libdispatch",
            # "--with-config-file=" + str(self.installDir / "etc/GNUStep/GNUStep.conf")
        ])


class BuildGnuStep_Gui(GnuStepModule):
    def __init__(self, config: CheriConfig):
        super().__init__(config, moduleName="gui")

    def checkSystemDependencies(self):
        # TODO check that libjpeg62-devel is not installed on opensuse, must use libjpeg8-devel
        # rpm -q libjpeg62-devel must not return 0
        super().checkSystemDependencies()


class BuildGnuStep_Back(GnuStepModule):
    def __init__(self, config: CheriConfig):
        super().__init__(config, moduleName="back")
        self.configureArgs.append("--enable-graphics=cairo")


# TODO: add MultiProject or something similar to project.py
class BuildGnuStep(Project):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.otherToolsDir)
        self.subprojects = [
            BuildLibObjC2(config),
            BuildGnuStep_Make(config),
            BuildGnuStep_Base(config),
            BuildGnuStep_Gui(config),
            BuildGnuStep_Back(config),
        ]

    def checkSystemDependencies(self):
        for p in self.subprojects:
            p.checkSystemDependencies()

    def process(self):
        for p in self.subprojects:
            p.process()


class BuildCheriTrace(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/CTSRD-CHERI/cheritrace.git")
        self._addRequiredSystemTool("clang")
        self._addRequiredSystemTool("clang++")
        self.llvmConfigPath = self.config.sdkDir / "bin/llvm-config"
        self.configureArgs.extend([
            "-DLLVM_CONFIG=" + str(self.llvmConfigPath),
            "-DCMAKE_C_COMPILER=clang",
            "-DCMAKE_CXX_COMPILER=clang++",
        ])

    def configure(self):
        if not self.llvmConfigPath.is_file():
            self.dependencyError("Could not find llvm-config from CHERI LLVM.",
                                 installInstructions="Build target 'llvm' first.")
        super().configure()


def gnuStepInstallInstructions():
    if IS_FREEBSD:
        return "Try running `pkg install gnustep-make gnustep-gui` or `cheribuild.py gnustep` to build from source"
    if IS_LINUX:
        return ("Try running `cheribuild.py gnustep`. It might also be possible to use distribution packages but they"
                " will probably be too old.")
        # packaged versions don't seem to work
        #     osRelease = parseOSRelease()
        #     print(osRelease)
        #     if osRelease["ID"] == "ubuntu":
        #         return """Somehow install GNUStep"""
        #     elif osRelease["ID"] == "opensuse":
        #         return """Try installing gnustep-make from the X11:/GNUstep project:
        # sudo zypper addrepo http://download.opensuse.org/repositories/X11:/GNUstep/openSUSE_{OPENSUSE_VERSION}/ gnustep
        # sudo zypper in libobjc2-devel gnustep-make gnustep-gui-devel gnustep-base-devel""".format(OPENSUSE_VERSION=osRelease["VERSION"])


class BuildCheriVis(Project):
    # TODO: allow external cheritrace
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/CTSRD-CHERI/CheriVis.git")
        self._addRequiredSystemTool("clang")
        self._addRequiredSystemTool("clang++")
        if IS_LINUX or IS_FREEBSD:
            self._addRequiredSystemTool("gnustep-config", installInstructions=gnuStepInstallInstructions)
        else:
            fatalError("Build currently only supported on Linux or FreeBSD!")
        self.gnustepMakefilesDir = None  # type: Path
        self.makeCommand = "make" if IS_LINUX else "gmake"
        self.commonMakeArgs = []

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        configOutput = runCmd("gnustep-config", "--variable=GNUSTEP_MAKEFILES", captureOutput=True).stdout
        self.gnustepMakefilesDir = Path(configOutput.decode("utf-8").strip())
        commonDotMake = self.gnustepMakefilesDir / "common.make"
        if not commonDotMake.is_file():
            self.dependencyError("gnustep-config binary exists, but", commonDotMake, "does not exist!",
                                 installInstructions=gnuStepInstallInstructions())
        # has to be a relative path for some reason....
        # pathlib.relative_to() won't work if the prefix is not the same...
        expectedCheritraceLib = str(self.config.sdkDir / "lib/libcheritrace.so")
        cheritraceLib = Path(os.getenv("CHERITRACE_LIB") or expectedCheritraceLib)
        if not cheritraceLib.exists():
            fatalError(cheritraceLib, "does not exist", fixitHint="Try running `cheribuild.py cheritrace` and if that"
                       " doesn't work set the environment variable CHERITRACE_LIB to point to libcheritrace.so")
        cheritraceDirRelative = os.path.relpath(str(cheritraceLib.parent.resolve()), str(self.sourceDir.resolve()))
        # TODO: set ADDITIONAL_LIB_DIRS?
        # http://www.gnustep.org/resources/documentation/Developer/Make/Manual/gnustep-make_1.html#SEC17
        # http://www.gnustep.org/resources/documentation/Developer/Make/Manual/gnustep-make_1.html#SEC29

        # library combos:
        # http://www.gnustep.org/resources/documentation/Developer/Make/Manual/gnustep-make_1.html#SEC35

        self.commonMakeArgs.extend([
            "CXX=clang++", "CC=clang",
            "GNUSTEP_MAKEFILES=" + str(self.gnustepMakefilesDir),
            "CHERITRACE_DIR=" + cheritraceDirRelative,  # make it find the cheritrace library
            "GNUSTEP_INSTALLATION_DOMAIN=USER",
            "GNUSTEP_NG_ARC=1",
            "messages=yes",
        ])

    def clean(self):
        # doesn't seem to be possible to use a out of source build
        self.runMake(self.commonMakeArgs, "clean", cwd=self.sourceDir)

    def compile(self):
        self.runMake(self.commonMakeArgs, "print-gnustep-make-help", cwd=self.sourceDir)
        self.runMake(self.commonMakeArgs, "all", cwd=self.sourceDir)

    def install(self):
        self.runMake(self.commonMakeArgs, "install", cwd=self.sourceDir)

#
# Some of these settings seem required:
"""
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>GSAllowWindowsOverIcons</key>
    <integer>1</integer>
    <key>GSAppOwnsMiniwindow</key>
    <integer>0</integer>
    <key>GSBackHandlesWindowDecorations</key>
    <integer>0</integer>
    <key>GSUseFreedesktopThumbnails</key>
    <integer>1</integer>
    <key>GraphicCompositing</key>
    <integer>1</integer>
    <key>NSInterfaceStyleDefault</key>
    <string>NSWindows95InterfaceStyle</string>
    <key>NSMenuInterfaceStyle</key>
    <string>NSWindows95InterfaceStyle</string>
</dict>
</plist>
"""
#


class Target(object):
    def __init__(self, name, projectClass, *, dependencies: set=set()):
        self.name = name
        self.dependencies = set(dependencies)
        self.projectClass = projectClass
        self.project = None
        self._completed = False

    def checkSystemDeps(self, config: CheriConfig):
        if self._completed:
            return
        self.project = self.projectClass(config)
        with setEnv(PATH=self.project.config.dollarPathWithOtherTools):
            # make sure all system dependencies exist first
            self.project.checkSystemDependencies()

    def execute(self):
        if self._completed:
            # TODO: make this an error once I have a clean solution for the pseudo targets
            # warningMessage(target.name, "has already been executed!")
            return
        # instantiate the project and run it
        starttime = time.time()
        with setEnv(PATH=self.project.config.dollarPathWithOtherTools):
            self.project.process()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._completed = True


# A target that does nothing (used for e.g. the all target)
# TODO: ideally we would do proper dependency resolution and not run targets multiple times
class PseudoTarget(Target):
    def __init__(self, allTargets: "AllTargets", name: str, *, orderedDependencies: "typing.List[str]"=list()):
        super().__init__(name, None, dependencies=set(orderedDependencies))
        self.allTargets = allTargets
        # TODO: somehow resolve dependencies properly but also include them without --include-dependencies
        self.orderedDependencies = orderedDependencies
        if not orderedDependencies:
            fatalError("PseudoTarget with no dependencies should not exist:!!", "Target name =", name)

    def checkSystemDeps(self, config: CheriConfig):
        if self._completed:
            return
        for dep in self.orderedDependencies:
            target = self.allTargets.targetMap[dep]  # type: Target
            if target._completed:
                continue
            target.checkSystemDeps(config)

    def execute(self):
        if self._completed:
            return
        starttime = time.time()
        for dep in self.orderedDependencies:
            target = self.allTargets.targetMap[dep]  # type: Target
            if target._completed:
                # warningMessage("Already processed", target.name, "while processing pseudo target", self.name)
                continue
            target.execute()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._completed = True


class AllTargets(object):
    def __init__(self):
        if IS_FREEBSD:
            sdkTargetDeps = ["llvm", "cheribsd"]
            cheriosTargetDeps = {"sdk"}
        else:
            # CHERIBSD files need to be copied from another host, so we don't build cheribsd
            sdkTargetDeps = ["awk", "elftoolchain", "binutils", "llvm"]
            cheriosTargetDeps = {"elftoolchain", "binutils", "llvm"}
            # These need to be built on Linux but are not required on FreeBSD
        cheriosTarget = Target("cherios", BuildCheriOS, dependencies=cheriosTargetDeps)
        sdkSysrootTarget = Target("sdk-sysroot", BuildSDK, dependencies=set(sdkTargetDeps))
        sdkTarget = PseudoTarget(self, "sdk", orderedDependencies=sdkTargetDeps + ["sdk-sysroot"])
        allTarget = PseudoTarget(self, "all", orderedDependencies=["qemu", "sdk", "disk-image", "run"])

        self._allTargets = [
            Target("binutils", BuildBinutils),
            Target("qemu", BuildQEMU),
            Target("cmake", BuildCMake),
            Target("llvm", BuildLLVM),
            Target("awk", BuildAwk),
            Target("elftoolchain", BuildElfToolchain),
            Target("cheritrace", BuildCheriTrace, dependencies={"llvm"}),
            Target("cherivis", BuildCheriVis, dependencies={"cheritrace"}),
            Target("gnustep", BuildGnuStep),
            Target("cheribsd", BuildCHERIBSD, dependencies={"llvm"}),
            Target("disk-image", BuildDiskImage, dependencies={"cheribsd", "qemu"}),
            sdkSysrootTarget,
            cheriosTarget,
            Target("run", LaunchQEMU, dependencies={"qemu", "disk-image"}),
            allTarget, sdkTarget
        ]
        self.targetMap = dict((t.name, t) for t in self._allTargets)
        # for t in self._allTargets:
        #     print("target:", t.name, ", deps", self.recursiveDependencyNames(t))

    def recursiveDependencyNames(self, target: Target, *, existing: set=None):
        if not existing:
            existing = set()
        for dep in target.dependencies:
            existing.add(dep)
            self.recursiveDependencyNames(self.targetMap[dep], existing=existing)
        return existing

    def topologicalSort(self, targets: "typing.List[Target]") -> "typing.Iterable[typing.List[Target]]":
        # based on http://rosettacode.org/wiki/Topological_sort#Python
        data = dict((t.name, set(t.dependencies)) for t in targets)

        # add all the targets that aren't included yet
        allDependencyNames = [self.recursiveDependencyNames(t) for t in targets]
        possiblyMissingDependencies = functools.reduce(set.union, allDependencyNames, set())
        for dep in possiblyMissingDependencies:
            if dep not in data:
                data[dep] = self.targetMap[dep].dependencies

        # do the actual sorting
        while True:
            ordered = set(item for item, dep in data.items() if not dep)
            if not ordered:
                break
            yield list(sorted(ordered))
            data = {item: (dep - ordered) for item, dep in data.items()
                    if item not in ordered}
        assert not data, "A cyclic dependency exists amongst %r" % data

    def run(self, config: CheriConfig):
        explicitlyChosenTargets = []  # type: typing.List[Target]
        for targetName in config.targets:
            if targetName not in self.targetMap:
                fatalError("Target", targetName, "does not exist. Valid choices are", ",".join(self.targetMap.keys()))
                sys.exit(1)
            explicitlyChosenTargets.append(self.targetMap[targetName])
        if config.skipDependencies:  # FIXME: remove this soon
            warningMessage("--skip-dependencies/-t flag is now the default behaviour and will be removed soon.")
        if not config.includeDependencies:
            # The wants only the explicitly passed targets to be executed, don't do any ordering
            chosenTargets = explicitlyChosenTargets  # TODO: ensure right order?
        else:
            # Otherwise run all targets in dependency order
            chosenTargets = []
            orderedTargets = self.topologicalSort(explicitlyChosenTargets)  # type: typing.Iterable[typing.List[Target]]
            for dependencyLevel, targetNames in enumerate(orderedTargets):
                # print("Level", dependencyLevel, "targets:", targetNames)
                chosenTargets.extend(self.targetMap[t] for t in targetNames)
        # now that the chosen targets have been resolved run them
        for target in chosenTargets:
            target.checkSystemDeps(config)
        # all dependencies exist -> run the targets
        for target in chosenTargets:
            target.execute()


# custom encoder to handle pathlib.Path objects
class MyJsonEncoder(json.JSONEncoder):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def default(self, o):
        if isinstance(o, Path):
            return str(o)
        return super().default(o)

if __name__ == "__main__":
    cheriConfig = CheriConfig()
    setCheriConfig(cheriConfig)
    # create the required directories
    for d in (cheriConfig.sourceRoot, cheriConfig.outputRoot, cheriConfig.extraFiles):
        if d.exists():
            continue
        if not cheriConfig.pretend:
            if cheriConfig.verbose:
                printCommand("mkdir", "-p", str(d))
            os.makedirs(str(d), exist_ok=True)
    try:
        targets = AllTargets()
        if cheriConfig.listTargets:
            print("Available targets are:", ", ".join(targets.targetMap.keys()))
        elif cheriConfig.dumpConfig:
            print(json.dumps(ConfigLoader.values, sort_keys=True, cls=MyJsonEncoder, indent=4))
        else:
            targets.run(cheriConfig)
    except KeyboardInterrupt:
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError as err:
        fatalError("Command ", "`" + " ".join(map(shlex.quote, err.cmd)) + "` failed with non-zero exit code",
                   err.returncode)

