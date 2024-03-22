#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
#
# Copyright (c) 2021 Brett F. Gutstein
# All rights reserved.
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
import argparse
import datetime

from run_tests_common import (
    boot_cheribsd,
    finish_and_write_junit_xml_report,
    get_default_junit_xml_name,
    junitparser,
    run_tests_main,
)


def setup_webkit_tests(qemu: boot_cheribsd.CheriBSDInstance, _: argparse.Namespace) -> None:
    qemu.checked_run(
        f"export LD_LIBRARY_PATH=/opt/{qemu.xtarget.generic_arch_suffix}/webkit/lib:"
        f"/usr/local/{qemu.xtarget.generic_arch_suffix}/lib/",
    )


def run_webkit_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    boot_cheribsd.info("Running SunSpider jsc tests")
    sunspider_tests = [
        "3d-cube.js",
        "access-fannkuch.js",
        "bitops-bits-in-byte.js",
        "crypto-aes.js",
        "date-format-xparb.js",
        "regexp-dna.js",
        "string-tagcloud.js",
        "3d-morph.js",
        "access-nbody.js",
        "bitops-bitwise-and.js",
        "crypto-md5.js",
        "math-cordic.js",
        "string-unpack-code.js",
        "3d-raytrace.js",
        "access-nsieve.js",
        "bitops-nsieve-bits.js",
        "crypto-sha1.js",
        "math-partial-sums.js",
        "string-base64.js",
        "string-validate-input.js",
        "access-binary-trees.js",
        "bitops-3bit-bits-in-byte.js",
        "controlflow-recursive.js",
        "date-format-tofte.js",
        "math-spectral-norm.js",
        "string-fasta.js",
    ]
    xml = junitparser.JUnitXml()
    all_tests_starttime = datetime.datetime.utcnow()
    for test in sunspider_tests:
        suite = junitparser.TestSuite(name=test)
        t = junitparser.TestCase(name=test)
        starttime = datetime.datetime.utcnow()
        try:
            qemu.checked_run(
                f"/opt/{qemu.xtarget.generic_arch_suffix}/webkit/bin/jsc"
                f" /source/PerformanceTests/SunSpider/tests/sunspider-1.0.2/{test}",
                timeout=300,
            )
        except boot_cheribsd.CheriBSDCommandFailed as e:
            boot_cheribsd.failure("Failed to run ", test, ": ", str(e), exit=False)
            if isinstance(e, boot_cheribsd.CheriBSDCommandTimeout):
                t.result = junitparser.Failure(message="Command timed out")
                # Send CTRL+C if the process timed out.
                qemu.sendintr()
                qemu.sendintr()
                qemu.expect_prompt(timeout=5 * 60)
            else:
                t.result = junitparser.Failure(message="Command failed")
        t.time = (datetime.datetime.utcnow() - starttime).total_seconds()
        suite.add_testcase(t)
        xml.add_testsuite(suite)
    return finish_and_write_junit_xml_report(all_tests_starttime, xml, args.junit_xml)


def adjust_args(args: argparse.Namespace):
    args.junit_xml = get_default_junit_xml_name(args.junit_xml, args.build_dir)


def add_args(parser: argparse.ArgumentParser):
    parser.add_argument("--junit-xml", required=False, help="Output file name for the JUnit XML results")


if __name__ == "__main__":
    run_tests_main(
        test_function=run_webkit_tests,
        test_setup_function=setup_webkit_tests,
        argparse_adjust_args_callback=adjust_args,
        argparse_setup_callback=add_args,
        should_mount_builddir=False,
        should_mount_srcdir=True,
    )
