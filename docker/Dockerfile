FROM ubuntu:20.04

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
  make ninja-build \
  gcc g++ \
  git \
  python3-minimal \
  lsb-release \
  wget \
  samba \
  texlive-base \
  texinfo

# RUN git config --global http.sslVerify false
# RUN cd /tmp && git clone https://github.com/arichardson/bmake && cd bmake \
#  && ./configure --with-default-sys-path=/usr/local/share/mk --with-machine=amd64 --without-meta --without-filemon --prefix=/usr/local \
#  && sh ./make-bootstrap.sh && make install && rm -rf /tmp/bmake

COPY cheribuild.json /root/.config/cheribuild.json

# deps to build QEMU+elftoolchain:
RUN apt-get update && apt-get install -y \
  libtool pkg-config python2-minimal autotools-dev automake autoconf libglib2.0-dev libpixman-1-dev \
  bison groff-base libarchive-dev flex

# INSTALL clang 10.0
RUN wget -O - https://apt.llvm.org/llvm-snapshot.gpg.key | apt-key add -
RUN echo "deb http://apt.llvm.org/bionic/ llvm-toolchain-bionic-8 main" > /etc/apt/sources.list.d/llvm.list
# RUN echo "deb http://ppa.launchpad.net/ubuntu-toolchain-r/test/ubuntu trusty main" > /etc/apt/sources.list.d/r-toolchain.list
# RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 1E9377A2BA9EF27F
RUN apt-get update && apt-get install -y clang-10 lld-10

RUN apt-get update && apt-get install -y cmake

RUN groupadd -g 12345 -r cheri && useradd -u 12345 --no-log-init -r -g cheri cheri

VOLUME ["/cheribuild", "/source", "/build", "/output"]
ENV PATH /cheribuild:$PATH
CMD bash
