#!/usr/bin/env python3
import argparse
import subprocess
import sys
import os
import logging
import shutil
import tempfile
from glob import glob
# import sh

# See https://ctsrd-trac.cl.cam.ac.uk/projects/cheri/wiki/QemuCheri

def buildLLVM():
    global cheriDir
    llvmDir = os.path.join(cheriDir, "llvm")
    if not options.skip_update:
        subprocess.check_call(["git", "-C", llvmDir, "pull", "--rebase"])
        subprocess.check_call(["git", "-C", os.path.join(llvmDir, "tools/clang"), "pull", "--rebase"])
    llvmBuildDir = os.path.join(cheriDir, "qemu/llvm-build")
    if options.clean:
        shutil.rmtree(llvmBuildDir)
    os.makedirs(llvmBuildDir, exist_ok=True)
    os.chdir(llvmBuildDir)
    if not options.skip_cmake:
        subprocess.check_call(["cmake", "-G", "Ninja",
                "-DCMAKE_CXX_COMPILER=clang++37", "-DCMAKE_C_COMPILER=clang37", # need at least 3.7 to build it
                "-DCMAKE_BUILD_TYPE=Release",
                "-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd",
                # not sure if the following are needed, I just copied them from the build_sdk script
                "-DCMAKE_INSTALL_PREFIX=" + os.path.join(cheriDir, "sdk"),
                "-DDEFAULT_SYSROOT=" + os.path.join(cheriDir, "sdk/sysroot"),
                llvmDir])
    subprocess.check_call(["ninja"])
    # delete the files incompatible with cheribsd
    incompatibleFiles = glob("lib/clang/3.*/include/std*") + glob("lib/clang/3.*/include/limits.h")
    if len(incompatibleFiles) == 0:
        sys.exit("Could not find incompatible builtin includes. Build system changed?")
    for i in incompatibleFiles:
        print("removing incompatible header", i)
        os.remove(i)
    os.chdir(cheriDir)
    

def buildCHERIBSD():
    global rootfsDir
    global cheriDir
    cheribsdDir = os.path.join(cheriDir, "cheribsd")
    os.chdir(cheribsdDir)
    if not options.skip_update:
        subprocess.check_call(["git", "-C", cheribsdDir, "pull", "--rebase"])
    cheriCC = os.path.join(cheriDir, "qemu/llvm-build/bin/clang")
    os.environ["MAKEOBJDIRPREFIX"] = os.path.join(cheriDir, "qemu/obj")
    if options.clean:
        shutil.rmtree(os.path.join(cheriDir, "qemu/obj"))
    makeCmd = ["make", "CHERI=256", "CHERI_CC=" + cheriCC, "-j32",
               "CPUTYPE=mips", # mipsfpu for hardware float
               "-DDB_FROM_SRC", "-DNO_ROOT",
               # "CFLAGS=-Wno-error=capabilities",
               "-DNO_WERROR"
               ]
    subprocess.check_call(makeCmd + ["buildworld"])
    subprocess.check_call(makeCmd + ["KERNCONF=CHERI_MALTA64", "buildkernel"])
    if os.path.isdir(rootfsDir):
        shutil.rmtree(rootfsDir)
    os.makedirs(rootfsDir, exist_ok=True)
    subprocess.check_call(makeCmd + ["installworld", "DESTDIR=" + rootfsDir])
    subprocess.check_call(makeCmd + ["KERNCONF=CHERI_MALTA64", "installkernel", "DESTDIR=" + rootfsDir])
    subprocess.check_call(makeCmd + ["distribution", "DESTDIR=" + rootfsDir])
    with open(os.path.join(rootfsDir, "etc/fstab"), "w") as fstab:
        fstab.write("/dev/ada0 / ufs rw 1 1\n") # TODO: NFS?
    os.chdir(cheriDir)


def buildQEMUImage():
    global rootfsDir
    global cheriDir
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
    parser.add_argument("--clean", action="store_true", help="Do a clean build")
    parser.add_argument("--skip-update", action="store_true", help="Skip the git pull step")
    parser.add_argument("--skip-llvm", action="store_true", help="Don't build LLVM")
    parser.add_argument("--skip-cmake", action="store_true", help="Don't run cmake on LLVM")
    parser.add_argument("--skip-cheribsd", action="store_true", help="Don't build CHERIBSD")
    parser.add_argument("--disk-image", action="store_true", help="Build a disk image usable for QEMU")
    parser.add_argument("--disk-image-path", help="The disk image path (defaults to qemu/disk.img)")
    options = parser.parse_args()
    # print(options)
    
    logging.basicConfig(level=logging.INFO)
    cheriDir = os.path.expanduser("~/cheri")
    rootfsDir = os.path.join(cheriDir, "qemu/rootfs")

    os.chdir(cheriDir)
    if not options.skip_llvm:
        buildLLVM()
    if not options.skip_cheribsd:
        buildCHERIBSD()
    if options.disk_image:
        buildQEMUImage()
    