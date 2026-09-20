#!/usr/bin/env python3
"""Concurrent HTTP load generator with an explicit failure taxonomy.

autocannon reports a single opaque "errors" number, which made it impossible to
tell a server defect from an artefact of its own connection handling.  This does
the same job -- N workers looping connect / GET / read-to-EOF / close -- but says
what actually went wrong.
"""
import argparse, json, socket, statistics, threading, time



def _tcp_counters():
    """Cumulative kernel TCP counters.  Taken either side of a load window they give
    segments exchanged per request, which is what the board's wifi task actually charges
    for - and unlike a packet capture this needs no root and cannot differ between arms."""
    try:
        with open("/proc/net/snmp") as f:
            lines = f.read().split("\n")
    except OSError:
        return None
    for i, l in enumerate(lines):
        if l.startswith("Tcp:") and not l.split()[1].isdigit():
            keys = l.split()[1:]
            vals = lines[i + 1].split()[1:]
            d = dict(zip(keys, (int(v) for v in vals)))
            return {k: d[k] for k in ("InSegs", "OutSegs", "RetransSegs") if k in d}
    return None


def worker(ip, port, path, deadline, out):
    req = ("GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: bench\r\n\r\n" % (path, ip)).encode()
    ok = 0; lat = []
    # Split the request so a regression can be placed: time to complete the TCP handshake
    # (the accept path), then time from the request going out to the first byte coming
    # back (the serve path), then the body.  A single total cannot tell those apart.
    phases = {"connect": [], "ttfb": [], "read": []}
    fail = {"reset": 0, "truncated": 0, "connect": 0, "timeout": 0, "other": 0}
    while time.time() < deadline:
        s = socket.socket(); s.settimeout(10)
        t0 = time.time()
        try:
            s.connect((ip, port))
        except socket.timeout:
            fail["timeout"] += 1; s.close(); continue
        except OSError:
            fail["connect"] += 1; s.close(); continue
        t_conn = time.time()
        try:
            s.sendall(req)
            data = b""
            t_first = None
            while True:
                b = s.recv(8192)
                if not b:
                    break
                if t_first is None:
                    t_first = time.time()
                data += b
            if b"200 OK" in data and b"</html>" in data:
                t_end = time.time()
                ok += 1; lat.append((t_end - t0) * 1000)
                phases["connect"].append((t_conn - t0) * 1000)
                if t_first is not None:
                    phases["ttfb"].append((t_first - t_conn) * 1000)
                    phases["read"].append((t_end - t_first) * 1000)
            elif data:
                fail["truncated"] += 1
            else:
                fail["reset"] += 1
        except ConnectionResetError:
            fail["reset"] += 1
        except socket.timeout:
            fail["timeout"] += 1
        except OSError:
            fail["other"] += 1
        finally:
            s.close()
    out.append((ok, lat, fail, phases))


def run(ip, port, path, connections, duration):
    out = []
    snmp0 = _tcp_counters()
    deadline = time.time() + duration
    ts = [threading.Thread(target=worker, args=(ip, port, path, deadline, out)) for _ in range(connections)]
    t0 = time.time()
    for t in ts: t.start()
    for t in ts: t.join()
    elapsed = time.time() - t0

    snmp1 = _tcp_counters()
    ok = sum(o for o, _, _, _ in out)
    lat = [x for _, l, _, _ in out for x in l]
    fail = {}
    for _, _, f, _ in out:
        for k, v in f.items():
            fail[k] = fail.get(k, 0) + v
    lat.sort()
    # A single dropped packet costs its connection about a second, because TCP retry
    # timers are long and it bites hardest at connection setup.  Those stalls dominate
    # the mean while leaving the typical response untouched, so the median is the
    # honest headline and the stall count is reported beside it as a network signal.
    stalls = sum(1 for x in lat if x > 500)
    return {
        "connections": connections,
        "duration_s": round(elapsed, 2),
        "ok": ok,
        "rps": round(ok / elapsed, 1),
        "latency_avg_ms": round(statistics.fmean(lat), 1) if lat else None,
        "latency_p50_ms": round(lat[len(lat) // 2], 1) if lat else None,
        "latency_p95_ms": round(lat[int(len(lat) * 0.95)], 1) if lat else None,
        "latency_max_ms": round(lat[-1], 1) if lat else None,
        "failures": fail,
        "stalls_over_500ms": stalls,
        "stall_pct": round(100.0 * stalls / len(lat), 2) if lat else None,
        "failure_total": sum(fail.values()),
        "phases_ms": _phase_summary(out),
        "tcp_segs": _seg_summary(snmp0, snmp1, ok),
    }


def _seg_summary(a, b, ok):
    """Segments per completed request, from the kernel's own counters."""
    if not a or not b or not ok:
        return None
    d = {k: b[k] - a[k] for k in a}
    out = {k.lower(): v for k, v in d.items()}
    out["in_per_req"] = round(d["InSegs"] / ok, 2)
    out["out_per_req"] = round(d["OutSegs"] / ok, 2)
    return out


def _phase_summary(out):
    """Median and p95 of each phase, so a shift can be attributed to one of them."""
    agg = {}
    for name in ("connect", "ttfb", "read"):
        xs = sorted(x for _, _, _, p in out for x in p[name])
        if not xs:
            continue
        agg[name] = {
            "p50": round(xs[len(xs) // 2], 2),
            "p95": round(xs[int(len(xs) * 0.95)], 2),
            "n": len(xs),
        }
    return agg or None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ip")
    ap.add_argument("--port", type=int, default=80)
    ap.add_argument("--path", default="/")
    ap.add_argument("-c", "--connections", type=int, default=8)
    ap.add_argument("-d", "--duration", type=int, default=10)
    args = ap.parse_args()
    print(json.dumps(run(args.ip, args.port, args.path, args.connections, args.duration)))
