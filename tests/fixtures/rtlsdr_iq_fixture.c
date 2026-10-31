/* Test-only USB ABI shim: deterministic synthetic IQ, never accesses hardware.
 * The real upstream rtl_adsb.exe performs preamble detection and demodulation.
 */
#include <stdint.h>
#include <string.h>
#include <windows.h>

#define API __declspec(dllexport)
typedef void (*sample_callback)(unsigned char *, uint32_t, void *);
static unsigned char device;
static uint32_t fixture_rate = 2000000;

API uint32_t rtlsdr_get_device_count(void) { return 1; }
API const char *rtlsdr_get_device_name(uint32_t index) { return "SYNTHETIC IQ TEST FIXTURE"; }
API int rtlsdr_get_device_usb_strings(uint32_t index, char *manufacturer, char *product, char *serial)
{
    if (manufacturer) strcpy(manufacturer, "TEST ONLY");
    if (product) strcpy(product, "SYNTHETIC IQ - NOT A RADIO");
    if (serial) strcpy(serial, "fixture");
    return 0;
}
API int rtlsdr_open(void **dev, uint32_t index) { *dev = &device; return 0; }
API int rtlsdr_close(void *dev) { return 0; }
API int rtlsdr_get_tuner_gains(void *dev, int *gains) { if (gains) gains[0] = 0; return 1; }
API int rtlsdr_set_tuner_gain_mode(void *dev, int mode) { return 0; }
API int rtlsdr_set_tuner_gain(void *dev, int gain) { return 0; }
API int rtlsdr_get_tuner_gain(void *dev) { return 0; }
API int rtlsdr_set_freq_correction(void *dev, int ppm) { return 0; }
API int rtlsdr_set_center_freq(void *dev, uint32_t freq) { return freq == 1090000000 ? 0 : -1; }
API int rtlsdr_set_sample_rate(void *dev, uint32_t rate)
{
    fixture_rate = rate;
    return rate == 2000000 || rate == 2400000 ? 0 : -1;
}
API int rtlsdr_set_agc_mode(void *dev, int mode) { return 0; }
API int rtlsdr_set_bias_tee(void *dev, int enabled) { return enabled ? -1 : 0; }
API int rtlsdr_reset_buffer(void *dev) { return 0; }
API int rtlsdr_cancel_async(void *dev) { return 0; }

static void pulse(unsigned char *iq, int sample)
{
    double left = sample * (fixture_rate / 2000000.0);
    double right = left + fixture_rate / 2000000.0;
    int i;
    for (i = (int)left; i < right; ++i) {
        double start = i > left ? i : left;
        double end = i + 1 < right ? i + 1 : right;
        unsigned char value = (unsigned char)(127 + 93 * (end - start));
        if (value > iq[2 * i]) iq[2 * i] = iq[2 * i + 1] = value;
    }
}

static void encode(unsigned char *iq, int start, const char *hex)
{
    const int preamble[] = {0, 2, 7, 9};
    int i, nibble, bit;
    for (i = 0; i < 4; ++i) pulse(iq, start + preamble[i]);
    for (i = 0; i < 28; ++i) {
        nibble = hex[i] <= '9' ? hex[i] - '0' : hex[i] - 'A' + 10;
        for (bit = 0; bit < 4; ++bit) {
            pulse(iq, start + 16 + (4 * i + bit) * 2 + ((nibble & (8 >> bit)) ? 0 : 1));
        }
    }
}

API int rtlsdr_read_async(void *dev, sample_callback callback, void *ctx, uint32_t buffers, uint32_t length)
{
    unsigned char iq[262144];
    int i;
    char burst[2];
    memset(iq, 127, sizeof(iq));
    encode(iq, 1024, "8D40621D58C382D690C8AC2863A7");
    encode(iq, 4096, "8D40621D58C386435CC412692AD6");
    Sleep(200);
    if (GetEnvironmentVariableA("AIRWSJTX_FIXTURE_BURST", burst, sizeof(burst))) {
        for (i = 0; i < 128; ++i) callback(iq, sizeof(iq), ctx);
        Sleep(1000);
        callback(iq, sizeof(iq), ctx);
        Sleep(1000);
        return 0;
    }
    for (i = 0; i < 4; ++i) {
        callback(iq, sizeof(iq), ctx);
        Sleep(200);
    }
    return 0;
}
