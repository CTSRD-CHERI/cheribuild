#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import shlex
import shutil
import tempfile
from pathlib import Path
import difflib

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
    if options.quiet and "stdout" not in kwargs:
        kwargs["stdout"] = subprocess.DEVNULL
    if not options.pretend:
        # print(cmdline, kwargs)
        subprocess.check_call(cmdline, **kwargs)


def fatalError(message: str):
    # we ignore fatal errors when simulating a run
    if options.pretend:
        print("Potential fatal error:", message)
    else:
        sys.exit(message)


class CheriPaths(object):
    def __init__(self, cmdlineArgs: argparse.Namespace):
        self.sourceRoot = Path(cmdlineArgs.source_root) if cmdlineArgs.source_root else DEFAULT_SOURCE_ROOT
        self.outputRoot = Path(cmdlineArgs.output_root) if cmdlineArgs.output_root else self.sourceRoot / "output"
        self.diskImage = Path(cmdlineArgs.disk_image_path) if cmdlineArgs.disk_image_path else self.outputRoot / "disk.img"
        print("Sources will be stored in", self.sourceRoot)
        print("Build artifacts will be stored in", self.outputRoot)
        print("Disk image will saved to", self.diskImage)
        self.cheribsdRootfs = self.outputRoot / "rootfs"
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / "cheribsd-obj"
        self.hostToolsDir = self.outputRoot / "host-tools"  # qemu and binutils (and llvm/clang)


class Project(object):
    def __init__(self, name: str, paths: CheriPaths, *, sourceDir="", buildDir="", installDir: Path=None, gitUrl=""):
        self.name = name
        self.gitUrl = gitUrl
        self.paths = paths
        self.sourceDir = Path(sourceDir if sourceDir else paths.sourceRoot / name)
        self.buildDir = Path(buildDir if buildDir else paths.outputRoot / (name + "-build"))
        self.installDir = installDir
        self.makeCommand = "make"
        self.configureCommand = None
        self.configureArgs = []

    @staticmethod
    def _update_git_repo(srcDir: Path, remoteUrl):
        if not (srcDir / ".git").is_dir():
            print(srcDir.path, "is not a git repository. Clone it from' " + remoteUrl + "'?")
            if sys.__stdin__.isatty() and input("y/[N]").lower() != "y":
                sys.exit("Sources for " + srcDir.path + "missing!")
            runCmd("git", "clone", remoteUrl, srcDir)
        runCmd("git", "pull", "--rebase", cwd=srcDir)

    def _makedirs(self, dir: Path):
        printCommand("mkdir", "-p", dir)
        if not options.pretend:
            os.makedirs(dir.path, exist_ok=True)

    # removes a directory tree if --clean is passed (or force=True parameter is passed)
    def _cleanDir(self, dir: Path, force=False):
        if (options.clean or force) and dir.is_dir():
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
        runCmd(self.makeCommand, makeJFlag, cwd=self.buildDir)

    def install(self):
        runCmd(self.makeCommand, "install", cwd=self.buildDir)

    def process(self):
        if not options.skip_update:
            self.update()
        if options.clean:
            self.clean()
        # always make sure the build dir exists
        if not self.buildDir.is_dir():
            self._makedirs(self.buildDir)
        if not options.skip_configure:
            self.configure()
        self.compile()
        self.install()


class BuildQEMU(Project):
    def __init__(self, paths: CheriPaths):
        super().__init__("qemu", paths, installDir=paths.hostToolsDir,
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
    def __init__(self, paths: CheriPaths):
        super().__init__("binutils", paths, installDir=paths.hostToolsDir,
                         gitUrl="https://github.com/CTSRD-CHERI/binutils.git")
        self.configureCommand = self.sourceDir / "configure"
        self.configureArgs = ["--target=mips64", "--disable-werror", "--prefix=" + self.installDir.path]


class BuildLLVM(Project):
    def __init__(self, paths: CheriPaths):
        # NOTE: currently we don't use the installDir because we use the compiler from the build directory
        # TODO: install the compiler
        super().__init__("llvm", paths, installDir=paths.hostToolsDir)
        self.makeCommand = "ninja"
        # FIXME: what is the correct default sysroot
        # should expand to ~/cheri/qemu/obj/mips.mips64/home/alr48/cheri/cheribsd
        # I think this might be correct: it contains x86 binaries but mips libraries so should be right)
        # if we pass a path starting with a slash to Path() it will reset to that absolute path
        # luckily we have to prepend mips.mips64, so it works out fine
        sysroot = Path(self.paths.cheribsdObj, "mips.mips64" + self.paths.cheribsdSources.path, "tmp")
        self.configureCommand = "cmake"
        self.configureArgs = [
            self.sourceDir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_CXX_COMPILER=clang++37", "-DCMAKE_C_COMPILER=clang37",  # need at least 3.7 to build it
            "-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd",
            "-DCMAKE_INSTALL_PREFIX=" + self.installDir.path,
            "-DDEFAULT_SYSROOT=" + sysroot.path,
        ]

    def update(self):
        self._update_git_repo(self.sourceDir, "https://github.com/CTSRD-CHERI/llvm.git")
        self._update_git_repo(self.sourceDir / "tools/clang", "https://github.com/CTSRD-CHERI/clang.git")

    def install(self):
        # runCmd(["ninja", "install"])
        # we don't actually install yet (TODO: would it make sense to do that?)
        # delete the files incompatible with cheribsd
        incompatibleFiles = list(self.buildDir.glob("lib/clang/3.*/include/std*"))
        incompatibleFiles += self.buildDir.glob("lib/clang/3.*/include/limits.h")
        if len(incompatibleFiles) == 0:
            fatalError("Could not find incompatible builtin includes. Build system changed?")
        for i in incompatibleFiles:
            printCommand("rm", shlex.quote(i.path))
            if not options.pretend:
                i.unlink()


class BuildCHERIBSD(Project):
    def __init__(self, paths: CheriPaths):
        super().__init__("cheribsd", paths, installDir=paths.cheribsdRootfs, buildDir=paths.cheribsdObj,
                         gitUrl="https://github.com/CTSRD-CHERI/cheribsd.git")

    def runMake(self, args, target):
        args.append(target)
        printCommand(" ".join(args), cwd=self.sourceDir)
        if options.pretend:
            return
        logfilePath = Path(self.buildDir / ("build." + target + ".log"))
        print("Saving build log to", logfilePath)
        with logfilePath.open("wb") as logfile:
            # TODO: add a verbose option that shows every line
            # quiet doesn't display anything, normal only status updates and verbose everything
            if options.quiet:
                # a lot more efficient than filtering every line
                subprocess.check_call(args, cwd=self.sourceDir.path, stdout=logfile)
                return
            # by default only show limited progress:e.g. ">>> stage 2.1: cleaning up the object tree"
            make = subprocess.Popen(args, cwd=self.sourceDir.path, stdout=subprocess.PIPE)
            # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
            clearLine = b"\x1b[2K\r"
            for line in make.stdout:
                logfile.write(line)
                # TODO: verbose mode
                # if options.verbose:
                    # sys.stdout.buffer.write(line)
                    # continue

                if line.startswith(b">>> "):  # major status update
                    sys.stdout.buffer.write(clearLine)
                    sys.stdout.buffer.write(line)
                elif line.startswith(b"===> "):  # new subdirectory
                    # clear the old line to have a continuously updating progress
                    sys.stdout.buffer.write(clearLine)
                    sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
                    sys.stdout.buffer.flush()
            retcode = make.wait()
            print("")  # add a newline at the end in case it didn't finish with a  >>> line
            if retcode:
                raise subprocess.CalledProcessError(retcode, args)

    def compile(self):
        os.environ["MAKEOBJDIRPREFIX"] = self.buildDir.path
        # make sure the new binutils are picked up
        if not os.environ["PATH"].startswith(self.paths.hostToolsDir.path):
            os.environ["PATH"] = (self.paths.hostToolsDir / "bin").path + ":" + os.environ["PATH"]
            print("Set PATH to", os.environ["PATH"])
        cheriCC = self.paths.outputRoot / "llvm-build/bin/clang"  # FIXME: see if it works with installing
        if not cheriCC.is_file():
            fatalError("CHERI CC does not exist: " + cheriCC.path)
        self.commonMakeArgs = [
            "make", "CHERI=256", "CHERI_CC=" + cheriCC.path,
            # "CPUTYPE=mips64", # mipsfpu for hardware float (apparently no longer supported: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_ROOT",  # -DNO_ROOT install without using root privilege
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "DESTDIR=" + self.installDir.path,
            "KERNCONF=CHERI_MALTA64",
            # "-DNO_CLEAN", # don't clean before (takes ages) and the rm -rf we do before should be enough
        ]
        # make sure the old install is purged before building, otherwise we might get strange errors
        # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
        self._cleanDir(self.installDir, force=True)
        self.runMake(self.commonMakeArgs + [makeJFlag], "buildworld")
        self.runMake(self.commonMakeArgs + [makeJFlag], "buildkernel")

    def install(self):
        # don't use multiple jobs here
        self.runMake(self.commonMakeArgs, "installworld")
        self.runMake(self.commonMakeArgs, "installkernel")
        self.runMake(self.commonMakeArgs, "distribution")
        # TODO: make this configurable to allow NFS, etc.
        fstabContents = "/dev/ada0 / ufs rw 1 1\n"
        fstabPath = self.paths.cheribsdRootfs / "etc/fstab"

        if options.pretend:
            printCommand("echo", shlex.quote(fstabContents.replace("\n", "\\n")), ">", shlex.quote(fstabPath.path))
        else:
            fstabPath.write_text(fstabContents)  # TODO: NFS?


class BuildDiskImage(Project):
    def __init__(self, paths):
        super().__init__("disk-image", paths)

    def patchManifestFile(self, tmpdir, manifestFile):
        # See https://github.com/CTSRD-CHERI/cheribsd/issues/107
        patchedManifestFile = Path(tmpdir, "METALOG")
        if options.pretend:
            return patchedManifestFile  # don't actually write the file

        # FIXME: multiple variables in one with statement cause will cause the last lines to not be written, WTF is wrong
        # work around it by using nested with statements
        with manifestFile.open("r") as orig:
            with patchedManifestFile.open("w") as patched:
                for line in orig:
                    if line.startswith("./usr/libcheri/.debug/ type=dir"):
                        # print("skipping ./usr/libcheri/.debug/ line")
                        continue  # don't write this line as we already inserted it further up in the file
                    patched.write(line)
                    if line.startswith("./usr/libcheri type=dir"):
                        # print("found ./usr/libcheri, addding ./usr/libcheri/.debug to METALOG")
                        patched.write("./usr/libcheri/.debug type=dir mode=0755 tags=debug\n")

        # create a diff to check if the number of changes matches
        with manifestFile.open() as a, patchedManifestFile.open() as b:
            diff = list(difflib.unified_diff(list(a), list(b)))
            if len(diff) != 18:
                print("Diff of patched METALOG has unexpected format (wrong number of changes):")
                print("".join(diff))
                sys.exit("METALOG format has changed, cannot patch it!!")
        print("Sucessfully patched METALOG")
        return patchedManifestFile

    def process(self):
        if self.paths.diskImage.is_file():
            # only show prompt if we can actually input something to stdin
            if sys.__stdin__.isatty():
                yn = input("An image already exists (" + self.paths.diskImage.path + "). Overwrite? [Y/n] ")
                if str(yn).lower() == "n":
                    return
            printCommand("rm", self.paths.diskImage.path)
            self.paths.diskImage.unlink()
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        manifestFile = self.paths.cheribsdRootfs / "METALOG"
        if not manifestFile.is_file():
            fatalError("mtree manifest " + manifestFile.path + " is missing")
        userGroupDbDir = self.paths.cheribsdSources / "etc"
        if not (userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in " + userGroupDbDir.path)
        # for now we need to patch the METALOG FILE:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifestFile = self.patchManifestFile(tmpdir, manifestFile)
            runCmd([
                "makefs",
                "-M", "1077936128",  # minimum image size = 1GB
                "-B", "be",  # big endian byte order
                "-F", manifestFile,  # use METALOG as the manifest for the disk image
                "-N", userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file (makes sure that the numeric UID values are correct
                self.paths.diskImage,  # output file
                self.paths.cheribsdRootfs  # directory tree to use for the image
            ])


class LaunchQEMU(Project):
    def __init__(self, paths):
        super().__init__("run", paths)

    def process(self):
        qemuBinary = self.paths.hostToolsDir / "bin/qemu-system-cheri"
        currentKernel = self.paths.cheribsdRootfs / "boot/kernel/kernel"
        print("About to run QEMU with image " + self.paths.diskImage.path + " and kernel " + currentKernel.path)
        # input("Press enter to continue")
        runCmd([qemuBinary, "-M", "malta",  # malta cpu
                "-kernel", currentKernel,  # assume the current image matches the kernel currently build
                "-nographic",  # no GPU
                "-m", "2048",  # 2GB memory
                "-hda", self.paths.diskImage
                ], stdout=sys.stdout)  # even with --quiet we want stdout here


def defaultNumberOfMakeJobs():
    makeJobs = os.cpu_count()
    if makeJobs > 24:
        # don't use up all the resources on shared build systems (you can still override this with the -j command line option)
        makeJobs = 16
    return makeJobs

if __name__ == "__main__":
    parser = argparse.ArgumentParser(formatter_class=lambda prog:
                                     argparse.HelpFormatter(prog, width=shutil.get_terminal_size()[0]))
    parser.add_argument("--make-jobs", "-j", type=int, default=defaultNumberOfMakeJobs(),
                        help="Number of jobs to use for compiling (default: %d)" % defaultNumberOfMakeJobs())
    parser.add_argument("--clean", action="store_true", help="Remove the build directory before build")
    parser.add_argument("--pretend", "-p", action="store_true",
                        help="Print the commands that would be run instead of executing them")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Don't show stdout of the commands that are executed")
    parser.add_argument("--list-targets", action="store_true", help="List all available targets and exit")
    parser.add_argument("--skip-update", action="store_true", help="Skip the git pull step")
    parser.add_argument("--skip-configure", action="store_true", help="Skip the configure step")
    parser.add_argument("--source-root",
                        help="The directory to store all sources (default: '%s')" % DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-root",
                        help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
    parser.add_argument("--disk-image-path",
                        help="The output path for the QEMU disk image (default: '<OUTPUT_ROOT>/disk.img')")
    parser.add_argument("targets", metavar="TARGET", type=str, nargs="*", help="The targets to build", default=["all"])
    options = parser.parse_args()
    paths = CheriPaths(options)

    # NOTE: This list must be in the right dependency order
    allTargets = [
        BuildBinutils(paths),
        BuildQEMU(paths),
        BuildLLVM(paths),
        BuildCHERIBSD(paths),
        BuildDiskImage(paths),
        LaunchQEMU(paths),
    ]
    allTargetNames = [t.name for t in allTargets]
    selectedTargets = options.targets
    if "all" in options.targets:
        selectedTargets = allTargetNames
    # make sure all targets passed on commandline exist
    invalidTargets = set(selectedTargets) - set(allTargetNames)
    if invalidTargets or options.list_targets:
        for t in invalidTargets:
            print("Invalid target", t)
        print("The following targets exist:", list(allTargetNames))
        print("target 'all' can be used to build everything")
        sys.exit()

    makeJFlag = "-j" + str(options.make_jobs)

    for target in allTargets:
        if target.name in selectedTargets:
            target.process()
