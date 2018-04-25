#!/bin/sh -xe

# The QEMU user DNS server appears to be broken for the version that we are using:
echo 'nameserver 8.8.8.8' > /etc/resolv.conf

# The current binary pkg depends on on older version of libarchive:
if [ ! -e /usr/lib/libarchive.so.6 ]; then
    env SSL_NO_VERIFY_PEER=1 fetch "https://people.freebsd.org/~arichardson/cheri-files/libarchive.so.6" -o /usr/lib/libarchive.so.6
fi

# Without the SSL_NO_VERIFY_PEER I get the following error:
# Certificate verification failed for /C=US/O=Let's Encrypt/CN=Let's Encrypt Authority X3
# 1076765744:error:14090086:SSL routines:ssl3_get_server_certificate:certificate verify failed:/exports/users/alr48/sources/cheribsd/crypto/openssl/ssl/s3_clnt.c:1269:
env SSL_NO_VERIFY_PEER=1 ASSUME_ALWAYS_YES=yes pkg bootstrap
env SSL_NO_VERIFY_PEER=1 ASSUME_ALWAYS_YES=yes pkg install kyua

# Now run kyua test -k /usr/tests/cheri/lib/Kyuafile
