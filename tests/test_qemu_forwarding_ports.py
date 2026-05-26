from pathlib import Path

import pytest

from .setup_mock_chericonfig import CheriConfig, setup_mock_chericonfig
from pycheribuild.projects import *  # noqa: F401, F403, RUF100
from pycheribuild.projects.cross import *  # noqa: F401, F403, RUF100
from pycheribuild.targets import target_manager


def _get_original_class(cls) -> type:
    return getattr(cls, "synthetic_base", cls)


def test_qemu_launch_ports_no_conflict():
    from pycheribuild.projects.run_qemu import LaunchQEMUBase

    config: CheriConfig = setup_mock_chericonfig(Path("/this/path/does/not/exist"))
    target_manager.register_command_line_options()

    # Find all targets that derive from LaunchQEMUBase
    qemu_targets = []
    for target in target_manager.targets(config):
        cls = target.project_class
        if issubclass(cls, LaunchQEMUBase) and cls is not LaunchQEMUBase:
            qemu_targets.append(target)

    # For each QEMU target, check all supported architectures and collect the forwarding ports
    allocated_ports = {}  # port -> (target_object, cls_object, arch_object)
    for target in qemu_targets:
        cls = target.project_class
        for arch in cls.supported_architectures():
            try:
                project_instance = cls.get_instance(None, config=config, cross_target=arch)
            except Exception:
                continue

            if not project_instance.forward_ssh_port:
                continue

            port = project_instance.ssh_forwarding_port
            if port is not None:
                if port in allocated_ports:
                    prev_target, prev_cls, prev_arch = allocated_ports[port]
                    if _get_original_class(prev_cls) is _get_original_class(cls) and prev_arch == arch:
                        # This is just an alias target name for the exact same class/architecture, not a conflict!
                        continue
                    pytest.fail(
                        f"SSH Port conflict detected! Port {port} is used by both:\n"
                        f"1) Target: {prev_target.name} (Class: {prev_cls.__name__}, "
                        f"Arch: {prev_arch.generic_arch_suffix})\n"
                        f"2) Target: {target.name} (Class: {cls.__name__}, "
                        f"Arch: {arch.generic_arch_suffix})"
                    )
                allocated_ports[port] = (target, cls, arch)
