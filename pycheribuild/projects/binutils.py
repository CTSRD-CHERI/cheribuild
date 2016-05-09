from ..project import AutotoolsProject
from ..utils import *


class BuildBinutils(AutotoolsProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, gitUrl="https://github.com/CTSRD-CHERI/binutils.git")
        # http://marcelog.github.io/articles/cross_freebsd_compiler_in_linux.html
        self.gitBranch = "cheribsd"  # the default branch "cheri" won't work for cross-compiling

        # If we don't use a patched binutils version on linux we get an ld binary that is
        # only able to handle 32 bit mips:
        # GNU ld (GNU Binutils) 2.18
        # Supported emulations:
        #     elf32ebmip

        # The version from the FreeBSD source tree supports the right targets:
        # GNU ld 2.17.50 [FreeBSD] 2007-07-03
        # Supported emulations:
        #    elf64btsmip_fbsd
        #    elf32btsmip_fbsd
        #    elf32ltsmip_fbsd
        #    elf64btsmip_fbsd
        #    elf64ltsmip_fbsd
        #    elf32btsmipn32_fbsd
        #    elf32ltsmipn32_fbsd
        self.configureArgs.extend([
            # on cheri gcc -dumpmachine returns mips64-undermydesk-freebsd, however this is not accepted by BFD
            # if we just pass --target=mips64 this apparently defaults to mips64-unknown-elf on freebsd
            # and also on Linux, but let's be explicit in case it assumes ELF binaries to target linux
            # "--target=mips64-undermydesk-freebsd",  # binutils for MIPS64/CHERI
            "--target=mips64-unknown-freebsd",  # binutils for MIPS64/FreeBSD
            "--disable-werror",  # -Werror won't work with recent compilers
            "--enable-ld",  # enable linker (is default, but just be safe)
            "--enable-libssp",  # not sure if this is needed
            "--enable-64-bit-bfd",  # Make sure we always have 64 bit support
            # "--enable-targets=" + enabledTargets,
            # TODO: --with-sysroot doesn't work properly so we need to tell clang not to pass the --sysroot option
            "--with-sysroot=" + str(self.config.sdkSysrootDir),  # as we pass --sysroot to clang we need this option
            "--disable-info",
            #  "--program-prefix=cheri-unknown-freebsd-",
            "MAKEINFO=missing",  # don't build docs, this will fail on recent Linux systems
        ])
        # newer compilers will default to -std=c99 which will break binutils:
        self.configureEnvironment["CFLAGS"] = "-std=gnu89 -O2"

    def update(self):
        self._ensureGitRepoIsCloned(srcDir=self.sourceDir, remoteUrl=self.gitUrl, initialBranch=self.gitBranch)
        # Make sure we have the version that can compile FreeBSD binaries
        status = self.runGitCmd("status", "-b", "-s", "--porcelain", "-u", "no",
                                captureOutput=True, printVerboseOnly=True)
        if not status.stdout.startswith(b"## cheribsd"):
            branches = self.runGitCmd("branch", "--list", captureOutput=True, printVerboseOnly=True).stdout
            if b" cheribsd" not in branches:
                self.runGitCmd("checkout", "-b", "cheribsd", "--track", "origin/cheribsd")
        self.runGitCmd("checkout", "cheribsd")
        super().update()

    def install(self):
        super().install()
        bindir = self.installDir / "bin"
        for tool in "addr2line ld ranlib strip ar nm readelf as objcopy size c++filt objdump strings".split():
            prefixedName = "mips64-unknown-freebsd-" + tool
            if not (bindir / prefixedName).is_file():
                fatalError("Binutils binary", prefixedName, "is missing!")
            # create the right symlinks to the tool (ld -> mips64-unknown-elf-ld, etc)
            runCmd("ln", "-fsn", prefixedName, tool, cwd=bindir)
            # Also symlink cheri-unknown-freebsd-ld -> ld (and the other targets)
            self.createBuildtoolTargetSymlinks(bindir / prefixedName, toolName=tool)
