import typing

import pytest

from .setup_mock_chericonfig import CheriConfig, setup_mock_chericonfig
from pycheribuild.projects import *  # noqa: F401, F403, RUF100
from pycheribuild.projects.cross import *  # noqa: F401, F403, RUF100
from pycheribuild.projects.run_qemu import LaunchQEMUBase
from pycheribuild.targets import target_manager


def test_qemu_launch_ports_no_conflict():
    config: CheriConfig = setup_mock_chericonfig()
    config.enable_hybrid_targets = True
    target_manager.register_command_line_options()

    # For each QEMU target, check all supported architectures and collect the forwarding ports
    allocated_ports = {}  # port -> (target_object, cls_object, arch_object)
    for target in target_manager.targets(config):
        cls = target.project_class
        if not issubclass(cls, LaunchQEMUBase):
            continue
        project_instance = typing.cast(LaunchQEMUBase, target.create_project(config))
        if not project_instance.forward_ssh_port:
            print("Ignoring", target.name, "since it does not forward SSH port")
            continue

        port = project_instance.ssh_forwarding_port
        print("Checking", target.name, "->", port)
        assert port is not None, "Port should be set since forward_ssh_port=True"
        if port in allocated_ports:
            prev_target, prev_cls, prev_arch = allocated_ports[port]
            pytest.fail(
                f"SSH Port conflict detected! Port {port} is used by both:\n"
                f"1) Target: {prev_target.name} (Class: {prev_cls.__name__}, Arch: {prev_arch})\n"
                f"2) Target: {target.name} (Class: {cls.__name__}, Arch: {project_instance.crosscompile_target})"
            )
        allocated_ports[port] = (target, cls, project_instance.crosscompile_target)
