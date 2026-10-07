# ESPAsyncWebServerBench

Performance benchmarks for [AsyncTCP](https://github.com/ESP32Async/AsyncTCP) and
[ESPAsyncWebServer](https://github.com/ESP32Async/ESPAsyncWebServer) on an ESP32.
It builds a test sketch against chosen git refs of both libraries, flashes it, puts
the board under HTTP and Server-Sent Events load from the host, and writes one JSON
record per run.

The tools are written to be driven by an AI coding assistant as much as by hand;
see [Notes for operators](#notes-for-operators).

## Requirements

- An ESP32 dev board (`esp32dev`) on a USB serial port.
- Linux host.  `load.py` reads `/proc/net/snmp` for segment counts.
- [PlatformIO](https://platformio.org/).  Its venv python supplies `pyserial`;
  set `PLATFORMIO_CORE_DIR` if it is not in `~/.platformio`.
- `git`, `curl`, and optionally `npx` for the autocannon reference run.
- AsyncTCP and ESPAsyncWebServer clones beside this one:

  ```
  Projects/
    AsyncTCP/
    ESPAsyncWebServer/
    ESPAsyncWebServerBench/
  ```

  or elsewhere, named by `BENCH_ASYNCTCP_DIR` and `BENCH_WEBSERVER_DIR`.

## WiFi

With no credentials the board starts a soft-AP named `esp-captive` at 192.168.4.1,
and the host must join it.  To use an existing network instead, create
`src/bench_credentials.h` (gitignored):

```c
#pragma once
#define WIFI_SSID "my-network"
#define WIFI_PASS "my-password"
#define WIFI_BSSID "AA:BB:CC:DD:EE:FF"  // optional; pins one access point
```

Without `WIFI_BSSID` the board joins the strongest AP for the SSID.  On a mesh,
pin it: roaming between APs, or joining a distant one, skews every number.

## Running

Benchmark one AsyncTCP ref:

```sh
python=${PLATFORMIO_CORE_DIR:-~/.platformio}/penv/bin/python
$python collect.py --branch main --out results/main.jsonl
```

`--branch` and `--webserver` (default `HEAD`) take any git ref.  Each is resolved to
a commit and built from git, so only committed work is measured and neither working
tree is touched.  `--port` selects the serial port (default `/dev/ttyUSB0`).
`$python collect.py --help` lists the rest.

Compare several refs:

```sh
BENCH_PORT=/dev/ttyUSB0 ./run-matrix.sh arduino-3 3 main my-fix
```

This builds every ref up front, then measures each once per round, cycling between
them, for 3 rounds.  Records go to a new file under `results/`.
`BENCH_WEBSERVER` picks the ESPAsyncWebServer ref; `BENCH_EXTRA_FLAGS` adds build
flags (e.g. `-D BENCH_SOFTAP`) and is recorded with the results.

Summarize:

```sh
python3 summarize.py results/<run>.jsonl
```

This prints median requests/sec, latency, stall rate and SSE events/sec per env
and branch, relative to `main` when `main` was measured.

### PlatformIO environments

- `arduino-3` builds the commits `collect.py` selects.  Use this for measurements.
- `arduino-2` does the same on Arduino 2 (espressif32 6.x).  Its packages share
  slots with `arduino-3`'s, so build one framework at a time and compare branches
  only within a framework.  `merge_factory.py` writes its `firmware.factory.bin`.
- `local` builds the AsyncTCP and ESPAsyncWebServer working trees, uncommitted
  changes included, for a quick look before committing.  See below.

### Measuring uncommitted work

```sh
$python collect.py --env local --branch wip-fix --out results/scratch.jsonl
$python collect.py --branch main --out results/scratch.jsonl
python3 summarize.py results/scratch.jsonl
```

With `--env local`, `--branch` is only a label (default `local`).  The record holds
each repo's `HEAD` in `sha` and `webserver_sha`, and, when a tree has uncommitted
changes, a hash of them in `changes` and `webserver_changes`, so two runs of the same
edits can be matched up.  It builds and measures in one go: refs, `--build-only` and
`--no-build` are refused, as is `run-matrix.sh local`.

A run like this is not interleaved, so trust only large differences.  Commit what
looks promising and confirm it with `run-matrix.sh`.

## What is measured

Each record holds:

- **idle:** `loop()` iterations/sec with no load, the ceiling for the loaded figures.
- **http:** `load.py` runs N connections looping connect / `GET /` / read / close.
  It reports requests/sec, latency percentiles, stalls over 500 ms, and failures
  split into reset, truncated, connect and timeout.
- **sse:** workers subscribe to `/events`.  The sketch numbers every event, so
  drops and generation gaps are counted, not inferred.
- **http_device / sse_device:** the board's own counters from `GET /stats`: loop
  rate, heap low-water mark, and per-task CPU where the framework has run-time
  stats.
- **autocannon:** a reference run, kept for continuity.  Its error counts are
  not comparable with `load.py`'s.  Skip it with `--no-autocannon`.

## Notes for operators

Advice gathered from running this suite, for people and AI assistants alike:

- **Compare medians, never means.**  A dropped packet costs its connection about
  a second, which moves a mean far more than any code change.
- **Interleave rounds.**  WiFi conditions drift over minutes.  Use `run-matrix.sh`
  rather than measuring one branch several times in a row, and run at least 3
  rounds before calling a difference.
- **Check `rssi` and `bssid` in every record.**  A record that joined a different AP is
  not comparable, and a large RSSI shift is worth a rerun.
- **Commit before measuring.**  Every env but `local` builds from git, so
  uncommitted edits are silently left out.
- **Builds are cached** per ref pair and flag set under `.pio/bench/`.  Delete that
  directory to force clean builds.
- **Flashing** writes `firmware.factory.bin` at 0x0 with esptool directly, retrying
  up to four times.  The esptool in PlatformIO's venv may be 4.x or 5.x; the flag
  spellings used (`write_flash`, `default_reset`) work in both.
- **Report** results with the refs, commit SHAs, env, round count, RSSI and BSSID,
  so they can be reproduced.

## License

LGPL-3.0-or-later; see [LICENSE](LICENSE).
