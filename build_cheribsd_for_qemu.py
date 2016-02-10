#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import shlex
import shutil
import tempfile
import threading
import difflib
import pprint
from pathlib import Path

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri

# change this if you want to customize where the sources go (or use --source-root=...)
DEFAULT_SOURCE_ROOT = Path(os.path.expanduser("~/cheri"))

if sys.version_info < (3, 4):
    sys.exit("This script requires at least Python 3.4")

# add the new 3.5 Path.home() and Path("foo").write_text() and 3.4.5 Path("foo").path to pathlib.Path
if sys.version_info < (3, 5, 2):
    # print("Working around old version of pathlib")
    Path.path = property(lambda self: str(self))
if sys.version_info < (3, 5):
    def _write_text(self, data, encoding=None, errors=None):
        if not isinstance(data, str):
            raise TypeError('data must be str, not %s' % data.__class__.__name__)
        with self.open(mode='w', encoding=encoding, errors=errors) as f:
            return f.write(data)

    Path.write_text = _write_text


def printCommand(*args, cwd="", **kwargs):
    yellow = "\x1b[1;33m"
    endColour = "\x1b[0m"  # reset
    newArgs = (yellow + "cd", shlex.quote(str(cwd)), "&&") if cwd else ()
    # comma in tuple is required otherwise it creates a tuple of string chars
    newArgs += (yellow + args[0],) + args[1:] + (endColour,)
    print(*newArgs, flush=True, **kwargs)


def runCmd(*args, **kwargs):
    if type(args[0]) is str or type(args[0]) is Path:
        cmdline = args  # multiple strings passed
    else:
        cmdline = args[0]  # list was passed
    cmdline = list(map(str, cmdline))  # make sure they are all strings
    cmdShellEscaped = " ".join(map(shlex.quote, cmdline))
    printCommand(cmdShellEscaped, cwd=kwargs.get("cwd"))
    kwargs["cwd"] = str(kwargs["cwd"]) if "cwd" in kwargs else os.getcwd()
    if config.quiet and "stdout" not in kwargs:
        kwargs["stdout"] = subprocess.DEVNULL
    if not config.pretend:
        # print(cmdline, kwargs)
        subprocess.check_call(cmdline, **kwargs)


def fatalError(message: str):
    # we ignore fatal errors when simulating a run
    if config.pretend:
        print("Potential fatal error:", message)
    else:
        sys.exit(message)


class CheriConfig(object):
    def __init__(self):
        self.parser = argparse.ArgumentParser(formatter_class=lambda prog:
                                              argparse.HelpFormatter(prog, width=shutil.get_terminal_size()[0]))

        _pretend = self._addBoolOption("pretend", "p", help="Print the commands that would be run instead of executing them")
        _quiet = self._addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
        _clean = self._addBoolOption("clean", "c", help="Remove the build directory before build")
        _skipUpdate = self._addBoolOption("skip-update", help="Skip the git pull step")
        _skipConfigure = self._addBoolOption("skip-configure", help="Skip the configure step")
        _listTargets = self._addBoolOption("list-targets", help="List all available targets and exit")

        _sourceRoot = self._addOption("source-root", default=DEFAULT_SOURCE_ROOT, help="The directory to store all sources")
        _outputRoot = self._addOption("output-root", help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
        _diskImage = self._addOption("disk-image-path", help="The output path for the QEMU disk image (default: '<OUTPUT_ROOT>/disk.img')")

        _makeJobs = self._addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(), help="Number of jobs to use for compiling")

        self.parser.add_argument("targets", metavar="TARGET", type=str, nargs="*", help="The targets to build", default=["all"])

        self._options = self.parser.parse_args()
        # TODO: load from config file
        # TODO: this can probably be made a lot simpler using lazy evaluation
        self.pretend = bool(self._loadOption(_pretend))
        self.quiet = bool(self._loadOption(_quiet))
        self.clean = bool(self._loadOption(_clean))
        self.skipUpdate = bool(self._loadOption(_skipUpdate))
        self.skipConfigure = bool(self._loadOption(_skipConfigure))
        self.listTargets = bool(self._loadOption(_listTargets))
        # path config options
        self.sourceRoot = Path(self._loadOption(_sourceRoot))
        self.outputRoot = Path(self._loadOption(_outputRoot, self.sourceRoot / "output"))
        self.diskImage = Path(self._loadOption(_diskImage, self.outputRoot / "disk.img"))

        self.makeJFlag = "-j" + str(self._loadOption(_makeJobs))
        self.targets = list(self._options.targets)

        print("Sources will be stored in", self.sourceRoot)
        print("Build artifacts will be stored in", self.outputRoot)
        print("Disk image will saved to", self.diskImage)

        # now the derived config options
        self.cheribsdRootfs = self.outputRoot / "rootfs"
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / "cheribsd-obj"
        self.hostToolsDir = self.outputRoot / "host-tools"  # qemu and binutils (and llvm/clang)
        pprint.pprint(vars(self))

    def _addOption(self, name: str, shortname=None, default=None, **kwargs) -> argparse.Action:
        if default and "help" in kwargs:
            kwargs["help"] = kwargs["help"] + " (default: \'" + str(default) + "\')"
            kwargs["default"] = default
        if shortname:
            action = self.parser.add_argument("--" + name, "-" + shortname, **kwargs)
        else:
            action = self.parser.add_argument("--" + name, **kwargs)
        assert isinstance(action, argparse.Action)
        print("add option:", vars(action))
        return action

    def _addBoolOption(self, name: str, shortname=None, **kwargs) -> str:
        return self._addOption(name, shortname, action="store_true", **kwargs)

    def _loadOption(self, action: argparse.Action, default=None) -> str:
        assert hasattr(self._options, action.dest)
        result = getattr(self._options, action.dest)
        print(action.dest, "=", result, "default =", default)
        return default if result is None else result

class Project(object):
    def __init__(self, name: str, config: CheriConfig, *, sourceDir="", buildDir="", installDir: Path=None, gitUrl=""):
        self.name = name
        self.gitUrl = gitUrl
        self.config = config
        self.sourceDir = Path(sourceDir if sourceDir else config.sourceRoot / name)
        self.buildDir = Path(buildDir if buildDir else config.outputRoot / (name + "-build"))
        self.installDir = installDir
        self.makeCommand = "make"
        self.configureCommand = None
        self.configureArgs = []

    @staticmethod
    def _update_git_repo(srcDir: Path, remoteUrl):
        if not (srcDir / ".git").is_dir():
            print(srcDir.path, "is not a git repository. Clone it from' " + remoteUrl + "'?")
            if sys.__stdin__.isatty() and input("y/[N]").lower() != "y":
                sys.exit("Sources for " + srcDir.path + " missing!")
            runCmd("git", "clone", remoteUrl, srcDir)
        runCmd("git", "pull", "--rebase", cwd=srcDir)

    def _makedirs(self, dir: Path):
        printCommand("mkdir", "-p", dir)
        if not self.config.pretend:
            os.makedirs(dir.path, exist_ok=True)

    # removes a directory tree if --clean is passed (or force=True parameter is passed)
    def _cleanDir(self, dir: Path, force=False):
        if (self.config.clean or force) and dir.is_dir():
            # http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
            # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
            runCmd(["rm", "-rf", dir.path])
        # make sure the dir is empty afterwars
        self._makedirs(dir)

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
        # make sure the dir is empty afterwards
        self._makedirs(self.buildDir)

    def configure(self):
        if self.configureCommand:
            runCmd([self.configureCommand] + self.configureArgs, cwd=self.buildDir)

    def compile(self):
        runCmd(self.makeCommand, self.config.makeJFlag, cwd=self.buildDir)

    def install(self):
        runCmd(self.makeCommand, "install", cwd=self.buildDir)

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
        super().__init__("qemu", config, installDir=config.hostToolsDir,
                         gitUrl="https://github.com/CTSRD-CHERI/qemu.git")
        # QEMU will not work with BSD make, need GNU make
        self.makeCommand = "gmake"
        self.configureCommand = self.sourceDir / "configure"
        self.configureArgs = ["--target-list=cheri-softmmu",
                              "--disable-linux-user",
                              "--disable-linux-aio",
                              "--disable-kvm",
                              "--disable-xen",
                              "--extra-cflags=-g",
                              "--prefix=" + self.installDir.path]

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir():
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir)
        super().update()


class BuildBinutils(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("binutils", config, installDir=config.hostToolsDir,
                         gitUrl="https://github.com/CTSRD-CHERI/binutils.git")
        self.configureCommand = self.sourceDir / "configure"
        self.configureArgs = ["--target=mips64", "--disable-werror", "--prefix=" + self.installDir.path]


class BuildLLVM(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("llvm", config, installDir=config.hostToolsDir)
        self.makeCommand = "ninja"
        # FIXME: what is the correct default sysroot
        # should expand to ~/cheri/qemu/obj/mips.mips64/home/alr48/cheri/cheribsd
        # I think this might be correct: it contains x86 binaries but mips libraries so should be right)
        # if we pass a path starting with a slash to Path() it will reset to that absolute path
        # luckily we have to prepend mips.mips64, so it works out fine
        sysroot = Path(self.config.cheribsdObj, "mips.mips64" + self.config.cheribsdSources.path, "tmp")
        # try to find clang 3.7, otherwise fall
        cCompiler = shutil.which("clang37") or "clang"
        cppCompiler = shutil.which("clang++37") or "clang++"
        self.configureCommand = "cmake"
        self.configureArgs = [
            self.sourceDir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_CXX_COMPILER=" + cppCompiler, "-DCMAKE_C_COMPILER=" + cCompiler,  # need at least 3.7 to build it
            "-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd",
            "-DCMAKE_INSTALL_PREFIX=" + self.installDir.path,
            "-DDEFAULT_SYSROOT=" + sysroot.path,
            "-DLLVM_TOOL_LLDB_BUILD=OFF", # disable LLDB for now
        ]

    def update(self):
        self._update_git_repo(self.sourceDir, "https://github.com/CTSRD-CHERI/llvm.git")
        self._update_git_repo(self.sourceDir / "tools/clang", "https://github.com/CTSRD-CHERI/clang.git")
        self._update_git_repo(self.sourceDir / "tools/lldb", "https://github.com/CTSRD-CHERI/lldb.git")

    def install(self):
        runCmd(["ninja", "install"], cwd=self.buildDir)
        # delete the files incompatible with cheribsd
        incompatibleFiles = list(self.installDir.glob("lib/clang/3.*/include/std*"))
        incompatibleFiles += self.installDir.glob("lib/clang/3.*/include/limits.h")
        if len(incompatibleFiles) == 0:
            fatalError("Could not find incompatible builtin includes. Build system changed?")
        for i in incompatibleFiles:
            printCommand("rm", shlex.quote(i.path))
            if not self.config.pretend:
                i.unlink()


class BuildCHERIBSD(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("cheribsd", config, installDir=config.cheribsdRootfs, buildDir=config.cheribsdObj,
                         gitUrl="https://github.com/CTSRD-CHERI/cheribsd.git")

    def runMake(self, args, target):
        allArgs = args + [target]
        printCommand(" ".join(allArgs), cwd=self.sourceDir)
        if self.config.pretend:
            return
        logfilePath = Path(self.buildDir / ("build." + target + ".log"))
        print("Saving build log to", logfilePath)

        def handleStdErr(logfile, stream, logfileLock):
            for line in stream:
                sys.stderr.buffer.write(line)
                sys.stderr.flush()
                with logfileLock:
                    logfile.write(line)

        with logfilePath.open("wb") as logfile:
            # TODO: add a verbose option that shows every line
            # quiet doesn't display anything, normal only status updates and verbose everything
            if self.config.quiet:
                # a lot more efficient than filtering every line
                subprocess.check_call(allArgs, cwd=self.sourceDir.path, stdout=logfile)
                return
            # by default only show limited progress:e.g. ">>> stage 2.1: cleaning up the object tree"
            make = subprocess.Popen(allArgs, cwd=self.sourceDir.path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            # use a thread to print stderr output and write it to logfile (not using a thread would block)
            logfileLock = threading.Lock()  # we need a mutex so the logfile line buffer doesn't get messed up
            stderrThread = threading.Thread(target=handleStdErr, args=(logfile, make.stderr, logfileLock))
            stderrThread.start()
            # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
            clearLine = b"\x1b[2K\r"
            for line in make.stdout:
                with logfileLock:
                    logfile.write(line)
                if line.startswith(b">>> "):  # major status update
                    sys.stdout.buffer.write(clearLine)
                    sys.stdout.buffer.write(line)
                elif line.startswith(b"===> "):  # new subdirectory
                    # clear the old line to have a continuously updating progress
                    sys.stdout.buffer.write(clearLine)
                    sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
                    sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
                    sys.stdout.buffer.flush()
            retcode = make.wait()
            stderrThread.join()
            print("")  # add a newline at the end in case it didn't finish with a  >>> line
            if retcode:
                raise subprocess.CalledProcessError(retcode, allArgs)

    def compile(self):
        os.environ["MAKEOBJDIRPREFIX"] = self.buildDir.path
        # make sure the new binutils are picked up
        if not os.environ["PATH"].startswith(self.config.hostToolsDir.path):
            os.environ["PATH"] = (self.config.hostToolsDir / "bin").path + ":" + os.environ["PATH"]
            print("Set PATH to", os.environ["PATH"])
        cheriCC = self.config.hostToolsDir / "bin/clang"
        if not cheriCC.is_file():
            fatalError("CHERI CC does not exist: " + cheriCC.path)
        self.commonMakeArgs = [
            "make", "CHERI=256", "CHERI_CC=" + cheriCC.path,
            # "CPUTYPE=mips64", # mipsfpu for hardware float (apparently no longer supported: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_ROOT",  # -DNO_ROOT install without using root privilege
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            "DEBUG_FLAGS=-g",  # enable debug stuff
            "DESTDIR=" + self.installDir.path,
            "KERNCONF=CHERI_MALTA64",
            # "-DNO_CLEAN", # don't clean before (takes ages) and the rm -rf we do before should be enough
        ]
        # make sure the old install is purged before building, otherwise we might get strange errors
        # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
        self._cleanDir(self.installDir, force=True)
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildworld")
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildkernel")

    def writeFile(self, path: Path, contents: str):
        printCommand("echo", shlex.quote(contents.replace("\n", "\\n")), ">", shlex.quote(path.path))
        if self.config.pretend:
            return
        if path.is_file():
            oldContents = path.read_text("utf-8")
            print("Overwriting old file", path, "- contents:\n\n", oldContents, "\n")
            if input("Continue? [Y/n]").lower() == "n":
                sys.exit()
        path.write_text(contents + "\n")

    def install(self):
        # don't use multiple jobs here
        self.runMake(self.commonMakeArgs, "installworld")
        self.runMake(self.commonMakeArgs, "installkernel")
        self.runMake(self.commonMakeArgs, "distribution")
        # TODO: make this configurable to allow NFS, etc.
        self.writeFile(self.config.cheribsdRootfs / "etc/fstab", "/dev/ada0 / ufs rw 1 1")

        # enable ssh and set hostname
        # TODO: use seperate file in /etc/rc.conf.d/ ?
        networkConfigOptions = (
            'hostname="qemu-cheri-' + os.getlogin() + '"\n'
            'ifconfig_le0="DHCP"\n'
            'sshd_enable="YES"')
        self.writeFile(self.config.cheribsdRootfs / "etc/rc.conf", networkConfigOptions)


class BuildDiskImage(Project):
    def __init__(self, config):
        super().__init__("disk-image", config)

    def process(self):
        if self.config.diskImage.is_file():
            # only show prompt if we can actually input something to stdin
            if sys.__stdin__.isatty() and not self.config.pretend:
                yn = input("An image already exists (" + self.config.diskImage.path + "). Overwrite? [Y/n] ")
                if str(yn).lower() == "n":
                    return
            printCommand("rm", self.config.diskImage.path)
            self.config.diskImage.unlink()
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        manifestFile = self.config.cheribsdRootfs / "METALOG"
        if not manifestFile.is_file():
            fatalError("mtree manifest " + manifestFile.path + " is missing")
        userGroupDbDir = self.config.cheribsdSources / "etc"
        if not (userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in " + userGroupDbDir.path)
        runCmd([
            "makefs",
            "-b", "70%",  # minimum 70% free blocks
            "-f", "30%",  # minimum 30% free inodes
            "-M", "4g",  # minimum image size = 4GB
            "-B", "be",  # big endian byte order
            "-F", manifestFile,  # use METALOG as the manifest for the disk image
            "-N", userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file (makes sure that the numeric UID values are correct
            self.config.diskImage,  # output file
            self.config.cheribsdRootfs  # directory tree to use for the image
        ])


class LaunchQEMU(Project):
    def __init__(self, config):
        super().__init__("run", config)

    def process(self):
        qemuBinary = self.config.hostToolsDir / "bin/qemu-system-cheri"
        currentKernel = self.config.cheribsdRootfs / "boot/kernel/kernel"
        print("About to run QEMU with image " + self.config.diskImage.path + " and kernel " + currentKernel.path)
        # input("Press enter to continue")
        runCmd([qemuBinary, "-M", "malta",  # malta cpu
                "-kernel", currentKernel,  # assume the current image matches the kernel currently build
                "-nographic",  # no GPU
                "-m", "2048",  # 2GB memory
                "-hda", self.config.diskImage,
                "-net", "nic", "-net", "user",
                "-redir", "tcp:9999::22",  # bind the qemu ssh port to the hosts port 9999
                ], stdout=sys.stdout)  # even with --quiet we want stdout here


def defaultNumberOfMakeJobs():
    makeJobs = os.cpu_count()
    if makeJobs > 24:
        # don't use up all the resources on shared build systems (you can still override this with the -j command line option)
        makeJobs = 16
    return makeJobs

if __name__ == "__main__":
    config = CheriConfig()

    # NOTE: This list must be in the right dependency order
    allTargets = [
        BuildBinutils(config),
        BuildQEMU(config),
        BuildLLVM(config),
        BuildCHERIBSD(config),
        BuildDiskImage(config),
        LaunchQEMU(config),
    ]
    allTargetNames = [t.name for t in allTargets]
    selectedTargets = config.targets
    if "all" in config.targets:
        selectedTargets = allTargetNames
    # make sure all targets passed on commandline exist
    invalidTargets = set(selectedTargets) - set(allTargetNames)
    if invalidTargets or config.listTargets:
        for t in invalidTargets:
            print("Invalid target", t)
        print("The following targets exist:", list(allTargetNames))
        print("target 'all' can be used to build everything")
        sys.exit()

    for target in allTargets:
        if target.name in selectedTargets:
            target.process()
