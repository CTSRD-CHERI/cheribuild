#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import logging
import shutil
import tempfile
import multiprocessing
import collections
import glob
# import sh

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri

class Project(object):
    def __init__(self, srcDir: str, buildDir: str, installDir: str):
        self.srcDir = srcDir
        self.buildDir = buildDir
        self.installDir = installDir


class Paths(object):
    def __init__(self, cmdlineArgs: argparse.Namespace):
        self.cheriRoot = os.path.expanduser("~/cheri") # change this if you want it somewhere else
        self.outputDir = os.path.join(self.cheriRoot, "output")
        self.rootfs = os.path.join(self.outputDir, "rootfs")
        self.diskImagePath = options.disk_image_path
        if not self.diskImagePath:
            self.diskImagePath = os.path.join(self.outputDir, "disk.img")

        self.hostToolsInstallDir = os.path.join(self.outputDir, "host-tools") # qemu and binutils (and llvm/clang)

        self.binutils = Project(srcDir = os.path.join(self.cheriRoot, "binutils"),
                            buildDir = os.path.join(self.outputDir, "binutils-build"),
                            installDir = self.hostToolsInstallDir)
        self.qemu = Project(srcDir = os.path.join(self.cheriRoot, "qemu"),
                            buildDir = os.path.join(self.outputDir, "qemu-build"),
                            installDir = self.hostToolsInstallDir)
        self.llvm = Project(srcDir = os.path.join(self.cheriRoot, "llvm"),
                            buildDir = os.path.join(self.outputDir, "llvm-build"),
                            installDir = self.hostToolsInstallDir)
        self.clang = Project(srcDir = os.path.join(self.llvm.srcDir, "tools/clang"),
                            buildDir = os.path.join(self.llvm.buildDir, "tools/clang"), # not needed as subproject of llvm
                            installDir = self.hostToolsInstallDir) # also not needed
        self.cheribsd = Project(srcDir = os.path.join(self.cheriRoot, "cheribsd"),
                            buildDir = os.path.join(self.outputDir, "cheribsd-obj"),
                            installDir = self.rootfs)


# removes a directory tree if --clean is passed (or force=True parameter is passed
def cleanDir(path, force=False, silent=False):
    if not options.clean and not force:
        return
    if os.path.isdir(path):
        print("Cleaning", path, "...", end="", flush=True)
        #http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
        # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
        subprocess.check_call(["rm", "-rf", path])
        os.makedirs(path, exist_ok=False)
        print(" done.")
    # always make sure the dir exists
    os.makedirs(path, exist_ok=True)


def buildQEMU():
    os.chdir(paths.qemu.srcDir)
    if not options.skip_update:
        subprocess.check_call(["git", "pull", "--rebase"])
    if options.clean:
        subprocess.check_call(["git", "clean", "-dfx"])
    if not options.skip_configure:
        subprocess.check_call(["./configure",
                               "--target-list=cheri-softmmu",
                               "--disable-linux-user",
                               "--disable-linux-aio",
                               "--disable-kvm",
                               "--disable-xen",
                               "--extra-cflags=-g",
                               "--prefix=" + paths.qemu.installDir])
    subprocess.check_call(["gmake", makeJFlag])
    subprocess.check_call(["gmake", "install"])


def buildBinUtils():
    binutilsDir = os.path.join(cheriDir, "binutils")
    if not options.skip_update:
        subprocess.check_call(["git", "-C", binutilsDir, "pull", "--rebase"])
    if options.clean:
        subprocess.check_call(["git", "-C", binutilsDir, "clean", "-dfx"])
    os.chdir(binutilsDir)
    if not options.skip_configure:
        subprocess.check_call(["./configure", "--target=mips64", "--disable-werror", "--prefix=" + paths.binutils.installDir])

    subprocess.check_call(["make", makeJFlag])
    subprocess.check_call(["make", "install"])


def buildLLVM():
    if not options.skip_update:
        subprocess.check_call(["git", "-C", paths.llvm.srcDir, "pull", "--rebase"])
        subprocess.check_call(["git", "-C", paths.clang.srcDir, "pull", "--rebase"])
    cleanDir(paths.llvm.buildDir)
    os.chdir(paths.llvm.buildDir)
    if not options.skip_configure:
        subprocess.check_call(["cmake", "-G", "Ninja",
                "-DCMAKE_CXX_COMPILER=clang++37", "-DCMAKE_C_COMPILER=clang37", # need at least 3.7 to build it
                "-DCMAKE_BUILD_TYPE=Release",
                "-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd",
                # not sure if the following is needed, I just copied them from the build_sdk script
                "-DCMAKE_INSTALL_PREFIX=" + paths.llvm.installDir,
                # "-DDEFAULT_SYSROOT=" + paths.cheribsd.buildDir, # FIXME: what is the correct value here?
                # should expand to ~/cheri/qemu/obj/mips.mips64/home/alr48/cheri/cheribsd (I think this is correct: it contains x86 binaries but mips libraries so should be right)
                "-DDEFAULT_SYSROOT=" + paths.cheribsd.buildDir + "/mips.mips64" + paths.cheribsd.srcDir,

                paths.llvm.srcDir])
    if options.make_jobs:
        subprocess.check_call(["ninja", "-j" + str(options.make_jobs)])
    else:
        subprocess.check_call(["ninja"])
    # subprocess.check_call(["ninja", "install"])
    # delete the files incompatible with cheribsd
    # incompatibleFiles = glob.glob(hostToolsInstallDir + "/lib/clang/3.*/include/std*") + glob.glob(hostToolsInstallDir + "/lib/clang/3.*/include/limits.h")
    incompatibleFiles = glob.glob("lib/clang/3.*/include/std*") + glob.glob("lib/clang/3.*/include/limits.h")
    if len(incompatibleFiles) == 0:
        sys.exit("Could not find incompatible builtin includes. Build system changed?")
    for i in incompatibleFiles:
        print("removing incompatible header", i)
        os.remove(i)


def buildCHERIBSD():
    os.chdir(paths.cheribsd.srcDir)
    if not options.skip_update:
        subprocess.check_call(["git", "-C", paths.cheribsd.srcDir, "pull", "--rebase"])
    #cheriCC = os.path.join(paths.llvm.installDir, "bin/clang")
    cheriCC = os.path.join(paths.llvm.buildDir, "bin/clang")
    if not os.path.isfile(cheriCC):
        sys.exit("CHERI CC does not exist: " + cheriCC)
    os.environ["MAKEOBJDIRPREFIX"] = paths.cheribsd.buildDir
    
    cleanDir(paths.cheribsd.buildDir)
    # make sure the old install is purged before building, otherwise we get strange errors
    # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
    cleanDir(paths.rootfsDir, force=True)
    makeCmd = ["make",
               "CHERI=256",
               # "CC=/usr/local/bin/clang37",
               "CHERI_CC=" + cheriCC,
               # "CPUTYPE=mips64", # mipsfpu for hardware float (apparently not needed: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
               #"TARGET=mips",
               #"TARGET_ARCH=mips64",
               #"TARGET_CPUTYPE=mips", # mipsfpu for hardware float
               "-DDB_FROM_SRC",
               "-DNO_ROOT", # -DNO_ROOT install without using root privilege
               "-DNO_CLEAN", # don't clean before (takes ages) and the rm -rf we do before should be enough
               #"CFLAGS=-Wno-error=capabilities",
               # "CFLAGS=-nostdinc",
               "-DNO_WERROR",
               makeJFlag
               ]
    subprocess.check_call(makeCmd + ["buildworld"])
    subprocess.check_call(makeCmd + ["KERNCONF=CHERI_MALTA64", "buildkernel"])
    subprocess.check_call(makeCmd + ["installworld", "DESTDIR=" + paths.rootfsDir])
    subprocess.check_call(makeCmd + ["KERNCONF=CHERI_MALTA64", "installkernel", "DESTDIR=" + paths.rootfsDir])
    subprocess.check_call(makeCmd + ["distribution", "DESTDIR=" + paths.rootfsDir])
    with open(os.path.join(paths.rootfsDir, "etc/fstab"), "w") as fstab:
        fstab.write("/dev/ada0 / ufs rw 1 1\n") # TODO: NFS?


def buildQEMUImage():
    if os.path.exists(paths.diskImagePath):
        yn = input("An image already exists (" + paths.diskImagePath + "). Overwrite? [y/N] ")
        if str(yn).lower() == "y":
            os.remove(paths.diskImagePath)
        else:
            return
    
    # make use of the mtree file created by make installworld
    # this means we can create a disk image without root privilege
    manifestFile = os.path.join(paths.rootfsDir, "METALOG");
    if not os.path.isfile(manifestFile):
        sys.exit("mtree manifest " + manifestFile + " is missing")
    userGroupDbDir = os.path.join(cheriDir, "cheribsd/etc")
    if not os.path.isfile(os.path.join(userGroupDbDir, "master.passwd")):
        sys.exit("master.passwd does not exist in " + userGroupDbDir)

    # for now we need to patch the METALOG FILE:
    with tempfile.TemporaryDirectory() as tmpdir:
        originalManifestFile = manifestFile
        manifestFile = os.path.join(tmpdir, "METALOG")
        shutil.copyfile(originalManifestFile, manifestFile)
        print("Patching METALOG", manifestFile)
        patch = subprocess.Popen(["patch", "-u", "-p1"], stdin=subprocess.PIPE, cwd=tmpdir)    
        patch.communicate(input=b"""
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
        patch.stdin.close()
        patch.wait()
        print("Sucessfully patched METALOG")
        # input("about to run makefs on " + manifestFile + ". continue?")

        subprocess.check_call(["makefs",
            "-M", "1077936128", # minimum image size = 1GB
            "-B", "be", # big endian byte order
            "-F", manifestFile, # use METALOG as the manifest for the disk image
            "-N", userGroupDbDir, # use master.passwd from the cheribsd source not the current systems passwd file (makes sure that the numeric UID values are correct
            paths.diskImagePath, # output file
            paths.rootfsDir # directory tree to use for the image
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
            #subprocess.check_call(["mtree", "-c", "-p", rootfsDir, "-K", "uid,gid"], stdout=manifest)
            ## replace all uid=1234 with uid=0 and same for gid
            #subprocess.check_call(["sed", "-i", "-e", "s/uid\=[[:digit:]]*/uid=0/g", manifest.name])
            #subprocess.check_call(["sed", "-i", "-e", "s/gid\=[[:digit:]]*/gid=0/g", manifest.name])
            ## makefs -M 1077936128 -B be -F ../root.mtree "qemu/disk.img"
            #subprocess.check_call(["makefs", "-M", "1077936128", "-B", "be", "-F", manifest.name, diskImagePath, rootfsDir])
            #print("QEMU disk image", diskImagePath, "successfully created!")


def runQEMU():
    qemuBinary = os.path.join(paths.qemu.installDir, "bin/qemu-system-cheri")
    currentKernel = os.path.join(paths.rootfsDir, "boot/kernel/kernel")
    input("About to run QEMU with image " + paths.diskImagePath + " and kernel " + currentKernel + "\nPress enter to continue")
    subprocess.check_call([qemuBinary,
                    "-M", "malta", # malta cpu
                    "-kernel", currentKernel , # assume the current image matches the kernel currently build
                    "-nographic", # no GPU
                    "-m", "2048", # 2GB memory
                    "-hda", paths.diskImagePath
                    ])

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clone", action="store_true", help="Perform the initial clone of the repositories")
    parser.add_argument("--make-jobs", "-j", help="Number of jobs to use for compiling", type=int)
    parser.add_argument("--clean", action="store_true", help="Do a clean build")
    parser.add_argument("--list-targets", action="store_true", help="List all available targets")
    parser.add_argument("--skip-update", action="store_true", help="Skip the git pull step")
    parser.add_argument("--skip-configure", action="store_true", help="Don't run the configure step")
    #parser.add_argument("--skip-binutils", action="store_true", help="Don't build binutils")
    #parser.add_argument("--skip-llvm", action="store_true", help="Don't build LLVM")
    #parser.add_argument("--skip-cheribsd", action="store_true", help="Don't build CHERIBSD")
    #parser.add_argument("--disk-image", action="store_true", help="Build a disk image usable for QEMU")
    parser.add_argument("--disk-image-path", help="The disk image path (defaults to qemu/disk.img)")
    parser.add_argument("targets", metavar="TARGET", type=str, nargs="*", help="The targets to build", default=["all"])
    options = parser.parse_args()
    allTargets = ["qemu", "binutils", "llvm", "cheribsd", "disk-image", "run"]
    if options.list_targets:
        for i in allTargets:
            print(i)
        print("target 'all' can be used to build everything")
        sys.exit();
    
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
    makeJFlag = "-j" + str(options.make_jobs) if options.make_jobs else "-j" + str(multiprocessing.cpu_count())

    if allTargets[0] in targets:
        buildQEMU()
    if allTargets[1] in targets:
        buildBinUtils()
    if allTargets[2] in targets:
        buildLLVM()
    if allTargets[3] in targets:
        # make sure the new binutils are picked up
        #if not os.environ["PATH"].startswith(hostToolsInstallDir):
        #    os.environ["PATH"] = os.path.join(hostToolsInstallDir, "bin") + ":" + os.environ["PATH"]
        #    print("Set PATH to", os.environ["PATH"])
        buildCHERIBSD()
    if allTargets[4] in targets:
        buildQEMUImage()
    if allTargets[5] in targets:
        runQEMU()
    