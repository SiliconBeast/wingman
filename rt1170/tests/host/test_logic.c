/*
 * Host unit tests for the supervisor core. No Zephyr needed.
 *   make -C rt1170/tests/host
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "wm_logic.h"
#include "wm_stats.h"

static int failures;

#define CHECK(cond)                                                                                \
	do {                                                                                       \
		if (!(cond)) {                                                                     \
			printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                     \
			failures++;                                                                \
		}                                                                                  \
	} while (0)

#define CHECK_STATE(s, st)                                                                         \
	do {                                                                                       \
		if ((s)->state != (st)) {                                                          \
			printf("FAIL %s:%d: state %s, expected %s\n", __FILE__, __LINE__,          \
			       wm_state_name((s)->state), wm_state_name(st));                      \
			failures++;                                                                \
		}                                                                                  \
	} while (0)

static uint32_t next_seq;

static wm_perception_t frame(int n, const wm_track_t *tracks)
{
	wm_perception_t f;

	memset(&f, 0, sizeof(f));
	f.magic = WM_MAGIC_PERCEPTION;
	f.version = WM_VERSION;
	f.n_tracks = (uint8_t)n;
	f.seq = next_seq++;
	f.tx_time_us = 1000ull * f.seq;
	if (n) {
		memcpy(f.tracks, tracks, (size_t)n * sizeof(wm_track_t));
	}
	f.crc16 = wm_crc16(&f, WM_PERCEPTION_CRC_LEN);
	return f;
}

static wm_track_t trk(uint16_t id, int32_t dist, int32_t lat, int32_t closing)
{
	wm_track_t t = {.id = id, .cls = WM_CLS_CAR, .conf = 200, .dist_mm = dist, .lat_mm = lat,
			.closing_mm_s = closing};
	return t;
}

static void feed(struct wm_sup *s, int n, const wm_track_t *tracks)
{
	wm_perception_t f = frame(n, tracks);

	wm_sup_on_frame(s, &f);
}

static void feed1(struct wm_sup *s, int32_t dist, int32_t lat, int32_t closing)
{
	wm_track_t t = trk(1, dist, lat, closing);

	feed(s, 1, &t);
}

static struct wm_sup fresh(void)
{
	struct wm_sup s;

	wm_sup_init(&s, NULL);
	wm_sup_link_ready(&s);
	next_seq = 0;
	return s;
}

static void test_crc_and_sizes(void)
{
	CHECK(wm_crc16("123456789", 9) == 0x29B1);
	CHECK(sizeof(wm_perception_t) == 146);
	CHECK(sizeof(wm_status_t) == 36);
}

static void test_parse(void)
{
	wm_perception_t f = frame(0, NULL), out;
	uint8_t buf[200];

	memcpy(buf, &f, sizeof(f));
	CHECK(wm_parse_perception(buf, sizeof(f), &out) == WM_PARSE_OK);
	CHECK(wm_parse_perception(buf, sizeof(f) - 1, &out) == WM_PARSE_BAD_LEN);
	CHECK(wm_parse_perception(buf, sizeof(f) + 1, &out) == WM_PARSE_BAD_LEN);

	buf[20] ^= 0x01;
	CHECK(wm_parse_perception(buf, sizeof(f), &out) == WM_PARSE_BAD_CRC);

	f.version = 9;
	f.crc16 = wm_crc16(&f, WM_PERCEPTION_CRC_LEN);
	memcpy(buf, &f, sizeof(f));
	CHECK(wm_parse_perception(buf, sizeof(f), &out) == WM_PARSE_BAD_FORMAT);

	f.version = WM_VERSION;
	f.n_tracks = 9;
	f.crc16 = wm_crc16(&f, WM_PERCEPTION_CRC_LEN);
	memcpy(buf, &f, sizeof(f));
	CHECK(wm_parse_perception(buf, sizeof(f), &out) == WM_PARSE_BAD_FORMAT);
}

static void test_threat_selection(void)
{
	struct wm_thresholds th;
	struct wm_threat t;

	wm_thresholds_default(&th);

	wm_track_t tr[] = {
		trk(1, 20000, 0, 10000),     /* in path, ttc 2000 */
		trk(2, 10000, 2000, 10000),  /* out of path */
		trk(3, 30000, -500, 20000),  /* in path, ttc 1500  <- threat */
		trk(4, 5000, 0, 50),         /* inside deadband */
		trk(5, 1000, 0, -3000),      /* opening */
		trk(6, 8000, 0, 10000),      /* low confidence */
	};
	tr[5].conf = 50;
	wm_perception_t f = frame(6, tr);

	wm_find_threat(&th, &f, &t);
	CHECK(t.id == 3);
	CHECK(t.ttc_ms == 1500);
	CHECK(!t.close_range);

	wm_perception_t e = frame(0, NULL);

	wm_find_threat(&th, &e, &t);
	CHECK(t.ttc_ms == WM_TTC_NONE && t.id == WM_THREAT_NONE);
}

static void test_approach_and_hysteresis(void)
{
	struct wm_sup s = fresh();

	CHECK_STATE(&s, WM_WAIT_LINK);
	feed1(&s, 40000, 0, 10000); /* ttc 4000 */
	CHECK_STATE(&s, WM_NOMINAL);
	feed1(&s, 25000, 0, 10000); /* 2500: not < 2500 */
	CHECK_STATE(&s, WM_NOMINAL);
	feed1(&s, 24900, 0, 10000); /* 2490 */
	CHECK_STATE(&s, WM_CAUTION);
	feed1(&s, 27000, 0, 10000); /* 2700: inside hysteresis band, stay */
	CHECK_STATE(&s, WM_CAUTION);
	feed1(&s, 11900, 0, 10000); /* 1190 */
	CHECK_STATE(&s, WM_WARNING);
	feed1(&s, 14000, 0, 10000); /* 1400: stay WARNING */
	CHECK_STATE(&s, WM_WARNING);
	feed1(&s, 16000, 0, 10000); /* 1600: down to CAUTION */
	CHECK_STATE(&s, WM_CAUTION);
	feed1(&s, 29000, 0, 10000); /* 2900: NOMINAL */
	CHECK_STATE(&s, WM_NOMINAL);
	feed(&s, 0, NULL);
	CHECK_STATE(&s, WM_NOMINAL);
}

static void test_close_range_and_braking(void)
{
	struct wm_sup s = fresh();

	feed1(&s, 2500, 0, 500); /* ttc 5000 but 2.5 m and closing */
	CHECK_STATE(&s, WM_WARNING);
	feed1(&s, 2500, 0, 50); /* inside deadband: not closing */
	CHECK_STATE(&s, WM_NOMINAL);

	wm_sup_set_braking(&s, true);
	feed1(&s, 10000, 0, 10000); /* ttc 1000 -> WARNING, suppressed to CAUTION */
	CHECK_STATE(&s, WM_CAUTION);
	CHECK(s.cnt.braking_suppressed == 1);
	feed1(&s, 2000, 0, 3000); /* close range is never suppressed */
	CHECK_STATE(&s, WM_WARNING);
	wm_sup_set_braking(&s, false);
}

static void test_heartbeat_failsafe_and_recovery(void)
{
	struct wm_sup s = fresh();

	wm_sup_on_heartbeat_lost(&s); /* before any link: ignored */
	CHECK_STATE(&s, WM_WAIT_LINK);

	feed(&s, 0, NULL);
	CHECK_STATE(&s, WM_NOMINAL);
	wm_sup_on_heartbeat_lost(&s);
	CHECK_STATE(&s, WM_FAILSAFE);
	CHECK(wm_sup_take_faults(&s) & WM_FAULT_HEARTBEAT);
	CHECK(s.cnt.failsafe_entries == 1);

	/* sender restarted: seq goes back to 0 and must be accepted */
	next_seq = 0;
	for (int i = 0; i < 9; i++) {
		feed(&s, 0, NULL);
		CHECK_STATE(&s, WM_FAILSAFE);
	}
	CHECK(!(wm_sup_take_faults(&s) & WM_FAULT_HEARTBEAT));
	feed(&s, 0, NULL); /* 10th consecutive in-sequence frame */
	CHECK_STATE(&s, WM_NOMINAL);
	CHECK(s.cnt.seq_old == 0);
}

static void test_recovery_needs_in_sequence(void)
{
	struct wm_sup s = fresh();

	feed(&s, 0, NULL);
	wm_sup_on_heartbeat_lost(&s);
	for (int i = 0; i < 5; i++) {
		feed(&s, 0, NULL);
	}
	next_seq += 3; /* gap restarts the count */
	for (int i = 0; i < 9; i++) {
		feed(&s, 0, NULL);
		CHECK_STATE(&s, WM_FAILSAFE);
	}
	feed(&s, 0, NULL);
	CHECK_STATE(&s, WM_FAILSAFE); /* gap frame itself did not count */
	feed(&s, 0, NULL);
	CHECK_STATE(&s, WM_NOMINAL);
	CHECK(s.cnt.seq_gap == 1);
}

static void test_recovery_goes_straight_to_threat_level(void)
{
	struct wm_sup s = fresh();

	feed(&s, 0, NULL);
	wm_sup_on_heartbeat_lost(&s);
	next_seq = 0;
	for (int i = 0; i < 10; i++) {
		feed1(&s, 10000, 0, 10000);
	}
	CHECK_STATE(&s, WM_WARNING);
}

static void test_bad_frames(void)
{
	struct wm_sup s = fresh();

	feed(&s, 0, NULL);
	for (int i = 0; i < 4; i++) {
		wm_sup_on_bad(&s, WM_PARSE_BAD_CRC);
	}
	CHECK_STATE(&s, WM_NOMINAL);
	feed(&s, 0, NULL); /* a good frame resets the run */
	for (int i = 0; i < 4; i++) {
		wm_sup_on_bad(&s, WM_PARSE_BAD_CRC);
	}
	CHECK_STATE(&s, WM_NOMINAL);
	wm_sup_on_bad(&s, WM_PARSE_BAD_LEN);
	CHECK_STATE(&s, WM_FAILSAFE);
	uint16_t f = wm_sup_take_faults(&s);

	CHECK((f & WM_FAULT_CRC) && (f & WM_FAULT_BAD_LEN));
	CHECK(wm_sup_take_faults(&s) == 0); /* transient bits cleared */
	CHECK(s.cnt.crc_fail == 8 && s.cnt.bad_len == 1);
}

static void test_sequence(void)
{
	struct wm_sup s = fresh();
	wm_perception_t a = frame(0, NULL);
	wm_perception_t b = frame(0, NULL);

	CHECK(wm_sup_on_frame(&s, &a));
	CHECK(wm_sup_on_frame(&s, &b));
	CHECK(!wm_sup_on_frame(&s, &b)); /* duplicate */
	CHECK(!wm_sup_on_frame(&s, &a)); /* old */
	CHECK(s.cnt.seq_old == 2);
	CHECK(wm_sup_take_faults(&s) & WM_FAULT_SEQ_OLD);

	/* wraparound */
	s = fresh();
	next_seq = 0xFFFFFFFEu;
	feed(&s, 0, NULL);
	feed(&s, 0, NULL);
	feed(&s, 0, NULL); /* seq 0 */
	CHECK(s.cnt.seq_old == 0 && s.cnt.seq_gap == 0 && s.cnt.frames_ok == 3);
}

static void test_reset(void)
{
	struct wm_sup s = fresh();

	s.th.warn_enter_ms = 999;
	s.sticky_faults = WM_FAULT_WDT_RESET;
	feed(&s, 0, NULL);
	wm_sup_reset(&s);
	CHECK_STATE(&s, WM_WAIT_LINK);
	CHECK(s.th.warn_enter_ms == 999);
	CHECK(s.cnt.frames_ok == 0);
	CHECK(wm_sup_take_faults(&s) == 0);
}

static void test_stats(void)
{
	struct wm_hist h;
	struct wm_run r;

	wm_hist_reset(&h);
	CHECK(wm_hist_percentile_ns(&h, 990) == 0);
	for (uint32_t i = 1; i <= 100; i++) {
		wm_hist_add(&h, i * 1000); /* 1..100 us */
	}
	CHECK(h.min_ns == 1000 && h.max_ns == 100000);
	CHECK(wm_hist_mean_ns(&h) == 50500);
	CHECK(wm_hist_percentile_ns(&h, 500) == 50500);
	CHECK(wm_hist_percentile_ns(&h, 990) == 99500);
	wm_hist_add(&h, 5000000); /* overflow bucket reports max */
	CHECK(wm_hist_percentile_ns(&h, 1000) == 5000000);

	wm_run_reset(&r);
	wm_run_add(&r, 2);
	wm_run_add(&r, 4);
	wm_run_add(&r, 4);
	wm_run_add(&r, 4);
	wm_run_add(&r, 5);
	wm_run_add(&r, 5);
	wm_run_add(&r, 7);
	wm_run_add(&r, 9);
	CHECK(r.mean == 5.0 && r.min == 2.0 && r.max == 9.0);
	CHECK(wm_run_std(&r) > 2.138 && wm_run_std(&r) < 2.139);
}

int main(void)
{
	test_crc_and_sizes();
	test_parse();
	test_threat_selection();
	test_approach_and_hysteresis();
	test_close_range_and_braking();
	test_heartbeat_failsafe_and_recovery();
	test_recovery_needs_in_sequence();
	test_recovery_goes_straight_to_threat_level();
	test_bad_frames();
	test_sequence();
	test_reset();
	test_stats();

	if (failures) {
		printf("%d FAILURE(S)\n", failures);
		return 1;
	}
	printf("ALL TESTS PASSED\n");
	return 0;
}
