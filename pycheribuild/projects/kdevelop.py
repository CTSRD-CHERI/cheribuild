from ..project import CMakeProject, Project
from ..utils import *
from pathlib import Path
import tempfile

import os


def kdevInstallDir(config: CheriConfig):
    return config.sdkDir


class BuildLibKompareDiff2(CMakeProject):
    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=kdevInstallDir(config), buildType="Debug",
                         gitUrl="git://anongit.kde.org/libkomparediff2.git", appendCheriBitsToBuildDir=True)


class BuildKDevplatform(CMakeProject):
    dependencies = ["libkomparediff2"]

    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=kdevInstallDir(config), buildType="Debug",
                         gitUrl="https://github.com/RichardsonAlex/kdevplatform.git", appendCheriBitsToBuildDir=True)
        self.gitBranch = "cheri"


class BuildKDevelop(CMakeProject):
    dependencies = ["kdevplatform", "llvm"]

    def __init__(self, config: CheriConfig):
        super().__init__(config, installDir=kdevInstallDir(config), buildType="Debug",
                         gitUrl="https://github.com/RichardsonAlex/kdevelop.git", appendCheriBitsToBuildDir=True)
        # Tell kdevelop to use the CHERI clang
        self.configureArgs.append("-DLLVM_ROOT=" + str(self.config.sdkDir))
        self.gitBranch = "cheri"


class StartKDevelop(Project):
    target = "run-kdevelop"
    dependencies = ["kdevelop"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self._addRequiredSystemTool("cmake")
        self._addRequiredSystemTool("qtpaths")
        self.newEnv = {}

    def prependToEnvList(self, var: str, *paths: Path):
        pathList = list(map(str, paths)) + list(os.getenv(var, "").split(":"))
        self.newEnv[var] = ":".join(filter(None, pathList)) # remove empty parts

    def process(self):
        kdevelopBinary = self.config.sdkDir / "bin/kdevelop"
        if not kdevelopBinary.exists():
            self.dependencyError("KDevelop is missing:", kdevelopBinary,
                                 installInstructions="Run `cheribuild.py kdevelop` or `cheribuild.py " +
                                                     self.target + " -d`.")
        # find libdir:
        libdir = None  # type: Path
        libdirSuffixes = ("lib", "lib64", "lib/x86_64-linux-gnu")
        for d in libdirSuffixes:
            if (self.config.sdkDir / d / "libKDevPlatformUtil.so").exists():
                libdir = self.config.sdkDir / d

        if not libdir:
            self.dependencyError("Could not find the kdevelop libraries in any of the", libdirSuffixes, "subdirs of",
                                 self.config.sdkDir, installInstructions="Run `cheribuild.py kdevelop`")

        qtpluginDir = runCmd("qtpaths", "--plugin-dir", captureOutput=True).stdout.decode("utf-8").strip()
        self.prependToEnvList("PATH", self.config.sdkDir / "bin")
        self.prependToEnvList("XDG_CONFIG_DIRS", self.config.sdkDir / "etc/xdg")
        self.prependToEnvList("XDG_DATA_DIRS", self.config.sdkDir / "share")
        self.prependToEnvList("QT_PLUGIN_PATH", libdir / "plugins", Path(qtpluginDir))
        # self.prependToEnvList("PKG_CONFIG_PATH", libdir / "pkgconfig")
        self.prependToEnvList("QML2_IMPORT_PATH", libdir / "qml", Path(qtpluginDir).parent / "qml")
        self.prependToEnvList("QML_IMPORT_PATH", libdir / "qml", Path(qtpluginDir).parent / "qml")
        self.newEnv["QT_MESSAGE_PATTERN"] = "\033[32m%{time h:mm:ss.zzz}%{if-category}\033[32m %{category}:%{endif} %{if-debug}\033[34m%{function}%{endif}%{if-warning}\033[31m%{backtrace depth=8}\n%{endif}%{if-critical}\033[31m%{backtrace depth=8}\n%{endif}%{if-fatal}\033[31m%{backtrace depth=8}\n%{endif}\033[0m %{message}"
        self.newEnv["KDEV_CLANG_DISPLAY_ARGS"] = "1"
        self.newEnv["KDEV_CLANG_DISPLAY_DIAGS"] = "1"
        with setEnv(**self.newEnv):
            runCmd(kdevelopBinary, "--ps")
