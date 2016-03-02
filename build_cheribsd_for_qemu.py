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
from collections import OrderedDict
from pathlib import Path

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri

# change this if you want to customize where the sources go (or use --source-root=...)
DEFAULT_SOURCE_ROOT = Path(os.path.expanduser("~/cheri"))

if sys.version_info < (3, 4):
    sys.exit("This script requires at least Python 3.4")
if sys.version_info >= (3, 5):
    import typing

IS_LINUX = sys.platform.startswith("linux")
IS_FREEBSD = sys.platform.startswith("freebsd")


def printCommand(arg1: "typing.Union[str, typing.Tuple, typing.List]", *args, cwd=None, **kwargs):
    yellow = "\x1b[1;33m"
    endColour = "\x1b[0m"  # reset
    # also allow passing a single string
    if not type(arg1) is str:
        allArgs = tuple(map(shlex.quote, arg1))
        arg1 = allArgs[0]
        args = allArgs[1:]
    newArgs = (yellow + "cd", shlex.quote(str(cwd)), "&&") if cwd else tuple()
    # comma in tuple is required otherwise it creates a tuple of string chars
    newArgs += (yellow + arg1,) + args + (endColour,)
    print(*newArgs, flush=True, **kwargs)


def runCmd(*args, captureOutput=False, **kwargs):
    if type(args[0]) is str or type(args[0]) is Path:
        cmdline = args  # multiple strings passed
    else:
        cmdline = args[0]  # list was passed
    cmdline = list(map(str, cmdline))  # make sure they are all strings
    printCommand(cmdline, cwd=kwargs.get("cwd"))
    kwargs["cwd"] = str(kwargs["cwd"]) if "cwd" in kwargs else os.getcwd()
    if not cheriConfig.pretend:
        # print(cmdline, kwargs)
        if captureOutput:
            return subprocess.check_output(cmdline, **kwargs)
        else:
            if cheriConfig.quiet and "stdout" not in kwargs:
                kwargs["stdout"] = subprocess.DEVNULL
            subprocess.check_call(cmdline, **kwargs)
    return b"" if captureOutput else None


def fatalError(*args):
    # we ignore fatal errors when simulating a run
    if cheriConfig.pretend:
        print("Potential fatal error:", *args)
    else:
        sys.exit(" ".join(map(str, args)))


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
        fromJSON = self._JSON.get(self.action.dest, None)
        if fromJSON and isDefault:
            print("Overriding default value for", self.action.dest, "with value from JSON:", fromJSON)
            result = fromJSON
        result = self.valueType(result)  # make sure it has the right type (e.g. Path, int, bool, str)

        ConfigLoader.values[self.action.dest] = result  # just for debugging
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


class CheriConfig(object):
    # boolean flags
    pretend = ConfigLoader.addBoolOption("pretend", "p", help="Only print the commands instead of running them")
    quiet = ConfigLoader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
    clean = ConfigLoader.addBoolOption("clean", "c", help="Remove the build directory before build")
    skipUpdate = ConfigLoader.addBoolOption("skip-update", help="Skip the git pull step")
    skipConfigure = ConfigLoader.addBoolOption("skip-configure", help="Skip the configure step")
    listTargets = ConfigLoader.addBoolOption("list-targets", help="List all available targets and exit")

    # configurable paths
    sourceRoot = ConfigLoader.addPathOption("source-root", default=DEFAULT_SOURCE_ROOT,
                                            help="The directory to store all sources")
    outputRoot = ConfigLoader.addPathOption("output-root", default=lambda p: (p.sourceRoot / "output"),
                                            help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
    extraFiles = ConfigLoader.addPathOption("extra-files", default=lambda p: (p.sourceRoot / "extra-files"),
                                            help="A directory with additional files that will be added to the image "
                                                 "(default: '<OUTPUT_ROOT>/extra-files')")
    diskImage = ConfigLoader.addPathOption("disk-image-path", default=lambda p: (p.outputRoot / "disk.img"),
                                           help="The output path for the QEMU disk image "
                                                "(default: '<OUTPUT_ROOT>/disk.img')")
    nfsKernelPath = ConfigLoader.addPathOption("nfs-kernel-path", default=lambda p: (p.outputRoot / "nfs/kernel"),
                                               help="The output path for the CheriBSD kernel that boots over NFS "
                                                    "(default: '<OUTPUT_ROOT>/nfs/kernel')")
    # other options
    makeJobs = ConfigLoader.addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(),
                                      help="Number of jobs to use for compiling")  # type: int

    def __init__(self):
        self.targets = ConfigLoader.loadTargets()
        self.makeJFlag = "-j" + str(self.makeJobs)

        print("Sources will be stored in", self.sourceRoot)
        print("Build artifacts will be stored in", self.outputRoot)
        print("Extra files for disk image will be searched for in", self.extraFiles)
        print("Disk image will saved to", self.diskImage)

        # now the derived config options
        self.cheribsdRootfs = self.outputRoot / "rootfs"
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / "cheribsd-obj"
        self.sdkDir = self.outputRoot / "sdk"  # qemu and binutils (and llvm/clang)
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
                 installDir: Path=None, gitUrl=""):
        self.name = name
        self.gitUrl = gitUrl
        self.config = config
        self.sourceDir = Path(sourceDir if sourceDir else config.sourceRoot / name)
        self.buildDir = Path(buildDir if buildDir else config.outputRoot / (name + "-build"))
        self.installDir = installDir
        self.makeCommand = "make"
        self.configureCommand = ""
        self.configureArgs = []  # type: typing.List[str]
        # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
        self.clearLineSequence = b"\x1b[2K\r"

    @staticmethod
    def _update_git_repo(srcDir: Path, remoteUrl):
        if not (srcDir / ".git").is_dir():
            print(srcDir, "is not a git repository. Clone it from' " + remoteUrl + "'?")
            if sys.__stdin__.isatty() and input("y/[N]").lower() != "y":
                sys.exit("Sources for " + str(srcDir) + " missing!")
            runCmd("git", "clone", remoteUrl, srcDir)
        # make sure we run git stash if we discover any local changes
        hasChanges = len(runCmd("git", "diff", captureOutput=True, cwd=srcDir)) > 1
        if hasChanges:
            runCmd("git", "stash", cwd=srcDir)
        runCmd("git", "pull", "--rebase", cwd=srcDir)
        if hasChanges:
            runCmd("git", "stash", "pop", cwd=srcDir)

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
        self._update_git_repo(self.sourceDir, self.gitUrl)

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
        super().__init__("qemu", config, installDir=config.sdkDir,
                         gitUrl="https://github.com/CTSRD-CHERI/qemu.git")
        # QEMU will not work with BSD make, need GNU make
        self.makeCommand = "gmake" if IS_FREEBSD else "make"
        self.configureCommand = self.sourceDir / "configure"
        self.configureArgs = ["--target-list=cheri-softmmu",
                              "--disable-linux-user",
                              "--disable-bsd-user",
                              "--disable-xen",
                              "--extra-cflags=-g -Wno-error=deprecated-declarations",
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
        super().__init__("llvm", config, installDir=config.sdkDir)
        self.makeCommand = "ninja"
        # try to find clang 3.7, otherwise fall back to system clang
        cCompiler = shutil.which("clang37") or "clang"
        cppCompiler = shutil.which("clang++37") or "clang++"
        # make sure we have at least version 3.7
        versionPattern = re.compile(b"clang version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # clang prints this output to stderr
        versionString = runCmd(cCompiler, "-v", captureOutput=True, stderr=subprocess.STDOUT)
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
        ]

    def _makeStdoutFilter(self, line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        super()._makeStdoutFilter(line)

    def update(self):
        self._update_git_repo(self.sourceDir, "https://github.com/CTSRD-CHERI/llvm.git")
        self._update_git_repo(self.sourceDir / "tools/clang", "https://github.com/CTSRD-CHERI/clang.git")
        self._update_git_repo(self.sourceDir / "tools/lldb", "https://github.com/CTSRD-CHERI/lldb.git")

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
        super().__init__(name, config, installDir=config.cheribsdRootfs, buildDir=config.cheribsdObj,
                         gitUrl="https://github.com/CTSRD-CHERI/cheribsd.git")
        self.binutilsDir = self.config.sdkDir / "mips64/bin"
        self.cheriCC = self.config.sdkDir / "bin/clang"
        self.installAsRoot = os.getuid() == 0
        self.commonMakeArgs = [
            "make", "CHERI=256", "CHERI_CC=" + str(self.cheriCC),
            # "CPUTYPE=mips64", # mipsfpu for hardware float
            # (apparently no longer supported: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            "-DNO_ROOT",  # use this even if current user is root, as without it the METALOG file is not created
            "DEBUG_FLAGS=-g",  # enable debug stuff
            "CROSS_BINUTILS_PREFIX=" + str(self.binutilsDir),  # use the CHERI-aware binutils and not the builtin ones
            # TODO: once clang can build the kernel:
            #  "-DCROSS_COMPILER_PREFIX=" + str(self.config.sdkDir / "bin")
            "KERNCONF=" + kernelConfig,
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
            file = self.config.cheribsdRootfs / i
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
        if not (self.binutilsDir / "as").is_file():
            fatalError("CHERI MIPS binutils are missing. Run 'build_cheribsd_for_qemu.py binutils'?")
        if self.installAsRoot:
            self._removeSchgFlag("lib/libc.so.7", "lib/libcrypt.so.5", "lib/libthr.so.3",
                                 "libexec/ld-cheri-elf.so.1", "libexec/ld-elf.so.1", "sbin/init",
                                 "usr/bin/chpass", "usr/bin/chsh", "usr/bin/ypchpass", "usr/bin/ypchfn",
                                 "usr/bin/ypchsh", "usr/bin/login", "usr/bin/opieinfo", "usr/bin/opiepasswd",
                                 "usr/bin/passwd", "usr/bin/yppasswd", "usr/bin/su", "usr/bin/crontab",
                                 "usr/lib/librt.so.1", "var/empty")
        # make sure the old install is purged before building, otherwise we might get strange errors
        # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
        # if we installed as root remove the schg flag from files before cleaning (otherwise rm will fail)
        self._cleanDir(self.installDir, force=True)

    def compile(self):
        self.setupEnvironment()
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildworld", cwd=self.sourceDir)
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildkernel", cwd=self.sourceDir)

    def install(self):
        # don't use multiple jobs here
        installArgs = self.commonMakeArgs + ["DESTDIR=" + str(self.installDir)]
        self.runMake(installArgs, "installworld", cwd=self.sourceDir)
        self.runMake(installArgs, "installkernel", cwd=self.sourceDir)
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
        # We don't want debug stuff with the SDK:
        self.commonMakeArgs.remove("DEBUG_FLAGS=-g")
        self.commonMakeArgs += [
            "DESTDIR=" + str(self.installDir),
            "MK_BINUTILS_BOOTSTRAP=no",  # don't build the binutils from the cheribsd source tree
            "MK_ELFTOOLCHAIN_BOOTSTRAP=no",  # don't build elftoolchain binaries
            "CROSS_COMPILER_PREFIX=" + str(self.config.sdkDir / "bin")
            # XDTP is not required, but why is it picking the wrong assembler
            # "XDTP=" + str(self.config.sdkDir / "mips64"),  # cross tools prefix
            # "CPUTYPE=mips64",  # cross tools prefix (otherwise makefile only appends -G0 which doesn't work without march=mips64

        ]

    def compile(self):
        self.setupEnvironment()
        self._cleanDir(self.installDir, force=True)  # make sure that the install dir is empty (can cause errors)
        # for now no parallel make
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "xdev-build", cwd=self.sourceDir)

    def install(self):
        # don't use multiple jobs here
        runCmd(self.commonMakeArgs + ["xdev-install"], cwd=self.sourceDir)


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
        # we need to add include files and libraries to the sysroot directory
        self._cleanDir(self.config.sdkSysrootDir, force=True)  # make sure the sysroot is cleaned
        self._makedirs(self.config.sdkSysrootDir / "usr")
        # use tar+untar to copy all ncessary files listed in metalog to the sysroot dir
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
        print("Successfully populated sysroot")


class LaunchQEMU(Project):
    def __init__(self, config):
        super().__init__("run", config)

    def process(self):
        qemuBinary = self.config.sdkDir / "bin/qemu-system-cheri"
        currentKernel = self.config.cheribsdRootfs / "boot/kernel/kernel"
        print("About to run QEMU with image ", self.config.diskImage, " and kernel ", currentKernel)
        # input("Press enter to continue")
        runCmd([qemuBinary, "-M", "malta",  # malta cpu
                "-kernel", currentKernel,  # assume the current image matches the kernel currently build
                "-nographic",  # no GPU
                "-m", "2048",  # 2GB memory
                "-hda", self.config.diskImage,
                "-net", "nic", "-net", "user",
                "-redir", "tcp:9999::22",  # bind the qemu ssh port to the hosts port 9999
                ], stdout=sys.stdout)  # even with --quiet we want stdout here


def main():
    # NOTE: This list must be in the right dependency order
    allTargets = [
        BuildBinutils(cheriConfig),
        BuildQEMU(cheriConfig),
        BuildLLVM(cheriConfig),
        BuildCHERIBSD(cheriConfig),
        BuildNfsKernel(cheriConfig),
        BuildNewSDK(cheriConfig),
        BuildSDK(cheriConfig),
        BuildDiskImage(cheriConfig),
        LaunchQEMU(cheriConfig),
    ]
    allTargetNames = [t.name for t in allTargets]
    selectedTargets = cheriConfig.targets
    if "all" in cheriConfig.targets:
        selectedTargets = allTargetNames
    # make sure all targets passed on commandline exist
    invalidTargets = set(selectedTargets) - set(allTargetNames)
    if len(invalidTargets) > 0 or cheriConfig.listTargets:
        for t in invalidTargets:
            print("Invalid target", t)
        print("The following targets exist:", list(allTargetNames))
        print("target 'all' can be used to build everything")
        sys.exit()

    for target in allTargets:
        if target.name in selectedTargets:
            target.process()


if __name__ == "__main__":
    cheriConfig = CheriConfig()
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Exiting due to Ctrl+C")
    except subprocess.CalledProcessError:
        # no need for the full traceback here
        print(sys.exc_info()[1])
