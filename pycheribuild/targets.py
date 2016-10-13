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
            warningMessage(self.name, "has already been executed!")
            return
        # instantiate the project and run it
        starttime = time.time()
        with setEnv(PATH=self.project.config.dollarPathWithOtherTools):
            self.project.process()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")
        self._completed = True

    def __lt__(self, other: "Target"):
        if other.name == "run":
            return True  # run must be executed last
        # if this target is one of the dependencies order it before
        otherDeps = other.projectClass.allDependencyNames()
        if self.name in otherDeps:
            return True
        ownDeps = self.projectClass.allDependencyNames()
        if len(ownDeps) < len(otherDeps):
            return True
        if len(ownDeps) > len(otherDeps):
            return False
        return self.name < other.name  # not a dep and number of deps is the same -> compare name


class TargetManager(object):
    def __init__(self):
        self._allTargets = {}

    def addTarget(self, target: Target):
        self._allTargets[target.name] = target

    @property
    def targetNames(self):
        return self._allTargets.keys()

    @property
    def targetMap(self):
        return self._allTargets.copy()

    def topologicalSort(self, targets: "typing.List[Target]") -> "typing.Iterable[typing.List[Target]]":
        # based on http://rosettacode.org/wiki/Topological_sort#Python
        data = dict((t.name, set(t.dependencies)) for t in targets)

        # add all the targets that aren't included yet
        allDependencyNames = [t.projectClass.allDependencyNames() for t in targets]
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
        # check that all target dependencies are correct:
        for t in self._allTargets.values():
            for dep in t.dependencies:
                if dep not in self._allTargets:
                    sys.exit("Invalid dependency " + dep + " for " + t.projectClass.__name__)

        # targetsSorted = sorted(self._allTargets.values())
        # print(" ".join(t.name for t in targetsSorted))
        # assert self._allTargets["llvm"] < self._allTargets["cheribsd"]
        # assert self._allTargets["llvm"] < self._allTargets["all"]
        # assert self._allTargets["disk-image"] > self._allTargets["qemu"]
        # assert self._allTargets["sdk"] > self._allTargets["sdk-sysroot"]

        explicitlyChosenTargets = []  # type: typing.List[Target]
        for targetName in config.targets:
            if targetName not in self._allTargets:
                fatalError("Target", targetName, "does not exist. Valid choices are", ",".join(self.targetNames))
                sys.exit(1)
            explicitlyChosenTargets.append(self.targetMap[targetName])
        if config.skipDependencies:  # FIXME: remove this soon
            warningMessage("--skip-dependencies/-t flag is now the default behaviour and will be removed soon.")

        chosenTargets = []
        if not config.includeDependencies:
            # The wants only the explicitly passed targets to be executed, don't do any ordering
            # we still reorder them to ensure that they are run in the right order
            for t in explicitlyChosenTargets:
                if t.projectClass.dependenciesMustBeBuilt:
                    chosenTargets.extend(self.targetMap[dep] for dep in t.projectClass.allDependencyNames())
                chosenTargets.append(t)
            chosenTargets = sorted(chosenTargets)
        else:
            # Otherwise run all targets in dependency order
            chosenTargets = []
            orderedTargets = self.topologicalSort(explicitlyChosenTargets)  # type: typing.Iterable[typing.List[Target]]
            for dependencyLevel, targetNames in enumerate(orderedTargets):
                # print("Level", dependencyLevel, "targets:", targetNames)
                chosenTargets.extend(self.targetMap[t] for t in targetNames)

        if config.verbose:
            print("Will execute the following targets:", " ".join(t.name for t in chosenTargets))
        # now that the chosen targets have been resolved run them
        for target in chosenTargets:
            target.checkSystemDeps(config)
        # all dependencies exist -> run the targets
        for target in chosenTargets:
            target.execute()

targetManager = TargetManager()
