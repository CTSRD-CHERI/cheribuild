#!/usr/bin/env python3

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri


import argparse
import subprocess
import sys
import os
import logging
import shutil
import shlex
import tempfile
import multiprocessing
import collections
import glob
from pathlib import Path

if sys.version_info < (3, 4):
    sys.exit("This script requires at least Python 3.4")

# add the new 3.5.2 home() and .path to pathlib.Path
if sys.version_info < (3, 5, 2):
    print("Working around old version of pathlib")
    Path.path = property(lambda self: str(self))
if sys.version_info < (3, 5):
    Path.home = lambda: Path(os.path.expanduser("~"))
    def _write_text(self, data, encoding=None, errors=None):
        if not isinstance(data, str):
            raise TypeError('data must be str, not %s' % data.__class__.__name__)
        with self.open(mode='w', encoding=encoding, errors=errors) as f:
            return f.write(data)

    Path.write_text = _write_text



# if you want to customize where the sources/build output goes just change this
class CheriPaths(object):
    def __init__(self, cmdlineArgs: argparse.Namespace):
        self.sourceRoot = Path.home() / "cheri"
        self.outputRoot = self.sourceRoot / "output"
        self.diskImage = Path(options.disk_image_path) if options.disk_image_path else self.outputRoot / "disk.image"
        self.cheribsdRootfs = self.outputRoot / "rootfs"
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / "cheribsd-obj"
        self.hostToolsDir = self.outputRoot / "host-tools"  # qemu and binutils (and llvm/clang)


def runCmd(*args, **kwargs):
    if type(args[0]) is str or type(args[0]) is Path:
        cmdline = args  # multiple strings passed
    else:
        cmdline = args[0]  # list was passed
    cmdline = list(map(str, cmdline))  # make sure they are all strings
    colour = "\x1b[1;33m"  # bold yellow
    endColour = "\x1b[0m"  # reset
    cmdShellEscaped = " ".join([shlex.quote(str(i)) for i in cmdline])
    kwargs["cwd"] = str(kwargs["cwd"]) if "cwd" in kwargs else os.getcwd()
    print(colour, "cd ", shlex.quote(kwargs["cwd"]), " && ", cmdShellEscaped, endColour, sep="")
    if not options.pretend:
        print(cmdline, kwargs)
        subprocess.check_call(cmdline, **kwargs)


# removes a directory tree if --clean is passed (or force=True parameter is passed
def cleanDir(path: Path, force=False, silent=False):
    if (options.clean or force) and path.is_dir():
        # http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
        # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
        runCmd(["rm", "-rf", path])
    # always make sure the dir exists
    os.makedirs(path.path, exist_ok=True)


def fatalError(message: str):
    # we ignore fatal errors when simulating a run
    if options.pretend:
        print("Potential fatal error:", message)
    else:
        sys.exit(message)


class Project(object):
    def __init__(self, name: str, paths: CheriPaths, *, sourceDir="", buildDir="", installDir=""):
        self.paths = paths
        self.sourceDir = Path(sourceDir if sourceDir else paths.sourceRoot / name)
        self.buildDir = Path(buildDir if buildDir else paths.outputRoot / (name + "-build"))
        self.installDir = Path(installDir)
        self.makeCommand = "make"
        self.configureCommand = None
        self.configureArgs = []

    def update(self):
        runCmd("git", "pull", "--rebase", cwd=self.sourceDir)

    def clean(self):
        # TODO: never use the source dir as a build dir
        # will have to check how well binutils and qemu work there
        if (self.buildDir / ".git").is_dir():
            # just use git clean for cleanup
            runCmd("git", "clean", "-dfx", cwd=self.buildDir)
        else:
            cleanDir(self.buildDir)

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
        os.makedirs(self.buildDir.path, exist_ok=True)
        if not options.skip_configure:
            self.configure()
        self.compile()
        self.install()


class BuildQEMU(Project):
    def __init__(self, paths: CheriPaths):
        super().__init__("qemu", paths)
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
        # TODO: report CHERIBSD bug to remove the need for this patch
        self.metalogPatch = """
--- rootfs/METALOG      2016-01-20 09:51:47.704461046 +0000
+++ rootfs2/METALOG       2016-01-20 11:51:59.831964687 +0000
@@ -121,6 +121,7 @@
 ./usr/lib/libxo type=dir uname=root gname=wheel mode=0755
 ./usr/lib/libxo/encoder type=dir uname=root gname=wheel mode=0755
 ./usr/libcheri type=dir uname=root gname=wheel mode=0755
+./usr/libcheri/.debug type=dir uname=root gname=wheel mode=0755 tags=debug
 ./usr/libdata type=dir uname=root gname=wheel mode=0755
 ./usr/libdata/gcc type=dir uname=root gname=wheel mode=0755
 ./usr/libdata/ldscripts type=dir uname=root gname=wheel mode=0755
@@ -4434,7 +4435,6 @@
 ./usr/lib//libhelloworld_p.a type=file uname=root gname=wheel mode=0444 size=6614
 ./usr/include/cheri//helloworld.h type=file uname=root gname=wheel mode=0444 size=2370
 ./usr/libcheri/helloworld.co.0 type=file uname=root gname=wheel mode=0555 size=90000
-./usr/libcheri/.debug/ type=dir mode=0755 tags=debug
 ./usr/libcheri/.debug/helloworld.co.0.debug type=file uname=root gname=wheel mode=0444 size=49144 tags=debug
 ./usr/libcheri//helloworld.co.0.dump type=file uname=root gname=wheel mode=0444 size=1014824
 ./usr/lib//librpcsec_gss.a type=file uname=root gname=wheel mode=0444 size=53040
"""

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir)
        super().update()

    def buildDiskImage(self):
        if self.paths.diskImage.is_file():
            #yn = input("An image already exists (" + self.paths.diskImage.path + "). Overwrite? [y/N] ")
            #if str(yn).lower() != "y":
                #return
            self.paths.diskImage.unlink()
        # make use of the mtree file created by make installworld
        # this means we can create a disk image without root privilege
        manifestFile = self.paths.cheribsdRootfs / "METALOG"
        if not manifestFile.is_file():
            fatalError("mtree manifest " + manifestFile + " is missing")
        userGroupDbDir = self.paths.cheribsdSources / "etc"
        if not (userGroupDbDir / "master.passwd").is_file():
            fatalError("master.passwd does not exist in " + userGroupDbDir)
        # for now we need to patch the METALOG FILE:
        with tempfile.TemporaryDirectory() as tmpdir:
            patchedManifestFile = Path(tmpdir, "METALOG")
            if not options.pretend:
                shutil.copyfile(manifestFile, patchedManifestFile)
            print("Patching METALOG", manifestFile)
            inputFile = Path(tmpdir, "METALOG.patch")
            inputFile.write_text(self.metalogPatch)
            runCmd("patch", "-u", "-p1", "-i", inputFile, cwd=tmpdir)
            print("Sucessfully patched METALOG")
            # input("about to run makefs on " + patchedManifestFile + ". continue?")
            runCmd([
                "makefs",
                "-M", "1077936128",  # minimum image size = 1GB
                "-B", "be",  # big endian byte order
                "-F", patchedManifestFile,  # use METALOG as the manifest for the disk image
                "-N", userGroupDbDir,  # use master.passwd from the cheribsd source not the current systems passwd file (makes sure that the numeric UID values are correct
                self.paths.diskImage,  # output file
                self.paths.cheribsdRootfs  # directory tree to use for the image
            ])

    def startEmulator(self):
        qemuBinary = self.paths.hostToolsDir / "bin/qemu-system-cheri"
        currentKernel = self.paths.cheribsdRootfs / "boot/kernel/kernel"
        print("About to run QEMU with image " + self.paths.diskImage.path + " and kernel " + currentKernel.path)
        # input("Press enter to continue")
        runCmd([qemuBinary, "-M", "malta",  # malta cpu
                "-kernel", currentKernel ,  # assume the current image matches the kernel currently build
                "-nographic",  # no GPU
                "-m", "2048",  # 2GB memory
                "-hda", self.paths.diskImage
                ])


class BuildBinutils(Project):
    def __init__(self, paths: CheriPaths):
        super().__init__("binutils", paths, installDir=paths.hostToolsDir)
        self.configureCommand = self.sourceDir / "configure"
        self.configureArgs = ["--target=mips64", "--disable-werror", "--prefix=" + self.installDir.path]


class BuildLLVM(Project):
    def __init__(self, paths: CheriPaths):
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
        super().update()
        runCmd(["git", "pull", "--rebase"], cwd=(self.sourceDir / "tools/clang"))

    def install(self):
        # runCmd(["ninja", "install"])
        # we don't actually install yet (TODO: would it make sense to do that?)
        # delete the files incompatible with cheribsd
        incompatibleFiles = list(self.buildDir.glob("lib/clang/3.*/include/std*"))
        incompatibleFiles += self.buildDir.glob("lib/clang/3.*/include/limits.h")
        if len(incompatibleFiles) == 0:
            fatalError("Could not find incompatible builtin includes. Build system changed?")
        for i in incompatibleFiles:
            print("removing incompatible header", i)
            if not options.pretend:
                i.unlink()


class BuildCHERIBSD(Project):
    def __init__(self, paths: CheriPaths):
        super().__init__("cheribsd", paths, buildDir=paths.cheribsdObj, installDir=paths.cheribsdRootfs)

    def compile(self):
        os.environ["MAKEOBJDIRPREFIX"] = self.buildDir.path
        # make sure the new binutils are picked up
        if not os.environ["PATH"].startswith(self.paths.hostToolsDir.path):
            os.environ["PATH"] = (self.paths.hostToolsDir / "bin").path + ":" + os.environ["PATH"]
            print("Set PATH to", os.environ["PATH"])
        cheriCC = self.paths.outputRoot / "llvm-build/bin/clang"  # FIXME: see if it works with installing
        if not cheriCC.is_file():
            fatalError("CHERI CC does not exist: " + cheriCC)
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
        cleanDir(self.installDir, force=True)
        runCmd(self.commonMakeArgs + ["buildworld", makeJFlag], cwd=self.sourceDir)
        runCmd(self.commonMakeArgs + ["buildkernel", makeJFlag], cwd=self.sourceDir)

    def install(self):
        # don't use multiple jobs here
        runCmd(self.commonMakeArgs + ["installworld"], cwd=self.sourceDir)
        runCmd(self.commonMakeArgs + ["installkernel"], cwd=self.sourceDir)
        runCmd(self.commonMakeArgs + ["distribution"], cwd=self.sourceDir)
        # TODO: make this configurable to allow NFS, etc.
        fstabContents = "/dev/ada0 / ufs rw 1 1\n"
        fstabPath = self.paths.cheribsdRootfs / "etc/fstab"

        if options.pretend:
            print("executing: echo", shlex.quote(fstabContents.replace("\n", "\\n")), ">", shlex.quote(fstabPath.path))
        else:
            fstabPath.write_text(fstabContents)  # TODO: NFS?


class Targets(object):
    def __init__(self, paths: CheriPaths):
        self.binutils = BuildBinutils(paths)
        self.qemu = BuildQEMU(paths)
        self.llvm = BuildLLVM(paths)
        self.cheribsd = BuildCHERIBSD(paths)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clone", action="store_true", help="Perform the initial clone of the repositories")
    parser.add_argument("--make-jobs", "-j", help="Number of jobs to use for compiling", type=int)
    parser.add_argument("--clean", action="store_true", help="Do a clean build")
    parser.add_argument("--pretend", "-p", action="store_true", help="Print the commands that would be run instead of executing them")
    parser.add_argument("--list-targets", action="store_true", help="List all available targets")
    parser.add_argument("--skip-update", action="store_true", help="Skip the git pull step")
    parser.add_argument("--skip-configure", action="store_true", help="Don't run the configure step")
    parser.add_argument("--disk-image-path", help="The disk image path (defaults to qemu/disk.img)")
    parser.add_argument("targets", metavar="TARGET", type=str, nargs="*", help="The targets to build", default=["all"])
    options = parser.parse_args()
    # print(options)
    allTargets = ["qemu", "binutils", "llvm", "cheribsd", "disk-image", "run"]
    if options.list_targets:
        for i in allTargets:
            print(i)
        print("target 'all' can be used to build everything")
        sys.exit()

    if "all" in options.targets:
        print("Building all targets")
        targets = allTargets
    else:
        targets = [x.lower() for x in options.targets]
    # print(options)
    for i in targets:
        if i not in allTargets:
            sys.exit("Unknown target " + i + " see --list-targets")

    newPaths = CheriPaths(options)
    buildTargets = Targets(newPaths)
    numCpus = multiprocessing.cpu_count()
    if numCpus > 24:
        # don't use up all the resources on shared build systems (you can still override this with the -j command line option)
        numCpus = 16
    makeJFlag = "-j" + str(options.make_jobs) if options.make_jobs else "-j" + str(numCpus)

    if allTargets[0] in targets:
        buildTargets.qemu.process()
    if allTargets[1] in targets:
        buildTargets.binutils.process()
    if allTargets[2] in targets:
        buildTargets.llvm.process()
    if allTargets[3] in targets:
        buildTargets.cheribsd.process()
    if allTargets[4] in targets:
        buildTargets.qemu.buildDiskImage()
    if allTargets[5] in targets:
        buildTargets.qemu.startEmulator()
