#!/bin/bash
# Measure tcpip_api_call() invocations per request for one branch on one env.
set -euo pipefail
BR=$1; ENV=$2
export PLATFORMIO_BUILD_FLAGS='-I/home/wmiles/Projects/bench'
cd /home/wmiles/Projects/AsyncTCP
git checkout -q -- src 2>/dev/null || true
git checkout -q "$BR"
python3 /home/wmiles/Projects/bench/instrument.py >/dev/null
cd /home/wmiles/Projects/ESPAsyncWebServer
~/.platformio/penv/bin/pio run -e "$ENV" -t upload --upload-port /dev/ttyUSB0 >/dev/null 2>&1 \
  || ~/.platformio/penv/bin/pio run -e "$ENV" -t upload --upload-port /dev/ttyUSB0 >/dev/null 2>&1
~/.platformio/penv/bin/python - "$BR" "$ENV" <<'PY'
import serial, subprocess, threading, time, sys, re
s=serial.Serial('/dev/ttyUSB0',115200,timeout=1)
s.setDTR(False); s.setRTS(True); time.sleep(0.1); s.setRTS(False); time.sleep(0.2)
ip=None; lines=[]; stop=False
def rd():
    while not stop:
        l=s.readline()
        if l: lines.append(l.decode('utf-8','replace').rstrip())
t=threading.Thread(target=rd); t.start()
for _ in range(80):
    time.sleep(1)
    for l in lines:
        if l.startswith('BENCH ip='): ip=l.split('=')[1].split()[0]
    if ip: break
time.sleep(3); lines.clear()
def sample():
    for l in reversed(lines):
        m=re.search(r'requests:\s*(\d+), api:\s*(\d+), lock:\s*(\d+)', l)
        if m: return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None
while sample() is None: time.sleep(1)
before=sample()
subprocess.run(["python3","/home/wmiles/Projects/bench/load.py",ip,"-c","8","-d","20"],capture_output=True)
time.sleep(3); stop=True; t.join(); s.close()
after=sample()
dr=after[0]-before[0]; da=after[1]-before[1]; dl=after[2]-before[2]
print("%-16s %-10s  requests %4d   api %.2f/req   queue-lock %.2f/req" % (
    sys.argv[1], sys.argv[2], dr, da/dr if dr else 0, dl/dr if dr else 0))
PY
