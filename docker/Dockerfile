FROM ubuntu:20.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
  make ninja-build \
  gcc g++ \
  git \
  python3-minimal \
  lsb-release \
  wget \
  samba \
  telnet \
  texlive-base \
  texinfo

# RUN git config --global http.sslVerify false
# RUN cd /tmp && git clone https://github.com/arichardson/bmake && cd bmake \
#  && ./configure --with-default-sys-path=/usr/local/share/mk --with-machine=amd64 --without-meta --without-filemon --prefix=/usr/local \
#  && sh ./make-bootstrap.sh && make install && rm -rf /tmp/bmake

COPY cheribuild.json /root/.config/cheribuild.json

# deps to build QEMU+elftoolchain:
RUN apt-get update && apt-get install -y \
  libtool pkg-config autotools-dev automake autoconf libglib2.0-dev libpixman-1-dev \
  bison groff-base libarchive-dev flex

RUN apt-get update && apt-get install -y cmake

RUN apt-get install -y clang-12 lld-12

VOLUME ["/cheribuild", "/source", "/build", "/output"]
ENV PATH /cheribuild:$PATH
CMD bash
