#!/usr/bin/env bash

set -xe

export _ARGCOMPLETE=1
export _ARGCOMPLETE_BENCHMARK=1
python3 -m cProfile -o ./cheribuild.prof ../cheribuild.py
snakeviz ./cheribuild.prof
