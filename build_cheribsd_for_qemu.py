#!/usr/bin/env python3
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
# import sh

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri

def runCmd(*args, **kwargs):
    if type(args[0]) is str:
        cmdline = args # multiple strings passed
    else:
        cmdline = args[0] # list was passed
    if options.pretend:
        # quotes according to msvc rules but should be fine
        print("executing:", " ".join([shlex.quote(i) for i in cmdline]))
        if not kwargs:
            kwargs["cwd"] = os.getcwd()
        print("  workdir:", kwargs["cwd"])

    else:
        subprocess.check_call(cmdline, **kwargs)


# removes a directory tree if --clean is passed (or force=True parameter is passed
def cleanDir(path, force=False, silent=False):
    if (options.clean or force) and os.path.isdir(path):
        if not options.pretend:
            # status update is useful as this can take a long time
            # when pretending this just spams the output
            print("Cleaning", path, "...")
        # http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
        # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
        runCmd(["rm", "-rf", path])
        os.makedirs(path, exist_ok=True)
    # always make sure the dir exists
    os.makedirs(path, exist_ok=True)


def fatalError(message: str):
    # we ignore fatal errors when simulating a run
    if options.pretend:
        print("Potential fatal error:", message)
    else:
        sys.exit(message)


class Project(object):
    def __init__(self, srcDir: str, buildDir: str, installDir: str):
        self.srcDir = srcDir
        self.buildDir = buildDir
        self.installDir = installDir
        self.makeCommand = "make"
        self.configureCommand = None
        self.configureArgs = []

    def update(self):
        runCmd("git", "-C", self.srcDir, "pull", "--rebase", cwd=self.srcDir)

    def clean(self):
        # TODO: never use the source dir as a build dir
        # will have to check how well binutils and qemu work there
        if os.path.isdir(os.path.join(self.buildDir, ".git")):
            # just use git clean for cleanup
            runCmd("git", "-C", self.buildDir, "clean", "-dfx", cwd=self.buildDir)
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
        os.makedirs(self.buildDir, exist_ok=True)
        if not options.skip_configure:
            self.configure()
        self.compile()
        self.install()


class BuildQEMU(Project):
    def __init__(self, srcDir, buildDir, installDir):
        super().__init__(srcDir, buildDir, installDir)
        # QEMU will not work with BSD make, need GNU make
        self.makeCommand = "gmake"
        self.configureCommand = os.path.join(self.srcDir, "configure")
        self.configureArgs = ["--target-list=cheri-softmmu",
                              "--disable-linux-user",
                              "--disable-linux-aio",
                              "--disable-kvm",
                              "--disable-xen",
                              "--extra-cflags=-g",
                              "--prefix=" + self.installDir]

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        runCmd("git", "checkout", "HEAD", "po/", cwd=self.srcDir)
        super().update()


class BuildBinutils(Project):
    def __init__(self, srcDir, buildDir, installDir):
        super().__init__(srcDir, buildDir, installDir)
        self.configureCommand = os.path.join(self.srcDir, "configure")
        self.configureArgs = ["--target=mips64", "--disable-werror", "--prefix=" + self.installDir]


class BuildLLVM(Project):
    def __init__(self, srcDir, buildDir, installDir):
        super().__init__(srcDir, buildDir, installDir)
        self.makeCommand = "ninja"

    def update(self):
        super().update()
        runCmd(["git", "-C", os.path.join(self.srcDir, "tools/clang"), "pull", "--rebase"])

    def configure(self):
        # we can only set configureArgs here as paths.cheribsd does not exist when the constructor runs
        # FIXME: what is the correct default sysroot
        # should expand to ~/cheri/qemu/obj/mips.mips64/home/alr48/cheri/cheribsd
        # I think this might be correct: it contains x86 binaries but mips libraries so should be right)
        sysroot = paths.cheribsd.buildDir + "/mips.mips64" + paths.cheribsd.srcDir + "/tmp"
        self.configureCommand = "cmake"
        self.configureArgs = [
            self.srcDir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_CXX_COMPILER=clang++37", "-DCMAKE_C_COMPILER=clang37",  # need at least 3.7 to build it
            "-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd",
            "-DCMAKE_INSTALL_PREFIX=" + self.installDir,
            "-DDEFAULT_SYSROOT=" + sysroot,
        ]
        super().configure()

    def install(self):
        # runCmd(["ninja", "install"])
        # we don't actually install yet (TODO: would it make sense to do that?)
        # delete the files incompatible with cheribsd
        os.chdir(self.buildDir)  # TODO: if we decide to install change this to self.installDir
        incompatibleFiles = glob.glob("lib/clang/3.*/include/std*") + glob.glob("lib/clang/3.*/include/limits.h")
        if len(incompatibleFiles) == 0:
            fatalError("Could not find incompatible builtin includes. Build system changed?")
        for i in incompatibleFiles:
            print("removing incompatible header", i)
            if not options.pretend:
                os.remove(i)


class BuildCHERIBSD(Project):
    def __init__(self, srcDir, buildDir, installDir):
        super().__init__(srcDir, buildDir, installDir)
        # make sure the new binutils are picked up

    def compile(self):
        os.environ["MAKEOBJDIRPREFIX"] = paths.cheribsd.buildDir
        if not os.environ["PATH"].startswith(paths.binutils.installDir):
            os.environ["PATH"] = os.path.join(paths.binutils.installDir, "bin") + ":" + os.environ["PATH"]
            print("Set PATH to", os.environ["PATH"])
        cheriCC = os.path.join(paths.llvm.buildDir, "bin/clang")
        if not os.path.isfile(cheriCC):
            fatalError("CHERI CC does not exist: " + cheriCC)
        self.commonMakeArgs = [
            "make", "CHERI=256", "CHERI_CC=" + cheriCC,
            # "CPUTYPE=mips64", # mipsfpu for hardware float (apparently no longer supported: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_ROOT",  # -DNO_ROOT install without using root privilege
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "DESTDIR=" + self.installDir,
            "KERNCONF=CHERI_MALTA64",
            # "-DNO_CLEAN", # don't clean before (takes ages) and the rm -rf we do before should be enough
        ]
        # make sure the old install is purged before building, otherwise we might get strange errors
        # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
        cleanDir(self.installDir, force=True)
        runCmd(self.commonMakeArgs + ["buildworld", makeJFlag], cwd=self.srcDir)
        runCmd(self.commonMakeArgs + ["buildkernel", makeJFlag], cwd=self.srcDir)

    def install(self):
        # don't use multiple jobs here
        runCmd(self.commonMakeArgs + ["installworld"], cwd=self.srcDir)
        runCmd(self.commonMakeArgs + ["installkernel"], cwd=self.srcDir)
        runCmd(self.commonMakeArgs + ["distribution"], cwd=self.srcDir)
        # TODO: make this configurable to allow NFS, etc.
        fstabContents = "/dev/ada0 / ufs rw 1 1\n"
        fstabPath = os.path.join(paths.cheribsd.installDir, "etc/fstab")

        if options.pretend:
            print("executing: echo", shlex.quote(fstabContents.replace("\n", "\\n")), ">", shlex.quote(fstabPath))
        else:
            with open(fstabPath, "w") as fstab:
                fstab.write(fstabContents)  # TODO: NFS?


def buildQEMUImage():
    if os.path.exists(paths.diskImagePath):
        yn = input("An image already exists (" + paths.diskImagePath + "). Overwrite? [y/N] ")
        if str(yn).lower() == "y":
            os.remove(paths.diskImagePath)
        else:
            return

    # make use of the mtree file created by make installworld
    # this means we can create a disk image without root privilege
    manifestFile = os.path.join(paths.cheribsd.installDir, "METALOG");
    if not os.path.isfile(manifestFile):
        fatalError("mtree manifest " + manifestFile + " is missing")
    userGroupDbDir = os.path.join(paths.cheribsd.srcDir, "etc")
    if not os.path.isfile(os.path.join(userGroupDbDir, "master.passwd")):
        fatalError("master.passwd does not exist in " + userGroupDbDir)

    def patchManifestFile(tmpdir, originalManifestFile):
        # for now we need to patch the METALOG FILE:
        manifestFile = os.path.join(tmpdir, "METALOG")
        if not options.pretend:
            shutil.copyfile(originalManifestFile, manifestFile)
        print("Patching METALOG", manifestFile)
        with open(os.path.join(tmpdir, "METALOG.patch"), "wb") as inputFile:
            inputFile.write(b"""
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
 ./usr/include/cheri//helloworld.h type=file uname=root gname=wheel mode=0444 size=2370
 ./usr/lib//libhelloworld_p.a type=file uname=root gname=wheel mode=0444 size=6598
 ./usr/libcheri/helloworld.co.0 type=file uname=root gname=wheel mode=0555 size=90032
-./usr/libcheri/.debug/ type=dir mode=0755 tags=debug
 ./usr/libcheri/.debug/helloworld.co.0.debug type=file uname=root gname=wheel mode=0444 size=48928 tags=debug
 ./usr/libcheri//helloworld.co.0.dump type=file uname=root gname=wheel mode=0444 size=1013571
 ./usr/lib//librpcsec_gss.a type=file uname=root gname=wheel mode=0444 size=53040
""")
            inputFile.flush()
            runCmd("patch", "-u", "-p1", "-i", inputFile.name)
            print("Sucessfully patched METALOG")

    with tempfile.TemporaryDirectory() as tmpdir:
        patchManifestFile(tmpdir, manifestFile)
        # input("about to run makefs on " + manifestFile + ". continue?")
        runCmd(["makefs",
            "-M", "1077936128", # minimum image size = 1GB
            "-B", "be", # big endian byte order
            "-F", manifestFile, # use METALOG as the manifest for the disk image
            "-N", userGroupDbDir, # use master.passwd from the cheribsd source not the current systems passwd file (makes sure that the numeric UID values are correct
            paths.diskImagePath, # output file
            paths.cheribsd.installDir # directory tree to use for the image
            ])
    
    #if False:
        ## no longer needed
        
        ## as we don't have root access we first have to make an mtree specification of the disk image
        ## where we replace uid=... and gid=... with the root uid
        ## and then we can run makefs with the mtree spec as input which will create a valid disk image
        ## that has the files correctly marked as owned by root
        ## FIXME: is this correct or do some files need a different owner?
        #with tempfile.NamedTemporaryFile() as manifest:
            #print("Creating disk image manifest file", manifest.name, "...")
            #runCmd(["mtree", "-c", "-p", paths.cheribsd.installDir, "-K", "uid,gid"], stdout=manifest)
            ## replace all uid=1234 with uid=0 and same for gid
            #runCmd(["sed", "-i", "-e", "s/uid\=[[:digit:]]*/uid=0/g", manifest.name])
            #runCmd(["sed", "-i", "-e", "s/gid\=[[:digit:]]*/gid=0/g", manifest.name])
            ## makefs -M 1077936128 -B be -F ../root.mtree "qemu/disk.img"
            #runCmd(["makefs", "-M", "1077936128", "-B", "be", "-F", manifest.name, diskImagePath, paths.cheribsd.installDir])
            #print("QEMU disk image", diskImagePath, "successfully created!")


def runQEMU():
    qemuBinary = os.path.join(paths.qemu.installDir, "bin/qemu-system-cheri")
    currentKernel = os.path.join(paths.cheribsd.installDir, "boot/kernel/kernel")
    print("About to run QEMU with image " + paths.diskImagePath + " and kernel " + currentKernel)
    #input("Press enter to continue")
    runCmd([qemuBinary,
                    "-M", "malta", # malta cpu
                    "-kernel", currentKernel , # assume the current image matches the kernel currently build
                    "-nographic", # no GPU
                    "-m", "2048", # 2GB memory
                    "-hda", paths.diskImagePath
                    ])


class Paths(object):
    def __init__(self, cmdlineArgs: argparse.Namespace):
        self.cheriRoot = os.path.expanduser("~/cheri")  # change this if you want it somewhere else
        self.outputDir = os.path.join(self.cheriRoot, "output")
        self.rootfsPath = os.path.join(self.outputDir, "rootfs")
        self.diskImagePath = options.disk_image_path
        if not self.diskImagePath:
            self.diskImagePath = os.path.join(self.outputDir, "disk.img")

        self.hostToolsInstallDir = os.path.join(self.outputDir, "host-tools")  # qemu and binutils (and llvm/clang)

        self.binutils = BuildBinutils(srcDir=os.path.join(self.cheriRoot, "binutils"),
                                      buildDir=os.path.join(self.outputDir, "binutils-build"),
                                      installDir=self.hostToolsInstallDir)
        self.qemu = BuildQEMU(srcDir=os.path.join(self.cheriRoot, "qemu"),
                              buildDir=os.path.join(self.outputDir, "qemu-build"),
                              installDir=self.hostToolsInstallDir)
        self.llvm = BuildLLVM(srcDir=os.path.join(self.cheriRoot, "llvm"),
                              buildDir=os.path.join(self.outputDir, "llvm-build"),
                              installDir=self.hostToolsInstallDir)
        self.cheribsd = BuildCHERIBSD(srcDir=os.path.join(self.cheriRoot, "cheribsd"),
                                      buildDir=os.path.join(self.outputDir, "cheribsd-obj"),
                                      installDir=self.rootfsPath)


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

    paths = Paths(options)
    numCpus = multiprocessing.cpu_count()
    if numCpus > 24:
        # don't use up all the resources on shared build systems (you can still override this with the -j command line option)
        numCpus = 16
    makeJFlag = "-j" + str(options.make_jobs) if options.make_jobs else "-j" + str(numCpus)

    if allTargets[0] in targets:
        paths.qemu.process()
    if allTargets[1] in targets:
        paths.binutils.process()
    if allTargets[2] in targets:
        paths.llvm.process()
    if allTargets[3] in targets:
        paths.cheribsd.process()
    if allTargets[4] in targets:
        buildQEMUImage()
    if allTargets[5] in targets:
        runQEMU()
    