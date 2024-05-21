FROM python:latest

LABEL maintainer="Alexander.Richardson@cl.cam.ac.uk"

# setting Git username and email for workaround of
# https://github.com/jenkinsci/docker/issues/519
ENV GIT_COMMITTER_NAME cheribuild
ENV GIT_COMMITTER_EMAIL cheribuild@cl.cam.ac.uk
