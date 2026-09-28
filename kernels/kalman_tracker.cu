/*
 * Kalman filter bank on the GPU: one thread per track, AoS vs SoA memory layout.
 *
 *   make && ./kalman_tracker                 # N sweep 64 .. 262144, prints a table
 *   ./kalman_tracker --csv results.csv       # also write the table as CSV
 *   ./kalman_tracker --sizes 1024,65536      # pick sizes
 *   sudo make profile                        # nvprof: gld/gst efficiency, occupancy
 *
 * Each "step" is one tracker frame for every track: predict by dt, then update
 * with that track's new measurement (same math as tx2/tracker.py, from
 * kalman_math.h). Both kernels run identical per-thread code; only where the 14
 * floats of state live in memory differs:
 *
 *   v1 AoS  track i = 14 consecutive floats; thread i reads tr[i].s[k] -> neighbouring
 *           threads are 56 B apart, so every load instruction touches many cache lines
 *   v2 SoA  float k of track i lives at st[k*n + i]; for each k, neighbouring threads
 *           read neighbouring floats -> fully coalesced
 *
 * Timings (all measured, per step, averaged over many steps after a warm-up launch):
 *   CPU        one core, same kalman_math.h code, std::chrono
 *   GPU AoS    kernel only, cudaEvent
 *   GPU SoA    kernel only, cudaEvent
 *   SoA e2e    copy this frame's measurements in + kernel + copy the 4*N state floats
 *              back (pinned memory) - what a tracker that needs results on the CPU pays
 * Correctness: after the first frames, every GPU state is compared with the CPU
 * reference (differences come only from float FMA contraction).
 *
 * CUDA 10.2 / C++11 / sm_62 (Jetson TX2). Report only numbers this program prints.
 */
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>
#include <vector>

#include <cuda_runtime.h>

#include "kalman_math.h"

#define CK(call)                                                                                   \
	do {                                                                                       \
		cudaError_t e_ = (call);                                                           \
		if (e_ != cudaSuccess) {                                                           \
			fprintf(stderr, "%s:%d: %s -> %s\n", __FILE__, __LINE__, #call,            \
				cudaGetErrorString(e_));                                           \
			exit(1);                                                                   \
		}                                                                                  \
	} while (0)

/*
 * Fair comparison: both kernels must run at the same occupancy, or AoS-vs-SoA mixes
 * memory layout with occupancy. Naively written, SoA needed 56 registers (vs 32 for
 * AoS) and got half the resident threads; with the address barrier in kf_step_soa
 * both compile to 32 registers, no spills (see -Xptxas -v in `make` output). The
 * launch bounds keep it that way: <= 32 registers at 256 threads/block = 8 blocks/SM.
 */
#define BLOCK 256
#define MIN_BLOCKS_PER_SM 8

struct TrackAoS {
	float s[KF_NS]; /* x[0..3] then P upper triangle [0..9] */
};

static const float DT = 1.0f / 30.0f;
static const float Q = (float)(KF_ACCEL_NOISE * KF_ACCEL_NOISE);
static const int FRAMES = 32;      /* distinct measurement frames, cycled */
static const int CHECK_STEPS = 32; /* steps compared against the CPU reference */

/* ------------------------------------------------------------------ kernels */

__global__ void __launch_bounds__(BLOCK, MIN_BLOCKS_PER_SM) kf_step_aos(TrackAoS *__restrict__ tr, const float2 *__restrict__ z, int n,
			    float dt, float q)
{
	int i = blockIdx.x * blockDim.x + threadIdx.x;
	if (i >= n) {
		return;
	}
	float s[KF_NS];
#pragma unroll
	for (int k = 0; k < KF_NS; k++) {
		s[k] = tr[i].s[k];
	}
	float2 m = z[i];
	kf_step(s, s + KF_NX, dt, q, m.x, m.y);
#pragma unroll
	for (int k = 0; k < KF_NS; k++) {
		tr[i].s[k] = s[k];
	}
}

__global__ void __launch_bounds__(BLOCK, MIN_BLOCKS_PER_SM) kf_step_soa(float *__restrict__ st, const float2 *__restrict__ z, int n,
			    float dt, float q)
{
	int i = blockIdx.x * blockDim.x + threadIdx.x;
	if (i >= n) {
		return;
	}
	float s[KF_NS];
	const float *src = st + i;
#pragma unroll
	for (int k = 0; k < KF_NS; k++, src += n) {
		s[k] = *src;
	}
	float2 m = z[i];
	kf_step(s, s + KF_NX, dt, q, m.x, m.y);
	/* Opaque to the optimiser: stops it keeping all 14 load addresses (64-bit each)
	 * alive through the math to reuse for the stores - that alone cost 24 extra
	 * registers. Now the store addresses are re-walked from one pointer. */
	float *dst = st + i;
	asm volatile("" : "+l"(dst));
#pragma unroll
	for (int k = 0; k < KF_NS; k++, dst += n) {
		*dst = s[k];
	}
}

/* ------------------------------------------------------------------ scene */

/* N targets, constant velocity, measured through the monocular noise model.
 * z[f*n + i] = measurement of target i in frame f; init[i*14..] = state after birth. */
static void make_scene(int n, std::vector<float> &init, float *z /* FRAMES*n float2 */)
{
	std::mt19937 g(12345u + (unsigned)n);
	std::uniform_real_distribution<float> u(0.0f, 1.0f);
	std::normal_distribution<float> n01(0.0f, 1.0f);

	init.assign((size_t)n * KF_NS, 0.0f);
	for (int i = 0; i < n; i++) {
		float lat = -10.0f + 20.0f * u(g), dist = 10.0f + 60.0f * u(g);
		float vl = -2.0f + 4.0f * u(g), vd = -12.0f + 15.0f * u(g);
		float rl, rd;
		kf_meas_var(dist, &rl, &rd);
		float *s = &init[(size_t)i * KF_NS];
		kf_init(s, s + KF_NX, lat + std::sqrt(rl) * n01(g), dist + std::sqrt(rd) * n01(g), rl, rd,
			(float)(KF_INIT_V_LAT_SD * KF_INIT_V_LAT_SD),
			(float)(KF_INIT_V_DIST_SD * KF_INIT_V_DIST_SD));
		for (int f = 0; f < FRAMES; f++) {
			lat += vl * DT;
			dist += vd * DT;
			kf_meas_var(dist, &rl, &rd);
			z[2 * ((size_t)f * n + i) + 0] = lat + std::sqrt(rl) * n01(g);
			z[2 * ((size_t)f * n + i) + 1] = dist + std::sqrt(rd) * n01(g);
		}
	}
}

static void cpu_step(float *states, const float *z, int n)
{
	for (int i = 0; i < n; i++) {
		float *s = states + (size_t)i * KF_NS;
		kf_step(s, s + KF_NX, DT, Q, z[2 * i], z[2 * i + 1]);
	}
}

static void aos_to_soa(const std::vector<float> &aos, std::vector<float> &soa, int n)
{
	soa.resize(aos.size());
	for (int i = 0; i < n; i++) {
		for (int k = 0; k < KF_NS; k++) {
			soa[(size_t)k * n + i] = aos[(size_t)i * KF_NS + k];
		}
	}
}

/* max relative difference, |a-b| / max(1, |b|) */
static double max_rel(const float *a, const float *b, size_t count)
{
	double m = 0;
	for (size_t j = 0; j < count; j++) {
		double d = std::fabs((double)a[j] - (double)b[j]) / std::max(1.0, std::fabs((double)b[j]));
		if (!(d <= m)) { /* also catches NaN */
			m = std::isnan(d) ? INFINITY : d;
		}
	}
	return m;
}

/* ------------------------------------------------------------------ one size */

struct Result {
	int n;
	int steps;
	double cpu_us, aos_us, soa_us, soa_e2e_us;
	double err_aos, err_soa;
};

static Result run_size(int n, int block, bool profile)
{
	Result r;
	memset(&r, 0, sizeof(r));
	r.n = n;
	/* enough steps that each timing covers a measurable interval */
	r.steps = profile ? 10 : std::max(20, std::min(1000, (int)(8000000LL / n)));

	const size_t zbytes = (size_t)FRAMES * n * sizeof(float2);
	const size_t sbytes = (size_t)n * KF_NS * sizeof(float);

	float *hz = nullptr, *hx = nullptr; /* pinned: measurements, and state readback */
	CK(cudaMallocHost(&hz, zbytes));
	CK(cudaMallocHost(&hx, (size_t)n * KF_NX * sizeof(float)));
	std::vector<float> init, init_soa;
	make_scene(n, init, hz);
	aos_to_soa(init, init_soa, n);

	TrackAoS *d_aos = nullptr;
	float *d_soa = nullptr;
	float2 *d_z = nullptr, *d_zframe = nullptr;
	CK(cudaMalloc(&d_aos, sbytes));
	CK(cudaMalloc(&d_soa, sbytes));
	CK(cudaMalloc(&d_z, zbytes));
	CK(cudaMalloc(&d_zframe, (size_t)n * sizeof(float2)));
	CK(cudaMemcpy(d_z, hz, zbytes, cudaMemcpyHostToDevice));
	CK(cudaMemcpy(d_aos, init.data(), sbytes, cudaMemcpyHostToDevice));
	CK(cudaMemcpy(d_soa, init_soa.data(), sbytes, cudaMemcpyHostToDevice));

	const int grid = (n + block - 1) / block;
	const float2 *zf = d_z; /* frame f at d_z + f*n */

	/* ---- correctness: CHECK_STEPS frames on CPU and both GPU layouts ---- */
	std::vector<float> ref = init;
	for (int f = 0; f < CHECK_STEPS; f++) {
		cpu_step(ref.data(), hz + 2 * (size_t)(f % FRAMES) * n, n);
		kf_step_aos<<<grid, block>>>(d_aos, zf + (size_t)(f % FRAMES) * n, n, DT, Q);
		kf_step_soa<<<grid, block>>>(d_soa, zf + (size_t)(f % FRAMES) * n, n, DT, Q);
	}
	CK(cudaGetLastError());
	CK(cudaDeviceSynchronize());
	std::vector<float> g_aos((size_t)n * KF_NS), g_soa((size_t)n * KF_NS), g_soa_aos((size_t)n * KF_NS);
	CK(cudaMemcpy(g_aos.data(), d_aos, sbytes, cudaMemcpyDeviceToHost));
	CK(cudaMemcpy(g_soa.data(), d_soa, sbytes, cudaMemcpyDeviceToHost));
	for (int i = 0; i < n; i++) {
		for (int k = 0; k < KF_NS; k++) {
			g_soa_aos[(size_t)i * KF_NS + k] = g_soa[(size_t)k * n + i];
		}
	}
	r.err_aos = max_rel(g_aos.data(), ref.data(), ref.size());
	r.err_soa = max_rel(g_soa_aos.data(), ref.data(), ref.size());

	if (profile) {
		/* nvprof replays each kernel per metric; a handful of launches is plenty */
		for (int s = 0; s < r.steps; s++) {
			kf_step_aos<<<grid, block>>>(d_aos, zf + (size_t)(s % FRAMES) * n, n, DT, Q);
			kf_step_soa<<<grid, block>>>(d_soa, zf + (size_t)(s % FRAMES) * n, n, DT, Q);
		}
		CK(cudaGetLastError());
		CK(cudaDeviceSynchronize());
	} else {
		cudaEvent_t t0, t1;
		CK(cudaEventCreate(&t0));
		CK(cudaEventCreate(&t1));
		float ms = 0;

		/* ---- CPU ---- */
		std::vector<float> cpu = ref;
		auto c0 = std::chrono::steady_clock::now();
		for (int s = 0; s < r.steps; s++) {
			cpu_step(cpu.data(), hz + 2 * (size_t)(s % FRAMES) * n, n);
		}
		auto c1 = std::chrono::steady_clock::now();
		r.cpu_us = std::chrono::duration<double, std::micro>(c1 - c0).count() / r.steps;
		volatile float sink = cpu[0]; /* keep the loop from being optimised away */
		(void)sink;

		/* ---- GPU AoS, kernel only ---- */
		kf_step_aos<<<grid, block>>>(d_aos, zf, n, DT, Q); /* warm-up */
		CK(cudaEventRecord(t0));
		for (int s = 0; s < r.steps; s++) {
			kf_step_aos<<<grid, block>>>(d_aos, zf + (size_t)(s % FRAMES) * n, n, DT, Q);
		}
		CK(cudaEventRecord(t1));
		CK(cudaEventSynchronize(t1));
		CK(cudaGetLastError());
		CK(cudaEventElapsedTime(&ms, t0, t1));
		r.aos_us = 1000.0 * ms / r.steps;

		/* ---- GPU SoA, kernel only ---- */
		kf_step_soa<<<grid, block>>>(d_soa, zf, n, DT, Q); /* warm-up */
		CK(cudaEventRecord(t0));
		for (int s = 0; s < r.steps; s++) {
			kf_step_soa<<<grid, block>>>(d_soa, zf + (size_t)(s % FRAMES) * n, n, DT, Q);
		}
		CK(cudaEventRecord(t1));
		CK(cudaEventSynchronize(t1));
		CK(cudaGetLastError());
		CK(cudaEventElapsedTime(&ms, t0, t1));
		r.soa_us = 1000.0 * ms / r.steps;

		/* ---- GPU SoA end to end: z in, kernel, x (first 4n floats of SoA) out ---- */
		CK(cudaEventRecord(t0));
		for (int s = 0; s < r.steps; s++) {
			CK(cudaMemcpyAsync(d_zframe, hz + 2 * (size_t)(s % FRAMES) * n, (size_t)n * sizeof(float2),
					   cudaMemcpyHostToDevice));
			kf_step_soa<<<grid, block>>>(d_soa, d_zframe, n, DT, Q);
			CK(cudaMemcpyAsync(hx, d_soa, (size_t)n * KF_NX * sizeof(float), cudaMemcpyDeviceToHost));
		}
		CK(cudaEventRecord(t1));
		CK(cudaEventSynchronize(t1));
		CK(cudaGetLastError());
		CK(cudaEventElapsedTime(&ms, t0, t1));
		r.soa_e2e_us = 1000.0 * ms / r.steps;

		CK(cudaEventDestroy(t0));
		CK(cudaEventDestroy(t1));
	}

	CK(cudaFree(d_aos));
	CK(cudaFree(d_soa));
	CK(cudaFree(d_z));
	CK(cudaFree(d_zframe));
	CK(cudaFreeHost(hz));
	CK(cudaFreeHost(hx));
	return r;
}

/* ------------------------------------------------------------------ main */

static std::vector<int> parse_sizes(const char *s)
{
	std::vector<int> v;
	while (*s) {
		char *end;
		long x = strtol(s, &end, 10);
		if (end == s || x <= 0) {
			fprintf(stderr, "bad --sizes\n");
			exit(2);
		}
		v.push_back((int)x);
		s = (*end == ',') ? end + 1 : end;
	}
	return v;
}

int main(int argc, char **argv)
{
	std::vector<int> sizes = {64, 256, 1024, 4096, 16384, 65536, 262144};
	int block = BLOCK;
	bool profile = false, sizes_given = false;
	const char *csv = nullptr;

	for (int a = 1; a < argc; a++) {
		std::string s = argv[a];
		if (s == "--sizes" && a + 1 < argc) {
			sizes = parse_sizes(argv[++a]);
			sizes_given = true;
		} else if (s == "--block" && a + 1 < argc) {
			block = atoi(argv[++a]);
		} else if (s == "--csv" && a + 1 < argc) {
			csv = argv[++a];
		} else if (s == "--profile") {
			profile = true;
		} else {
			fprintf(stderr, "usage: %s [--sizes 64,1024,...] [--block 256] [--csv out.csv] [--profile]\n",
				argv[0]);
			return 2;
		}
	}
	if (block <= 0 || block > BLOCK || block % 32) {
		fprintf(stderr, "--block must be a multiple of 32 in 32..%d (kernels are built with __launch_bounds__(%d))\n",
			BLOCK, BLOCK);
		return 2;
	}
	if (profile && !sizes_given) {
		sizes = {65536, 262144};
	}

	cudaDeviceProp prop;
	CK(cudaGetDeviceProperties(&prop, 0));
	printf("GPU: %s, sm_%d%d, %d SMs, %.0f MHz | block %d | %zu B/track | %s\n", prop.name, prop.major,
	       prop.minor, prop.multiProcessorCount, prop.clockRate / 1000.0, block, sizeof(TrackAoS),
	       profile ? "PROFILE MODE (no timing)" : "timing mode");
	if (!profile) {
		printf("%9s %6s %12s %12s %12s %12s %9s %10s %10s\n", "N", "steps", "CPU us", "GPU AoS us",
		       "GPU SoA us", "SoA e2e us", "CPU/SoA", "err AoS", "err SoA");
	}

	FILE *out = nullptr;
	if (csv) {
		out = fopen(csv, "w");
		if (!out) {
			perror(csv);
			return 1;
		}
		fprintf(out, "n,steps,cpu_us,gpu_aos_us,gpu_soa_us,gpu_soa_e2e_us,err_aos,err_soa\n");
	}

	const double TOL = 1e-3;
	bool all_ok = true;
	int cross_kernel = -1, cross_e2e = -1;
	for (size_t j = 0; j < sizes.size(); j++) {
		Result r = run_size(sizes[j], block, profile);
		bool ok = r.err_aos < TOL && r.err_soa < TOL;
		all_ok = all_ok && ok;
		if (profile) {
			printf("N=%d: %d launches per layout, err AoS %.1e SoA %.1e %s\n", r.n, r.steps, r.err_aos,
			       r.err_soa, ok ? "OK" : "MISMATCH");
			continue;
		}
		printf("%9d %6d %12.2f %12.2f %12.2f %12.2f %8.2fx %10.1e %10.1e%s\n", r.n, r.steps, r.cpu_us,
		       r.aos_us, r.soa_us, r.soa_e2e_us, r.cpu_us / r.soa_us, r.err_aos, r.err_soa,
		       ok ? "" : "  MISMATCH");
		if (out) {
			fprintf(out, "%d,%d,%.3f,%.3f,%.3f,%.3f,%.3e,%.3e\n", r.n, r.steps, r.cpu_us, r.aos_us,
				r.soa_us, r.soa_e2e_us, r.err_aos, r.err_soa);
		}
		if (cross_kernel < 0 && r.soa_us < r.cpu_us) {
			cross_kernel = r.n;
		}
		if (cross_e2e < 0 && r.soa_e2e_us < r.cpu_us) {
			cross_e2e = r.n;
		}
	}
	if (out) {
		fclose(out);
		printf("wrote %s\n", csv);
	}
	if (!profile) {
		printf("\nsmallest N in this sweep where GPU SoA beats one CPU core: kernel only %s, incl. transfers %s\n",
		       cross_kernel > 0 ? std::to_string(cross_kernel).c_str() : "none",
		       cross_e2e > 0 ? std::to_string(cross_e2e).c_str() : "none");
	}
	printf("%s (GPU vs CPU reference, tolerance %.0e)\n", all_ok ? "CORRECTNESS OK" : "CORRECTNESS FAILED", TOL);
	return all_ok ? 0 : 1;
}
