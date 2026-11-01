/* AirWSJT-X receive-only ADS-B helper. SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Uses the pinned dump1090-fa 2.4 MS/s demodulator; see dump1090-fa/UPSTREAM.txt.
 * No bit correction, adaptive gain, Mode A/C, or DF24.
 * DF scoring adapts mode_s.c, GPL-2.0-or-later:
 * Copyright (c) 2014-2016 Oliver Jowett <oliver@mutability.co.uk>
 * Copyright (c) 2021 FlightAware LLC
 * Additional upstream notices are retained in dump1090-fa/UPSTREAM.txt.
 *
 * Built as an executable by default. With AIRWSJTX_LIBRARY defined (Android), it is
 * linked in-process against a librtlsdr that opens an Android USB file descriptor,
 * and frames, IQ reports and messages are delivered through callbacks instead.
 */
#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#else
#include <dlfcn.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <time.h>
#include <unistd.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#define __cdecl
#endif
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "dump1090-fa/dump1090.h"

#define BLOCK 262144
#define QUEUE 32
#define OVERLAP 300
#ifdef AIRWSJTX_LIBRARY
#define API(name, ret, args) ret name args;
API(rtlsdr_open_sys_dev, int, (void **, intptr_t));
typedef void (*airwsjtx_frame_fn)(void *ctx, const char *hex, uint64_t timestamp_12mhz);
typedef void (*airwsjtx_text_fn)(void *ctx, const char *text);
static airwsjtx_frame_fn frame_cb;
static airwsjtx_text_fn report_cb, log_cb;
static void *cb_ctx;
static void LOG(const char *format, ...)
{
    char text[512];
    va_list args;
    va_start(args, format);
    vsnprintf(text, sizeof(text), format, args);
    va_end(args);
    if (log_cb) log_cb(cb_ctx, text);
}
#else
#define API(name, ret, args) static ret (__cdecl *name) args
#define LOG(...) (fprintf(stderr, __VA_ARGS__), fflush(stderr))
#endif
API(rtlsdr_open, int, (void **, uint32_t));
API(rtlsdr_close, int, (void *));
API(rtlsdr_set_tuner_gain_mode, int, (void *, int));
API(rtlsdr_set_tuner_gain, int, (void *, int));
API(rtlsdr_get_tuner_gain, int, (void *));
API(rtlsdr_set_agc_mode, int, (void *, int));
API(rtlsdr_set_center_freq, int, (void *, uint32_t));
API(rtlsdr_set_sample_rate, int, (void *, uint32_t));
API(rtlsdr_set_bias_tee, int, (void *, int));
API(rtlsdr_reset_buffer, int, (void *));
API(rtlsdr_cancel_async, int, (void *));
typedef void (__cdecl *sample_callback)(unsigned char *, uint32_t, void *);
API(rtlsdr_read_async, int, (void *, sample_callback, void *, uint32_t, uint32_t));

#ifdef _WIN32
typedef volatile LONG flag_t;
#define FLAG_GET(flag) InterlockedCompareExchange(&(flag), 0, 0)
#define FLAG_SET(flag) InterlockedExchange(&(flag), 1)
typedef CRITICAL_SECTION lock_t;
#define LOCK_INIT(l) InitializeCriticalSection(&(l))
#define LOCK(l) EnterCriticalSection(&(l))
#define UNLOCK(l) LeaveCriticalSection(&(l))
#define LOCK_FREE(l) DeleteCriticalSection(&(l))
typedef HANDLE signal_t;
static int signal_init(signal_t *s) { *s = CreateSemaphoreW(NULL, 0, QUEUE + 1, NULL); return *s != NULL; }
static void signal_post(signal_t *s) { ReleaseSemaphore(*s, 1, NULL); }
static void signal_wait(signal_t *s, unsigned ms) { WaitForSingleObject(*s, ms); }
static void signal_free(signal_t *s) { CloseHandle(*s); }
#else
typedef volatile sig_atomic_t flag_t;
#define FLAG_GET(flag) __atomic_load_n(&(flag), __ATOMIC_SEQ_CST)
#define FLAG_SET(flag) __atomic_store_n(&(flag), 1, __ATOMIC_SEQ_CST)
typedef pthread_mutex_t lock_t;
#define LOCK_INIT(l) pthread_mutex_init(&(l), NULL)
#define LOCK(l) pthread_mutex_lock(&(l))
#define UNLOCK(l) pthread_mutex_unlock(&(l))
#define LOCK_FREE(l) pthread_mutex_destroy(&(l))
/* Counting semaphore; macOS does not implement unnamed POSIX semaphores. */
typedef struct { pthread_mutex_t lock; pthread_cond_t cond; unsigned value; } signal_t;
static int signal_init(signal_t *s)
{
    s->value = 0;
    return !pthread_mutex_init(&s->lock, NULL) && !pthread_cond_init(&s->cond, NULL);
}
static void signal_post(signal_t *s)
{
    pthread_mutex_lock(&s->lock);
    if (s->value <= QUEUE) ++s->value;
    pthread_cond_signal(&s->cond);
    pthread_mutex_unlock(&s->lock);
}
static void signal_wait(signal_t *s, unsigned ms)
{
    struct timespec deadline;
    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += ms / 1000;
    deadline.tv_nsec += (long)(ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) { ++deadline.tv_sec; deadline.tv_nsec -= 1000000000L; }
    pthread_mutex_lock(&s->lock);
    while (!s->value)
        if (pthread_cond_timedwait(&s->cond, &s->lock, &deadline)) break;
    if (s->value) --s->value;
    pthread_mutex_unlock(&s->lock);
}
static void signal_free(signal_t *s) { pthread_cond_destroy(&s->cond); pthread_mutex_destroy(&s->lock); }
#endif

static void *device;
static flag_t exiting, reader_done;
static int reader_code;
static lock_t queue_lock;
static signal_t ready;
static unsigned char queue[QUEUE][BLOCK];
static unsigned lengths[QUEUE], head, count;
static uint64_t sequences[QUEUE], offsets[QUEUE], sequence, captured_samples, dropped;
static unsigned rate = 2400000;
static uint64_t samples, clipped, valid, accepted_other, failed_df17_trials, gaps;
static uint64_t block_start, input_end;
static double total_power, valid_signal_sum;
static uint16_t magnitudes[BLOCK / 2 + OVERLAP * 2];
static unsigned tail_length;
static double wall_start;
struct receiver_modes Modes;

static double clock_seconds(void)
{
#ifdef _WIN32
    LARGE_INTEGER tick, frequency;
    QueryPerformanceCounter(&tick);
    QueryPerformanceFrequency(&frequency);
    return (double)tick.QuadPart / frequency.QuadPart;
#else
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec + now.tv_nsec / 1e9;
#endif
}

static uint32_t syndrome(const unsigned char *msg, unsigned bits)
{
    uint32_t crc = 0;
    for (unsigned i = 0; i < bits; ++i) {
        unsigned top = crc >> 23;
        crc = ((crc << 1) | ((msg[i / 8] >> (7 - i % 8)) & 1)) & 0xFFFFFF;
        if (top) crc ^= 0xFFF409;
    }
    return crc;
}

uint64_t mstime(void)
{
    return block_start * 1000 / rate;
}

int modesMessageLenByType(int type)
{
    return (type & 16) ? 112 : 56;
}

score_rank scoreModesMessage(const unsigned char *msg)
{
    static const unsigned char zeros[7] = {0};
    if (!memcmp(msg, zeros, sizeof(zeros))) return SR_ALL_ZEROS;
    unsigned df = msg[0] >> 3;
    uint32_t crc = syndrome(msg, (unsigned)modesMessageLenByType((int)df));
    uint32_t addr = ((uint32_t)msg[1] << 16) | ((uint32_t)msg[2] << 8) | msg[3];
    switch (df) {
    case 0: case 4: case 5: case 16: case 20: case 21:
        return icaoFilterTest(crc) ? SR_UNRELIABLE_KNOWN : SR_UNRELIABLE_UNKNOWN;
    case 11:
        if (crc & 0xFFFF80) return SR_UNCORRECTABLE;
        if (crc & 0x7F)
            return icaoFilterTest(addr) ? SR_DF11_IID_KNOWN : SR_DF11_IID_UNKNOWN;
        return icaoFilterTest(addr) ? SR_DF11_ACQ_KNOWN : SR_DF11_ACQ_UNKNOWN;
    case 17:
        if (crc) { ++failed_df17_trials; return SR_UNCORRECTABLE; }
        return icaoFilterTest(addr) ? SR_DF17_KNOWN : SR_DF17_UNKNOWN;
    case 18:
        if (crc) return SR_UNCORRECTABLE;
        return icaoFilterTest(addr | ICAO_FILTER_ADSB_NT) ? SR_DF18_KNOWN : SR_DF18_UNKNOWN;
    default:
        return SR_UNKNOWN_DF;
    }
}

int decodeModesMessage(struct modesMessage *mm, const unsigned char *msg)
{
    if (mm->score < SR_ACCEPT_THRESHOLD) return -1;
    memcpy(mm->msg, msg, sizeof(mm->msg));
    return 0;
}

void useModesMessage(struct modesMessage *mm)
{
    unsigned df = mm->msg[0] >> 3;
    unsigned bits = (unsigned)modesMessageLenByType((int)df);
    uint64_t end_tick = mm->timestampMsg + (bits - 56) * 12;
    if (end_tick > input_end * 5) return; /* No packets fabricated from EOF padding. */
    uint32_t addr = ((uint32_t)mm->msg[1] << 16) | ((uint32_t)mm->msg[2] << 8) | mm->msg[3];
    if (df == 17 || (df == 11 && syndrome(mm->msg, 56) == 0))
        icaoFilterAdd(addr);
    else if (df == 18)
        icaoFilterAdd(addr | ICAO_FILTER_ADSB_NT);
    if (df != 17) { ++accepted_other; return; }
    ++valid;
    valid_signal_sum += mm->signalLevel;
    char hex[29];
    static const char digits[] = "0123456789abcdef";
    for (unsigned i = 0; i < 14; ++i) {
        hex[i * 2] = digits[mm->msg[i] >> 4];
        hex[i * 2 + 1] = digits[mm->msg[i] & 15];
    }
    hex[28] = 0;
#ifdef AIRWSJTX_LIBRARY
    if (frame_cb) frame_cb(cb_ctx, hex, mm->timestampMsg);
#else
    printf("*%s;\n", hex);
    fprintf(stderr, "AIRWSJTX_FRAME {\"frame\":\"%s\",\"df\":17,\"crc_syndrome\":0,"
        "\"timestamp_12mhz\":%llu,\"sample_rate\":%u}\n",
        hex, (unsigned long long)mm->timestampMsg, rate);
    fflush(stderr);
#endif
}

static void report(void)
{
    double elapsed = clock_seconds() - wall_start;
    double power = samples ? total_power / samples : 0;
    double signal = valid ? valid_signal_sum / valid : 0;
    char text[1024], level[32];
    if (valid) snprintf(level, sizeof(level), "%.3f", 10 * log10(signal));
    else strcpy(level, "null");
    LOCK(queue_lock);
    uint64_t lost = dropped;
    UNLOCK(queue_lock);
    snprintf(text, sizeof(text),
        "{\"samples\":%llu,\"sample_rate\":%u,\"elapsed_s\":%.3f,"
        "\"clipped_components\":%llu,\"clipped_fraction\":%.9f,"
        "\"mean_power_dbfs\":%.3f,\"signal_power_dbfs\":%s,\"candidate_quiet_magnitude\":null"
        ",\"dropped_buffers\":%llu,\"discontinuities\":%llu,"
        "\"usb_overruns\":null,\"native_valid_df17\":%llu,"
        "\"preamble_candidates\":%llu,\"df17_crc_rejected_phase_trials\":%llu,"
        "\"accepted_other_modes\":%llu,\"rejected_bad\":%llu,\"rejected_unknown_icao\":%llu}",
        (unsigned long long)samples, rate, elapsed,
        (unsigned long long)clipped, samples ? clipped / (2.0 * samples) : 0,
        power > 0 ? 10 * log10(power / 32512.5) : -120, level,
        (unsigned long long)lost, (unsigned long long)gaps, (unsigned long long)valid,
        (unsigned long long)Modes.stats_current.demod_preambles,
        (unsigned long long)failed_df17_trials, (unsigned long long)accepted_other,
        (unsigned long long)Modes.stats_current.demod_rejected_bad,
        (unsigned long long)Modes.stats_current.demod_rejected_unknown_icao);
#ifdef AIRWSJTX_LIBRARY
    if (report_cb) report_cb(cb_ctx, text);
#else
    fprintf(stderr, "AIRWSJTX_IQ %s\n", text);
    fflush(stderr);
#endif
}

static void demodulate_buffer(unsigned available, int discontinuous)
{
    if (available < OVERLAP) { tail_length = available; return; }
    struct mag_buf mag = {0};
    mag.data = magnitudes;
    mag.validLength = available;
    mag.overlap = OVERLAP;
    mag.flags = discontinuous ? MAGBUF_DISCONTINUOUS : 0;
    mag.sampleTimestamp = block_start * 5;
    mag.sysTimestamp = mstime();
    double power = 0;
    for (unsigned i = 0; i < available - OVERLAP; ++i)
        power += (double)magnitudes[i] * magnitudes[i];
    if (available > OVERLAP)
        mag.mean_power = power / (available - OVERLAP) / 65535.0 / 65535.0;
    icaoFilterExpire();
    demodulate2400(&mag);
    tail_length = OVERLAP;
    memmove(magnitudes, magnitudes + available - OVERLAP, OVERLAP * sizeof(uint16_t));
#ifndef AIRWSJTX_LIBRARY
    fflush(stdout);
#endif
}

static void process(const unsigned char *raw, unsigned length, int discontinuous, uint64_t start_sample)
{
    if (discontinuous) { tail_length = 0; ++gaps; }
    unsigned old_tail = tail_length;
    block_start = start_sample - old_tail;
    unsigned n = length / 2;
    double block_power = 0;
    for (unsigned i = 0; i < n; ++i) {
        double inphase = raw[2 * i] - 127.5, quadrature = raw[2 * i + 1] - 127.5;
        clipped += raw[2 * i] == 0 || raw[2 * i] == 255;
        clipped += raw[2 * i + 1] == 0 || raw[2 * i + 1] == 255;
        double power = inphase * inphase + quadrature * quadrature;
        block_power += power;
        magnitudes[old_tail + i] = (uint16_t)lround(sqrt(power / 32512.5) * 65535);
    }
    samples += n;
    total_power += block_power;
    input_end = start_sample + n;
    demodulate_buffer(old_tail + n, discontinuous);
}

static void flush_tail(void)
{
    if (!tail_length) return;
    block_start = input_end - tail_length;
    unsigned available = tail_length + OVERLAP;
    memset(magnitudes + tail_length, 0, OVERLAP * sizeof(uint16_t));
    demodulate_buffer(available, 0);
    tail_length = 0;
}

static void __cdecl received(unsigned char *raw, uint32_t length, void *ctx)
{
    (void)ctx;
    if (FLAG_GET(exiting)) { rtlsdr_cancel_async(device); return; }
    LOCK(queue_lock);
    uint64_t current = sequence++;
    uint64_t offset = captured_samples;
    captured_samples += length / 2;
    if (length > BLOCK || (length & 1) || count == QUEUE) {
        ++dropped;
    } else {
        unsigned slot = (head + count) % QUEUE;
        memcpy(queue[slot], raw, length);
        lengths[slot] = length; sequences[slot] = current; offsets[slot] = offset; ++count;
        signal_post(&ready);
    }
    UNLOCK(queue_lock);
}

static void read_samples(void)
{
    reader_code = rtlsdr_read_async(device, received, NULL, 12, BLOCK);
    FLAG_SET(reader_done);
    signal_post(&ready);
}

#ifdef _WIN32
static DWORD WINAPI reader(void *unused) { (void)unused; read_samples(); return 0; }

static BOOL WINAPI interrupted(DWORD signal)
{
    (void)signal;
    FLAG_SET(exiting);
    return TRUE;
}
#else
static void *reader(void *unused) { (void)unused; read_samples(); return NULL; }
#endif

#if !defined(_WIN32) && !defined(AIRWSJTX_LIBRARY)
static void interrupted(int number)
{
    (void)number;
    FLAG_SET(exiting);
}

/* Load only the librtlsdr shipped beside this executable; never search other paths. */
static void *load_bundled_library(const char *name)
{
    char path[PATH_MAX], resolved[PATH_MAX];
#ifdef __APPLE__
    uint32_t size = sizeof(path);
    if (_NSGetExecutablePath(path, &size)) return NULL;
#else
    ssize_t size = readlink("/proc/self/exe", path, sizeof(path) - 1);
    if (size < 0) return NULL;
    path[size] = 0;
#endif
    if (!realpath(path, resolved)) return NULL;
    char *slash = strrchr(resolved, '/');
    if (!slash || (size_t)(slash - resolved) + 1 + strlen(name) >= sizeof(resolved)) return NULL;
    strcpy(slash + 1, name);
    return dlopen(resolved, RTLD_NOW | RTLD_LOCAL);
}
#endif

#define STREAM_STUCK 2
/* Configures the open device and demodulates until stopped, the duration ends or USB fails.
 * Returns 0, 1 on failure, or STREAM_STUCK when the USB reader could not be stopped. */
static int stream_device(int manual, int gain, double duration, unsigned char *raw)
{
#define CHECK(call) do { int code = (call); if (code < 0) { \
    LOG("Failed " #call " (%d)\n", code); return 1; } } while (0)
    CHECK(rtlsdr_set_tuner_gain_mode(device, manual));
    if (manual) {
        CHECK(rtlsdr_set_tuner_gain(device, gain));
        int actual = rtlsdr_get_tuner_gain(device);
        if (actual != gain) { LOG("Failed gain readback (%d != %d)\n", actual, gain); return 1; }
        LOG("Tuner gain set to %.2f dB.\n", actual / 10.0);
    } else LOG("Tuner gain set to automatic.\n");
    CHECK(rtlsdr_set_agc_mode(device, 1));
    CHECK(rtlsdr_set_center_freq(device, 1090000000));
    CHECK(rtlsdr_set_sample_rate(device, rate));
    CHECK(rtlsdr_set_bias_tee(device, 0));
    CHECK(rtlsdr_reset_buffer(device));
#undef CHECK
    LOG("dump1090-fa 465e063 Mode S: 1090000000 Hz, %u S/s; no correction; digital AGC on; bias tee off.\n", rate);
#ifdef _WIN32
    SetConsoleCtrlHandler(interrupted, TRUE);
    HANDLE worker = CreateThread(NULL, 0, reader, NULL, 0, NULL);
    if (!worker) { LOG("Failed creating USB reader\n"); return 1; }
#else
#ifndef AIRWSJTX_LIBRARY
    struct sigaction action = {0};
    action.sa_handler = interrupted;
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGHUP, &action, NULL);
    /* A dead bridge must not leave the receiver holding USB: stop when reparented. */
    pid_t owner = getppid();
#endif
    pthread_t worker;
    if (pthread_create(&worker, NULL, reader, NULL)) { LOG("Failed creating USB reader\n"); return 1; }
#endif
    int result = 0;
    uint64_t expected = 0;
    double next_report = clock_seconds() + 1;
    while (!FLAG_GET(exiting)) {
        if (duration && clock_seconds() - wall_start >= duration) break;
#if !defined(_WIN32) && !defined(AIRWSJTX_LIBRARY)
        if (getppid() != owner) { LOG("Owning bridge exited; stopping receiver.\n"); break; }
#endif
        signal_wait(&ready, 100);
        unsigned length = 0;
        uint64_t current = expected, offset = 0;
        LOCK(queue_lock);
        if (count) {
            length = lengths[head]; current = sequences[head]; offset = offsets[head];
            memcpy(raw, queue[head], length);
            head = (head + 1) % QUEUE; --count;
        }
        int empty = count == 0;
        UNLOCK(queue_lock);
        if (length) { process(raw, length, current != expected, offset); expected = current + 1; }
        if (clock_seconds() >= next_report) { report(); next_report = clock_seconds() + 1; }
        if (FLAG_GET(reader_done) && empty) break;
    }
    FLAG_SET(exiting);
    rtlsdr_cancel_async(device);
#ifdef _WIN32
    if (WaitForSingleObject(worker, 5000) != WAIT_OBJECT_0) {
        LOG("Failed to stop USB reader\n");
        /* Process exit releases USB; never unload a DLL executing in a worker. */
        ExitProcess(1);
    }
    CloseHandle(worker);
#else
    double stop_deadline = clock_seconds() + 5;
    while (!FLAG_GET(reader_done) && clock_seconds() < stop_deadline) usleep(10000);
    if (!FLAG_GET(reader_done)) {
        LOG("Failed to stop USB reader\n");
#ifdef AIRWSJTX_LIBRARY
        /* In-process: leave the device open rather than free it under a live reader. */
        pthread_detach(worker);
        return STREAM_STUCK;
#else
        /* Process exit releases USB; never unload a library executing in a worker. */
        _exit(1);
#endif
    }
    pthread_join(worker, NULL);
#endif
    if (reader_code < 0) { LOG("USB read failed (%d)\n", reader_code); result = 1; }
    return result;
}

#ifdef AIRWSJTX_LIBRARY
static flag_t running;

/* Requests a running airwsjtx_adsb_run() to stop; safe from any thread. */
void airwsjtx_adsb_stop(void) { FLAG_SET(exiting); }

/* Receives ADS-B from the RTL-SDR behind an Android USB file descriptor until
 * airwsjtx_adsb_stop(). The caller keeps ownership of fd. One receiver at a time. */
int airwsjtx_adsb_run(intptr_t fd, int manual, double gain_db,
                      airwsjtx_frame_fn on_frame, airwsjtx_text_fn on_report,
                      airwsjtx_text_fn on_log, void *ctx)
{
    if (__atomic_exchange_n(&running, 1, __ATOMIC_SEQ_CST)) return -1;
    frame_cb = on_frame; report_cb = on_report; log_cb = on_log; cb_ctx = ctx;
    device = NULL;
    __atomic_store_n(&exiting, 0, __ATOMIC_SEQ_CST);
    __atomic_store_n(&reader_done, 0, __ATOMIC_SEQ_CST);
    reader_code = 0; head = count = 0; sequence = captured_samples = dropped = 0;
    samples = clipped = valid = accepted_other = failed_df17_trials = gaps = 0;
    block_start = input_end = 0; total_power = valid_signal_sum = 0; tail_length = 0;
    memset(&Modes, 0, sizeof(Modes));
    int gain = (int)lround(gain_db * 10);
    LOCK_INIT(queue_lock);
    int result = 1;
    unsigned char *raw = malloc(BLOCK);
    if (!raw) { LOG("Failed to allocate IQ buffer\n"); goto done; }
    if (!signal_init(&ready)) { LOG("Failed to create receive semaphore\n"); free(raw); goto done; }
    wall_start = clock_seconds();
    icaoFilterInit();
    int code = rtlsdr_open_sys_dev(&device, fd);
    if (code < 0) { LOG("Failed rtlsdr_open_sys_dev (%d)\n", code); device = NULL; }
    else {
        result = stream_device(manual, gain, 0, raw);
        if (result == STREAM_STUCK) goto stuck;
        rtlsdr_close(device);
        device = NULL;
    }
    flush_tail();
    report();
    signal_free(&ready);
    free(raw);
done:
    LOCK_FREE(queue_lock);
    frame_cb = NULL; report_cb = NULL; log_cb = NULL; cb_ctx = NULL;
    __atomic_store_n(&running, 0, __ATOMIC_SEQ_CST);
    return result;
stuck:
    /* The reader thread still uses these; keep them alive and refuse further runs. */
    frame_cb = NULL; report_cb = NULL; log_cb = NULL;
    return result;
}
#else
int main(int argc, char **argv)
{
    const char *input = NULL;
    int index = 0, manual = 0, gain = 0;
    double duration = 0;
    for (int i = 1; i < argc; ++i) {
        if (i + 1 >= argc) { fprintf(stderr, "Missing option value\n"); return 2; }
        if (!strcmp(argv[i], "-d")) index = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-g")) { manual = 1; gain = (int)lround(atof(argv[++i]) * 10); }
        else if (!strcmp(argv[i], "--input")) input = argv[++i];
        else if (!strcmp(argv[i], "--rate")) rate = (unsigned)atoi(argv[++i]);
        else if (!strcmp(argv[i], "--seconds")) duration = atof(argv[++i]);
        else { fprintf(stderr, "Unknown option: %s\n", argv[i]); return 2; }
    }
    if (rate != 2400000 || index < 0 || duration < 0) {
        fprintf(stderr, "dump1090-fa demodulation requires 2400000 S/s and a valid index/duration\n"); return 2;
    }
    LOCK_INIT(queue_lock);
    if (!signal_init(&ready)) { fprintf(stderr, "Failed to create receive semaphore\n"); return 1; }
    if (setvbuf(stdout, NULL, _IOFBF, 65536)) {
        fprintf(stderr, "Failed to configure frame output buffering\n"); return 1;
    }
    wall_start = clock_seconds();
    icaoFilterInit();
    unsigned char *raw = malloc(BLOCK);
    if (!raw) { fprintf(stderr, "Failed to allocate IQ buffer\n"); return 1; }
    int result = 0;
    if (input) {
        FILE *file = fopen(input, "rb");
        if (!file) { fprintf(stderr, "Failed to open IQ input\n"); result = 1; }
        else {
            size_t length;
            while ((length = fread(raw, 1, BLOCK, file)) != 0) {
                if (length & 1) { fprintf(stderr, "Invalid odd IQ byte count\n"); result = 1; break; }
                uint64_t offset = input_end;
                process(raw, (unsigned)length, 0, offset);
            }
            if (ferror(file)) { fprintf(stderr, "Failed reading IQ input\n"); result = 1; }
            fclose(file);
        }
    } else {
#ifdef _WIN32
        HMODULE library = LoadLibraryExW(L"rtlsdr.dll", NULL,
                                         LOAD_LIBRARY_SEARCH_APPLICATION_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
        if (!library) { fprintf(stderr, "Failed loading bundled rtlsdr.dll (%lu)\n", GetLastError()); return 1; }
#define LOAD(name) do { *(FARPROC *)&name = GetProcAddress(library, #name); \
    if (!name) { fprintf(stderr, "Missing RTL-SDR API: " #name "\n"); return 1; } } while (0)
#else
#ifdef __APPLE__
        void *library = load_bundled_library("librtlsdr.dylib");
#else
        void *library = load_bundled_library("librtlsdr.so");
#endif
        if (!library) {
            const char *reason = dlerror();
            fprintf(stderr, "Failed loading bundled librtlsdr (%s)\n", reason ? reason : "executable path unavailable");
            return 1;
        }
#define LOAD(name) do { *(void **)&name = dlsym(library, #name); \
    if (!name) { fprintf(stderr, "Missing RTL-SDR API: " #name "\n"); return 1; } } while (0)
#endif
        LOAD(rtlsdr_open); LOAD(rtlsdr_close); LOAD(rtlsdr_set_tuner_gain_mode);
        LOAD(rtlsdr_set_tuner_gain); LOAD(rtlsdr_get_tuner_gain); LOAD(rtlsdr_set_agc_mode);
        LOAD(rtlsdr_set_center_freq); LOAD(rtlsdr_set_sample_rate); LOAD(rtlsdr_set_bias_tee);
        LOAD(rtlsdr_reset_buffer); LOAD(rtlsdr_cancel_async); LOAD(rtlsdr_read_async);
        int code = rtlsdr_open(&device, (uint32_t)index);
        if (code < 0) { fprintf(stderr, "Failed rtlsdr_open(&device, (uint32_t)index) (%d)\n", code); result = 1; device = NULL; }
        else result = stream_device(manual, gain, duration, raw);
        if (device) rtlsdr_close(device);
#ifdef _WIN32
        FreeLibrary(library);
#else
        dlclose(library);
#endif
    }
    flush_tail();
    report();
    fflush(stdout);
    free(raw);
    signal_free(&ready);
    LOCK_FREE(queue_lock);
    return result;
}
#endif
