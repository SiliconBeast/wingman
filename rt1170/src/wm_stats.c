#include <math.h>
#include <string.h>

#include "wm_stats.h"

void wm_hist_reset(struct wm_hist *h)
{
	memset(h, 0, sizeof(*h));
	h->min_ns = UINT32_MAX;
}

void wm_hist_add(struct wm_hist *h, uint32_t ns)
{
	uint32_t b = ns / WM_HIST_BUCKET_NS;

	if (b > WM_HIST_BUCKETS) {
		b = WM_HIST_BUCKETS;
	}
	h->bucket[b]++;
	h->n++;
	h->sum_ns += ns;
	if (ns < h->min_ns) {
		h->min_ns = ns;
	}
	if (ns > h->max_ns) {
		h->max_ns = ns;
	}
}

uint32_t wm_hist_mean_ns(const struct wm_hist *h)
{
	return h->n ? (uint32_t)(h->sum_ns / h->n) : 0;
}

uint32_t wm_hist_percentile_ns(const struct wm_hist *h, uint32_t per_mille)
{
	if (h->n == 0) {
		return 0;
	}
	/* smallest bucket whose cumulative count reaches ceil(n * p) */
	uint64_t target = ((uint64_t)h->n * per_mille + 999) / 1000;
	uint64_t acc = 0;

	if (target == 0) {
		target = 1;
	}
	for (uint32_t b = 0; b <= WM_HIST_BUCKETS; b++) {
		acc += h->bucket[b];
		if (acc >= target) {
			return b == WM_HIST_BUCKETS ? h->max_ns : (b + 1) * WM_HIST_BUCKET_NS;
		}
	}
	return h->max_ns;
}

void wm_run_reset(struct wm_run *r)
{
	memset(r, 0, sizeof(*r));
}

void wm_run_add(struct wm_run *r, double x)
{
	r->n++;
	if (r->n == 1 || x < r->min) {
		r->min = x;
	}
	if (r->n == 1 || x > r->max) {
		r->max = x;
	}
	double d = x - r->mean;

	r->mean += d / r->n;
	r->m2 += d * (x - r->mean);
}

double wm_run_std(const struct wm_run *r)
{
	return r->n > 1 ? sqrt(r->m2 / (r->n - 1)) : 0.0;
}
