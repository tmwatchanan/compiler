#!/bin/bash
# Run a command in pawncc-bench:latest. Repo is /repo (read-only), .bench-work is
# /work (read-write), workdir /work.
# Set LD_LIBRARY_PATH=/work/bin/<x> so pawncc finds its libpawnc.so.
# NOTE: copy any .pwn/.p to a container-local dir (e.g. /tmp) before compiling:
# 32-bit pawncc can't reliably stat files on Windows bind mounts.
# usage: bash bench/stack-estimate/run.sh <command...>
export MSYS_NO_PATHCONV=1
REPO="$(cd "$(dirname "$0")/../.." && (pwd -W 2>/dev/null || pwd))"
exec docker run --rm -v "$REPO:/repo:ro" -v "$REPO/.bench-work:/work" -w /work \
  pawncc-bench:latest "$@"
