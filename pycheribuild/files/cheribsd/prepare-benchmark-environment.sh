#!/bin/sh

set -x
# disable network (atse0 on the FPGA, le0 in QEMU):

ifconfig atse0 down
ifconfig le0 down
# purecap devctl doesn't work yet:
cheribsdbox devctl disable atse0
# FIXME: if we disable le0, we get an error when trying to enable it again
echo "Skipping for now:" "cheribsdbox devctl disable le0"

# stop services that might run:
service devd stop
service syslogd stop

set +x
echo ''
echo ''
echo '============================================================================'
echo 'PLEASE CHECK THERE ARE NO PROCESSES RUNNING THAT MIGHT IMPACT THE BENCHMARK:'
echo '============================================================================'
echo ''
echo ''
set -x

ps aux
