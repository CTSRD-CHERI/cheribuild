#
# Copyright (c) 2018 Alex Richardson
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

from ...utils import getCompilerInfo, Type_T
from ..project import SimpleProject, Project
from ...targets import targetManager, MultiArchTargetAlias
from ...config.chericonfig import CrossCompileTarget, CheriConfig

# A base class for all multi-arch projects (ensures that the appropriate -native/-cheri/-mips targets are added)
class MultiArchBaseMixin(object):
    doNotAddToTargets = True
    CAN_TARGET_ALL_TARGETS = [CrossCompileTarget.CHERI, CrossCompileTarget.MIPS, CrossCompileTarget.NATIVE]
    # WARNING: baremetal CHERI probably doesn't work
    CAN_TARGET_ALL_BAREMETAL_TARGETS = [CrossCompileTarget.MIPS, CrossCompileTarget.CHERI]
    CAN_TARGET_ALL_TARGETS_EXCEPT_CHERI = [CrossCompileTarget.NATIVE, CrossCompileTarget.MIPS]
    CAN_TARGET_ALL_TARGETS_EXCEPT_NATIVE = [CrossCompileTarget.CHERI, CrossCompileTarget.MIPS]
    supported_architectures = CAN_TARGET_ALL_TARGETS # TODO: once risc-v works: list(CrossCompileTarget)
    # The architecture to build for if no --xmips/--xhost flag is passed (defaults to supported_architectures[0] if no match)
    default_architecture = None
    appendCheriBitsToBuildDir = True
    _crossCompileTarget = None  # type: CrossCompileTarget
    # only the subclasses generated in the ProjectSubclassDefinitionHook can have __init__ called
    _should_not_be_instantiated = True
    # noinspection PyProtectedMember
    _no_overwrite_allowed = Project._no_overwrite_allowed + ("_crossCompileTarget",)

    @property
    def crosscompile_target(self):
        return self.get_crosscompile_target(self.config)

    @classmethod
    def get_crosscompile_target(cls, config: CheriConfig) -> CrossCompileTarget:
        target = cls._crossCompileTarget
        if target is not None:
            return target
        # Find the best match based on config.crossCompileTarget
        default_target = config.crossCompileTarget
        assert cls.supported_architectures, "Must not be empty"
        # if we can build the default target (--xmips/--xhost) chose that
        if default_target in cls.supported_architectures:
            return default_target
        # otherwise fall back to the default specified in the class
        result = cls.default_architecture
        if not result:
            # otherwise pick the first supported arch:
            result = cls.supported_architectures[0]
        # Otherwise pick the best match:
        if default_target == CrossCompileTarget.CHERI and result == CrossCompileTarget.MIPS:
            # add this note for e.g. GDB:
            cls._configure_status_message = "Cannot compile " + cls.target + " in CHERI purecap mode, building MIPS binaries instead"
        return result

    def __init__(self, config: CheriConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        assert isinstance(self, SimpleProject)

    def get_host_triple(self):
        compiler = getCompilerInfo(self.config.clangPath if self.config.clangPath else shutil.which("cc"))
        return compiler.default_target

    def compiling_for_mips(self):
        return self._crossCompileTarget == CrossCompileTarget.MIPS

    def compiling_for_cheri(self):
        return self._crossCompileTarget == CrossCompileTarget.CHERI

    def compiling_for_host(self):
        return self._crossCompileTarget == CrossCompileTarget.NATIVE

    def compiling_for_riscv(self):
        return self._crossCompileTarget == CrossCompileTarget.RISCV

    @property
    def display_name(self):
        return self.projectName + " (" + self._crossCompileTarget.value + ")"

    @classmethod
    def get_class_for_target(cls: "typing.Type[Type_T]", arch: CrossCompileTarget) -> "typing.Type[Type_T]":
        target = targetManager.get_target_raw(cls.target)
        assert isinstance(target, MultiArchTargetAlias)
        for t in target.derived_targets:
            if t.target_arch == arch:
                return t.projectClass
        raise LookupError("Invalid arch " + str(arch) + " for class " + str(self))
