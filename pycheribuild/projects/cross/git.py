#
# Copyright (c) 2021 Jessica Clarke
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

from .crosscompileproject import CrossCompileAutotoolsProject, GitRepository
from .curl import BuildCurl
from .expat import BuildExpat
from ...config.compilation_targets import FreeBSDTargetInfo


# CMake build system in contrib/buildsystems sadly doesn't have all the
# configure checks needed for non-Windows, and is itself broken on Windows
# (though that breakage is trivially fixable). Have to use the sort-of
# autotools build system that doesn't support out-of-tree builds.
class BuildGit(CrossCompileAutotoolsProject):
    repository = GitRepository("https://github.com/git/git")
    build_via_symlink_farm = True
    builds_docbook_xml = True
    dependencies = ["curl", "libexpat"]

    def check_system_dependencies(self) -> None:
        super().check_system_dependencies()
        self.check_required_system_tool("asciidoc")

    def setup(self):
        super().setup()
        if self.compiling_for_cheri():
            # Ancient obstack code is actually ok; the places where these
            # warnings fire are all dead due to using an AS/400 code path,
            # which is chosen based on a constant run-time expression and so
            # still diagnosed.
            self.cross_warning_flags.append("-Wno-error=cheri-capability-misuse")
        if not self.compiling_for_host():
            assert isinstance(self.target_info, FreeBSDTargetInfo)
            # Various configure checks that require execution
            self.configure_environment.update(
                ac_cv_iconv_omits_bom="no",
                ac_cv_fread_reads_directories="yes",
                ac_cv_snprintf_returns_bogus="no",
            )
            # Doesn't use pkg-config
            self.configure_args.extend([
                "--with-curl=" + str(BuildCurl.get_install_dir(self)),
                "--with-expat=" + str(BuildExpat.get_install_dir(self)),
                ])
            # Build-time detection of uname to determine more properties
            # Only S and R seem to be used currently, but provide sensible
            # values or, for V, a dummy kernconf (and format it like a release
            # kernel rather than providing a dummy date/time and build path).
            self.make_args.set(
                uname_S="FreeBSD",
                uname_M=self.target_info.freebsd_target,
                uname_O="FreeBSD",
                uname_R="14.0-CURRENT",
                uname_P=self.target_info.freebsd_target_arch,
                uname_V="FreeBSD 14.0-CURRENT GENERIC ",
            )

    def configure(self):
        self.run_make("configure", cwd=self.source_dir)
        super().configure()

    def compile(self, **kwargs):
        super().compile(**kwargs)
        self.run_make("man", cwd=self.build_dir / "Documentation")

    def install(self, **kwargs):
        super().install(**kwargs)
        self.run_make_install(target="install-man", cwd=self.build_dir / "Documentation")
