"""Constant-velocity Kalman tracker for monocular detections.

State x = [lat, dist, v_lat, v_dist] (m, m/s), measurement z = [lat, dist].
Greedy nearest-neighbour association (same class), gate 2 m scaled with distance,
birth on unmatched detection, delete after MAX_MISSES, publish after MIN_HITS.
closing speed = -v_dist.

Python 3.6 + NumPy only. Same model as kernels/kalman_math.h (CV, hand-expanded F/H),
so the CUDA bank can replace predict/update later without changing the interface.
"""
import numpy as np

MAX_MISSES = 10
MIN_HITS = 3
GATE_M = 2.0
GATE_FRAC = 0.15            # gate grows with distance: max(GATE_M, GATE_FRAC * dist)

ACCEL_NOISE = 3.0           # m/s^2, white-noise acceleration (process noise)
R_LAT = 0.3                 # m, lateral measurement std
R_DIST_FRAC = 0.08          # distance std grows with distance (monocular)
R_DIST_MIN = 0.2            # m


def meas_cov(dist):
    sd = max(R_DIST_MIN, R_DIST_FRAC * abs(dist))
    return np.diag([R_LAT ** 2, sd ** 2])


class Detection(object):
    __slots__ = ("cls", "conf", "lat", "dist", "box")

    def __init__(self, cls, conf, lat, dist, box=None):
        self.cls, self.conf, self.lat, self.dist, self.box = cls, conf, lat, dist, box


class Track(object):
    _next_id = 1

    def __init__(self, det):
        self.id = Track._next_id
        Track._next_id = Track._next_id % 0xFFFE + 1       # 1..0xFFFE, never 0xFFFF
        self.cls = det.cls
        self.conf = det.conf
        self.box = det.box
        self.x = np.array([det.lat, det.dist, 0.0, 0.0])
        R = meas_cov(det.dist)
        self.P = np.diag([R[0, 0], R[1, 1], 4.0 ** 2, 10.0 ** 2])   # unknown velocity
        self.hits = 1
        self.misses = 0

    def predict(self, dt):
        F = np.array([[1, 0, dt, 0],
                      [0, 1, 0, dt],
                      [0, 0, 1, 0],
                      [0, 0, 0, 1]], dtype=float)
        q = ACCEL_NOISE ** 2
        dt2, dt3, dt4 = dt * dt, dt ** 3, dt ** 4
        Q = q * np.array([[dt4 / 4, 0, dt3 / 2, 0],
                          [0, dt4 / 4, 0, dt3 / 2],
                          [dt3 / 2, 0, dt2, 0],
                          [0, dt3 / 2, 0, dt2]])
        self.x = F.dot(self.x)
        self.P = F.dot(self.P).dot(F.T) + Q

    def update(self, det):
        H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
        z = np.array([det.lat, det.dist])
        y = z - H.dot(self.x)
        S = H.dot(self.P).dot(H.T) + meas_cov(det.dist)
        K = self.P.dot(H.T).dot(np.linalg.inv(S))
        self.x = self.x + K.dot(y)
        I_KH = np.eye(4) - K.dot(H)
        # Joseph form keeps P symmetric positive-definite
        self.P = I_KH.dot(self.P).dot(I_KH.T) + K.dot(meas_cov(det.dist)).dot(K.T)
        self.conf = 0.7 * self.conf + 0.3 * det.conf
        self.box = det.box
        self.hits += 1
        self.misses = 0

    @property
    def lat(self):
        return float(self.x[0])

    @property
    def dist(self):
        return float(self.x[1])

    @property
    def closing(self):
        return float(-self.x[3])

    def confirmed(self):
        return self.hits >= MIN_HITS and self.misses == 0


class Tracker(object):
    def __init__(self):
        self.tracks = []

    def step(self, detections, dt):
        """Advance by dt seconds and fold in this frame's detections. Returns confirmed tracks."""
        dt = max(1e-3, min(dt, 0.5))
        for t in self.tracks:
            t.predict(dt)

        # greedy nearest neighbour over all (track, detection) pairs inside the gate
        pairs = []
        for ti, t in enumerate(self.tracks):
            gate = max(GATE_M, GATE_FRAC * abs(t.dist))
            for di, d in enumerate(detections):
                if d.cls != t.cls:
                    continue
                e = ((t.lat - d.lat) ** 2 + (t.dist - d.dist) ** 2) ** 0.5
                if e <= gate:
                    pairs.append((e, ti, di))
        pairs.sort(key=lambda p: p[0])
        used_t, used_d = set(), set()
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            self.tracks[ti].update(detections[di])
            used_t.add(ti)
            used_d.add(di)

        for ti, t in enumerate(self.tracks):
            if ti not in used_t:
                t.misses += 1
        self.tracks = [t for t in self.tracks if t.misses <= MAX_MISSES]
        for di, d in enumerate(detections):
            if di not in used_d:
                self.tracks.append(Track(d))
        return [t for t in self.tracks if t.confirmed()]
