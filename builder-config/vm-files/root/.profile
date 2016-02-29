# $FreeBSD: releng/10.2/etc/root/dot.profile 199243 2009-11-13 05:54:55Z ed $
#
PATH=/sbin:/bin:/usr/sbin:/usr/bin:/usr/games:/usr/local/sbin:/usr/local/bin:~/bin
export PATH
HOME=/root
export HOME
TERM=${TERM:-xterm}
export TERM
# The default here is more, but less is better
PAGER=less
export PAGER
# add colour to the console
CLICOLOR=1
export CLICOLOR
