import os
from .configloader import ConfigLoader
from pathlib import Path


def defaultNumberOfMakeJobs():
    makeJobs = os.cpu_count()
    if makeJobs > 24:
        # don't use up all the resources on shared build systems
        # (you can still override this with the -j command line option)
        makeJobs = 16
    return makeJobs


def defaultSshForwardingPort():
    # chose a different port for each user (hopefully it isn't in use yet)
    return 9999 + ((os.getuid() - 1000) % 10000)


def defaultDiskImagePath(conf: "CheriConfig"):
    if conf.cheriBits == 128:
        return conf.outputRoot / "cheri128-disk.qcow2"
    return conf.outputRoot / "cheri256-disk.qcow2"


class CheriConfig(object):
    # boolean flags
    pretend = ConfigLoader.addBoolOption("pretend", "p", help="Only print the commands instead of running them")
    quiet = ConfigLoader.addBoolOption("quiet", "q", help="Don't show stdout of the commands that are executed")
    verbose = ConfigLoader.addBoolOption("verbose", "v", help="Print all commmands that are executed")
    clean = ConfigLoader.addBoolOption("clean", "c", help="Remove the build directory before build")
    force = ConfigLoader.addBoolOption("force", "f", help="Don't prompt for user input but use the default action")
    skipUpdate = ConfigLoader.addBoolOption("skip-update", help="Skip the git pull step")
    skipConfigure = ConfigLoader.addBoolOption("skip-configure", help="Skip the configure step")
    skipBuildworld = ConfigLoader.addBoolOption("skip-buildworld", help="Skip the FreeBSD buildworld step -> only build"
                                                " and install the kernel")
    listTargets = ConfigLoader.addBoolOption("list-targets", help="List all available targets and exit")
    dumpConfig = ConfigLoader.addBoolOption("dump-configuration", help="Print the current configuration as JSON."
                                            " This can be saved to ~/.config/cheribuild.json to make it persistent")
    skipDependencies = ConfigLoader.addBoolOption("skip-dependencies", "t",
                                                  help="Only build the targets that were explicitly passed on the "
                                                       "command line")
    noLogfile = ConfigLoader.addBoolOption("no-logfile", help="Don't write a logfile for the build steps")

    _buildCheri128 = ConfigLoader.addBoolOption("cheri-128", "-128", group=ConfigLoader.cheriBitsGroup,
                                                help="Shortcut for --cheri-bits=128")
    _buildCheri256 = ConfigLoader.addBoolOption("cheri-256", "-256", group=ConfigLoader.cheriBitsGroup,
                                                help="Shortcut for --cheri-bits=256")
    _cheriBits = ConfigLoader.addOption("cheri-bits", type=int, group=ConfigLoader.cheriBitsGroup, choices=["128", "256"],
                                        default=256, help="Whether to build the whole software stack for 128 or 256 bit"
                                        " CHERI. The output directories will be suffixed with the number of bits to"
                                        " make sure the right binaries are being used."
                                        " WARNING: 128-bit CHERI is still very unstable.")

    # configurable paths
    sourceRoot = ConfigLoader.addPathOption("source-root", default=Path(os.path.expanduser("~/cheri")),
                                            help="The directory to store all sources")
    outputRoot = ConfigLoader.addPathOption("output-root", default=lambda p: (p.sourceRoot / "output"),
                                            help="The directory to store all output (default: '<SOURCE_ROOT>/output')")
    extraFiles = ConfigLoader.addPathOption("extra-files", default=lambda p: (p.sourceRoot / "extra-files"),
                                            help="A directory with additional files that will be added to the image "
                                                 "(default: '<OUTPUT_ROOT>/extra-files')")
    # TODO: only create a qcow2 image?
    diskImage = ConfigLoader.addPathOption("disk-image-path", default=defaultDiskImagePath, help="The output path for"
                                           " the QEMU disk image (default: '<OUTPUT_ROOT>/cheri256-disk.qcow2')")

    # other options
    makeJobs = ConfigLoader.addOption("make-jobs", "j", type=int, default=defaultNumberOfMakeJobs(),
                                      help="Number of jobs to use for compiling")  # type: int
    sshForwardingPort = ConfigLoader.addOption("ssh-forwarding-port", "s", type=int, default=defaultSshForwardingPort(),
                                               help="The port to use on localhost to forward the QEMU ssh port. "
                                                    "You can then use `ssh root@localhost -p $PORT` connect to the VM",
                                               metavar="PORT")  # type: int
    extraMakeOptions = " ".join([
        "-DWITHOUT_HTML",  # should not be needed
        "-DWITHOUT_SENDMAIL", "-DWITHOUT_MAIL",  # no need for sendmail
        "-DWITHOUT_GAMES",  # not needed
        "-DWITHOUT_MAN",  # seems to be a majority of the install time
        "-DWITH_FAST_DEPEND",  # no separate make depend step, do it while compiling
        # "-DWITH_INSTALL_AS_USER", should be enforced by -DNO_ROOT
        "-DWITH_DIRDEPS_BUILD", "-DWITH_DIRDEPS_CACHE",  # experimental fast build options
        # "-DWITH_LIBCHERI_JEMALLOC"  # use jemalloc instead of -lmalloc_simple
    ])

    cheribsdExtraMakeOptions = ConfigLoader.addOption("cheribsd-make-options", type=str, default=extraMakeOptions,
                                                      help="Additional options to be passed to make when "
                                                      "building CHERIBSD. See man src.conf for more info")
    # allow overriding the git revisions in case there is a regression
    cheriBsdRevision = ConfigLoader.addOption("cheribsd-revision", type=str, metavar="GIT_COMMIT_ID",
                                              help="The git revision or branch of CHERIBSD to check out",
                                              group=ConfigLoader.revisionGroup)  # type: str
    llvmRevision = ConfigLoader.addOption("llvm-revision", type=str, metavar="GIT_COMMIT_ID",
                                          help="The git revision or branch of LLVM to check out",
                                          group=ConfigLoader.revisionGroup)  # type: str
    clangRevision = ConfigLoader.addOption("clang-revision", type=str, metavar="GIT_COMMIT_ID",
                                           help="The git revision or branch of clang to check out",
                                           group=ConfigLoader.revisionGroup)  # type: str
    lldbRevision = ConfigLoader.addOption("lldb-revision", type=str, metavar="GIT_COMMIT_ID",
                                          help="The git revision or branch of clang to check out",
                                          group=ConfigLoader.revisionGroup)  # type: str
    qemuRevision = ConfigLoader.addOption("qemu-revision", type=str, metavar="GIT_COMMIT_ID",
                                          help="The git revision or branch of QEMU to check out",
                                          group=ConfigLoader.revisionGroup)  # type: str

    # To allow building CHERI software on non-FreeBSD systems
    freeBsdBuildMachine = ConfigLoader.addOption("freebsd-builder-hostname", type=str, metavar="SSH_HOSTNAME",
                                                 help="This string will be passed to ssh and be something like "
                                                      "user@hostname of a FreeBSD system that can be used to build "
                                                      "CHERIBSD. Can also be the name of a host in  ~/.ssh/config.",
                                                 group=ConfigLoader.remoteBuilderGroup)  # type: str
    # TODO: query this from the remote machine instead of needed an options
    freeBsdBuilderOutputPath = ConfigLoader.addOption("freebsd-builder-output-path", type=str, metavar="PATH",
                                                      help="The path where the cheribuild output is stored on the"
                                                           " FreeBSD build server.",
                                                      group=ConfigLoader.remoteBuilderGroup)  # type: str
    freeBsdBuilderCopyOnly = ConfigLoader.addBoolOption("freebsd-builder-copy-only", help="Only scp the SDK from the"
                                                        "FreeBSD build server and don't build the SDK first.",
                                                        group=ConfigLoader.remoteBuilderGroup)

    def __init__(self):
        self.targets = ConfigLoader.loadTargets()
        self.makeJFlag = "-j" + str(self.makeJobs)

        if self._buildCheri128:
            self.cheriBits = 128
        elif self._buildCheri256:
            self.cheriBits = 256
        else:
            self.cheriBits = self._cheriBits
        self.cheriBitsStr = str(self.cheriBits)

        if not self.quiet:
            print("Sources will be stored in", self.sourceRoot)
            print("Build artifacts will be stored in", self.outputRoot)
            print("Extra files for disk image will be searched for in", self.extraFiles)
            print("Disk image will saved to", self.diskImage)

        # now the derived config options
        self.cheribsdRootfs = self.outputRoot / ("rootfs" + self.cheriBitsStr)
        self.cheribsdSources = self.sourceRoot / "cheribsd"
        self.cheribsdObj = self.outputRoot / ("cheribsd-obj-" + self.cheriBitsStr)
        self.sdkDirectoryName = "sdk" + self.cheriBitsStr
        self.sdkDir = self.outputRoot / self.sdkDirectoryName  # qemu and binutils (and llvm/clang)
        self.sdkSysrootDir = self.sdkDir / "sysroot"
        self.sysrootArchiveName = "cheri-sysroot.tar.gz"

        # for debugging purposes print all the options
        for i in ConfigLoader.options:
            i.__get__(self, CheriConfig)  # for loading of lazy value
        if not self.quiet:
            print("cheribuild.py configuration:", dict(ConfigLoader.values))
