/* Shared state between main.c (threads), shell.c and imu.c. */
#ifndef WM_APP_H
#define WM_APP_H

#include <zephyr/kernel.h>

#include "wm_logic.h"
#include "wm_stats.h"

struct wm_measurements {
	struct wm_hist latency;      /* packet arrival -> outputs written */
	struct wm_run interval_us;   /* inter-decision interval */
	struct wm_run failover_ms;   /* last valid packet -> FAILSAFE outputs written */
	uint32_t queue_overflow;     /* events dropped because decide was behind */
	uint32_t status_tx_fail;
};

struct wm_imu_state {
	bool ok;
	int16_t ax_mg; /* longitudinal, +forward, low-passed, bias-removed */
	int16_t ay_mg; /* lateral, +right */
	uint8_t maneuver;
	bool impact_latched;
};

/* Guards g_sup, g_meas and g_imu. Held for microseconds only. */
extern struct k_spinlock g_lock;
extern struct wm_sup g_sup;
extern struct wm_measurements g_meas;
extern struct wm_imu_state g_imu;

extern volatile bool g_hang_requested;

void wm_meas_reset(void);

/* Black box: last ~2 s of decisions, frozen on impact. */
struct wm_bb_entry {
	uint32_t t_ms;
	uint8_t state;
	uint16_t threat_id;
	int32_t ttc_ms;
	int16_t ax_mg;
	int16_t ay_mg;
};
#define WM_BB_LEN 64
void wm_blackbox_freeze(void);
void wm_blackbox_print(void (*print)(void *ctx, const struct wm_bb_entry *e), void *ctx);

void wm_imu_rezero(void);

#endif /* WM_APP_H */
