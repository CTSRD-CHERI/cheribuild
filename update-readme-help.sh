#!/bin/sh
set -e
set -u

err() {
	echo >&2 "Error:" "$@"
	exit 1
}

if [ ! -f cheribuild.py ] || [ ! -f README.md ]; then
	err "Not being run in cheribuild's source directory?"
fi

TEMP=`mktemp`
exec 3>"$TEMP" 4<"$TEMP"
rm "$TEMP"

sed -n '
p
/<!-- BEGIN HELP OUTPUT -->/q
' README.md >&3

echo '```' >&3
PATH="$PWD:$PATH" HOME="\$HOME" XDG_CONFIG_HOME="\$HOME/.config" _GENERATING_README=1 cheribuild.py --help 2>&1 | sed "
# XXX: Ideally the default would be printed as ~/.config/cheribuild.json rather
# than the possibly-bundled file.
s!$PWD/cheribuild.json!\$HOME/.config/cheribuild.json!
" >&3
echo '```' >&3

sed -n '
/<!-- END HELP OUTPUT -->/b loop
b

: loop
p
n
b loop
' README.md >&3

cat <&4 >README.md
