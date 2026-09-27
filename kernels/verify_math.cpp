/*
 * Host-only check of the hand-expanded Kalman math in kalman_math.h.
 *
 *   g++ -O2 -std=c++11 verify_math.cpp -o verify_math && ./verify_math     -> "MATH OK"
 *   ./verify_math --check replay.txt   (replay from python/reference_kalman.py --dump)
 *
 * 1. Random predict/update steps: hand-expanded (double and float) vs a naive
 *    full-matrix implementation in double written independently below.
 * 2. One constant-velocity target: is the velocity recovered?
 * 3. 1000 tracks x 200 frames in float through the real noise model: velocity
 *    error vs truth, divergence from the double-precision naive filter, and P
 *    staying positive definite.
 * 4. (--check) step-by-step replay against the NumPy reference.
 * Every number printed is measured by this run.
 */
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <random>
#include <string>

#include "kalman_math.h"

/* ------------------------------------------------ naive reference (double, full 4x4) */

struct M4 {
	double a[4][4];
};

static M4 mul(const M4 &A, const M4 &B)
{
	M4 C;
	for (int i = 0; i < 4; i++) {
		for (int j = 0; j < 4; j++) {
			double s = 0;
			for (int k = 0; k < 4; k++) {
				s += A.a[i][k] * B.a[k][j];
			}
			C.a[i][j] = s;
		}
	}
	return C;
}

static M4 transpose(const M4 &A)
{
	M4 T;
	for (int i = 0; i < 4; i++) {
		for (int j = 0; j < 4; j++) {
			T.a[i][j] = A.a[j][i];
		}
	}
	return T;
}

struct Naive {
	double x[4];
	M4 P;

	void predict(double dt, double q)
	{
		M4 F = {{{1, 0, dt, 0}, {0, 1, 0, dt}, {0, 0, 1, 0}, {0, 0, 0, 1}}};
		double nx[4];
		for (int i = 0; i < 4; i++) {
			nx[i] = 0;
			for (int k = 0; k < 4; k++) {
				nx[i] += F.a[i][k] * x[k];
			}
		}
		memcpy(x, nx, sizeof(x));
		P = mul(mul(F, P), transpose(F));
		const double a = q * pow(dt, 4) / 4, b = q * pow(dt, 3) / 2, c = q * dt * dt;
		P.a[0][0] += a;
		P.a[1][1] += a;
		P.a[0][2] += b;
		P.a[2][0] += b;
		P.a[1][3] += b;
		P.a[3][1] += b;
		P.a[2][2] += c;
		P.a[3][3] += c;
	}

	void update(double z0, double z1, double r0, double r1)
	{
		/* H selects rows/cols 0,1 */
		double S[2][2] = {{P.a[0][0] + r0, P.a[0][1]}, {P.a[1][0], P.a[1][1] + r1}};
		double det = S[0][0] * S[1][1] - S[0][1] * S[1][0];
		double Si[2][2] = {{S[1][1] / det, -S[0][1] / det}, {-S[1][0] / det, S[0][0] / det}};
		double K[4][2];
		for (int i = 0; i < 4; i++) {
			for (int j = 0; j < 2; j++) {
				K[i][j] = P.a[i][0] * Si[0][j] + P.a[i][1] * Si[1][j];
			}
		}
		double y[2] = {z0 - x[0], z1 - x[1]};
		for (int i = 0; i < 4; i++) {
			x[i] += K[i][0] * y[0] + K[i][1] * y[1];
		}
		M4 IKH;
		for (int i = 0; i < 4; i++) {
			for (int j = 0; j < 4; j++) {
				double kh = (j < 2) ? K[i][j] : 0.0;
				IKH.a[i][j] = (i == j ? 1.0 : 0.0) - kh;
			}
		}
		P = mul(IKH, P);
	}
};

static const int UP_I[KF_NP] = {0, 0, 0, 0, 1, 1, 1, 2, 2, 3};
static const int UP_J[KF_NP] = {0, 1, 2, 3, 1, 2, 3, 2, 3, 3};

template <typename T>
static void to_naive(const T *x, const T *p, Naive &n)
{
	for (int i = 0; i < 4; i++) {
		n.x[i] = x[i];
	}
	for (int k = 0; k < KF_NP; k++) {
		n.P.a[UP_I[k]][UP_J[k]] = p[k];
		n.P.a[UP_J[k]][UP_I[k]] = p[k];
	}
}

/* max relative difference between a hand-expanded state and the naive one */
template <typename T>
static double rel_diff(const T *x, const T *p, const Naive &n)
{
	double m = 0;
	for (int i = 0; i < 4; i++) {
		m = fmax(m, fabs((double)x[i] - n.x[i]) / fmax(1.0, fabs(n.x[i])));
	}
	for (int k = 0; k < KF_NP; k++) {
		double ref = n.P.a[UP_I[k]][UP_J[k]];
		m = fmax(m, fabs((double)p[k] - ref) / fmax(1.0, fabs(ref)));
	}
	return m;
}

/* positive definite via Cholesky on the full symmetric matrix */
template <typename T>
static bool is_pd(const T *p)
{
	double A[4][4], L[4][4] = {{0}};
	for (int k = 0; k < KF_NP; k++) {
		A[UP_I[k]][UP_J[k]] = A[UP_J[k]][UP_I[k]] = p[k];
	}
	for (int i = 0; i < 4; i++) {
		for (int j = 0; j <= i; j++) {
			double s = A[i][j];
			for (int k = 0; k < j; k++) {
				s -= L[i][k] * L[j][k];
			}
			if (i == j) {
				if (s <= 0) {
					return false;
				}
				L[i][i] = sqrt(s);
			} else {
				L[i][j] = s / L[j][j];
			}
		}
	}
	return true;
}

/* ------------------------------------------------ tests */

static int failures;

static void check(bool ok, const char *what)
{
	printf("%s  %s\n", ok ? "ok  " : "FAIL", what);
	if (!ok) {
		failures++;
	}
}

static void random_spd(std::mt19937 &g, double *p10)
{
	std::uniform_real_distribution<double> u(-1, 1);
	double A[4][4];
	for (int i = 0; i < 4; i++) {
		for (int j = 0; j < 4; j++) {
			A[i][j] = u(g) * 3;
		}
	}
	for (int k = 0; k < KF_NP; k++) {
		double s = 0;
		for (int m = 0; m < 4; m++) {
			s += A[UP_I[k]][m] * A[UP_J[k]][m];
		}
		p10[k] = s + (UP_I[k] == UP_J[k] ? 0.1 : 0.0);
	}
}

static void test_random_steps()
{
	std::mt19937 g(42);
	std::uniform_real_distribution<double> u(-1, 1);
	double worst_d = 0, worst_f = 0;

	for (int t = 0; t < 20000; t++) {
		double xd[4], pd[KF_NP];
		for (int i = 0; i < 4; i++) {
			xd[i] = u(g) * 50;
		}
		random_spd(g, pd);
		double dt = 0.005 + 0.2 * (u(g) + 1), q = 20 * (u(g) + 1);
		double z0 = u(g) * 50, z1 = u(g) * 50, r0 = 0.01 + (u(g) + 1), r1 = 0.01 + 5 * (u(g) + 1);

		float xf[4], pf[KF_NP];
		for (int i = 0; i < 4; i++) {
			xf[i] = (float)xd[i];
		}
		for (int k = 0; k < KF_NP; k++) {
			pf[k] = (float)pd[k];
		}
		Naive n;
		to_naive(xd, pd, n);

		kf_predict(xd, pd, dt, q);
		kf_update(xd, pd, z0, z1, r0, r1);
		kf_predict(xf, pf, (float)dt, (float)q);
		kf_update(xf, pf, (float)z0, (float)z1, (float)r0, (float)r1);
		n.predict(dt, q);
		n.update(z0, z1, r0, r1);

		worst_d = fmax(worst_d, rel_diff(xd, pd, n));
		worst_f = fmax(worst_f, rel_diff(xf, pf, n));
	}
	printf("20000 random predict+update steps vs naive full-matrix (double):\n");
	printf("      hand-expanded double: max rel diff %.2e\n", worst_d);
	printf("      hand-expanded float:  max rel diff %.2e\n", worst_f);
	check(worst_d < 1e-9, "double hand expansion matches naive matrices (< 1e-9)");
	check(worst_f < 1e-3, "float hand expansion matches naive matrices (< 1e-3)");
}

static void test_single_track()
{
	/* exactly constant velocity, fixed small noise, near-zero q: tests the math */
	std::mt19937 g(1);
	std::normal_distribution<float> nz(0.0f, 0.1f);
	float pos[2] = {0.0f, 20.0f};
	const float v[2] = {5.0f, 2.0f}, dt = 0.1f, r = 0.01f, q = 0.01f;
	float x[4], p[KF_NP];

	kf_init(x, p, pos[0] + nz(g), pos[1] + nz(g), r, r, 16.0f, 100.0f);
	for (int k = 1; k < 60; k++) {
		pos[0] += v[0] * dt;
		pos[1] += v[1] * dt;
		kf_predict(x, p, dt, q);
		kf_update(x, p, pos[0] + nz(g), pos[1] + nz(g), r, r);
	}
	printf("single track, 60 frames @ 0.1 s, 0.1 m noise, q=0.01 (float): velocity %.3f / %.3f (true 5.000 / 2.000)\n",
	       x[2], x[3]);
	check(fabs(x[2] - 5.0f) < 0.2f && fabs(x[3] - 2.0f) < 0.2f, "velocity recovered within 0.2 m/s");
}

static void test_bank()
{
	std::mt19937 g(7);
	std::uniform_real_distribution<float> u(0, 1);
	std::normal_distribution<float> n01(0, 1);
	const int N = 1000, FR = 200;
	const float dt = 1.0f / 30, q = (float)(KF_ACCEL_NOISE * KF_ACCEL_NOISE);
	double err_sum = 0, err_max = 0, div_max = 0;
	int not_pd = 0;

	for (int t = 0; t < N; t++) {
		float pos[2] = {-10 + 20 * u(g), 10 + 50 * u(g)};
		float v[2] = {-2 + 4 * u(g), -10 + 13 * u(g)};
		float rl, rd;
		kf_meas_var(pos[1], &rl, &rd);
		float z0 = pos[0] + sqrtf(rl) * n01(g), z1 = pos[1] + sqrtf(rd) * n01(g);
		float x[4], p[KF_NP];
		kf_init(x, p, z0, z1, rl, rd, (float)(KF_INIT_V_LAT_SD * KF_INIT_V_LAT_SD),
			(float)(KF_INIT_V_DIST_SD * KF_INIT_V_DIST_SD));
		Naive ref;
		to_naive(x, p, ref);

		for (int k = 1; k < FR; k++) {
			pos[0] += v[0] * dt;
			pos[1] += v[1] * dt;
			kf_meas_var(pos[1], &rl, &rd);
			z0 = pos[0] + sqrtf(rl) * n01(g);
			z1 = pos[1] + sqrtf(rd) * n01(g);
			kf_step(x, p, dt, q, z0, z1);
			float rl2, rd2;
			kf_meas_var(z1, &rl2, &rd2);
			ref.predict(dt, q);
			ref.update(z0, z1, rl2, rd2);
		}
		double e = hypot(x[2] - v[0], x[3] - v[1]);
		err_sum += e;
		err_max = fmax(err_max, e);
		div_max = fmax(div_max, rel_diff(x, p, ref));
		if (!is_pd(p)) {
			not_pd++;
		}
	}
	printf("1000 tracks x 200 frames @ 30 Hz, monocular noise model (float):\n");
	printf("      velocity error vs truth: mean %.3f m/s, max %.3f m/s\n", err_sum / N, err_max);
	printf("      max rel divergence from double naive filter: %.2e\n", div_max);
	printf("      covariances not positive definite: %d / %d\n", not_pd, N);
	check(div_max < 1e-3, "float bank stays within 1e-3 of the double naive filter");
	check(not_pd == 0, "every P positive definite after 200 frames");
}

static int check_replay(const char *path)
{
	FILE *f = fopen(path, "r");
	if (!f) {
		perror(path);
		return 1;
	}
	char line[4096];
	double x[4] = {0}, p[KF_NP] = {0};
	double worst = 0;
	long n = 0;
	while (fgets(line, sizeof(line), f)) {
		double a[6], e[KF_NS];
		if (!strncmp(line, "init ", 5)) {
			if (sscanf(line + 5, "%lf %lf %lf %lf %lf %lf", &a[0], &a[1], &a[2], &a[3], &a[4], &a[5]) != 6) {
				continue;
			}
			kf_init(x, p, a[0], a[1], a[2], a[3], a[4], a[5]);
		} else if (!strncmp(line, "step ", 5)) {
			if (sscanf(line + 5, "%lf %lf %lf %lf", &a[0], &a[1], &a[2], &a[3]) != 4) {
				continue;
			}
			kf_step(x, p, a[0], a[1], a[2], a[3]);
		} else {
			continue;
		}
		const char *bar = strchr(line, '|');
		if (!bar) {
			continue;
		}
		char *s = (char *)bar + 1;
		for (int k = 0; k < KF_NS; k++) {
			e[k] = strtod(s, &s);
		}
		for (int k = 0; k < KF_NS; k++) {
			double got = k < 4 ? x[k] : p[k - 4];
			worst = fmax(worst, fabs(got - e[k]) / fmax(1.0, fabs(e[k])));
		}
		n++;
	}
	fclose(f);
	printf("replay %s: %ld states, max rel diff vs NumPy reference %.2e\n", path, n, worst);
	check(n > 0 && worst < 1e-9, "hand-expanded double matches python/reference_kalman.py (< 1e-9)");
	return failures ? 1 : 0;
}

int main(int argc, char **argv)
{
	if (argc == 3 && std::string(argv[1]) == "--check") {
		int rc = check_replay(argv[2]);
		printf(rc ? "MATH FAILED\n" : "MATH OK\n");
		return rc;
	}
	test_random_steps();
	test_single_track();
	test_bank();
	if (failures) {
		printf("MATH FAILED (%d)\n", failures);
	} else {
		printf("MATH OK\n");
	}
	return failures ? 1 : 0;
}
