#
# Copyright (c) 2019 Alex Richardson
#
# This software was developed by SRI International and the University of
# Cambridge Computer Laboratory (Department of Computer Science and
# Technology) under DARPA contract HR0011-18-C-0016 ("ECATS"), as part of the
# DARPA SSITH research programme.
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

from .crosscompileproject import *
from ..project import ReuseOtherProjectRepository
from ...config.loader import ConfigOptionBase
from ...utils import setEnv, IS_FREEBSD, commandline_to_str, is_jenkins_build
from pathlib import Path
import inspect
import tempfile


class BuildSimpleCheriBenchmarks(CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/arichardson/simple-cheri-benchmarks.git")
    crossInstallDir = CrossInstallDir.CHERIBSD_ROOTFS
    projectName = "simple-cheri-benchmarks"

    def create_test_dir(self, outdir: Path):
        self.cleanDirectory(outdir)
        for f in ("run_jenkins-bluehive.sh", "libqsort_default.so", "test_qsort_default", "test_qsort_static",
                  "malloc_bench_shared", "malloc_bench_static", "malloc_benchmark.sh", "run_cheribsd.sh"):
            self.installFile(self.buildDir / f, outdir / f, force=True, printVerboseOnly=False)
        return outdir


    def run_tests(self):
        self.create_test_dir(self.buildDir / "test-dir")
        self.run_cheribsd_test_script("run_simple_benchmarks.py", use_benchmark_kernel_by_default=True)

    def run_benchmarks(self):
        with tempfile.TemporaryDirectory() as td:
            benchmarks_dir = self.create_test_dir(Path(td))
            self.run_fpga_benchmark(benchmarks_dir, output_file=self.default_statcounters_csv_name,
                                    benchmark_script_args=["-d1", "-r10", "-o", self.default_statcounters_csv_name])
