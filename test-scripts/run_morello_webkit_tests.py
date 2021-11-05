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

from run_tests_common import boot_cheribsd, run_tests_main


def run_webkit_tests(qemu: boot_cheribsd.CheriBSDInstance, args: argparse.Namespace) -> bool:
    target_arch = qemu.xtarget.generic_arch_suffix
    qemu.checked_run("export LD_LIBRARY_PATH=/opt/{ta}/webkit/lib:/usr/local/{ta}/lib/".format(ta=target_arch))
    boot_cheribsd.info("Running SunSpider jsc tests")
    sunspider_tests = ["3d-cube.js", "access-fannkuch.js", "bitops-bits-in-byte.js", "crypto-aes.js",
                       "date-format-xparb.js", "regexp-dna.js", "string-tagcloud.js", "3d-morph.js",
                       "access-nbody.js", "bitops-bitwise-and.js", "crypto-md5.js", "math-cordic.js",
                       "string-unpack-code.js", "3d-raytrace.js", "access-nsieve.js", "bitops-nsieve-bits.js",
                       "crypto-sha1.js", "math-partial-sums.js", "string-base64.js", "string-validate-input.js",
                       "access-binary-trees.js", "bitops-3bit-bits-in-byte.js", "controlflow-recursive.js",
                       "date-format-tofte.js", "math-spectral-norm.js", "string-fasta.js"]
    tests_successful = True
    for test in sunspider_tests:
        try:
            qemu.checked_run("/opt/{ta}/webkit/bin/jsc /source/PerformanceTests/SunSpider/tests/sunspider-1.0.2/{test}"
                             .format(ta=target_arch, test=test), timeout=300)
        except boot_cheribsd.CheriBSDCommandFailed as e:
            boot_cheribsd.failure("Failed to run ", test, ": ", str(e), exit=False)
            if isinstance(e, boot_cheribsd.CheriBSDCommandTimeout):
                # Send CTRL+C if the process timed out.
                qemu.sendintr()
                qemu.sendintr()
                qemu.expect_prompt(timeout=5 * 60)
            tests_successful = False
    return tests_successful


if __name__ == '__main__':
    run_tests_main(test_function=run_webkit_tests, should_mount_builddir=False, should_mount_srcdir=True)
