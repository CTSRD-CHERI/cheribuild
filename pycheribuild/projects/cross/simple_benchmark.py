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

import tempfile
from pathlib import Path

from .crosscompileproject import CrossCompileCMakeProject, DefaultInstallDir, GitRepository
from .benchmark_mixin import BenchmarkMixin


class BuildSimpleCheriBenchmarks(BenchmarkMixin, CrossCompileCMakeProject):
    repository = GitRepository("https://github.com/arichardson/simple-cheri-benchmarks.git")
    cross_install_dir = DefaultInstallDir.ROOTFS_OPTBASE
    target = "simple-cheri-benchmarks"

    def create_test_dir(self, outdir: Path):
        self.clean_directory(outdir)
        for f in ("run_jenkins-bluehive.sh", "libqsort_default.so", "test_qsort_default", "test_qsort_static",
                  "benchmark_qsort", "malloc_bench_shared", "malloc_bench_static", "malloc_benchmark.sh",
                  "run_cheribsd.sh"):
            self.install_file(self.build_dir / f, outdir / f, force=True, print_verbose_only=False)
        return outdir

    @property
    def archname_column(self):
        return self.crosscompile_target.generic_arch_suffix + self.build_configuration_suffix()

    def run_tests(self):
        if self.compiling_for_host():
            self.fatal("running x86 tests is not implemented yet")
            return
        self.create_test_dir(self.build_dir / "test-dir")
        # testing, not benchmarking -> run only once: (-s small / -s large?)
        test_command = "cd /build/test-dir && ./run_jenkins-bluehive.sh -d0 -r1 -o {output} -a {tgt}".format(
            tgt=self.archname_column, output=self.default_statcounters_csv_name)
        self.target_info.run_cheribsd_test_script("run_simple_tests.py", "--test-command", test_command,
                                                  "--test-timeout", str(120 * 60), mount_builddir=True)

    def run_benchmarks(self):
        if not self.compiling_for_mips(include_purecap=True):
            self.warning("Cannot run these benchmarks for non-MIPS yet")
            return
        with tempfile.TemporaryDirectory() as td:
            benchmarks_dir = self.create_test_dir(Path(td))
            self.run_fpga_benchmark(benchmarks_dir, output_file=self.default_statcounters_csv_name,
                                    benchmark_script_args=["-d1", "-r10", "-o", self.default_statcounters_csv_name,
                                                           "-a", self.archname_column])
