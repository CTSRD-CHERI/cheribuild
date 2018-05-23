import sys
import io
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
