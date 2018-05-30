import pytest
import sys
import io
import os
import tempfile
try:
    import typing
except ImportError:
    typing = {}
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from pycheribuild.mtree import MtreeFile


def _get_as_str(mtree: MtreeFile) -> str:
    output = io.StringIO()
    mtree.write(output)
    return output.getvalue()


def test_empty():
    mtree = MtreeFile()
    assert "#mtree 2.0\n# END\n" == _get_as_str(mtree)


def test_add_dir():
    mtree = MtreeFile()
    mtree.add_dir("bin")
    print(_get_as_str(mtree), file=sys.stderr)
    expected = """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./bin type=dir uname=root gname=wheel mode=0755
# END
"""
    assert expected == _get_as_str(mtree)
    mtree = MtreeFile()
    # same with a trailing slash
    mtree.add_dir("bin/")
    print(_get_as_str(mtree), file=sys.stderr)
    assert expected == _get_as_str(mtree)


def test_add_file():
    mtree = MtreeFile()
    mtree.add_file(Path("/foo/bar"), "tmp/mysh", mode=0o755)
    print(_get_as_str(mtree), file=sys.stderr)
    expected = """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./tmp type=dir uname=root gname=wheel mode=0755
./tmp/mysh type=file uname=root gname=wheel mode=0755 contents=/foo/bar
# END
"""
    assert expected == _get_as_str(mtree)


@pytest.fixture(params=["/usr/bin", "/this/does/not/exist", "./testfile", "testfile", "/tmp/testfile",
                        "../this/does/not/exist"])
def temp_symlink():
    target = "/usr/bin"
    with tempfile.TemporaryDirectory() as td:
        link = Path(td, "testlink")
        link.symlink_to(target)
        file = link.with_name("testfile")
        file.touch(mode=0o700)
        yield link, file, target  # provide the fixture value


def test_symlink_symlink(temp_symlink):
    mtree = MtreeFile()
    print(temp_symlink)
    mtree.add_file(temp_symlink[0], "tmp/link", mode=0o755)
    mtree.add_file(temp_symlink[1], "tmp/testfile", mode=0o755)
    print(_get_as_str(mtree), file=sys.stderr)
    expected = """#mtree 2.0
. type=dir uname=root gname=wheel mode=0755
./tmp type=dir uname=root gname=wheel mode=0755
./tmp/link type=link uname=root gname=wheel mode=0755 link={target}
./tmp/testfile type=file uname=root gname=wheel mode=0755 contents={testfile}
# END
""".format(target=temp_symlink[2], testfile=str(temp_symlink[1]))
    assert expected == _get_as_str(mtree)
