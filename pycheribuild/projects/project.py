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
import copy
import io
import inspect
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import errno
import sys
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from copy import deepcopy

from ..config.loader import ConfigLoaderBase, ComputedDefaultValue, ConfigOptionBase
from ..config.chericonfig import CheriConfig, CrossCompileTarget
from ..targets import Target, MultiArchTarget, MultiArchTargetAlias, targetManager
from ..filesystemutils import FileSystemUtils
from ..utils import *

__all__ = ["Project", "CMakeProject", "AutotoolsProject", "TargetAlias", "TargetAliasWithDependencies", # no-combine
           "SimpleProject", "CheriConfig", "flushStdio", "MakeOptions", "MakeCommandKind", "Path",  # no-combine
           "CrossCompileTarget"]  # no-combine


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

def _default_stdout_filter(arg: bytes):
    raise NotImplementedError("Should never be called, this is a dummy")


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
        if hasattr(cls, "supported_architectures") and cls.supported_architectures is not None:
            # Add a the target for the default architecture
            base_target = MultiArchTargetAlias(targetName, cls)
            targetManager.addTarget(base_target)
            # TODO: make this hold with CheriBSD
            # assert cls._crossCompileTarget is None, "Should not be set!"
            # assert cls._should_not_be_instantiated, "multiarch base classes should not be instantiated"
            for arch in cls.supported_architectures:
                assert isinstance(arch, CrossCompileTarget)
                # create a new class to ensure different build dirs and config name strings
                new_name = targetName + "-" + arch.value
                new_dict = cls.__dict__.copy()
                new_dict["_crossCompileTarget"] = arch
                new_dict["_should_not_be_instantiated"] = False  # unlike the subclass we can instantiate these
                new_dict["doNotAddToTargets"] = True  # We are already adding it here
                new_dict["target"] = new_name
                new_dict["synthetic_base"] = cls  # We are already adding it here
                new_type = type(cls.__name__ + "_" + arch.name, (cls,) + cls.__bases__, new_dict)
                targetManager.addTarget(MultiArchTarget(new_name, new_type, arch, base_target))
        else:
            # Only one target is supported:
            targetManager.addTarget(Target(targetName, cls))
        # print("Adding target", targetName, "with deps:", cls.dependencies)


class SimpleProject(FileSystemUtils, metaclass=ProjectSubclassDefinitionHook):
    _configLoader = None  # type: ConfigLoaderBase

    # These two class variables can be defined in subclasses to customize dependency ordering of targets
    target = ""  # type: str
    projectName = None
    dependencies = []  # type: typing.List[str]
    dependenciesMustBeBuilt = False
    isAlias = False
    is_sdk_target = False  # for --skip-sdk
    sourceDir = None
    buildDir = None
    build_in_source_dir = False # For projects that can't build in the source dir
    installDir = None
    # Whether to hide the options from the default --help output (only add to --help-hidden)
    hide_options_from_help = False
    # To check that we don't create an crosscompile targets without a fixed target
    _should_not_be_instantiated = False
    __cached_deps = None  # type: typing.List[Target]

    @classmethod
    def allDependencyNames(cls, config: CheriConfig) -> "typing.List[str]":
        return [t.name for t in cls.recursive_dependencies(config)]

    @classmethod
    def needs_cheribsd_sysroot(cls, target: CrossCompileTarget):
        # Only CrossCompileProjects will need the sysroot
        return False

    @classmethod
    def direct_dependencies(cls, config: CheriConfig) -> "typing.Generator[Target]":
        dependencies = cls.dependencies
        expected_build_arch = cls.get_crosscompile_target(config)
        if callable(dependencies):
            dependencies = dependencies(cls, config)
        for dep_name in dependencies:
            if callable(dep_name):
                dep_name = dep_name(cls, config)
            # Handle --include-dependencies with --skip-sdk is passed
            dep_target = targetManager.get_target_raw(dep_name)
            if config.skipSdk and dep_target.projectClass.is_sdk_target:
                if config.verbose:
                    statusUpdate("Not adding ", cls.target, "dependency", dep_target.name,
                                 "since it is an SDK target and --skip-sdk was passed.")
                continue
            # Now find the actual crosscompile targets for target aliases:
            if isinstance(dep_target, MultiArchTargetAlias):
                # Find the correct dependency (e.g. libcxx-native should depend on libcxxrt-native)
                # try to find a better match:
                for tgt in dep_target.derived_targets:
                    if tgt.target_arch == expected_build_arch:
                        dep_target = tgt
                        # print("Overriding with", tgt.name)
            assert not isinstance(dep_target, MultiArchTargetAlias), "All targets should be fully resolved: " + cls.__name__
            yield dep_target


    @classmethod
    def recursive_dependencies(cls, config: CheriConfig) -> "typing.List[Target]":
        if cls.__cached_deps:
            return cls.__cached_deps
        result = []  # type: typing.List[Target]
        for target in cls.direct_dependencies(config):
            if target not in result:
                result.append(target)
            # now recursively add the other deps:
            recursive_deps = target.projectClass.recursive_dependencies(config)
            for r in recursive_deps:
                if r not in result:
                    result.append(r)
        cls.__cached_deps = result
        return result

    @classmethod
    def _cached_dependencies(cls) -> "typing.List[Target]":
        assert cls.__cached_deps is not None, "_cached_dependencies called before allDependencyNames()"
        return cls.__cached_deps

    @classmethod
    def get_instance(cls: "typing.Type[Type_T]", caller: "typing.Optional[SimpleProject]", config: CheriConfig) -> "Type_T":
        # TODO: assert that target manager has been initialized
        cross_target = None
        if caller is not None:
            cross_target = caller.get_crosscompile_target(config)
        return cls.get_instance_for_cross_target(cross_target, config)

    @classmethod
    def get_instance_for_cross_target(cls: "typing.Type[Type_T]", cross_target: CrossCompileTarget,
                                      config: CheriConfig) -> "Type_T":
        target = targetManager.get_target(cls.target, cross_target, config)
        result = target.get_or_create_project(cross_target, config)
        return result

    @classmethod
    def get_crosscompile_target(cls, config: CheriConfig) -> "typing.Optional[CrossCompileTarget]":
        return None  # XXX: does it make sense to return NATIVE instead? Will break stuff for little gain I guess

    # Project subclasses will automatically have a target based on their name generated unless they add this:
    doNotAddToTargets = True

    # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
    # However, if the output is just a plain text file don't attempt to do any line clearing
    _clearLineSequence = b"\x1b[2K\r" if sys.__stdout__.isatty() else b"\n"

    __commandLineOptionGroup = None

    @classmethod
    def addConfigOption(cls, name: str, default: "typing.Union[Type_T, typing.Callable[[], Type_T]]" = None,
                        kind: "typing.Union[typing.Type[str], typing.Callable[[str], Type_T]]" = str, *,
                        showHelp=False, shortname=None, _no_fallback_config_name: bool=False,
                        fallback_config_name: str=None, **kwargs) -> "Type_T":
        configOptionKey = cls.target
        # if cls.target != cls.projectName.lower():
        #    self.fatal("Target name does not match project name:", cls.target, "vs", cls.projectName.lower())

        # Hide stuff like --foo/install-directory from --help
        helpHidden = not showHelp

        # check that the group was defined in the current class not a superclass
        if "_commandLineOptionGroup" not in cls.__dict__:
            # noinspection PyProtectedMember
            # has to be a single underscore otherwise the name gets mangled to _Foo__commandlineOptionGroup
            cls._commandLineOptionGroup = cls._configLoader._parser.add_argument_group(
                "Options for target '" + cls.target + "'")
        # For targets such as qtbase-mips we want to fall back to checking the value of the option for qtbase
        fallback_name_base = getattr(cls, "_config_inherits_from", None)
        synthetic_base = getattr(cls, "synthetic_base", None)
        if cls.hide_options_from_help:
            helpHidden = True
        if synthetic_base is not None:
            # Don't show the help options for qtbase-mips/qtbase-native/qtbase-cheri in default --help output, the
            # base version is enough. They will still be included in --help-all
            helpHidden = True
            fallback_name_base = synthetic_base.target

        # We don't want to inherit certain options from the non-target specific class since they should always be
        # set directly for that target. Currently the only such option is build-directory since sharing that would
        # break the build in most cases.
        if not _no_fallback_config_name and fallback_name_base:
            if name not in ["build-directory"]:
                fallback_config_name = fallback_name_base + "/" + name
            elif synthetic_base is not None:
                from .cross.multiarchmixin import MultiArchBaseMixin
                assert issubclass(cls, MultiArchBaseMixin)
                # build-directory should only be inherited for the default target (e.g. cheribsd-cheri -> cheribsd):
                if cls.default_architecture is not None and cls.default_architecture == cls._crossCompileTarget:
                    # Don't allow cheribsd-purecap/build-directory to fall back to cheribsd/build-directory
                    # but if the projectName is the same we can assume it's the same class:
                    if cls.projectName == synthetic_base.projectName:
                        fallback_config_name = fallback_name_base + "/" + name
        return cls._configLoader.addOption(configOptionKey + "/" + name, shortname, default=default, type=kind,
                                           _owningClass=cls, group=cls._commandLineOptionGroup, helpHidden=helpHidden,
                                           _fallback_name=fallback_config_name, **kwargs)

    @classmethod
    def addBoolOption(cls, name: str, *, shortname=None, default=False, **kwargs):
        # noinspection PyTypeChecker
        return cls.addConfigOption(name, default=default, kind=bool, shortname=shortname, action="store_true", **kwargs)

    @classmethod
    def addPathOption(cls, name: str, *, shortname=None, **kwargs):
        return cls.addConfigOption(name, kind=Path, shortname=shortname, **kwargs)

    __configOptionsSet = dict()  # typing.Dict[Type, bool]

    @classmethod
    def setupConfigOptions(cls, **kwargs):
        # assert cls not in cls.__configOptionsSet, "Setup called twice?"
        cls.__configOptionsSet[cls] = True

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        assert not self._should_not_be_instantiated, "Should not have instantiated " + self.__class__.__name__
        assert self.__class__ in self.__configOptionsSet, "Forgot to call super().setupConfigOptions()? " + str(self.__class__)
        self.__requiredSystemTools = {}  # type: typing.Dict[str, typing.Any]
        self.__requiredSystemHeaders = {}  # type: typing.Dict[str, typing.Any]
        self.__requiredPkgConfig = {}  # type: typing.Dict[str, typing.Any]
        self._systemDepsChecked = False
        if self.build_in_source_dir:
            self.verbose_print("Cannot build", self.projectName, "in a separate build dir, will build in", self.sourceDir)
            self.buildDir = self.sourceDir


    def _addRequiredSystemTool(self, executable: str, installInstructions=None, freebsd: str=None, apt: str=None,
                               zypper: str=None, homebrew: str=None, cheribuild_target: str=None):
        if not installInstructions:
            installInstructions = OSInfo.install_instructions(executable, False, freebsd=freebsd, zypper=zypper, apt=apt,
                                                              homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__requiredSystemTools[executable] = installInstructions

    def _addRequiredPkgConfig(self, package: str, install_instructions=None, freebsd: str=None, apt: str = None,
                              zypper: str=None, homebrew: str=None, cheribuild_target: str=None):
        self._addRequiredSystemTool("pkg-config", freebsd="pkgconf", homebrew="pkg-config", apt="pkg-config", )
        if not install_instructions:
            install_instructions = OSInfo.install_instructions(package, True, freebsd=freebsd, zypper=zypper, apt=apt,
                                                               homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__requiredPkgConfig[package] = install_instructions

    def _addRequiredSystemHeader(self, header: str, install_instructions=None, freebsd: str=None, apt: str = None,
                              zypper: str=None, homebrew: str=None, cheribuild_target: str=None):
        self._addRequiredSystemTool("pkg-config", freebsd="pkgconf", homebrew="pkg-config", apt="pkg-config", )
        if not install_instructions:
            install_instructions = OSInfo.install_instructions(header, True, freebsd=freebsd, zypper=zypper, apt=apt,
                                                               homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__requiredSystemHeaders[header] = install_instructions

    def queryYesNo(self, message: str = "", *, defaultResult=False, forceResult=True, yesNoStr: str=None) -> bool:
        if yesNoStr is None:
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
                try:
                    # noinspection PyProtectedMember
                    if project._lastStdoutLineCanBeOverwritten:
                        sys.stdout.buffer.write(b"\n")
                        flushStdio(sys.stdout)
                        project._lastStdoutLineCanBeOverwritten = False
                    sys.stderr.buffer.write(errLine)
                    flushStdio(sys.stderr)
                    if not project.config.noLogfile:
                        outfile.write(errLine)
                except ValueError:
                    # Don't print a backtrace on ctrl+C (since that will exit the main thread and close the file)
                    # ValueError: write to closed file
                    continue


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

    def __runProcessWithFilteredOutput(self, proc: subprocess.Popen, logfile: "typing.Optional[typing.IO]",
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
        if callable(installInstructions):
            installInstructions = installInstructions()
        self.fatal("Dependency for", self.target, "missing:", *args, fixitHint=installInstructions)

    def checkSystemDependencies(self) -> None:
        """
        Checks that all the system dependencies (required tool, etc) are available
        :return: Throws an error if dependencies are missing
        """
        for (tool, installInstructions) in self.__requiredSystemTools.items():
            if not shutil.which(str(tool)):
                if installInstructions is None or installInstructions == "":
                    installInstructions = "Try installing `" + tool + "` using your system package manager."
                self.dependencyError("Required program", tool, "is missing!", installInstructions=installInstructions)
        for (package, instructions) in self.__requiredPkgConfig.items():
            if not shutil.which("pkg-config"):
                # error should already have printed above
                break
            check_cmd = ["pkg-config", "--exists", package]
            printCommand(check_cmd, printVerboseOnly=True)
            exit_code = subprocess.call(check_cmd)
            if exit_code != 0:
                self.dependencyError("Required library", package, "is missing!", installInstructions=instructions)
        for (header, instructions) in self.__requiredSystemHeaders.items():
            if not Path("/usr/include", header).exists() and not Path("/usr/local/include", header).exists():
                self.dependencyError("Required C header", header, "is missing!", installInstructions=instructions)
        self._systemDepsChecked = True

    def process(self):
        raise NotImplementedError()

    def run_tests(self):
        # for the --test option
        statusUpdate("No tests defined for target", self.target)
        pass

    def run_cheribsd_test_script(self, script_name, *script_args, kernel_path=None, disk_image_path=None,
                                 mount_builddir=True):
        from .build_qemu import BuildQEMU
        # noinspection PyUnusedLocal
        script_dir = Path("/this/will/not/work/when/using/remote-cheribuild.py")
        if kernel_path is None:
            from .cross.cheribsd import BuildCheriBsdMfsKernel
            kernel_path = BuildCheriBsdMfsKernel.get_installed_kernel_path(self, self.config)
            if not kernel_path.exists():
                cheribsd_image = "cheribsd{suffix}-cheri{suffix}-malta64-mfs-root-minimal-cheribuild-kernel.bz2".format(
                        suffix="" if self.config.cheriBits == 256 else self.config.cheriBitsStr)
                freebsd_image = "freebsd-malta64-mfs-root-minimal-cheribuild-kernel.bz2"
                if self.get_crosscompile_target(self.config) == CrossCompileTarget.MIPS:
                    guessed_archive = cheribsd_image if self.config.run_mips_tests_with_cheri_image else freebsd_image
                elif self.get_crosscompile_target(self.config) == CrossCompileTarget.CHERI:
                    guessed_archive = cheribsd_image
                else:
                    self.fatal("Could not guess path to kernel image for CheriBSD")
                    guessed_archive = "invalid path"
                jenkins_kernel_path = self.config.cheribsd_image_root / guessed_archive
                if jenkins_kernel_path.exists():
                    kernel_path = jenkins_kernel_path
                else:
                    self.fatal("Could not find kernel image", kernel_path, "and jenkins path", jenkins_kernel_path,
                               "is also missing")
        # generate a sensible error when using remote-cheribuild.py by omitting this line:
        script_dir = Path(__file__).parent.parent.parent / "test-scripts"   # no-combine
        script = script_dir / script_name
        if not script.exists():
            self.fatal("Could not find test script", script)
        qemu_path = BuildQEMU.qemu_binary(self)
        if not qemu_path.exists():
            self.fatal("QEMU binary", qemu_path, "doesn't exist")
        cmd = [script, "--kernel", kernel_path,
               "--qemu-cmd", qemu_path,
               "--ssh-key", self.config.test_ssh_key] + list(script_args)
        if self.buildDir and mount_builddir:
            cmd.extend(["--build-dir", self.buildDir])
        if disk_image_path:
            cmd.extend(["--disk-image", disk_image_path])
        if self.config.tests_interact:
            cmd.append("--interact")
        if self.config.test_extra_args:
            cmd.extend(map(str, self.config.test_extra_args))
        runCmd(cmd)

    def runShellScript(self, script, shell="sh", **kwargs):
        print_args = dict(**kwargs)
        if "captureOutput" in print_args:
            del print_args["captureOutput"]
        printCommand(shell, "-xe" if self.config.verbose else "-e", "-c", script, **print_args)
        kwargs["no_print"] = True
        return runCmd(shell, "-xe" if self.config.verbose else "-e", input=script, **kwargs)

    def print(self, *args, **kwargs):
        if not self.config.quiet:
            print(*args, **kwargs)

    def verbose_print(self, *args, **kwargs):
        if self.config.verbose:
            print(*args, **kwargs)

    @staticmethod
    def info(*args, **kwargs):
        # TODO: move all those methods here
        statusUpdate(*args, **kwargs)

    @staticmethod
    def warning(*args, **kwargs):
        warningMessage(*args, **kwargs)

    @staticmethod
    def fatal(*args, sep=" ", fixitHint=None, fatalWhenPretending=False):
        fatalError(*args, sep=sep, fixitHint=fixitHint, fatalWhenPretending=fatalWhenPretending)


def installDirNotSpecified(config: CheriConfig, project: "Project"):
    raise RuntimeError("installDirNotSpecified! dummy impl must not be called: " + str(project))


def _defaultBuildDir(config: CheriConfig, project: "Project"):
    # make sure we have different build dirs for LLVM/CHERIBSD/QEMU 128 and 256
    target = project.get_crosscompile_target(config)
    return project.buildDirForTarget(config, target)


class MakeCommandKind(Enum):
    DefaultMake = "system default make"
    GnuMake = "GNU make"
    BsdMake = "BSD make"
    Ninja = "ninja"
    CustomMakeTool = "custom make tool"


class MakeOptions(object):
    def __init__(self, kind: MakeCommandKind, project: SimpleProject, **kwargs):
        self.__project = project
        self._vars = OrderedDict()
        # Used by e.g. FreeBSD:
        self._with_options = OrderedDict()  # type: typing.Dict[str, bool]
        self._flags = list()
        self.env_vars = {}
        self.set(**kwargs)
        self.kind = kind
        self.__can_pass_j_flag = None  # type: typing.Optional[bool]
        self.__command = None  # type: typing.Optional[str]
        self.install_instructions = None  # type: typing.Optional[str]

    def __deepcopy__(self, memo):
        assert False, "Should not be called!"
        pass

    def __do_set(self, target_dict: "typing.Dict[str, str]", **kwargs):
        for k, v in kwargs.items():
            if isinstance(v, bool):
                v = "1" if v else "0"
            target_dict[k] = str(v)

    def set(self, **kwargs):
        self.__do_set(self._vars, **kwargs)

    def set_env(self, **kwargs):
        self.__do_set(self.env_vars, **kwargs)

    def set_with_options(self, **kwargs):
        """
        For every argument in kwargs sets a WITH_FOO if FOO=True or a WITHOUT_FOO if FOO=False
        Used by the FreeBSD build sysmtem: e.g. make -DWITH_MAN / -DWITHOUT_MAN
        :return: dict of VAR=True/False
        """
        for k, v in kwargs.items():
            assert isinstance(v, bool)
            self._with_options[k] = v

    def add_flags(self, *args) -> None:
        """
        :param args: the flags to add (e.g. -j 16, etc.)
        """
        self._flags.extend(args)

    def _get_defined_var(self, name) -> str:
        # BSD make supports a -DVAR syntax but GNU doesn't
        if self.kind == MakeCommandKind.BsdMake:
            return "-D" + name
        else:
            assert self.kind == MakeCommandKind.GnuMake
            return name + "=1"

    @property
    def is_gnu_make(self):
        if self.kind == MakeCommandKind.GnuMake:
            return True
        if self.kind != MakeCommandKind.DefaultMake:
            return False
        # otherwise parse make --version
        print("IS GNU MAKE: ", b"GNU Make" in get_version_output(self.command))
        return b"GNU Make" in get_version_output(self.command)

    @property
    def command(self) -> str:
        # Don't cache this value in case the user changes the kind
        if self.__command is not None:
            return self.__command
        cmd = self.__infer_command()
        assert self.kind == MakeCommandKind.CustomMakeTool or not Path(cmd).is_absolute()
        return cmd

    # noinspection PyProtectedMember
    def __infer_command(self) -> str:
        if self.kind == MakeCommandKind.DefaultMake:
            self.__project._addRequiredSystemTool("make")
            return "make"
        elif self.kind == MakeCommandKind.GnuMake:
            if IS_LINUX and not shutil.which("gmake"):
                statusUpdate("Could not find `gmake` command, assuming `make` is GNU make")
                self.__project._addRequiredSystemTool("make")
                return "make"
            else:
                self.__project._addRequiredSystemTool("gmake", homebrew="make")
                return "gmake"
        elif self.kind == MakeCommandKind.BsdMake:
            if IS_FREEBSD:
                return "make"
            else:
                self.__project._addRequiredSystemTool("bmake", homebrew="bmake", cheribuild_target="bmake")
                return "bmake"
        elif self.kind == MakeCommandKind.Ninja:
            self.__project._addRequiredSystemTool("ninja", homebrew="ninja", apt="ninja-build")
            return "ninja"
        else:
            if self.__command is not None:
                return self.__command
            self.fatal("Cannot infer path from CustomMakeTool. Set self.make_args.set_command(\"tool\")")
            raise RuntimeError()

    def set_command(self, value, can_pass_j_flag=True, **kwargs):
        self.__command = str(value)
        # noinspection PyProtectedMember
        if not Path(value).is_absolute():
            self.__project._addRequiredSystemTool(value, **kwargs)
        self.__can_pass_j_flag = can_pass_j_flag

    @property
    def all_commandline_args(self) -> list:
        assert self.kind
        result = []
        # First all the variables
        for k, v in self._vars.items():
            assert isinstance(v, str)
            if v == "1":
                result.append(self._get_defined_var(k))
            else:
                result.append(k + "=" + v)
        # then the WITH/WITHOUT variables
        for k, v in self._with_options.items():
            result.append(self._get_defined_var("WITH_" if v else "WITHOUT_") + k)
        # and finally the command line flags like -k
        result.extend(self._flags)
        return result

    def remove_var(self, variable):
        if variable in self._vars:
            del self._vars[variable]
        if variable in self._with_options:
            del self._with_options[variable]
        for flag in self._flags.copy():
            if flag.strip() == "-D" + variable or flag.startswith(variable + "="):
                self._flags.remove(flag)

    def remove_flag(self, flag: str):
        if flag in self._flags:
            self._flags.remove(flag)

    def remove_all(self, predicate: "typing.Callable[bool, [str]]"):
        keys = list(self._vars.keys())
        for k in keys:
            if predicate(k):
                del self._vars[k]

    def copy(self):
        result = copy.copy(self)

        # Make sure that the list and dict objects are different
        result._vars = copy.deepcopy(self._vars)
        result._with_options = copy.deepcopy(self._with_options)
        result._flags = copy.deepcopy(self._flags)
        result.env_vars = copy.deepcopy(self.env_vars)
        return result

    def update(self, other: "MakeOptions"):
        self._vars.update(other._vars)
        self._with_options.update(other._with_options)
        self._flags.extend(other._flags)
        self.env_vars.update(other.env_vars)

    @property
    def can_pass_jflag(self):
        if self.__can_pass_j_flag is not None:
            return self.__can_pass_j_flag
        return self.kind != MakeCommandKind.CustomMakeTool


class Project(SimpleProject):
    repository = ""
    gitRevision = None
    gitBranch = ""
    skipGitSubmodules = False
    compileDBRequiresBear = True
    doNotAddToTargets = True
    build_dir_suffix = ""   # add a suffix to the build dir (e.g. for freebsd-with-bootstrap-clang)

    defaultSourceDir = ComputedDefaultValue(
        function=lambda config, project: Path(config.sourceRoot / project.projectName.lower()),
        asString=lambda cls: "$SOURCE_ROOT/" + cls.projectName.lower())

    appendCheriBitsToBuildDir = False
    """ Whether to append -128/-256 to the computed build directory name"""

    @classmethod
    def projectBuildDirHelpStr(cls):
        result = "$BUILD_ROOT/" + cls.projectName.lower()
        if cls.appendCheriBitsToBuildDir or hasattr(cls, "crossCompileTarget"):
            result += "-$TARGET"
        result += "-build"
        return result

    defaultBuildDir = ComputedDefaultValue(
        function=_defaultBuildDir, asString=lambda cls: cls.projectBuildDirHelpStr())

    make_kind = MakeCommandKind.DefaultMake
    """
    The kind of too that is used for building and installing (defaults to using "make")
    Set this to MakeCommandKind.GnuMake if the build system needs GNU make features or BsdMake if it needs bmake
    """

    # A per-project config option to generate a CMakeLists.txt that just has a custom taget that calls cheribuild.py
    generate_cmakelists = None

    # TODO: remove these three
    @classmethod
    def getSourceDir(cls, caller: "SimpleProject", config: CheriConfig):
        return cls.get_instance(caller, config).sourceDir

    @classmethod
    def getBuildDir(cls, caller: "SimpleProject", config: CheriConfig):
        return cls.get_instance(caller, config).buildDir

    @classmethod
    def getInstallDir(cls, caller: "SimpleProject", config: CheriConfig):
        return cls.get_instance(caller, config).real_install_root_dir

    @classmethod
    def buildDirSuffix(cls, config: CheriConfig, target: CrossCompileTarget):
        if target is None:
            # HACK since I can't make the class variable in BuildLLVM dynamic
            # TODO: remove once unified SDK is stable
            append_bits = cls.appendCheriBitsToBuildDir
            if cls.target in ("llvm", "qemu") and config.unified_sdk:
                append_bits = False
            result = "-" + config.cheriBitsStr + "-build" if append_bits else "-build"
        elif target == CrossCompileTarget.CHERI:
            result = "-" + config.cheriBitsStr + "-build"
        else:
            result = "-" + target.value + "-build"
        if config.cross_target_suffix:
            result += "-" + config.cross_target_suffix
        if cls.build_dir_suffix:
            result = "-" + cls.build_dir_suffix + result
        return result

    @classmethod
    def buildDirForTarget(cls, config: CheriConfig, target: CrossCompileTarget):
        return config.buildRoot / (cls.projectName.lower() + cls.buildDirSuffix(config, target))

    _installToSDK = ComputedDefaultValue(
        function=lambda config, project: config.sdkDir,
        asString="$INSTALL_ROOT/sdk")
    _installToBootstrapTools = ComputedDefaultValue(
        function=lambda config, project: config.otherToolsDir,
        asString="$INSTALL_ROOT/bootstrap")

    defaultInstallDir = installDirNotSpecified
    """ The default installation directory (will probably be set to _installToSDK or _installToBootstrapTools) """

    # useful for cross compile projects that use a prefix and DESTDIR
    _installPrefix = None
    destdir = None

    __can_use_lld_map = dict()  # type: typing.Dict[Path, bool]

    @classmethod
    def canUseLLd(cls, compiler: Path):
        if IS_MAC:
            return False  # lld does not work on MacOS
        if compiler not in cls.__can_use_lld_map:
            try:
                runCmd([compiler, "-fuse-ld=lld", "-xc", "-o", "/dev/null", "-"], runInPretendMode=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, raiseInPretendMode=True,
                       input="int main() { return 0; }\n", printVerboseOnly=True)
                statusUpdate(compiler, "supports -fuse-ld=lld, linking should be much faster!")
                cls.__can_use_lld_map[compiler] = True
            except subprocess.CalledProcessError:
                statusUpdate(compiler, "does not support -fuse-ld=lld, using slower bfd instead")
                cls.__can_use_lld_map[compiler] = False
        return cls.__can_use_lld_map[compiler]

    @classmethod
    def can_use_lto(cls, ccinfo: CompilerInfo):
        if ccinfo.compiler == "apple-clang":
            return True
        elif ccinfo.compiler == "clang" and ccinfo.version >= (4, 0, 0) and cls.canUseLLd(ccinfo.path):
            return True
        else:
            return False

    def checkSystemDependencies(self):
        # Check that the make command exists (this will also add it to the required system tools)
        if self.make_args.command is None:
            self.fatal("Make command not set!")
        super().checkSystemDependencies()

    @classmethod
    def setupConfigOptions(cls, installDirectoryHelp="", **kwargs):
        super().setupConfigOptions(**kwargs)
        # statusUpdate("Setting up config options for", cls, cls.target)
        cls.sourceDir = cls.addPathOption("source-directory", metavar="DIR", default=cls.defaultSourceDir,
                                          help="Override default source directory for " + cls.projectName)
        cls.buildDir = cls.addPathOption("build-directory", metavar="DIR", default=cls.defaultBuildDir,
                                         help="Override default source directory for " + cls.projectName)

        cls.skipUpdate = cls.addBoolOption("skip-update",
                                           default=ComputedDefaultValue(lambda config, proj: config.skipUpdate,
                                                                        "the value of the global --skip-update option"),
                                           help="Override --skip-update/--no-skip-update for this target only ")

        if not installDirectoryHelp:
            installDirectoryHelp = "Override default install directory for " + cls.projectName
        cls._installDir = cls.addPathOption("install-directory", metavar="DIR", help=installDirectoryHelp,
                                           default=cls.defaultInstallDir)
        if "repository" in cls.__dict__:
            cls.gitRevision = cls.addConfigOption("git-revision", kind=str, help="The git revision to checkout prior to"
                                                                                 " building. Useful if HEAD is broken for one project but you still"
                                                                                 " want to update the other projects.",
                                                  metavar="REVISION")
            cls.repository = cls.addConfigOption("repository", kind=str, help="The URL of the git repository",
                                                 default=cls.repository, metavar="REPOSITORY")
        if "generate_cmakelists" not in cls.__dict__:
            # Make sure not to dereference a parent class descriptor here -> use getattr_static
            option = inspect.getattr_static(cls, "generate_cmakelists")
            # If option is not a fixed bool then we need a command line option:
            if not isinstance(option, bool):
                assert option is None or isinstance(option, ConfigOptionBase)
                assert not issubclass(cls, CMakeProject), "generate_cmakelists option needed -> should not be a CMakeProject"
                cls.generate_cmakelists = cls.addBoolOption("generate-cmakelists",
                                                        help="Generate a CMakeLists.txt that just calls cheribuild. "
                                                             "Useful for IDEs that only support CMake")
            else:
                assert issubclass(cls, CMakeProject), "Should be a CMakeProject: " + cls.__name__

    def __init__(self, config: CheriConfig):
        super().__init__(config)
        # set up the install/build/source directories (allowing overrides from config file)

        self.configureCommand = ""
        # non-assignable variables:
        self.configureArgs = []  # type: typing.List[str]
        self.configureEnvironment = {}  # type: typing.Dict[str,str]
        self._lastStdoutLineCanBeOverwritten = False
        self.make_args = MakeOptions(self.make_kind, self)
        if self.config.create_compilation_db and self.compileDBRequiresBear:
            # CompileDB seems to generate broken compile_commands,json
            if self.make_args.is_gnu_make and False:
                # use compiledb instead of bear for gnu make
                # https://blog.jetbrains.com/clion/2018/08/working-with-makefiles-in-clion-using-compilation-db/
                self._addRequiredSystemTool("compiledb", installInstructions="Run `pip2 install --user compiledb``")
                self._compiledb_tool = "compiledb"
            else:
                self._addRequiredSystemTool("bear", installInstructions="Run `cheribuild.py bear`")
                self._compiledb_tool = "bear"
        self._preventAssign = True

    _no_overwrite_allowed = ("configureArgs", "configureEnvironment", "make_args")

    # Make sure that API is used properly
    def __setattr__(self, name, value):
        # if self.__dict__.get("_locked") and name == "x":
        #     raise AttributeError, "MyClass does not allow assignment to .x member"
        # self.__dict__[name] = value
        if self.__dict__.get("_preventAssign"):
            # assert name not in ("sourceDir", "buildDir", "installDir")
            assert name != "installDir", "installDir should not be modified, only _installDir or _installPrefix"
            assert name != "installPrefix", "installPrefix should not be modified, only _installDir or _installPrefix"
            if name in self._no_overwrite_allowed:
                import traceback
                traceback.print_stack()
                raise RuntimeError(self.__class__.__name__ + "." + name + " mustn't be set. Called from" +
                                   self.__class__.__name__)
        self.__dict__[name] = value

    def _ensureGitRepoIsCloned(self, *, srcDir: Path, remoteUrl, initialBranch=None, skipSubmodules=False):
        # git-worktree creates a .git file instead of a .git directory so we can't use .is_dir()
        if not (srcDir / ".git").exists():
            if self.config.skipClone:
                self.fatal("Sources for", str(srcDir), " missing!")
            print(srcDir, "is not a git repository. Clone it from' " + remoteUrl + "'?", end="")
            if not self.queryYesNo(defaultResult=False):
                self.fatal("Sources for", str(srcDir), " missing!")
            cloneCmd = ["git", "clone", "--depth", "1"]
            if not skipSubmodules:
                cloneCmd.append("--recurse-submodules")
            if initialBranch:
                cloneCmd += ["--branch", initialBranch]
            runCmd(cloneCmd + [remoteUrl, srcDir], cwd="/")

    def _updateGitRepo(self, srcDir: Path, remoteUrl, *, revision=None, initialBranch=None, skipSubmodules=False):
        self._ensureGitRepoIsCloned(srcDir=srcDir, remoteUrl=remoteUrl, initialBranch=initialBranch,
                                    skipSubmodules=skipSubmodules)
        if self.skipUpdate:
            return
        # make sure we run git stash if we discover any local changes
        hasChanges = len(runCmd("git", "diff", "--stat", "--ignore-submodules",
                                captureOutput=True, cwd=srcDir, printVerboseOnly=True).stdout) > 1

        pullCmd = ["git", "pull"]
        has_autostash = False
        git_version = get_program_version(Path(shutil.which("git"))) if shutil.which("git") else (0, 0, 0)
        # Use the autostash flag for Git >= 2.14 (https://stackoverflow.com/a/30209750/894271)
        if git_version >= (2, 14):
            has_autostash = True
            pullCmd.append("--autostash")

        if hasChanges:
            print(coloured(AnsiColour.green, "Local changes detected in", srcDir))
            # TODO: add a config option to skip this query?
            if self.config.force_update:
                statusUpdate("Updating", srcDir, "with autostash due to --force-update")
            elif not self.queryYesNo("Stash the changes, update and reapply?", defaultResult=True, forceResult=True):
                statusUpdate("Skipping update of", srcDir)
                return
            if not has_autostash:
                # TODO: ask if we should continue?
                stashResult = runCmd("git", "stash", "save", "Automatic stash by cheribuild.py",
                                     captureOutput=True, cwd=srcDir, printVerboseOnly=True).stdout
                # print("stashResult =", stashResult)
                if "No local changes to save" in stashResult.decode("utf-8"):
                    # print("NO REAL CHANGES")
                    hasChanges = False  # probably git diff showed something from a submodule

        if not skipSubmodules:
            pullCmd.append("--recurse-submodules")
        runCmd(pullCmd + ["--rebase"], cwd=srcDir, printVerboseOnly=True)
        if not skipSubmodules:
            runCmd("git", "submodule", "update", "--recursive", cwd=srcDir, printVerboseOnly=True)
        if hasChanges and not has_autostash:
            runCmd("git", "stash", "pop", cwd=srcDir, printVerboseOnly=True)
        if revision:
            runCmd("git", "checkout", revision, cwd=srcDir, printVerboseOnly=True)

    def _get_make_commandline(self, makeTarget, make_command, options, parallel: bool=True, compilationDbName: str=None):
        assert options is not None
        assert make_command is not None
        if makeTarget:
            allArgs = options.all_commandline_args + [makeTarget]
        else:
            allArgs = options.all_commandline_args
        if parallel and options.can_pass_jflag:
            allArgs.append(self.config.makeJFlag)
        allArgs = [make_command] + allArgs
        # TODO: use compdb instead for GNU make projects?
        if self.config.create_compilation_db and self.compileDBRequiresBear:
            if self._compiledb_tool == "bear":
                allArgs = [shutil.which("bear"), "--cdb", self.buildDir / compilationDbName, "--append"] + allArgs
            else:
                allArgs = [shutil.which("compiledb"), "--output", self.buildDir / compilationDbName] + allArgs
        if not self.config.makeWithoutNice:
            allArgs = ["nice"] + allArgs
        # TODO: this should be a super-verbose flag instead
        if self.config.verbose and make_command == "ninja":
            allArgs.append("-v")
        if self.config.passDashKToMake:
            allArgs.append("-k")
            if make_command == "ninja":
                # ninja needs the maximum number of failed jobs as an argument
                allArgs.append("50")
        return allArgs

    def get_make_commandline(self, makeTarget, make_command:str=None, options: MakeOptions=None,
                             parallel: bool=True, compilationDbName: str=None) -> list:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = self.make_args.command
        return self._get_make_commandline(makeTarget, make_command, options, parallel, compilationDbName)

    def runMake(self, makeTarget="", *, make_command: str = None, options: MakeOptions=None, logfileName: str = None,
                cwd: Path = None, appendToLogfile=False, compilationDbName="compile_commands.json",
                parallel: bool=True, stdoutFilter: "typing.Callable[[bytes], None]" = _default_stdout_filter) -> None:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = self.make_args.command
        allArgs = self._get_make_commandline(makeTarget, make_command, options, parallel=parallel,
                                             compilationDbName=compilationDbName)
        if not cwd:
            cwd = self.buildDir
        if not logfileName:
            logfileName = Path(make_command).name
            if makeTarget:
                logfileName += "." + makeTarget

        starttime = time.time()
        if self.config.noLogfile and stdoutFilter == _default_stdout_filter:
            # if output isatty() (i.e. no logfile) ninja already filters the output -> don't slow this down by
            # adding a redundant filter in python
            if make_command == "ninja" and makeTarget != "install":
                stdoutFilter = None
        if stdoutFilter is _default_stdout_filter:
            stdoutFilter = self._stdoutFilter
        env = options.env_vars
        self.runWithLogfile(allArgs, logfileName=logfileName, stdoutFilter=stdoutFilter, cwd=cwd, env=env,
                            appendToLogfile=appendToLogfile)
        # if we create a compilation db, copy it to the source dir:
        if self.config.copy_compilation_db_to_source_dir and (self.buildDir / compilationDbName).exists():
            self.installFile(self.buildDir / compilationDbName, self.sourceDir / compilationDbName, force=True)
        # add a newline at the end in case it ended with a filtered line (no final newline)
        print("Running", make_command, makeTarget, "took", time.time() - starttime, "seconds")

    def update(self):
        if not self.repository:
            self.fatal("Cannot update", self.projectName, "as it is missing a git URL", fatalWhenPretending=True)
        self._updateGitRepo(self.sourceDir, self.repository, revision=self.gitRevision, initialBranch=self.gitBranch,
                            skipSubmodules=self.skipGitSubmodules)

    def clean(self) -> ThreadJoiner:
        assert self.config.clean
        # TODO: never use the source dir as a build dir (unfortunately GDB, postgres and elftoolchain won't work)
        # will have to check how well binutils and qemu work there
        if (self.buildDir / ".git").is_dir():
            if (self.buildDir / "GNUmakefile").is_file() and self.make_kind != MakeCommandKind.BsdMake and self.target != "elftoolchain":
                runCmd(self.make_args.command, "distclean", cwd=self.buildDir)
            else:
                # just use git clean for cleanup
                warningMessage(self.projectName, "does not support out-of-source builds, using git clean to remove"
                                                 "build artifacts.")
                # Try to keep project files for IDEs and other dotfiles:
                runCmd("git", "clean", "-dfx", "--exclude=.*", "--exclude=*.kdev4", cwd=self.buildDir)
        elif self.buildDir == self.sourceDir:
            self.fatal("Cannot clean non-git source directories. Please override")
        else:
            return self.asyncCleanDirectory(self.buildDir, keepRoot=True)
        return ThreadJoiner(None)

    def needsConfigure(self) -> bool:
        """
        :return: Whether the configure command needs to be run (by default assume yes)
        """
        return True

    def should_run_configure(self):
        if self.config.forceConfigure or self.config.configureOnly:
            return True
        if self.config.clean:
            return True
        return self.needsConfigure()

    def configure(self, cwd: Path = None, configure_path: Path=None):
        if cwd is None:
            cwd = self.buildDir
        if not self.should_run_configure():
            return

        _configure_path = self.configureCommand
        if configure_path:
            _configure_path = configure_path
        if _configure_path:
            self.runWithLogfile([_configure_path] + self.configureArgs,
                                logfileName="configure", cwd=cwd, env=self.configureEnvironment)

    def compile(self, cwd: Path = None):
        if cwd is None:
            cwd = self.buildDir
        self.runMake("all", cwd=cwd)

    @property
    def makeInstallEnv(self):
        if self.destdir:
            env = self.make_args.env_vars.copy()
            if "DESTDIR" not in env:
                env["DESTDIR"] = str(self.destdir)
            return env
        return self.make_args.env_vars

    @property
    def real_install_root_dir(self):
        """
        :return: the real install root directory (e.g. if prefix == /usr/local and desdir == /tmp/benchdir it will
         return /tmp/benchdir/usr/local
        """
        if self.destdir is not None:
            assert self._installPrefix
            return self.destdir / Path(self._installPrefix).relative_to(Path("/"))
        return self._installDir

    @property
    def installDir(self):
        return self.real_install_root_dir

    @property
    def installPrefix(self):
        if self._installPrefix is not None:
            return self._installPrefix
        return self._installDir

    def runMakeInstall(self, *, options: MakeOptions=None, target="install", _stdoutFilter=_default_stdout_filter, cwd=None,
                       parallel=False, **kwargs):
        if options is None:
            options = self.make_args.copy()
        else:
            options = options.copy()
        options.env_vars.update(self.makeInstallEnv)
        self.runMake(makeTarget=target, options=options, stdoutFilter=_stdoutFilter, cwd=cwd,
                     parallel=parallel, **kwargs)

    def install(self, _stdoutFilter=_default_stdout_filter):
        self.runMakeInstall(_stdoutFilter=_stdoutFilter)

    def _do_generate_cmakelists(self):
        assert not isinstance(self, CMakeProject), self
        cmakelists = """
# Do not edit!
# Generated by cheribuild.py
#
cmake_minimum_required(VERSION 3.8)
project({project} LANGUAGES NONE)
set(CLEAR_MAKEENV env -u MAKEFLAGS -u MAKELEVEL -u MAKE -u MAKE_TERMERR -u MAKE_TERMOUT -u MFLAGS)
add_custom_target(cheribuild ALL VERBATIM USES_TERMINAL COMMAND {command} --skip-update --skip-install {target})
add_custom_target(cheribuild-j1 VERBATIM USES_TERMINAL COMMAND {command} --skip-update -j1 {target})
add_custom_target(cheribuild-verbose VERBATIM USES_TERMINAL COMMAND {command} --skip-update -v {target})
add_custom_target(cheribuild-verbose-j1 VERBATIM USES_TERMINAL COMMAND {command} --skip-update -v -j1 {target})

add_custom_target(cheribuild-with-install VERBATIM USES_TERMINAL COMMAND {command} --skip-update {target})
add_custom_target(cheribuild-full VERBATIM USES_TERMINAL COMMAND {command} {target})
""".format(command="${CLEAR_MAKEENV} " + sys.argv[0], project=self.projectName, target=self.target)
        target_file = self.sourceDir / "CMakeLists.txt"
        create = True
        if target_file.exists():
            existing_code = target_file.read_text()
            if existing_code == cmakelists:
                create = False
            elif "Generated by cheribuild.py" not in existing_code:
                print("A different CMakeLists.txt already exists. Contents:\n",
                      coloured(AnsiColour.green, existing_code), end="")
                if not self.queryYesNo("Overwrite?", forceResult=False):
                    create = False
        if create:
            self.writeFile(target_file, cmakelists, overwrite=True)

    @property
    def display_name(self):
        return self.projectName

    def process(self):
        if self.generate_cmakelists:
            self._do_generate_cmakelists()
        if self.config.verbose:
            print(self.projectName, "directories: source=%s, build=%s, install=%s" %
                  (self.sourceDir, self.buildDir, self.installDir))
        self.update()
        if not self._systemDepsChecked:
            self.checkSystemDependencies()
        assert self._systemDepsChecked, "self._systemDepsChecked must be set by now!"

        # run the rm -rf <build dir> in the background
        cleaningTask = self.clean() if self.config.clean else ThreadJoiner(None)
        if cleaningTask is None:
            cleaningTask = ThreadJoiner(None)
        assert isinstance(cleaningTask, ThreadJoiner), ""
        with cleaningTask:
            if not self.buildDir.is_dir():
                self.makedirs(self.buildDir)
            if not self.config.skipConfigure or self.config.configureOnly:
                if self.should_run_configure():
                    statusUpdate("Configuring", self.display_name, "... ")
                    self.configure()
            if self.config.configureOnly:
                return
            statusUpdate("Building", self.display_name, "... ")
            self.compile()
            if not self.config.skipInstall:
                statusUpdate("Installing", self.display_name, "... ")
                self.install()


class CMakeProject(Project):
    """
    Like Project but automatically sets up the defaults for CMake projects
    Sets configure command to CMake, adds -DCMAKE_INSTALL_PREFIX=installdir
    and checks that CMake is installed
    """
    doNotAddToTargets = True
    compileDBRequiresBear = False  # cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON does it
    generate_cmakelists = False  # There is already a CMakeLists.txt

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
        self._addRequiredSystemTool("cmake", homebrew="cmake", zypper="cmake", apt="cmake", freebsd="cmake")
        self.generator = generator
        self.configureArgs.append(str(self.sourceDir))  # TODO: use undocumented -H and -B options?
        if self.generator == CMakeProject.Generator.Ninja:
            self.configureArgs.append("-GNinja")
            self.make_args.kind = MakeCommandKind.Ninja
        if self.generator == CMakeProject.Generator.Makefiles:
            self.configureArgs.append("-GUnix Makefiles")
            self.make_args.kind = MakeCommandKind.DefaultMake

        self.configureArgs.append("-DCMAKE_BUILD_TYPE=" + self.cmakeBuildType)
        # TODO: do it always?
        if self.config.create_compilation_db:
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
        if self.installPrefix != self.installDir:
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
        if self.config.copy_compilation_db_to_source_dir and (self.buildDir / "compile_commands.json").exists():
            self.installFile(self.buildDir / "compile_commands.json", self.sourceDir / "compile_commands.json", force=True)

    def install(self, _stdoutFilter="__DEFAULT__"):
        if _stdoutFilter == "__DEFAULT__":
            _stdoutFilter = self._cmakeInstallStdoutFilter
        super().install(_stdoutFilter=_stdoutFilter)

    def _get_cmake_version(self):
        cmd = Path(self.configureCommand)
        assert self.configureCommand is not None
        if not cmd.is_absolute() or not Path(self.configureCommand).exists():
            self.fatal("Could not find cmake binary:", self.configureCommand)
            return 0, 0, 0
        assert cmd.is_absolute()
        return get_program_version(cmd, program_name=b"cmake")

    def checkSystemDependencies(self):
        if not Path(self.configureCommand).is_absolute():
            abspath = shutil.which(self.configureCommand)
            if abspath:
                self.configureCommand = abspath
        super().checkSystemDependencies()
        if self.__minimum_cmake_version:
            # try to find cmake 3.4 or newer
            versionComponents = self._get_cmake_version()
            # noinspection PyTypeChecker
            if versionComponents < self.__minimum_cmake_version:
                versionStr = ".".join(map(str, versionComponents))
                expectedStr = ".".join(map(str, self.__minimum_cmake_version))
                instrs = "Use your package manager to install CMake > " + expectedStr + \
                         " or run `cheribuild.py cmake` to install the latest version locally"
                self.dependencyError("CMake version", versionStr, "is too old (need at least", expectedStr + ")",
                                     installInstructions=instrs)

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

    def configure(self, **kwargs):
        if self._configure_supports_prefix:
            if self.installPrefix != self.installDir:
                assert self.destdir, "custom install prefix requires DESTDIR being set!"
                self.configureArgs.append("--prefix=" + str(self.installPrefix))
            else:
                self.configureArgs.append("--prefix=" + str(self.installDir))
        if self.extraConfigureFlags:
            self.configureArgs.extend(self.extraConfigureFlags)
        super().configure(**kwargs)

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
