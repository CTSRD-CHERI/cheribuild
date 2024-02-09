from .cheribsd import BuildFreeBSD
from .crosscompileproject import CrossCompileProject
from ..project import (
    GitRepository,
    MakeCommandKind,
    MakeOptions,
)
from ...config.compilation_targets import CompilationTargets


class BuildDrmKMod(CrossCompileProject):
    target: str = "drm-kmod"
    repository = GitRepository("https://github.com/freebsd/drm-kmod", default_branch="master", force_branch=True)
    supported_architectures = CompilationTargets.ALL_FREEBSD_AND_CHERIBSD_TARGETS
    build_in_source_dir: bool = False
    use_buildenv: bool = False  # doesn't quite work yet (MAKEOBJDIRPREFIX isn't set)
    freebsd_project: BuildFreeBSD
    kernel_make_args: MakeOptions

    def setup(self) -> None:
        super().setup()
        self.freebsd_project = self.target_info.get_rootfs_project(t=BuildFreeBSD, caller=self)
        if self.use_buildenv:
            extra_make_args = dict(SYSDIR=self.freebsd_project.source_dir / "sys")
        else:
            extra_make_args = dict(
                LOCAL_MODULES=self.source_dir.name,
                LOCAL_MODULES_DIR=self.source_dir.parent,
                MODULES_OVERRIDE="linuxkpi",
            )
        self.kernel_make_args = self.freebsd_project.kernel_make_args_for_config(
            self.freebsd_project.kernel_config,
            extra_make_args,
        )
        assert self.kernel_make_args.kind == MakeCommandKind.BsdMake

    def clean(self, **kwargs) -> None:
        # TODO: use buildenv and only build the kernel modules...
        if self.use_buildenv:
            self.info("Cleaning drm-kmod modules for configs:", self.freebsd_project.kernel_config)
            self.freebsd_project.build_and_install_subdir(
                self.kernel_make_args,
                str(self.source_dir),
                skip_build=True,
                skip_clean=False,
                skip_install=True,
            )
        else:
            self.info("Clean not supported yet")

    def compile(self, **kwargs) -> None:
        # TODO: use buildenv and only build the kernel modules...
        self.info("Building drm-kmod modules for configs:", self.freebsd_project.kernel_config)
        if self.use_buildenv:
            self.freebsd_project.build_and_install_subdir(
                self.kernel_make_args,
                str(self.source_dir),
                skip_build=False,
                skip_clean=True,
                skip_install=True,
            )
        else:
            self.run_make(
                "buildkernel",
                options=self.kernel_make_args,
                cwd=self.freebsd_project.source_dir,
                parallel=True,
            )

    def install(self, **kwargs) -> None:
        # TODO: use buildenv and only install the kernel modules...
        self.info("Installing drm-kmod modules for configs:", self.freebsd_project.kernel_config)
        make_args = self.kernel_make_args.copy()
        # FIXME: it appears that installkernel removes all .ko files, so we can no longer create a disk image
        # if we install with MODULES_OVERRIDE.
        make_args.remove_var("MODULES_OVERRIDE")
        make_args.set_env(METALOG=self.real_install_root_dir / "METALOG.drm-kmod")
        if self.use_buildenv:
            self.freebsd_project.build_and_install_subdir(
                make_args,
                str(self.source_dir),
                skip_build=True,
                skip_clean=True,
                skip_install=False,
            )
        else:
            self.run_make_install(
                target="installkernel",
                options=make_args,
                cwd=self.freebsd_project.source_dir,
                parallel=False,
            )
