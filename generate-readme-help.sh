#!/bin/sh

set -e
set -u

PATH="$PWD:$PATH" HOME="\$HOME" XDG_CONFIG_HOME="\$HOME/.config" _GENERATING_README=1 cheribuild.py --help 2>&1 | sed "
# XXX: Ideally the default would be printed as ~/.config/cheribuild.json rather
# than the possibly-bundled file.
s!$PWD/cheribuild.json!\$HOME/.config/cheribuild.json!
"
