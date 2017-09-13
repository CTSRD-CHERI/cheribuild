#
# Copyright (c) 2016 Alex Richardson
# All rights reserved.
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory under DARPA/AFRL contract FA8750-10-C-0237
# ("CTSRD"), as part of the DARPA CRASH research programme.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS ``AS IS'' AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
# OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
# SUCH DAMAGE.
#
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import errno
from enum import Enum
from pathlib import Path

from .config.loader import ConfigLoaderBase, ComputedDefaultValue
from .config.chericonfig import CheriConfig, CrossCompileTarget
from .targets import Target, targetManager
from .filesystemutils import FileSystemUtils
from .utils import *

__all__ = ["Project", "CMakeProject", "AutotoolsProject", "TargetAlias", "TargetAliasWithDependencies", # no-combine
           "SimpleProject", "CheriConfig", "flushStdio"]  # no-combine


def flushStdio(stream):
    while True:
        try:
            # can lead to EWOULDBLOCK if stream cannot be flushed immediately
            stream.flush()
            break
        except BlockingIOError as e:
            if e.errno != errno.EWOULDBLOCK:
                raise
            else:
                time.sleep(0.1)


class ProjectSubclassDefinitionHook(type):
    def __init__(cls, name: str, bases, clsdict):
        super().__init__(name, bases, clsdict)
        if clsdict.get("doNotAddToTargets"):
            return  # if doNotAddToTargets is defined within the class we skip it

        projectName = None
        if "projectName" in clsdict:
            projectName = clsdict["projectName"]
        else:
            # fall back to name of target then infer from class name
            # if targetName:
            #     projectName = targetName
            if name.startswith("Build"):
                projectName = name[len("Build"):].replace("_", "-")
            cls.projectName = projectName

        # load "target" field first then check project name (as that might default to target)
        targetName = None
        if "target" in clsdict:
            targetName = clsdict["target"]
        elif projectName:
            targetName = projectName.lower()
            cls.target = targetName

        if not targetName:
            sys.exit("target name is not set and cannot infer from class " + name +
                     " -- set projectName=, target= or doNotAddToTargets=True")

        if cls.__dict__.get("dependenciesMustBeBuilt"):
            if not cls.dependencies:
                sys.exit("PseudoTarget with no dependencies should not exist!! Target name = " + targetName)
        targetManager.addTarget(Target(targetName, cls, dependencies=set(cls.dependencies)))
        # print("Adding target", targetName, "with deps:", cls.dependencies)


class SimpleProject(FileSystemUtils, metaclass=ProjectSubclassDefinitionHook):
    _configLoader = None  # type: ConfigLoaderBase

    # These two class variables can be defined in subclasses to customize dependency ordering of targets
    target = ""  # type: str
    projectName = None
    dependencies = []  # type: typing.List[str]
    dependenciesMustBeBuilt = False
    isAlias = False
    sourceDir = None
    buildDir = None
    installDir = None

    @classmethod
    def allDependencyNames(cls):
        result = set()
        for dep in cls.dependencies:
            result.add(dep)
            result = result.union(targetManager.targetMap[dep].projectClass.allDependencyNames())
        return result

    # Project subclasses will automatically have a target based on their name generated unless they add this:
    doNotAddToTargets = True

    # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
    _clearLineSequence = b"\x1b[2K\r"

    _cmakeInstallInstructions = ("Use your package manager to install CMake > 3.4 or run "
                                "`cheribuild.py cmake` to install the latest version locally")
    __commandLineOptionGroup = None

    @classmethod
    def addConfigOption(cls, name: str, default: "typing.Union[Type_T, typing.Callable[[], Type_T]]" = None,
                        kind: "typing.Callable[[str], Type_T]" = str, *,
                        showHelp=False, shortname=None, **kwargs) -> "Type_T":
        configOptionKey = cls.target
        # use the old config option for cheribsd
        if cls.target == "cheribsd-without-sysroot":
            configOptionKey = cls.projectName.lower()
        elif cls.target != cls.projectName.lower():
            fatalError("Target name does not match project name:", cls.target, "vs", cls.projectName.lower())

        # Hide stuff like --foo/install-directory from --help
        if isinstance(default, ComputedDefaultValue):
            if callable(default.asString):
                default.asString = default.asString(cls)
        helpHidden = not showHelp

        # check that the group was defined in the current class not a superclass
        if "_commandLineOptionGroup" not in cls.__dict__:
            # noinspection PyProtectedMember
            # has to be a single underscore otherwise the name gets mangled to _Foo__commandlineOptionGroup
            cls._commandLineOptionGroup = cls._configLoader._parser.add_argument_group(
                "Options for target '" + cls.target + "'")

        return cls._configLoader.addOption(configOptionKey + "/" + name, shortname, default=default, type=kind,
                                           _owningClass=cls, group=cls._commandLineOptionGroup, helpHidden=helpHidden,
                                           **kwargs)

    @classmethod
    def addBoolOption(cls, name: str, *, shortname=None, default=False, **kwargs):
        return cls.addConfigOption(name, default=default, kind=bool, shortname=shortname, action="store_true", **kwargs)

    @classmethod
    def addPathOption(cls, name: str, *, shortname=None, **kwargs):
        return cls.addConfigOption(name, kind=Path, shortname=shortname, **kwargs)

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        pass

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.__requiredSystemTools = {}  # type: typing.Dict[str, typing.Any]
        self._systemDepsChecked = False

    def _addRequiredSystemTool(self, executable: str, installInstructions=None, homebrewPackage=None):
        if IS_MAC and not installInstructions:
            if not homebrewPackage:
                homebrewPackage = executable
            self.__requiredSystemTools[executable] = "Run `brew install " + homebrewPackage + "`"
        else:
            self.__requiredSystemTools[executable] = installInstructions

    def queryYesNo(self, message: str = "", *, defaultResult=False, forceResult=True) -> bool:
        yesNoStr = " [Y]/n " if defaultResult else " y/[N] "
        if self.config.pretend:
            print(message + yesNoStr)
            return forceResult  # in pretend mode we always return true
        if self.config.force:
            # in force mode we always return the forced result without prompting the user
            print(message + yesNoStr, "y" if forceResult else "n")
            return forceResult
        if not sys.__stdin__.isatty():
            return defaultResult  # can't get any input -> return the default
        result = input(message + yesNoStr)
        if defaultResult:
            return not result.startswith("n")  # if default is yes accept anything other than strings starting with "n"
        return str(result).lower().startswith("y")  # anything but y will be treated as false

    @staticmethod
    def _handleStdErr(outfile, stream, fileLock, project: "Project"):
        for errLine in stream:
            with fileLock:
                # noinspection PyProtectedMember
                if project._lastStdoutLineCanBeOverwritten:
                    sys.stdout.buffer.write(b"\n")
                    flushStdio(sys.stdout)
                    project._lastStdoutLineCanBeOverwritten = False
                sys.stderr.buffer.write(errLine)
                flushStdio(sys.stderr)
                if not project.config.noLogfile:
                    outfile.write(errLine)

    def _lineNotImportantStdoutFilter(self, line: bytes):
        # by default we don't keep any line persistent, just have updating output
        if self._lastStdoutLineCanBeOverwritten:
            sys.stdout.buffer.write(Project._clearLineSequence)
        sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
        sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
        flushStdio(sys.stdout)
        self._lastStdoutLineCanBeOverwritten = True

    def _showLineStdoutFilter(self, line: bytes):
        if self._lastStdoutLineCanBeOverwritten:
            sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.write(line)
        flushStdio(sys.stdout)
        self._lastStdoutLineCanBeOverwritten = False

    def _stdoutFilter(self, line: bytes):
        self._lineNotImportantStdoutFilter(line)

    def runWithLogfile(self, args: "typing.Sequence[str]", logfileName: str, *, stdoutFilter=None, cwd: Path = None,
                       env: dict = None, appendToLogfile=False) -> None:
        """
        Runs make and logs the output
        config.quiet doesn't display anything, normal only status updates and config.verbose everything
        :param appendToLogfile: whether to append to the logfile if it exists
        :param args: the command to run (e.g. ["make", "-j32"])
        :param logfileName: the name of the logfile (e.g. "build.log")
        :param cwd the directory to run make in (defaults to self.buildDir)
        :param stdoutFilter a filter to use for standard output (a function that takes a single bytes argument)
        :param env the environment to pass to make
        """
        printCommand(args, cwd=cwd, env=env)
        # make sure that env is either None or a os.environ with the updated entries entries
        if env:
            newEnv = os.environ.copy()
            env = {k: str(v) for k, v in env.items()}  # make sure everything is a string
            newEnv.update(env)
        else:
            newEnv = None
        assert not logfileName.startswith("/")
        if self.config.noLogfile:
            logfilePath = Path(os.devnull)
        else:
            logfilePath = self.buildDir / (logfileName + ".log")
            print("Saving build log to", logfilePath)
        if self.config.pretend:
            return
        if self.config.verbose:
            stdoutFilter = None

        if not self.config.noLogfile and logfilePath.is_file() and not appendToLogfile:
            logfilePath.unlink()  # remove old logfile
        args = list(map(str, args))  # make sure all arguments are strings
        cmdStr = " ".join([shlex.quote(s) for s in args])

        if self.config.noLogfile:
            if stdoutFilter is None:
                # just run the process connected to the current stdout/stdin
                check_call_handle_noexec(args, cwd=str(cwd), env=newEnv)
            else:
                make = popen_handle_noexec(args, cwd=str(cwd), stdout=subprocess.PIPE, env=newEnv)
                self.__runProcessWithFilteredOutput(make, None, stdoutFilter, cmdStr)
            return

        # open file in append mode
        with logfilePath.open("ab") as logfile:
            # print the command and then the logfile
            if appendToLogfile:
                logfile.write(b"\n\n")
            if cwd:
                logfile.write(("cd " + shlex.quote(str(cwd)) + " && ").encode("utf-8"))
            logfile.write(cmdStr.encode("utf-8") + b"\n\n")
            if self.config.quiet:
                # a lot more efficient than filtering every line
                check_call_handle_noexec(args, cwd=str(cwd), stdout=logfile, stderr=logfile, env=newEnv)
                return
            make = popen_handle_noexec(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=newEnv)
            self.__runProcessWithFilteredOutput(make, logfile, stdoutFilter, cmdStr)

    def __runProcessWithFilteredOutput(self, proc: subprocess.Popen, logfile: "typing.Optional[io.FileIO]",
                                       stdoutFilter: "typing.Callable[[bytes], None]", cmdStr: str):
        logfileLock = threading.Lock()  # we need a mutex so the logfile line buffer doesn't get messed up
        stderrThread = None
        if logfile:
            # use a thread to print stderr output and write it to logfile (not using a thread would block)
            stderrThread = threading.Thread(target=self._handleStdErr, args=(logfile, proc.stderr, logfileLock, self))
            stderrThread.start()
        for line in proc.stdout:
            with logfileLock:  # make sure we don't interleave stdout and stderr lines
                if logfile:
                    logfile.write(line)
                if stdoutFilter:
                    stdoutFilter(line)
                else:
                    sys.stdout.buffer.write(line)
                    flushStdio(sys.stdout)
        retcode = proc.wait()
        if stderrThread:
            stderrThread.join()
        # Not sure if the remaining call is needed
        remainingErr, remainingOut = proc.communicate()
        if remainingErr:
            print("Process had remaining stderr:", remainingErr)
            sys.stderr.buffer.write(remainingErr)
            if logfile:
                logfile.write(remainingOut)
        if remainingOut:
            print("Process had remaining stdout:", remainingOut)
            sys.stdout.buffer.write(remainingOut)
            if logfile:
                logfile.write(remainingErr)
        if stdoutFilter and self._lastStdoutLineCanBeOverwritten:
            # add the final new line after the filtering
            sys.stdout.buffer.write(b"\n")
        if retcode:
            message = "Command \"%s\" failed with exit code %d.\n" % (cmdStr, retcode)
            if logfile:
                message += "See " + logfile.name + " for details."
            raise SystemExit(message)

    def dependencyError(self, *args, installInstructions: str = None):
        self._systemDepsChecked = True  # make sure this is always set
        fatalError("Dependency for", self.target, "missing:", *args, fixitHint=installInstructions)

    def checkSystemDependencies(self) -> None:
        """
        Checks that all the system dependencies (required tool, etc) are available
        :return: Throws an error if dependencies are missing
        """
        for (tool, installInstructions) in self.__requiredSystemTools.items():
            if not shutil.which(tool):
                if callable(installInstructions):
                    installInstructions = installInstructions()
                if not installInstructions:
                    installInstructions = "Try installing `" + tool + "` using your system package manager."
                self.dependencyError("Required program", tool, "is missing!", installInstructions=installInstructions)
        self._systemDepsChecked = True

    def process(self):
        raise NotImplementedError()


def installDirNotSpecified(config: CheriConfig, project: "Project"):
    raise RuntimeError("dummy impl must not be called: " + str(project))


def _defaultBuildDir(config: CheriConfig, project: "Project"):
    # make sure we have different build dirs for LLVM/CHERIBSD/QEMU 128 and 256
    if config.crossCompileTarget == CrossCompileTarget.NATIVE:
        buildDirSuffix = "-build"
    elif config.crossCompileTarget == CrossCompileTarget.MIPS:
        buildDirSuffix = "-mips-build"
    else:
        buildDirSuffix = "-" + config.cheriBitsStr + "-build"
    return config.buildRoot / (project.projectName.lower() + buildDirSuffix)


class Project(SimpleProject):
    repository = ""
    gitRevision = None
    gitBranch = ""
    skipGitSubmodules = False
    compileDBRequiresBear = True
    doNotAddToTargets = True

    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: Path(config.sourceRoot / project.projectName.lower()),
        asString=lambda cls: "$SOURCE_ROOT/" + cls.projectName.lower())

    appendCheriBitsToBuildDir = False
    """ Whether to append -128/-256 to the computed build directory name"""
    defaultBuildDir = ComputedDefaultValue(
        function=_defaultBuildDir, asString=lambda cls: "$BUILD_ROOT/" + cls.projectName.lower())

    requiresGNUMake = False
    """ If true this project must be built with GNU make (gmake on FreeBSD) and not BSD make or ninja"""

    # TODO: remove these three
    @classmethod
    def getSourceDir(cls, config: CheriConfig):
        return cls.sourceDir

    @classmethod
    def getBuildDir(cls, config: CheriConfig):
        return cls.buildDir

    @classmethod
    def getInstallDir(cls, config: CheriConfig):
        return cls.installDir

    _installToSDK = ComputedDefaultValue(
        function=lambda config, project: config.sdkDir,
        asString="$INSTALL_ROOT/sdk256 or $INSTALL_ROOT/sdk128 depending on CHERI bits")
    _installToBootstrapTools = ComputedDefaultValue(
        function=lambda config, project: config.otherToolsDir,
        asString="$INSTALL_ROOT/bootstrap")

    defaultInstallDir = installDirNotSpecified
    """ The default installation directory (will probably be set to _installToSDK or _installToBootstrapTools) """

    # useful for cross compile projects that use a prefix and DESTDIR
    installPrefix = None
    destdir = None

    __can_use_lld_map = dict()  # type: typing.Dict[Path, bool]

    @classmethod
    def canUseLLd(cls, compiler: Path):
        if compiler not in cls.__can_use_lld_map:
            try:
                runCmd([compiler, "-fuse-ld=lld", "-xc", "-o" "-", "-"], runInPretendMode=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       input="int main() { return 0; }\n", printVerboseOnly=True)
                statusUpdate(compiler, "supports -fuse-ld=lld, linking should be much faster!")
                cls.__can_use_lld_map[compiler] = True
            except subprocess.CalledProcessError:
                statusUpdate(compiler, "does not support -fuse-ld=lld, using slower bfd instead")
                cls.__can_use_lld_map[compiler] = False
        return cls.__can_use_lld_map[compiler]

    @classmethod
    def setupConfigOptions(cls, installDirectoryHelp="", **kwargs):
        super().setupConfigOptions(**kwargs)
        # statusUpdate("Setting up config options for", cls, cls.target)
        cls.sourceDir = cls.addPathOption("source-directory", metavar="DIR", default=cls.defaultSourceDir,
                                          help="Override default source directory for " + cls.projectName)
        cls.buildDir = cls.addPathOption("build-directory", metavar="DIR", default=cls.defaultBuildDir,
                                         help="Override default source directory for " + cls.projectName)
        if not installDirectoryHelp:
            installDirectoryHelp = "Override default install directory for " + cls.projectName
        cls.installDir = cls.addPathOption("install-directory", metavar="DIR", help=installDirectoryHelp,
                                           default=cls.defaultInstallDir)
        if "repository" in cls.__dict__:
            cls.gitRevision = cls.addConfigOption("git-revision", kind=str, help="The git revision to checkout prior to"
                                                                                 " building. Useful if HEAD is broken for one project but you still"
                                                                                 " want to update the other projects.",
                                                  metavar="REVISION")
            cls.repository = cls.addConfigOption("repository", kind=str, help="The URL of the git repository",
                                                 default=cls.repository, metavar="REPOSITORY")
            # TODO: add the gitRevision option

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # set up the install/build/source directories (allowing overrides from config file)

        self.configureCommand = ""
        # non-assignable variables:
        self.commonMakeArgs = []
        self.configureArgs = []  # type: typing.List[str]
        self.configureEnvironment = {}  # type: typing.Dict[str,str]
        if self.config.createCompilationDB and self.compileDBRequiresBear:
            self._addRequiredSystemTool("bear", installInstructions="Run `cheribuild.py bear`")
        self._lastStdoutLineCanBeOverwritten = False
        self._preventAssign = True

        if self.requiresGNUMake:
            if IS_LINUX and not shutil.which("gmake"):
                statusUpdate("Could not find `gmake` command, assuming `make` is GNU make")
                self.makeCommand = "make"
            else:
                self._addRequiredSystemTool("gmake", homebrewPackage="make")
                self.makeCommand = "gmake"
        else:
            self.makeCommand = "make"

    # Make sure that API is used properly
    def __setattr__(self, name, value):
        # if self.__dict__.get("_locked") and name == "x":
        #     raise AttributeError, "MyClass does not allow assignment to .x member"
        # self.__dict__[name] = value
        if self.__dict__.get("_preventAssign"):
            # assert name not in ("sourceDir", "buildDir", "installDir")
            if name in ("configureArgs", "configureEnvironment", "commonMakeArgs"):
                import traceback
                traceback.print_stack()
                fatalError("Project." + name + " mustn't be set, only modification is allowed.", "Called from",
                           self.__class__.__name__)
        self.__dict__[name] = value

    def _ensureGitRepoIsCloned(self, *, srcDir: Path, remoteUrl, initialBranch=None, skipSubmodules=False):
        # git-worktree creates a .git file instead of a .git directory so we can't use .is_dir()
        if not (srcDir / ".git").exists():
            print(srcDir, "is not a git repository. Clone it from' " + remoteUrl + "'?", end="")
            if not self.queryYesNo(defaultResult=False):
                fatalError("Sources for", str(srcDir), " missing!")
            cloneCmd = ["git", "clone"]
            if not skipSubmodules:
                cloneCmd.append("--recurse-submodules")
            if initialBranch:
                cloneCmd += ["--branch", initialBranch]
            runCmd(cloneCmd + [remoteUrl, srcDir], cwd="/")

    def _updateGitRepo(self, srcDir: Path, remoteUrl, *, revision=None, initialBranch=None, skipSubmodules=False):
        self._ensureGitRepoIsCloned(srcDir=srcDir, remoteUrl=remoteUrl, initialBranch=initialBranch,
                                    skipSubmodules=skipSubmodules)
        # make sure we run git stash if we discover any local changes
        hasChanges = len(runCmd("git", "diff", "--stat", "--ignore-submodules",
                                captureOutput=True, cwd=srcDir, printVerboseOnly=True).stdout) > 1
        if hasChanges:
            print(coloured(AnsiColour.green, "Local changes detected in", srcDir))
            # TODO: add a config option to skip this query?
            if not self.queryYesNo("Stash the changes, update and reapply?", defaultResult=True, forceResult=True):
                statusUpdate("Skipping update of", srcDir)
                return
            # TODO: ask if we should continue?
            stashResult = runCmd("git", "stash", "save", "Automatic stash by cheribuild.py",
                                 captureOutput=True, cwd=srcDir, printVerboseOnly=True).stdout
            # print("stashResult =", stashResult)
            if "No local changes to save" in stashResult.decode("utf-8"):
                # print("NO REAL CHANGES")
                hasChanges = False  # probably git diff showed something from a submodule
        pullCmd = ["git", "pull"]
        if not skipSubmodules:
            pullCmd.append("--recurse-submodules")
        runCmd(pullCmd + ["--rebase"], cwd=srcDir, printVerboseOnly=True)
        if not skipSubmodules:
            runCmd("git", "submodule", "update", "--recursive", cwd=srcDir, printVerboseOnly=True)
        if hasChanges:
            runCmd("git", "stash", "pop", cwd=srcDir, printVerboseOnly=True)
        if revision:
            runCmd("git", "checkout", revision, cwd=srcDir, printVerboseOnly=True)

    def runMake(self, args: "typing.List[str]", makeTarget="", *, makeCommand: str = None, logfileName: str = None,
                cwd: Path = None, env=None, appendToLogfile=False, compilationDbName="compile_commands.json",
                stdoutFilter: "typing.Callable[[bytes], None]" = "__default_filter__") -> None:
        if not makeCommand:
            makeCommand = self.makeCommand
        if not cwd:
            cwd = self.buildDir

        if makeTarget:
            allArgs = args + [makeTarget]
            if not logfileName:
                logfileName = Path(makeCommand).name + "." + makeTarget
        else:
            allArgs = args
            if not logfileName:
                logfileName = Path(makeCommand).name
        allArgs = [makeCommand] + allArgs
        if self.config.createCompilationDB and self.compileDBRequiresBear:
            allArgs = [shutil.which("bear"), "--cdb", self.buildDir / compilationDbName,
                       "--append"] + allArgs
        if not self.config.makeWithoutNice:
            allArgs = ["nice"] + allArgs
        starttime = time.time()
        if self.config.noLogfile and stdoutFilter == "__default_filter__":
            # if output isatty() (i.e. no logfile) ninja already filters the output -> don't slow this down by
            # adding a redundant filter in python
            if self.makeCommand == "ninja" and makeTarget != "install":
                stdoutFilter = None
        if stdoutFilter == "__default_filter__":
            stdoutFilter = self._stdoutFilter
        # TODO: this should be a super-verbose flag instead
        if self.config.verbose and makeCommand == "ninja":
            allArgs.append("-v")
        if self.config.passDashKToMake:
            allArgs.append("-k")
            if makeCommand == "ninja":
                # ninja needs the maximum number of failed jobs as an argument
                allArgs.append("50")
        self.runWithLogfile(allArgs, logfileName=logfileName, stdoutFilter=stdoutFilter, cwd=cwd, env=env,
                            appendToLogfile=appendToLogfile)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", self.makeCommand, makeTarget, "took", time.time() - starttime, "seconds")

    def update(self):
        if not self.repository:
            fatalError("Cannot update", self.projectName, "as it is missing a git URL", fatalWhenPretending=True)
        self._updateGitRepo(self.sourceDir, self.repository, revision=self.gitRevision, initialBranch=self.gitBranch,
                            skipSubmodules=self.skipGitSubmodules)

    def clean(self) -> ThreadJoiner:
        assert self.config.clean
        # TODO: never use the source dir as a build dir (unfortunately GDB, postgres and elftoolchain won't work)
        # will have to check how well binutils and qemu work there
        if (self.buildDir / ".git").is_dir():
            if (self.buildDir / "GNUmakefile").is_file() and self.makeCommand != "bmake" and self.target != "elftoolchain":
                runCmd(self.makeCommand, "distclean", cwd=self.buildDir)
            else:
                # just use git clean for cleanup
                warningMessage(self.projectName, "does not support out-of-source builds, using git clean to remove"
                                                 "build artifacts.")
                # Try to keep project files for IDEs and other dotfiles:
                runCmd("git", "clean", "-dfx", "--exclude=.*", "--exclude=*.kdev4", cwd=self.buildDir)
        else:
            return self.asyncCleanDirectory(self.buildDir)
        return ThreadJoiner(None)

    def needsConfigure(self) -> bool:
        """
        :return: Whether the configure command needs to be run (by default assume yes)
        """
        return True

    def configure(self, cwd: Path = None):
        if cwd is None:
            cwd = self.buildDir
        if not self.needsConfigure() and not self.config.configureOnly and not self.config.forceConfigure:
            if not self.config.pretend and not self.config.clean:
                return
        if self.configureCommand:
            self.runWithLogfile([self.configureCommand] + self.configureArgs,
                                logfileName="configure", cwd=cwd, env=self.configureEnvironment)

    def compile(self, cwd: Path = None):
        if cwd is None:
            cwd = self.buildDir
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag], cwd=cwd)

    @property
    def makeInstallEnv(self):
        if self.destdir:
            env = os.environ.copy()
            env["DESTDIR"] = str(self.destdir)
            return env
        return None

    @property
    def real_install_root_dir(self):
        """
        :return: the real install root directory (e.g. if prefix == /usr/local and desdir == /tmp/benchdir it will
         return /tmp/benchdir/usr/local
        """
        if self.destdir is not None:
            assert self.installPrefix
            return self.destdir / self.installPrefix.relative_to(Path("/"))
        return self.installDir

    def runMakeInstall(self, *, args: list = None, target="install", _stdoutFilter="__default_filter__", cwd=None):
        if args is None:
            args = self.commonMakeArgs
        self.runMake(args, makeTarget=target, stdoutFilter=_stdoutFilter, env=self.makeInstallEnv, cwd=cwd)

    def install(self, _stdoutFilter="__default_filter__"):
        self.runMakeInstall(_stdoutFilter=_stdoutFilter)

    def process(self):
        if self.config.verbose:
            installDir = self.installDir
            if self.destdir is not None:
                installDir = str(self.destdir) + str(self.installPrefix)
            print(self.projectName, "directories: source=%s, build=%s, install=%s" %
                  (self.sourceDir, self.buildDir, installDir))
        if not self.config.skipUpdate:
            self.update()
        if not self._systemDepsChecked:
            self.checkSystemDependencies()
        assert self._systemDepsChecked, "self._systemDepsChecked must be set by now!"

        # run the rm -rf <build dir> in the background
        cleaningTask = self.clean() if self.config.clean else ThreadJoiner(None)
        with cleaningTask:
            if not self.buildDir.is_dir():
                self.makedirs(self.buildDir)
            if not self.config.skipConfigure or self.config.configureOnly:
                statusUpdate("Configuring", self.projectName, "... ")
                self.configure()
            if self.config.configureOnly:
                return
            statusUpdate("Building", self.projectName, "... ")
            self.compile()
            if not self.config.skipInstall:
                statusUpdate("Installing", self.projectName, "... ")
                self.install()


class CMakeProject(Project):
    doNotAddToTargets = True
    compileDBRequiresBear = False  # cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON does it
    """
    Like Project but automatically sets up the defaults for CMake projects
    Sets configure command to CMake, adds -DCMAKE_INSTALL_PREFIX=installdir
    and checks that CMake is installed
    """

    class Generator(Enum):
        Default = 0
        Ninja = 1
        Makefiles = 2

    defaultCMakeBuildType = "Release"

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.cmakeBuildType = cls.addConfigOption("build-type", default=cls.defaultCMakeBuildType, metavar="BUILD_TYPE",
                                                 help="The CMake build type (Debug, RelWithDebInfo, Release)")
        cls.cmakeOptions = cls.addConfigOption("cmake-options", default=[], kind=list, metavar="OPTIONS",
                                               help="Additional command line options to pass to CMake")

    def __init__(self, config, generator=Generator.Ninja):
        super().__init__(config)
        self.configureCommand = os.getenv("CMAKE_COMMAND", "cmake")
        self._addRequiredSystemTool("cmake", installInstructions=self._cmakeInstallInstructions)
        self.generator = generator
        self.configureArgs.append(str(self.sourceDir))  # TODO: use undocumented -H and -B options?
        if self.generator == CMakeProject.Generator.Ninja:
            self.configureArgs.append("-GNinja")
            self.makeCommand = "ninja"
            self._addRequiredSystemTool("ninja")
        if self.generator == CMakeProject.Generator.Makefiles:
            self.configureArgs.append("-GUnix Makefiles")

        self.configureArgs.append("-DCMAKE_BUILD_TYPE=" + self.cmakeBuildType)
        # TODO: do it always?
        if self.config.createCompilationDB:
            self.configureArgs.append("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
            # Don't add the user provided options here, add them in configure() so that they are put last
        self.__minimum_cmake_version = tuple()

    def add_cmake_options(self, **kwargs):
        for option, value in kwargs.items():
            if isinstance(value, bool):
                value = "ON" if value else "OFF"
            self.configureArgs.append("-D" + option + "=" + str(value))

    def set_minimum_cmake_version(self, major, minor):
        self.__minimum_cmake_version = (major, minor)

    def _cmakeInstallStdoutFilter(self, line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        self._showLineStdoutFilter(line)

    def needsConfigure(self) -> bool:
        if self.config.pretend and (self.config.forceConfigure or self.config.clean):
            return True
        # CMake is smart enough to detect when it must be reconfigured -> skip configure if cache exists
        cmakeCache = self.buildDir / "CMakeCache.txt"
        buildFile = "build.ninja" if self.generator == CMakeProject.Generator.Ninja else "Makefile"
        return not cmakeCache.exists() or not (self.buildDir / buildFile).exists()

    def configure(self, **kwargs):
        if self.installPrefix:
            assert self.destdir, "custom install prefix requires DESTDIR being set!"
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.installPrefix)
        else:
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.installDir)
        self.configureArgs.extend(self.cmakeOptions)
        # make sure we get a completely fresh cache when --reconfigure is passed:
        cmakeCache = self.buildDir / "CMakeCache.txt"
        if self.config.forceConfigure:
            self.deleteFile(cmakeCache)
        super().configure(**kwargs)

    def install(self, _stdoutFilter="__DEFAULT__"):
        if _stdoutFilter == "__DEFAULT__":
            _stdoutFilter = self._cmakeInstallStdoutFilter
        super().install(_stdoutFilter=_stdoutFilter)

    def _get_cmake_version(self):
        versionPattern = re.compile(b"cmake version (\\d+)\\.(\\d+)\\.?(\\d+)?")
        # cmake prints this output to stdout
        versionString = runCmd(self.configureCommand, "--version", captureOutput=True, printVerboseOnly=True).stdout
        match = versionPattern.search(versionString)
        return tuple(map(int, match.groups())) if match else (0, 0, 0)

    def checkSystemDependencies(self):
        super().checkSystemDependencies()
        if self.__minimum_cmake_version:
            # try to find cmake 3.4 or newer
            versionComponents = self._get_cmake_version()
            # noinspection PyTypeChecker
            if versionComponents < self.__minimum_cmake_version:
                versionStr = ".".join(map(str, versionComponents))
                expectedStr = ".".join(map(str, self.__minimum_cmake_version))
                self.dependencyError("CMake version", versionStr, "is too old (need at least", expectedStr + ")",
                                     installInstructions=self._cmakeInstallInstructions)

    @staticmethod
    def findPackage(name: str) -> bool:
        try:
            cmd = "cmake --find-package -DCOMPILER_ID=Clang -DLANGUAGE=CXX -DMODE=EXIST -DQUIET=TRUE".split()
            cmd.append("-DNAME=" + name)
            return runCmd(cmd).returncode == 0
        except subprocess.CalledProcessError:
            return False


class AutotoolsProject(Project):
    doNotAddToTargets = True
    _configure_supports_prefix = True

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.extraConfigureFlags = cls.addConfigOption("configure-options", default=[], kind=list, metavar="OPTIONS",
                                                      help="Additional command line options to pass to configure")

    """
    Like Project but automatically sets up the defaults for autotools like projects
    Sets configure command to ./configure, adds --prefix=installdir
    """

    def __init__(self, config, configureScript="configure"):
        super().__init__(config)
        self.configureCommand = self.sourceDir / configureScript

    def configure(self, cwd: Path=None):
        if self._configure_supports_prefix:
            if self.installPrefix:
                assert self.destdir, "custom install prefix requires DESTDIR being set!"
                self.configureArgs.append("--prefix=" + str(self.installPrefix))
            else:
                self.configureArgs.append("--prefix=" + str(self.installDir))
        if self.extraConfigureFlags:
            self.configureArgs.extend(self.extraConfigureFlags)
        super().configure(cwd=cwd)

    def needsConfigure(self):
        return not (self.buildDir / "Makefile").exists()

# A target that is just an alias for at least one other targets but does not force building of dependencies
class TargetAlias(SimpleProject):
    doNotAddToTargets = True
    dependenciesMustBeBuilt = False
    hasSourceFiles = False
    isAlias = True

    def process(self):
        assert len(self.dependencies) > 0


# A target that does nothing (used for e.g. the "all" target)
class TargetAliasWithDependencies(TargetAlias):
    doNotAddToTargets = True
    dependenciesMustBeBuilt = True
    hasSourceFiles = False


class BuildAll(TargetAliasWithDependencies):
    dependencies = ["qemu", "sdk", "disk-image", "run"]
