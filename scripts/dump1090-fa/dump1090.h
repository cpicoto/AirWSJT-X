/* AirWSJT-X adapter for the pinned dump1090-fa Mode S demodulator.
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Score ordering is derived from FlightAware mode_s.h (GPL-2.0-or-later):
 * Copyright (c) 2017-2021 FlightAware, LLC
 * Copyright (c) 2017 Oliver Jowett <oliver@mutability.co.uk>
 * See COPYING and UPSTREAM.txt. Error correction and adaptive gain are disabled.
 */
#ifndef AIRWSJTX_DUMP1090_ADAPTER_H
#define AIRWSJTX_DUMP1090_ADAPTER_H
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define MODES_LONG_MSG_BYTES 14
#define MODES_SHORT_MSG_BYTES 7
#define MAGBUF_DISCONTINUOUS 1
#define ICAO_FILTER_ADSB_NT (1U << 25)

typedef enum {
    SR_NOT_SET, SR_ALL_ZEROS, SR_UNKNOWN_DF, SR_UNCORRECTABLE,
    SR_UNKNOWN_THRESHOLD, SR_UNRELIABLE_UNKNOWN,
    SR_DF11_IID_1ERROR_UNKNOWN, SR_DF11_ACQ_1ERROR_UNKNOWN, SR_DF11_IID_UNKNOWN,
    SR_DF18_2ERROR_UNKNOWN, SR_DF17_2ERROR_UNKNOWN, SR_ACCEPT_THRESHOLD,
    SR_UNRELIABLE_KNOWN, SR_DF18_2ERROR_KNOWN, SR_DF17_2ERROR_KNOWN,
    SR_DF18_1ERROR_UNKNOWN, SR_DF17_1ERROR_UNKNOWN, SR_DF11_ACQ_UNKNOWN,
    SR_DF11_IID_1ERROR_KNOWN, SR_DF11_ACQ_1ERROR_KNOWN, SR_DF11_IID_KNOWN,
    SR_DF18_1ERROR_KNOWN, SR_DF17_1ERROR_KNOWN, SR_DF11_ACQ_KNOWN,
    SR_DF18_UNKNOWN, SR_DF17_UNKNOWN, SR_DF18_KNOWN, SR_DF17_KNOWN
} score_rank;

struct modesMessage {
    uint8_t msg[14];
    uint64_t timestampMsg, sysTimestampMsg;
    score_rank score;
    unsigned correctedbits;
    double signalLevel;
};

struct mag_buf {
    uint16_t *data;
    uint32_t validLength, overlap, flags;
    uint64_t sampleTimestamp, sysTimestamp;
    double mean_power;
};

struct receiver_modes {
    unsigned enable_df24, fix_df, nfix_crc;
    struct {
        uint64_t demod_preambles, demod_rejected_bad, demod_rejected_unknown_icao;
        uint64_t demod_accepted[3], signal_power_count, noise_power_count, strong_signal_count;
        double signal_power_sum, noise_power_sum, peak_signal_power;
    } stats_current;
};

extern struct receiver_modes Modes;
int modesMessageLenByType(int type);
score_rank scoreModesMessage(const unsigned char *msg);
int decodeModesMessage(struct modesMessage *mm, const unsigned char *msg);
void useModesMessage(struct modesMessage *mm);
void demodulate2400(struct mag_buf *mag);
void icaoFilterInit(void);
void icaoFilterAdd(uint32_t addr);
int icaoFilterTest(uint32_t addr);
void icaoFilterExpire(void);
uint64_t mstime(void);

static inline uint64_t receiveclock_ms_elapsed(uint64_t start, uint64_t end)
{
    return (end - start) / 12000;
}

static inline void adaptive_update(const uint16_t *samples, unsigned count,
                                   const struct modesMessage *message)
{
    /* Gain is explicitly controlled by the app; do not run upstream adaptation. */
    (void)samples; (void)count; (void)message;
}
#endif
