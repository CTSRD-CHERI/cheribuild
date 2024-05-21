FROM ubuntu:20.04

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

RUN apt-get update && apt-get install -y  --no-install-recommends \
  make ninja-build \
  gcc \
  git \
  python3-minimal python3-pip python3-setuptools

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --user -r /tmp/requirements.txt && rm -f /tmp/requirements.txt
