#!/bin/bash
# Measure every AsyncTCP branch once per round, cycling between them.
#
# Interleaving matters: this network drifts, and measuring one branch three times
# in a row lets that drift look like a property of the branch.  One round of all
# three, repeated, spreads any drift evenly across them.
#
# Every ref is resolved and built once, before the first round, so each round
# measures the same firmware and a broken build fails the run up front.
#
# Records go to $BENCH_OUT, by default a new file under results/.
#
#   ./run-matrix.sh arduino-3 3        # 3 rounds of all branches
#   BENCH_BRANCHES="main my-fix" BENCH_WEBSERVER=v3.12.1 ./run-matrix.sh arduino-3 3
set -euo pipefail
ENV=${1:?usage: run-matrix.sh <pio-env> [rounds]}
ROUNDS=${2:-3}
BRANCHES=(${BENCH_BRANCHES:-main core-changes core-changes-us})
WEBSERVER=${BENCH_WEBSERVER:-HEAD}
OUT=${BENCH_OUT:-results/$(date -u +%Y%m%dT%H%M%SZ)-$ENV.jsonl}
PY=~/.platformio/penv/bin/python
cd "$(dirname "$0")"
mkdir -p "$(dirname "$OUT")"
# by-id, so a USB re-enumeration does not move the board out from under a long run
BENCH_PORT="${BENCH_PORT:-/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0}"

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
