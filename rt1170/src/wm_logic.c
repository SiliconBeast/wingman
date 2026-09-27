#include <string.h>

#include "wm_logic.h"

void wm_thresholds_default(struct wm_thresholds *th)
{
	th->caution_enter_ms = 2500;
	th->caution_exit_ms = 2800;
	th->warn_enter_ms = 1200;
	th->warn_exit_ms = 1500;
	th->close_range_mm = 3000;
	th->inpath_lat_mm = 1500;
	th->closing_deadband_mm_s = 100;
	th->min_conf = 102; /* 0.4 * 255 */
	th->heartbeat_timeout_ms = 100;
	th->bad_frames_to_failsafe = 5;
	th->good_frames_to_recover = 10;
}

enum wm_parse_result wm_parse_perception(const void *buf, size_t len, wm_perception_t *out)
{
	if (len != sizeof(wm_perception_t)) {
		return WM_PARSE_BAD_LEN;
	}
	memcpy(out, buf, sizeof(*out));
	if (wm_crc16(out, WM_PERCEPTION_CRC_LEN) != out->crc16) {
		return WM_PARSE_BAD_CRC;
	}
	if (out->magic != WM_MAGIC_PERCEPTION || out->version != WM_VERSION ||
	    out->n_tracks > WM_MAX_TRACKS) {
		return WM_PARSE_BAD_FORMAT;
	}
	return WM_PARSE_OK;
}

static int32_t abs32(int32_t v)
{
	return v < 0 ? -v : v;
}

void wm_find_threat(const struct wm_thresholds *th, const wm_perception_t *f,
		    struct wm_threat *out)
{
	out->ttc_ms = WM_TTC_NONE;
	out->id = WM_THREAT_NONE;
	out->dist_mm = 0;
	out->close_range = false;

	for (unsigned int i = 0; i < f->n_tracks && i < WM_MAX_TRACKS; i++) {
		const wm_track_t *t = &f->tracks[i];

		if (t->conf < th->min_conf || t->dist_mm <= 0 || abs32(t->lat_mm) >= th->inpath_lat_mm ||
		    t->closing_mm_s <= th->closing_deadband_mm_s) {
			continue;
		}

		int64_t ttc = (int64_t)t->dist_mm * 1000 / t->closing_mm_s;

		if (ttc > INT32_MAX) {
			ttc = INT32_MAX;
		}
		if (t->dist_mm < th->close_range_mm) {
			out->close_range = true;
		}
		if (out->ttc_ms == WM_TTC_NONE || ttc < out->ttc_ms) {
			out->ttc_ms = (int32_t)ttc;
			out->id = t->id;
			out->dist_mm = t->dist_mm;
		}
	}
}

static void clear_threat(struct wm_threat *t)
{
	t->ttc_ms = WM_TTC_NONE;
	t->id = WM_THREAT_NONE;
	t->dist_mm = 0;
	t->close_range = false;
}

void wm_sup_init(struct wm_sup *s, const struct wm_thresholds *th)
{
	memset(s, 0, sizeof(*s));
	if (th) {
		s->th = *th;
	} else {
		wm_thresholds_default(&s->th);
	}
	s->state = WM_BOOT;
	clear_threat(&s->threat);
}

void wm_sup_reset(struct wm_sup *s)
{
	struct wm_thresholds th = s->th;
	bool braking = s->driver_braking;

	wm_sup_init(s, &th);
	s->driver_braking = braking;
	s->state = WM_WAIT_LINK;
}

void wm_sup_link_ready(struct wm_sup *s)
{
	if (s->state == WM_BOOT) {
		s->state = WM_WAIT_LINK;
	}
}

static void enter_failsafe(struct wm_sup *s)
{
	if (s->state != WM_FAILSAFE) {
		s->cnt.failsafe_entries++;
		s->state = WM_FAILSAFE;
	}
	s->consec_good = 0;
	clear_threat(&s->threat);
}

/* Hysteresis: thresholds to stay in a level are looser than to enter it. */
static enum wm_state evaluate(struct wm_sup *s, enum wm_state cur)
{
	const struct wm_thresholds *th = &s->th;
	const struct wm_threat *t = &s->threat;

	if (t->ttc_ms == WM_TTC_NONE) {
		return WM_NOMINAL;
	}

	bool warn = t->close_range ||
		    t->ttc_ms < (cur == WM_WARNING ? th->warn_exit_ms : th->warn_enter_ms);

	if (warn) {
		/* Driver already braking: don't nag, but never suppress the close-range override. */
		if (s->driver_braking && !t->close_range) {
			s->cnt.braking_suppressed++;
			return WM_CAUTION;
		}
		return WM_WARNING;
	}

	bool in_caution = (cur == WM_CAUTION || cur == WM_WARNING);

	if (t->ttc_ms < (in_caution ? th->caution_exit_ms : th->caution_enter_ms)) {
		return WM_CAUTION;
	}
	return WM_NOMINAL;
}

bool wm_sup_on_frame(struct wm_sup *s, const wm_perception_t *f)
{
	bool in_seq = true;

	if (s->have_seq) {
		int32_t d = (int32_t)(f->seq - s->last_seq);

		if (d <= 0) {
			s->cnt.seq_old++;
			s->faults |= WM_FAULT_SEQ_OLD;
			s->consec_good = 0;
			return false;
		}
		if (d != 1) {
			in_seq = false;
			s->cnt.seq_gap++;
			s->faults |= WM_FAULT_SEQ_GAP;
		}
	}
	s->have_seq = true;
	s->last_seq = f->seq;
	s->consec_bad = 0;
	s->hb_lost = false;
	s->cnt.frames_ok++;
	s->consec_good = in_seq ? s->consec_good + 1 : 0;

	wm_find_threat(&s->th, f, &s->threat);

	enum wm_state cur = s->state;

	if (cur == WM_FAILSAFE) {
		if (s->consec_good < s->th.good_frames_to_recover) {
			return true;
		}
		cur = WM_NOMINAL;
	} else if (cur == WM_BOOT || cur == WM_WAIT_LINK) {
		cur = WM_NOMINAL;
	}
	s->state = evaluate(s, cur);
	return true;
}

void wm_sup_on_bad(struct wm_sup *s, enum wm_parse_result why)
{
	if (why == WM_PARSE_BAD_CRC) {
		s->cnt.crc_fail++;
		s->faults |= WM_FAULT_CRC;
	} else {
		s->cnt.bad_len++;
		s->faults |= WM_FAULT_BAD_LEN;
	}
	s->consec_good = 0;
	s->consec_bad++;
	if (s->consec_bad >= s->th.bad_frames_to_failsafe) {
		enter_failsafe(s);
	}
}

void wm_sup_on_heartbeat_lost(struct wm_sup *s)
{
	if (s->state == WM_BOOT || s->state == WM_WAIT_LINK) {
		return; /* never had a link; nothing to lose */
	}
	if (!s->hb_lost) {
		s->cnt.heartbeat_loss++;
	}
	s->hb_lost = true;
	/* A restarted sender begins again at seq 0; accept it instead of flagging it old. */
	s->have_seq = false;
	enter_failsafe(s);
}

void wm_sup_set_braking(struct wm_sup *s, bool braking)
{
	s->driver_braking = braking;
}

uint16_t wm_sup_take_faults(struct wm_sup *s)
{
	uint16_t f = s->faults | s->sticky_faults;

	if (s->hb_lost) {
		f |= WM_FAULT_HEARTBEAT;
	}
	s->faults = 0;
	return f;
}

const char *wm_state_name(enum wm_state st)
{
	switch (st) {
	case WM_BOOT:
		return "BOOT";
	case WM_WAIT_LINK:
		return "WAIT_LINK";
	case WM_NOMINAL:
		return "NOMINAL";
	case WM_CAUTION:
		return "CAUTION";
	case WM_WARNING:
		return "WARNING";
	case WM_FAILSAFE:
		return "FAILSAFE";
	}
	return "?";
}
