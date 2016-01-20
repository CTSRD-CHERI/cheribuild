#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import logging
import shutil
import tempfile
import multiprocessing
from glob import glob
# import sh

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri

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
    global cheriDir
    qemuDir = os.path.join(cheriDir, "qemu-cheri")
    os.chdir(qemuDir)
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
                               "--prefix=" + hostToolsInstallDir])
    subprocess.check_call(["gmake", makeJFlag])
    subprocess.check_call(["gmake", "install"])


def buildBinUtils():
    global cheriDir
    binutilsDir = os.path.join(cheriDir, "binutils")
    if not options.skip_update:
        subprocess.check_call(["git", "-C", binutilsDir, "pull", "--rebase"])
    if options.clean:
        subprocess.check_call(["git", "-C", binutilsDir, "clean", "-dfx"])
    os.chdir(binutilsDir)
    if not options.skip_configure:
        subprocess.check_call(["./configure", "--target=mips64", "--disable-werror", "--prefix=" + hostToolsInstallDir])

    subprocess.check_call(["make", makeJFlag])
    subprocess.check_call(["make", "install"])


def buildLLVM():
    llvmDir = os.path.join(cheriDir, "llvm")
    if not options.skip_update:
        subprocess.check_call(["git", "-C", llvmDir, "pull", "--rebase"])
        subprocess.check_call(["git", "-C", os.path.join(llvmDir, "tools/clang"), "pull", "--rebase"])
    cleanDir(llvmBuildDir)
    os.chdir(llvmBuildDir)
    if not options.skip_configure:
        subprocess.check_call(["cmake", "-G", "Ninja",
                "-DCMAKE_CXX_COMPILER=clang++37", "-DCMAKE_C_COMPILER=clang37", # need at least 3.7 to build it
                "-DCMAKE_BUILD_TYPE=Release",
                "-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd",
                # not sure if the following is needed, I just copied them from the build_sdk script
                "-DCMAKE_INSTALL_PREFIX=" + hostToolsInstallDir,
                "-DDEFAULT_SYSROOT=" + os.path.join(cheriDir, "qemu/rootfs"),
                llvmDir])
    if options.make_jobs:
        subprocess.check_call(["ninja", "-j" + str(options.make_jobs)])
    else:
        subprocess.check_call(["ninja"])
    # subprocess.check_call(["ninja", "install"])
    # delete the files incompatible with cheribsd
    # incompatibleFiles = glob(hostToolsInstallDir + "/lib/clang/3.*/include/std*") + glob(hostToolsInstallDir + "/lib/clang/3.*/include/limits.h")
    incompatibleFiles = glob("lib/clang/3.*/include/std*") + glob("lib/clang/3.*/include/limits.h")
    if len(incompatibleFiles) == 0:
        sys.exit("Could not find incompatible builtin includes. Build system changed?")
    for i in incompatibleFiles:
        print("removing incompatible header", i)
        os.remove(i)


def buildCHERIBSD():
    cheribsdDir = os.path.join(cheriDir, "cheribsd")
    os.chdir(cheribsdDir)
    if not options.skip_update:
        subprocess.check_call(["git", "-C", cheribsdDir, "pull", "--rebase"])
    #cheriCC = os.path.join(hostToolsInstallDir, "bin/clang")
    cheriCC = os.path.join(llvmBuildDir, "bin/clang")
    if not os.path.isfile(cheriCC):
        sys.exit("CHERI CC does not exist: " + cheriCC)
    os.environ["MAKEOBJDIRPREFIX"] = os.path.join(cheriDir, "qemu/obj")
    
    cleanDir(os.path.join(cheriDir, "qemu/obj"))
    # make sure the old install is purged before building, otherwise we get strange errors
    # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
    cleanDir(rootfsDir, force=True)
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
    subprocess.check_call(makeCmd + ["installworld", "DESTDIR=" + rootfsDir])
    subprocess.check_call(makeCmd + ["KERNCONF=CHERI_MALTA64", "installkernel", "DESTDIR=" + rootfsDir])
    subprocess.check_call(makeCmd + ["distribution", "DESTDIR=" + rootfsDir])
    with open(os.path.join(rootfsDir, "etc/fstab"), "w") as fstab:
        fstab.write("/dev/ada0 / ufs rw 1 1\n") # TODO: NFS?


def buildQEMUImage():
    # sudo -E makefs -M 1077936128 -B be /var/tmp/disk.img /var/tmp/root
    diskImagePath = options.disk_image_path
    if not diskImagePath:
        diskImagePath = os.path.join(cheriDir, "qemu/disk.img")
    if os.path.exists(diskImagePath):
        yn = input("An image already exists (" + diskImagePath + "). Overwrite? [y/N]")
        if str(yn).lower() == "y":
            os.remove(diskImagePath)
    # as we don't have root access we first have to make an mtree specification of the disk image
    # where we replace uid=... and gid=... with the root uid
    # and then we can run makefs with the mtree spec as input which will create a valid disk image
    # that has the files correctly marked as owned by root
    # FIXME: is this correct or do some files need a different owner?
    with tempfile.NamedTemporaryFile() as manifest:
        print("Creating disk image manifest file", manifest.name, "...")
        subprocess.check_call(["mtree", "-c", "-p", rootfsDir, "-K", "uid,gid"], stdout=manifest)
        # replace all uid=1234 with uid=0 and same for gid
        subprocess.check_call(["sed", "-i", "-e", "s/uid\=[[:digit:]]*/uid=0/g", manifest.name])
        subprocess.check_call(["sed", "-i", "-e", "s/gid\=[[:digit:]]*/gid=0/g", manifest.name])
        # makefs -M 1077936128 -B be -F ../root.mtree "qemu/disk.img"
        subprocess.check_call(["makefs", "-M", "1077936128", "-B", "be", "-F", manifest.name, diskImagePath, rootfsDir])
        print("QEMU disk image", diskImagePath, "successfully created!")


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
    parser.add_argument("--disk-image", action="store_true", help="Build a disk image usable for QEMU")
    parser.add_argument("--disk-image-path", help="The disk image path (defaults to qemu/disk.img)")
    parser.add_argument("targets", metavar="TARGET", type=str, nargs="*", help="The targets to build", default=["all"])
    options = parser.parse_args()
    allTargets = ["qemu", "binutils", "llvm", "cheribsd", "disk-image"]
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

    logging.basicConfig(level=logging.INFO)
    cheriDir = os.path.expanduser("~/cheri")
    hostToolsInstallDir = os.path.join(cheriDir, "qemu/host-tools")
    llvmBuildDir = os.path.join(cheriDir, "qemu/llvm-build")
    rootfsDir = os.path.join(cheriDir, "qemu/rootfs")
    makeJFlag = "-j" + str(options.make_jobs) if options.make_jobs else "-j" + str(multiprocessing.cpu_count())

    os.chdir(cheriDir)
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
    