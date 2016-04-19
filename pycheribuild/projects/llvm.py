import re
import shlex
import shutil

from ..project import Project
from ..utils import *


class BuildLLVM(Project):
    def __init__(self, config: CheriConfig):
        super().__init__("llvm", config, installDir=config.sdkDir, appendCheriBitsToBuildDir=True)
        self.makeCommand = "ninja"
        # try to find clang 3.7, otherwise fall back to system clang
        cCompiler = shutil.which("clang37") or shutil.which("clang")
        cppCompiler = shutil.which("clang++37") or shutil.which("clang++")
        if not cCompiler or not cppCompiler:
            fatalError("Could not find clang or clang37 in $PATH, please install it.")
        # make sure we have at least version 3.7
        versionPattern = re.compile(b"clang version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # clang prints this output to stderr
        versionString = runCmd(cCompiler, "-v", captureError=True, printVerboseOnly=True).stderr
        match = versionPattern.search(versionString)
        versionComponents = tuple(map(int, match.groups())) if match else (0, 0, 0)
        if versionComponents < (3, 7):
            fatalError("Clang version is too old (need at least 3.7): got", str(versionComponents))

        self.configureCommand = "cmake"
        self.configureArgs = [
            self.sourceDir, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_CXX_COMPILER=" + cppCompiler, "-DCMAKE_C_COMPILER=" + cCompiler,  # need at least 3.7 to build it
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
