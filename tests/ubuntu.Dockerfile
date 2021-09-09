#FROM ubuntu:16.04
FROM ubuntu:bionic-20180426

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

RUN apt-get update && apt-get install -y  --no-install-recommends \
  make ninja-build \
  gcc \
  git \
  python3-minimal python3-pip python3-setuptools
RUN python3 -m pip install "pytest==6.2.4"
