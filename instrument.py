#!/usr/bin/env python3
"""Add a counter of tcpip_api_call() invocations, so the two branches can be
compared on how often they cross to the LwIP thread.  Applies to whichever branch
is checked out; `git checkout src/` reverts it."""
import re, sys
p = "/home/wmiles/Projects/AsyncTCP/src/AsyncTCP.cpp"
s = open(p).read()
if "_asynctcp_api_calls" in s:
    print("already instrumented"); sys.exit(0)
anchor = "typedef struct {\n  struct tcpip_api_call_data call;"
assert anchor in s, "anchor not found"
s = s.replace(anchor, "volatile uint32_t _asynctcp_api_calls = 0;\n\n" + anchor, 1)
# queue-mutex acquisitions, counted in the guard's constructor (both branch shapes)
# Must sit OUTSIDE the anonymous namespace the guard lives in, or it gets
# internal linkage and the sketch cannot see it.
ns = "// Guard class for the global queue\nnamespace {"
assert ns in s
s = s.replace(ns, "volatile uint32_t _asynctcp_lock_takes = 0;\n\n" + ns, 1)
a = "holds_mutex(xSemaphoreTake(_async_queue_mutex, portMAX_DELAY))"
b = "holds_mutex = mutex && (xSemaphoreTake(mutex, portMAX_DELAY) == pdTRUE);"
if a in s:
    s = s.replace(a, "holds_mutex((++_asynctcp_lock_takes, xSemaphoreTake(_async_queue_mutex, portMAX_DELAY)))", 1)
elif b in s:
    s = s.replace(b, "++_asynctcp_lock_takes;\n    " + b, 1)
else:
    raise SystemExit("guard constructor shape not recognised")
s, n = re.subn(r"(\n( +))tcpip_api_call\(", r"\1++_asynctcp_api_calls;\1tcpip_api_call(", s)
open(p, "w").write(s)
print(f"instrumented {n} call sites")
