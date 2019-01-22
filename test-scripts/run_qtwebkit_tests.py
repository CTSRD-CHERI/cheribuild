#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
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
import pexpect
import argparse
import os
import subprocess
from pathlib import Path
import boot_cheribsd
from junitparser import JUnitXml


def setup_qtwebkit_test_environment(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace):
    boot_cheribsd.run_cheribsd_command(qemu, "export LD_LIBRARY_PATH=/lib:/usr/lib:/usr/local/lib:/sysroot/lib:/sysroot/usr/lib:/sysroot/usr/local/lib")
    boot_cheribsd.run_cheribsd_command(qemu, "export LD_CHERI_LIBRARY_PATH=/usr/libcheri:/usr/local/libcheri:/sysroot/libcheri:/sysroot/usr/libcheri:/sysroot/usr/local/libcheri")
    boot_cheribsd.run_cheribsd_command(qemu, "export ICU_DATA=/sysroot/usr/local/share/icu/60.0.1")
    boot_cheribsd.run_cheribsd_command(qemu, "export LANG=en_US.UTF-8")
    boot_cheribsd.run_cheribsd_command(qemu, "echo '<h1>Hello World!</h1>' > /tmp/helloworld.html")

    # mime database
    boot_cheribsd.run_cheribsd_command(qemu, "mkdir -p /usr/share/mime/packages")
    boot_cheribsd.run_cheribsd_command(qemu, "mkdir -p /usr/local/Qt-cheri/lib/fonts")
    boot_cheribsd.run_cheribsd_command(qemu, "ln -sf /usr/local/Qt-cheri /usr/local/Qt-mips")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "cp /source/LayoutTests/resources/Ahem.ttf /usr/local/Qt-cheri/lib/fonts")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "cp /source/LayoutTests/fast/writing-mode/resources/DroidSansFallback-reduced.ttf /usr/local/Qt-cheri/lib/fonts")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "cp /build/mime.cache /usr/share/mime")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "cp /build/freedesktop.org.xml /usr/share/mime/packages/freedesktop.org.xml")

    boot_cheribsd.success("To debug crashes run: `sysctl kern.corefile=/build/%N.%P.core; sysctl kern.coredump=1` and then run CHERI gdb on the host system.")

    # copy the smaller files to /tmp to avoid the smbfs overhead
    boot_cheribsd.checked_run_cheribsd_command(qemu, "cp /build/bin/jsc.stripped /tmp/jsc")
    boot_cheribsd.checked_run_cheribsd_command(qemu, "cp /build/bin/DumpRenderTree.stripped /tmp/DumpRenderTree")


def run_qtwebkit_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running QtWebkit tests")
    try:
        # Check that jsc + dumprendertree work
        boot_cheribsd.checked_run_cheribsd_command(qemu, "/tmp/jsc --help", timeout=1200)
        boot_cheribsd.checked_run_cheribsd_command(qemu, "/tmp/DumpRenderTree -v /tmp/helloworld.html", timeout=1800)
        boot_cheribsd.checked_run_cheribsd_command(qemu, "/tmp/DumpRenderTree -p --stdout /build/hello.png /tmp/helloworld.html", timeout=1800)

        boot_cheribsd.checked_run_cheribsd_command(qemu, "/source/Tools/Scripts/run-layout-jsc -j /tmp/jsc -t /source/LayoutTests -r /build/results -x /build/results.xml", timeout=None)
        return True
    finally:
        tests_xml_path = Path(args.build_dir, 'results.xml')
        try:
            if tests_xml_path.exists():
                # Process junit xml file with junitparser to update the number of tests, failures, total time, etc.
                xml = JUnitXml.fromfile(str(tests_xml_path))
                xml.update_statistics()
                xml.write()
        except:
            boot_cheribsd.failure("Could not update JUnit XML", tests_xml_path, exit=False)
            return False


if __name__ == '__main__':
    from run_tests_common import run_tests_main
    # we don't need ssh running to execute the tests, but we need both host and source dir mounted
    run_tests_main(test_function=run_qtwebkit_tests, test_setup_function=setup_qtwebkit_test_environment,
                   need_ssh=False, should_mount_builddir=True, should_mount_srcdir=True, should_mount_sysroot=True)
