#!/usr/bin/env bash
# Compile an open.mp server's scripts with several pawncc builds and record time, peak memory, .amx md5
# and the STKEST line. Run in the server image, e.g.:
#   docker run --rm --entrypoint bash -v <server>/src:/mnt/src:ro -v <repo>/.bench-work:/bench omp-server:dev \
#     -c 'bash /bench/real-script.sh run new-Release:new: old-Release:old-e:-e/tmp/w/err.txt'
# (copy this script into .bench-work first). SRCS overrides the scripts (default src/gamemodes/main.pwn).
set -u
OUT=/bench/$1; shift; mkdir -p "$OUT"
W=/tmp/w; rm -rf $W; mkdir -p $W/gen; cp -r /mnt/src $W/src; cd $W
cat > gen/build_info.inc <<INC
#if defined _build_info_included
	#endinput
#endif
#define _build_info_included
#define BUILD_ID "bench"
INC
FLAGS=('-;+' '-(+' '-\' '-Z-' '-d3' '-O0' '-t4' '-E' '-i/srv/omp/qawno/include' '-isrc/include' '-igen')
run() { # run <build> <label> <src> <name> <extra...>
  local b=$1 label=$2 src=$3 name=$4; shift 4
  local o="$OUT/$name.$label"
  local t0=$(date +%s.%N) hwm=0 h
  LD_LIBRARY_PATH=/bench/bin/$b /bench/bin/$b/pawncc "$src" "-o$W/$name.$label.amx" "${FLAGS[@]}" "$@" >"$o.stdout" 2>"$o.stderr" &
  local pid=$!
  while kill -0 $pid 2>/dev/null; do
    h=$(awk '/VmHWM/{print $2}' /proc/$pid/status 2>/dev/null); [[ -n "$h" && "$h" -gt "$hwm" ]] && hwm=$h; sleep 0.2
  done
  wait $pid; local rc=$?
  local t1=$(date +%s.%N)
  local md5=$(md5sum < "$W/$name.$label.amx" 2>/dev/null | cut -c1-12)
  printf '%s\t%s\t%s\trc=%s\twall=%.1fs\tpeakKB=%s\tamx=%s\t%s\n' "$name" "$label" "$b" "$rc" "$(awk "BEGIN{print $t1 - $t0}")" "$hwm" "$md5" "$(grep STKEST "$o.stderr")" | tee -a "$OUT/results.tsv"
}
for v in "$@"; do
  IFS=: read -r b label extra <<<"$v"; IFS=, read -r -a ex <<<"$extra"
  for src in ${SRCS:-src/gamemodes/main.pwn}; do run "$b" "$label" "$src" "$(basename "$src" .pwn)" "${ex[@]}"; done
done
