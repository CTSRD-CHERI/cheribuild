import os
import subprocess
import datetime

from ..project import Project
from ..utils import *

from pathlib import Path


class BuildSDK(Project):
    target = "sdk-sysroot"
    dependencies = ["llvm", "cheribsd"] if IS_FREEBSD else ["awk", "elftoolchain", "binutils", "llvm"]

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # if we pass a string starting with a slash to Path() it will reset to that absolute path
        # luckily we have to prepend mips.mips64, so it works out fine
        # expands to e.g. /home/alr48/cheri/output/cheribsd-obj/mips.mips64/home/alr48/cheri/cheribsd
        cheribsdBuildRoot = Path(self.config.cheribsdObj, "mips.mips64" + str(self.config.cheribsdSources))
        self.CHERITOOLS_OBJ = cheribsdBuildRoot / "tmp/usr/bin/"
        self.CHERIBOOTSTRAPTOOLS_OBJ = cheribsdBuildRoot / "tmp/legacy/usr/bin/"
        self.CHERILIBEXEC_OBJ = cheribsdBuildRoot / "tmp/usr/libexec/"

    def fixSymlinks(self):
        # copied from the build_sdk.sh script
        # TODO: we could do this in python as well, but this method works
        fixlinksSrc = includeLocalFile("files/fixlinks.c")
        runCmd("cc", "-x", "c", "-", "-o", self.config.sdkDir / "bin/fixlinks", input=fixlinksSrc)
        runCmd(self.config.sdkDir / "bin/fixlinks", cwd=self.config.sdkSysrootDir / "usr/lib")

    def buildCheridis(self):
        # Compile the cheridis helper (TODO: add it to the LLVM repo instead?)
        cheridisSrc = includeLocalFile("files/cheridis.c")
        runCmd("cc", "-DLLVM_PATH=\"%s\"" % str(self.config.sdkDir / "bin"), "-x", "c", "-",
               "-o", self.config.sdkDir / "bin/cheridis", input=cheridisSrc)

    def createSdkNotOnFreeBSD(self):
        if not self.config.freeBsdBuilderOutputPath or not self.config.freeBsdBuildMachine:
            # TODO: improve this information
            fatalError("SDK files must be copied from a FreeBSD server. See --help for more info")
            return
        remoteSysrootPath = os.path.join(self.config.freeBsdBuilderOutputPath, self.config.sdkDirectoryName,
                                         self.config.sysrootArchiveName)
        remoteSysrootPath = self.config.freeBsdBuildMachine + ":" + remoteSysrootPath
        statusUpdate("Will build SDK on", self.config.freeBsdBuildMachine, "and copy the sysroot files from",
                     remoteSysrootPath)
        if not self.queryYesNo("Continue?"):
            return

        if not self.config.freeBsdBuilderCopyOnly:
            # build the SDK on the remote machine:
            remoteRunScript = Path(__file__).parent.resolve() / "py3-run-remote.sh"
            if not remoteRunScript.is_file():
                remoteRunScript = Path(__file__).parent.parent.parent.resolve() / "py3-run-remote.sh"
            if not remoteRunScript.is_file():
                fatalError("Could not find py3-run-remote.sh script. Should be in this directory!")
            runCmd(remoteRunScript, self.config.freeBsdBuildMachine, __file__,
                   "--cheri-bits", self.config.cheriBits,  # make sure we build for the right number of cheri bits
                   "sdk")  # run target SDK with dependencies

        # now copy the files
        self._makedirs(self.config.sdkSysrootDir)
        runCmd("rm", "-f", self.config.sdkDir / self.config.sysrootArchiveName, printVerboseOnly=True)
        runCmd("scp", remoteSysrootPath, self.config.sdkDir)
        runCmd("rm", "-rf", self.config.sdkSysrootDir)
        runCmd("tar", "xzf", self.config.sdkDir / self.config.sysrootArchiveName, cwd=self.config.sdkDir)
        self.installCMakeConfig()

    def installCMakeConfig(self):
        date = datetime.datetime.now()
        microVersion = str(date.year) + str(date.month) + str(date.day)
        versionFile = includeLocalFile("files/CheriSDKConfigVersion.cmake.in")
        versionFile.replace("@SDK_BUILD_DATE@", microVersion)
        configFile = includeLocalFile("files/CheriSDKConfig.cmake")
        cmakeConfigDir = self.config.sdkDir / "share/cmake/CheriSDK"
        self._makedirs(cmakeConfigDir)
        self.writeFile(cmakeConfigDir / "CheriSDKConfig.cmake", configFile, overwrite=True)
        self.writeFile(cmakeConfigDir / "CheriSDKConfigVersion.cmake", versionFile, overwrite=True)

    def process(self):
        if not IS_FREEBSD:
            self.createSdkNotOnFreeBSD()
            return

        for i in (self.CHERIBOOTSTRAPTOOLS_OBJ, self.CHERITOOLS_OBJ, self.CHERITOOLS_OBJ, self.config.cheribsdRootfs):
            if not i.is_dir():
                fatalError("Directory", i, "is missing!")
        # make sdk a link to the 256 bit sdk
        if (self.config.outputRoot / "sdk").is_dir():
            # remove the old sdk directory from previous versions of this script
            runCmd("rm", "-rf", self.config.outputRoot / "sdk", printVerboseOnly=True)
        if not self.config.pretend and not (self.config.outputRoot / "sdk").exists():
            runCmd("ln", "-sf", "sdk256", "sdk", cwd=self.config.outputRoot)
        # we need to add include files and libraries to the sysroot directory
        self._cleanDir(self.config.sdkSysrootDir, force=True)  # make sure the sysroot is cleaned
        self._makedirs(self.config.sdkSysrootDir / "usr")
        # use tar+untar to copy all necessary files listed in metalog to the sysroot dir
        archiveCmd = ["tar", "cf", "-", "--include=./lib/", "--include=./usr/include/",
                      "--include=./usr/lib/", "--include=./usr/libcheri", "--include=./usr/libdata/",
                      # only pack those files that are mentioned in METALOG
                      "@METALOG"]
        printCommand(archiveCmd, cwd=self.config.cheribsdRootfs)
        if not self.config.pretend:
            with subprocess.Popen(archiveCmd, stdout=subprocess.PIPE, cwd=str(self.config.cheribsdRootfs)) as tar:
                runCmd(["tar", "xf", "-"], stdin=tar.stdout, cwd=self.config.sdkSysrootDir)
        if not (self.config.sdkSysrootDir / "lib/libc.so.7").is_file():
            fatalError(self.config.sdkSysrootDir, "is missing the libc library, install seems to have failed!")

        # install tools:
        tools = "as objdump strings addr2line crunchide gcc gcov nm strip ld objcopy size brandelf elfcopy".split()
        for tool in tools:
            if (self.CHERITOOLS_OBJ / tool).is_file():
                self.copyFile(self.CHERITOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)
            elif (self.CHERIBOOTSTRAPTOOLS_OBJ / tool).is_file():
                self.copyFile(self.CHERIBOOTSTRAPTOOLS_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)
            else:
                fatalError("Required tool", tool, "is missing!")

        # GCC wants the cc1 and cc1plus tools to be in the directory specified by -B.
        # We must make this the same directory that contains ld for linking and
        # compiling to both work...
        for tool in ("cc1", "cc1plus"):
            self.copyFile(self.CHERILIBEXEC_OBJ / tool, self.config.sdkDir / "bin" / tool, force=True)

        tools += "clang clang++ llvm-mc llvm-objdump llvm-readobj llvm-size llc".split()
        for tool in tools:
            self.createBuildtoolTargetSymlinks(self.config.sdkDir / "bin" / tool)

        self.buildCheridis()
        # fix symbolic links in the sysroot:
        print("Fixing absolute paths in symbolic links inside lib directory...")
        self.fixSymlinks()
        # create an archive to make it easier to copy the sysroot to another machine
        runCmd("rm", "-f", self.config.sdkDir / self.config.sysrootArchiveName)
        runCmd("tar", "-czf", self.config.sdkDir / self.config.sysrootArchiveName, "sysroot",
               cwd=self.config.sdkDir)
        print("Successfully populated sysroot")
        self.installCMakeConfig()
