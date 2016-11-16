import os
import shlex
import sys

from ..project import Project
from ..utils import *


class BuildCHERIBSD(Project):
    dependencies = ["llvm"]

    @classmethod
    def setupConfigOptions(cls):
        super().setupConfigOptions()
        defaultExtraMakeOptions = [
            "DEBUG_FLAGS=-g",  # enable debug stuff
            "-DWITHOUT_TESTS",  # seems to break the creation of disk-image (METALOG is invalid)
            "-DWITHOUT_HTML",  # should not be needed
            "-DWITHOUT_SENDMAIL", "-DWITHOUT_MAIL",  # no need for sendmail
            "-DWITHOUT_SVNLITE",  # no need for SVN
            # "-DWITHOUT_GAMES",  # not needed
            # "-DWITHOUT_MAN",  # seems to be a majority of the install time
            # "-DWITH_FAST_DEPEND",  # no separate make depend step, do it while compiling
            # "-DWITH_INSTALL_AS_USER", should be enforced by -DNO_ROOT
            # "-DWITH_DIRDEPS_BUILD", "-DWITH_DIRDEPS_CACHE",  # experimental fast build options
            # "-DWITH_LIBCHERI_JEMALLOC"  # use jemalloc instead of -lmalloc_simple
        ]
        # For compatibility we still accept --cheribsd-make-options here
        cls.makeOptions = cls.addConfigOption("build-options", default=defaultExtraMakeOptions, kind=list,
                                              metavar="OPTIONS", shortname="-cheribsd-make-options",  # compatibility
                                              help="Additional options to be passed to make when building CHERIBSD. "
                                                   "See `man src.conf` for more info.")
        # TODO: separate options for kernel/install?

    def __init__(self, config: CheriConfig, *, projectName="cheribsd", kernelConfig="CHERI_MALTA64"):
        super().__init__(config, projectName=projectName, sourceDir=config.sourceRoot / "cheribsd",
                         installDir=config.cheribsdRootfs, buildDir=config.cheribsdObj, appendCheriBitsToBuildDir=True,
                         gitUrl="https://github.com/CTSRD-CHERI/cheribsd.git", gitRevision=config.cheriBsdRevision)
        self.kernelConfig = kernelConfig
        if self.config.cheriBits == 128:
            # make sure we use a kernel with 128 bit CPU features selected
            self.kernelConfig = kernelConfig.replace("CHERI_", "CHERI128_")
        self.binutilsDir = self.config.sdkDir / "mips64/bin"
        self.cheriCC = self.config.sdkDir / "bin/clang"
        self.cheriCXX = self.config.sdkDir / "bin/clang++"
        self.installAsRoot = os.getuid() == 0
        self.commonMakeArgs.extend([
            "CHERI=" + self.config.cheriBitsStr,
            # "-dCl",  # add some debug output to trace commands properly
            "CHERI_CC=" + str(self.cheriCC),
            # "CPUTYPE=mips64", # mipsfpu for hardware float
            # (apparently no longer supported: https://github.com/CTSRD-CHERI/cheribsd/issues/102)
            "-DDB_FROM_SRC",  # don't use the system passwd file
            "-DNO_WERROR",  # make sure we don't fail if clang introduces a new warning
            "-DNO_CLEAN",  # don't clean, we have the --clean flag for that
            "-DNO_ROOT",  # use this even if current user is root, as without it the METALOG file is not created
            # "CROSS_BINUTILS_PREFIX=" + str(self.binutilsDir),  # use the CHERI-aware binutils and not the builtin ones
            # TODO: once clang can build the kernel:
            #  "-DCROSS_COMPILER_PREFIX=" + str(self.config.sdkDir / "bin")
            "KERNCONF=" + self.kernelConfig,
        ])
        self.commonMakeArgs.extend(self.makeOptions)
        if not (self.config.verbose or self.config.quiet):
            # By default we only want to print the status updates -> use make -s so we have to do less filtering
            self.commonMakeArgs.append("-s")

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        if line.startswith(b">>> "):  # major status update
            sys.stdout.buffer.write(Project.clearLineSequence)
            sys.stdout.buffer.write(line)
        elif line.startswith(b"===> "):  # new subdirectory
            # clear the old line to have a continuously updating progress
            sys.stdout.buffer.write(Project.clearLineSequence)
            sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
            sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
            sys.stdout.buffer.flush()
        elif line.startswith(b"-----------"):
            pass  # useless separator
        else:
            sys.stdout.buffer.write(line)

    def _removeSchgFlag(self, *paths: "typing.Iterable[str]"):
        for i in paths:
            file = self.installDir / i
            if file.exists():
                runCmd("chflags", "noschg", str(file))

    def setupEnvironment(self):
        if not self.cheriCC.is_file():
            fatalError("CHERI CC does not exist: ", self.cheriCC)
        if not self.cheriCXX.is_file():
            fatalError("CHERI CXX does not exist: ", self.cheriCXX)
        # if not (self.binutilsDir / "as").is_file():
        #     fatalError("CHERI MIPS binutils are missing. Run 'cheribuild.py binutils'?")
        if not self.config.skipBuildworld:
            if self.installAsRoot:
                # we need to remove the schg flag as otherwise rm -rf will fail to remove these files
                self._removeSchgFlag(
                    "lib/libc.so.7", "lib/libcrypt.so.5", "lib/libthr.so.3", "libexec/ld-cheri-elf.so.1",
                    "libexec/ld-elf.so.1", "sbin/init", "usr/bin/chpass", "usr/bin/chsh", "usr/bin/ypchpass",
                    "usr/bin/ypchfn", "usr/bin/ypchsh", "usr/bin/login", "usr/bin/opieinfo", "usr/bin/opiepasswd",
                    "usr/bin/passwd", "usr/bin/yppasswd", "usr/bin/su", "usr/bin/crontab", "usr/lib/librt.so.1",
                    "var/empty"
                )
            # make sure the old install is purged before building, otherwise we might get strange errors
            # and also make sure it exists (if DESTDIR doesn't exist yet install will fail!)
            # if we installed as root remove the schg flag from files before cleaning (otherwise rm will fail)
            self._cleanDir(self.installDir, force=True)
        else:
            self._makedirs(self.installDir)

    def clean(self):
        if self.config.skipBuildworld:
            # TODO: only clean the current kernel config not all of them
            kernelBuildDir = self.buildDir / "mips.mips64/home/alr48/cheri/cheribsd/sys/"
            self._cleanDir(kernelBuildDir)
        else:
            super().clean()

    def compile(self):
        self.setupEnvironment()
        if not self.config.skipBuildworld:
            self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildworld", cwd=self.sourceDir)
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], "buildkernel", cwd=self.sourceDir)

    def install(self):
        # don't use multiple jobs here
        installArgs = self.commonMakeArgs + ["DESTDIR=" + str(self.installDir)]
        self.runMake(installArgs, "installkernel", cwd=self.sourceDir)
        if not self.config.skipBuildworld:
            self.runMake(installArgs, "installworld", cwd=self.sourceDir)
            self.runMake(installArgs, "distribution", cwd=self.sourceDir)

    def process(self):
        if not IS_FREEBSD:
            statusUpdate("Can't build CHERIBSD on a non-FreeBSD host! Any targets that depend on this will need to scp",
                         "the required files from another server (see --frebsd-build-server options)")
            return
        # make sure the new clang and other tool are picked up
        # TODO: this shouldn't be needed, we build binutils as part of cheribsd
        path = os.getenv("PATH")
        if not path.startswith(str(self.config.sdkDir)):
            path = str(self.config.sdkDir / "bin") + ":" + path
        with setEnv(MAKEOBJDIRPREFIX=str(self.buildDir), PATH=path):
            super().process()
