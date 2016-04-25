import functools
import time
import sys

from .project import Project
from .utils import *
from .projects.awk import BuildAwk
from .projects.elftoolchain import BuildElfToolchain
from .projects.binutils import BuildBinutils
from .projects.cmake import BuildCMake
from .projects.build_qemu import BuildQEMU
from .projects.cheribsd import BuildCHERIBSD
from .projects.disk_image import BuildDiskImage
from .projects.llvm import BuildLLVM
from .projects.run_qemu import LaunchQEMU
from .projects.sdk import BuildSDK


# A target that does nothing (used for e.g. the all target)
class PseudoTarget(Project):
    def __init__(self, config):
        super().__init__("pseudo", config)

    def process(self):
        pass


class Target(object):
    def __init__(self, name, projectClass, *, dependencies: "typing.Sequence[str]"=set()):
        self.name = name
        self.dependencies = set(dependencies)
        self.projectClass = projectClass

    def execute(self, config: CheriConfig):
        # instantiate the project and run it
        starttime = time.time()
        project = self.projectClass(config)
        project.process()
        statusUpdate("Built target '" + self.name + "' in", time.time() - starttime, "seconds")


class AllTargets(object):
    def __init__(self):
        sdkTarget = Target("sdk", BuildSDK)
        if IS_FREEBSD:
            sdkTarget.dependencies = set(["cheribsd", "llvm"])
        else:
            # cheribsd files need to be copied from another host
            sdkTarget.dependencies = set(["awk", "elftoolchain", "binutils", "llvm"])

        self._allTargets = [
            Target("binutils", BuildBinutils),
            Target("qemu", BuildQEMU),
            Target("cmake", BuildCMake),
            Target("llvm", BuildLLVM),
            Target("awk", BuildAwk),
            Target("elftoolchain", BuildElfToolchain),
            Target("cheribsd", BuildCHERIBSD, dependencies=["llvm"]),
            # SDK only needs to build CHERIBSD if we are on a FreeBSD host, otherwise the files will be copied
            Target("disk-image", BuildDiskImage, dependencies=["cheribsd", "qemu"]),
            sdkTarget,
            Target("run", LaunchQEMU, dependencies=["qemu", "disk-image"]),
            Target("all", PseudoTarget, dependencies=["qemu", "llvm", "cheribsd", "sdk", "disk-image", "run"]),
        ]
        self.targetMap = dict((t.name, t) for t in self._allTargets)
        # for t in self._allTargets:
        #     print("target:", t.name, ", deps", self.recursiveDependencyNames(t))

    def recursiveDependencyNames(self, target: Target, existing: set=None):
        if not existing:
            existing = set()
        for dep in target.dependencies:
            existing.add(dep)
            self.recursiveDependencyNames(self.targetMap[dep], existing)
        return existing

    def topologicalSort(self, targets: "typing.List[Target]") -> "typing.Iterable[typing.List[Target]]":
        # based on http://rosettacode.org/wiki/Topological_sort#Python
        data = dict((t.name, set(t.dependencies)) for t in targets)
        # add all the targets that aren't included yet
        possiblyMissingDependencies = functools.reduce(set.union,
                                                       [self.recursiveDependencyNames(t) for t in targets], set())
        for dep in possiblyMissingDependencies:
            if dep not in data:
                data[dep] = self.targetMap[dep].dependencies

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
        if config.skipDependencies:
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
            target.execute(config)

