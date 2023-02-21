set -e

password="$(cat ~/.config/ctsrd-jenkins-readonly-user.txt)"
olddir=$PWD
JENKINS_TEST_DIR=${JENKINS_TEST_DIR:-/local/scratch/$USER/jenkins-test}
cd "$JENKINS_TEST_DIR"


if [ -z "$SKIP_DOWNLOAD" ]; then
    if [ "$(uname -s)" = "Linux" ]; then
        curl -O -u "readonly:${password}" https://ctsrd-build.cl.cam.ac.uk/job/CLANG-LLVM-linux/job/dev/lastSuccessfulBuild/artifact/cheri-clang-llvm.tar.xz
    elif [ "$(uname -s)" = "FreeBSD" ]; then
        curl -O -u "readonly:${password}" https://ctsrd-build.cl.cam.ac.uk/job/CLANG-LLVM-freebsd/job/dev/lastSuccessfulBuild/artifact/cheri-clang-llvm.tar.xz
    fi

    # # sysroots (TODO)
    # for cpu in mips cheri128 cheri256; do
    #     curl -O -u "readonly:${password}" https://ctsrd-build.cl.cam.ac.uk/view/Toolchain/job/CHERIBSD-WORLD/CPU=${cpu},ISA=cap-table-pcrel/lastSuccessfulBuild/artifact/${cpu}-cap-table-pcrel-cheribsd-world.tar.xz
    # done
    #
    # # minimal kernel images:
    # curl -O -u "readonly:${password}" https://ctsrd-build.cl.cam.ac.uk/view/CheriBSD/job/CheriBSD-allkernels-multi/BASE_ABI=n64,CPU=mips,ISA=vanilla,label=freebsd/lastSuccessfulBuild/artifact/ctsrd/cheribsd/trunk/bsdtools/freebsd-malta64-mfs-root-jenkins_bluehive-kernel.bz2
    # curl -O -u "readonly:${password}" https://ctsrd-build.cl.cam.ac.uk/view/CheriBSD/job/CheriBSD-allkernels-multi/BASE_ABI=n64,CPU=cheri128,ISA=vanilla,label=freebsd/lastSuccessfulBuild/artifact/ctsrd/cheribsd/trunk/bsdtools/cheribsd128-cheri128-malta64-mfs-root-jenkins_bluehive-kernel.bz2
    # curl -O -u "readonly:${password}" https://ctsrd-build.cl.cam.ac.uk/view/CheriBSD/job/CheriBSD-allkernels-multi/BASE_ABI=n64,CPU=cheri256,ISA=vanilla,label=freebsd/lastSuccessfulBuild/artifact/ctsrd/cheribsd/trunk/bsdtools/cheribsd-cheri-malta64-mfs-root-jenkins_bluehive-kernel.bz2


    # QEMU
    curl -O -u "readonly:${password}" https://ctsrd-build.cl.cam.ac.uk/view/QEMU/job/qemu/job/qemu-cheri/lastSuccessfulBuild/artifact/*zip*/archive.zip
    rm -rf qemu-* archive/
    unzip archive.zip
    mv archive/qemu-* .
    chmod -v +x qemu-*/bin/*
    rmdir archive
fi

export WORKSPACE=$JENKINS_TEST_DIR
export CPU=cheri128
export ISA=cap-table-pcrel


if [ "$(uname -s)" = "Darwin" ]; then
    rm -rf cherisdk/bin
    mkdir -p cherisdk/bin
    for i in clang clang++ clang-cpp ld.lld ld; do
        ln -svfn "$CHERI_SDK/$i" "cherisdk/bin/$i"
        ln -svfn "$CHERI_SDK/$i" "cherisdk/bin/cheri-unknown-freebsd-$i"
        ln -svfn "$CHERI_SDK/$i" "cherisdk/bin/mips64-unknown-freebsd-$i"
    done
    for i in ar nm objcopy objdump objcopy ranlib strip; do
        ln -svfn "$CHERI_SDK/llvm-$i" "cherisdk/bin/llvm-$i"
        ln -svfn "$CHERI_SDK/llvm-$i" "cherisdk/bin/$i"
        ln -svfn "$CHERI_SDK/llvm-$i" "cherisdk/bin/cheri-unknown-freebsd-$i"
        ln -svfn "$CHERI_SDK/llvm-$i" "cherisdk/bin/mips64-unknown-freebsd-$i"
    done
    ln -svfn "$CHERI_SDK/llvm-config" "cherisdk/bin/llvm-config"



    mkdir -p qemu-mac/share
    mkdir -p qemu-mac/bin
    ln -svfn "$CHERI_SDK/../share/qemu" qemu-mac/share/qemu
    for i in cheri256 cheri128 cheri128magic; do
        ln -svfn "$CHERI_SDK/qemu-system-$i" qemu-mac/bin/
    done

    tar Jxf "${CPU}-cap-table-pcrel-cheribsd-world.tar.xz" -C cherisdk --strip-components 1 --exclude 'bin/*'
else
    $olddir/jenkins-cheri-build.py --extract-sdk
fi

ls -la cherisdk/bin
ls -la cherisdk
