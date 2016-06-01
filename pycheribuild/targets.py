import functools
import time
import sys

from .project import Project
from .utils import *
from .projects.awk import BuildAwk
from .projects.elftoolchain import BuildElfToolchain
from .projects.binutils import BuildBinutils
from .projects.cmake import BuildCMake
from .projects.cherios import BuildCheriOS
from .projects.gnustep import BuildGnuStep
from .projects.cheritrace import BuildCheriTrace
from .projects.cherivis import BuildCheriVis
from .projects.build_qemu import BuildQEMU
from .projects.cheribsd import BuildCHERIBSD
from .projects.disk_image import BuildDiskImage
from .projects.llvm import BuildLLVM
from .projects.run_qemu import LaunchQEMU
from .projects.sdk import BuildSDK


class Target(object):
    def __init__(self, name, projectClass, *, dependencies: "typing.Sequence[str]"=set()):
        self.name = name
        self.dependencies = set(dependencies)
        self.projectClass = projectClass
        self.project = None

    def checkSystemDeps(self, config: CheriConfig):
        self.project = self.projectClass(config)
        with setEnv(PATH=self.project.config.dollarPathWithOtherTools):
            # make sure all system dependencies exist first
            self.project.checkSystemDependencies()

    def execute(self):
        # instantiate the project and run it
        starttime = time.time()
        with setEnv(PATH=self.project.config.dollarPathWithOtherTools):
            self.project.process()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")


# A target that does nothing (used for e.g. the all target)
# TODO: ideally we would do proper dependency resolution and not run targets multiple times
class PseudoTarget(Target):
    def __init__(self, allTargets: "AllTargets", name: str, *, dependencies: "typing.List[str]"=list()):
        super().__init__(name, None, dependencies=dependencies)
        self.allTargets = allTargets
        # TODO: somehow resolve dependencies properly but also include them without --include-dependencies
        self.sortedDependencies = dependencies
        if not dependencies:
            fatalError("PseudoTarget with no dependencies should not exist:!!", "Target name =", name)

    def checkSystemDeps(self, config: CheriConfig):
        for dep in self.sortedDependencies:
            target = self.allTargets.targetMap[dep]  # type: Target
            target.checkSystemDeps(config)

    def execute(self):
        starttime = time.time()
        for dep in self.sortedDependencies:
            target = self.allTargets.targetMap[dep]  # type: Target
            target.execute()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._completed = True


class AllTargets(object):
    def __init__(self):
        sdkTarget = Target("sdk", BuildSDK)
        cheriosTarget = Target("cherios", BuildCheriOS)
        if IS_FREEBSD:
            sdkTarget.dependencies = {"cheribsd", "llvm"}
            cheriosTarget.dependencies = {"sdk"}
            allTargetDeps = ["qemu", "llvm", "cheribsd", "sdk", "disk-image", "run"]
        else:
            sdkTarget.dependencies = {"awk", "elftoolchain", "binutils", "llvm"}
            cheriosTarget.dependencies = {"elftoolchain", "binutils", "llvm"}
            # These need to be built on Linux but are not required on FreeBSD
            # CHERIBSD files need to be copied from another host, so we don't build cheribsd here
            allTargetDeps = ["awk", "binutils", "elftoolchain" "qemu", "llvm", "sdk", "disk-image", "run"]
        allTarget = PseudoTarget(self, "all", dependencies=allTargetDeps)

        self._allTargets = [
            Target("binutils", BuildBinutils),
            Target("qemu", BuildQEMU),
            Target("cmake", BuildCMake),
            Target("llvm", BuildLLVM),
            Target("awk", BuildAwk),
            Target("elftoolchain", BuildElfToolchain),
            Target("cheritrace", BuildCheriTrace, dependencies=["llvm"]),
            Target("cherivis", BuildCheriVis, dependencies=["cheritrace"]),
            Target("gnustep", BuildGnuStep),
            Target("cheribsd", BuildCHERIBSD, dependencies=["llvm"]),
            Target("disk-image", BuildDiskImage, dependencies=["cheribsd", "qemu"]),
            sdkTarget,  # SDK only needs to build CHERIBSD if we are on FreeBSD, otherwise the files will be copied
            cheriosTarget,
            Target("run", LaunchQEMU, dependencies=["qemu", "disk-image"]),
            allTarget
        ]
        self.targetMap = dict((t.name, t) for t in self._allTargets)
        # for t in self._allTargets:
        #     print("target:", t.name, ", deps", self.recursiveDependencyNames(t))

    def recursiveDependencyNames(self, target: Target, *, existing: set=None):
        if not existing:
            existing = set()
        for dep in target.dependencies:
            existing.add(dep)
            self.recursiveDependencyNames(self.targetMap[dep], existing=existing)
        return existing

    def topologicalSort(self, targets: "typing.List[Target]") -> "typing.Iterable[typing.List[Target]]":
        # based on http://rosettacode.org/wiki/Topological_sort#Python
        data = dict((t.name, set(t.dependencies)) for t in targets)

        # add all the targets that aren't included yet
        allDependencyNames = [self.recursiveDependencyNames(t) for t in targets]
        possiblyMissingDependencies = functools.reduce(set.union, allDependencyNames, set())
        for dep in possiblyMissingDependencies:
            if dep not in data:
                data[dep] = self.targetMap[dep].dependencies

        # do the actual sorting
        while True:
            ordered = set(item for item, dep in data.items() if not dep)
            if not ordered:
                break
            yield list(sorted(ordered))
            data = {item: (dep - ordered) for item, dep in data.items()
                    if item not in ordered}
        assert not data, "A cyclic dependency exists amongst %r" % data

    def run(self, config: CheriConfig):
        explicitlyChosenTargets = []  # type: typing.List[Target]
        for targetName in config.targets:
            if targetName not in self.targetMap:
                fatalError("Target", targetName, "does not exist. Valid choices are", ",".join(self.targetMap.keys()))
                sys.exit(1)
            explicitlyChosenTargets.append(self.targetMap[targetName])
        if config.skipDependencies:  # FIXME: remove this soon
            warningMessage("--skip-dependencies/-t flag is now the default behaviour and will be removed soon.")
        if not config.includeDependencies:
            # The wants only the explicitly passed targets to be executed, don't do any ordering
            chosenTargets = explicitlyChosenTargets  # TODO: ensure right order?
        else:
            # Otherwise run all targets in dependency order
            chosenTargets = []
            orderedTargets = self.topologicalSort(explicitlyChosenTargets)  # type: typing.Iterable[typing.List[Target]]
            for dependencyLevel, targetNames in enumerate(orderedTargets):
                # print("Level", dependencyLevel, "targets:", targetNames)
                chosenTargets.extend(self.targetMap[t] for t in targetNames)
        # now that the chosen targets have been resolved run them
        for target in chosenTargets:
            target.checkSystemDeps(config)
        # all dependencies exist -> run the targets
        for target in chosenTargets:
            target.execute()

