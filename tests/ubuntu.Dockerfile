#FROM ubuntu:16.04
FROM ubuntu:xenial-20210114

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

RUN apt-get update && apt-get install -y  --no-install-recommends \
  make ninja-build \
  gcc \
  git \
  python3-minimal python3-pip python3-setuptools
# Work around https://github.com/jaraco/zipp/issues/40 and
# https://github.com/pypa/pip/issues/5599
# We have to pin pytest to 6.1.2 since 6.2 dropped support for python 3.5
# Same for packaging: 20.9 is the last python3.5 version
RUN python3 -m pip install importlib-metadata==1.0.0 pytest==6.1.2 packaging==20.9
