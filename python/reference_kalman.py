#!/usr/bin/env python3
"""Reference constant-velocity Kalman filter: plain NumPy, full matrices, float64.

Deliberately naive (generic matrix products, no hand expansion) so it is an
independent check on kernels/kalman_math.h, which the CUDA kernels use.
Same model as tx2/tracker.py: x = [lat, dist, v_lat, v_dist], z = [lat, dist].

  python3 python/reference_kalman.py                 # self-test, prints measured numbers
  python3 python/reference_kalman.py --dump f.txt    # write a replay file for kernels/verify_math

Python 3.6 / NumPy 1.13 compatible.
"""
import argparse
import sys

import numpy as np

ACCEL_NOISE = 3.0
R_LAT = 0.3
R_DIST_FRAC = 0.08
R_DIST_MIN = 0.2
INIT_V_LAT_SD = 4.0
INIT_V_DIST_SD = 10.0
Q_DEFAULT = ACCEL_NOISE ** 2

H = np.array([[1.0, 0, 0, 0], [0, 1.0, 0, 0]])


def meas_var(dist):
    sd = max(R_DIST_MIN, R_DIST_FRAC * abs(dist))
    return R_LAT ** 2, sd ** 2


def F_mat(dt):
    return np.array([[1.0, 0, dt, 0],
                     [0, 1.0, 0, dt],
                     [0, 0, 1.0, 0],
                     [0, 0, 0, 1.0]])


def Q_mat(dt, q):
    a, b, c = dt ** 4 / 4.0, dt ** 3 / 2.0, dt ** 2
    return q * np.array([[a, 0, b, 0],
                         [0, a, 0, b],
                         [b, 0, c, 0],
                         [0, b, 0, c]])


def init(z, r, v_var=(INIT_V_LAT_SD ** 2, INIT_V_DIST_SD ** 2)):
    x = np.array([z[0], z[1], 0.0, 0.0])
    P = np.diag([r[0], r[1], v_var[0], v_var[1]])
    return x, P


def predict(x, P, dt, q=Q_DEFAULT):
    F = F_mat(dt)
    return F.dot(x), F.dot(P).dot(F.T) + Q_mat(dt, q)


def update(x, P, z, r):
    R = np.diag(r)
    S = H.dot(P).dot(H.T) + R
    K = P.dot(H.T).dot(np.linalg.inv(S))
    x = x + K.dot(np.asarray(z) - H.dot(x))
    P = (np.eye(4) - K.dot(H)).dot(P)
    return x, P


def step(x, P, z, dt, q=Q_DEFAULT):
    x, P = predict(x, P, dt, q)
    return update(x, P, z, meas_var(z[1]))


UPPER = [(0, 0), (0, 1), (0, 2), (0, 3), (1, 1), (1, 2), (1, 3), (2, 2), (2, 3), (3, 3)]


def upper(P):
    return [P[i, j] for i, j in UPPER]


# ---------------------------------------------------------------- self-test

def single_track_test(rng, frames=60, dt=0.1, noise=0.1, v_true=(5.0, 2.0), q=0.01):
    """One target, exactly constant velocity, small fixed measurement noise: does the
    filter recover the velocity? Tests the math, not the tuning, so q is near zero -
    the correct model for a target that never accelerates. (The tracker's q=9 is for
    real manoeuvres and would, correctly, keep the estimate noisier here.)"""
    pos = np.array([0.0, 20.0])
    v = np.array(v_true)
    r = (noise ** 2, noise ** 2)
    x, P = init(pos + rng.normal(0, noise, 2), r)
    for _ in range(frames - 1):
        pos = pos + v * dt
        x, P = predict(x, P, dt, q)
        x, P = update(x, P, pos + rng.normal(0, noise, 2), r)
    return x


def bank_test(rng, n=1000, frames=200, dt=1.0 / 30):
    """Many targets through the real (distance-dependent) noise model."""
    errs = []
    worst_asym = 0.0
    min_eig = np.inf
    for _ in range(n):
        pos = np.array([rng.uniform(-10, 10), rng.uniform(10, 60)])
        v = np.array([rng.uniform(-2, 2), rng.uniform(-10, 3)])
        rl, rd = meas_var(pos[1])
        x, P = init(pos + rng.normal(0, [np.sqrt(rl), np.sqrt(rd)]), (rl, rd))
        for _ in range(frames - 1):
            pos = pos + v * dt
            rl, rd = meas_var(pos[1])
            z = pos + rng.normal(0, [np.sqrt(rl), np.sqrt(rd)])
            x, P = step(x, P, z, dt)
        errs.append(np.linalg.norm(x[2:] - v))
        worst_asym = max(worst_asym, np.abs(P - P.T).max())
        min_eig = min(min_eig, np.linalg.eigvalsh(0.5 * (P + P.T)).min())
    return float(np.mean(errs)), float(np.max(errs)), worst_asym, min_eig


def _nums(vals):
    # float() first: newer NumPy reprs scalars as "np.float64(...)"; %.17g round-trips exactly
    return " ".join("%.17g" % float(v) for v in vals)


def dump(path, rng, n_tracks=20, frames=100, dt=1.0 / 30):
    """Replay file for kernels/verify_math --check: every init and every step with the
    reference result, so the hand-expanded C++ can be compared line by line."""
    with open(path, "w") as f:
        f.write("# reference_kalman.py replay: q=%r\n" % Q_DEFAULT)
        for _ in range(n_tracks):
            pos = np.array([rng.uniform(-10, 10), rng.uniform(5, 80)])
            v = np.array([rng.uniform(-3, 3), rng.uniform(-15, 5)])
            r = meas_var(pos[1])
            z = pos + rng.normal(0, 0.5, 2)
            x, P = init(z, r)
            f.write("init %s | %s\n" % (
                _nums([z[0], z[1], r[0], r[1], INIT_V_LAT_SD ** 2, INIT_V_DIST_SD ** 2]),
                _nums(list(x) + upper(P))))
            for _ in range(frames):
                pos = pos + v * dt
                z = pos + rng.normal(0, 0.5, 2)
                x, P = step(x, P, z, dt)
                f.write("step %s | %s\n" % (_nums([dt, Q_DEFAULT, z[0], z[1]]), _nums(list(x) + upper(P))))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", metavar="FILE", help="write a replay file for kernels/verify_math --check")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = np.random.RandomState(args.seed)

    if args.dump:
        dump(args.dump, rng)
        print("wrote %s" % args.dump)
        return

    x = single_track_test(rng)
    print("single track, 60 frames @ 0.1 s, 0.1 m noise, q=0.01: velocity estimate %.3f / %.3f (true 5.000 / 2.000)"
          % (x[2], x[3]))
    ok1 = abs(x[2] - 5.0) < 0.2 and abs(x[3] - 2.0) < 0.2

    mean_err, max_err, asym, min_eig = bank_test(rng)
    print("1000 tracks x 200 frames @ 30 Hz, monocular noise model: velocity error mean %.3f m/s, max %.3f m/s"
          % (mean_err, max_err))
    print("covariance: max |P - P^T| %.2e, min eigenvalue %.2e" % (asym, min_eig))
    ok2 = min_eig > 0 and asym < 1e-9

    print("REFERENCE OK" if ok1 and ok2 else "REFERENCE FAILED")
    sys.exit(0 if ok1 and ok2 else 1)


if __name__ == "__main__":
    main()
