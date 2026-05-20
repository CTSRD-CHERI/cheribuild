#!/usr/bin/env python3
#
# Copyright (c) 2026 Alex Richardson
# All rights reserved.
#
# Test script to check that the built binaries are truly portable and run
# on a plain Rocky Linux 8 docker container without any extra libraries installed.
#

import subprocess
import sys
from pathlib import Path


def run_cmd(args, **kwargs):
    print(f"Running: {' '.join(map(str, args))}")
    return subprocess.run(args, check=True, **kwargs)


def main():
    cheribuild_dir = Path(__file__).resolve().parent.parent

    # Resolve output directory using docker-portable-cheribuild.py
    try:
        res = subprocess.run(
            [
                sys.executable,
                str(cheribuild_dir / "docker-portable-cheribuild.py"),
                "--get-config-option",
                "output-root",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            text=True,
        )
        output_root = Path(res.stdout.strip())
    except subprocess.CalledProcessError:
        output_root = Path.home() / "cheri/output-portable"

    if len(sys.argv) > 1:
        output_root = Path(sys.argv[1])

    if not output_root.exists():
        print(f"Error: Output root directory '{output_root}' does not exist.", file=sys.stderr)
        print("Please compile the portable binaries first or specify the correct output path.", file=sys.stderr)
        sys.exit(1)

    bin_dir = output_root / "sdk/bin"
    if not bin_dir.exists():
        print(f"Error: Binary directory '{bin_dir}' does not exist inside output root.", file=sys.stderr)
        sys.exit(1)

    # Find binaries to test
    test_binaries = []
    if (bin_dir / "clang").is_file():
        test_binaries.append("clang")
    if (bin_dir / "clang++").is_file():
        test_binaries.append("clang++")

    # Find only one of the qemu-system-* binaries (they all have the same dependencies)
    qemu_binaries = [item.name for item in bin_dir.glob("qemu-system-*") if item.is_file()]
    if qemu_binaries:
        qemu_binaries.sort()
        test_binaries.append(qemu_binaries[0])

    if not test_binaries:
        print(f"Error: No clang or qemu-system-* binaries found in '{bin_dir}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Found binaries to test: {', '.join(test_binaries)}")

    docker_images = [
        "rockylinux/rockylinux:8",
        "ubuntu:22.04",
        "ubuntu:20.04",
    ]

    failed = False
    for image in docker_images:
        print("\n==================================================")
        print(f"Testing portability on docker image: {image}")
        print("==================================================")
        print(f"Ensuring {image} docker image is available...")
        run_cmd(["docker", "pull", image])

        for binary in test_binaries:
            binary_path = f"/output/sdk/bin/{binary}"
            print(f"\nTesting portability of {binary} on plain {image}...")
            print(f"  Host path: {bin_dir / binary}")
            print(f"  Container path: {binary_path}")

            # Run ldd inside the docker container
            ldd_cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{output_root}:/output:ro",
                image,
                "ldd",
                binary_path,
            ]
            print("  Running ldd on the binary in the container...")
            res_ldd = subprocess.run(ldd_cmd, capture_output=True, check=False, text=True)
            print("  ldd stdout:")
            print(res_ldd.stdout)
            if res_ldd.stderr:
                print("  ldd stderr:", file=sys.stderr)
                print(res_ldd.stderr, file=sys.stderr)

            # We run docker with read-only mounting for safety to test --version
            cmd = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{output_root}:/output:ro",
                image,
                binary_path,
                "--version",
            ]

            res = subprocess.run(cmd, capture_output=True, check=False, text=True)
            if res.returncode == 0:
                print(f"  [PASS] {binary} starts up and runs --version successfully.")
            else:
                print(f"  [FAIL] {binary} failed to run on plain {image}!", file=sys.stderr)
                print("  Stderr/Stdout:", file=sys.stderr)
                print(res.stdout, file=sys.stderr)
                print(res.stderr, file=sys.stderr)
                failed = True

    if failed:
        print(
            "Portability check FAILED. One or more binaries failed to run on the target containers.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("All checks PASSED! The binaries are successfully portable.")
    sys.exit(0)


if __name__ == "__main__":
    main()
