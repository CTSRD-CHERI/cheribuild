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
import errno
import inspect
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from enum import Enum
from pathlib import Path

from ..config.chericonfig import CheriConfig, CrossCompileTarget, CPUArchitecture
from ..config.loader import ConfigLoaderBase, ComputedDefaultValue, ConfigOptionBase
from ..filesystemutils import FileSystemUtils
from ..targets import Target, MultiArchTarget, MultiArchTargetAlias, targetManager
from ..utils import *

__all__ = ["Project", "CMakeProject", "AutotoolsProject", "TargetAlias", "TargetAliasWithDependencies",  # no-combine
           "SimpleProject", "CheriConfig", "flushStdio", "MakeOptions", "MakeCommandKind", "Path",  # no-combine
           "CrossCompileTarget", "CPUArchitecture", "GitRepository", "ComputedDefaultValue",  # no-combine
           "commandline_to_str", "ReuseOtherProjectRepository", "ExternallyManagedSourceRepository",  # no-combine
           "MultiArchBaseMixin",  # TODO: remove  # no-combine
           ]  # no-combine


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
        if clsdict.get("doNotAddToTargets") is not None:
            if clsdict.get("doNotAddToTargets") is True:
                return  # if doNotAddToTargets is defined within the class we skip it
        elif name.endswith("Base"):
            fatalError("Found class name ending in Base (", name, ") but doNotAddToTargets was not defined", sep="")

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
        supported_archs = cls.supported_architectures
        assert supported_archs, "Must not be empty: " + str(supported_archs)
        assert isinstance(supported_archs, list)
        # TODO: if len(cls.supported_architectures) > 1:
        if cls._always_add_suffixed_targets or len(supported_archs) > 1:
            # Add a the target for the default architecture
            base_target = MultiArchTargetAlias(targetName, cls)
            targetManager.addTarget(base_target)
            # TODO: make this hold with CheriBSD
            assert cls._crossCompileTarget is CrossCompileTarget.NONE, "Should not be set!"
            # assert cls._should_not_be_instantiated, "multiarch base classes should not be instantiated"
            for arch in supported_archs:
                assert isinstance(arch, CrossCompileTarget)
                # create a new class to ensure different build dirs and config name strings
                new_name = targetName + "-" + arch.generic_suffix
                new_dict = cls.__dict__.copy()
                new_dict["_crossCompileTarget"] = arch
                new_dict["_should_not_be_instantiated"] = False  # unlike the subclass we can instantiate these
                new_dict["doNotAddToTargets"] = True  # We are already adding it here
                new_dict["target"] = new_name
                new_dict["synthetic_base"] = cls  # We are already adding it here
                new_type = type(cls.__name__ + "_" + arch.name, (cls,) + cls.__bases__, new_dict)
                targetManager.addTarget(MultiArchTarget(new_name, new_type, arch, base_target))
        else:
            assert len(supported_archs) == 1
            # Only one target is supported:
            cls._crossCompileTarget = supported_archs[0]
            cls._should_not_be_instantiated = False  # can be instantiated
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
    _mips_build_hybrid = None  # whether to build MIPS binaries as hybrid ones
    # Project subclasses will automatically have a target based on their name generated unless they add this:
    doNotAddToTargets = True
    # ANSI escape sequence \e[2k clears the whole line, \r resets to beginning of line
    # However, if the output is just a plain text file don't attempt to do any line clearing
    _clearLineSequence = b"\x1b[2K\r" if sys.__stdout__.isatty() else b"\n"

    CAN_TARGET_ALL_TARGETS = [CrossCompileTarget.CHERIBSD_MIPS_PURECAP, CrossCompileTarget.MIPS, CrossCompileTarget.NATIVE]
    CAN_TARGET_ONLY_NATIVE = [CrossCompileTarget.NATIVE]
    # WARNING: baremetal CHERI probably doesn't work
    CAN_TARGET_ALL_BAREMETAL_TARGETS = [CrossCompileTarget.MIPS, CrossCompileTarget.CHERIBSD_MIPS_PURECAP]
    CAN_TARGET_ALL_TARGETS_EXCEPT_CHERI = [CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]
    CAN_TARGET_ALL_TARGETS_EXCEPT_NATIVE = [CrossCompileTarget.CHERIBSD_MIPS_PURECAP, CrossCompileTarget.MIPS]
    supported_architectures = CAN_TARGET_ONLY_NATIVE
    # The architecture to build for if no --xmips/--xhost flag is passed (defaults to supported_architectures[0]
    # if no match)
    _default_architecture = None
    appendCheriBitsToBuildDir = True
    _crossCompileTarget = CrossCompileTarget.NONE  # type: CrossCompileTarget
    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    # To check that we don't create an crosscompile targets without a fixed target
    _should_not_be_instantiated = True
    # To prevent non-suffixed targets in case the only target is not NATIVE
    _always_add_suffixed_targets = False  # add a suffixed target even if only one variant is supported

    @property
    def _no_overwrite_allowed(self) -> "typing.Tuple[str]":
        return "_crossCompileTarget",

    @property
    def mips_build_hybrid(self) -> bool:
        if self._mips_build_hybrid is None:
            return self.config.use_hybrid_sysroot_for_mips
        else:
            return self._mips_build_hybrid
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
        assert expected_build_arch is not None
        if expected_build_arch is CrossCompileTarget.NONE:
            raise ValueError("Cannot call direct_dependencies() on a target alias")
        if callable(dependencies):
            if inspect.ismethod(dependencies):
                # noinspection PyCallingNonCallable
                dependencies = cls.dependencies(config)
            else:
                dependencies = dependencies(cls, config)
        for dep_name in dependencies:
            if callable(dep_name):
                dep_name = dep_name(cls, config)
            try:
                dep_target = targetManager.get_target_raw(dep_name)
            except KeyError:
                fatalError("Could not find target '", dep_name, "' for ", cls.__name__, sep="")
                raise
            # Handle --include-dependencies with --skip-sdk is passed
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
                    if tgt.target_arch is expected_build_arch:
                        dep_target = tgt
                        # print("Overriding with", tgt.name)
                        break
            assert not isinstance(dep_target, MultiArchTargetAlias), "All targets should be fully resolved: " + cls.__name__
            yield dep_target

    def is_exact_instance(self, class_type: "typing.Type[Any]") -> bool:
        if self.__class__ == class_type or getattr(self, "synthetic_base", object) == class_type:
            self.verbose_print(self, "is exact instance of", class_type)
            return True
        else:
            self.verbose_print(self, "is not exact instance of", class_type)
            return False

    @classmethod
    def recursive_dependencies(cls, config: CheriConfig) -> "typing.List[Target]":
        # look only in __dict__ to avoid parent class lookup
        _cached = cls.__dict__.get("_cached_deps", None)
        if _cached is not None:
            return _cached
        result = []  # type: typing.List[Target]
        for target in cls.direct_dependencies(config):
            if target not in result:
                result.append(target)
            # now recursively add the other deps:
            recursive_deps = target.projectClass.recursive_dependencies(config)
            for r in recursive_deps:
                if r not in result:
                    result.append(r)
        cls._cached_deps = result
        return result

    @classmethod
    def _cached_dependencies(cls) -> "typing.List[Target]":
        # look only in __dict__ to avoid parent class lookup
        _cached = cls.__dict__.get("_cached_deps", None)
        if _cached is None:
            raise ValueError("_cached_dependencies called before allDependencyNames()")
        return _cached

    @classmethod
    def get_instance(cls: "typing.Type[Type_T]", caller: "typing.Optional[SimpleProject]",
                     config: CheriConfig = None, cross_target: CrossCompileTarget = CrossCompileTarget.NONE) -> "Type_T":
        # TODO: assert that target manager has been initialized
        assert cross_target is not None
        if caller is not None:
            if config is None:
                config = caller.config
            if cross_target is CrossCompileTarget.NONE:
                cross_target = caller.get_crosscompile_target(config)
        else:
            assert config is not None, "Need either caller or config argument!"

        return cls.get_instance_for_cross_target(cross_target, config, caller=caller)

    @classmethod
    def get_instance_for_cross_target(cls: "typing.Type[Type_T]", cross_target: CrossCompileTarget,
                                      config: CheriConfig, caller: "SimpleProject" = None) -> "Type_T":
        # Also need to handle calling self.get_instance_for_cross_target() on a target-specific instance
        # In that case cls.target returns e.g. foo-mips, etc and targetManager will always return the MIPS version
        root_class = getattr(cls, "synthetic_base", cls)
        target = targetManager.get_target(root_class.target, cross_target, config, caller=caller)
        result = target.get_or_create_project(cross_target, config)
        assert isinstance(result, SimpleProject)
        found_target = result.get_crosscompile_target(config)
        # XXX: FIXME: add cross target to every call
        if cross_target is not None:
            assert found_target is cross_target, "Didn't find right instance of " + str(cls) + ": " + str(
                found_target) + " vs. " + str(cross_target) + ", caller was " + repr(caller)
        return result

    @classmethod
    def get_crosscompile_target(cls, config: CheriConfig) -> CrossCompileTarget:
        target = cls._crossCompileTarget
        assert target is not None
        if target is not CrossCompileTarget.NONE:
            return target
        # Find the best match based on config.crossCompileTarget
        default_target = config.crossCompileTarget
        assert cls.supported_architectures, "Must not be empty"
        # if we can build the default target (--xmips/--xhost) chose that
        if default_target in cls.supported_architectures:
            return default_target
        # otherwise fall back to the default specified in the class
        result = cls.default_architecture
        # Otherwise pick the best match:
        if default_target.is_cheri_purecap([CPUArchitecture.MIPS]):
            # add this note for e.g. GDB:
            # noinspection PyUnresolvedReferences
            cls._configure_status_message = "Cannot compile " + cls.target + " in CHERI purecap mode," \
                                                                             " building MIPS binaries instead"
        return result

    @classproperty
    def default_architecture(cls) -> CrossCompileTarget:
        result = cls._default_architecture
        if result is not None:
            return result
        # otherwise pick the first supported arch:
        return cls.supported_architectures[0]

    @property
    def crosscompile_target(self):
        return self.get_crosscompile_target(self.config)

    def get_host_triple(self):
        compiler = getCompilerInfo(self.config.clangPath if self.config.clangPath else shutil.which("cc"))
        return compiler.default_target

    def compiling_for_mips(self, include_purecap: bool):
        return self._crossCompileTarget.is_mips(include_purecap=include_purecap)

    def compiling_for_cheri(self):
        return self._crossCompileTarget.is_cheri_purecap()

    def compiling_for_host(self):
        return self._crossCompileTarget.is_native()

    def compiling_for_riscv(self):
        return self._crossCompileTarget.is_riscv(include_purecap=False)

    @property
    def display_name(self):
        if self._crossCompileTarget is CrossCompileTarget.NONE:
            return self.projectName + " (target alias)"
        return self.projectName + " (" + self._crossCompileTarget.build_suffix(self.config) + ")"

    @classmethod
    def get_class_for_target(cls: "typing.Type[Type_T]", arch: CrossCompileTarget) -> "typing.Type[Type_T]":
        target = targetManager.get_target_raw(cls.target)
        assert isinstance(target, MultiArchTargetAlias)
        for t in target.derived_targets:
            if t.target_arch is arch:
                return t.projectClass
        raise LookupError("Invalid arch " + str(arch) + " for class " + str(cls))

    @property
    def crossSysrootPath(self):
        assert self.crosscompile_target is not None, "called from invalid class " + str(self.__class__)
        return self.config.get_sysroot_path(self.crosscompile_target, self.mips_build_hybrid)

    # Duplicate all arguments instead of using **kwargs to get sensible code completion
    @staticmethod
    def run_cmd(*args, captureOutput=False, captureError=False, input: typing.Union[str, bytes] = None, timeout=None,
           print_verbose_only=False, runInPretendMode=False, raiseInPretendMode=False, no_print=False,
           replace_env=False, **kwargs):
        return runCmd(*args, captureOutput=captureOutput, captureError=captureError, input=input, timeout=timeout,
                      print_verbose_only=print_verbose_only, runInPretendMode=runInPretendMode,
                      raiseInPretendMode=raiseInPretendMode, no_print=no_print, replace_env=replace_env, **kwargs)

    @classmethod
    def addConfigOption(cls, name: str, default: typing.Union[Type_T, typing.Callable[[], Type_T]] = None,
                        kind: "typing.Union[typing.Type[Type_T], typing.Callable[[str], Type_T]]" = str, *,
                        showHelp = False, shortname=None, _no_fallback_config_name: bool = False,
                        only_add_for_targets: "typing.List[CrossCompileTarget]" = None,
                        fallback_config_name: str = None, **kwargs) -> Type_T:
        # Need a string annotation for kind to avoid https://github.com/python/typing/issues/266 which seems to affect
        # the version of python in Ubuntu 16.04
        if only_add_for_targets is not None:
            # Some config options only apply to certain targets -> add them to those targets and the generic one
            target = cls._crossCompileTarget
            assert target is not None
            # If we are adding to the base class or the target is not in
            if target is not CrossCompileTarget.NONE and not any(x is target for x in only_add_for_targets):
                return default
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
        if not _no_fallback_config_name and fallback_name_base and fallback_config_name is None:
            if name not in ["build-directory"]:
                fallback_config_name = fallback_name_base + "/" + name
            elif synthetic_base is not None:
                assert name == "build-directory"
                assert issubclass(cls, SimpleProject), cls
                # build-directory should only be inherited for the default target (e.g. cheribsd-cheri -> cheribsd):
                if cls.default_architecture is not None and cls.default_architecture is cls._crossCompileTarget:
                    # Don't allow cheribsd-purecap/build-directory to fall back to cheribsd/build-directory
                    # but if the projectName is the same we can assume it's the same class:
                    if cls.projectName == synthetic_base.projectName:
                        fallback_config_name = fallback_name_base + "/" + name
        return cls._configLoader.addOption(configOptionKey + "/" + name, shortname, default=default, type=kind,
                                           _owningClass=cls, group=cls._commandLineOptionGroup, helpHidden=helpHidden,
                                           _fallback_name=fallback_config_name, **kwargs)

    @classmethod
    def addBoolOption(cls, name: str, *, shortname=None, default=False, only_add_for_targets: list=None, **kwargs) -> bool:
        # noinspection PyTypeChecker
        return cls.addConfigOption(name, default=default, kind=bool, shortname=shortname, action="store_true",
                                   only_add_for_targets=only_add_for_targets, **kwargs)

    @classmethod
    def addPathOption(cls, name: str, *, shortname=None, only_add_for_targets: list=None, **kwargs) -> Path:
        return cls.addConfigOption(name, kind=Path, shortname=shortname, only_add_for_targets=only_add_for_targets,
                                   **kwargs)

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
        assert not hasattr(self, "gitBranch"), "gitBranch must not be used: " + self.__class__.__name__

    def addRequiredSystemTool(self, executable: str, installInstructions=None, freebsd: str=None, apt: str=None,
                              zypper: str=None, homebrew: str=None, cheribuild_target: str=None):
        if not installInstructions:
            installInstructions = OSInfo.install_instructions(executable, False, freebsd=freebsd, zypper=zypper, apt=apt,
                                                              homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__requiredSystemTools[executable] = installInstructions

    def _addRequiredPkgConfig(self, package: str, install_instructions=None, freebsd: str=None, apt: str = None,
                              zypper: str=None, homebrew: str=None, cheribuild_target: str=None):
        self.addRequiredSystemTool("pkg-config", freebsd="pkgconf", homebrew="pkg-config", apt="pkg-config", )
        if not install_instructions:
            install_instructions = OSInfo.install_instructions(package, True, freebsd=freebsd, zypper=zypper, apt=apt,
                                                               homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__requiredPkgConfig[package] = install_instructions

    def _addRequiredSystemHeader(self, header: str, install_instructions=None, freebsd: str=None, apt: str = None,
                              zypper: str=None, homebrew: str=None, cheribuild_target: str=None):
        self.addRequiredSystemTool("pkg-config", freebsd="pkgconf", homebrew="pkg-config", apt="pkg-config", )
        if not install_instructions:
            install_instructions = OSInfo.install_instructions(header, True, freebsd=freebsd, zypper=zypper, apt=apt,
                                                               homebrew=homebrew, cheribuild_target=cheribuild_target)
        self.__requiredSystemHeaders[header] = install_instructions

    def queryYesNo(self, message: str = "", *, default_result=False, force_result=True, yesNoStr: str=None) -> bool:
        if yesNoStr is None:
            yesNoStr = " [Y]/n " if default_result else " y/[N] "
        if self.config.pretend:
            print(message + yesNoStr, coloured(AnsiColour.green, "y" if force_result else "n"), sep="")
            return force_result  # in pretend mode we always return true
        if self.config.force:
            # in force mode we always return the forced result without prompting the user
            print(message + yesNoStr, coloured(AnsiColour.green, "y" if force_result else "n"), sep="")
            return force_result
        if not sys.__stdin__.isatty():
            return default_result  # can't get any input -> return the default
        result = input(message + yesNoStr)
        if default_result:
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
                    if project.config.write_logfile:
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
        if self.config.write_logfile:
            logfilePath = self.buildDir / (logfileName + ".log")
            print("Saving build log to", logfilePath)
        else:
            logfilePath = Path(os.devnull)
        if self.config.pretend:
            return
        if self.config.verbose:
            stdoutFilter = None

        if self.config.write_logfile and logfilePath.is_file() and not appendToLogfile:
            logfilePath.unlink()  # remove old logfile
        args = list(map(str, args))  # make sure all arguments are strings
        cmdStr = commandline_to_str(args)

        if not self.config.write_logfile:
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

    def check_system_dependencies(self) -> None:
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
            printCommand(check_cmd, print_verbose_only=True)
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

    def run_benchmarks(self):
        # for the --benchmark option
        statusUpdate("No benchmarks defined for target", self.target)

    def run_cheribsd_test_script(self, script_name, *script_args, kernel_path=None, disk_image_path=None,
                                 mount_builddir=True, mount_sourcedir=False, mount_sysroot=False, mount_installdir=False,
                                 use_benchmark_kernel_by_default=False):
        # mount_sysroot may be needed for projects such as QtWebkit where the minimal image doesn't contain all the
        # necessary libraries
        from .build_qemu import BuildQEMU
        # noinspection PyUnusedLocal
        script_dir = Path("/this/will/not/work/when/using/remote-cheribuild.py")
        xtarget = self.crosscompile_target
        test_native = xtarget.is_native()
        if kernel_path is None and not test_native and "--kernel" not in self.config.test_extra_args:
            from .cross.cheribsd import BuildCheriBsdMfsKernel
            # Use the benchmark kernel by default if the parameter is set and the user didn't pass
            # --no-use-minimal-benchmark-kernel on the command line or in the config JSON
            use_benchmark_kernel_value = self.config.use_minimal_benchmark_kernel  # Load the value first to ensure that it has been loaded
            use_benchmark_config_option = inspect.getattr_static(self.config, "use_minimal_benchmark_kernel")
            assert isinstance(use_benchmark_config_option, ConfigOptionBase)
            if use_benchmark_kernel_value or (use_benchmark_kernel_by_default and use_benchmark_config_option.is_default_value):
                kernel_path = BuildCheriBsdMfsKernel.get_installed_benchmark_kernel_path(self)
            else:
                kernel_path = BuildCheriBsdMfsKernel.get_installed_kernel_path(self)

            if not kernel_path.exists():
                cheribsd_image = "cheribsd{suffix}-cheri{suffix}-malta64-mfs-root-minimal-cheribuild-kernel.bz2".format(
                        suffix="" if self.config.cheriBits == 256 else self.config.cheriBitsStr)
                freebsd_image = "freebsd-malta64-mfs-root-minimal-cheribuild-kernel.bz2"
                if xtarget.is_mips(include_purecap=False):
                    guessed_archive = cheribsd_image if self.config.run_mips_tests_with_cheri_image else freebsd_image
                elif xtarget.is_cheri_purecap([CPUArchitecture.MIPS]):
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
        if test_native:
            cmd = [script, "--test-native"]
        else:
            cmd = [script, "--ssh-key", self.config.test_ssh_key]
            if "--kernel" not in self.config.test_extra_args:
                cmd.extend(["--kernel", kernel_path])
            if "--qemu-cmd" not in self.config.test_extra_args:
                qemu_path = BuildQEMU.qemu_binary(self)
                if not qemu_path.exists():
                    self.fatal("QEMU binary", qemu_path, "doesn't exist")
                cmd.extend(["--qemu-cmd", qemu_path])
        if mount_builddir and self.buildDir and "--build-dir" not in self.config.test_extra_args:
            cmd.extend(["--build-dir", self.buildDir])
        if mount_sourcedir and self.sourceDir and "--source-dir" not in self.config.test_extra_args:
            cmd.extend(["--source-dir", self.sourceDir])
        if mount_sysroot and "--sysroot-dir" not in self.config.test_extra_args:
            cmd.extend(["--sysroot-dir", self.crossSysrootPath])
        if mount_installdir:
            if "--install-destdir" not in self.config.test_extra_args:
                cmd.extend(["--install-destdir", self.destdir])
            if "--install-prefix" not in self.config.test_extra_args:
                cmd.extend(["--install-prefix", self.installPrefix])
        if disk_image_path and not test_native and "--disk-image" not in self.config.test_extra_args:
            cmd.extend(["--disk-image", disk_image_path])
        if self.config.tests_interact:
            cmd.append("--interact")
        if self.config.tests_env_only:
            cmd.append("--test-environment-only")
        if self.config.trap_on_unrepresentable:
            cmd.append("--trap-on-unrepresentable")
        if self.config.test_ld_preload:
            cmd.append("--test-ld-preload=" + str(self.config.test_ld_preload))
            if xtarget.is_cheri_purecap():
                cmd.append("--test-ld-preload-variable=LD_CHERI_PRELOAD")
            else:
                cmd.append("--test-ld-preload-variable=LD_PRELOAD")


        cmd += list(script_args)
        if self.config.test_extra_args:
            cmd.extend(map(str, self.config.test_extra_args))
        runCmd(cmd)

    def runShellScript(self, script, shell="sh", **kwargs):
        print_args = dict(**kwargs)
        if "captureOutput" in print_args:
            del print_args["captureOutput"]
        printCommand(shell, "-xe" if self.config.verbose else "-e", "-i", "-c", script, **print_args)
        kwargs["no_print"] = True
        return runCmd(shell, "-xe" if self.config.verbose else "-e", "-i", "-c", script, **kwargs)

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
    assert isinstance(project, Project)
    target = project.get_crosscompile_target(config)
    return project.build_dir_for_target(target)


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
        return b"GNU Make" in get_version_output(Path(self.command))

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
            self.__project.addRequiredSystemTool("make")
            return "make"
        elif self.kind == MakeCommandKind.GnuMake:
            if IS_LINUX and not shutil.which("gmake"):
                statusUpdate("Could not find `gmake` command, assuming `make` is GNU make")
                self.__project.addRequiredSystemTool("make")
                return "make"
            else:
                self.__project.addRequiredSystemTool("gmake", homebrew="make")
                return "gmake"
        elif self.kind == MakeCommandKind.BsdMake:
            if IS_FREEBSD:
                return "make"
            else:
                self.__project.addRequiredSystemTool("bmake", homebrew="bmake", cheribuild_target="bmake")
                return "bmake"
        elif self.kind == MakeCommandKind.Ninja:
            self.__project.addRequiredSystemTool("ninja", homebrew="ninja", apt="ninja-build")
            return "ninja"
        else:
            if self.__command is not None:
                return self.__command
            self.__project.fatal("Cannot infer path from CustomMakeTool. Set self.make_args.set_command(\"tool\")")
            raise RuntimeError()

    def set_command(self, value, can_pass_j_flag=True, **kwargs):
        self.__command = str(value)
        # noinspection PyProtectedMember
        if not Path(value).is_absolute():
            self.__project.addRequiredSystemTool(value, **kwargs)
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


class SourceRepository(object):
    def ensure_cloned(self, current_project: "Project", *, src_dir: Path, skip_submodules=False):
        raise NotImplementedError

    def update(self, current_project: "Project", *, src_dir: Path, revision=None, skip_submodules=False):
        raise NotImplementedError


class ExternallyManagedSourceRepository(SourceRepository):
    def ensure_cloned(self, current_project: "Project", **kwargs):
        current_project.info("Not cloning repositiory since it is externally managed")

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        current_project.info("Not updating", src_dir, "since it is externally managed")


class ReuseOtherProjectRepository(SourceRepository):
    def __init__(self, source_project: "typing.Type[Project]", *, subdirectory=".",
                 repo_for_target: CrossCompileTarget = CrossCompileTarget.NONE):
        self.source_project = source_project
        self.subdirectory = subdirectory
        self.repo_for_target = repo_for_target

    def ensure_cloned(self, current_project: "Project", **kwargs):
        if not self.get_real_source_dir(current_project, current_project.config).exists():
            current_project.fatal("Source repository for target", current_project.target, "does not exist.",
                                  fixitHint="This project uses the sources from the " + self.source_project.target +
                                  "target so you will have to clone that first. Try running:\n\t`" +
                                  "cheribuild.py " + self.source_project.target + "--no-skip-update --skip-configure " +
                                  "--skip-build --skip-install`")

    def get_real_source_dir(self, caller: SimpleProject, config: CheriConfig) -> Path:
        return self.source_project.getSourceDir(caller, config, cross_target=self.repo_for_target) / self.subdirectory

    def update(self, current_project: "Project", *, src_dir: Path, **kwargs):
        # TODO: allow updating the repo?
        current_project.info("Not updating", src_dir, "since it reuses the repository for ", self.source_project.target)


class ReuseOtherProjectDefaultTargetRepository(ReuseOtherProjectRepository):
    def __init__(self, source_project: "typing.Type[Project]", *, subdirectory="."):
        super().__init__(source_project, subdirectory=subdirectory,
                         repo_for_target=source_project.supported_architectures[0])


class GitRepository(SourceRepository):
    def __init__(self, url, *, old_urls: typing.List[bytes] = None, default_branch: str = None,
                 force_branch: bool = False):
        self.url = url
        self.old_urls = old_urls
        self.default_branch = default_branch
        self.force_branch = force_branch

    def ensure_cloned(self, current_project: "Project", *, src_dir: Path, skip_submodules=False):
        # git-worktree creates a .git file instead of a .git directory so we can't use .is_dir()
        if not (src_dir / ".git").exists():
            if current_project.config.skipClone:
                current_project.fatal("Sources for", str(src_dir), " missing!")
            assert isinstance(self.url, str), self.url
            assert not self.url.startswith("<"), "Invalid URL " + self.url
            if not current_project.queryYesNo(
                    str(src_dir) + "is not a git repository. Clone it from '" + self.url + "'?"):
                current_project.fatal("Sources for", str(src_dir), " missing!")
            clone_cmd = ["git", "clone"]
            if current_project.config.shallow_clone:
                # Note: we pass --no-single-branch since otherwise git fetch will not work with branches and
                # the solution of running  `git config remote.origin.fetch "+refs/heads/*:refs/remotes/origin/*"`
                # is not very intuitive. This increases the amount of data fetched but increases usability
                clone_cmd.extend(["--depth", "1", "--no-single-branch"])
            if not skip_submodules:
                clone_cmd.append("--recurse-submodules")
            if self.default_branch:
                clone_cmd += ["--branch", self.default_branch]
            runCmd(clone_cmd + [self.url, src_dir], cwd="/")
            # Could also do this but it seems to fetch more data than --no-single-branch
            # if self.config.shallow_clone:
            #    runCmd(["git", "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"], cwd=src_dir)

    def update(self, current_project: "Project", *, src_dir: Path, revision=None,
               skip_submodules=False):
        self.ensure_cloned(current_project, src_dir=src_dir, skip_submodules=skip_submodules)
        if current_project.skipUpdate:
            return
        # handle repositories that have moved
        if src_dir.exists() and self.old_urls:
            # Update from the old url:
            for old_url in self.old_urls:
                assert isinstance(old_url, bytes)
                remote_url = runCmd("git", "remote", "get-url", "origin", captureOutput=True, cwd=src_dir).stdout.strip()
                if remote_url == old_url:
                    warningMessage(current_project.projectName, "still points to old repository", remote_url)
                    if current_project.queryYesNo("Update to correct URL?"):
                        runCmd("git", "remote", "set-url", "origin", self.url, runInPretendMode=True, cwd=src_dir)

        # make sure we run git stash if we discover any local changes
        has_changes = len(runCmd("git", "diff", "--stat", "--ignore-submodules",
                                captureOutput=True, cwd=src_dir, print_verbose_only=True).stdout) > 1

        pull_cmd = ["git", "pull"]
        has_autostash = False
        git_version = get_program_version(Path(shutil.which("git"))) if shutil.which("git") else (0, 0, 0)
        # Use the autostash flag for Git >= 2.14 (https://stackoverflow.com/a/30209750/894271)
        if git_version >= (2, 14):
            has_autostash = True
            pull_cmd.append("--autostash")

        if has_changes:
            print(coloured(AnsiColour.green, "Local changes detected in", src_dir))
            # TODO: add a config option to skip this query?
            if current_project.config.force_update:
                statusUpdate("Updating", src_dir, "with autostash due to --force-update")
            elif not current_project.queryYesNo("Stash the changes, update and reapply?", default_result=True, force_result=True):
                statusUpdate("Skipping update of", src_dir)
                return
            if not has_autostash:
                # TODO: ask if we should continue?
                stash_result = runCmd("git", "stash", "save", "Automatic stash by cheribuild.py",
                                     captureOutput=True, cwd=src_dir, print_verbose_only=True).stdout
                # print("stash_result =", stash_result)
                if "No local changes to save" in stash_result.decode("utf-8"):
                    # print("NO REAL CHANGES")
                    has_changes = False  # probably git diff showed something from a submodule

        if not skip_submodules:
            pull_cmd.append("--recurse-submodules")
        runCmd(pull_cmd + ["--rebase"], cwd=src_dir, print_verbose_only=True)
        if not skip_submodules:
            runCmd("git", "submodule", "update", "--recursive", cwd=src_dir, print_verbose_only=True)
        if has_changes and not has_autostash:
            runCmd("git", "stash", "pop", cwd=src_dir, print_verbose_only=True)
        if revision:
            runCmd("git", "checkout", revision, cwd=src_dir, print_verbose_only=True)

        if src_dir.exists() and self.force_branch:
            assert self.default_branch, "default_branch must be set if force_branch is true!"
            # TODO: move this to Project so it can also be used for other targets
            status = runCmd("git", "status", "-b", "-s", "--porcelain", "-u", "no",
                            captureOutput=True, print_verbose_only=True, cwd=src_dir, runInPretendMode=True)
            if status.stdout.startswith(b"## ") and not status.stdout.startswith(
                    b"## " + self.default_branch.encode("utf-8") + b"..."):
                current_branch = status.stdout[3:status.stdout.find(b"...")].strip()
                warningMessage("You are trying to build the", current_branch.decode("utf-8"),
                               "branch. You should be using", self.default_branch)
                if current_project.queryYesNo("Would you like to change to the " + self.default_branch + " branch?"):
                    runCmd("git", "checkout", self.default_branch, cwd=src_dir)
                elif not current_project.queryYesNo("Are you sure you want to continue?", force_result=False):
                    current_project.fatal("Wrong branch:", current_branch.decode("utf-8"))


class MultiArchBaseMixin(object):
    supported_architectures = SimpleProject.CAN_TARGET_ALL_TARGETS


class Project(SimpleProject):
    repository = None  # type: SourceRepository
    gitRevision = None
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
    def getSourceDir(cls, caller: "SimpleProject", config: CheriConfig = None,
                     cross_target: CrossCompileTarget = CrossCompileTarget.NONE):
        return cls.get_instance(caller, config, cross_target).sourceDir

    @classmethod
    def getBuildDir(cls, caller: "SimpleProject", config: CheriConfig = None,
                    cross_target: CrossCompileTarget = CrossCompileTarget.NONE):
        return cls.get_instance(caller, config, cross_target).buildDir

    @classmethod
    def getInstallDir(cls, caller: "SimpleProject", config: CheriConfig = None,
                      cross_target: CrossCompileTarget = CrossCompileTarget.NONE):
        return cls.get_instance(caller, config, cross_target).real_install_root_dir

    def build_configuration_suffix(self, target: CrossCompileTarget = CrossCompileTarget.NONE) -> str:
        """
        :param target: the target to use
        :return: a string such as -128/-native-asan that identifies the build configuration
        """
        config = self.config
        assert target is not None
        if target is CrossCompileTarget.NONE:
            target = self.get_crosscompile_target(config)
        # targets that only support native don't need a suffix
        if target.is_native() and len(self.supported_architectures) == 1:
            result = "-" + config.cheriBitsStr if self.appendCheriBitsToBuildDir else ""
        result = target.build_suffix(config, build_hybrid=self.mips_build_hybrid)
        if self.use_asan:
            result = "-asan" + result
        if self.build_dir_suffix:
            result = self.build_dir_suffix + result
        return result

    def build_dir_for_target(self, target: CrossCompileTarget):
        return self.config.buildRoot / (self.projectName.lower() + self.build_configuration_suffix(target) + "-build")

    _installToSDK = ComputedDefaultValue(
        function=lambda config, project: config.sdkDir,
        asString="$INSTALL_ROOT/sdk")
    _installToBootstrapTools = ComputedDefaultValue(
        function=lambda config, project: config.otherToolsDir,
        asString="$INSTALL_ROOT/bootstrap")

    default_use_asan = False
    can_build_with_asan = False

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
                       input="int main() { return 0; }\n", print_verbose_only=True)
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

    def check_system_dependencies(self):
        # Check that the make command exists (this will also add it to the required system tools)
        if self.make_args.command is None:
            self.fatal("Make command not set!")
        super().check_system_dependencies()

    @classmethod
    def setupConfigOptions(cls, installDirectoryHelp="", **kwargs):
        super().setupConfigOptions(**kwargs)
        # statusUpdate("Setting up config options for", cls, cls.target)
        cls.sourceDir = cls.addPathOption("source-directory", metavar="DIR", default=cls.defaultSourceDir,
                                          help="Override default source directory for " + cls.projectName)
        cls.buildDir = cls.addPathOption("build-directory", metavar="DIR", default=cls.defaultBuildDir,
                                         help="Override default source directory for " + cls.projectName)
        if cls.can_build_with_asan:
            asan_default = ComputedDefaultValue(
                function=lambda config, proj: False if proj.get_crosscompile_target(config).is_cheri_purecap() else proj.default_use_asan,
                asString=str(cls.default_use_asan))
            cls.use_asan = cls.addBoolOption("use-asan", default=asan_default, help="Build with AddressSanitizer enabled")
        else:
            cls.use_asan = False
        cls.skipUpdate = cls.addBoolOption("skip-update",
                                           default=ComputedDefaultValue(lambda config, proj: config.skipUpdate,
                                                                        "the value of the global --skip-update option"),
                                           help="Override --skip-update/--no-skip-update for this target only ")

        if not installDirectoryHelp:
            installDirectoryHelp = "Override default install directory for " + cls.projectName
        cls._installDir = cls.addPathOption("install-directory", metavar="DIR", help=installDirectoryHelp,
                                           default=cls.defaultInstallDir)
        if "repository" in cls.__dict__ and isinstance(cls.repository, GitRepository):
            cls.gitRevision = cls.addConfigOption("git-revision", metavar="REVISION",
                help="The git revision to checkout prior to building. Useful if HEAD is broken for one "
                     "project but you still want to update the other projects.")
            # TODO: can argparse action be used to store to the class member directly?
            # seems like I can create a new action a pass a reference to the repository:
            #class FooAction(argparse.Action):
            # def __init__(self, option_strings, dest, nargs=None, **kwargs):
            #     if nargs is not None:
            #         raise ValueError("nargs not allowed")
            #     super(FooAction, self).__init__(option_strings, dest, **kwargs)
            # def __call__(self, parser, namespace, values, option_string=None):
            #     print('%r %r %r' % (namespace, values, option_string))
            #     setattr(namespace, self.dest, values)
            cls._repositoryUrl = cls.addConfigOption("repository", kind=str, help="The URL of the git repository",
                                                    default=cls.repository.url, metavar="REPOSITORY")
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
        if isinstance(self.repository, ReuseOtherProjectRepository):
            # HACK: override the source directory (ignoring the setting from the JSON)
            # This should be done using a decorator that also changes defaultSourceDir so that we can
            # take the JSON into account
            self.sourceDir = self.repository.get_real_source_dir(self, config)
            self.info("Overriding source directory for", self.target, "since it reuses the sources of",
                      self.repository.source_project.target, "->", self.sourceDir)
        super().__init__(config)
        # set up the install/build/source directories (allowing overrides from config file)
        assert isinstance(self.repository, SourceRepository), self.target + " repository member is wrong!"
        if hasattr(self, "_repositoryUrl"):
            # TODO: remove this and use a custom argparse.Action subclass
            assert isinstance(self.repository, GitRepository)
            self.repository.url = self._repositoryUrl
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
                self.addRequiredSystemTool("compiledb", installInstructions="Run `pip2 install --user compiledb``")
                self._compiledb_tool = "compiledb"
            else:
                self.addRequiredSystemTool("bear", installInstructions="Run `cheribuild.py bear`")
                self._compiledb_tool = "bear"
        self._force_clean = False
        self._preventAssign = True

    @property
    def _no_overwrite_allowed(self) -> "typing.Iterable[str]":
        return super()._no_overwrite_allowed + ("configureArgs", "configureEnvironment", "make_args")

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

    def _get_make_commandline(self, make_target, make_command, options, parallel: bool=True, compilationDbName: str=None):
        assert options is not None
        assert make_command is not None
        if make_target:
            allArgs = options.all_commandline_args + [make_target]
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
        if self.config.debug_output and make_command == "ninja":
            allArgs.append("-v")
        if self.config.passDashKToMake:
            allArgs.append("-k")
            if make_command == "ninja":
                # ninja needs the maximum number of failed jobs as an argument
                allArgs.append("50")
        return allArgs

    def get_make_commandline(self, make_target, make_command:str=None, options: MakeOptions=None,
                             parallel: bool=True, compilationDbName: str=None) -> list:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = self.make_args.command
        return self._get_make_commandline(make_target, make_command, options, parallel, compilationDbName)

    def runMake(self, make_target="", *, make_command: str = None, options: MakeOptions=None, logfileName: str = None,
                cwd: Path = None, appendToLogfile=False, compilationDbName="compile_commands.json",
                parallel: bool=True, stdoutFilter: "typing.Optional[typing.Callable[[bytes], None]]" = _default_stdout_filter) -> None:
        if not options:
            options = self.make_args
        if not make_command:
            make_command = self.make_args.command
        allArgs = self._get_make_commandline(make_target, make_command, options, parallel=parallel,
                                             compilationDbName=compilationDbName)
        if not cwd:
            cwd = self.buildDir
        if not logfileName:
            logfileName = Path(make_command).name
            if make_target:
                logfileName += "." + make_target

        starttime = time.time()
        if not self.config.write_logfile and stdoutFilter == _default_stdout_filter:
            # if output isatty() (i.e. no logfile) ninja already filters the output -> don't slow this down by
            # adding a redundant filter in python
            if make_command == "ninja" and make_target != "install":
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
        print("Running", make_command, make_target, "took", time.time() - starttime, "seconds")

    def update(self):
        if not self.repository and not self.config.skipUpdate:
            self.fatal("Cannot update", self.projectName, "as it is missing a repository source", fatalWhenPretending=True)
        self.repository.update(self, src_dir=self.sourceDir, revision=self.gitRevision,
                               skip_submodules=self.skipGitSubmodules)

    _extra_git_clean_excludes = []

    def _git_clean_source_dir(self):
        # just use git clean for cleanup
        warningMessage(self.projectName, "does not support out-of-source builds, using git clean to remove "
                                         "build artifacts.")
        git_clean_cmd = ["git", "clean", "-dfx", "--exclude=.*", "--exclude=*.kdev4"] + self._extra_git_clean_excludes
        # Try to keep project files for IDEs and other dotfiles:
        runCmd(git_clean_cmd, cwd=self.sourceDir)

    def clean(self) -> ThreadJoiner:
        assert self.config.clean or self._force_clean
        # TODO: never use the source dir as a build dir (unfortunately mibench and elftoolchain won't work)
        # will have to check how well binutils and qemu work there
        if (self.buildDir / ".git").is_dir():
            if (self.buildDir / "GNUmakefile").is_file() and self.make_kind != MakeCommandKind.BsdMake and self.target != "elftoolchain":
                runCmd(self.make_args.command, "distclean", cwd=self.buildDir)
            else:
                assert self.sourceDir == self.buildDir
                self._git_clean_source_dir()
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
        if not Path(_configure_path).exists():
            self.fatal("Configure command ", _configure_path, "does not exist!")
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
    def installPrefix(self) -> Path:
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
        self.runMake(make_target=target, options=options, stdoutFilter=_stdoutFilter, cwd=cwd,
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
            existing_code = self.readFile(target_file)
            if existing_code == cmakelists:
                create = False
            elif "Generated by cheribuild.py" not in existing_code:
                print("A different CMakeLists.txt already exists. Contents:\n",
                      coloured(AnsiColour.green, existing_code), end="")
                if not self.queryYesNo("Overwrite?", force_result=False):
                    create = False
        if create:
            self.writeFile(target_file, cmakelists, overwrite=True)

    @property
    def csetbounds_stats_file(self) -> Path:
        return self.buildDir / "csetbounds-stats.csv"

    def process(self):
        if self.generate_cmakelists:
            self._do_generate_cmakelists()
        if self.config.verbose:
            print(self.projectName, "directories: source=%s, build=%s, install=%s" %
                  (self.sourceDir, self.buildDir, self.installDir))
        if not self.config.skipUpdate:
            self.update()
        if not self._systemDepsChecked:
            self.check_system_dependencies()
        assert self._systemDepsChecked, "self._systemDepsChecked must be set by now!"

        last_build_file = Path(self.buildDir, ".last_build_kind")
        if self.build_in_source_dir and not self.config.clean:
            if not last_build_file.exists():
                self._force_clean = True  # could be an old build prior to adding this check
            else:
                last_build_kind = self.readFile(last_build_file)
                if last_build_kind != self.build_configuration_suffix():
                    if not self.queryYesNo("Last build was for configuration" + last_build_kind +
                                           " but currently building" + self.build_configuration_suffix() +
                                           ". Will clean before build. Continue?", force_result=True, default_result=True):
                        self.fatal("Cannot continue")
                        return
                    self._force_clean = True

        # run the rm -rf <build dir> in the background
        cleaningTask = self.clean() if (self._force_clean or self.config.clean) else ThreadJoiner(None)
        if cleaningTask is None:
            cleaningTask = ThreadJoiner(None)
        assert isinstance(cleaningTask, ThreadJoiner), ""
        with cleaningTask:
            if not self.buildDir.is_dir():
                self.makedirs(self.buildDir)
            if self.build_in_source_dir:
                self.writeFile(last_build_file, self.build_configuration_suffix(), overwrite=True)
            if not self.config.skipConfigure or self.config.configureOnly:
                if self.should_run_configure():
                    statusUpdate("Configuring", self.display_name, "... ")
                    self.configure()
            if self.config.configureOnly:
                return
            if not self.config.skipBuild:
                if self.config.csetbounds_stats and (self.csetbounds_stats_file.exists() or self.config.pretend):
                    self.moveFile(self.csetbounds_stats_file, self.csetbounds_stats_file.with_suffix(".from-configure.csv"),
                                  force=True)
                    # move any csetbounds stats from configuration (since they are not useful)
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
        self.addRequiredSystemTool("cmake", homebrew="cmake", zypper="cmake", apt="cmake", freebsd="cmake")
        # allow a -G flag in cmake-options to override the default generator (e.g. use makefiles for CLion)
        custom_generator = next((x for x in self.cmakeOptions if x.startswith("-G")), None)
        if custom_generator:
            if "Unix Makefiles" in custom_generator:
                generator = CMakeProject.Generator.Makefiles
            elif "Ninja" in custom_generator:
                generator = CMakeProject.Generator.Ninja
            else:
                # TODO: add support for cmake --build <dir> --target <tgt> -- <args>
                fatalError("Unknown CMake Generator", custom_generator, "-> don't know which build command to run")
        self.generator = generator
        self.configureArgs.append(str(self.sourceDir))  # TODO: use undocumented -H and -B options?
        if self.generator == CMakeProject.Generator.Ninja:
            if not custom_generator:
                self.configureArgs.append("-GNinja")
            self.make_args.kind = MakeCommandKind.Ninja
        if self.generator == CMakeProject.Generator.Makefiles:
            if not custom_generator:
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
            if any(x.startswith("-D" + option) for x in self.cmakeOptions):
                self.info("Not using default value of '", value, "' for CMake option '", option,
                          "' since it is explicitly overwritten in the configuration", sep="")
                continue
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

    def check_system_dependencies(self):
        if not Path(self.configureCommand).is_absolute():
            abspath = shutil.which(self.configureCommand)
            if abspath:
                self.configureCommand = abspath
        super().check_system_dependencies()
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
    dependencies = ["qemu", "sdk", "disk-image-cheri", "run"]
