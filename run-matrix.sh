#!/bin/bash
# Measure each given AsyncTCP branch once per round, cycling between them.
#
# Interleaving matters: networks drift, and measuring one branch three times in
# a row lets that drift look like a property of the branch.  One round of every
# branch, repeated, spreads any drift evenly across them.
#
# Every ref is resolved and built once, before the first round, so each round
# measures the same firmware and a broken build fails the run up front.
#
# Records go to $BENCH_OUT, by default a new file under results/.
#
#   ./run-matrix.sh arduino-3 3 main my-fix     # 3 rounds of both branches
#   BENCH_WEBSERVER=v3.12.1 ./run-matrix.sh arduino-3 3 main my-fix
set -euo pipefail
if (( $# < 3 )); then
  echo "usage: run-matrix.sh <pio-env> <rounds> <branch>..." >&2
  exit 2
fi
ENV=$1
if [[ $ENV == local ]]; then
  echo "run-matrix.sh: the local env builds the working trees; use collect.py --env local" >&2
  exit 2
fi
ROUNDS=$2
BRANCHES=("${@:3}")
WEBSERVER=${BENCH_WEBSERVER:-HEAD}
OUT=${BENCH_OUT:-results/$(date -u +%Y%m%dT%H%M%SZ)-$ENV.jsonl}
PY=${PLATFORMIO_CORE_DIR:-$HOME/.platformio}/penv/bin/python
[[ -x $PY ]] || PY=python3
cd "$(dirname "$0")"
mkdir -p "$(dirname "$OUT")"
# A /dev/serial/by-id path keeps a USB re-enumeration from moving the board
# out from under a long run.
BENCH_PORT="${BENCH_PORT:-/dev/ttyUSB0}"

commit() { git -C "$1" rev-parse --verify "$2^{commit}"; }
WEBSERVER_SHA=$(commit "${BENCH_WEBSERVER_DIR:-../ESPAsyncWebServer}" "$WEBSERVER")
declare -A SHA
for b in "${BRANCHES[@]}"; do
  SHA[$b]=$(commit "${BENCH_ASYNCTCP_DIR:-../AsyncTCP}" "$b")
done

target() {
  echo --env "$ENV" --branch "$1" --commit "${SHA[$1]}" \
       --webserver "$WEBSERVER" --webserver-commit "$WEBSERVER_SHA"
}

for b in "${BRANCHES[@]}"; do
  echo "--- build: $b on $ENV ---"
  $PY ./collect.py $(target "$b") --build-only
done
for r in $(seq 1 "$ROUNDS"); do
  for b in "${BRANCHES[@]}"; do
    echo "--- round $r: $b on $ENV ---"
    $PY ./collect.py $(target "$b") --no-build --round "$r" \
        --port "$BENCH_PORT" --out "$OUT" \
        --repeats 2 --duration 10 --sse-duration 15 --no-autocannon
  done
done
echo "records in $OUT; python3 summarize.py $OUT"
