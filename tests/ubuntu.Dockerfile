FROM ubuntu:16.04

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

RUN apt-get update && apt-get install -y  --no-install-recommends \
  make ninja-build \
  gcc \
  git \
  python3-minimal python3-pip python3-setuptools python3-zipp
RUN pip3 install pytest
