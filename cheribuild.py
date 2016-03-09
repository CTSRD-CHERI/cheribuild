#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import shlex
import shutil
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
                 colour=AnsiColour.yellow, cwd=None, sep=" ", **kwargs):
    # also allow passing a single string
    if not type(arg1) is str:
        allArgs = arg1
        arg1 = allArgs[0]
        remainingArgs = allArgs[1:]
    newArgs = ("cd", shlex.quote(str(cwd)), "&&") if cwd else tuple()
    # comma in tuple is required otherwise it creates a tuple of string chars
    newArgs += (shlex.quote(str(arg1)),) + tuple(map(shlex.quote, map(str, remainingArgs)))
    print(coloured(colour, newArgs, sep=sep), flush=True, **kwargs)


def runCmd(*args, captureOutput=False, input: "typing.Union[str, bytes]"=None, timeout=None, **kwargs):
    if len(args) == 1 and isinstance(args[0], (list, tuple)):
        cmdline = args[0]  # list with parameters was passed
    else:
        cmdline = args
    cmdline = list(map(str, cmdline))  # make sure they are all strings
    printCommand(cmdline, cwd=kwargs.get("cwd"))
    kwargs["cwd"] = str(kwargs["cwd"]) if "cwd" in kwargs else os.getcwd()
    if cheriConfig.pretend:
        return CompletedProcess(args=cmdline, returncode=0, stdout=b"")

    # actually run the process now:
    if input is not None:
        assert "stdin" not in kwargs  # we need to use stdin here
        kwargs['stdin'] = subprocess.PIPE
        if not isinstance(input, bytes):
            input = str(input).encode("utf-8")
    if captureOutput:
        assert "stdout" not in kwargs  # we need to use stdout here
        kwargs["stdout"] = subprocess.PIPE
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


def fatalError(*args):
    # we ignore fatal errors when simulating a run
    if cheriConfig.pretend:
        print(coloured(AnsiColour.red, ("Potential fatal error:",) + args))
    else:
        sys.exit(coloured(AnsiColour.red, args))


class ConfigLoader(object):
    _parser = argparse.ArgumentParser(formatter_class=
                                      lambda prog: argparse.HelpFormatter(prog, width=shutil.get_terminal_size()[0]))
    options = []
    _parsedArgs = None
    _JSON = {}  # type: dict
    values = OrderedDict()

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
            with cls._configPath.open("r") as f:
                cls._JSON = json.load(f, encoding="utf-8")
        except IOError:
            print("Could not load config file", cls._configPath)
        return cls._parsedArgs.targets

    @classmethod
    def addOption(cls, name: str, shortname=None, default=None, type=None, **kwargs):
        if default and not hasattr(default, '__call__') and "help" in kwargs:
            # only add the default string if it is not lambda
            kwargs["help"] = kwargs["help"] + " (default: \'" + str(default) + "\')"
        if shortname:
            action = cls._parser.add_argument("--" + name, "-" + shortname, **kwargs)
        else:
            action = cls._parser.add_argument("--" + name, **kwargs)
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
    if conf.buildCheri128:
        return conf.outputRoot / "cheri128-disk.img"
    return conf.outputRoot / "cheri256-disk.img"


class CheriConfig(object):
    # boolean flags
    pretend = ConfigLoader.addBoolOption("pretend", "p", help="Only print the commands instead of running them")
    quiet = ConfigLoader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
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
    buildCheri128 = ConfigLoader.addBoolOption("cheri-128", "-128", help="Build for 128 bit CHERI instead of 256")

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
                                                    "You can then use `ssh root@localhost -p $PORT` connect to the VM")  # type: int

    # allow overriding the git revisions in case there is a regression
    cheriBsdRevision = ConfigLoader.addOption("cheribsd-revision", type=str,
                                              help="The git revision or branch of CHERIBSD to check out")  # type: str
    llvmRevision = ConfigLoader.addOption("llvm-revision", type=str,
                                          help="The git revision or branch of LLVM to check out")  # type: str
    clangRevision = ConfigLoader.addOption("clang-revision", type=str,
                                           help="The git revision or branch of clang to check out")  # type: str
    lldbRevision = ConfigLoader.addOption("lldb-revision", type=str,
                                          help="The git revision or branch of clang to check out")  # type: str
    qemuRevision = ConfigLoader.addOption("qemu-revision", type=str,
                                          help="The git revision or branch of QEMU to check out")  # type: str

    def __init__(self):
        self.targets = ConfigLoader.loadTargets()
        self.makeJFlag = "-j" + str(self.makeJobs)

        print("Sources will be stored in", self.sourceRoot)
        print("Build artifacts will be stored in", self.outputRoot)
        print("Extra files for disk image will be searched for in", self.extraFiles)
        print("Disk image will saved to", self.diskImage)
        self.qcow2DiskImage = Path(str(self.diskImage).replace(".img", ".qcow2"))

        self.cheriBits = 128 if self.buildCheri128 else 256
        self.cheriBitsStr = str(self.cheriBits)
        # now the derived config options
        self.cheribsdRootfs = self.outputRoot / ("rootfs" + self.cheriBitsStr)
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / ("cheribsd-obj-" + self.cheriBitsStr)
        self.sdkDir = self.outputRoot / ("sdk" + self.cheriBitsStr)  # qemu and binutils (and llvm/clang)
        self.sdkSysrootDir = self.sdkDir / "sysroot"

        for d in (self.sourceRoot, self.outputRoot, self.extraFiles):
            if not self.pretend:
                printCommand("mkdir", "-p", str(d))
                os.makedirs(str(d), exist_ok=True)

        # for debugging purposes print all the options
        for i in ConfigLoader.options:
            i.__get__(self, CheriConfig)  # for loading of lazy value
        pprint.pprint(ConfigLoader.values)


class Project(object):
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
        self.clearLineSequence = b"\x1b[2K\r"

    @staticmethod
    def _updateGitRepo(srcDir: Path, remoteUrl, revision=None):
        if not (srcDir / ".git").is_dir():
            print(srcDir, "is not a git repository. Clone it from' " + remoteUrl + "'?")
            if sys.__stdin__.isatty() and input("y/[N]").lower() != "y":
                sys.exit("Sources for " + str(srcDir) + " missing!")
            runCmd("git", "clone", remoteUrl, srcDir)
        # make sure we run git stash if we discover any local changes
        hasChanges = len(runCmd("git", "diff", captureOutput=True, cwd=srcDir).stdout) > 1
        if hasChanges:
            runCmd("git", "stash", cwd=srcDir)
        runCmd("git", "pull", "--rebase", cwd=srcDir)
        if hasChanges:
            runCmd("git", "stash", "pop", cwd=srcDir)
        if revision:
            runCmd("git", "checkout", revision, cwd=srcDir)

    def _makedirs(self, path: Path):
        printCommand("mkdir", "-p", path)
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
            runCmd([self.configureCommand] + self.configureArgs, cwd=self.buildDir)

    def _makeStdoutFilter(self, line: bytes):
        # by default we don't keep any line persistent, just have updating output
        sys.stdout.buffer.write(self.clearLineSequence)
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
        """
        Runs make and logs the output
        :param args: the make command to run (e.g. ["make", "-j32"])
        :param makeTarget: the target to build (e.g. "install")
        :param cwd the directory to run make in (defaults to self.buildDir)
        """
        if makeTarget:
            allArgs = args + [makeTarget]
            logfilePath = Path(self.buildDir / ("build." + makeTarget + ".log"))
        else:
            allArgs = args
            logfilePath = Path(self.buildDir / "build.log")

        if not cwd:
            cwd = self.buildDir

        printCommand(" ".join(allArgs), cwd=self.sourceDir)
        if self.config.pretend:
            return
        print("Saving build log to", logfilePath)

        with logfilePath.open("wb") as logfile:
            # TODO: add a verbose option that shows every line
            starttime = time.time()
            # quiet doesn't display anything, normal only status updates and verbose everything
            if self.config.quiet:
                # a lot more efficient than filtering every line
                subprocess.check_call(allArgs, cwd=str(cwd), stdout=logfile, env=env)
                return
            make = subprocess.Popen(allArgs, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            # use a thread to print stderr output and write it to logfile (not using a thread would block)
            logfileLock = threading.Lock()  # we need a mutex so the logfile line buffer doesn't get messed up
            stderrThread = threading.Thread(target=self._handleStdErr, args=(logfile, make.stderr, logfileLock))
            stderrThread.start()
            for line in make.stdout:
                with logfileLock:
                    logfile.write(line)
                    self._makeStdoutFilter(line)
            retcode = make.wait()
            stderrThread.join()
            cmdStr = " ".join([shlex.quote(s) for s in allArgs])
            if retcode:
                raise SystemExit("Command \"%s\" failed with exit code %d.\nSee %s for details." %
                                 (cmdStr, retcode, logfile.name))
            else:
                # add a newline at the end in case it ended with a filtered line (no final newline)
                print("\nBuilding", makeTarget, "took", time.time() - starttime, "seconds")

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
        self.compile()
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
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir)
        super().update()


# FIXME: do we need this? seems like cheribsd has all these utilities
class BuildBinutils(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("binutils", config, installDir=config.sdkDir,
                         gitUrl="https://github.com/CTSRD-CHERI/binutils.git")
        self.configureCommand = self.sourceDir / "configure"
        self.configureArgs = ["--target=mips64", "--disable-werror", "--prefix=" + str(self.installDir)]


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
        versionString = runCmd(cCompiler, "-v", captureOutput=True, stderr=subprocess.STDOUT).stdout
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

    def _makeStdoutFilter(self, line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        super()._makeStdoutFilter(line)

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
        for i in incompatibleFiles:
            printCommand("rm", shlex.quote(str(i)))
            if not self.config.pretend:
                i.unlink()


class BuildCHERIBSD(Project):
    def __init__(self, config: CheriConfig, *, name="cheribsd", kernelConfig="CHERI_MALTA64"):
        super().__init__(name, config, sourceDir=config.sourceRoot / "cheribsd", installDir=config.cheribsdRootfs,
                         buildDir=config.cheribsdObj, gitUrl="https://github.com/CTSRD-CHERI/cheribsd.git",
                         gitRevision=config.cheriBsdRevision, appendCheriBitsToBuildDir=True)
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

    def _makeStdoutFilter(self, line: bytes):
        if line.startswith(b">>> "):  # major status update
            sys.stdout.buffer.write(self.clearLineSequence)
            sys.stdout.buffer.write(line)
        elif line.startswith(b"===> "):  # new subdirectory
            # clear the old line to have a continuously updating progress
            sys.stdout.buffer.write(self.clearLineSequence)
            sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
            sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
            sys.stdout.buffer.flush()

    def _removeSchgFlag(self, *paths: "typing.Iterable[str]"):
        for i in paths:
            file = self.installDir / i
            if file.exists():
                runCmd("chflags", "noschg", str(file))

    def setupEnvironment(self):
        if not IS_FREEBSD:
            fatalError("Can't build CHERIBSD on a non-FreeBSD host!")
        os.environ["MAKEOBJDIRPREFIX"] = str(self.buildDir)
        printCommand("export MAKEOBJDIRPREFIX=" + str(self.buildDir))
        # make sure the new binutils are picked up
        if not os.environ["PATH"].startswith(str(self.config.sdkDir)):
            os.environ["PATH"] = str(self.config.sdkDir / "bin") + ":" + os.environ["PATH"]
            print("Set PATH to", os.environ["PATH"])
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
        # TODO: should we run installworld even when --skip-buildworld is passed?
        if not self.config.skipBuildworld:
            self.runMake(installArgs, "installworld", cwd=self.sourceDir)
            self.runMake(installArgs, "distribution", cwd=self.sourceDir)


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
        self.manifestFile = self.config.cheribsdRootfs / "METALOG"
        self.userGroupDbDir = self.config.cheribsdSources / "etc"

    def writeFile(self, path: Path, contents: str):
        printCommand("echo", shlex.quote(contents.replace("\n", "\\n")), ">", shlex.quote(str(path)))
        if self.config.pretend:
            return
        newContents = contents + "\n"  # make sure the file has a newline at the end
        if path.is_file():
            with path.open("r", encoding="utf-8") as f:
                oldContents = f.read()
            if oldContents == newContents:
                print("File", path, "already exists with same contents, skipping write operation")
                return
            print("About to overwrite file ", path, ". Diff is:", sep="")
            diff = difflib.unified_diff(io.StringIO(oldContents).readlines(), io.StringIO(newContents).readlines(),
                                        str(path), str(path))
            print("".join(diff))  # difflib.unified_diff() returns an iterator with lines
            if input("Continue? [Y/n]").lower() == "n":
                sys.exit()
        with path.open(mode='w') as f:
            f.write(contents + "\n")

    def addFileToImage(self, file: Path, targetDir: str, user="root", group="wheel", mode="0644"):
        # e.g. "install -N /home/alr48/cheri/cheribsd/etc -U -M /home/alr48/cheri/output/rootfs//METALOG
        # -D /home/alr48/cheri/output/rootfs -o root -g wheel -m 444 alarm.3.gz
        # /home/alr48/cheri/output/rootfs/usr/share/man/man3/"
        runCmd(["install",
                "-N", str(self.userGroupDbDir),  # Use a custom user/group database text file
                "-U",  # Indicate that install is running unprivileged (do not change uid/gid)
                "-M", str(self.manifestFile),  # the mtree manifest to write the entry to
                "-D", str(self.config.cheribsdRootfs),  # DESTDIR (will be stripped from the start of the mtree file
                "-o", user, "-g", group,  # uid and gid
                "-m", mode,  # access rights
                str(file), str(self.config.cheribsdRootfs / targetDir)  # target file and destination dir
                ])

    def process(self):
        if not self.manifestFile.is_file():
            fatalError("mtree manifest", self.manifestFile, "is missing")
        if not (self.userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in ", self.userGroupDbDir)

        if self.config.diskImage.is_file():
            # only show prompt if we can actually input something to stdin
            if sys.__stdin__.isatty() and not self.config.pretend:
                yn = input("An image already exists (" + str(self.config.diskImage) + "). Overwrite? [Y/n] ")
                if str(yn).lower() == "n":
                    return
            printCommand("rm", self.config.diskImage)
            self.config.diskImage.unlink()

        # TODO: make this configurable to allow NFS, etc.
        self.writeFile(self.config.cheribsdRootfs / "etc/fstab", "/dev/ada0 / ufs rw 1 1")
        self.addFileToImage(self.config.cheribsdRootfs / "etc/fstab", targetDir="etc")

        # enable ssh and set hostname
        # TODO: use separate file in /etc/rc.conf.d/ ?
        networkConfigOptions = (
            'hostname="qemu-cheri-' + os.getlogin() + '"\n'
            'ifconfig_le0="DHCP"\n'
            'sshd_enable="YES"')
        self.writeFile(self.config.cheribsdRootfs / "etc/rc.conf", networkConfigOptions)
        self.addFileToImage(self.config.cheribsdRootfs / "etc/rc.conf", targetDir="etc")
        # make sure that the disk image always has the same SSH host keys
        # If they don't exist the system will generate one on first boot (which means we keep having to add new ones)
        self.generateSshHostKeys()

        # TODO: https://www.freebsd.org/cgi/man.cgi?mount_unionfs(8) should make this easier
        # Overlay extra-files over additional stuff over cheribsd rootfs dir

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
        runCmd("qemu-img", "info", self.config.qcow2DiskImage)
        runCmd("rm", "-f", self.config.qcow2DiskImage)
        # create a qcow2 version:
        runCmd("qemu-img", "convert",
               "-f", "raw",  # input file is in raw format (not required as QEMU can detect it
               "-O", "qcow2",  # convert to qcow2 format
               self.config.diskImage,  # input file
               self.config.qcow2DiskImage)  # output file
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

    def process(self):
        for i in (self.CHERIBOOTSTRAPTOOLS_OBJ, self.CHERITOOLS_OBJ, self.CHERITOOLS_OBJ, self.config.cheribsdRootfs):
            if not i.is_dir():
                fatalError("Directory", i, "is missing!")
        # make sdk a link to the 256 bit sdk
        if (self.config.outputRoot / "sdk").is_dir():
            # remove the old sdk directory from previous versions of this script
            runCmd("rm", "-rf", self.config.outputRoot / "sdk")
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
                runCmd("cp", "-f", self.CHERITOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool)
            elif (self.CHERIBOOTSTRAPTOOLS_OBJ / tool).is_file():
                runCmd("cp", "-f", self.CHERIBOOTSTRAPTOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool)
            else:
                fatalError("Required tool", tool, "is missing!")

        # GCC wants the cc1 and cc1plus tools to be in the directory specified by -B.
        # We must make this the same directory that contains ld for linking and
        # compiling to both work...
        for tool in ("cc1", "cc1plus"):
            runCmd("cp", "-f", self.CHERILIBEXEC_OBJ / tool, self.config.sdkDir / "bin" / tool)

        tools += "clang clang++ llvm-mc llvm-objdump llvm-readobj llvm-size llc".split()
        for tool in tools:
            runCmd("ln", "-fs", tool, "cheri-unknown-freebsd-" + tool, cwd=self.config.sdkDir / "bin")
            runCmd("ln", "-fs", tool, "mips4-unknown-freebsd-" + tool, cwd=self.config.sdkDir / "bin")
            runCmd("ln", "-fs", tool, "mips64-unknown-freebsd-" + tool, cwd=self.config.sdkDir / "bin")

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
              coloured(AnsiColour.green, "\nListinging for SSH connections on localhost:" +
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
        print("Built target '" +  self.name + "'")


class AllTargets(object):
    def __init__(self):
        self._allTargets = [
            Target("binutils", BuildBinutils),
            Target("qemu", BuildQEMU),
            Target("llvm", BuildLLVM),
            Target("cheribsd", BuildCHERIBSD, dependencies=["llvm"]),
            Target("cheribsd-nfs", BuildNfsKernel, dependencies=["llvm"]),
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
            for dependecyLevel, targetNames in enumerate(orderedTargets):
                # print("Level", dependecyLevel, "targets:", targetNames)
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
