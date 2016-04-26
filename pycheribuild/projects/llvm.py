import re
import shlex
import shutil
from pathlib import Path

from ..project import Project
from ..utils import *


class BuildLLVM(Project):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True)
        self.requiredSystemTools = ["ninja", "cmake"]
        self.makeCommand = "ninja"

        # TODO: also accept 3.8, 3.9, etc binaries
        # try to find clang 3.7, otherwise fall back to system clang
        self.cCompiler = shutil.which("clang37") or shutil.which("clang-3.7") or shutil.which("clang")
        self.cppCompiler = shutil.which("clang++37") or shutil.which("clang++-3.7") or shutil.which("clang++")

        self.configureCommand = "cmake"
        self.configureArgs = [
            self.sourceDir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_INSTALL_PREFIX=" + str(self.installDir),
            "-DLLVM_TOOL_LLDB_BUILD=OFF",  # disable LLDB for now
            # saves a bit of time and but might be slightly broken in current clang:
            "-DCLANG_ENABLE_STATIC_ANALYZER=OFF",  # save some build time by skipping the static analyzer
            "-DCLANG_ENABLE_ARCMT=OFF",  # need to disable ARCMT to disable static analyzer
        ]
        if IS_FREEBSD:
            self.configureArgs.append("-DDEFAULT_SYSROOT=" + str(self.config.sdkSysrootDir))
            self.configureArgs.append("-DLLVM_DEFAULT_TARGET_TRIPLE=cheri-unknown-freebsd")

        if self.config.cheriBits == 128:
            self.configureArgs.append("-DLLVM_CHERI_IS_128=ON")

    def printClang37InstallHint(self):
        print("Clang 3.7 (or newer) was not found.")
        if IS_FREEBSD:
            print("Try running `pkg install clang37`")
        osRelease = self.readFile(Path("/etc/os-release")) if Path("/etc/os-release").is_file() else ""
        if "Ubuntu" in osRelease:
            print("""Try following the instructions on http://askubuntu.com/questions/735201/installing-clang-3-8-on-ubuntu-14-04-3:
    wget -O - http://llvm.org/apt/llvm-snapshot.gpg.key|sudo apt-key add -
    sudo apt-add-repository "deb http://llvm.org/apt/trusty/ llvm-toolchain-trusty-3.7 main"
    sudo apt-get update
    sudo apt-get install clang-3.7
""")

    def checkSystemDependencies(self):
        with setEnv(PATH=self.config.dollarPathWithOtherTools):
            super().checkSystemDependencies()
        if not self.cCompiler or not self.cppCompiler:
            self.printClang37InstallHint()
            self.dependencyError("Could not find clang!")
        # make sure we have at least version 3.7
        versionPattern = re.compile(b"clang version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # clang prints this output to stderr
        versionString = runCmd(self.cCompiler, "-v", captureError=True, printVerboseOnly=True).stderr
        match = versionPattern.search(versionString)
        versionComponents = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if versionComponents < (3, 7):
            versionStr = ".".join(map(str, versionComponents))
            if IS_LINUX:
                self.printClang37InstallHint()
            self.dependencyError(self.cCompiler, "version", versionStr, "is too old (need at least 3.7).")

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        Project._makeStdoutFilter(line)

    def update(self):
        self._updateGitRepo(self.sourceDir, "https://github.com/CTSRD-CHERI/llvm.git",
                            revision=self.config.llvmRevision)
        self._updateGitRepo(self.sourceDir / "tools/clang", "https://github.com/CTSRD-CHERI/clang.git",
                            revision=self.config.clangRevision)
        self._updateGitRepo(self.sourceDir / "tools/lldb", "https://github.com/CTSRD-CHERI/lldb.git",
                            revision=self.config.lldbRevision)

    def install(self):
        super().install()
        # delete the files incompatible with cheribsd
        incompatibleFiles = list(self.installDir.glob("lib/clang/3.*/include/std*"))
        incompatibleFiles += self.installDir.glob("lib/clang/3.*/include/limits.h")
        if len(incompatibleFiles) == 0:
            fatalError("Could not find incompatible builtin includes. Build system changed?")
        print("Removing incompatible builtin includes...")
        for i in incompatibleFiles:
            printCommand("rm", shlex.quote(str(i)), printVerboseOnly=True)
            if not self.config.pretend:
                i.unlink()
        # create a symlink for the target
        self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang")
        self.createBuildtoolTargetSymlinks(self.installDir / "bin/clang++")

    def process(self):
        # this must be added after checkSystemDependencies
        self.configureArgs.append("-DCMAKE_CXX_COMPILER=" + self.cppCompiler)
        self.configureArgs.append("-DCMAKE_C_COMPILER=" + self.cCompiler)

        with setEnv(PATH=self.config.dollarPathWithOtherTools):
            super().process()
