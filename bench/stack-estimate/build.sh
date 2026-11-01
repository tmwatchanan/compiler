#!/bin/bash
# Build 32-bit Linux pawncc in Docker from a git ref or the working tree.
# usage: bash bench/stack-estimate/build.sh <name> (--ref <gitref> | --worktree)
#          [--config Release|Debug] [--patch <file.patch>]
# Output: .bench-work/bin/<name>-<config>/{pawncc,libpawnc.so,pawndisasm,pawnruns}
set -e
export MSYS_NO_PATHCONV=1
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
WORK="$REPO/.bench-work"
# Windows-style paths for docker (Git Bash); plain paths elsewhere
W() { (cd "$1" && pwd -W 2>/dev/null || pwd); }
NAME="$1"; shift
MODE=""; REF=""; CONFIG=Release; PATCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) MODE=ref; REF="$2"; shift 2;;
    --worktree) MODE=worktree; shift;;
    --config) CONFIG="$2"; shift 2;;
    --patch) PATCH="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2;;
    *) echo "unknown arg $1" >&2; exit 2;;
  esac
done
[ -n "$NAME" ] && [ -n "$MODE" ] || { echo "usage: build.sh <name> (--ref R | --worktree) [--config C] [--patch F]" >&2; exit 2; }
TAG="$NAME-$CONFIG"
SRC="$WORK/src/$TAG"
OUT="$WORK/bin/$TAG"
rm -rf "$SRC" "$OUT"; mkdir -p "$SRC" "$OUT"
cd "$REPO"
if [ "$MODE" = ref ]; then
  git archive "$REF" | tar -x -C "$SRC"
else
  git ls-files -z --cached --others --exclude-standard | xargs -0 -I{} cp --parents {} "$SRC" 2>/dev/null \
    || git ls-files -z | xargs -0 cp --parents -t "$SRC"
fi
[ -z "$PATCH" ] || patch -d "$SRC" -p1 < "$PATCH"
docker image inspect pawncc-bench:latest >/dev/null 2>&1 \
  || docker build -t pawncc-bench:latest "$(W "$HERE")"
# copy sources into the container's own fs, build there, copy binaries out
docker run --rm -e CONFIG="$CONFIG" -v "$(W "$SRC"):/in:ro" -v "$(W "$OUT"):/out" pawncc-bench:latest sh -c '
  set -e
  mkdir -p /tmp/src && cp -r /in/. /tmp/src/ && cd /tmp/src
  cmake -S source/compiler -B build -G Ninja -DCMAKE_BUILD_TYPE=$CONFIG \
    -DCMAKE_C_FLAGS="-m32 -DsNAMEMAX=63" -DBUILD_TESTING=True
  cmake --build build --parallel $(nproc)
  for f in pawncc libpawnc.so pawndisasm pawnruns; do
    p=$(find build -name $f -type f | head -1); [ -n "$p" ] && cp "$p" /out/ || echo "note: $f not built"
  done
  ls -la /out'
