#!/bin/sh
# This script can be use to benchmark kernel startup performance (e.g. on QEMU)
# Example usage:
# MIPS:
# ~/cheri/output/sdk/bin/qemu-system-cheri128 -M malta -m 2048 -nographic \
#     -kernel ~/cheri/output/kernel-mips-hybrid128.CHERI_MALTA64_MFS_ROOT \
#     -nic user,id=net0,ipv6=offhostfwd=tcp::12345-:22 -append init_path=/sbin/startup-benchmark.sh
# RISC-V: TODO: the -append option is currently ignored
# ~/cheri/output/sdk/bin/qemu-system-riscv64cheri -M virt -m 2048 -nographic \
#     -kernel ~/cheri/output/kernel-riscv64-purecap.CHERI_QEMU_MFS_ROOT -device virtio-net-device,netdev=net0 \
#     -netdev user,id=net0,ipv6=off,hostfwd=tcp::12345-:22 -append init_path=/sbin/startup-benchmark.sh

# Avoid 5 second sleep before shutdown
sysctl kern.shutdown.poweroff_delay=0
# Note: `poweroff` will panic the kernel with "going nowhere without my init!"
# -n      The file system cache is not flushed.
# -p      The system will turn off the power if it can.
# -q      The system is halted or restarted quickly and ungracefully,
reboot -npq
# In case reboot failed:
exec /bin/sh
