/*
 * Constant-velocity Kalman filter, hand-expanded for one track.
 *
 * Shared by the CUDA kernels (kalman_tracker.cu), their CPU reference, and the
 * host-only checker (verify_math.cpp). Same model as tx2/tracker.py and
 * python/reference_kalman.py:
 *
 *   state   x = [lat, dist, v_lat, v_dist]            (m, m/s)
 *   F       = [[1,0,dt,0],[0,1,0,dt],[0,0,1,0],[0,0,0,1]]
 *   Q       = q * per-axis [[dt^4/4, dt^3/2],[dt^3/2, dt^2]]   (white-noise acceleration)
 *   H       = [[1,0,0,0],[0,1,0,0]]                    (we measure lat, dist)
 *   R       = diag(r_lat, r_dist)
 *
 * No general matrix multiply: F and H are mostly zeros/ones, so every product is
 * written out term by term. P is symmetric, so only its upper triangle is stored
 * (10 floats, order below) - it stays exactly symmetric by construction and a
 * track is 14 floats (56 bytes) instead of 20.
 *
 * Everything reads a snapshot of the old P into locals before writing, so the
 * in-place updates never read a value they already overwrote.
 *
 * Templated on the scalar so verify_math.cpp can run it in float and double.
 * C++11, CUDA 10.2 compatible.
 */
#ifndef KALMAN_MATH_H
#define KALMAN_MATH_H

#if defined(__CUDACC__)
#define KF_HD __host__ __device__ __forceinline__
#else
#define KF_HD inline
#endif

/* upper-triangle storage order of P */
enum {
	KF_P00 = 0, KF_P01, KF_P02, KF_P03,
	KF_P11, KF_P12, KF_P13,
	KF_P22, KF_P23,
	KF_P33,
	KF_NP /* 10 */
};
enum { KF_NX = 4, KF_NS = KF_NX + KF_NP /* 14 floats per track */ };

/* Model constants, identical to tx2/tracker.py. Plain (double) literals, always used
 * as T(KF_...): folded to a float constant at compile time in the float path, and
 * exactly the Python values in the double path (0.08f != 0.08). */
#define KF_ACCEL_NOISE    3.0   /* m/s^2 -> q = 9 */
#define KF_R_LAT          0.3   /* m */
#define KF_R_DIST_FRAC    0.08  /* distance std grows with distance (monocular) */
#define KF_R_DIST_MIN     0.2   /* m */
#define KF_INIT_V_LAT_SD  4.0   /* m/s, unknown velocity at birth */
#define KF_INIT_V_DIST_SD 10.0

/* Measurement variances for a detection at distance `dist` (matches tracker.meas_cov). */
template <typename T>
KF_HD void kf_meas_var(T dist, T *r_lat, T *r_dist)
{
	T sd = (dist < T(0) ? -dist : dist) * T(KF_R_DIST_FRAC);
	if (sd < T(KF_R_DIST_MIN)) {
		sd = T(KF_R_DIST_MIN);
	}
	*r_lat = T(KF_R_LAT) * T(KF_R_LAT);
	*r_dist = sd * sd;
}

/* New track from its first measurement: position = z, velocity unknown. */
template <typename T>
KF_HD void kf_init(T *x, T *p, T z_lat, T z_dist, T r_lat, T r_dist, T v_lat_var, T v_dist_var)
{
	x[0] = z_lat;
	x[1] = z_dist;
	x[2] = T(0);
	x[3] = T(0);
	for (int k = 0; k < KF_NP; k++) {
		p[k] = T(0);
	}
	p[KF_P00] = r_lat;
	p[KF_P11] = r_dist;
	p[KF_P22] = v_lat_var;
	p[KF_P33] = v_dist_var;
}

/* x <- F x ;  P <- F P F^T + Q */
template <typename T>
KF_HD void kf_predict(T *x, T *p, T dt, T q)
{
	const T dt2 = dt * dt;
	const T q_pp = q * dt2 * dt2 * T(0.25); /* q dt^4/4 */
	const T q_pv = q * dt2 * dt * T(0.5);   /* q dt^3/2 */
	const T q_vv = q * dt2;                 /* q dt^2   */

	x[0] += dt * x[2];
	x[1] += dt * x[3];

	const T p00 = p[KF_P00], p01 = p[KF_P01], p02 = p[KF_P02], p03 = p[KF_P03];
	const T p11 = p[KF_P11], p12 = p[KF_P12], p13 = p[KF_P13];
	const T p22 = p[KF_P22], p23 = p[KF_P23];
	const T p33 = p[KF_P33];

	/* (F P F^T)_ij, with row_i(F) = e_i + dt*e_{i+2} for i<2 and e_i otherwise */
	p[KF_P00] = p00 + T(2) * dt * p02 + dt2 * p22 + q_pp;
	p[KF_P01] = p01 + dt * (p03 + p12) + dt2 * p23;
	p[KF_P02] = p02 + dt * p22 + q_pv;
	p[KF_P03] = p03 + dt * p23;
	p[KF_P11] = p11 + T(2) * dt * p13 + dt2 * p33 + q_pp;
	p[KF_P12] = p12 + dt * p23;
	p[KF_P13] = p13 + dt * p33 + q_pv;
	p[KF_P22] = p22 + q_vv;
	/* p23 unchanged: velocities are uncorrelated in Q and F leaves them alone */
	p[KF_P33] = p33 + q_vv;
}

/*
 * Measurement update with z = (lat, dist), R = diag(r_lat, r_dist).
 *   S = H P H^T + R           (2x2, closed-form inverse)
 *   K = P H^T S^-1            (4x2)
 *   x <- x + K (z - H x)
 *   P <- (I - K H) P  =  P - K [row0(P); row1(P)]
 * Returns false (and changes nothing) if S is not positive definite.
 */
template <typename T>
KF_HD bool kf_update(T *x, T *p, T z_lat, T z_dist, T r_lat, T r_dist)
{
	const T p00 = p[KF_P00], p01 = p[KF_P01], p02 = p[KF_P02], p03 = p[KF_P03];
	const T p11 = p[KF_P11], p12 = p[KF_P12], p13 = p[KF_P13];
	const T p22 = p[KF_P22], p23 = p[KF_P23];
	const T p33 = p[KF_P33];

	const T s00 = p00 + r_lat;
	const T s01 = p01;
	const T s11 = p11 + r_dist;
	const T det = s00 * s11 - s01 * s01;
	if (!(det > T(0)) || !(s00 > T(0))) {
		return false;
	}
	const T inv = T(1) / det;
	const T i00 = s11 * inv;
	const T i01 = -s01 * inv;
	const T i11 = s00 * inv;

	/* K = [P col0, P col1] * S^-1 ; P col0 = (p00,p01,p02,p03), col1 = (p01,p11,p12,p13) */
	const T k00 = p00 * i00 + p01 * i01, k01 = p00 * i01 + p01 * i11;
	const T k10 = p01 * i00 + p11 * i01, k11 = p01 * i01 + p11 * i11;
	const T k20 = p02 * i00 + p12 * i01, k21 = p02 * i01 + p12 * i11;
	const T k30 = p03 * i00 + p13 * i01, k31 = p03 * i01 + p13 * i11;

	const T y0 = z_lat - x[0];
	const T y1 = z_dist - x[1];
	x[0] += k00 * y0 + k01 * y1;
	x[1] += k10 * y0 + k11 * y1;
	x[2] += k20 * y0 + k21 * y1;
	x[3] += k30 * y0 + k31 * y1;

	/* P'_ij = P_ij - K_i0 * P_0j - K_i1 * P_1j   (i <= j) */
	p[KF_P00] = p00 - k00 * p00 - k01 * p01;
	p[KF_P01] = p01 - k00 * p01 - k01 * p11;
	p[KF_P02] = p02 - k00 * p02 - k01 * p12;
	p[KF_P03] = p03 - k00 * p03 - k01 * p13;
	p[KF_P11] = p11 - k10 * p01 - k11 * p11;
	p[KF_P12] = p12 - k10 * p02 - k11 * p12;
	p[KF_P13] = p13 - k10 * p03 - k11 * p13;
	p[KF_P22] = p22 - k20 * p02 - k21 * p12;
	p[KF_P23] = p23 - k20 * p03 - k21 * p13;
	p[KF_P33] = p33 - k30 * p03 - k31 * p13;
	return true;
}

/* One tracker frame for one track: predict by dt, then fold in measurement z. */
template <typename T>
KF_HD void kf_step(T *x, T *p, T dt, T q, T z_lat, T z_dist)
{
	T r_lat, r_dist;

	kf_meas_var(z_dist, &r_lat, &r_dist);
	kf_predict(x, p, dt, q);
	kf_update(x, p, z_lat, z_dist, r_lat, r_dist);
}

#endif /* KALMAN_MATH_H */
