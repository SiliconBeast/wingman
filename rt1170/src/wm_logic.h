/*
 * Wingman supervisor core: frame parsing, threat selection, state machine.
 *
 * Pure C, no Zephyr dependencies, so it is unit-tested on the host
 * (rt1170/tests/host). The Zephyr glue in main.c owns one struct wm_sup and
 * calls it from the decide thread only.
 */
#ifndef WM_LOGIC_H
#define WM_LOGIC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "wingman_proto.h"

struct wm_thresholds {
	int32_t caution_enter_ms;
	int32_t caution_exit_ms;
	int32_t warn_enter_ms;
	int32_t warn_exit_ms;
	int32_t close_range_mm;        /* in-path + closing + nearer than this -> WARNING */
	int32_t inpath_lat_mm;         /* |lat| below this is in our path */
	int32_t closing_deadband_mm_s; /* closing speed must exceed this to be a threat */
	uint8_t min_conf;              /* 0..255 */
	uint32_t heartbeat_timeout_ms;
	uint32_t bad_frames_to_failsafe;
	uint32_t good_frames_to_recover;
};

void wm_thresholds_default(struct wm_thresholds *th);

enum wm_parse_result {
	WM_PARSE_OK = 0,
	WM_PARSE_BAD_LEN,
	WM_PARSE_BAD_CRC,
	WM_PARSE_BAD_FORMAT, /* magic / version / n_tracks; reported as WM_FAULT_BAD_LEN */
};

/* Validates length, CRC, magic, version, n_tracks and copies into *out. */
enum wm_parse_result wm_parse_perception(const void *buf, size_t len, wm_perception_t *out);

struct wm_threat {
	int32_t ttc_ms;   /* WM_TTC_NONE if no threat */
	uint16_t id;      /* WM_THREAT_NONE if no threat */
	int32_t dist_mm;  /* distance of the min-TTC track */
	bool close_range; /* some in-path closing track is within close_range_mm */
};

void wm_find_threat(const struct wm_thresholds *th, const wm_perception_t *f,
		    struct wm_threat *out);

struct wm_counters {
	uint32_t frames_ok;
	uint32_t crc_fail;
	uint32_t bad_len;
	uint32_t seq_gap;
	uint32_t seq_old;
	uint32_t heartbeat_loss;
	uint32_t failsafe_entries;
	uint32_t braking_suppressed; /* frames where WARNING was downgraded to CAUTION */
};

struct wm_sup {
	struct wm_thresholds th;
	enum wm_state state;

	bool have_seq;
	uint32_t last_seq;
	uint32_t consec_bad;
	uint32_t consec_good;
	bool hb_lost;
	bool driver_braking;

	uint16_t faults;        /* transient, accumulated until wm_sup_take_faults() */
	uint16_t sticky_faults; /* e.g. WM_FAULT_WDT_RESET, cleared by wm_sup_reset() */

	struct wm_threat threat;
	struct wm_counters cnt;
};

/* th == NULL -> defaults. State starts at WM_BOOT. */
void wm_sup_init(struct wm_sup *s, const struct wm_thresholds *th);

/* Clears counters, faults (incl. sticky) and sequence tracking; back to WAIT_LINK. Keeps thresholds. */
void wm_sup_reset(struct wm_sup *s);

/* Network is up: BOOT -> WAIT_LINK. */
void wm_sup_link_ready(struct wm_sup *s);

/* A parsed, CRC-valid frame. Returns false if it was discarded (duplicate / out of order). */
bool wm_sup_on_frame(struct wm_sup *s, const wm_perception_t *f);

/* A frame that failed wm_parse_perception(). */
void wm_sup_on_bad(struct wm_sup *s, enum wm_parse_result why);

/* No valid frame for heartbeat_timeout_ms. */
void wm_sup_on_heartbeat_lost(struct wm_sup *s);

/* IMU context: driver already braking hard -> WARNING is downgraded to CAUTION. */
void wm_sup_set_braking(struct wm_sup *s, bool braking);

/* Current fault word for a status packet; clears the transient bits. */
uint16_t wm_sup_take_faults(struct wm_sup *s);

const char *wm_state_name(enum wm_state st);

#endif /* WM_LOGIC_H */
