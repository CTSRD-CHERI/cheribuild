#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK

# https://stackoverflow.com/questions/1112618/import-python-package-from-local-directory-into-interpreter
# https://stackoverflow.com/questions/14500183/in-python-can-i-call-the-main-of-an-imported-module
try:
    from .pycheribuild import __main__   # "myapp" case
except SystemError:
    import pycheribuild.__main__  # "__main__" case

pycheribuild.__main__.main()
