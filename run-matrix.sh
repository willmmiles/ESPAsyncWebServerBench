#!/bin/bash
# Measure every AsyncTCP branch once per round, cycling between them.
#
# Interleaving matters: this network drifts, and measuring one branch three times
# in a row lets that drift look like a property of the branch.  One round of all
# three, repeated, spreads any drift evenly across them.
#
#   ./run-matrix.sh arduino-3 3        # 3 rounds of all branches
set -euo pipefail
ENV=${1:?usage: run-matrix.sh <pio-env> [rounds]}
ROUNDS=${2:-3}
BRANCHES=(${BENCH_BRANCHES:-main core-changes})
PY=~/.platformio/penv/bin/python
cd /home/wmiles/Projects/ESPAsyncWebServer
export BENCH_ASYNCTCP_DIR="${BENCH_ASYNCTCP_DIR:-/home/wmiles/Projects/AsyncTCP}"
# by-id, so a USB re-enumeration does not move the board out from under a long run
BENCH_PORT="${BENCH_PORT:-/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0}"
for r in $(seq 1 "$ROUNDS"); do
  for b in "${BRANCHES[@]}"; do
    echo "--- round $r: $b on $ENV ---"
    $PY /home/wmiles/Projects/bench/collect.py \
        --env "$ENV" --branch "$b" --round "$r" \
        --port "$BENCH_PORT" \
        --repeats 2 --duration 10 --sse-duration 15 --no-autocannon
  done
done
