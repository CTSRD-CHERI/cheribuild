FROM ubuntu:16.04

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

RUN apt-get update && apt-get install -y \
  make ninja-build \
  gcc \
  git \
  python3-minimal \
  libbsd-dev

# deps to build QEMU:
RUN apt-get update && apt-get install -y \
  libtool pkg-config python-minimal autotools-dev automake autoconf libglib2.0-dev libpixman-1-dev

RUN git config --global http.sslVerify false
RUN cd /tmp && git clone https://github.com/arichardson/bmake && cd bmake \
  && ./configure --with-default-sys-path=/usr/local/share/mk --with-machine=amd64 --without-meta --without-filemon --prefix=/usr/local \
  && sh ./make-bootstrap.sh && make install && rm -rf /tmp/bmake

COPY cheribuild.json /root/.config/cheribuild.json


VOLUME ["/cheribuild", "/source", "/build", "/output"]
ENV PATH /cheribuild:$PATH