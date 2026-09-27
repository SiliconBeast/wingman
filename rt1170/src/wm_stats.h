/*
 * Measurement accumulators. Pure C; host-tested.
 *
 * wm_hist: fixed-bucket latency histogram (WM_HIST_BUCKET_NS wide, last bucket
 *          collects overflow) with exact min/max/mean and bucket-resolution
 *          percentiles.
 * wm_run:  running min/max/mean/stddev (Welford) for intervals and failover times.
 */
#ifndef WM_STATS_H
#define WM_STATS_H

#include <stdint.h>

#define WM_HIST_BUCKET_NS 500u
#define WM_HIST_BUCKETS   400u /* 400 x 0.5 us = 200 us; bucket 400 = overflow */

struct wm_hist {
	uint32_t n;
	uint32_t min_ns;
	uint32_t max_ns;
	uint64_t sum_ns;
	uint32_t bucket[WM_HIST_BUCKETS + 1];
};

void wm_hist_reset(struct wm_hist *h);
void wm_hist_add(struct wm_hist *h, uint32_t ns);
uint32_t wm_hist_mean_ns(const struct wm_hist *h);
/* Upper edge (ns) of the bucket holding the given percentile (per_mille: 500 = p50, 990 = p99). */
uint32_t wm_hist_percentile_ns(const struct wm_hist *h, uint32_t per_mille);

struct wm_run {
	uint32_t n;
	double min;
	double max;
	double mean;
	double m2;
};

void wm_run_reset(struct wm_run *r);
void wm_run_add(struct wm_run *r, double x);
double wm_run_std(const struct wm_run *r);

#endif /* WM_STATS_H */
