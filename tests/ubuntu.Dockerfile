FROM ubuntu:16.04

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

RUN apt-get update && apt-get install -y  --no-install-recommends \
  make ninja-build \
  gcc \
  git \
  python3-minimal python3-pip python3-setuptools
# Work around https://github.com/jaraco/zipp/issues/40 and
# https://github.com/pypa/pip/issues/5599
RUN python3 -m pip install --upgrade pip && python3 -m pip install pytest
