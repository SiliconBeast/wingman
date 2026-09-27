/*
 * Wingman wire protocol: TX2 perception node <-> RT1170 safety supervisor.
 *
 * Single source of truth. Mirrored in tx2/wingman_proto.py; both sides are
 * unit-tested against the same CRC vector (b"123456789" -> 0x29B1).
 *
 * All fields little-endian, structs packed, no padding.
 *   perception  TX2  -> RT1170  UDP :5005   146 bytes, always full size
 *   status      RT1170 -> TX2   UDP :5006    36 bytes
 * CRC: CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF, no reflection, no xorout)
 *      over every byte before the crc16 field.
 */
#ifndef WINGMAN_PROTO_H
#define WINGMAN_PROTO_H

#include <stddef.h>
#include <stdint.h>

#define WM_MAGIC_PERCEPTION 0x574D /* "WM" */
#define WM_MAGIC_STATUS     0x5753 /* "WS" */
#define WM_VERSION          1
#define WM_MAX_TRACKS       8

#define WM_PORT_PERCEPTION 5005
#define WM_PORT_STATUS     5006

enum wm_class {
	WM_CLS_UNKNOWN = 0,
	WM_CLS_PERSON = 1,
	WM_CLS_CAR = 2,
	WM_CLS_BIKE = 3,
	WM_CLS_TRUCK = 4,
};

enum wm_state {
	WM_BOOT = 0,
	WM_WAIT_LINK = 1,
	WM_NOMINAL = 2,
	WM_CAUTION = 3,
	WM_WARNING = 4,
	WM_FAILSAFE = 5,
};

enum wm_maneuver {
	WM_MAN_UNKNOWN = 0,
	WM_MAN_STEADY = 1,
	WM_MAN_BRAKE = 2,
	WM_MAN_ACCEL = 3,
	WM_MAN_SWERVE = 4,
};

/* fault_flags bits */
#define WM_FAULT_HEARTBEAT (1u << 0)
#define WM_FAULT_CRC       (1u << 1)
#define WM_FAULT_SEQ_GAP   (1u << 2)
#define WM_FAULT_SEQ_OLD   (1u << 3) /* duplicate / out-of-order */
#define WM_FAULT_BAD_LEN   (1u << 4) /* wrong length, magic, version or n_tracks */
#define WM_FAULT_WDT_RESET (1u << 5) /* last boot was a watchdog reset */

#define WM_TTC_NONE    (-1)
#define WM_THREAT_NONE 0xFFFFu

typedef struct __attribute__((packed)) {
	uint16_t id;
	uint8_t cls;          /* enum wm_class */
	uint8_t conf;         /* 0..255 = 0..1 */
	int32_t dist_mm;      /* longitudinal distance ahead, >0 */
	int32_t lat_mm;       /* lateral offset, +right */
	int32_t closing_mm_s; /* >0 = getting closer */
} wm_track_t;

typedef struct __attribute__((packed)) {
	uint16_t magic; /* WM_MAGIC_PERCEPTION */
	uint8_t version;
	uint8_t n_tracks;    /* 0..8 */
	uint32_t seq;        /* +1 per frame */
	uint64_t tx_time_us; /* TX2 monotonic clock */
	wm_track_t tracks[WM_MAX_TRACKS];
	uint16_t crc16;
} wm_perception_t;

typedef struct __attribute__((packed)) {
	uint16_t magic; /* WM_MAGIC_STATUS */
	uint8_t version;
	uint8_t state;            /* enum wm_state */
	uint32_t seq_echo;        /* seq of the frame that produced this decision */
	uint64_t tx_time_us_echo; /* copied back -> TX2 computes round trip */
	int32_t ttc_ms;           /* -1 = no threat */
	uint16_t threat_id;       /* 0xFFFF = none */
	uint16_t fault_flags;
	uint32_t decision_latency_ns; /* packet arrival -> outputs written */
	int16_t imu_ax_mg;            /* longitudinal accel, milli-g */
	int16_t imu_ay_mg;            /* lateral accel */
	uint8_t maneuver;             /* enum wm_maneuver */
	uint8_t reserved;
	uint16_t crc16;
} wm_status_t;

_Static_assert(sizeof(wm_track_t) == 16, "wm_track_t must be 16 bytes");
_Static_assert(sizeof(wm_perception_t) == 146, "wm_perception_t must be 146 bytes");
_Static_assert(sizeof(wm_status_t) == 36, "wm_status_t must be 36 bytes");
_Static_assert(offsetof(wm_perception_t, crc16) == 144, "crc16 must be last");
_Static_assert(offsetof(wm_status_t, crc16) == 34, "crc16 must be last");

#define WM_PERCEPTION_CRC_LEN offsetof(wm_perception_t, crc16)
#define WM_STATUS_CRC_LEN     offsetof(wm_status_t, crc16)

/*
 * CRC-16/CCITT-FALSE. On Zephyr this is the library crc16_itu_t(); elsewhere
 * (host unit tests) a bitwise reference implementation with identical output.
 */
#ifdef __ZEPHYR__
#include <zephyr/sys/crc.h>
static inline uint16_t wm_crc16(const void *buf, size_t len)
{
	return crc16_itu_t(0xFFFF, (const uint8_t *)buf, len);
}
#else
static inline uint16_t wm_crc16(const void *buf, size_t len)
{
	const uint8_t *p = (const uint8_t *)buf;
	uint16_t crc = 0xFFFF;

	while (len--) {
		crc ^= (uint16_t)(*p++) << 8;
		for (int i = 0; i < 8; i++) {
			crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
		}
	}
	return crc;
}
#endif

#endif /* WINGMAN_PROTO_H */
