import argparse
import os
import shutil
import shlex
import subprocess
import sys
import threading
import time
from .utils import *
from .targets import Target, targetManager
from .configloader import ConfigLoader
from pathlib import Path
from enum import Enum


class ProjectSubclassDefinitionHook(type):
    def __init__(cls, name: str, bases: set, clsdict: dict):
        super().__init__(name, bases, clsdict)
        if clsdict.get("doNotAddToTargets"):
            return  # if doNotAddToTargets is defined within the class we skip it

        if "target" in clsdict:
            targetName = clsdict["target"]
        elif name.startswith("Build"):
            targetName = name[len("Build"):].replace("_", "-").lower()
            cls.target = targetName
        else:
            sys.exit("Project target name cannot be inferred for " + name + ", set target= or doNotAddToTarget=True")
        if cls.__dict__.get("dependenciesMustBeBuilt"):
            if not cls.dependencies:
                sys.exit("PseudoTarget with no dependencies should not exist!! Target name = " + targetName)
        targetManager.addTarget(Target(targetName, cls, dependencies=set(cls.dependencies)))
        # print("Adding target", targetName, "with deps:", cls.dependencies)


class Project(object, metaclass=ProjectSubclassDefinitionHook):
    # These two class variables can be defined in subclasses to customize dependency ordering of targets
    target = ""  # type: str
    dependencies = []  # type: typing.List[str]
    dependenciesMustBeBuilt = False

    @classmethod
    def allDependencyNames(cls):
        result = set()
        for dep in cls.dependencies:
            result.add(dep)
            result = result.union(targetManager.targetMap[dep].projectClass.allDependencyNames())
        return result

    # Project subclasses will automatically have a target based on their name generated unless they add this:
    doNotAddToTargets = True  # type: bool

    # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
    clearLineSequence = b"\x1b[2K\r"

    cmakeInstallInstructions = ("Use your package manager to install CMake > 3.4 or run "
                                "`cheribuild.py cmake` to install the latest version locally")
    compileDBRequiresBear = True

    __commandLineOptionGroup = None

    @classmethod
    def addConfigOption(cls, name: str, default=None, kind=str, *, shortname=None, **kwargs):
        if not ConfigLoader.showAllHelp:
            kwargs["help"] = argparse.SUPPRESS
        if not cls.__commandLineOptionGroup:
            # noinspection PyProtectedMember
            cls.__commandLineOptionGroup = ConfigLoader._parser.add_argument_group(
                    "Options for target '" + cls.target + "'")

        return ConfigLoader.addOption(cls.target + "/" + name, shortname, default=default, type=kind,
                                      group=cls.__commandLineOptionGroup, **kwargs)

    @classmethod
    def addBoolOption(cls, name: str, *, shortname=None, **kwargs):
        return cls.addConfigOption(name, default=False, kind=bool, shortname=shortname, action="store_true", **kwargs)

    @classmethod
    def setupConfigOptions(cls):
        # statusUpdate("Setting up config options for", cls, cls.target)
        # TODO: add the gitRevision option
        pass

    def __init__(self, config: CheriConfig, *, projectName: str=None, sourceDir: Path=None, buildDir: Path=None,
                 installDir: Path=None, gitUrl="", gitRevision=None, appendCheriBitsToBuildDir=False):
        className = self.__class__.__name__
        if projectName:
            self.projectName = projectName
        elif self.target:
            self.projectName = self.target
        elif className.startswith("Build"):
            self.projectName = className[len("Build"):].replace("_", "-")
        else:
            fatalError("Project name is not set and cannot infer from class", className)
        self.projectNameLower = self.projectName.lower()

        self.gitUrl = gitUrl
        self.gitRevision = gitRevision
        self.gitBranch = ""
        self.config = config
        self.sourceDir = Path(sourceDir if sourceDir else config.sourceRoot / self.projectNameLower)
        # make sure we have different build dirs for LLVM/CHERIBSD/QEMU 128 and 256,
        buildDirSuffix = "-" + config.cheriBitsStr + "-build" if appendCheriBitsToBuildDir else "-build"
        self.buildDir = Path(buildDir if buildDir else config.buildRoot / (self.projectNameLower + buildDirSuffix))
        self.installDir = installDir
        self.makeCommand = "make"
        self.configureCommand = ""
        self._systemDepsChecked = False
        # non-assignable variables:
        self.commonMakeArgs = []
        self.configureArgs = []  # type: typing.List[str]
        self.configureEnvironment = {}  # type: typing.Dict[str,str]
        self.__requiredSystemTools = {}  # type: typing.Dict[str, typing.Any]
        self._preventAssign = True
        if self.config.createCompilationDB and self.compileDBRequiresBear:
            self._addRequiredSystemTool("bear", installInstructions="Run `cheribuild.py bear`")

    # Make sure that API is used properly
    def __setattr__(self, name, value):
        # if self.__dict__.get("_locked") and name == "x":
        #     raise AttributeError, "MyClass does not allow assignment to .x member"
        # self.__dict__[name] = value
        if self.__dict__.get("_preventAssign") and name in ("configureArgs", "configureEnvironment", "commonMakeArgs"):
            import traceback
            traceback.print_stack()
            fatalError("Project." + name + " mustn't be set, only modification is allowed.", "Called from",
                       self.__class__.__name__)
        self.__dict__[name] = value

    def _addRequiredSystemTool(self, executable: str, installInstructions=None):
        self.__requiredSystemTools[executable] = installInstructions

    def queryYesNo(self, message: str="", *, defaultResult=False, forceResult=True) -> bool:
        yesNoStr = " [Y]/n " if defaultResult else " y/[N] "
        if self.config.pretend:
            print(message + yesNoStr)
            return True  # in pretend mode we always return true
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

    def runGitCmd(self, *args, cwd=None, **kwargs):
        if not cwd:
            cwd = self.sourceDir
        return runCmd("git", *args, cwd=cwd, **kwargs)

    def _ensureGitRepoIsCloned(self, *, srcDir: Path, remoteUrl, initialBranch=None):
        if not (srcDir / ".git").is_dir():
            print(srcDir, "is not a git repository. Clone it from' " + remoteUrl + "'?", end="")
            if not self.queryYesNo(defaultResult=False):
                fatalError("Sources for", str(srcDir), " missing!")
            if initialBranch:
                runCmd("git", "clone", "--recurse-submodules", "--branch", initialBranch, remoteUrl, srcDir)
            else:
                runCmd("git", "clone", "--recurse-submodules", remoteUrl, srcDir)

    def _updateGitRepo(self, srcDir: Path, remoteUrl, *, revision=None, initialBranch=None):
        self._ensureGitRepoIsCloned(srcDir=srcDir, remoteUrl=remoteUrl, initialBranch=initialBranch)
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
        runCmd("git", "pull", "--recurse-submodules", "--rebase", cwd=srcDir, printVerboseOnly=True)
        runCmd("git", "submodule", "update", "--recursive", cwd=srcDir, printVerboseOnly=True)
        if hasChanges:
            runCmd("git", "stash", "pop", cwd=srcDir, printVerboseOnly=True)
        if revision:
            runCmd("git", "checkout", revision, cwd=srcDir, printVerboseOnly=True)

    def _makedirs(self, path: Path):
        printCommand("mkdir", "-p", path, printVerboseOnly=True)
        if not self.config.pretend:
            os.makedirs(str(path), exist_ok=True)

    # removes a directory tree if --clean is passed (or force=True parameter is passed)
    def _cleanDir(self, path: Path, force=False):
        if (self.config.clean or force) and path.is_dir():
            # http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
            # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
            runCmd("rm", "-rf", str(path))

        # make sure the dir is empty afterwards
        self._makedirs(path)

    def readFile(self, file: Path) -> str:
        # just return an empty string in pretend mode
        if self.config.pretend and not file.is_file():
            return "\n"
        with file.open("r", encoding="utf-8") as f:
            return f.read()

    def writeFile(self, file: Path, contents: str, *, overwrite: bool, noCommandPrint=False) -> None:
        """
        :param file: The target path to write contents to
        :param contents: the contents of the new file
        :param overwrite: If true the file will be overwritten, otherwise it will cause an error if the file exists
        :param noCommandPrint: don't ever print the echo commmand (even in verbose)
        """
        if not noCommandPrint:
            printCommand("echo", contents, colour=AnsiColour.green, outputFile=file, printVerboseOnly=True)
        if self.config.pretend:
            return
        if not overwrite and file.exists():
            fatalError("File", file, "already exists!")
        self._makedirs(file.parent)
        with file.open("w", encoding="utf-8") as f:
            f.write(contents)

    def createSymlink(self, src: Path, dest: Path, *, relative=True, cwd: Path=None):
        assert dest.is_absolute() or cwd is not None
        if not cwd:
            cwd = dest.parent
        if relative:
            if src.is_absolute():
                src = src.relative_to(dest.parent if dest.is_absolute() else cwd)
            if cwd is not None and cwd.is_dir():
                dest = dest.relative_to(cwd)
            runCmd("ln", "-fsn", src, dest, cwd=cwd, printVerboseOnly=True)
        else:
            runCmd("ln", "-fsn", src, dest, cwd=cwd, printVerboseOnly=True)

    def installFile(self, src: Path, dest: Path, *, force=False, createDirs=True):
        if force:
            printCommand("cp", "-f", src, dest, printVerboseOnly=True)
        else:
            printCommand("cp", src, dest, printVerboseOnly=True)
        if self.config.pretend:
            return
        if dest.exists() and force:
            dest.unlink()
        if not src.exists():
            fatalError("Required file", src, "does not exist")
        if createDirs and not dest.parent.exists():
            self._makedirs(dest.parent)
        if dest.is_symlink():
            dest.unlink()
        shutil.copy(str(src), str(dest), follow_symlinks=False)

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        # by default we don't keep any line persistent, just have updating output
        sys.stdout.buffer.write(Project.clearLineSequence)
        sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
        sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
        sys.stdout.buffer.flush()

    @staticmethod
    def _handleStdErr(outfile, stream, fileLock, noLogfile):
        for errLine in stream:
            with fileLock:
                sys.stderr.buffer.write(errLine)
                sys.stderr.buffer.flush()
                if not noLogfile:
                    outfile.write(errLine)

    def runMake(self, args: "typing.List[str]", makeTarget="", *, makeCommand: str=None, logfileName: str=None,
                cwd: Path=None, env=None, appendToLogfile=False) -> None:
        if not makeCommand:
            makeCommand = self.makeCommand
        if not cwd:
            cwd = self.buildDir

        if makeTarget:
            allArgs = args + [makeTarget]
            if not logfileName:
                logfileName = self.makeCommand + "." + makeTarget
        else:
            allArgs = args
            if not logfileName:
                logfileName = makeCommand
        allArgs = [makeCommand] + allArgs
        if self.config.createCompilationDB and self.compileDBRequiresBear:
            allArgs = [self.config.otherToolsDir / "bin/bear", "--cdb", self.buildDir / "compile_commands.json",
                       "--append"] + allArgs
        starttime = time.time()
        self.runWithLogfile(allArgs, logfileName=logfileName, stdoutFilter=self._makeStdoutFilter, cwd=cwd, env=env,
                            appendToLogfile=appendToLogfile)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", self.makeCommand, makeTarget, "took", time.time() - starttime, "seconds")

    def runWithLogfile(self, args: "typing.Sequence[str]", logfileName: str, *, stdoutFilter=None, cwd: Path = None,
                       env: dict=None, appendToLogfile=False) -> None:
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
        printCommand(args, cwd=cwd)
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

        if logfilePath.is_file() and not appendToLogfile:
            logfilePath.unlink()  # remove old logfile
        args = list(map(str, args))  # make sure all arguments are strings
        cmdStr = " ".join([shlex.quote(s) for s in args])
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
                subprocess.check_call(args, cwd=str(cwd), stdout=logfile, stderr=logfile, env=newEnv)
                return
            make = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=newEnv)
            # use a thread to print stderr output and write it to logfile (not using a thread would block)
            logfileLock = threading.Lock()  # we need a mutex so the logfile line buffer doesn't get messed up
            stderrThread = threading.Thread(target=self._handleStdErr,
                                            args=(logfile, make.stderr, logfileLock, self.config.noLogfile))
            stderrThread.start()
            for line in make.stdout:
                with logfileLock:  # make sure we don't interleave stdout and stderr lines
                    if not self.config.noLogfile:
                        # will be /dev/null with noLogfile anyway but saves a syscall per line
                        logfile.write(line)
                    if stdoutFilter:
                        stdoutFilter(line)
                    else:
                        sys.stdout.buffer.write(line)
                        sys.stdout.buffer.flush()
            retcode = make.wait()
            remainingErr, remainingOut = make.communicate()
            sys.stderr.buffer.write(remainingErr)
            logfile.write(remainingErr)
            sys.stdout.buffer.write(remainingOut)
            logfile.write(remainingOut)
            if stdoutFilter:
                # add the final new line after the filtering
                sys.stdout.buffer.write(b"\n")
            stderrThread.join()
            if retcode:
                raise SystemExit("Command \"%s\" failed with exit code %d.\nSee %s for details." %
                                 (cmdStr, retcode, logfile.name))

    def createBuildtoolTargetSymlinks(self, tool: Path, toolName: str=None, createUnprefixedLink: bool=False,
                                      cwd: str=None):
        """
        Create mips4-unknown-freebsd, cheri-unknown-freebsd and mips64-unknown-freebsd prefixed symlinks
        for build tools like clang, ld, etc.
        :param createUnprefixedLink: whether to create a symlink toolName -> tool.name
        (in case the real tool is prefixed)
        :param cwd: the working directory
        :param tool: the binary for which the symlinks will be created
        :param toolName: the unprefixed name of the tool (defaults to tool.name) such as e.g. "ld", "ar"
        """
        # if the actual tool we are linking to make sure we link to the destinations so we don't create symlink loops
        cwd = cwd or tool.parent  # set cwd before resolving potential symlink
        if not toolName:
            toolName = tool.name
        if not tool.is_file():
            fatalError("Attempting to create symlink to non-existent build tool:", tool)

        # a prefixed tool was installed -> create link such as mips4-unknown-freebsd-ld -> ld
        if createUnprefixedLink:
            assert tool.name != toolName
            runCmd("ln", "-fsn", tool.name, toolName, cwd=cwd, printVerboseOnly=True)

        for target in ("mips4-unknown-freebsd-", "cheri-unknown-freebsd-", "mips64-unknown-freebsd-"):
            link = tool.parent / (target + toolName)  # type: Path
            if link == tool:  # happens for binutils, where prefixed tools are installed
                # if self.config.verbose:
                #    print(coloured(AnsiColour.yellow, "Not overwriting", link, "because it is the target"))
                continue
            runCmd("ln", "-fsn", tool.name, target + toolName, cwd=cwd, printVerboseOnly=True)

    def dependencyError(self, *args, installInstructions: str=None):
        self._systemDepsChecked = True  # make sure this is always set
        fatalError(*args, fixitHint=installInstructions)

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

    def update(self):
        self._updateGitRepo(self.sourceDir, self.gitUrl, revision=self.gitRevision, initialBranch=self.gitBranch)

    def clean(self):
        # TODO: never use the source dir as a build dir
        # will have to check how well binutils and qemu work there
        if (self.buildDir / ".git").is_dir():
            # just use git clean for cleanup
            runCmd("git", "clean", "-dfx", cwd=self.buildDir)
        else:
            self._cleanDir(self.buildDir)

    def configure(self):
        if self.configureCommand:
            self.runWithLogfile([self.configureCommand] + self.configureArgs,
                                logfileName="configure", cwd=self.buildDir, env=self.configureEnvironment)

    def compile(self):
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag])

    def install(self):
        self.runMake(self.commonMakeArgs, "install")

    def process(self):
        if not self.config.skipUpdate:
            self.update()
        if not self._systemDepsChecked:
            self.checkSystemDependencies()
        assert self._systemDepsChecked, "self._systemDepsChecked must be set by now!"
        if self.config.clean:
            self.clean()
        # always make sure the build dir exists
        if not self.buildDir.is_dir():
            self._makedirs(self.buildDir)
        if not self.config.skipConfigure:
            statusUpdate("Configuring", self.projectName, "... ")
            self.configure()
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
    def setupConfigOptions(cls):
        super().setupConfigOptions()
        cls.cmakeBuildType = cls.addConfigOption("build-type", default=cls.defaultCMakeBuildType, metavar="BUILD_TYPE",
                                                 help="The CMake build type (Debug, RelWithDebInfo, Release)")
        cls.cmakeOptions = cls.addConfigOption("cmake-options", default=[], kind=list, metavar="OPTIONS",
                                               help="Additional command line options to pass to CMake")

    def __init__(self, *args, generator=Generator.Ninja, buildType="Release", **kwargs):
        super().__init__(*args, **kwargs)
        self.configureCommand = "cmake"
        self._addRequiredSystemTool("cmake", installInstructions=self.cmakeInstallInstructions)
        self.generator = generator
        self.configureArgs.append(str(self.sourceDir))  # TODO: use undocumented -H and -B options?
        if self.generator == CMakeProject.Generator.Ninja:
            self.configureArgs.append("-GNinja")
            self.makeCommand = "ninja"
            self._addRequiredSystemTool("ninja")
        if self.generator == CMakeProject.Generator.Makefiles:
            self.configureArgs.append("-GUnix Makefiles")
        self.configureArgs.append("-DCMAKE_INSTALL_PREFIX=" + str(self.installDir))
        self.configureArgs.append("-DCMAKE_BUILD_TYPE=" + self.cmakeBuildType)
        # TODO: do it always?
        if self.config.createCompilationDB:
            self.configureArgs.append("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
        # Don't add the user provided options here, add them in configure() so that they are put last

    def configure(self):
        self.configureArgs.extend(self.cmakeOptions)
        super().configure()

    @staticmethod
    def _makeStdoutFilter(line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        Project._makeStdoutFilter(line)

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
    """
    Like Project but automatically sets up the defaults for autotools like projects
    Sets configure command to ./configure, adds --prefix=installdir
    """
    def __init__(self, *args, configureScript="configure", **kwargs):
        super().__init__(*args, **kwargs)
        self.configureCommand = self.sourceDir / configureScript
        self.configureArgs.append("--prefix=" + str(self.installDir))
        self.makeCommand = "make"


# A target that does nothing (used for e.g. the "all" target)
class PseudoTarget(Project):
    doNotAddToTargets = True
    dependenciesMustBeBuilt = True

    def process(self):
        pass


class BuildAll(PseudoTarget):
    dependencies = ["qemu", "sdk", "disk-image", "run"]
