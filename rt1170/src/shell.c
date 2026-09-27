/*
 * wm stats [reset]          latency histogram, jitter, failover, counters
 * wm state                  current state, threat, faults, thresholds
 * wm reset                  clear counters/faults, back to WAIT_LINK
 * wm thresh <caution> <warn>  TTC enter thresholds in ms (exit = enter + 300)
 * wm imu [zero]             IMU context; "zero" re-measures bias (keep board still)
 * wm blackbox               decisions frozen at the last impact
 * wm hang                   stall the decide thread -> watchdog reset
 */
#include <stdlib.h>

#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>

#include "wm_app.h"

/* Print doubles without relying on printf float support. */
#define FX2(v)      (int)(v), (int)(((v) < 0 ? -(v) : (v)) * 100) % 100
#define US(ns)      (ns) / 1000u, ((ns) % 1000u) / 10u

static int cmd_stats(const struct shell *sh, size_t argc, char **argv)
{
	static struct wm_measurements m;
	struct wm_counters c;

	if (argc > 1 && strcmp(argv[1], "reset") == 0) {
		k_spinlock_key_t key = k_spin_lock(&g_lock);

		wm_meas_reset();
		k_spin_unlock(&g_lock, key);
		shell_print(sh, "stats cleared");
		return 0;
	}

	k_spinlock_key_t key = k_spin_lock(&g_lock);

	m = g_meas;
	c = g_sup.cnt;
	k_spin_unlock(&g_lock, key);

	const struct wm_hist *h = &m.latency;

	shell_print(sh, "decision latency (socket delivery -> outputs written), n=%u", h->n);
	if (h->n) {
		uint32_t p50 = wm_hist_percentile_ns(h, 500);
		uint32_t p99 = wm_hist_percentile_ns(h, 990);
		uint32_t mean = wm_hist_mean_ns(h);

		shell_print(sh, "  min %u.%02u us  mean %u.%02u us  p50<=%u.%02u us  p99<=%u.%02u us  max %u.%02u us",
			    US(h->min_ns), US(mean), US(p50), US(p99), US(h->max_ns));
		shell_print(sh, "  histogram (%u ns buckets, non-empty only):", WM_HIST_BUCKET_NS);
		for (uint32_t b = 0; b <= WM_HIST_BUCKETS; b++) {
			if (!h->bucket[b]) {
				continue;
			}
			if (b == WM_HIST_BUCKETS) {
				shell_print(sh, "    >=%u.%02u us : %u", US(b * WM_HIST_BUCKET_NS),
					    h->bucket[b]);
			} else {
				shell_print(sh, "    %u.%02u-%u.%02u us : %u", US(b * WM_HIST_BUCKET_NS),
					    US((b + 1) * WM_HIST_BUCKET_NS), h->bucket[b]);
			}
		}
	}
	shell_print(sh, "inter-decision interval, n=%u: mean %d.%02d us  std %d.%02d us  min %d.%02d  max %d.%02d",
		    m.interval_us.n, FX2(m.interval_us.mean), FX2(wm_run_std(&m.interval_us)),
		    FX2(m.interval_us.min), FX2(m.interval_us.max));
	shell_print(sh, "failover (last valid frame -> FAILSAFE outputs), n=%u: mean %d.%02d ms  min %d.%02d  max %d.%02d",
		    m.failover_ms.n, FX2(m.failover_ms.mean), FX2(m.failover_ms.min),
		    FX2(m.failover_ms.max));
	shell_print(sh, "frames ok %u  crc %u  bad_len %u  seq_gap %u  seq_old %u", c.frames_ok,
		    c.crc_fail, c.bad_len, c.seq_gap, c.seq_old);
	shell_print(sh, "heartbeat_loss %u  failsafe_entries %u  braking_suppressed %u",
		    c.heartbeat_loss, c.failsafe_entries, c.braking_suppressed);
	shell_print(sh, "queue_overflow %u  status_tx_fail %u", m.queue_overflow, m.status_tx_fail);
	return 0;
}

static int cmd_state(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	k_spinlock_key_t key = k_spin_lock(&g_lock);
	struct wm_sup s = g_sup;

	k_spin_unlock(&g_lock, key);

	shell_print(sh, "state %s", wm_state_name(s.state));
	if (s.threat.id != WM_THREAT_NONE) {
		shell_print(sh, "threat id=%u ttc=%d ms dist=%d mm%s", s.threat.id, s.threat.ttc_ms,
			    s.threat.dist_mm, s.threat.close_range ? " CLOSE-RANGE" : "");
	} else {
		shell_print(sh, "threat none");
	}
	uint16_t f = s.faults | s.sticky_faults | (s.hb_lost ? WM_FAULT_HEARTBEAT : 0);

	shell_print(sh, "faults 0x%02x%s%s%s%s%s%s", f, (f & WM_FAULT_HEARTBEAT) ? " HEARTBEAT" : "",
		    (f & WM_FAULT_CRC) ? " CRC" : "", (f & WM_FAULT_SEQ_GAP) ? " SEQ_GAP" : "",
		    (f & WM_FAULT_SEQ_OLD) ? " SEQ_OLD" : "", (f & WM_FAULT_BAD_LEN) ? " BAD_LEN" : "",
		    (f & WM_FAULT_WDT_RESET) ? " WDT_RESET" : "");
	shell_print(sh, "driver braking %s, last seq %u", s.driver_braking ? "yes" : "no", s.last_seq);
	shell_print(sh, "thresholds: caution %d/%d ms  warn %d/%d ms (enter/exit)  close %d mm  lane %d mm  heartbeat %u ms",
		    s.th.caution_enter_ms, s.th.caution_exit_ms, s.th.warn_enter_ms,
		    s.th.warn_exit_ms, s.th.close_range_mm, s.th.inpath_lat_mm,
		    s.th.heartbeat_timeout_ms);
	return 0;
}

static int cmd_reset(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	k_spinlock_key_t key = k_spin_lock(&g_lock);

	wm_sup_reset(&g_sup);
	wm_meas_reset();
	g_imu.impact_latched = false;
	k_spin_unlock(&g_lock, key);
	shell_print(sh, "supervisor reset -> WAIT_LINK");
	return 0;
}

static int cmd_thresh(const struct shell *sh, size_t argc, char **argv)
{
	int caution = atoi(argv[1]);
	int warn = atoi(argv[2]);

	if (warn <= 0 || caution <= warn) {
		shell_error(sh, "need 0 < warn < caution (ms)");
		return -EINVAL;
	}
	k_spinlock_key_t key = k_spin_lock(&g_lock);

	g_sup.th.caution_enter_ms = caution;
	g_sup.th.caution_exit_ms = caution + 300;
	g_sup.th.warn_enter_ms = warn;
	g_sup.th.warn_exit_ms = warn + 300;
	k_spin_unlock(&g_lock, key);
	shell_print(sh, "caution %d/%d ms, warn %d/%d ms", caution, caution + 300, warn, warn + 300);
	return 0;
}

static int cmd_imu(const struct shell *sh, size_t argc, char **argv)
{
	static const char *const names[] = {"unknown", "steady", "brake", "accel", "swerve"};

	if (argc > 1 && strcmp(argv[1], "zero") == 0) {
		wm_imu_rezero();
		shell_print(sh, "re-zeroing IMU, keep the board still for 0.5 s");
		return 0;
	}
	k_spinlock_key_t key = k_spin_lock(&g_lock);
	struct wm_imu_state s = g_imu;
	bool braking = g_sup.driver_braking;

	k_spin_unlock(&g_lock, key);

	if (!s.ok) {
		shell_print(sh, "IMU not running");
		return 0;
	}
	shell_print(sh, "ax %d mg  ay %d mg  maneuver %s  braking %s  impact %s", s.ax_mg, s.ay_mg,
		    s.maneuver < ARRAY_SIZE(names) ? names[s.maneuver] : "?", braking ? "yes" : "no",
		    s.impact_latched ? "LATCHED" : "no");
	return 0;
}

static void bb_print(void *ctx, const struct wm_bb_entry *e)
{
	const struct shell *sh = ctx;

	shell_print(sh, "  %8u ms  %-9s id=%-5u ttc=%-6d ax=%-5d ay=%d", e->t_ms,
		    wm_state_name((enum wm_state)e->state), e->threat_id, e->ttc_ms, e->ax_mg,
		    e->ay_mg);
}

static int cmd_blackbox(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	shell_print(sh, "black box (last %d decisions before impact, oldest first):", WM_BB_LEN);
	wm_blackbox_print(bb_print, (void *)sh);
	return 0;
}

static int cmd_hang(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argc);
	ARG_UNUSED(argv);

	shell_warn(sh, "stalling decide thread...");
	g_hang_requested = true;
	return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(wm_cmds,
	SHELL_CMD_ARG(stats, NULL, "Measurements. 'wm stats reset' clears them.", cmd_stats, 1, 1),
	SHELL_CMD(state, NULL, "State, threat, faults, thresholds.", cmd_state),
	SHELL_CMD(reset, NULL, "Clear counters and faults, back to WAIT_LINK.", cmd_reset),
	SHELL_CMD_ARG(thresh, NULL, "<caution_ms> <warn_ms> TTC enter thresholds.", cmd_thresh, 3, 0),
	SHELL_CMD_ARG(imu, NULL, "IMU context. 'wm imu zero' re-zeroes.", cmd_imu, 1, 1),
	SHELL_CMD(blackbox, NULL, "Decisions frozen at the last impact.", cmd_blackbox),
	SHELL_CMD(hang, NULL, "Stall the decide thread (watchdog demo).", cmd_hang),
	SHELL_SUBCMD_SET_END);

SHELL_CMD_REGISTER(wm, &wm_cmds, "Wingman supervisor", NULL);
