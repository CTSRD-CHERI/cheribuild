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
from pathlib import Path

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri

# change this if you want to customize where the sources go (or use --source-root=...)
DEFAULT_SOURCE_ROOT = Path(os.path.expanduser("~/cheri"))

if sys.version_info < (3, 4):
    sys.exit("This script requires at least Python 3.4")
if sys.version_info >= (3, 5):
    import typing


def printCommand(*args, cwd=None, **kwargs):
    yellow = "\x1b[1;33m"
    endColour = "\x1b[0m"  # reset
    newArgs = (yellow + "cd", shlex.quote(str(cwd)), "&&") if cwd else tuple()
    # comma in tuple is required otherwise it creates a tuple of string chars
    newArgs += (yellow + args[0],) + args[1:] + (endColour,)
    print(*newArgs, flush=True, **kwargs)


def runCmd(*args, captureOutput=False, **kwargs):
    if type(args[0]) is str or type(args[0]) is Path:
        cmdline = args  # multiple strings passed
    else:
        cmdline = args[0]  # list was passed
    cmdline = list(map(str, cmdline))  # make sure they are all strings
    cmdShellEscaped = " ".join(map(shlex.quote, cmdline))
    printCommand(cmdShellEscaped, cwd=kwargs.get("cwd"))
    kwargs["cwd"] = str(kwargs["cwd"]) if "cwd" in kwargs else os.getcwd()
    if not cheriConfig.pretend:
        # print(cmdline, kwargs)
        if captureOutput:
            return subprocess.check_output(cmdline, **kwargs)
        else:
            if cheriConfig.quiet and "stdout" not in kwargs:
                kwargs["stdout"] = subprocess.DEVNULL
            subprocess.check_call(cmdline, **kwargs)
    return "" if captureOutput else None


def fatalError(*args):
    # we ignore fatal errors when simulating a run
    if cheriConfig.pretend:
        print("Potential fatal error:", *args)
    else:
        sys.exit(" ".join(args))


class CheriConfig(object):
    def __init__(self):
        def formatterSetup(prog):
            return argparse.HelpFormatter(prog, width=shutil.get_terminal_size()[0])

        self._parser = argparse.ArgumentParser(formatter_class=formatterSetup)

        _pretend = self._addBoolOption("pretend", "p",
                                       help="Print the commands that would be run instead of executing them")
        _quiet = self._addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
        _clean = self._addBoolOption("clean", "c", help="Remove the build directory before build")
        _skipUpdate = self._addBoolOption("skip-update", help="Skip the git pull step")
        _skipConfigure = self._addBoolOption("skip-configure", help="Skip the configure step")
        _listTargets = self._addBoolOption("list-targets", help="List all available targets and exit")

        _sourceRoot = self._addOption("source-root", default=DEFAULT_SOURCE_ROOT,
                                      help="The directory to store all sources")
        _outputRoot = self._addOption("output-root",
                                      help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
        _extraFiles = self._addOption("extra-files", help="A directory with additional files that will be added to the"
                                      " image (default: '<OUTPUT_ROOT>/extra-files')")
        _diskImage = self._addOption("disk-image-path",
                                     help="The output path for the QEMU disk image (default: '<OUTPUT_ROOT>/disk.img')")

        _makeJobs = self._addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(),
                                    help="Number of jobs to use for compiling")

        self._parser.add_argument("targets", metavar="TARGET", type=str, nargs="*",
                                  help="The targets to build", default=["all"])

        self._options = self._parser.parse_args()
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
        self.extraFiles = Path(self._loadOption(_extraFiles, self.sourceRoot / "extra-files"))
        self.diskImage = Path(self._loadOption(_diskImage, self.outputRoot / "disk.img"))

        self.makeJFlag = "-j" + str(self._loadOption(_makeJobs))
        self.targets = list(self._options.targets)

        print("Sources will be stored in", self.sourceRoot)
        print("Build artifacts will be stored in", self.outputRoot)
        print("Extra files for disk image will be searched for in", self.extraFiles)
        print("Disk image will saved to", self.diskImage)

        # now the derived config options
        self.cheribsdRootfs = self.outputRoot / "rootfs"
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / "cheribsd-obj"
        self.sdkDir = self.outputRoot / "host-tools"  # qemu and binutils (and llvm/clang)

        for d in (self.sourceRoot, self.outputRoot, self.extraFiles):
            if not self.pretend:
                printCommand("mkdir", "-p", str(d))
                os.makedirs(str(d), exist_ok=True)

        del self._options
        del self._parser
        pprint.pprint(vars(self))

    def _addOption(self, name: str, shortname=None, default=None, **kwargs) -> argparse.Action:
        if default and "help" in kwargs:
            kwargs["help"] = kwargs["help"] + " (default: \'" + str(default) + "\')"
            kwargs["default"] = default
        if shortname:
            action = self._parser.add_argument("--" + name, "-" + shortname, **kwargs)
        else:
            action = self._parser.add_argument("--" + name, **kwargs)
        assert isinstance(action, argparse.Action)
        # print("add option:", vars(action))
        return action

    def _addBoolOption(self, name: str, shortname=None, **kwargs) -> argparse.Action:
        return self._addOption(name, shortname, action="store_true", **kwargs)

    def _loadOption(self, action: argparse.Action, default=None) -> argparse.Action:
        assert hasattr(self._options, action.dest)
        result = getattr(self._options, action.dest)
        # print(action.dest, "=", result, "default =", default)
        return default if result is None else result


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

    def runMake(self, args: "typing.List[str]", makeTarget="", *, cwd: Path=None) -> None:
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
                subprocess.check_call(allArgs, cwd=str(cwd), stdout=logfile)
                return
            make = subprocess.Popen(allArgs, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
        self.makeCommand = "gmake"
        self.configureCommand = self.sourceDir / "configure"
        self.configureArgs = ["--target-list=cheri-softmmu",
                              "--disable-linux-user",
                              "--disable-linux-aio",
                              "--disable-kvm",
                              "--disable-xen",
                              "--extra-cflags=-g",
                              "--prefix=" + str(self.installDir)]

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir():
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir)
        super().update()


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
        self.configureCommand = "cmake"
        self.configureArgs = [
            self.sourceDir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_CXX_COMPILER=" + cppCompiler, "-DCMAKE_C_COMPILER=" + cCompiler,  # need at least 3.7 to build it
            "-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd",
            "-DCMAKE_INSTALL_PREFIX=" + str(self.installDir),
            "-DDEFAULT_SYSROOT=" + str(self.config.sdkDir / "sysroot"),
            "-DLLVM_TOOL_LLDB_BUILD=OFF",  # disable LLDB for now
        ]

    def _makeStdoutFilter(self, line: bytes):
        # don't show the up-to date install lines
        if not line.startswith(b"-- Up-to-date:"):
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

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
    def __init__(self, config: CheriConfig):
        super().__init__("cheribsd", config, installDir=config.cheribsdRootfs, buildDir=config.cheribsdObj,
                         gitUrl="https://github.com/CTSRD-CHERI/cheribsd.git")

    def _makeStdoutFilter(self, line):
        if line.startswith(b">>> "):  # major status update
            sys.stdout.buffer.write(self.clearLineSequence)
            sys.stdout.buffer.write(line)
        elif line.startswith(b"===> "):  # new subdirectory
            # clear the old line to have a continuously updating progress
            sys.stdout.buffer.write(self.clearLineSequence)
            sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
            sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
            sys.stdout.buffer.flush()

    def compile(self):
        os.environ["MAKEOBJDIRPREFIX"] = str(self.buildDir)
        # make sure the new binutils are picked up
        if not os.environ["PATH"].startswith(str(self.config.sdkDir)):
            os.environ["PATH"] = str(self.config.sdkDir / "bin") + ":" + os.environ["PATH"]
            print("Set PATH to", os.environ["PATH"])
        cheriCC = self.config.sdkDir / "bin/clang"
        if not cheriCC.is_file():
            fatalError("CHERI CC does not exist: " + str(cheriCC))
        self.commonMakeArgs = [
            "make", "CHERI=256", "CHERI_CC=" + str(cheriCC),
            # "CPUTYPE=mips64", # mipsfpu for hardware float (apparently no longer supported: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_ROOT",  # -DNO_ROOT install without using root privilege
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            "DEBUG_FLAGS=-g",  # enable debug stuff
            "DESTDIR=" + str(self.installDir),
            "KERNCONF=CHERI_MALTA64",
            # "-DNO_CLEAN", # don't clean before (takes ages) and the rm -rf we do before should be enough
        ]
        # make sure the old install is purged before building, otherwise we might get strange errors
        # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
        self._cleanDir(self.installDir, force=True)
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildworld")
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildkernel")

    def install(self):
        # don't use multiple jobs here
        self.runMake(self.commonMakeArgs, "installworld")
        self.runMake(self.commonMakeArgs, "installkernel")
        self.runMake(self.commonMakeArgs, "distribution")


class BuildDiskImage(Project):
    def __init__(self, config):
        super().__init__("disk-image", config)
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        self.manifestFile = self.config.cheribsdRootfs / "METALOG"
        if not self.manifestFile.is_file():
            fatalError("mtree manifest", self.manifestFile, "is missing")
        self.userGroupDbDir = self.config.cheribsdSources / "etc"
        if not (self.userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in ", self.userGroupDbDir)

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


def defaultNumberOfMakeJobs():
    makeJobs = os.cpu_count()
    if makeJobs > 24:
        # don't use up all the resources on shared build systems
        # (you can still override this with the -j command line option)
        makeJobs = 16
    return makeJobs


def main():
    # NOTE: This list must be in the right dependency order
    allTargets = [
        BuildBinutils(cheriConfig),
        BuildQEMU(cheriConfig),
        BuildLLVM(cheriConfig),
        BuildCHERIBSD(cheriConfig),
        BuildDiskImage(cheriConfig),
        LaunchQEMU(cheriConfig),
    ]
    allTargetNames = [t.name for t in allTargets]
    selectedTargets = cheriConfig.targets
    if "all" in cheriConfig.targets:
        selectedTargets = allTargetNames
    # make sure all targets passed on commandline exist
    invalidTargets = set(selectedTargets) - set(allTargetNames)
    if invalidTargets or cheriConfig.listTargets:
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
    main()
