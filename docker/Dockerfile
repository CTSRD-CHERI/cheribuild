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
  texinfo \
  libtool pkg-config autotools-dev automake autoconf \
  libarchive-dev libglib2.0-dev libpixman-1-dev \
  bison groff-base flex \
  cmake \
  clang-12 lld-12

COPY cheribuild.json /root/.config/cheribuild.json

VOLUME ["/cheribuild", "/source", "/build", "/output"]
ENV PATH /cheribuild:$PATH
CMD bash
