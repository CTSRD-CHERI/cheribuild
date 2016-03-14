#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import shlex
import shutil
import tempfile
import threading
import pprint
import time
import difflib
import io
import re
import json
import socket
from collections import OrderedDict
from functools import reduce
from pathlib import Path
from enum import Enum

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri

if sys.version_info < (3, 4):
    sys.exit("This script requires at least Python 3.4")
if sys.version_info < (3, 5):
    # copy of python 3.5 subprocess.CompletedProcess
    class CompletedProcess(object):
        def __init__(self, args, returncode, stdout=None, stderr=None):
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
    pass

IS_LINUX = sys.platform.startswith("linux")
IS_FREEBSD = sys.platform.startswith("freebsd")


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


def printCommand(arg1: "typing.Union[str, typing.Tuple, typing.List]", *remainingArgs,
                 colour=AnsiColour.yellow, cwd=None, sep=" ", printVerboseOnly=False, **kwargs):
    if cheriConfig.quiet or (printVerboseOnly and not cheriConfig.verbose):
        return
    # also allow passing a single string
    if not type(arg1) is str:
        allArgs = arg1
        arg1 = allArgs[0]
        remainingArgs = allArgs[1:]
    newArgs = ("cd", shlex.quote(str(cwd)), "&&") if cwd else tuple()
    # comma in tuple is required otherwise it creates a tuple of string chars
    newArgs += (shlex.quote(str(arg1)),) + tuple(map(shlex.quote, map(str, remainingArgs)))
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
    if cheriConfig.pretend:
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
    elif cheriConfig.quiet and "stdout" not in kwargs:
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


def fatalError(*args, sep=" "):
    # we ignore fatal errors when simulating a run
    if cheriConfig.pretend:
        print(coloured(AnsiColour.red, ("Potential fatal error:",) + args, sep=sep))
    else:
        sys.exit(coloured(AnsiColour.red, args, sep=sep))


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
        cls._parsedArgs = cls._parser.parse_args()
        try:
            configdir = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
            cls._configPath = Path(configdir, "cheribuild.json")
            if cls._configPath.exists():
                with cls._configPath.open("r") as f:
                    cls._JSON = json.load(f, encoding="utf-8")
            else:
                print("Configuration file", cls._configPath, "does not exist, using only command line arguments.")
        except IOError:
            print("Could not load config file", cls._configPath)
        return cls._parsedArgs.targets

    @classmethod
    def addOption(cls, name: str, shortname=None, default=None, type=None, group=None, **kwargs):
        if default and not hasattr(default, '__call__') and "help" in kwargs:
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
        return cls.addOption(name, shortname, type=Path, **kwargs)

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
            if hasattr(self.default, '__call__'):
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
        return conf.outputRoot / "cheri128-disk.img"
    return conf.outputRoot / "cheri256-disk.img"


class CheriConfig(object):
    # boolean flags
    pretend = ConfigLoader.addBoolOption("pretend", "p", help="Only print the commands instead of running them")
    quiet = ConfigLoader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
    verbose = ConfigLoader.addBoolOption("verbose", "v", help="Print all commmands that are executed")
    clean = ConfigLoader.addBoolOption("clean", "c", help="Remove the build directory before build")
    skipUpdate = ConfigLoader.addBoolOption("skip-update", help="Skip the git pull step")
    skipConfigure = ConfigLoader.addBoolOption("skip-configure", help="Skip the configure step")
    skipBuildworld = ConfigLoader.addBoolOption("skip-buildworld", help="Skip the FreeBSD buildworld step -> only build"
                                                " and install the kernel")
    listTargets = ConfigLoader.addBoolOption("list-targets", help="List all available targets and exit")
    dumpConfig = ConfigLoader.addBoolOption("dump-configuration", help="Print the current configuration as JSON."
                                            " This can be saved to ~/.config/cheribuild.json to make it persistent")
    skipDependencies = ConfigLoader.addBoolOption("skip-dependencies", "t",
                                                  help="Only build the targets that were explicitly passed on the "
                                                       "command line")

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
    # TODO: only create a qcow2 image?
    diskImage = ConfigLoader.addPathOption("disk-image-path", default=defaultDiskImagePath, help="The output path for"
                                           " the QEMU disk image (default: '<OUTPUT_ROOT>/cheri256-disk.img')")
    nfsKernelPath = ConfigLoader.addPathOption("nfs-kernel-path", default=lambda p: (p.outputRoot / "nfs/kernel"),
                                               help="The output path for the CheriBSD kernel that boots over NFS "
                                                    "(default: '<OUTPUT_ROOT>/nfs/kernel')")

    # other options
    makeJobs = ConfigLoader.addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(),
                                      help="Number of jobs to use for compiling")  # type: int
    sshForwardingPort = ConfigLoader.addOption("ssh-forwarding-port", "s", type=int, default=defaultSshForwardingPort(),
                                               help="The port to use on localhost to forward the QEMU ssh port. "
                                                    "You can then use `ssh root@localhost -p $PORT` connect to the VM",
                                               metavar="PORT")  # type: int

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
        self.qcow2DiskImage = Path(str(self.diskImage).replace(".img", ".qcow2"))

        # now the derived config options
        self.cheribsdRootfs = self.outputRoot / ("rootfs" + self.cheriBitsStr)
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / ("cheribsd-obj-" + self.cheriBitsStr)
        self.sdkDirectoryName = "sdk" + self.cheriBitsStr
        self.sdkDir = self.outputRoot / self.sdkDirectoryName  # qemu and binutils (and llvm/clang)
        self.sdkSysrootDir = self.sdkDir / "sysroot"

        # for debugging purposes print all the options
        for i in ConfigLoader.options:
            i.__get__(self, CheriConfig)  # for loading of lazy value
        if not self.quiet:
            print("cheribuild.py configuration:", dict(ConfigLoader.values))


class Project(object):
    clearLineSequence = b"\x1b[2K\r"

    def __init__(self, name: str, config: CheriConfig, *, sourceDir: Path=None, buildDir: Path=None,
                 installDir: Path=None, gitUrl="", gitRevision=None, appendCheriBitsToBuildDir=False):
        self.name = name
        self.gitUrl = gitUrl
        self.gitRevision = gitRevision
        self.config = config
        self.sourceDir = Path(sourceDir if sourceDir else config.sourceRoot / name)
        # make sure we have different build dirs for LLVM/CHERIBSD/QEMU 128 and 256,
        buildDirSuffix = "-" + config.cheriBitsStr + "-build" if appendCheriBitsToBuildDir else "-build"
        self.buildDir = Path(buildDir if buildDir else config.outputRoot / (name + buildDirSuffix))
        self.installDir = installDir
        self.makeCommand = "make"
        self.configureCommand = ""
        self.configureArgs = []  # type: typing.List[str]
        # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line

    def queryYesNo(self, message: str="", *, defaultResult=False) -> bool:
        yesNoStr = " [Y]/n " if defaultResult else " y/[N] "
        if self.config.pretend:
            print(message + yesNoStr)
            return True  # in pretend mode we always return true
        if not sys.__stdin__.isatty():
            return defaultResult  # can't get any input -> return the default
        result = input(message + yesNoStr)
        if defaultResult:
            return not result.startswith("n")  # if default is yes accept anything other than strings starting with "n"
        return str(result).lower().startswith("y")  # anything but y will be treated as false

    def _updateGitRepo(self, srcDir: Path, remoteUrl, revision=None):
        if not (srcDir / ".git").is_dir():
            print(srcDir, "is not a git repository. Clone it from' " + remoteUrl + "'?", end="")
            if not self.queryYesNo(defaultResult=False):
                fatalError("Sources for", str(srcDir), " missing!")
            runCmd("git", "clone", remoteUrl, srcDir)
        # make sure we run git stash if we discover any local changes
        hasChanges = len(runCmd("git", "diff", captureOutput=True, cwd=srcDir, printVerboseOnly=True).stdout) > 1
        if hasChanges:
            runCmd("git", "stash", cwd=srcDir, printVerboseOnly=True)
        runCmd("git", "pull", "--rebase", cwd=srcDir, printVerboseOnly=True)
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

    def update(self):
        self._updateGitRepo(self.sourceDir, self.gitUrl, self.gitRevision)

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
            statusUpdate("Configuring", self.name, "... ")
            self.runWithLogfile([self.configureCommand] + self.configureArgs,
                                logfileName="configure", cwd=self.buildDir)

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        # by default we don't keep any line persistent, just have updating output
        sys.stdout.buffer.write(Project.clearLineSequence)
        sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
        sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
        sys.stdout.buffer.flush()

    @staticmethod
    def _handleStdErr(outfile, stream, fileLock):
        for errLine in stream:
            sys.stderr.buffer.write(errLine)
            sys.stderr.buffer.flush()
            with fileLock:
                outfile.write(errLine)

    def runMake(self, args: "typing.List[str]", makeTarget="", *, cwd: Path=None, env=None) -> None:
        if makeTarget:
            allArgs = args + [makeTarget]
            logfileName = self.makeCommand + "." + makeTarget
        else:
            allArgs = args
            logfileName = "build"
        if not cwd:
            cwd = self.buildDir
        starttime = time.time()
        self.runWithLogfile(allArgs, logfileName=logfileName, stdoutFilter=self._makeStdoutFilter, cwd=cwd, env=env)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", self.makeCommand, makeTarget, "took", time.time() - starttime, "seconds")

    def runWithLogfile(self, args: "typing.Sequence[str]", logfileName: str, *, stdoutFilter=None, cwd: Path = None,
                       env=None) -> None:
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
        assert not logfileName.startswith("/")
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
                subprocess.check_call(args, cwd=str(cwd), stdout=logfile, stderr=logfile, env=env)
                return
            make = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            # use a thread to print stderr output and write it to logfile (not using a thread would block)
            logfileLock = threading.Lock()  # we need a mutex so the logfile line buffer doesn't get messed up
            stderrThread = threading.Thread(target=self._handleStdErr, args=(logfile, make.stderr, logfileLock))
            stderrThread.start()
            for line in make.stdout:
                with logfileLock:  # make sure we don't interleave stdout and stderr lines
                    logfile.write(line)
                    if stdoutFilter:
                        stdoutFilter(line)
                    else:
                        sys.stdout.buffer.write(line)
                        sys.stdout.buffer.flush()
            retcode = make.wait()
            if stdoutFilter:
                # add the final new line after the filtering
                sys.stdout.buffer.write(b"\n")
            stderrThread.join()
            if retcode:
                raise SystemExit("Command \"%s\" failed with exit code %d.\nSee %s for details." %
                                 (cmdStr, retcode, logfile.name))

    def compile(self):
        self.runMake([self.makeCommand, self.config.makeJFlag])

    def install(self):
        self.runMake([self.makeCommand], "install")

    def process(self):
        if not self.config.skipUpdate:
            self.update()
        if self.config.clean:
            self.clean()
        # always make sure the build dir exists
        if not self.buildDir.is_dir():
            self._makedirs(self.buildDir)
        if not self.config.skipConfigure:
            self.configure()
        statusUpdate("Building", self.name, "... ")
        self.compile()
        statusUpdate("Installing", self.name, "... ")
        self.install()


class BuildQEMU(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("qemu", config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/CTSRD-CHERI/qemu.git", gitRevision=config.qemuRevision)
        # QEMU will not work with BSD make, need GNU make
        self.makeCommand = "gmake" if IS_FREEBSD else "make"
        self.configureCommand = self.sourceDir / "configure"
        extraCFlags = "-g -Wno-error=deprecated-declarations"

        if config.cheriBits == 128:
            # enable QEMU 128 bit capabilities
            # https://github.com/CTSRD-CHERI/qemu/commit/bb6b29fcd74dde4518146897c22286fd16ca7eb8
            extraCFlags += " -DCHERI_MAGIC128=1"
        self.configureArgs = ["--target-list=cheri-softmmu",
                              "--disable-linux-user",
                              "--disable-bsd-user",
                              "--disable-xen",
                              "--extra-cflags=" + extraCFlags,
                              "--prefix=" + str(self.installDir)]
        if IS_LINUX:
            # "--enable-libnfs", # version on Ubuntu 14.04 is too old? is it needed?
            self.configureArgs += ["--enable-kvm", "--enable-linux-aio", "--enable-vte", "--enable-sdl",
                                   "--with-sdlabi=2.0", "--enable-virtfs"]
        else:
            self.configureArgs += ["--disable-linux-aio", "--disable-kvm"]

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir():
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir, printVerboseOnly=True)
        super().update()


# FIXME: do we need this? seems like cheribsd has all these utilities
class BuildBinutils(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("binutils", config, installDir=config.sdkDir,
                         gitUrl="https://github.com/CTSRD-CHERI/binutils.git")
        self.configureCommand = self.sourceDir / "configure"
        self.configureArgs = [
            "--target=mips64",  # binutils for MIPS64/CHERI
            "--disable-werror",  # -Werror won't work with recent compilers
            "--prefix=" + str(self.installDir),  # install to the SDK dir
            "MAKEINFO=missing",  # don't build docs, this will fail on recent Linux systems
        ]

    def update(self):
        super().update()
        # make sure *.info is newer than other files, because newer versions of makeinfo will fail
        infoFiles = ["bfd/doc/bfd.info", "ld/ld.info", "gprof/gprof.info", "gas/doc/as.info",
                     "binutils/sysroff.info", "binutils/doc/binutils.info", "etc/configure.info", "etc/standards.info"]
        for i in infoFiles:
            runCmd("touch", self.sourceDir / i)


class BuildLLVM(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("llvm", config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True)
        self.makeCommand = "ninja"
        # try to find clang 3.7, otherwise fall back to system clang
        cCompiler = shutil.which("clang37") or "clang"
        cppCompiler = shutil.which("clang++37") or "clang++"
        # make sure we have at least version 3.7
        versionPattern = re.compile(b"clang version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # clang prints this output to stderr
        versionString = runCmd(cCompiler, "-v", captureError=True, printVerboseOnly=True).stderr
        match = versionPattern.search(versionString)
        versionComponents = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if versionComponents < (3, 7):
            fatalError("Clang version is too old (need at least 3.7): got", str(versionComponents))

        self.configureCommand = "cmake"
        self.configureArgs = [
            self.sourceDir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_CXX_COMPILER=" + cppCompiler, "-DCMAKE_C_COMPILER=" + cCompiler,  # need at least 3.7 to build it
            "-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd",
            "-DCMAKE_INSTALL_PREFIX=" + str(self.installDir),
            "-DDEFAULT_SYSROOT=" + str(self.config.sdkSysrootDir),
            "-DLLVM_TOOL_LLDB_BUILD=OFF",  # disable LLDB for now
            # doesn't save much time and seems to be slightly broken in current clang:
            # "-DCLANG_ENABLE_STATIC_ANALYZER=OFF",  # save some build time by skipping the static analyzer
            # "-DCLANG_ENABLE_ARCMT=OFF",  # need to disable ARCMT to disable static analyzer
        ]
        if self.config.cheriBits == 128:
            self.configureArgs.append("-DLLVM_CHERI_IS_128=ON")

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        Project._makeStdoutFilter(line)

    def update(self):
        self._updateGitRepo(self.sourceDir, "https://github.com/CTSRD-CHERI/llvm.git",
                            revision=self.config.llvmRevision)
        self._updateGitRepo(self.sourceDir / "tools/clang", "https://github.com/CTSRD-CHERI/clang.git",
                            revision=self.config.clangRevision)
        self._updateGitRepo(self.sourceDir / "tools/lldb", "https://github.com/CTSRD-CHERI/lldb.git",
                            revision=self.config.lldbRevision)

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


class BuildCHERIBSD(Project):
    def __init__(self, config: CheriConfig, *, name="cheribsd", kernelConfig="CHERI_MALTA64"):
        super().__init__(name, config, sourceDir=config.sourceRoot / "cheribsd", installDir=config.cheribsdRootfs,
                         buildDir=config.cheribsdObj, gitUrl="https://github.com/CTSRD-CHERI/cheribsd.git",
                         gitRevision=config.cheriBsdRevision, appendCheriBitsToBuildDir=True)
        self.kernelConfig = kernelConfig
        if self.config.cheriBits == 128:
            # make sure we use a kernel with 128 bit CPU features selected
            self.kernelConfig = kernelConfig.replace("CHERI_", "CHERI128_")
        self.binutilsDir = self.config.sdkDir / "mips64/bin"
        self.cheriCC = self.config.sdkDir / "bin/clang"
        self.cheriCXX = self.config.sdkDir / "bin/clang++"
        self.installAsRoot = os.getuid() == 0
        self.commonMakeArgs = [
            "make", "CHERI=" + self.config.cheriBitsStr,
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
        ]

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

    def _removeSchgFlag(self, *paths: "typing.Iterable[str]"):
        for i in paths:
            file = self.installDir / i
            if file.exists():
                runCmd("chflags", "noschg", str(file))

    def setupEnvironment(self):
        os.environ["MAKEOBJDIRPREFIX"] = str(self.buildDir)
        printCommand("export", "MAKEOBJDIRPREFIX=" + str(self.buildDir))
        # make sure the new binutils are picked up
        # TODO: this shouldn't be needed, we build binutils as part of cheribsd
        if not os.environ["PATH"].startswith(str(self.config.sdkDir)):
            os.environ["PATH"] = str(self.config.sdkDir / "bin") + ":" + os.environ["PATH"]
            printCommand("export", "PATH=" + os.environ["PATH"])
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
        super().process()


class BuildNfsKernel(BuildCHERIBSD):
    def __init__(self, config: CheriConfig):
        super().__init__(config, name="cheribsd-nfs", kernelConfig="CHERI_MALTA64_NFSROOT")
        self.installAsRoot = True
        # we don't want a metalog file, we want all files with right permissions
        self.commonMakeArgs.remove("-DNO_ROOT")
        # self.buildDir = self.config.outputRoot / "nfskernel-build"
        self.installDir = self.config.outputRoot / "nfs/rootfs"

    def install(self):
        if not os.getuid() == 0:
            fatalError("Need to be root to build the CHERIBSD NFSROOT")
        super().install()
        # Also install the kernel to a separate directory (which in my case is on the host machine):
        self._makedirs(self.config.nfsKernelPath)
        installArgs = self.commonMakeArgs + ["DESTDIR=" + str(self.config.nfsKernelPath)]
        self.runMake(installArgs, "installkernel", cwd=self.sourceDir)


class BuildNewSDK(BuildCHERIBSD):
    def __init__(self, config: CheriConfig):
        super().__init__(config, name="new-sdk")
        self.installDir = self.config.outputRoot / "xdev-install"
        self.buildDir = self.config.outputRoot / "xdev-build"
        # use make xdev-build/xdev-install to create the cross build environment
        # MK_DEBUG_FILES seems to be unconditionally true, how do I fix this?

        self.commonMakeArgs = [
            "make",
            # "CHERI=256", "CHERI_CC=" + str(self.cheriCC),
            "TARGET=mips", "TARGET_ARCH=mips64", "CPUTYPE=mips64",
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            # "-DNO_ROOT",  # use this even if current user is root, as without it the METALOG file is not created
            # "DEBUG_FLAGS=-g",  # enable debug stuff
            "DESTDIR=" + str(self.installDir),
            "MK_DEBUG_FILES=no",  # HACK: don't create the debug files
            # "XDTP=/usr/mips64"),  # cross tools prefix (default is fine)
            "WITH_LIBCPLUSPLUS=yes",   # compile libc++
            # We already have our own cross compiler
            "MK_CLANG_BOOTSTRAP=no",
            "MK_GCC_BOOTSTRAP=no",
            "XCC=" + str(self.cheriCC),  # TODO: still needed?
            "XCXX=" + str(self.cheriCXX),  # TODO: still needed?
            "CROSS_BINUTILS_PREFIX=" + str(self.binutilsDir),  # use the CHERI-aware binutils and not the builtin ones
            "DCROSS_COMPILER_PREFIX=" + str(self.config.sdkDir / "bin"),
            "MK_BINUTILS_BOOTSTRAP=yes",  # don't build the GNU binutils from the cheribsd source tree
            "MK_ELFTOOLCHAIN_BOOTSTRAP=yes",  # don't build elftoolchain binaries
        ]

    def compile(self):
        self.setupEnvironment()
        self._cleanDir(self.installDir, force=True)  # make sure that the install dir is empty (can cause errors)
        # for now no parallel make
        runCmd(self.commonMakeArgs + [self.config.makeJFlag, "xdev-build"], cwd=self.sourceDir)

    def install(self):
        # don't use multiple jobs here
        runCmd(self.commonMakeArgs + ["xdev"], cwd=self.sourceDir)


class BuildDiskImage(Project):
    def __init__(self, config):
        super().__init__("disk-image", config)
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifestFile = None  # type: Path
        self.userGroupDbDir = self.config.cheribsdSources / "etc"
        self.extraFiles = []  # type: typing.List[Path]

    def writeFile(self, outDir: str, pathInImage: str, contents: str) -> Path:
        if not pathInImage.startswith("/"):
            fatalError("Can't use a relative path for pathInImage:", pathInImage)
        targetFile = Path(outDir + pathInImage)
        self._makedirs(targetFile.parent)
        print("Generating ", pathInImage, " with the following contents:\n",
              coloured(AnsiColour.green, contents), sep="", end="")
        if self.config.pretend:
            return targetFile
        if targetFile.is_file():
            # Should no longer happen with the new logic
            with targetFile.open("r", encoding="utf-8") as f:
                oldContents = f.read()
            if oldContents == contents:
                print("File", targetFile, "already exists with same contents, skipping write operation")
                return targetFile
            print("About to overwrite file ", targetFile, ". Diff is:", sep="")
            diff = difflib.unified_diff(io.StringIO(oldContents).readlines(), io.StringIO(contents).readlines(),
                                        str(targetFile), str(targetFile))
            print("".join(diff))  # difflib.unified_diff() returns an iterator with lines
            if not self.queryYesNo("Continue?", defaultResult=False):
                sys.exit()
        with targetFile.open(mode='w') as f:
            f.write(contents)
        return targetFile

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

    def createFileForImage(self, outDir: str, pathInImage: str, *, contents: str="\n"):
        assert pathInImage.startswith("/")
        userProvided = self.config.extraFiles / pathInImage[1:]
        if userProvided.is_file():
            print("Using user provided", pathInImage, "instead of generating default")
            self.extraFiles.remove(userProvided)
            targetFile = userProvided
        else:
            assert userProvided not in self.extraFiles
            targetFile = self.writeFile(outDir, pathInImage, contents)
        self.addFileToImage(targetFile, str(Path(pathInImage).parent.relative_to("/")))

    def process(self):
        if not (self.config.cheribsdRootfs / "METALOG").is_file():
            fatalError("mtree manifest", self.config.cheribsdRootfs / "METALOG", "is missing")
        if not (self.userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in ", self.userGroupDbDir)

        if self.config.diskImage.is_file():
            # only show prompt if we can actually input something to stdin
            print("An image already exists (" + str(self.config.diskImage) + ").", end="")
            if not self.queryYesNo("Overwrite?", defaultResult=True):
                return  # we are done here
            printCommand("rm", self.config.diskImage)
            self.config.diskImage.unlink()

        with tempfile.TemporaryDirectory() as outDir:
            self.manifestFile = outDir + "/METALOG"
            shutil.copy2(str(self.config.cheribsdRootfs / "METALOG"), self.manifestFile)

            # we need to add /etc/fstab and /etc/rc.conf as well as the SSH host keys to the disk-image
            # If they do not exist in the extra-files directory yet we generate a default one and use that
            # Additionally all other files in the extra-files directory will be added to the disk image
            for root, dirnames, filenames in os.walk(str(self.config.extraFiles)):
                for filename in filenames:
                    self.extraFiles.append(Path(root, filename))

            # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
            # Overlay extra-files over additional stuff over cheribsd rootfs dir

            # create the disk image
            self.createFileForImage(outDir, "/etc/fstab", contents="/dev/ada0 / ufs rw 1 1\n")
            # enable ssh and set hostname
            # TODO: use separate file in /etc/rc.conf.d/ ?
            networkConfigOptions = (
                'hostname="qemu-cheri-' + os.getlogin() + '"\n'
                'ifconfig_le0="DHCP"\n'
                'sshd_enable="YES"\n'
            )
            self.createFileForImage(outDir, "/etc/rc.conf", contents=networkConfigOptions)

            # make sure that the disk image always has the same SSH host keys
            # If they don't exist the system will generate one on first boot and we have to accept them every time
            self.generateSshHostKeys()
            print("Adding 'PermitRootLogin without-password' to sshd_config")
            # make sure we can login as root with pubkey auth:
            sshdConfig = self.config.cheribsdRootfs / "etc/ssh/sshd_config"
            assert sshdConfig.is_file()
            with sshdConfig.open("r") as file:
                newSshdConfigContents = file.read()
            newSshdConfigContents += "\n# Allow root login with pubkey auth:\nPermitRootLogin without-password\n"
            self.createFileForImage(outDir, "/etc/ssh/sshd_config", contents=newSshdConfigContents)
            # now try adding the right ~/.authorized
            authorizedKeys = self.config.extraFiles / "root/.ssh/authorized_keys"
            if not authorizedKeys.is_file():
                sshKeys = list(Path(os.path.expanduser("~/.ssh/")).glob("id_*.pub"))
                if len(sshKeys) > 0:
                    print("Found the following ssh keys:", sshKeys)
                    if self.queryYesNo("Should they be added to /root/.ssh/authorized_keys?", defaultResult=True):
                        contents = ""
                        for p in sshKeys:
                            with p.open("r") as pubkey:
                                contents += pubkey.read()
                        self.createFileForImage(outDir, "/root/.ssh/authorized_keys", contents=contents)

            # TODO: add the users SSH key to authorized_keys

            # now add all the user provided files to the image:
            for p in self.extraFiles:
                pathInImage = p.relative_to(self.config.extraFiles)
                print("Adding user provided file /", pathInImage, " to disk image.", sep="")
                self.addFileToImage(p, str(pathInImage.parent))
            runCmd([
                "makefs",
                "-b", "70%",  # minimum 70% free blocks
                "-f", "30%",  # minimum 30% free inodes
                "-M", "4g",  # minimum image size = 4GB
                "-B", "be",  # big endian byte order
                "-F", self.manifestFile,  # use METALOG as the manifest for the disk image
                "-N", self.userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file
                # which makes sure that the numeric UID values are correct
                self.config.diskImage,  # output file
                self.config.cheribsdRootfs  # directory tree to use for the image
            ])
        # Converting QEMU images: https://en.wikibooks.org/wiki/QEMU/Images
        if not self.config.quiet:
            runCmd("qemu-img", "info", self.config.diskImage)
        runCmd("rm", "-f", self.config.qcow2DiskImage, printVerboseOnly=True)
        # create a qcow2 version:
        runCmd("qemu-img", "convert",
               "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
               "-O", "qcow2",  # convert to qcow2 format
               self.config.diskImage,  # input file
               self.config.qcow2DiskImage)  # output file
        if not self.config.quiet:
            runCmd("qemu-img", "info", self.config.qcow2DiskImage)

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


class BuildSDK(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("sdk", config)
        # if we pass a string starting with a slash to Path() it will reset to that absolute path
        # luckily we have to prepend mips.mips64, so it works out fine
        # expands to e.g. /home/alr48/cheri/output/cheribsd-obj/mips.mips64/home/alr48/cheri/cheribsd
        cheribsdBuildRoot = Path(self.config.cheribsdObj, "mips.mips64" + str(self.config.cheribsdSources))
        self.CHERITOOLS_OBJ = cheribsdBuildRoot / "tmp/usr/bin/"
        self.CHERIBOOTSTRAPTOOLS_OBJ = cheribsdBuildRoot / "tmp/legacy/usr/bin/"
        self.CHERILIBEXEC_OBJ = cheribsdBuildRoot / "tmp/usr/libexec/"

    def fixSymlinks(self):
        pass

    def buildCheridis(self):
        pass

    def createSdkNotOnFreeBSD(self):
        if not self.config.freeBsdBuilderOutputPath or not self.config.freeBsdBuildMachine:
            # TODO: improve this information
            fatalError("SDK files must be copied those files from a FreeBSD server. See --help for more info")
            return
        remoteSysrootPath = os.path.join(self.config.freeBsdBuilderOutputPath, self.config.sdkDirectoryName, "sysroot")
        remoteSysrootPath = self.config.freeBsdBuildMachine + ":" + remoteSysrootPath
        statusUpdate("Will build SDK on", self.config.freeBsdBuildMachine, "and copy the sysroot files from",
                     remoteSysrootPath)
        if not self.queryYesNo("Continue?"):
            return

        if not self.config.freeBsdBuilderCopyOnly:
            # build the SDK on the remote machine:
            remoteRunScript = Path(__file__).parent.resolve() / "py3-run-remote.sh"
            if not remoteRunScript.is_file():
                fatalError("Could not find py3-run-remote.sh script. Should be in this directory!")
            runCmd(remoteRunScript, self.config.freeBsdBuildMachine, __file__, "sdk")

        # now copy the files
        self._makedirs(self.config.sdkSysrootDir)
        runCmd("scp", "-vr",  remoteSysrootPath, self.config.sdkSysrootDir)

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
            tar = subprocess.Popen(archiveCmd, stdout=subprocess.PIPE, cwd=str(self.config.cheribsdRootfs))
            runCmd(["tar", "xf", "-"], stdin=tar.stdout, cwd=self.config.sdkSysrootDir)
        if not (self.config.sdkSysrootDir / "lib/libc.so.7").is_file():
            fatalError(self.config.sdkSysrootDir, "is missing the libc library, install seems to have failed!")

        # install tools:
        tools = "as objdump strings addr2line crunchide gcc gcov nm strip ld objcopy size brandelf".split()
        for tool in tools:
            if (self.CHERITOOLS_OBJ / tool).is_file():
                runCmd("cp", "-f", self.CHERITOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, printVerboseOnly=True)
            elif (self.CHERIBOOTSTRAPTOOLS_OBJ / tool).is_file():
                runCmd("cp", "-f", self.CHERIBOOTSTRAPTOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool,
                       printVerboseOnly=True)
            else:
                fatalError("Required tool", tool, "is missing!")

        # GCC wants the cc1 and cc1plus tools to be in the directory specified by -B.
        # We must make this the same directory that contains ld for linking and
        # compiling to both work...
        for tool in ("cc1", "cc1plus"):
            runCmd("cp", "-f", self.CHERILIBEXEC_OBJ / tool, self.config.sdkDir / "bin" / tool, printVerboseOnly=True)

        tools += "clang clang++ llvm-mc llvm-objdump llvm-readobj llvm-size llc".split()
        for tool in tools:
            runCmd("ln", "-fs", tool, "cheri-unknown-freebsd-" + tool, cwd=self.config.sdkDir / "bin",
                   printVerboseOnly=True)
            runCmd("ln", "-fs", tool, "mips4-unknown-freebsd-" + tool, cwd=self.config.sdkDir / "bin",
                   printVerboseOnly=True)
            runCmd("ln", "-fs", tool, "mips64-unknown-freebsd-" + tool, cwd=self.config.sdkDir / "bin",
                   printVerboseOnly=True)

        # Compile the cheridis helper (TODO: add it to the LLVM repo instead?)
        cheridisSrc = """
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

        # fix symbolic links in the sysroot:
        print("Fixing absolute paths in symbolic links inside lib directory...")
        fixlinksSrc = """
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

    printf("fixed %d/%d symbolic links\\n", fixed, links);
}
"""
        runCmd("cc", "-x", "c", "-", "-o", self.config.sdkDir / "bin/fixlinks", input=fixlinksSrc)
        runCmd(self.config.sdkDir / "bin/fixlinks", cwd=self.config.sdkSysrootDir / "usr/lib")
        print("Successfully populated sysroot")


class LaunchQEMU(Project):
    def __init__(self, config):
        super().__init__("run", config)

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
                "-hda", self.config.qcow2DiskImage,
                "-net", "nic", "-net", "user",
                # bind the qemu ssh port to the hosts port 9999
                "-redir", "tcp:" + str(self.config.sshForwardingPort) + "::22",
                ], stdout=sys.stdout)  # even with --quiet we want stdout here

    def isForwardingPortAvailable(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", self.config.sshForwardingPort))
            s.close()
            return True
        except OSError:
            return False


# ufstype=ufs2 is required as the Linux kernel can't automatically determine which UFS filesystem is being used
# Mount the filesystem of a BSD VM: guestmount -a /foo/bar.qcow2 -m /dev/sda1:/:ufstype=ufs2:ufs --ro /mnt/foo
# Same thing is possible with qemu-nbd, but needs root (might be faster)

# A target that does nothing (used for e.g. the all target)
class PseudoTarget(Project):
    def __init__(self, config):
        super().__init__("pseudo", config)

    def process(self):
        pass


class Target(object):
    def __init__(self, name, projectClass, *, dependencies: "typing.Iterable[str]"=set()):
        self.name = name
        self.dependencies = set(dependencies)
        self.projectClass = projectClass

    def execute(self, config: CheriConfig):
        # instantiate the project and run it
        project = self.projectClass(config)
        project.process()
        statusUpdate("Built target '" + self.name + "'")


class AllTargets(object):
    def __init__(self):
        self._allTargets = [
            Target("binutils", BuildBinutils),
            Target("qemu", BuildQEMU),
            Target("llvm", BuildLLVM),
            Target("cheribsd", BuildCHERIBSD, dependencies=["llvm"]),
            Target("cheribsd-nfs", BuildNfsKernel, dependencies=["llvm"]),
            # SDK only needs to build CHERIBSD if we are on a FreeBSD host, otherwise the files will be copied
            Target("sdk", BuildSDK, dependencies=["cheribsd", "llvm"]),
            Target("new-sdk", BuildNewSDK, dependencies=["binutils", "llvm"]),
            Target("disk-image", BuildDiskImage, dependencies=["cheribsd"]),
            Target("run", LaunchQEMU, dependencies=["qemu", "disk-image"]),
            Target("all", PseudoTarget, dependencies=["qemu", "llvm", "cheribsd", "sdk", "disk-image", "run"]),
        ]
        self.targetMap = dict((t.name, t) for t in self._allTargets)
        # for t in self._allTargets:
        #     print("target:", t.name, ", deps", self.recursiveDependencyNames(t))

    def recursiveDependencyNames(self, target: Target, existing: set=None):
        if not existing:
            existing = set()
        for dep in target.dependencies:
            existing.add(dep)
            self.recursiveDependencyNames(self.targetMap[dep], existing)
        return existing

    def topologicalSort(self, targets: "typing.List[Target]") -> "typing.Iterable[typing.List[Target]]":
        # based on http://rosettacode.org/wiki/Topological_sort#Python
        data = dict((t.name, set(t.dependencies)) for t in targets)
        # add all the targets that aren't included yet
        possiblyMissingDependencies = reduce(set.union, [self.recursiveDependencyNames(t) for t in targets], set())
        for dep in possiblyMissingDependencies:
            if dep not in data:
                data[dep] = self.targetMap[dep].dependencies

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
        if config.skipDependencies:
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
            target.execute(config)


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
    # create the required directories
    for d in (cheriConfig.sourceRoot, cheriConfig.outputRoot, cheriConfig.extraFiles):
        if not cheriConfig.pretend:
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
