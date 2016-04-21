import os
import shutil
import shlex
import subprocess
import sys
import threading
import time
from .utils import *
from pathlib import Path


class Project(object):
    clearLineSequence = b"\x1b[2K\r"

    def __init__(self, name: str, config: CheriConfig, *, sourceDir: Path=None, buildDir: Path=None,
                 installDir: Path=None, gitUrl="", gitRevision=None, appendCheriBitsToBuildDir=False):
        self.name = name
        self.gitUrl = gitUrl
        self.gitRevision = gitRevision
        self.gitBranch = ""
        self.config = config
        self.sourceDir = Path(sourceDir if sourceDir else config.sourceRoot / name)
        # make sure we have different build dirs for LLVM/CHERIBSD/QEMU 128 and 256,
        buildDirSuffix = "-" + config.cheriBitsStr + "-build" if appendCheriBitsToBuildDir else "-build"
        self.buildDir = Path(buildDir if buildDir else config.outputRoot / (name + buildDirSuffix))
        self.installDir = installDir
        self.makeCommand = "make"
        self.configureCommand = ""
        self.configureArgs = []  # type: typing.List[str]
        self.configureEnvironment = None  # type: typing.Dict[str,str]

        # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line

    def queryYesNo(self, message: str="", *, defaultResult=False, forceResult=True) -> bool:
        yesNoStr = " [Y]/n " if defaultResult else " y/[N] "
        if self.config.pretend:
            print(message + yesNoStr)
            return True  # in pretend mode we always return true
        if self.config.force:
            # in force mode we always return the forced result without prompting the user
            print(message + yesNoStr, "y")
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

    def _updateGitRepo(self, srcDir: Path, remoteUrl, *, revision=None, initialBranch=None):
        if not (srcDir / ".git").is_dir():
            print(srcDir, "is not a git repository. Clone it from' " + remoteUrl + "'?", end="")
            if not self.queryYesNo(defaultResult=False):
                fatalError("Sources for", str(srcDir), " missing!")
            if initialBranch:
                runCmd("git", "clone", "--branch", initialBranch, remoteUrl, srcDir)
            else:
                runCmd("git", "clone", remoteUrl, srcDir)
        # make sure we run git stash if we discover any local changes
        hasChanges = len(runCmd("git", "diff", captureOutput=True, cwd=srcDir, printVerboseOnly=True).stdout) > 1
        if hasChanges:
            runCmd("git", "stash", cwd=srcDir, printVerboseOnly=True)
        runCmd("git", "pull", "--rebase", cwd=srcDir, printVerboseOnly=True)
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

    def writeFile(self, file: Path, contents: str):
        printCommand("echo", contents, colour=AnsiColour.green, outputFile=file, printVerboseOnly=True)
        if self.config.pretend:
            return
        with file.open("w", encoding="utf-8") as f:
            return f.write(contents)

    def copyFile(self, src: Path, dest: Path, *, force=False):
        if force:
            printCommand("cp", "-f", src, dest, printVerboseOnly=True)
        else:
            printCommand("cp", src, dest, printVerboseOnly=True)
        if self.config.pretend:
            return
        if dest.exists() and force:
            dest.unlink()
        shutil.copy(str(src), str(dest), follow_symlinks=False)

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
            statusUpdate("Configuring", self.name, "... ")
            self.runWithLogfile([self.configureCommand] + self.configureArgs,
                                logfileName="configure", cwd=self.buildDir, env=self.configureEnvironment)

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

    def runMake(self, args: "typing.List[str]", makeTarget="", *, cwd: Path=None, env=None) -> None:
        if makeTarget:
            allArgs = args + [makeTarget]
            logfileName = self.makeCommand + "." + makeTarget
        else:
            allArgs = args
            logfileName = "build"
        if not cwd:
            cwd = self.buildDir
        starttime = time.time()
        self.runWithLogfile(allArgs, logfileName=logfileName, stdoutFilter=self._makeStdoutFilter, cwd=cwd, env=env)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", self.makeCommand, makeTarget, "took", time.time() - starttime, "seconds")

    def runWithLogfile(self, args: "typing.Sequence[str]", logfileName: str, *, stdoutFilter=None, cwd: Path = None,
                       env=None) -> None:
        """
        Runs make and logs the output
        config.quiet doesn't display anything, normal only status updates and config.verbose everything
        :param args: the command to run (e.g. ["make", "-j32"])
        :param logfileName: the name of the logfile (e.g. "build.log")
        :param cwd the directory to run make in (defaults to self.buildDir)
        :param stdoutFilter a filter to use for standard output (a function that takes a single bytes argument)
        :param env the environment to pass to make
        """
        printCommand(args, cwd=cwd)
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

        if logfilePath.is_file():
            logfilePath.unlink()  # remove old logfile
        args = list(map(str, args))  # make sure all arguments are strings
        cmdStr = " ".join([shlex.quote(s) for s in args])
        # open file in append mode
        with logfilePath.open("ab") as logfile:
            # print the command and then the logfile
            logfile.write(cmdStr.encode("utf-8") + b"\n\n")
            if self.config.quiet:
                # a lot more efficient than filtering every line
                subprocess.check_call(args, cwd=str(cwd), stdout=logfile, stderr=logfile, env=env)
                return
            make = subprocess.Popen(args, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
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

    @staticmethod
    def createBuildtoolTargetSymlinks(tool: Path, toolName: str=None):
        """
        Create mips4-unknown-freebsd, cheri-unknown-freebsd and mips64-unknown-freebsd prefixed symlinks
        for build tools like clang, ld, etc.
        :param tool: the binary for which the symlinks will be created
        :param toolName: the unprefixed name of the tool (defaults to tool.name)
        """
        if not tool.is_file():
            fatalError("Attempting to creat symlink to non-existent build tool:", tool)
        if not toolName:
            toolName = tool.name
        for target in ("mips4-unknown-freebsd-", "cheri-unknown-freebsd-", "mips64-unknown-freebsd-"):
            if (target + toolName) == tool.name:
                continue  # happens for binutils, where prefixed tools are installed
            runCmd("ln", "-fsn", tool.name, target + toolName, cwd=tool.parent, printVerboseOnly=True)

    def compile(self):
        self.runMake([self.makeCommand, self.config.makeJFlag])

    def install(self):
        self.runMake([self.makeCommand], "install")

    def process(self):
        if not self.config.skipUpdate:
            self.update()
        if self.config.clean:
            self.clean()
        # always make sure the build dir exists
        if not self.buildDir.is_dir():
            self._makedirs(self.buildDir)
        if not self.config.skipConfigure:
            self.configure()
        statusUpdate("Building", self.name, "... ")
        self.compile()
        statusUpdate("Installing", self.name, "... ")
        self.install()

