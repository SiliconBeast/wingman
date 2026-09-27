#!/usr/bin/env python3
"""Tracker sanity: a pedestrian approaching at a known speed through noisy monocular
detections, plus clutter. Checks the closing-speed estimate, id stability, confirmation
and deletion.  python3 tests/test_tracker.py   (needs numpy)
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tx2"))
from tracker import MAX_MISSES, MIN_HITS, Detection, Tracker  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok    " if cond else "FAIL  ") + msg)
    fails += 0 if cond else 1


rng = random.Random(3)
tr = Tracker()
dt = 1 / 30.0
ids = set()
out = []
for k in range(150):                               # 5 s
    t = k * dt
    dist = 15.0 - 1.5 * t
    d = [Detection(1, 0.9, 0.4 + rng.gauss(0, 0.1), dist * (1 + rng.gauss(0, 0.03)))]
    d.append(Detection(2, 0.8, 3.5 + rng.gauss(0, 0.1), 20 * (1 + rng.gauss(0, 0.03))))
    out = tr.step(d, dt)
    if k == MIN_HITS - 2:
        check(not out, "nothing published before %d hits" % MIN_HITS)
    ids.update(x.id for x in out if x.cls == 1)

ped = [x for x in out if x.cls == 1][0]
car = [x for x in out if x.cls == 2][0]
check(len(ids) == 1, "pedestrian keeps one id (%s)" % sorted(ids))
check(abs(ped.closing - 1.5) < 0.3, "pedestrian closing %.2f m/s (true 1.5)" % ped.closing)
check(abs(ped.dist - (15 - 1.5 * 149 * dt)) < 0.6, "pedestrian distance %.2f m" % ped.dist)
check(abs(car.closing) < 0.3, "parked car closing %.2f m/s (true 0)" % car.closing)

for _ in range(MAX_MISSES + 1):
    tr.step([], dt)
check(not tr.tracks, "tracks deleted after %d misses" % MAX_MISSES)

print("ALL TRACKER TESTS PASSED" if not fails else "%d FAILURE(S)" % fails)
sys.exit(1 if fails else 0)
