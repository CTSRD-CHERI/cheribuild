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
  clang-12 lld-12 \
  gosu && \
  apt-get clean

COPY cheribuild.json /root/.config/cheribuild.json
COPY entrypoint.sh /usr/bin/entrypoint.sh

VOLUME ["/cheribuild", "/source", "/build", "/output"]
ENV PATH /cheribuild:$PATH
# We use an ENTRYPOINT script to ensure that cheribuild is run as a non-root
# user that has a UID/GID matching the host so that file ownership in the
# volumes
ENTRYPOINT ["/usr/bin/entrypoint.sh"]
CMD ["/bin/bash"]
