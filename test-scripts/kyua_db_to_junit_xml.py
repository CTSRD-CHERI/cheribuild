#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
# -
# Copyright (c) 2019 Alex Richardson
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
import tempfile
from pathlib import Path
from typing import Optional

from run_tests_common import boot_cheribsd, junitparser


def convert_kyua_db_to_junit_xml(db_file: Path, output_file: Path, prefix: "Optional[str]" = None):
    assert output_file.resolve() != db_file.resolve()
    with output_file.open("w") as output_stream:
        command = ["kyua", "report-junit", "--results-file=" + str(db_file)]
        boot_cheribsd.run_host_command(command, stdout=output_stream)
        # TODO: xml escape the file?
        if not boot_cheribsd.PRETEND:
            fixup_kyua_generated_junit_xml(output_file, prefix)


def fixup_kyua_generated_junit_xml(xml_file: Path, prefix: "Optional[str]" = None):
    boot_cheribsd.info("Updating statistics in JUnit file ", xml_file)
    # Process junit xml file with junitparser to update the number of tests, failures, total time, etc.
    orig_xml_str = xml_file.read_text("utf-8", errors="backslashreplace")
    xml_str = orig_xml_str
    for i in range(32):
        if chr(i) not in ("\n", "\t"):
            # Can't reference NULL character -> backslashescape instead
            # xml_str = xml_str.replace(chr(i), "&#" + str(i) + ";")
            xml_str = xml_str.replace(chr(i), "\\x" + format(i, "02x") + ";")
    with tempfile.NamedTemporaryFile("wb") as tf:
        # create a temporary file first to avoid clobbering the original one if we fail to parse it
        tf.write(xml_str.encode("ascii", errors="xmlcharrefreplace"))
        tf.flush()
        xml = junitparser.JUnitXml.fromfile(tf.name)
        xml.update_statistics()
        if prefix is not None:
            if isinstance(xml, junitparser.TestSuite):
                xml.name = prefix if xml.name is None else prefix + "/" + xml.name
                # Some projects produce a JUnit XML with a single <testsuite> root element
                # Add a prefixed <testsuites> element improve jenkins visualization
                new_xml = junitparser.JUnitXml(prefix)
                new_xml.add_testsuite(xml)
                new_xml.update_statistics()
                xml = new_xml
            else:
                for suite in xml:
                    suite.name = prefix if suite.name is None else prefix + "/" + suite.name
        # Now we can overwrite the input file
        xml.write(str(xml_file))
        boot_cheribsd.run_host_command(["grep", "<testsuite", str(xml_file)])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("db", help="The database to convert")
    parser.add_argument(
        "xml",
        nargs=argparse.OPTIONAL,
        help="The output file (or - for stdout). Defaults to the db file with suffix .xml",
    )
    parser.add_argument("--update-stats", action="store_true", help="Only update stats instead of parsing a kyua db")
    parser.add_argument("--add-prefix", help="Add a prefix to all testsuites")
    args = parser.parse_args()
    if not args.xml:
        output = Path(args.db).with_suffix(".xml")
    elif args.xml == "-":
        output = Path("/dev/stdout")
    else:
        output = Path(args.xml)
    if args.update_stats:
        fixup_kyua_generated_junit_xml(Path(args.db), args.add_prefix)
    else:
        convert_kyua_db_to_junit_xml(Path(args.db), output, args.add_prefix)
