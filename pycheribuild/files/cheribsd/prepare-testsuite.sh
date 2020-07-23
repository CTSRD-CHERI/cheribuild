#!/bin/sh -xe

if command -v kyua >/dev/null; then
    echo "kyua is already installed"
    exit 0
fi
# TODO: sysctl kern.corefile=/var/coredumps/%U/%N.%P.core
# TODO: sysctl kern.corefile=/rootfs/%N.%P.core

# The QEMU user DNS server appears to be broken for the version that we are using:
echo 'nameserver 8.8.8.8' >/etc/resolv.conf
# See https://github.com/freebsd/pkg/blob/master/libpkg/pkg_config.c for options
export NAMESERVER=8.8.8.8

if [ -e /usr/lib64/libc.so.7 ]; then
    _mips_libdir=/usr/lib64
else
    _mips_libdir=/usr/lib
fi

# The current binary pkg depends on on older version of libarchive, libssl and libcrypto:
for _lib in libarchive.so.6 libssl.so.8 libcrypto.so.8; do
    if [ ! -e ${_mips_libdir}/${_lib} ]; then
        # Without the SSL_NO_VERIFY_PEER I get the following error:
        # Certificate verification failed for /C=US/O=Let's Encrypt/CN=Let's Encrypt Authority X3
        # 1076765744:error:14090086:SSL routines:ssl3_get_server_certificate:certificate verify failed:/exports/users/alr48/sources/cheribsd/crypto/openssl/ssl/s3_clnt.c:1269:
        env SSL_NO_VERIFY_PEER=1 fetch "https://people.freebsd.org/~arichardson/cheri-files/${_lib}" -o ${_mips_libdir}/${_lib}
    fi
done

# Verify that the local pkg.conf exists:
stat /etc/local-kyua-pkg/config/pkg.conf || exit 1

# "Add extra strict, pedantic warnings as an aid to package maintainers"
export DEVELOPER_MODE=yes
# Should not be needed since we are only fetching locally
# export SSL_NO_VERIFY_PEER=1
export ASSUME_ALWAYS_YES=yes

# FIXME: pkg bootstrap invokes pkg-static without a way of setting the config file
# Therefore we have to link /usr/local/etc/pkg.conf with the custom override
mkdir -p /usr/local/etc
ln -sf /etc/local-kyua-pkg/config/pkg.conf /usr/local/etc/pkg.conf
# Check that we actually linked the right file:
grep ABI /usr/local/etc/pkg.conf
# pkg bootstrap doesn't parse arguments sensibly so we need to set env vars
# FIXME: for bootstrap I seem to have to set ABI=FreeBSD:12:mips64 but the packages expect ABI=freebsd:12:mips:64:eb:n64
env ABI=FreeBSD:12:mips64 PKG_BOOTSTRAP_CONFIG_FILE=/etc/local-kyua-pkg/config/pkg.conf pkg bootstrap
# only fetch from the kyua-pkg-cache repository
env SSL_NO_VERIFY_PEER=1 pkg --config /etc/local-kyua-pkg/config/pkg.conf --option ASSUME_ALWAYS_YES=yes install kyua

# Now run kyua test -k /usr/tests/cheri/lib/Kyuafile

echo 'Sucessfully installed kyua.'
echo ''
# shellcheck disable=SC2016
echo 'To run tests execute `kyua test -k /path/to/Kyuafile` (e.g. /usr/tests/cheri/lib/Kyuafile)'
echo ''
# shellcheck disable=SC2016
echo 'To debug a test failure run `kyua debug -k /path/to/Kyuafile TEST_NAME:TEST_FUNCTION`'
# shellcheck disable=SC2016
echo 'Example: `kyua debug -k /usr/tests/cheri/lib/libc/locale/Kyuafile mbtowc_test:mbtowc`'
echo 'If GDB is installed as /usr/bin/gdb this will also give a backtrace on crash'
