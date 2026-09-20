// What does a reference-count operation actually cost on this part?
//
// Times the ways AsyncTCP could count references, uncontended, and checks whether
// std::atomic is genuinely lock-free here - the ASYNCTCP_ATOMIC_REFCOUNT switch in
// the library keys off ATOMIC_INT_LOCK_FREE, so that assumption needs testing
// rather than trusting.
//
// A macro rather than a template - the .ino preprocessor hoists prototypes and
// mangles templates.  Kept as a .cpp rather than a .ino for a second reason: any
// edit to platformio.ini makes the IDE reindex, which races PlatformIO's
// .ino -> .cpp conversion and fails the build at random.

#include <Arduino.h>
#include <atomic>
#include <memory>
#include <ext/concurrence.h>
#include <bits/gthr.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

struct Payload {
  uint32_t a, b, c, d;
};

static SemaphoreHandle_t mux;
static portMUX_TYPE spin = portMUX_INITIALIZER_UNLOCKED;
static std::atomic<uint32_t> acount{0};
static volatile uint32_t pcount = 0;
static volatile long sink = 0;
static std::shared_ptr<Payload> sp;
static std::weak_ptr<Payload> wp;

#define TIMEIT(what, iters, body)                                                \
  do {                                                                           \
    for (uint32_t i = 0; i < 500; i++) {                                         \
      body;                                                                      \
    } /* warm: lazy init and cache effects land outside the measurement */        \
    const int64_t t0 = esp_timer_get_time();                                     \
    for (uint32_t i = 0; i < (uint32_t)(iters); i++) {                           \
      body;                                                                      \
    }                                                                            \
    const int64_t dt = esp_timer_get_time() - t0;                                \
    Serial.printf("  %-34s %8.1f ns/op\n", what, (double)dt * 1000.0 / (iters)); \
  } while (0)

void setup() {
  Serial.begin(115200);
  delay(2500);
  Serial.printf("\n\nRefcount cost - core %d, %d MHz\n\n", xPortGetCoreID(), (int)getCpuFrequencyMhz());

  mux = xSemaphoreCreateMutex();
  sp = std::make_shared<Payload>();
  wp = sp;

  Serial.println("is std::atomic actually lock-free here?");
  Serial.printf("  ATOMIC_INT_LOCK_FREE (2 = always)   %d\n", (int)ATOMIC_INT_LOCK_FREE);
  Serial.printf("  ATOMIC_SHORT_LOCK_FREE              %d\n", (int)ATOMIC_SHORT_LOCK_FREE);
  // __atomic_always_lock_free is a compile-time builtin; is_lock_free() is a
  // libatomic call that the IDF 5 toolchain does not link.
  Serial.printf("  always_lock_free(4) / (2)           %s / %s\n",
                __atomic_always_lock_free(4, 0) ? "true" : "false",
                __atomic_always_lock_free(2, 0) ? "true" : "false");
  Serial.printf("  __GCC_ATOMIC_INT_LOCK_FREE          %d\n", (int)__GCC_ATOMIC_INT_LOCK_FREE);
#ifdef __GCC_HAVE_SYNC_COMPARE_AND_SWAP_4
  Serial.println("  __GCC_HAVE_SYNC_COMPARE_AND_SWAP_4  defined");
#else
  Serial.println("  __GCC_HAVE_SYNC_COMPARE_AND_SWAP_4  NOT defined");
#endif
  Serial.printf("  __cplusplus                         %ldL\n", (long)__cplusplus);
  // The distinction that matters: _S_mutex with gthreads INACTIVE would make
  // shared_ptr's refcount silently non-atomic rather than merely slow.
  const int pol = (int)__gnu_cxx::__default_lock_policy;
  Serial.printf("  shared_ptr lock policy              %d (%s)\n", pol,
                pol == 0 ? "_S_single - NOT thread safe" : pol == 1 ? "_S_mutex" : "_S_atomic");
  Serial.printf("  __gthread_active_p()                %s\n", __gthread_active_p() ? "true" : "false");
  Serial.printf("  sizeof shared_ptr / weak_ptr        %u / %u bytes\n", (unsigned)sizeof(sp), (unsigned)sizeof(wp));

  Serial.println("\nper-operation cost");
  TIMEIT("plain non-atomic increment", 200000, pcount = pcount + 1);
  TIMEIT("atomic fetch_add relaxed", 200000, acount.fetch_add(1, std::memory_order_relaxed));
  TIMEIT("atomic fetch_sub acq_rel", 200000, acount.fetch_sub(1, std::memory_order_acq_rel));
  TIMEIT("portMUX critical section", 100000, do { portENTER_CRITICAL(&spin); pcount = pcount + 1; portEXIT_CRITICAL(&spin); } while (0));
  TIMEIT("FreeRTOS mutex take+give", 50000, do { xSemaphoreTake(mux, portMAX_DELAY); pcount = pcount + 1; xSemaphoreGive(mux); } while (0));

  Serial.println("\nshared_ptr - one add-ref plus one release per iteration");
  TIMEIT("shared_ptr copy+destroy", 50000, do { auto q = sp; sink = sink + (long)(intptr_t)q.get(); } while (0));
  TIMEIT("weak_ptr lock()+destroy", 50000, do { auto q = wp.lock(); sink = sink + (long)(intptr_t)q.get(); } while (0));

  Serial.println("\nallocation, for the per-connection cost");
  TIMEIT("make_shared + destroy", 20000, do { auto q = std::make_shared<Payload>(); sink = sink + (long)(intptr_t)q.get(); } while (0));
  TIMEIT("new + delete", 20000, do { auto *q = new Payload(); sink = sink + (long)(intptr_t)q; delete q; } while (0));

  Serial.println("\ndone");
}

void loop() {
  delay(1000);
}
