FROM ubuntu:18.04

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

RUN apt-get update && apt-get install -y \
  make ninja-build \
  gcc g++ \
  git \
  python3-minimal \
  lsb-release \
  wget \
  samba

# RUN git config --global http.sslVerify false
# RUN cd /tmp && git clone https://github.com/arichardson/bmake && cd bmake \
#  && ./configure --with-default-sys-path=/usr/local/share/mk --with-machine=amd64 --without-meta --without-filemon --prefix=/usr/local \
#  && sh ./make-bootstrap.sh && make install && rm -rf /tmp/bmake

COPY cheribuild.json /root/.config/cheribuild.json

# deps to build QEMU+elftoolchain:
RUN apt-get update && apt-get install -y \
  libtool pkg-config python-minimal autotools-dev automake autoconf libglib2.0-dev libpixman-1-dev \
  bison groff-base libarchive-dev flex

# INSTALL clang 8.0
RUN wget -O - https://apt.llvm.org/llvm-snapshot.gpg.key | apt-key add -
RUN echo "deb http://apt.llvm.org/bionic/ llvm-toolchain-bionic-8 main" > /etc/apt/sources.list.d/llvm.list
# RUN echo "deb http://ppa.launchpad.net/ubuntu-toolchain-r/test/ubuntu trusty main" > /etc/apt/sources.list.d/r-toolchain.list
# RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 1E9377A2BA9EF27F
RUN apt-get update && apt-get install -y clang-8 lld-8

RUN apt-get update && apt-get install -y cmake

VOLUME ["/cheribuild", "/source", "/build", "/output"]
ENV PATH /cheribuild:$PATH
CMD bash
