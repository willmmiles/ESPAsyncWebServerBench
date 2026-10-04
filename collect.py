#!/usr/bin/env python3
"""Collect ESPAsyncWebServer performance numbers for one AsyncTCP branch.

Builds and uploads src/PerfTests.cpp, waits for the board to associate, then runs
the benchmarks and writes one JSON record per configuration.

Run it with the PlatformIO venv python, which has pyserial:

    ~/.platformio/penv/bin/python collect.py --branch refcounted-impl

--branch and --webserver take any git ref.  Each is resolved to a commit and built
from git, so only committed work is measured and neither working tree is touched.
--commit and --webserver-commit pin those commits instead, and --build-only and
--no-build split building from measuring, so a matrix builds each target once.
Use --skip-upload --ip A.B.C.D to re-measure a board that is already running.
"""

import argparse
import hashlib
import json
import statistics
import importlib.util
import os
import re
import subprocess
import threading
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ASYNCTCP = os.environ.get("BENCH_ASYNCTCP_DIR", os.path.join(HERE, "../AsyncTCP"))
WEBSERVER = os.environ.get("BENCH_WEBSERVER_DIR", os.path.join(HERE, "../ESPAsyncWebServer"))
PIO = os.path.expanduser("~/.platformio/penv/bin/pio")
ESPTOOL = os.path.expanduser("~/.platformio/penv/bin/esptool")

_spec = importlib.util.spec_from_file_location("load", os.path.join(HERE, "load.py"))
load = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(load)


def run(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


def git(repo, *args):
    return run(["git", "-C", repo, *args])


def resolve(repo, ref):
    r = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if r.returncode:
        sys.exit(f"{ref} is not a commit in {repo}")
    sha = r.stdout.strip()
    print(f"  {os.path.basename(os.path.normpath(repo))} @ {ref} ({sha[:7]})")
    return sha


def pio_env(env, asynctcp_sha, webserver_sha):
    extra = os.environ.get("BENCH_EXTRA_FLAGS", "")
    # One workspace per build, so a later round rebuilds nothing and pio never has to
    # notice that a library spec changed under an existing libdeps folder.
    key = f"{env}-{asynctcp_sha[:12]}-{webserver_sha[:12]}"
    if extra:
        key += "-" + hashlib.sha1(extra.encode()).hexdigest()[:8]
    return dict(
        os.environ,
        BENCH_ASYNCTCP=f"AsyncTCP=git+file://{os.path.realpath(ASYNCTCP)}#{asynctcp_sha}",
        BENCH_WEBSERVER=f"ESPAsyncWebServer=git+file://{os.path.realpath(WEBSERVER)}#{webserver_sha}",
        PLATFORMIO_WORKSPACE_DIR=os.path.join(HERE, ".pio", "bench", key),
        PLATFORMIO_BUILD_FLAGS=extra,
    )


def build(env, env_vars):
    r = subprocess.run([PIO, "run", "-e", env], cwd=HERE, capture_output=True, text=True, env=env_vars)
    if r.returncode:
        sys.exit(r.stdout[-3000:] + r.stderr[-2000:])


def upload(env, port, env_vars):
    # The merged image carries bootloader, partitions and boot_app0, so flashing it
    # needs nothing from the build or the shared framework package.  (pio's own
    # `-t nobuild -t upload` drops those images from the esptool command.)
    firmware = os.path.join(env_vars["PLATFORMIO_WORKSPACE_DIR"], "build", env, "firmware.factory.bin")
    if not os.path.exists(firmware):
        sys.exit(f"no firmware at {firmware}; build it first")
    # Retry before giving up, so one failed bootloader sync does not end a long matrix.
    # Underscore spellings: penv's esptool resolves to whichever tool-esptoolpy last
    # took the shared package slot, 4.x or 5.x, and only these work in both.
    for attempt in (1, 2, 3, 4):
        r = subprocess.run(
            [ESPTOOL, "--port", port, "--baud", "460800", "--before", "default_reset",
             "--after", "hard_reset", "write_flash", "-z", "0x0", firmware],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            return
        print(f"  upload attempt {attempt} failed")
    sys.exit(r.stdout[-3000:] + r.stderr[-2000:])


def wait_for_board(port, timeout=90):
    """Reset the board and read the BENCH lines the sketch prints on association."""
    import serial

    s = serial.Serial(port, 115200, timeout=1)
    s.setDTR(False)
    s.setRTS(True)
    time.sleep(0.1)
    s.setRTS(False)
    time.sleep(0.2)
    info = {}
    start = time.time()
    try:
        while time.time() - start < timeout:
            line = s.readline()
            if not line:
                continue
            t = line.decode("utf-8", "replace").strip()
            if t.startswith("BENCH "):
                for k, v in re.findall(r"(\w+)=(\S+)", t):
                    info[k] = v
                if "ip" in info:
                    return info
    finally:
        s.close()
    sys.exit(f"board did not report an IP within {timeout}s (got {info})")


def autocannon_bench(ip, connections, duration):
    """Kept because the example documents it, but its error and latency figures are
    not trustworthy here: it counts the server's `Connection: close` against itself,
    which inflates both.  Recorded for continuity, not for judging a change."""
    r = run(["npx", "--yes", "autocannon@7", "-c", str(connections),
             "-d", str(duration), "--json", f"http://{ip}/"])
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        print("  autocannon produced no JSON:", r.stdout[-300:], r.stderr[-300:])
        return None
    return {
        "rps_avg": d["requests"]["average"],
        "rps_stddev": d["requests"]["stddev"],
        "latency_avg_ms": d["latency"]["average"],
        "latency_p975_ms": d["latency"]["p97_5"],
        "latency_max_ms": d["latency"]["max"],
        "ok_2xx": d["2xx"],
        "non2xx": d["non2xx"],
        "errors": d["errors"],
        "timeouts": d["timeouts"],
        "bytes_per_sec": d["throughput"]["average"],
    }


def read_stats(ip, reset=False):
    """GET /stats from the sketch.  With reset=True the board zeroes its counters after
    answering, so the next read covers exactly the window we are about to run."""
    url = f"http://{ip}/stats?reset=1" if reset else f"http://{ip}/stats"
    r = run(["curl", "-s", "--max-time", "10", url])
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def task_cpu(before, after):
    """Per-task CPU over the window, as a share of the total the scheduler accounted for.

    Only arduino-3 reports this: Arduino 3 / IDF5 is built with
    CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS, Arduino 2 / IDF4 is not.
    """
    if not before or not after:
        return None
    b, a = before.get("tasks"), after.get("tasks")
    if not b or not a:
        return None
    total = a.get("_total", 0) - b.get("_total", 0)
    if total <= 0:
        return None
    out = {}
    for name, end in a.items():
        if name == "_total":
            continue
        delta = end - b.get(name, 0)
        if delta > 0:
            out[name] = round(100.0 * delta / total, 2)
    return {"window_ticks": total, "pct": dict(sorted(out.items(), key=lambda kv: -kv[1]))}



SS_BIN = "/usr/sbin/ss"
SS_FIELD = re.compile(rb"(bytes_received|data_segs_in|segs_in|retrans|rcv_ooopack|rcvmss|mss):([0-9/]+)")
SS_PORT = re.compile(rb":(\d+)\s+" + re.escape(b"") + rb"[\d.]+:http")


def ss_snapshot(ip):
    """Per-socket TCP counters for connections to the board, keyed by local port."""
    try:
        r = subprocess.run([SS_BIN, "-ti", "dst", ip], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {}
    out, cur, socks = r.stdout, None, {}
    for line in out.split(b"\n"):
        m = re.search(rb"(\d+\.\d+\.\d+\.\d+):(\d+)\s+" + ip.encode() + rb":", line)
        if m:
            cur = int(m.group(2))
            continue
        if cur is None:
            continue
        vals = {}
        for k, v in SS_FIELD.findall(line):
            key = k.decode()
            txt = v.decode()
            vals[key] = int(txt.split("/")[-1]) if "/" in txt else int(txt)
        if vals:
            prev = socks.get(cur, {})
            # keep the largest sample per socket: counters only grow while it is open
            socks[cur] = {k: max(v, prev.get(k, 0)) for k, v in vals.items()}
    return socks


def ss_summary(samples):
    """Fold the per-socket samples into packing and loss figures."""
    merged = {}
    for snap in samples:
        for port, vals in snap.items():
            prev = merged.get(port, {})
            merged[port] = {k: max(v, prev.get(k, 0)) for k, v in vals.items()}
    if not merged:
        return None
    rx = sum(v.get("bytes_received", 0) for v in merged.values())
    segs = sum(v.get("data_segs_in", 0) for v in merged.values())
    if not segs:
        return None
    return {
        "sockets": len(merged),
        "bytes_received": rx,
        "data_segs_in": segs,
        "bytes_per_segment": round(rx / segs, 1),
        "mss": max((v.get("mss", 0) for v in merged.values()), default=0),
        "mss_utilisation_pct": round(
            100.0 * (rx / segs) / max(max((v.get("mss", 1) for v in merged.values()), default=1), 1), 2),
        "retrans": sum(v.get("retrans", 0) for v in merged.values()),
        "rcv_ooopack": sum(v.get("rcv_ooopack", 0) for v in merged.values()),
    }


SSE_EVENT = re.compile(rb"ping-(\d+)-(\d+)")


def sse_bench(ip, workers, duration):
    """N concurrent SSE readers.

    The sketch stamps every event "ping-<seq>-<millis>", which turns a raw count into a
    measurement.  Each reader connects at its own moment, so its own first and last seq -
    not the global range - bound what it could have received; comparing that span against
    what it actually got separates events dropped on the way to a client from events the
    board was too busy to generate at all.  Counting alone cannot tell those apart, and
    they have different causes.
    """
    procs = [
        subprocess.Popen(
            ["curl", "-s", "-N", "--max-time", str(duration),
             "-H", "Accept: text/event-stream", f"http://{ip}/events"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        for _ in range(workers)
    ]
    # Sample the kernel's per-socket counters while the readers are attached: they vanish
    # with the socket, so the last sample before close is the one that counts.
    ss_samples, ss_stop = [], threading.Event()

    def ss_poll():
        while not ss_stop.is_set():
            snap = ss_snapshot(ip)
            if snap:
                ss_samples.append(snap)
            ss_stop.wait(1.0)

    ss_thread = threading.Thread(target=ss_poll, daemon=True)
    ss_thread.start()

    counts, per_worker, seq_to_ms, seen = [], [], {}, set()
    malformed, out_of_order = [], 0
    for p in procs:
        out, _ = p.communicate(timeout=duration + 30)
        counts.append(out.count(b"\ndata:") + (1 if out.startswith(b"data:") else 0))
        # Parse per line, so a data: line that does not carry a well-formed stamp is
        # counted rather than silently skipped: out-of-order bytes in the stream look
        # exactly like a dropped event unless malformed lines are counted separately.
        seqs = []
        # curl is stopped by --max-time, so the stream usually ends mid-frame.  That last
        # partial line is an artefact of where we cut, not a malformed event.
        lines = out.split(b"\n")
        if not out.endswith(b"\n") and lines:
            lines.pop()
        for line in lines:
            if not line.startswith(b"data:"):
                continue
            m = SSE_EVENT.fullmatch(line[5:].strip())
            if not m:
                malformed.append(line[:80])
                continue
            seq = int(m.group(1))
            seqs.append(seq)
            seq_to_ms.setdefault(seq, int(m.group(2)))
        # Sequence numbers must arrive in the order the board produced them.
        backwards = sum(1 for a, b in zip(seqs, seqs[1:]) if b <= a)
        out_of_order += backwards
        seen.update(seqs)
        if seqs:
            lo, hi = min(seqs), max(seqs)
            window = hi - lo + 1          # events generated while this reader was attached
            missed = window - len(set(seqs))
            # A burst is a run of consecutive missing seqs: queue overflow drops in clumps,
            # while isolated losses are scattered.
            have, bursts, run_open = set(seqs), 0, False
            for n in range(lo, hi + 1):
                if n in have:
                    run_open = False
                elif not run_open:
                    bursts += 1
                    run_open = True
            per_worker.append({
                "received": len(seqs), "first_seq": lo, "last_seq": hi,
                "window": window, "missed": missed,
                "drop_pct": round(100.0 * missed / window, 2) if window else 0.0,
                "drop_bursts": bursts,
            })
        else:
            per_worker.append({"received": 0, "first_seq": None, "last_seq": None,
                               "window": 0, "missed": 0, "drop_pct": None, "drop_bursts": 0})

    ss_stop.set()
    ss_thread.join(timeout=3)

    total = sum(counts)
    result = {
        "workers": workers,
        "duration_s": duration,
        "events_total": total,
        "events_per_sec": round(total / duration, 1),
        "per_worker": counts,
        "per_worker_detail": per_worker,
        "tcp": ss_summary(ss_samples),
        "malformed_data_lines": len(malformed),
        "malformed_samples": [m.decode("utf-8", "replace") for m in malformed[:5]],
        "out_of_order": out_of_order,
    }

    # Generation cadence, from the board's own clock on both ends of each interval.  The
    # sketch aims to send every 10 ms, so intervals past that are loop() missing its slot.
    if len(seen) >= 2:
        lo, hi = min(seen), max(seen)
        result["seq_min"], result["seq_max"] = lo, hi
        result["generated"] = hi - lo + 1
        result["generated_per_sec"] = round((hi - lo + 1) / duration, 1)
        result["never_generated"] = (hi - lo + 1) - len(seen)  # gaps no reader saw
        gaps = []
        prev_seq = prev_ms = None
        for seq in sorted(seen):
            ms = seq_to_ms[seq]
            if prev_seq is not None and seq == prev_seq + 1 and ms >= prev_ms:
                gaps.append(ms - prev_ms)
            prev_seq, prev_ms = seq, ms
        if gaps:
            gaps.sort()
            result["gen_interval_ms"] = {
                "p50": gaps[len(gaps) // 2],
                "p95": gaps[min(len(gaps) - 1, int(len(gaps) * 0.95))],
                "max": gaps[-1],
                "over_20ms": sum(1 for g in gaps if g > 20),
                "n": len(gaps),
            }
    drops = [w["drop_pct"] for w in per_worker if w["drop_pct"] is not None]
    if drops:
        result["drop_pct_median"] = round(statistics.median(drops), 2)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="arduino-3", help="platformio env")
    ap.add_argument("--branch", required=True, help="AsyncTCP ref to test")
    ap.add_argument("--webserver", default="HEAD", help="ESPAsyncWebServer ref to test")
    ap.add_argument("--commit", help="AsyncTCP commit to build, recorded under --branch")
    ap.add_argument("--webserver-commit", help="ESPAsyncWebServer commit to build")
    ap.add_argument("--build-only", action="store_true", help="build the firmware and exit")
    ap.add_argument("--no-build", action="store_true", help="upload firmware built earlier")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--ip", help="skip build/upload and measure this address")
    ap.add_argument("--skip-upload", action="store_true")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--connections", type=int, default=8)
    ap.add_argument("--duration", type=int, default=10)
    ap.add_argument("--sse-workers", type=int, default=10)
    ap.add_argument("--sse-duration", type=int, default=20)
    ap.add_argument("--no-autocannon", action="store_true",
                    help="skip the reference run; its error counts are not comparable")
    ap.add_argument("--round", type=int, default=0,
                    help="round number, for interleaved runs")
    ap.add_argument("--out", default="/home/wmiles/Projects/bench/results.jsonl")
    args = ap.parse_args()

    print(f"== {args.branch} on {args.env} ==")
    sha = webserver_sha = None
    info = {}
    if args.ip and args.skip_upload:
        ip = args.ip
    else:
        sha = resolve(ASYNCTCP, args.commit or args.branch)
        webserver_sha = resolve(WEBSERVER, args.webserver_commit or args.webserver)
        env_vars = pio_env(args.env, sha, webserver_sha)
        if not args.no_build:
            build(args.env, env_vars)
        if args.build_only:
            return
        upload(args.env, args.port, env_vars)
        info = wait_for_board(args.port)
        ip = info["ip"]
    print(f"  board at {ip} rssi={info.get('rssi')} bssid={info.get('bssid')}")

    # Let the stack settle, and prove it answers before timing anything
    time.sleep(3)
    warm = run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                "--max-time", "10", f"http://{ip}/"])
    if warm.stdout.strip() != "200":
        sys.exit(f"board is not serving: got {warm.stdout!r}")

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "branch": args.branch,
        "build_flags": os.environ.get("BENCH_EXTRA_FLAGS", ""),
        "sha": sha and sha[:7],
        "webserver": args.webserver,
        "webserver_sha": webserver_sha and webserver_sha[:7],
        "env": args.env,
        "round": args.round,
        "ip": ip,
        "rssi": info.get("rssi"),
        "bssid": info.get("bssid"),
        "connections": args.connections,
        "duration_s": args.duration,
        "http": [],
        "autocannon": None,
        "sse": None,
        "idle": None,
        "http_device": None,
        "sse_device": None,
    }

    # Idle baseline: what the board does with no load on it.  loops/sec here is the
    # ceiling the loaded figures are measured against, and it is branch-sensitive too.
    read_stats(ip, reset=True)
    time.sleep(3)
    idle = read_stats(ip)
    if idle:
        record["idle"] = {
            "loops_per_sec": round(1000.0 * idle["loops"] / max(idle["window_ms"], 1), 1),
            "sse_generated_per_sec": round(1000.0 * idle["sse_generated"] / max(idle["window_ms"], 1), 1),
            "free_heap": idle.get("free_heap"),
        }
        print(f"  idle: {record['idle']['loops_per_sec']:.0f} loops/sec")

    before = read_stats(ip, reset=True)
    for i in range(args.repeats):
        r = load.run(ip, 80, "/", args.connections, args.duration)
        record["http"].append(r)
        print(f"  http run {i + 1}: {r['rps']} rps, {r['latency_avg_ms']} ms avg, "
              f"p95 {r['latency_p95_ms']} ms, {r['failure_total']} failures")
        time.sleep(2)
    after = read_stats(ip)
    if after:
        record["http_device"] = {
            "loops_per_sec": round(1000.0 * after["loops"] / max(after["window_ms"], 1), 1),
            "requests": after.get("requests"),
            "free_heap": after.get("free_heap"),
            "min_free_heap": after.get("min_free_heap"),
            "task_cpu": task_cpu(before, after),
        }
        print(f"  http device: {record['http_device']['loops_per_sec']:.0f} loops/sec")

    if not args.no_autocannon:
        record["autocannon"] = autocannon_bench(ip, args.connections, args.duration)
    if record["autocannon"]:
        a = record["autocannon"]
        print(f"  autocannon (reference): {a['rps_avg']:.1f} rps, {a['errors']} errors")

    before = read_stats(ip, reset=True)
    record["sse"] = sse_bench(ip, args.sse_workers, args.sse_duration)
    after = read_stats(ip)
    if after:
        record["sse_device"] = {
            "loops_per_sec": round(1000.0 * after["loops"] / max(after["window_ms"], 1), 1),
            "generated_per_sec": round(1000.0 * after["sse_generated"] / max(after["window_ms"], 1), 1),
            "free_heap": after.get("free_heap"),
            "task_cpu": task_cpu(before, after),
        }
    sse = record["sse"]
    print(f"  sse: {sse['events_per_sec']} events/sec delivered "
          f"across {args.sse_workers} workers")
    if "generated_per_sec" in sse:
        print(f"       generated {sse['generated_per_sec']}/sec on the board, "
              f"median per-client drop {sse.get('drop_pct_median')}%")
    if sse.get("gen_interval_ms"):
        g = sse["gen_interval_ms"]
        print(f"       send interval p50 {g['p50']} ms, p95 {g['p95']} ms, max {g['max']} ms "
              f"({g['over_20ms']}/{g['n']} over 20 ms)")
    if record["sse_device"]:
        d = record["sse_device"]
        print(f"       device: {d['loops_per_sec']:.0f} loops/sec")
        if d["task_cpu"]:
            top = list(d["task_cpu"]["pct"].items())[:5]
            print("       cpu: " + ", ".join(f"{k} {v}%" for k, v in top))

    with open(args.out, "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"  appended to {args.out}")


if __name__ == "__main__":
    main()
