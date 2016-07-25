from ..project import AutotoolsProject
from ..utils import *


class BuildQEMU(AutotoolsProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/CTSRD-CHERI/qemu.git", gitRevision=config.qemuRevision)
        self.gitBranch = "qemu-cheri"
        # QEMU will not work with BSD make, need GNU make
        self.makeCommand = "gmake" if IS_FREEBSD else "make"
        extraCFlags = "-g -Wno-error"

        if config.cheriBits == 128:
            # enable QEMU 128 bit capabilities
            # https://github.com/CTSRD-CHERI/qemu/commit/40a7fc2823e2356fa5ffe1ee1d672f1d5ec39a12
            extraCFlags += " -DCHERI_128=1"
        self.configureArgs.extend([
            "--target-list=cheri-softmmu",
            "--disable-linux-user",
            "--disable-bsd-user",
            "--disable-xen",
            "--disable-docs",
            "--extra-cflags=" + extraCFlags,
        ])
        if IS_LINUX:
            # "--enable-libnfs", # version on Ubuntu 14.04 is too old? is it needed?
            # self.configureArgs += ["--enable-kvm", "--enable-linux-aio", "--enable-vte", "--enable-sdl",
            #                        "--with-sdlabi=2.0", "--enable-virtfs"]
            self.configureArgs.extend(["--disable-stack-protector"])  # seems to be broken on some Ubuntu 14.04 systems
        else:
            self.configureArgs.extend(["--disable-linux-aio", "--disable-kvm"])

    def update(self):
        # the build sometimes modifies the po/ subdirectory
        # reset that directory by checking out the HEAD revision there
        # this is better than git reset --hard as we don't lose any other changes
        if (self.sourceDir / "po").is_dir():
            runCmd("git", "checkout", "HEAD", "po/", cwd=self.sourceDir, printVerboseOnly=True)
        super().update()
