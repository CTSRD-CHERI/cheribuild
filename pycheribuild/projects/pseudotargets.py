from ..project import Project
from ..utils import *


# A target that does nothing (used for e.g. the "all" target)
class PseudoTarget(Project):
    doNotAddToTargets = True
    dependenciesMustBeBuilt = True

    def process(self):
        pass


class BuildCheriBSDSdk(PseudoTarget):
    target = "sdk"
    dependencies = ["sdk-sysroot"]


class BuildAll(PseudoTarget):
    dependencies = ["qemu", "sdk", "disk-image", "run"]
