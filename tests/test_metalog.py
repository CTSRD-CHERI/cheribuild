import io
import os
import sys
import tempfile

import pytest

try:
    import typing
except ImportError:
    typing = {}
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from pycheribuild.mtree import MtreeFile  # noqa: E402

HAVE_LCHMOD = True
if "_TEST_SKIP_METALOG" in os.environ:
    del os.environ["_TEST_SKIP_METALOG"]


def _create_file(parent: Path, name: str, mode: int) -> Path:
    p = Path(parent, name)
    # Python 3.4 compat
    with p.open("wb") as f:
        f.write(b"empty")
    p.chmod(mode)
    return p


def _create_symlink(parent: Path, name: str, target: str, mode: int) -> Path:
    p = Path(parent, name)
    p.symlink_to(target)
    try:
        p.lchmod(mode)
    except NotImplementedError:
        global HAVE_LCHMOD  # noqa: PLW0603
        HAVE_LCHMOD = False
        pass
    return p


def _create_dir(parent: Path, name: str, mode: int) -> Path:
    p = Path(parent, name)
    p.mkdir()
    p.chmod(mode)
    return p


def _get_as_str(mtree: MtreeFile) -> str:
    output = io.StringIO()
    mtree.write(output, pretend=False)
    return output.getvalue()


def test_empty():
    mtree = MtreeFile(verbose=False)
    assert _get_as_str(mtree) == "#mtree 2.0\n# END\n"


def test_add_dir():
    mtree = MtreeFile(verbose=False)
    mtree.add_dir("bin")
    expected = """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./bin type=dir uname=root gname=wheel mode=0755
# END
"""
    assert expected == _get_as_str(mtree)
    mtree = MtreeFile(verbose=False)
    # same with a trailing slash
    mtree.add_dir("bin/", mode="0755")
    assert expected == _get_as_str(mtree)


def test_add_dir_infer_mode():
    mtree = MtreeFile(verbose=False)
    with tempfile.TemporaryDirectory() as td:
        parent_dir = _create_dir(Path(td), "parent", 0o750)
        testdir = _create_dir(parent_dir, "testdir", 0o700)
        mtree.add_dir("foo/bar", reference_dir=testdir)
        expected = """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./foo type=dir uname=root gname=wheel mode=0750
./foo/bar type=dir uname=root gname=wheel mode=0700
# END
"""
        assert expected == _get_as_str(mtree)


def test_add_file_infer_mode():
    mtree = MtreeFile(verbose=False)
    with tempfile.TemporaryDirectory() as td:
        parent_dir = _create_dir(Path(td), "parent", 0o750)
        testdir = _create_dir(parent_dir, "testdir", 0o700)
        testfile = _create_file(testdir, "file", 0o666)
        testlink = _create_symlink(testdir, name="link", target="file", mode=0o444)
        symlink_perms = "0444" if HAVE_LCHMOD else "0777"
        assert oct(testfile.lstat().st_mode) == "0o100666"
        assert oct(testlink.lstat().st_mode) == "0o12" + symlink_perms
        print("testlink", oct(testlink.lstat().st_mode))
        mtree.add_file(testfile, "foo/bar/file")
        mtree.add_file(testlink, "foo/bar/link")
        expected = f"""#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./foo type=dir uname=root gname=wheel mode=0750
./foo/bar type=dir uname=root gname=wheel mode=0700
./foo/bar/file type=file uname=root gname=wheel mode=0666 contents={testfile}
./foo/bar/link type=link uname=root gname=wheel mode={symlink_perms} link=file
# END
"""
        assert expected == _get_as_str(mtree)


# Check that we override the permissions for .ssh and authorized_keys to avoid surprising ssh auth failures
def test_add_file_infer_ssh_mode():
    mtree = MtreeFile(verbose=False)
    with tempfile.TemporaryDirectory() as td:
        root_dir = _create_dir(Path(td), "root", 0o744)
        ssh_dir = _create_dir(root_dir, ".ssh", 0o777)
        auth_keys = _create_file(ssh_dir, "authorized_keys", 0o666)
        privkey = _create_file(ssh_dir, "id_foo", 0o754)
        pubkey = _create_file(ssh_dir, "id_foo.pub", 0o755)
        testlink = _create_symlink(ssh_dir, "link", target="authorized_keys", mode=0o767)
        symlink_perms = "0767" if HAVE_LCHMOD else "0777"  # However, in the mtree it is actually 0600 due to .ssh perms
        # The input files have wrong permissions but the mtree should be correct:
        assert oct(auth_keys.lstat().st_mode) == "0o100666"
        assert oct(privkey.lstat().st_mode) == "0o100754"
        assert oct(pubkey.lstat().st_mode) == "0o100755"
        assert oct(testlink.lstat().st_mode) == "0o12" + symlink_perms
        assert oct(ssh_dir.lstat().st_mode) == "0o40777"
        assert oct(root_dir.lstat().st_mode) == "0o40744"
        mtree.add_file(auth_keys, "root/.ssh/authorized_keys")
        mtree.add_file(privkey, "root/.ssh/id_foo")
        mtree.add_file(pubkey, "root/.ssh/id_foo.pub")
        mtree.add_file(testlink, "root/.ssh/link")

        expected = f"""#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./root type=dir uname=root gname=wheel mode=0755
./root/.ssh type=dir uname=root gname=wheel mode=0700
./root/.ssh/authorized_keys type=file uname=root gname=wheel mode=0600 contents={auth_keys}
./root/.ssh/id_foo type=file uname=root gname=wheel mode=0600 contents={privkey}
./root/.ssh/id_foo.pub type=file uname=root gname=wheel mode=0755 contents={pubkey}
./root/.ssh/link type=link uname=root gname=wheel mode=0600 link=authorized_keys
# END
"""
        assert expected == _get_as_str(mtree)


normalized_usr_tests_duplicate_mtree = """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./usr type=dir uname=root gname=wheel mode=0755
./usr/lib type=dir uname=root gname=wheel mode=0755
./usr/lib/debug type=dir uname=root gname=wheel mode=0755
./usr/lib/debug/usr type=dir uname=root gname=wheel mode=0755
./usr/lib/debug/usr/tests type=dir uname=root gname=wheel mode=0755
# END
"""


def test_normalize_paths():
    # The makefs for cheribsd was failing because mtree contained the following lines:
    # ./usr/lib/debug//usr/tests and then later on
    # ./usr/lib/debug/usr/tests
    # One of the two was added by cheribuild because a file with the double slash was added so
    # the mtree code assumed the file did not exist:
    mtree = MtreeFile(verbose=False)
    assert len(mtree._mtree) == 0
    mtree.add_dir("usr/lib/debug/usr/tests")
    assert len(mtree._mtree) == 6
    mtree.add_dir("usr/lib/debug//usr/tests")
    # This should not add another entry!
    assert len(mtree._mtree) == 6
    assert normalized_usr_tests_duplicate_mtree == _get_as_str(mtree)


def test_normalize_paths_loaded_from_file():
    # Same thing as above just this time loaded from a file instead of created programmatically
    file = """
#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./usr type=dir uname=root gname=wheel mode=0755
./usr/lib type=dir uname=root gname=wheel mode=0755
./usr/lib/debug type=dir uname=root gname=wheel mode=0755
./usr/lib/debug//usr/tests type=dir uname=root gname=wheel mode=0755
./usr/lib/debug/usr type=dir uname=root gname=wheel mode=0755
./usr/lib/debug/usr/tests type=dir uname=root gname=wheel mode=0755
# END
"""
    # check that we deduplicate these:
    mtree = MtreeFile(file=io.StringIO(file), verbose=False)
    print(_get_as_str(mtree), file=sys.stderr)
    assert normalized_usr_tests_duplicate_mtree == _get_as_str(mtree)
    assert len(mtree._mtree) == 6


def test_contents_root():
    # When parsing the cheribsdbox mtree we want to convert relative paths to absolute ones
    file = """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./bin type=dir uname=root gname=wheel mode=0755
./bin/cat type=file uname=root gname=wheel mode=0755 contents=./bin/cheribsdbox
./bin/cheribsdbox type=file uname=root gname=wheel mode=0755 contents=/path/to/rootfs/bin/cheribsdbox
# END
"""
    mtree = MtreeFile(file=io.StringIO(file), contents_root=Path("/path/to/rootfs"), verbose=False)
    assert _get_as_str(mtree) == """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./bin type=dir uname=root gname=wheel mode=0755
./bin/cat type=file uname=root gname=wheel mode=0755 contents=/path/to/rootfs/bin/cheribsdbox
./bin/cheribsdbox type=file uname=root gname=wheel mode=0755 contents=/path/to/rootfs/bin/cheribsdbox
# END
"""


def test_add_file():
    mtree = MtreeFile(verbose=False)
    mtree.add_file(Path("/foo/bar"), "tmp/mysh", mode=0o755)
    print(_get_as_str(mtree), file=sys.stderr)
    expected = """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./tmp type=dir uname=root gname=wheel mode=0755
./tmp/mysh type=file uname=root gname=wheel mode=0755 contents=/foo/bar
# END
"""
    assert expected == _get_as_str(mtree)


@pytest.fixture(
    params=["/usr/bin", "/this/does/not/exist", "./testfile", "testfile", "/tmp/testfile", "../this/does/not/exist"],
)
def temp_symlink():
    target = "/usr/bin"
    with tempfile.TemporaryDirectory() as td:
        link = _create_symlink(Path(td), "testlink", target, mode=0o644)
        file = _create_file(Path(td), "testfile", mode=0o700)
        yield link, file, target  # provide the fixture value


# noinspection PyShadowingNames
def test_symlink_symlink(temp_symlink):
    mtree = MtreeFile(verbose=False)
    print(temp_symlink)
    mtree.add_file(temp_symlink[0], "tmp/link", mode=0o755, parent_dir_mode=0o755)
    mtree.add_file(temp_symlink[1], "tmp/testfile", mode=0o755, parent_dir_mode=0o755)
    print(_get_as_str(mtree), file=sys.stderr)
    expected = f"""#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./tmp type=dir uname=root gname=wheel mode=0755
./tmp/link type=link uname=root gname=wheel mode=0755 link={temp_symlink[2]}
./tmp/testfile type=file uname=root gname=wheel mode=0755 contents={temp_symlink[1]!s}
# END
"""
    assert expected == _get_as_str(mtree)


# noinspection PyShadowingNames
def test_symlink_infer_mode(temp_symlink):
    mtree = MtreeFile(verbose=False)
    print(temp_symlink)
    mtree.add_file(temp_symlink[0], "tmp/link", parent_dir_mode=0o755)
    mtree.add_file(temp_symlink[1], "tmp/testfile", parent_dir_mode=0o755)
    print(_get_as_str(mtree), file=sys.stderr)
    symlink_perms = "0644" if HAVE_LCHMOD else "0777"
    expected = f"""#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./tmp type=dir uname=root gname=wheel mode=0755
./tmp/link type=link uname=root gname=wheel mode={symlink_perms} link={temp_symlink[2]}
./tmp/testfile type=file uname=root gname=wheel mode=0700 contents={temp_symlink[1]!s}
# END
"""
    assert expected == _get_as_str(mtree)
