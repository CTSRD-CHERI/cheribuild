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


class SimpleProject(object, metaclass=ProjectSubclassDefinitionHook):
    # These two class variables can be defined in subclasses to customize dependency ordering of targets
    target = ""  # type: str
    projectName = None
    dependencies = []  # type: typing.List[str]
    dependenciesMustBeBuilt = False
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
    clearLineSequence = b"\x1b[2K\r"

    cmakeInstallInstructions = ("Use your package manager to install CMake > 3.4 or run "
                                "`cheribuild.py cmake` to install the latest version locally")
    __commandLineOptionGroup = None

    @classmethod
    def addConfigOption(cls, name: str, default: "typing.Union[Type_T, typing.Callable[[], Type_T]]"=None,
                        kind: "typing.Callable[[str], Type_T]"=str, *,
                        showHelp=False, shortname=None, **kwargs) -> "Type_T":
        assert cls.target, "target not set for " + cls.__name__
        # Hide stuff like --foo/install-directory from --help
        if isinstance(default, ConfigLoader.ComputedDefaultValue):
            if callable(default.asString):
                default.asString = default.asString(cls)
        helpHidden = not showHelp

        # check that the group was defined in the current class not a superclass
        if "_commandLineOptionGroup" not in cls.__dict__:
            # noinspection PyProtectedMember
            # has to be a single underscore otherwise the name gets mangled to _Foo__commandlineOptionGroup
            cls._commandLineOptionGroup = ConfigLoader._parser.add_argument_group(
                    "Options for target '" + cls.target + "'")

        return ConfigLoader.addOption(cls.target + "/" + name, shortname, default=default, type=kind, _owningClass=cls,
                                      group=cls._commandLineOptionGroup, helpHidden=helpHidden, **kwargs)

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
        self.config = config
        self.__requiredSystemTools = {}  # type: typing.Dict[str, typing.Any]
        self._systemDepsChecked = False

    def _addRequiredSystemTool(self, executable: str, installInstructions=None):
        self.__requiredSystemTools[executable] = installInstructions

    def queryYesNo(self, message: str="", *, defaultResult=False, forceResult=True) -> bool:
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

    def makedirs(self, path: Path):
        if not self.config.pretend and not path.is_dir():
            printCommand("mkdir", "-p", path, printVerboseOnly=True)
            os.makedirs(str(path), exist_ok=True)

    def _deleteDirectories(self, *dirs):
        # http://stackoverflow.com/questions/5470939/why-is-shutil-rmtree-so-slow
        # shutil.rmtree(path) # this is slooooooooooooooooow for big trees
        runCmd("rm", "-rf", *dirs)

    def cleanDirectory(self, path: Path, keepRoot=False) -> None:
        """ After calling this function path will be an empty directory
        :param path: the directory to delete
        :param keepRoot: Whether to keep the root directory (e.g. for NFS exported mountpoints)
        """
        if path.is_dir():
            # If the root dir is used e.g. as an NFS mount we mustn't remove it, but only the subdirectories
            entries = list(map(str, path.iterdir())) if keepRoot else [path]
            self._deleteDirectories(*entries)
        # always make sure the path exists
        self.makedirs(path)

    class DeleterThread(threading.Thread):
        def __init__(self, project: "SimpleProject", path: Path):
            super().__init__(name="Deleting " + str(path))
            self.path = path
            self.project = project

        def run(self):
            try:
                if self.project.config.verbose:
                    statusUpdate("Deleting", self.path, "asynchronously")
                self.project._deleteDirectories(self.path)
                if self.project.config.verbose:
                    statusUpdate("Async delete of", self.path, "finished")
            except Exception as e:
                warningMessage("Could not remove directory", self.path, e)

    def asyncCleanDirectory(self, path: Path, *, keepRoot=False) -> ThreadJoiner:
        """
        Delete a directory in the background (e.g. deleting the cheribsd build directory delays the build
        with self.asyncCleanDirectory("foo")
            # foo has been moved to foo.tmp and foo is now and empty dir:
            do_something()
        # now foo.tpt no longer exists
        :param path: the directory to clean
        :param keepRoot: currently not supported
        :return:
        """
        deleterThread = None
        tempdir = path.with_suffix(".delete-me-pls")
        if keepRoot:
            # TODO: mkdir tempdir, move stuff there, then rm -rf tempdir
            warningMessage("Cannot asynchronously delete with keepRoot yet, deleting", path, "in foreground")
            self.cleanDirectory(path, keepRoot=keepRoot)
        elif not path.is_dir():
            self.makedirs(path)
        elif len(list(path.iterdir())) == 0:
            statusUpdate("Not cleaning", path, "it is already empty")
        else:
            if tempdir.is_dir():
                warningMessage("Previous async cleanup of ", path, "failed. Cleaning up now")
                self._deleteDirectories(tempdir)
            # rename the directory, create a new dir and then delete it in a background thread
            runCmd("mv", path, tempdir)
            self.makedirs(path)
        if tempdir.is_dir() or self.config.pretend:
            # we now have an empty directory, start background deleter and return to caller
            deleterThread = SimpleProject.DeleterThread(self, tempdir)
        return ThreadJoiner(deleterThread)

    def deleteFile(self, file: Path, printVerboseOnly=False):
        printCommand("rm", "-f", file, printVerboseOnly=printVerboseOnly)
        if not file.is_file() or self.config.pretend:
            return
        file.unlink()

    def copyRemoteFile(self, remotePath: str, targetFile: Path):
        # if we have rsync we can skip the copy if file is already up-to-date
        if shutil.which("rsync"):
            runCmd("rsync", "-aviu", "--progress", remotePath, targetFile)
        else:
            runCmd("scp", remotePath, targetFile)

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
        self.makedirs(file.parent)
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
            self.makedirs(dest.parent)
        if dest.is_symlink():
            dest.unlink()
        shutil.copy(str(src), str(dest), follow_symlinks=False)

    @staticmethod
    def _handleStdErr(outfile, stream, fileLock, project: "Project"):
        for errLine in stream:
            with fileLock:
                # noinspection PyProtectedMember
                if project._lastStdoutLineCanBeOverwritten:
                    sys.stdout.buffer.write(b"\n")
                    sys.stdout.buffer.flush()
                    project._lastStdoutLineCanBeOverwritten = False
                sys.stderr.buffer.write(errLine)
                sys.stderr.buffer.flush()
                if not project.config.noLogfile:
                    outfile.write(errLine)

    def _lineNotImportantStdoutFilter(self, line: bytes):
        # by default we don't keep any line persistent, just have updating output
        if self._lastStdoutLineCanBeOverwritten:
            sys.stdout.buffer.write(Project.clearLineSequence)
        sys.stdout.buffer.write(line[:-1])  # remove the newline at the end
        sys.stdout.buffer.write(b" ")  # add a space so that there is a gap before error messages
        sys.stdout.buffer.flush()
        self._lastStdoutLineCanBeOverwritten = True

    def _showLineStdoutFilter(self, line: bytes):
        if self._lastStdoutLineCanBeOverwritten:
            sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
        self._lastStdoutLineCanBeOverwritten = False

    def _stdoutFilter(self, line: bytes):
        self._lineNotImportantStdoutFilter(line)

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
                    sys.stdout.buffer.flush()
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

    @staticmethod
    def createBuildtoolTargetSymlinks(tool: Path, toolName: str=None, createUnprefixedLink: bool=False, cwd: str=None):
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

    def process(self):
        raise NotImplementedError()


def installDirNotSpecified(config: CheriConfig, project: "Project"):
    raise RuntimeError("dummy impl must not be called: " + str(project))


def _defaultBuildDir(config: CheriConfig, project: "Project"):
    # make sure we have different build dirs for LLVM/CHERIBSD/QEMU 128 and 256
    buildDirSuffix = "-" + config.cheriBitsStr + "-build" if project.appendCheriBitsToBuildDir else "-build"
    return config.buildRoot / (project.projectName.lower() + buildDirSuffix)


class Project(SimpleProject):
    repository = ""
    gitRevision = None
    compileDBRequiresBear = True
    doNotAddToTargets = True

    defaultSourceDir = ConfigLoader.ComputedDefaultValue(
            function=lambda config, project: Path(config.sourceRoot / project.projectName.lower()),
            asString=lambda cls: "$SOURCE_ROOT/" + cls.projectName.lower())

    appendCheriBitsToBuildDir = False
    """ Whether to append -128/-256 to the computed build directory name"""
    defaultBuildDir = ConfigLoader.ComputedDefaultValue(
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

    _installToSDK = ConfigLoader.ComputedDefaultValue(
            function=lambda config, project: config.sdkDir,
            asString="$INSTALL_ROOT/sdk256 or $INSTALL_ROOT/sdk128 depending on CHERI bits")
    _installToBootstrapTools = ConfigLoader.ComputedDefaultValue(
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
                result = runCmd([compiler, "-fuse-ld=lld", "-xc", "-o" "-", "-"], runInPretendMode=True,
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
                                                  " want to update the other projects.", metavar="REVISION")
            cls.repository = cls.addConfigOption("repository", kind=str, help="The URL of the git repository",
                                                 default=cls.repository, metavar="REPOSITORY")
        # TODO: add the gitRevision option

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        self.gitBranch = ""
        # set up the install/build/source directories (allowing overrides from config file)

        if self.config.verbose:
            print(self.projectName, "directories: source=%s, build=%s, install=%s" %
                  (self.sourceDir, self.buildDir, self.installDir))

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
                self._addRequiredSystemTool("gmake")
                self.makeCommand = "gmake"
        else:
            self.makeCommand = "make"

    # Make sure that API is used properly
    def __setattr__(self, name, value):
        # if self.__dict__.get("_locked") and name == "x":
        #     raise AttributeError, "MyClass does not allow assignment to .x member"
        # self.__dict__[name] = value
        if self.__dict__.get("_preventAssign"):
            assert name not in ("sourceDir", "buildDir", "installDir")
            if name in ("configureArgs", "configureEnvironment", "commonMakeArgs"):
                import traceback
                traceback.print_stack()
                fatalError("Project." + name + " mustn't be set, only modification is allowed.", "Called from",
                           self.__class__.__name__)
        self.__dict__[name] = value

    def runGitCmd(self, *args, cwd=None, **kwargs):
        if not cwd:
            cwd = self.sourceDir
        return runCmd("git", *args, cwd=cwd, **kwargs)

    def _ensureGitRepoIsCloned(self, *, srcDir: Path, remoteUrl, initialBranch=None):
        # git-worktree creates a .git file instead of a .git directory so we can't use .is_dir()
        if not (srcDir / ".git").exists():
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

    def runMake(self, args: "typing.List[str]", makeTarget="", *, makeCommand: str=None, logfileName: str=None,
                cwd: Path=None, env=None, appendToLogfile=False, compilationDbName="compile_commands.json",
                stdoutFilter: "typing.Callable[[bytes], None]"="__default_filter__") -> None:
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
            allArgs = [self.config.otherToolsDir / "bin/bear", "--cdb", self.buildDir / compilationDbName,
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
        self.runWithLogfile(allArgs, logfileName=logfileName, stdoutFilter=stdoutFilter, cwd=cwd, env=env,
                            appendToLogfile=appendToLogfile)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", self.makeCommand, makeTarget, "took", time.time() - starttime, "seconds")

    def update(self):
        if not self.repository:
            fatalError("Cannot update", self.projectName, "as it is missing a git URL", fatalWhenPretending=True)
        self._updateGitRepo(self.sourceDir, self.repository, revision=self.gitRevision, initialBranch=self.gitBranch)

    def clean(self) -> ThreadJoiner:
        assert self.config.clean
        # TODO: never use the source dir as a build dir (unfortunately GDB, postgres and elftoolchain won't work)
        # will have to check how well binutils and qemu work there
        if (self.buildDir / ".git").is_dir():
            if (self.buildDir / "GNUmakefile").is_file():
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

    def configure(self):
        if not self.needsConfigure() and not self.config.forceConfigure:
            return
        if self.configureCommand:
            self.runWithLogfile([self.configureCommand] + self.configureArgs,
                                logfileName="configure", cwd=self.buildDir, env=self.configureEnvironment)

    def compile(self):
        self.runMake(self.commonMakeArgs + [self.config.makeJFlag])

    @property
    def makeInstallEnv(self):
        if self.destdir:
            env = os.environ.copy()
            env["DESTDIR"] = str(self.destdir)
            return env
        return None

    def runMakeInstall(self, *, args: list=None, target="install", _stdoutFilter=None, cwd=None):
        if args is None:
            args = self.commonMakeArgs
        self.runMake(args, makeTarget=target, stdoutFilter=_stdoutFilter, env=self.makeInstallEnv, cwd=cwd)

    def install(self, _stdoutFilter=None):
        self.runMakeInstall()

    def process(self):
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
    def setupConfigOptions(cls, **kwargs):
        super().setupConfigOptions(**kwargs)
        cls.cmakeBuildType = cls.addConfigOption("build-type", default=cls.defaultCMakeBuildType, metavar="BUILD_TYPE",
                                                 help="The CMake build type (Debug, RelWithDebInfo, Release)")
        cls.cmakeOptions = cls.addConfigOption("cmake-options", default=[], kind=list, metavar="OPTIONS",
                                               help="Additional command line options to pass to CMake")

    def __init__(self, config, generator=Generator.Ninja):
        super().__init__(config)
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

        if self.installPrefix:
            assert self.destdir, "custom install prefix requires DESTDIR being set!"
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.installPrefix)
        else:
            self.add_cmake_options(CMAKE_INSTALL_PREFIX=self.installDir)

        self.configureArgs.append("-DCMAKE_BUILD_TYPE=" + self.cmakeBuildType)
        # TODO: do it always?
        if self.config.createCompilationDB:
            self.configureArgs.append("-DCMAKE_EXPORT_COMPILE_COMMANDS=ON")
        # Don't add the user provided options here, add them in configure() so that they are put last

    def add_cmake_option(self, option: str, value):
        if isinstance(value, bool):
            value = "ON" if value else "OFF"
        self.configureArgs.append("-D" + option + "=" + str(value))

    def add_cmake_options(self, **kwargs):
        for k, v in kwargs.items():
            self.add_cmake_option(k, v)

    def _cmakeInstallStdoutFilter(self, line: bytes):
        # don't show the up-to date install lines
        if line.startswith(b"-- Up-to-date:"):
            return
        self._showLineStdoutFilter(line)

    def needsConfigure(self) -> bool:
        if self.config.pretend and self.config.forceConfigure:
            return True
        # CMake is smart enough to detect when it must be reconfigured -> skip configure if cache exists
        cmakeCache = self.buildDir / "CMakeCache.txt"
        return not cmakeCache.exists()

    def configure(self):
        self.configureArgs.extend(self.cmakeOptions)
        # make sure we get a completely fresh cache when --reconfigure is passed:
        cmakeCache = self.buildDir / "CMakeCache.txt"
        if self.config.forceConfigure:
            self.deleteFile(cmakeCache)
        super().configure()

    def install(self, _stdoutFilter="__DEFAULT__"):
        if _stdoutFilter == "__DEFAULT__":
            _stdoutFilter = self._cmakeInstallStdoutFilter
        super().install(_stdoutFilter=_stdoutFilter)

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
        if self.installPrefix:
            assert self.destdir, "custom install prefix requires DESTDIR being set!"
            self.configureArgs.append("--prefix=" + str(self.installPrefix))
        else:
            self.configureArgs.append("--prefix=" + str(self.installDir))
        if self.extraConfigureFlags:
            self.configureArgs.extend(self.extraConfigureFlags)


# A target that does nothing (used for e.g. the "all" target)
class PseudoTarget(SimpleProject):
    doNotAddToTargets = True
    dependenciesMustBeBuilt = True
    hasSourceFiles = False

    def process(self):
        pass


class BuildAll(PseudoTarget):
    dependencies = ["qemu", "sdk", "disk-image", "run"]
