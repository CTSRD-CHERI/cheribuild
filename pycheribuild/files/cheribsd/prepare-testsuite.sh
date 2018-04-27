#!/bin/sh -xe

# TODO: sysctl kern.corefile=/var/coredumps/%U/%N.%P.core
# TODO: sysctl kern.corefile=/rootfs/%N.%P.core

# The QEMU user DNS server appears to be broken for the version that we are using:
echo 'nameserver 8.8.8.8' > /etc/resolv.conf

# The current binary pkg depends on on older version of libarchive:
if [ ! -e /usr/lib/libarchive.so.6 ]; then
    # Without the SSL_NO_VERIFY_PEER I get the following error:
    # Certificate verification failed for /C=US/O=Let's Encrypt/CN=Let's Encrypt Authority X3
    # 1076765744:error:14090086:SSL routines:ssl3_get_server_certificate:certificate verify failed:/exports/users/alr48/sources/cheribsd/crypto/openssl/ssl/s3_clnt.c:1269:
    env SSL_NO_VERIFY_PEER=1 fetch "https://people.freebsd.org/~arichardson/cheri-files/libarchive.so.6" -o /usr/lib/libarchive.so.6
fi


# Verify that the local pkg.conf exists:
stat /etc/local-kyua-pkg/config/pkg.conf || exit 1



# pkg bootstrap doesn't parse arguments sensibly so we need to set env vars
env SSL_NO_VERIFY_PEER=1 ASSUME_ALWAYS_YES=yes PKG_BOOTSTRAP_CONFIG_FILE=/etc/local-kyua-pkg/config/pkg.conf pkg bootstrap


# only fetch from the kyua-pkg-cache repository
PKG_OPTIONS="--config /etc/local-kyua-pkg/config/pkg.conf --option ASSUME_ALWAYS_YES=yes"
env SSL_NO_VERIFY_PEER=1 pkg $PKG_OPTIONS install kyua

# Now run kyua test -k /usr/tests/cheri/lib/Kyuafile
