// SPDX-License-Identifier: LGPL-3.0-or-later
// Copyright 2016-2026 Hristo Gochkov, Mathieu Carbou, Emil Muratov, Will Miles

//
// Perf tests
//

#include <Arduino.h>
#if defined(ESP32) || defined(LIBRETINY)
#include <AsyncTCP.h>
#include <WiFi.h>
#elif defined(ESP8266)
#include <ESP8266WiFi.h>
#include <ESPAsyncTCP.h>
#elif defined(TARGET_RP2040) || defined(TARGET_RP2350) || defined(PICO_RP2040) || defined(PICO_RP2350)
#include <RPAsyncTCP.h>
#include <WiFi.h>
#endif

#include <ESPAsyncWebServer.h>

static const char *htmlContent PROGMEM = R"(
<!DOCTYPE html>
<html>
<head>
    <title>Sample HTML</title>
</head>
<body>
    <h1>Hello, World!</h1>
    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin euismod, purus a euismod
    rhoncus, urna ipsum cursus massa, eu dictum tellus justo ac justo. Quisque ullamcorper
    arcu nec tortor ullamcorper, vel fermentum justo fermentum. Vivamus sed velit ut elit
    accumsan congue ut ut enim. Ut eu justo eu lacus varius gravida ut a tellus. Nulla facilisi.
    Integer auctor consectetur ultricies. Fusce feugiat, mi sit amet bibendum viverra, orci leo
    dapibus elit, id varius sem dui id lacus.</p>
    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin euismod, purus a euismod
    rhoncus, urna ipsum cursus massa, eu dictum tellus justo ac justo. Quisque ullamcorper
    arcu nec tortor ullamcorper, vel fermentum justo fermentum. Vivamus sed velit ut elit
    accumsan congue ut ut enim. Ut eu justo eu lacus varius gravida ut a tellus. Nulla facilisi.
    Integer auctor consectetur ultricies. Fusce feugiat, mi sit amet bibendum viverra, orci leo
    dapibus elit, id varius sem dui id lacus.</p>
    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin euismod, purus a euismod
    rhoncus, urna ipsum cursus massa, eu dictum tellus justo ac justo. Quisque ullamcorper
    arcu nec tortor ullamcorper, vel fermentum justo fermentum. Vivamus sed velit ut elit
    accumsan congue ut ut enim. Ut eu justo eu lacus varius gravida ut a tellus. Nulla facilisi.
    Integer auctor consectetur ultricies. Fusce feugiat, mi sit amet bibendum viverra, orci leo
    dapibus elit, id varius sem dui id lacus.</p>
    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin euismod, purus a euismod
    rhoncus, urna ipsum cursus massa, eu dictum tellus justo ac justo. Quisque ullamcorper
    arcu nec tortor ullamcorper, vel fermentum justo fermentum. Vivamus sed velit ut elit
    accumsan congue ut ut enim. Ut eu justo eu lacus varius gravida ut a tellus. Nulla facilisi.
    Integer auctor consectetur ultricies. Fusce feugiat, mi sit amet bibendum viverra, orci leo
    dapibus elit, id varius sem dui id lacus.</p>
    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin euismod, purus a euismod
    rhoncus, urna ipsum cursus massa, eu dictum tellus justo ac justo. Quisque ullamcorper
    arcu nec tortor ullamcorper, vel fermentum justo fermentum. Vivamus sed velit ut elit
    accumsan congue ut ut enim. Ut eu justo eu lacus varius gravida ut a tellus. Nulla facilisi.
    Integer auctor consectetur ultricies. Fusce feugiat, mi sit amet bibendum viverra, orci leo
    dapibus elit, id varius sem dui id lacus.</p>
    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin euismod, purus a euismod
    rhoncus, urna ipsum cursus massa, eu dictum tellus justo ac justo. Quisque ullamcorper
    arcu nec tortor ullamcorper, vel fermentum justo fermentum. Vivamus sed velit ut elit
    accumsan congue ut ut enim. Ut eu justo eu lacus varius gravida ut a tellus. Nulla facilisi.
    Integer auctor consectetur ultricies. Fusce feugiat, mi sit amet bibendum viverra, orci leo
    dapibus elit, id varius sem dui id lacus.</p>
    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin euismod, purus a euismod
    rhoncus, urna ipsum cursus massa, eu dictum tellus justo ac justo. Quisque ullamcorper
    arcu nec tortor ullamcorper, vel fermentum justo fermentum. Vivamus sed velit ut elit
    accumsan congue ut ut enim. Ut eu justo eu lacus varius gravida ut a tellus. Nulla facilisi.
    Integer auctor consectetur ultricies. Fusce feugiat, mi sit amet bibendum viverra, orci leo
    dapibus elit, id varius sem dui id lacus.</p>
    <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Proin euismod, purus a euismod
    rhoncus, urna ipsum cursus massa, eu dictum tellus justo ac justo. Quisque ullamcorper
    arcu nec tortor ullamcorper, vel fermentum justo fermentum. Vivamus sed velit ut elit
    accumsan congue ut ut enim. Ut eu justo eu lacus varius gravida ut a tellus. Nulla facilisi.
    Integer auctor consectetur ultricies. Fusce feugiat, mi sit amet bibendum viverra, orci leo
    dapibus elit, id varius sem dui id lacus.</p>
</body>
</html>
)";

static const size_t htmlContentLength = strlen_P(htmlContent);
static constexpr char characters[] = "0123456789ABCDEF";
static size_t charactersIndex = 0;

// Credentials live in an untracked local header so they never reach the repo.
#if __has_include("bench_credentials.h")
#include "bench_credentials.h"
#endif

#ifndef WIFI_SSID
#define WIFI_SSID "esp-captive"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS ""
#endif

static AsyncWebServer server(80);
static AsyncEventSource events("/events");

static volatile size_t requests = 0;

// ---- benchmark instrumentation ----------------------------------------------------
// Counters the host reads over /stats, so a run needs no serial traffic: printing from
// loop() under load blocks on a UART nobody is draining and would be measured as our cost.
static uint32_t bench_loops = 0;         // loop() iterations, the app-task CPU share
static uint32_t bench_sse_seq = 0;       // events the board generated, ever
static uint32_t bench_window_start = 0;  // millis() at the last counter reset
static uint32_t bench_window_seq = 0;    // bench_sse_seq at the last counter reset

static void bench_reset_window(uint32_t now) {
  bench_window_start = now;
  bench_window_seq = bench_sse_seq;
  bench_loops = 0;
  requests = 0;
}

// Per-task CPU, where the framework was built with run-time stats.  Arduino 3 / IDF5 sets
// CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS; Arduino 2 / IDF4 does not, and reports null.
#if defined(ESP32) && defined(CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS) && defined(CONFIG_FREERTOS_USE_TRACE_FACILITY)
#include <freertos/task.h>
static String bench_task_stats() {
  const UBaseType_t n = uxTaskGetNumberOfTasks();
  TaskStatus_t *st = (TaskStatus_t *)malloc(n * sizeof(TaskStatus_t));
  if (!st) {
    return "null";
  }
  uint32_t total = 0;
  const UBaseType_t got = uxTaskGetSystemState(st, n, &total);
  String out = "{";
  for (UBaseType_t i = 0; i < got; ++i) {
    if (i) {
      out += ',';
    }
    out += '"';
    out += st[i].pcTaskName;
    out += "\":";
    out += (uint32_t)st[i].ulRunTimeCounter;
  }
  out += ",\"_total\":";
  out += total;
  out += '}';
  free(st);
  return out;
}
#else
static String bench_task_stats() {
  return "null";
}
#endif

void setup() {
  Serial.begin(115200);

#if ASYNCWEBSERVER_WIFI_SUPPORTED
  // Station mode rather than the example's soft-AP, so the benchmark client is a real
  // host on the LAN.  This network has several APs on the same SSID and associating
  // with a distant one skews response times badly, so pin WIFI_BSSID, or failing that
  // the strongest BSSID - and print which one, so runs can be compared honestly.
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // power save adds tens of ms of latency

#ifdef WIFI_BSSID
  uint8_t bssid[6];
  sscanf(WIFI_BSSID, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx", &bssid[0], &bssid[1], &bssid[2], &bssid[3], &bssid[4], &bssid[5]);
  Serial.printf("BENCH bssid=%s\n", WIFI_BSSID);
  WiFi.begin(WIFI_SSID, WIFI_PASS, 0, bssid);
#else
  int best = -1;
  int n = WiFi.scanNetworks();
  for (int i = 0; i < n; i++) {
    if (WiFi.SSID(i) == WIFI_SSID) {
      if (best < 0 || WiFi.RSSI(i) > WiFi.RSSI(best)) {
        best = i;
      }
    }
  }

  if (best >= 0) {
    Serial.printf("BENCH bssid=%s rssi=%d channel=%d\n", WiFi.BSSIDstr(best).c_str(), WiFi.RSSI(best), WiFi.channel(best));
    WiFi.begin(WIFI_SSID, WIFI_PASS, WiFi.channel(best), WiFi.BSSID(best));
  } else {
    Serial.println("BENCH bssid=none");
    WiFi.begin(WIFI_SSID, WIFI_PASS);
  }
#endif

  while (WiFi.status() != WL_CONNECTED) {
    delay(200);
  }
  Serial.printf("BENCH bssid=%s ip=%s rssi=%d\n", WiFi.BSSIDstr().c_str(), WiFi.localIP().toString().c_str(), WiFi.RSSI());
#endif

  // Pauses in the request parsing phase
  //
  // autocannon -c 32 -w 32 -a 96 -t 30 --renderStatusCodes -m POST -H "Content-Type: application/json" -b '{"foo": "bar"}' http://192.168.4.1/delay
  //
  // curl -v -X POST -H "Content-Type: application/json" -d '{"game": "test"}' http://192.168.4.1/delay
  //
  server.onNotFound([](AsyncWebServerRequest *request) {
    requests = requests + 1;
    if (request->url() == "/delay") {
      request->send(200, "application/json", "{\"status\":\"OK\"}");
    } else {
      request->send(404, "text/plain", "Not found");
    }
  });
  server.onRequestBody([](AsyncWebServerRequest *request, uint8_t *data, size_t len, size_t index, size_t total) {
    if (request->url() == "/delay") {
      delay(3000);
    }
  });

  // HTTP endpoint
  //
  // > autocannon -c 16 -w 16 -d 20 --renderStatusCodes  http://192.168.4.1/
  // > ab -c 16 -t 20 http://192.168.4.1/
  //
  server.on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
    // need to cast to uint8_t*
    // if you do not, the const char* will be copied in a temporary String buffer
    requests = requests + 1;
    request->send(200, "text/html", (uint8_t *)htmlContent, htmlContentLength);
  });

  // IMPORTANT - DO NOT WRITE SUCH CODE IN PRODUCTON !
  //
  // This example simulates the slowdown that can happen when:
  // - downloading a huge file from sdcard
  // - doing some file listing on SDCard because it is horribly slow to get a file listing with file stats on SDCard.
  // So in both cases, ESP would deadlock or TWDT would trigger.
  //
  // This example simulats that by slowing down the chunk callback:
  // - d=2000 is the delay in ms in the callback
  // - l=10000 is the length of the response
  //
  // time curl -N -v -G -d 'd=2000' -d 'l=10000'  http://192.168.4.1/slow.html --output -
  //
  // THIS CODE WILL CRASH BECAUSE OF THE WATCHDOG.
  // IF YOU REALLY NEED TO DO THIS, YOU MUST DISABLE THE TWDT
  //
  // CORRECT WAY IS TO USE SSE OR WEBSOCKETS TO DO THE COSTLY PROCESSING ASYNC.
  //
  server.on("/slow.html", HTTP_GET, [](AsyncWebServerRequest *request) {
    requests = requests + 1;
    uint32_t d = request->getParam("d")->value().toInt();
    uint32_t l = request->getParam("l")->value().toInt();
    Serial.printf("d = %" PRIu32 ", l = %" PRIu32 "\n", d, l);
    AsyncWebServerResponse *response = request->beginChunkedResponse("text/html", [d, l](uint8_t *buffer, size_t maxLen, size_t index) -> size_t {
      Serial.printf("%u\n", index);
      // finished ?
      if (index >= l) {
        return 0;
      }

      // slow down the task to simulate some heavy processing, like SD card reading
      delay(d);

      memset(buffer, characters[charactersIndex], 256);
      charactersIndex = (charactersIndex + 1) % sizeof(characters);
      return 256;
    });

    request->send(response);
  });

  // SSS endpoint
  //
  // launch 16 concurrent workers for 30 seconds
  // > for i in {1..16}; do ( count=$(gtimeout 30 curl -s -N -H "Accept: text/event-stream" http://192.168.4.1/events 2>&1 | grep -c "^data:"); echo "Total: $count events, $(echo "$count / 4" | bc -l) events / second" ) & done;
  //
  // With AsyncTCP, with 16 workers: a lot of "Event message queue overflow: discard message", no crash
  //
  // Total: 1711 events, 427.75 events / second
  // Total: 1711 events, 427.75 events / second
  // Total: 1626 events, 406.50 events / second
  // Total: 1562 events, 390.50 events / second
  // Total: 1706 events, 426.50 events / second
  // Total: 1659 events, 414.75 events / second
  // Total: 1624 events, 406.00 events / second
  // Total: 1706 events, 426.50 events / second
  // Total: 1487 events, 371.75 events / second
  // Total: 1573 events, 393.25 events / second
  // Total: 1569 events, 392.25 events / second
  // Total: 1559 events, 389.75 events / second
  // Total: 1560 events, 390.00 events / second
  // Total: 1562 events, 390.50 events / second
  // Total: 1626 events, 406.50 events / second
  //
  // With AsyncTCP, with 10 workers:
  //
  // Total: 2038 events, 509.50 events / second
  // Total: 2120 events, 530.00 events / second
  // Total: 2119 events, 529.75 events / second
  // Total: 2038 events, 509.50 events / second
  // Total: 2037 events, 509.25 events / second
  // Total: 2119 events, 529.75 events / second
  // Total: 2119 events, 529.75 events / second
  // Total: 2120 events, 530.00 events / second
  // Total: 2038 events, 509.50 events / second
  // Total: 2038 events, 509.50 events / second
  //
  // With AsyncTCPSock, with 16 workers: ESP32 CRASH !!!
  //
  // With AsyncTCPSock, with 10 workers:
  //
  // Total: 1242 events, 310.50 events / second
  // Total: 1242 events, 310.50 events / second
  // Total: 1242 events, 310.50 events / second
  // Total: 1242 events, 310.50 events / second
  // Total: 1181 events, 295.25 events / second
  // Total: 1182 events, 295.50 events / second
  // Total: 1240 events, 310.00 events / second
  // Total: 1181 events, 295.25 events / second
  // Total: 1181 events, 295.25 events / second
  // Total: 1183 events, 295.75 events / second
  //
  server.addHandler(&events);

  // ---- benchmark instrumentation -------------------------------------------------
  // GET /stats           read counters
  // GET /stats?reset=1   read, then zero them, so a load window can be measured exactly
  server.on("/stats", HTTP_GET, [](AsyncWebServerRequest *request) {
    String out;
    out.reserve(768);
    const uint32_t now = millis();
    out += "{\"uptime_ms\":";
    out += now;
    out += ",\"window_ms\":";
    out += (uint32_t)(now - bench_window_start);
    out += ",\"loops\":";
    out += bench_loops;
    out += ",\"sse_generated\":";
    out += (uint32_t)(bench_sse_seq - bench_window_seq);
    out += ",\"sse_seq\":";
    out += bench_sse_seq;
    out += ",\"requests\":";
    out += (uint32_t)requests;
    out += ",\"free_heap\":";
    out += ESP.getFreeHeap();
    out += ",\"min_free_heap\":";
    out += ESP.getMinFreeHeap();
    out += ",\"tasks\":";
    out += bench_task_stats();
    out += "}";
    if (request->hasParam("reset")) {
      bench_reset_window(now);
    }
    request->send(200, "application/json", out);
  });

  server.begin();
}

static uint32_t lastSSE = 0;
static uint32_t deltaSSE = 10;

static uint32_t lastHeap = 0;

void loop() {
  ++bench_loops;
  uint32_t now = millis();
  if (now - lastSSE >= deltaSSE) {
    // "ping-<seq>-<millis>": seq is monotonic per boot, so a reader can tell an event
    // the board never generated from one generated and dropped on the way to it.
    ++bench_sse_seq;
    events.send(String("ping-") + bench_sse_seq + "-" + now, "heartbeat", now);
    lastSSE = millis();
  }

#ifdef ESP32
  if (now - lastHeap >= 2000) {
    Serial.printf(
      "Uptime: %3lu s, requests: %3u, Free heap: %" PRIu32 ", Min free heap: %" PRIu32 "\n", millis() / 1000, requests, ESP.getFreeHeap(), ESP.getMinFreeHeap()
    );
    lastHeap = now;
  }
#endif
}
