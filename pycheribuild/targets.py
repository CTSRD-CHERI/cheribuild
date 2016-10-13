import functools
import time
import sys

from .utils import *


class Target(object):
    def __init__(self, name, projectClass, *, dependencies: set=set()):
        self.name = name
        self.dependencies = set(dependencies)
        self.projectClass = projectClass
        self.project = None
        self._completed = False

    def checkSystemDeps(self, config: CheriConfig):
        if self._completed:
            return
        self.project = self.projectClass(config)
        with setEnv(PATH=self.project.config.dollarPathWithOtherTools):
            # make sure all system dependencies exist first
            self.project.checkSystemDependencies()

    def execute(self):
        if self._completed:
            # TODO: make this an error once I have a clean solution for the pseudo targets
            # warningMessage(target.name, "has already been executed!")
            return
        # instantiate the project and run it
        starttime = time.time()
        with setEnv(PATH=self.project.config.dollarPathWithOtherTools):
            self.project.process()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._completed = True


# A target that does nothing (used for e.g. the all target)
# TODO: ideally we would do proper dependency resolution and not run targets multiple times
class PseudoTarget(Target):
    def __init__(self, allTargets: "AllTargets", name: str, *, orderedDependencies: "typing.List[str]"=list()):
        super().__init__(name, None, dependencies=set(orderedDependencies))
        self.allTargets = allTargets
        # TODO: somehow resolve dependencies properly but also include them without --include-dependencies
        self.orderedDependencies = orderedDependencies
        if not orderedDependencies:
            fatalError("PseudoTarget with no dependencies should not exist:!!", "Target name =", name)

    def checkSystemDeps(self, config: CheriConfig):
        if self._completed:
            return
        for dep in self.orderedDependencies:
            target = self.allTargets.targetMap[dep]  # type: Target
            if target._completed:
                continue
            target.checkSystemDeps(config)

    def execute(self):
        if self._completed:
            return
        starttime = time.time()
        for dep in self.orderedDependencies:
            target = self.allTargets.targetMap[dep]  # type: Target
            if target._completed:
                # warningMessage("Already processed", target.name, "while processing pseudo target", self.name)
                continue
            target.execute()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._completed = True


class TargetManager(object):
    def __init__(self):
        if IS_FREEBSD:
            sdkTargetDeps = ["llvm", "cheribsd"]
        else:
            sdkTargetDeps = ["awk", "elftoolchain", "binutils", "llvm"]
            # These need to be built on Linux but are not required on FreeBSD
        sdkTarget = PseudoTarget(self, "sdk", orderedDependencies=sdkTargetDeps + ["sdk-sysroot"])
        allTarget = PseudoTarget(self, "all", orderedDependencies=["qemu", "sdk", "disk-image", "run"])

        self._allTargets = {}
        self.addTarget(sdkTarget)
        self.addTarget(allTarget)
        # for t in self.allTargets:
        #     print("target:", t.name, ", deps", self.recursiveDependencyNames(t))

    def addTarget(self, target: Target):
        self._allTargets[target.name] = target

    @property
    def targetNames(self):
        return self._allTargets.keys()

    @property
    def targetMap(self):
        return self._allTargets.copy()

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
                print("Level", dependencyLevel, "targets:", targetNames)
                chosenTargets.extend(self.targetMap[t] for t in targetNames)
        # now that the chosen targets have been resolved run them
        for target in chosenTargets:
            target.checkSystemDeps(config)
        # all dependencies exist -> run the targets
        for target in chosenTargets:
            target.execute()

targetManager = TargetManager()
