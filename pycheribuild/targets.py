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
import sys
import time
from collections import OrderedDict

from .config.chericonfig import CheriConfig
from .config.target_info import CompilationTargets, CrossCompileTarget
from .utils import *

if typing.TYPE_CHECKING:   # no-combine
    from .projects.project import SimpleProject   # no-combine


class Target(object):
    instantiating_targets_should_warn = True

    def __init__(self, name, _project_class: "typing.Type[SimpleProject]"):
        self.name = name
        self._project_class = _project_class
        self.__project = None  # type: typing.Optional[SimpleProject]
        self._completed = False
        self._tests_have_run = False
        self._benchmarks_have_run = False
        self._creating_project = False  # avoid cycles

    @property
    def projectClass(self) -> "typing.Type[SimpleProject]":
        result = self._project_class
        assert result._xtarget is not CompilationTargets.NONE
        return result

    def get_real_target(self, cross_target: CrossCompileTarget, config, caller=None) -> "Target":
        return self

    def get_or_create_project(self, target_arch: "CrossCompileTarget", config) -> "SimpleProject":
        # Note: MultiArchTarget uses caller to select the right project (e.g. libcxxrt-native needs libunwind-native path)
        if self.__project is None:
            self.__project = self.create_project(config)
        assert self.__project is not None
        return self.__project

    def get_dependencies(self, config: CheriConfig) -> "typing.List[Target]":
        return self.projectClass.recursive_dependencies(config)

    def checkSystemDeps(self, config: CheriConfig):
        if self._completed:
            return
        project = self.get_or_create_project(CompilationTargets.NONE, config)
        with setEnv(PATH=config.dollarPathWithOtherTools):
            # make sure all system dependencies exist first
            project.check_system_dependencies()

    def create_project(self, config: CheriConfig) -> "SimpleProject":
        assert not self._creating_project
        if self.instantiating_targets_should_warn:
            raise RuntimeError(coloured(AnsiColour.magenta, "Instantiating target", self.name, "before run()!"))
        self._creating_project = True
        return self._create_project(config)

    def _create_project(self, config: CheriConfig) -> "SimpleProject":
        return self.projectClass(config)

    def execute(self, config: CheriConfig):
        if self._completed:
            # TODO: make this an error once I have a clean solution for the pseudo targets
            warningMessage(self.name, "has already been executed!")
            return
        # instantiate the project and run it
        starttime = time.time()
        assert self.__project is not None, "Should have been initialized in checkSystemDeps()"
        project = self.__project
        # noinspection PyProtectedMember
        assert not self.__project._setup_called, str(self._project_class) + ".setup() should not have been called yet."
        self.__project.setup()
        # noinspection PyProtectedMember
        assert self.__project._setup_called, str(self._project_class) + ": forgot to call super().setup()?"
        new_env = {"PATH": project.config.dollarPathWithOtherTools}
        if project.config.clang_colour_diags:
            new_env["CLANG_FORCE_COLOR_DIAGNOSTICS"] = "always"
        with setEnv(**new_env):
            project.process()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._completed = True

    def run_tests(self, config: "CheriConfig"):
        if self._tests_have_run:
            # TODO: make this an error once I have a clean solution for the pseudo targets
            warningMessage(self.name, "has already been executed!")
            return
        # instantiate the project and run it
        starttime = time.time()
        project = self.get_or_create_project(self.projectClass.get_crosscompile_target(config), config)
        # noinspection PyProtectedMember
        if not project._setup_called:
            project.setup()
        new_env = {"PATH": project.config.dollarPathWithOtherTools}
        if project.config.clang_colour_diags:
            new_env["CLANG_FORCE_COLOR_DIAGNOSTICS"] = "always"
        with setEnv(**new_env):
            project.run_tests()
        statusUpdate("Ran tests for target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._tests_have_run = True

    def run_benchmarks(self, config: "CheriConfig"):
        if self._benchmarks_have_run:
            # TODO: make this an error once I have a clean solution for the pseudo targets
            warningMessage(self.name, "has already been executed!")
            return
        # instantiate the project and run it
        starttime = time.time()
        project = self.get_or_create_project(self.projectClass.get_crosscompile_target(config), config)
        # noinspection PyProtectedMember
        if not project._setup_called:
            project.setup()
        new_env = {"PATH": project.config.dollarPathWithOtherTools}
        if project.config.clang_colour_diags:
            new_env["CLANG_FORCE_COLOR_DIAGNOSTICS"] = "always"
        with setEnv(**new_env):
            project.run_benchmarks()
        statusUpdate("Ran benchmarks for target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._benchmarks_have_run = True

    def reset(self):
        # For unit tests to get a fresh instance
        self._completed = False
        self._tests_have_run = False
        self.__project = None
        self._creating_project = False

    # noinspection PyProtectedMember
    def __lt__(self, other: "Target"):
        # print(self, "__lt__", other)
        # if this target is one of the dependencies order it before
        otherDeps = other.projectClass._cached_dependencies()
        # print("other deps:", otherDeps)
        if self in otherDeps:
            # print(self, "is in", other, "deps -> is less")
            return True
        # and if it is the other way around we are not less
        if other in self.projectClass._cached_dependencies():
            # print(other, "is in", self, "deps -> is greater")
            return False
        if other.name.startswith("run") and not self.name.startswith("run"):
            return True  # run must be executed last
        elif self.name.startswith("run"):
            return False
        if other.name.startswith("disk-image") and not self.name.startswith("disk-image"):
            return True  # disk-image should be done just before run
        elif self.name.startswith("disk-image"):
            return False
        # print(self, "is not in", other, "deps -> is not less")
        # otherwise just keep everything in order
        return False
        # This was previously done
        # ownDeps = self.projectClass.allDependencyNames()
        # if len(ownDeps) < len(otherDeps):
        #     return True
        # if len(ownDeps) > len(otherDeps):
        #     return False
        # return self.name < other.name  # not a dep and number of deps is the same -> compare name

    def __repr__(self):
        return "<Target " + self.name + ">"


# XXX: can't call this CrossCompileTarget since that is already the name of the enum
class MultiArchTarget(Target):
    def __init__(self, name, projectClass, target_arch: "CrossCompileTarget", base_target: "MultiArchTargetAlias"):
        super().__init__(name, projectClass)
        assert target_arch is not None
        self.target_arch = target_arch
        self.base_target = base_target
        base_target.derived_targets.append(self)

    @property
    def projectClass(self) -> "typing.Type[SimpleProject]":
        assert self.target_arch is not CompilationTargets.NONE
        return self._project_class

    def _create_project(self, config: CheriConfig) -> "SimpleProject":
        return self.projectClass(config)

    def __repr__(self):
        return "<Cross target (" + self.target_arch.name + ") " + self.name + ">"


class _TargetAliasBase(Target):
    @property
    def projectClass(self) -> "typing.Type[SimpleProject]":
        assert self._project_class is not None
        return self._project_class

    def _create_project(self, config: CheriConfig):
        raise ValueError("Should not be called!")

    def get_real_target(self, cross_target: CrossCompileTarget, config,
                        caller: "typing.Union[SimpleProject, str]" = "<unknown>") -> Target:
        raise NotImplementedError()

    def get_or_create_project(self, cross_target: "CrossCompileTarget", config) -> "SimpleProject":
        assert cross_target is not None
        tgt = self.get_real_target(cross_target, config)
        # Update the cross target
        cross_target = tgt._project_class._xtarget
        assert cross_target is not CompilationTargets.NONE
        return tgt.get_or_create_project(cross_target, config)

    def execute(self, config):
        return self.get_real_target(CompilationTargets.NONE, config).execute(config)

    def run_tests(self, config: "CheriConfig"):
        return self.get_real_target(CompilationTargets.NONE, config).run_tests(config)

    def run_benchmarks(self, config: "CheriConfig"):
        return self.get_real_target(CompilationTargets.NONE, config).run_benchmarks(config)

    def checkSystemDeps(self, config: CheriConfig):
        return self.get_real_target(CompilationTargets.NONE, config).checkSystemDeps(config)


# This is used for targets like "libcxx", etc and resolves to "libcxx-cheri/libcxx-native/libcxx-mips"
# at runtime
class MultiArchTargetAlias(_TargetAliasBase):
    def __init__(self, name, projectClass):
        super().__init__(name, projectClass)
        self.derived_targets = []  # type: typing.List[MultiArchTarget]

    def __repr__(self):
        return "<Cross target alias " + self.name + ">"

    def get_real_target(self, cross_target: CrossCompileTarget, config,
                        caller: "typing.Union[SimpleProject, str]" = "<unknown>") -> Target:
        assert cross_target is not None
        assert self.derived_targets, "derived targets must not be empty"
        if cross_target is CompilationTargets.NONE:
            # Use the default target:
            cross_target = self._project_class.get_crosscompile_target(config)
        assert cross_target is not None and cross_target is not CompilationTargets.NONE
        # find the correct derived project:
        for tgt in self.derived_targets:
            if tgt.target_arch is cross_target:
                return tgt
        raise LookupError("Could not find '" + self.name + "' target for " + str(cross_target) + ", caller was " + str(caller))


class SimpleTargetAlias(_TargetAliasBase):
    def __init__(self, name, real_target_name: str, t: "TargetManager"):
        self.real_target = t.get_target_raw(real_target_name)
        real_cls = self.real_target.projectClass
        assert not isinstance(self.real_target, _TargetAliasBase), "Target aliases must reference a real target not another alias"
        super().__init__(name, real_cls)
        self.real_target_name = real_target_name
        # Add the alias name for config lookups so that old configs remain valid
        # Note: we can't modify _alias_target_names since otherwise we change it for all classes
        # noinspection PyProtectedMember
        real_cls._alias_target_names = getattr(real_cls, "_alias_target_names", tuple()) + (self.name, )

    def get_real_target(self, cross_target: CrossCompileTarget, config,
                        caller: "typing.Union[SimpleProject, str]" = "<unknown>") -> Target:
        return self.real_target

    def __repr__(self):
        return "<Target alias " + self.name + " (for " + self.real_target_name + ")>"


class DeprecatedTargetAlias(SimpleTargetAlias):
    def get_real_target(self, cross_target: CrossCompileTarget, config,
                        caller: "typing.Union[SimpleProject, str]" = "<unknown>") -> Target:
        warningMessage("Using deprecated target", self.name, ". Please use ", self.real_target_name, "instead." , sep="")
        return self.real_target


class TargetManager(object):
    def __init__(self):
        self._allTargets = {}  # type: typing.Dict[str, Target]

    def add_target(self, target: Target) -> None:
        assert target.name not in self._allTargets
        self._allTargets[target.name] = target

    def add_target_alias(self, name: str, real_target: str) -> None:
        assert name not in self._allTargets
        self._allTargets[name] = SimpleTargetAlias(name, real_target, self)

    def registerCommandLineOptions(self):
        # this cannot be done in the Project metaclass as otherwise we get
        # RuntimeError: super(): empty __class__ cell
        # https://stackoverflow.com/questions/13126727/how-is-super-in-python-3-implemented/28605694#28605694
        for tgt in self._allTargets.values():
            if not isinstance(tgt, SimpleTargetAlias):
                tgt._project_class.setup_config_options()

    @property
    def targetNames(self):
        return self._allTargets.keys()

    @property
    def targets(self) -> "typing.Iterable[Target]":
        return self._allTargets.values()

    def get_target_raw(self, name: str) -> Target:
        # return the actual target without resolving MultiArchTargetAlias
        return self._allTargets[name]

    def get_target(self, name: str, arch: "CrossCompileTarget", config: CheriConfig,
                   caller: "typing.Union[SimpleProject, str]") -> Target:
        assert arch is not None
        target = self.get_target_raw(name)
        # print("get_target", name, arch, end="")
        if isinstance(target, MultiArchTargetAlias):
            # Pick the default architecture if no arch was passed
            target = target.get_real_target(arch, config, caller=caller)
        # print(" ->", target)
        return target

    @staticmethod
    def sort_in_dependency_order(targets: "typing.List[Target]") -> "typing.List[Target]":
        # pythons sorted() is guaranteed to be stable:
        sorted_targets = list(sorted(targets))
        # remove duplicates (insert into an orderdict to keep order
        return list(OrderedDict((x, True) for x in sorted_targets).keys())

    def get_all_targets(self, explicit_targets: "typing.List[Target]", config: CheriConfig) -> "typing.List[Target]":
        add_dependencies = config.includeDependencies
        chosen_targets = []  # type: typing.List[Target]
        for t in explicit_targets:
            if isinstance(t, SimpleTargetAlias):
                t = t.real_target
            chosen_targets.append(t)
            deps_to_add = []
            if add_dependencies:
                deps_to_add = t.projectClass.recursive_dependencies(config)
            elif t.projectClass.dependenciesMustBeBuilt:
                # some targets such as sdk always need their dependencies build:
                deps_to_add = t.projectClass.recursive_dependencies(config)
            elif t.projectClass.isAlias:
                assert not t.projectClass.dependenciesMustBeBuilt
                # for aliases without full dependencies just add the direct dependencies
                deps_to_add = t.projectClass.direct_dependencies(config)
            # Now add all the dependencies:
            for dep_target in deps_to_add:
                # when --skip-sdk is passed don't include sdk dependencies
                if config.skipSdk and dep_target.projectClass.is_sdk_target:
                    if config.verbose:
                        statusUpdate("Not adding ", t, "dependency", dep_target,
                                     "since it is an SDK target and --skip-sdk was passed.")
                    continue
                chosen_targets.append(dep_target)

        sort = self.sort_in_dependency_order(chosen_targets)
        return sort

    def run(self, config: CheriConfig):
        chosenTargets = self.get_all_chosen_targets(config)

        for target in chosenTargets:
            target.checkSystemDeps(config)
        # all dependencies exist -> run the targets
        for target in chosenTargets:
            if config.print_targets_only:
                statusUpdate("Will build target", coloured(AnsiColour.yellow, target.name))
                print("    Dependencies for", target.name, "are", target.projectClass.allDependencyNames(config))
            else:
                target.execute(config)

    def get_all_chosen_targets(self, config) -> "typing.Iterable[Target]":
        # check that all target dependencies are correct:
        for t in self._allTargets.values():
            if isinstance(t, MultiArchTargetAlias):
                continue
            for dep in t.get_dependencies(config):
                if dep.name not in self._allTargets:
                    sys.exit("Invalid dependency " + dep.name + " for " + t.projectClass.__name__)
        # targetsSorted = sorted(self._allTargets.values())
        # print(" ".join(t.name for t in targetsSorted))
        # assert self._allTargets["llvm"] < self._allTargets["cheribsd"]
        # assert self._allTargets["llvm"] < self._allTargets["all"]
        # assert self._allTargets["disk-image"] > self._allTargets["qemu"]
        # assert self._allTargets["sdk"] > self._allTargets["sdk-sysroot"]
        explicitlyChosenTargets = []  # type: typing.List[Target]
        for targetName in config.targets:
            if targetName not in self._allTargets:
                sys.exit(coloured(AnsiColour.red, "Target", targetName, "does not exist. Valid choices are",
                                  ",".join(self.targetNames)))
            explicitlyChosenTargets.append(self.get_target(targetName, CompilationTargets.NONE, config, caller="cmdline parsing"))
        chosenTargets = self.get_all_targets(explicitlyChosenTargets, config)
        print("Will execute the following targets:\n  ", "\n   ".join(t.name for t in chosenTargets))
        # now that the chosen targets have been resolved run them
        Target.instantiating_targets_should_warn = False  # Fine to instantiate Project() now
        return chosenTargets

    def reset(self):
        for i in self._allTargets.values():
            i.reset()


target_manager = TargetManager()
