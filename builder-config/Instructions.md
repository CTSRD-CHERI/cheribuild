# Using virt-manager

http://wiki.libvirt.org/page/Networking#NAT_forwarding_.28aka_.22virtual_networks.22.29

http://www.area536.com/projects/freebsd-as-a-kvm-guest-using-virtio/ but no need to edit /etc/fstab as it uses /dev/gpt

Sample XML (not sure which of this is needed):

```xml
<domain type='kvm' id='2'>
  <name>freebsd-builder</name>
  <uuid>25fa23cf-e2ef-4405-92e4-715036d5f025</uuid>
  <title>VM to build CHERIBSD</title>
  <memory unit='KiB'>4194304</memory>
  <currentMemory unit='KiB'>4194304</currentMemory>
  <vcpu placement='static' current='1'>4</vcpu>
  <resource>
    <partition>/machine</partition>
  </resource>
  <os>
    <type arch='x86_64' machine='pc-i440fx-2.4'>hvm</type>
  </os>
  <features>
    <acpi/>
    <apic/>
    <vmport state='off'/>
  </features>
  <cpu mode='custom' match='exact'>
    <model fallback='allow'>Westmere</model>
    <topology sockets='1' cores='4' threads='1'/>
  </cpu>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <pm>
    <suspend-to-mem enabled='no'/>
    <suspend-to-disk enabled='no'/>
  </pm>
  <devices>
    <emulator>/usr/bin/qemu-kvm</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='/home/alex/VirtualBox VMs/CHERIBSD-BUILDER/FreeBSD-10.2-RELEASE-amd64.qcow2'/>
      <backingStore/>
      <target dev='vda' bus='virtio'/>
      <boot order='1'/>
      <alias name='virtio-disk0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x09' function='0x0'/>
    </disk>
    <controller type='usb' index='0'>
      <alias name='usb'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x2'/>
    </controller>
    <controller type='pci' index='0' model='pci-root'>
      <alias name='pci.0'/>
    </controller>
    <controller type='virtio-serial' index='0'>
      <alias name='virtio-serial0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x05' function='0x0'/>
    </controller>
    <controller type='ide' index='0'>
      <alias name='ide'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x01' function='0x1'/>
    </controller>
    <filesystem type='mount' accessmode='mapped'>
      <source dir='/sources/ctsrd'/>
      <target dir='ctsrd_sources'/>
      <alias name='fs0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x03' function='0x0'/>
    </filesystem>
    <filesystem type='mount' accessmode='mapped'>
      <source dir='/build/cheri'/>
      <target dir='build_output'/>
      <alias name='fs1'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x06' function='0x0'/>
    </filesystem>
    <interface type='network'>
      <mac address='52:54:00:92:7c:7b'/>
      <source network='default' bridge='virbr0'/>
      <target dev='vnet0'/>
      <model type='virtio'/>
      <alias name='net0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x04' function='0x0'/>
    </interface>
    <serial type='pty'>
      <source path='/dev/pts/2'/>
      <target port='0'/>
      <alias name='serial0'/>
    </serial>
    <console type='pty' tty='/dev/pts/2'>
      <source path='/dev/pts/2'/>
      <target type='serial' port='0'/>
      <alias name='serial0'/>
    </console>
    <channel type='spicevmc'>
      <target type='virtio' name='com.redhat.spice.0' state='disconnected'/>
      <alias name='channel0'/>
      <address type='virtio-serial' controller='0' bus='0' port='1'/>
    </channel>
    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    <graphics type='spice' port='5900' autoport='yes' listen='127.0.0.1'>
      <listen type='address' address='127.0.0.1'/>
      <image compression='off'/>
    </graphics>
    <video>
      <model type='qxl' ram='65536' vram='65536' vgamem='16384' heads='1'/>
      <alias name='video0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x0'/>
    </video>
    <redirdev bus='usb' type='spicevmc'>
      <alias name='redir0'/>
    </redirdev>
    <redirdev bus='usb' type='spicevmc'>
      <alias name='redir1'/>
    </redirdev>
    <memballoon model='virtio'>
      <alias name='balloon0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x07' function='0x0'/>
    </memballoon>
    <rng model='virtio'>
      <backend model='random'>/dev/random</backend>
      <alias name='rng0'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x08' function='0x0'/>
    </rng>
  </devices>
</domain>
```


## Using a serial console with a VM managed by virt-manager

In the VM execute `echo 'console="comconsole"' >> /boot/loader.conf` to use serial console

after that you can access the console using a console with GNU screen.
`virsh dumpxml cheribsd-builder | grep pty` will output the right pty to use:
e.g. `<console type='pty' tty='/dev/pts/2'>`. **NOTE: The pty number will be
different on every new VM launch!**

A better way of getting the console is `virsh ttyconsole cheribsd-builder`

Then you can connect to the VM using `screen /dev/pts/2 19200` and interact
with it over a normal console instead of the virt-manager GUI console that
doesn't allow copy paste, etc.
The 19200 is the baud rate for the serial and it seems to me it can be omitted
with recent versios of screen

You can also use `virsh console cheribsd-builder` but that console is not as good
as it seems to be limited to 80x24 and handles input weirdly

TL;DR: Run `screen $(virsh ttyconsole cheribsd-builder)` as root and use `Ctrl+a, d` to disconnect

**TODO: should probably use SSH**

# mounting the disk image as a local filesystem:
http://www.rushiagr.com/blog/2014/08/02/qcow2-mount/


# enabling networking

See [libvirt wiki](http://wiki.libvirt.org/page/VirtualNetworking)

Set virtmanager to use virtio for disks and network. Use the default network bridge
so that

In the VM `/etc/rc.conf` have a least the following lines:

```bash
# use cheribsd-builder as the hostname
hostname="cheribsd-builder.local.vm"
# tell FreeBSD to use em0 as the name for the first virtio network
# which is the one bridged to the host adapter
# this should allow outgoing traffic, but connections to the VM only from the host!!
ifconfig_vtnet0_name="em0"
# Use DHCP setup for the virtio network
ifconfig_em0="DHCP"
# Use a UK keyboard layout because I have a UK keyboard
keymap="uk.iso"
# enable SSH (TODO: set up another user to allow connections from the host)
sshd_enable="YES"
# Not sure if this is required but I have /tmp as a tmpfs in my /etc/fstab
tmpfs="YES"
```

# disabling sendmail listening on external interfaces:

http://lifeisabug.com/configure-sendmail-on-freebsd-to-only-accept-local-mail-for-hostnames-in-etc-mail-local-host-names/

Add `sendmail_enable="NO"` to `/etc/rc.conf`
Also make sure that you have hostname set to e.g. `hostname="cheribsd-builder.local.vm"` as sendmail will wait
for ages during boot if there is no FQDN, so just use .local as the domain name.



# Installing stuff:


```bash
pkg install git
# setup TMPFS
mkdir -p /tmp
mount -t tmpfs fdesc /dev/fd
echo "tmpfs   /tmp            tmpfs           rw      0       0" >> /etc/fstab

pkg install vim-lite   # because vi is much harder to use than vim
# echo "set number" >> ~/.vimrc
touch ~/.vimrc
pkg install python3    # to run the build scripts
# pkg install plan9port  # for QEMU file shares (doesn't seem to work, I'll use nfs instead)
```

## optional stuff:

```bash
pkg install bash      # not really necessary but I'm used to it
mount -t fdescfs fdesc /dev/fd
# permanently add fsdescfs to fstab (required by bash):
echo "fdesc   /dev/fd         fdescfs         rw      0       0" >> /etc/fstab
```


# enabling NFS shares:

E.g. on [openSuSE](http://www.unixmen.com/setup-nfs-server-on-opensuse-42-1/)

Create `/etc/exports` with content like this:
```
# map all NFS requests to uid=alex,gid=users
/build/cheri    192.168.122.0/24(rw,async,no_subtree_check,all_squash,anonuid=1000,anongid=100)
# make sure this one is sync so that we don't get corruption (with the build dir it doesn't matter)
/sources/ctsrd  192.168.122.0/24(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=100)
```

Make sure that the virtual bridge can access the host computer

On openSuSE this requires adding `virbr0` to the SuSEfirewall "Internal Zone"

This can be done by setting `FW_DEV_INT="virbr0"` in `/etc/sysconfig/SuSEfirewall2`
this seems to have broken the VM DHCP ....

# Setting up NFS shares to mount CHERIBSD VM

https://www.freebsd.org/doc/handbook/network-nfs.html

```bash
echo 'nfs_client_enable="YES"' >> /etc/rc.conf
service nfsclient start
mkdir -p /mnt/build_output
mount 192.168.122.1:/build/cheri /mnt/build_output
mkdir -p /mnt/sources
mount 192.168.122.1:/sources/ctsrd /mnt/sources
# check if write access works
echo test >  /mnt/sources/test
echo test >  /mnt/build_output/test
# add it to fstab
echo "192.168.122.1:/build/cheri    /mnt/build_output  nfs   rw  0  0" >> /etc/fstab
echo "192.168.122.1:/sources/ctsrd  /mnt/sources       nfs   rw  0  0" >> /etc/fstab
```


# FSTAB:


```
# Custom /etc/fstab for FreeBSD VM images
/dev/gpt/rootfs              /                 ufs      rw  1  1
/dev/gpt/swapfs              none              swap     sw  0  0
fdesc                        /dev/fd           fdescfs  rw  0  0
tmpfs                        /tmp              tmpfs    rw  0  0
192.168.122.1:/build/cheri   /mnt/build_output nfs      rw  0  0
192.168.122.1:/sources/ctsrd /mnt/sources      nfs      rw  0  0
```
