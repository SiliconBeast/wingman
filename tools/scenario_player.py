#!/usr/bin/env python3
"""Scripted perception frames -> RT1170, checks the status replies, prints PASS/FAIL.

Stands in for the TX2 camera pipeline so the RT1170 can be proven on its own.
Plain Python 3.6+, no dependencies. Runs on the Odroid (Windows or Linux) or the TX2.

  python3 tools/scenario_player.py --scenario approach
  python3 tools/scenario_player.py --scenario follow --stop-at 2s --repeat 50     # failover x50
  python3 tools/scenario_player.py --scenario approach --drop 0.1 --corrupt-crc 0.05
  python3 tools/scenario_player.py --matrix --md docs/test_matrix.md              # full test matrix

Every run starts with 1 s of empty frames so the RT1170 is NOMINAL (a board left in
FAILSAFE by a previous run needs 10 in-sequence frames to recover). Scenario time t=0
is the end of that preroll. Don't run this and tx2/perception.py at the same time:
both listen on UDP :5006.
"""
import argparse
import csv
import os
import random
import re
import socket
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tx2"))
import wingman_proto as wp  # noqa: E402

PREROLL_S = 1.0
HEARTBEAT_MS = 100          # must match the firmware default
RECOVER_FRAMES = 10         # must match the firmware default
CONF = 230


def T(id_, dist_m, lat_m, closing_ms, cls=wp.CLS_CAR, conf=CONF):
    return wp.Track(id_, cls, conf, dist_m * 1000.0, lat_m * 1000.0, closing_ms * 1000.0)


# ---------------------------------------------------------------- scenarios
# Each: duration (s), tracks(t) -> [Track], and expectations:
#   reach: [(state, t_min, t_max)]  first time state is seen must fall in the window
#   never: states that must never be seen after preroll
#   final: state expected at the end
#   threat_id: if set, every CAUTION/WARNING status must name this track

class Scenario(object):
    def __init__(self, name, desc, duration, tracks, reach=(), never=(), final=None,
                 threat_id=None):
        self.name, self.desc, self.duration, self.tracks = name, desc, duration, tracks
        self.reach, self.never, self.final, self.threat_id = list(reach), set(never), final, threat_id


def _approach(t):
    return [T(1, 40 - 10 * t, 0.0, 10)]


def _cut_in(t):
    if t < 0.5:
        return []
    return [T(7, 8 - 3 * (t - 0.5), 0.3, 3)]


def _lateral_pass(t):
    return [T(3, 10 - 5 * t, 2.0, 5, cls=wp.CLS_PERSON)]


def _follow(t):
    return [T(4, 20 + 0.05 * ((t * 7) % 1 - 0.5), 0.1, 0.05 * ((t * 13) % 2 - 1))]


def _multi(t):
    return [
        T(10, 30, 0.2, 0),                                 # in path, not closing
        T(11, 10 - 15 * t * 0.3, -2.5, 15),                # closing fast, other lane
        T(12, 6, 0.0, 8, conf=40),                         # in path, low confidence
        T(13, 36 - 12 * t, 0.4, 12, cls=wp.CLS_TRUCK),     # the real threat
        T(14, 18, 1.8, 6, cls=wp.CLS_PERSON),              # sidewalk
    ]


SCENARIOS = {
    s.name: s for s in [
        # 40 m closing 10 m/s: TTC 2.5 s at t=1.5, 1.2 s at t=2.8, 3 m at t=3.7
        Scenario("approach", "car from 40 m closing at 10 m/s", 3.8, _approach,
                 reach=[(wp.CAUTION, 1.5, 1.5), (wp.WARNING, 2.8, 2.8)], final=wp.WARNING,
                 threat_id=1),
        Scenario("stationary", "object at 15 m, not moving", 3.0,
                 lambda t: [T(2, 15, 0, 0)], never=[wp.CAUTION, wp.WARNING, wp.FAILSAFE],
                 final=wp.NOMINAL),
        # appears at t=0.5 at 8 m closing 3 m/s: TTC 2.67 s; <2.5 s (7.5 m) at t=0.67;
        # <1.2 s (3.6 m) at t=1.97; <3 m close-range override from t=2.17
        Scenario("cut_in", "track appears at 8 m closing 3 m/s", 2.4, _cut_in,
                 reach=[(wp.CAUTION, 0.67, 0.67), (wp.WARNING, 1.97, 1.97)], final=wp.WARNING,
                 threat_id=7),
        Scenario("lateral_pass", "person 2 m to the side, closing 5 m/s", 1.8, _lateral_pass,
                 never=[wp.CAUTION, wp.WARNING, wp.FAILSAFE], final=wp.NOMINAL),
        Scenario("follow", "matched speed at 20 m (jitter inside deadband)", 3.0, _follow,
                 never=[wp.CAUTION, wp.WARNING, wp.FAILSAFE], final=wp.NOMINAL),
        # threat 13: <30 m (TTC 2.5 s) at t=0.5, <14.4 m (TTC 1.2 s) at t=1.8
        Scenario("multi", "five tracks, only #13 in path and threatening", 2.2, _multi,
                 reach=[(wp.CAUTION, 0.5, 0.5), (wp.WARNING, 1.8, 1.8)], final=wp.WARNING,
                 threat_id=13),
    ]
}


# ---------------------------------------------------------------- run one

def parse_duration(s):
    m = re.match(r"^\s*([\d.]+)\s*(ms|s)?\s*$", s)
    if not m:
        raise argparse.ArgumentTypeError("bad duration %r" % s)
    v = float(m.group(1))
    return v / 1000.0 if m.group(2) == "ms" else v


def parse_burst(s):
    """'200ms@5s' -> (0.2, 5.0)"""
    try:
        length, at = s.split("@")
        return parse_duration(length), parse_duration(at)
    except ValueError:
        raise argparse.ArgumentTypeError("expected LENGTH@TIME, e.g. 200ms@5s")


class Receiver(threading.Thread):
    def __init__(self, sock, mirror=None):
        threading.Thread.__init__(self)
        self.daemon = True
        self.sock = sock
        self.mirror = mirror
        self.lock = threading.Lock()
        self.events = []          # (t_rx_monotonic, Status)
        self.running = True

    def run(self):
        while self.running:
            try:
                data, _ = self.sock.recvfrom(256)
            except socket.timeout:
                continue
            except OSError:
                if not self.running:
                    return
                continue
            t = time.monotonic()
            if self.mirror:
                self.sock.sendto(data, self.mirror)
            st = wp.unpack_status(data)
            if st is not None:
                with self.lock:
                    self.events.append((t, st))

    def take(self):
        with self.lock:
            ev, self.events = self.events, []
        return ev


def sleep_until(t_target):
    while True:
        d = t_target - time.monotonic()
        if d <= 0:
            return
        time.sleep(d - 0.002 if d > 0.003 else 0)


def run_once(sc, args, sock, rx, rng, seq_start):
    period = 1.0 / args.rate
    target = (args.target, args.port)
    seq = seq_start
    n_total = int(round((PREROLL_S + sc.duration) / period))
    stop_at = args.stop_at
    burst = args.burst_loss
    sent = []                     # (t_scn, seq, n_tracks) for frames actually sent
    held = None                   # frame held back for --reorder
    counts = dict(sent=0, dropped=0, corrupted=0, duplicated=0, reordered=0)
    sent_tx = {}                  # seq -> tx_time_us, to match replies for round trips

    rx.take()
    t0 = time.monotonic() + PREROLL_S   # scenario t=0
    t_start = t0 - PREROLL_S
    last_sent_t = None

    for i in range(n_total):
        t_frame = t_start + i * period
        sleep_until(t_frame)
        t_scn = t_frame - t0
        if stop_at is not None and t_scn >= stop_at:
            break
        tracks = sc.tracks(t_scn) if t_scn >= 0 else []
        now_us = int(time.monotonic() * 1e6)
        sent_tx[seq] = now_us
        pkt = wp.pack_perception(seq, now_us, tracks)
        seq = (seq + 1) & 0xFFFFFFFF

        if burst and burst[1] <= t_scn < burst[1] + burst[0]:
            counts["dropped"] += 1
            continue
        if t_scn >= 0 and args.drop and rng.random() < args.drop:
            counts["dropped"] += 1
            continue
        if t_scn >= 0 and args.corrupt_crc and rng.random() < args.corrupt_crc:
            b = bytearray(pkt)
            b[rng.randrange(0, len(b) - 2)] ^= 0xFF
            pkt = bytes(b)
            counts["corrupted"] += 1
        if t_scn >= 0 and args.reorder and held is None and rng.random() < args.reorder:
            held = pkt                      # send after the next one
            counts["reordered"] += 1
            continue
        sock.sendto(pkt, target)
        if held is not None:
            sock.sendto(held, target)
            held = None
        if t_scn >= 0 and args.duplicate and rng.random() < args.duplicate:
            sock.sendto(pkt, target)
            counts["duplicated"] += 1
        counts["sent"] += 1
        sent.append((t_scn, seq - 1, len(tracks)))
        last_sent_t = t_frame

    # keep listening: long enough to see FAILSAFE after a stop, and late replies
    tail = 0.6 if stop_at is not None else 0.15
    time.sleep(tail)
    events_abs = rx.take()
    events = [(t - t0, st) for t, st in events_abs]
    last_sent_scn = (last_sent_t - t0) if last_sent_t is not None else None
    return (check(sc, args, events, counts, last_sent_scn), events, rtt_list(events_abs, sent_tx),
            counts, seq)


# ---------------------------------------------------------------- checking

def check(sc, args, events, counts, last_sent_scn):
    """Returns dict(ok, reasons, metrics)."""
    reasons = []
    metrics = {}
    period = 1.0 / args.rate
    lossy = bool(args.drop or args.corrupt_crc or args.reorder or args.burst_loss)
    tol = 2 * period + 0.03 + (3 * period if lossy else 0)

    if not events:
        return dict(ok=False, reasons=["no status packets received - is the RT1170 at %s:%d "
                                       "and replying to this host's :5006?" % (args.target, args.port)],
                    metrics=metrics)

    pre = [st for t, st in events if t < 0]
    if not pre or pre[-1].state != wp.NOMINAL:
        reasons.append("not NOMINAL at end of preroll (got %s)" %
                       (wp.state_name(pre[-1].state) if pre else "nothing"))

    post = [(t, st) for t, st in events if t >= 0]
    # decisions made while frames were still flowing (the FAILSAFE that follows the
    # end of a run is correct behaviour, not a scenario violation)
    live = [(t, st) for t, st in post if last_sent_scn is None or t <= last_sent_scn + period]
    first_seen = {}
    for t, st in live:
        first_seen.setdefault(st.state, t)

    stop_at = args.stop_at
    burst = args.burst_loss
    outage = burst is not None and burst[0] * 1000 > HEARTBEAT_MS
    # With an injected outage, FAILSAFE is expected; don't count it against 'never'.
    never = set(sc.never)
    if stop_at is not None or outage:
        never.discard(wp.FAILSAFE)

    for s in sorted(never):
        if s in first_seen:
            reasons.append("%s seen at t=%.3fs (must never happen)" % (wp.state_name(s), first_seen[s]))

    effective_end = sc.duration if stop_at is None else stop_at
    for state, t_min, t_max in sc.reach:
        if t_min > effective_end:
            continue
        if outage and burst[1] <= t_min <= burst[1] + burst[0] + RECOVER_FRAMES * period + tol:
            continue  # expected transition falls inside the outage/recovery window
        t = first_seen.get(state)
        if t is None:
            reasons.append("never reached %s (expected ~%.2fs)" % (wp.state_name(state), t_min))
        elif not (t_min - tol <= t <= t_max + tol):
            reasons.append("%s at t=%.3fs, expected %.2f..%.2fs (+/-%.0f ms)" % (
                wp.state_name(state), t, t_min, t_max, tol * 1000))

    if sc.threat_id is not None:
        wrong = [st.threat_id for t, st in post
                 if st.state in (wp.CAUTION, wp.WARNING) and st.threat_id != sc.threat_id]
        if wrong:
            reasons.append("threat id %s reported, expected %d" % (sorted(set(wrong)), sc.threat_id))

    if stop_at is not None:
        fs = [t for t, st in post if t >= stop_at - period and st.state == wp.FAILSAFE]
        if not fs:
            reasons.append("no FAILSAFE after sender stopped at t=%.2fs" % stop_at)
        elif last_sent_scn is not None:
            failover_ms = (fs[0] - last_sent_scn) * 1000.0
            metrics["failover_ms"] = failover_ms
            if not (HEARTBEAT_MS - 5 <= failover_ms <= HEARTBEAT_MS + 60):
                reasons.append("failover %.1f ms outside %d..%d ms" % (
                    failover_ms, HEARTBEAT_MS - 5, HEARTBEAT_MS + 60))
        if [st for t, st in post if t >= stop_at and st.state == wp.FAILSAFE and
                not st.fault_flags & wp.FAULT_HEARTBEAT]:
            reasons.append("FAILSAFE status without HEARTBEAT fault flag")
    elif outage:
        b_len, b_at = burst
        fs = [t for t, st in post if b_at <= t and st.state == wp.FAILSAFE]
        if not fs:
            reasons.append("no FAILSAFE during %.0f ms outage at %.2fs" % (b_len * 1000, b_at))
        else:
            metrics["failover_ms"] = (fs[0] - b_at) * 1000.0 + period * 1000.0
        recovered = [t for t, st in post if t > b_at + b_len and st.state != wp.FAILSAFE]
        if not recovered:
            reasons.append("did not recover after outage")
        else:
            metrics["recovery_ms"] = (recovered[0] - (b_at + b_len)) * 1000.0
    elif sc.final is not None:
        # judge the last decision made while frames were still flowing, not the
        # (correct) FAILSAFE that follows once the player stops sending
        last = live[-1][1].state if live else None
        if last != sc.final:
            reasons.append("final state %s, expected %s" % (
                wp.state_name(last) if last is not None else "none", wp.state_name(sc.final)))

    faults = 0
    for t, st in post:
        faults |= st.fault_flags
    metrics["faults"] = faults
    if args.corrupt_crc and counts["corrupted"] and not faults & wp.FAULT_CRC:
        reasons.append("CRC corruption injected but CRC fault never reported")
    if args.duplicate and counts["duplicated"] and not faults & wp.FAULT_SEQ_OLD:
        reasons.append("duplicates injected but SEQ_OLD never reported")
    if args.reorder and counts["reordered"] and not faults & wp.FAULT_SEQ_OLD:
        reasons.append("reordering injected but SEQ_OLD never reported")

    lat = [st.decision_latency_ns / 1000.0 for t, st in events if st.decision_latency_ns]
    if lat:
        metrics["lat_us"] = lat
    return dict(ok=not reasons, reasons=reasons, metrics=metrics)


def rtt_list(events_abs, sent_tx):
    """events_abs: [(t_rx_monotonic, Status)], sent_tx: {seq: tx_time_us} of frames sent in
    this run -> round trips in ms. Only the first reply echoing a frame we sent counts:
    keepalives and bad-frame replies re-echo an older frame (maybe from a previous run)."""
    out = []
    pending = dict(sent_tx)
    for t, st in events_abs:
        if pending.get(st.seq_echo) == st.tx_time_us_echo:
            del pending[st.seq_echo]
            out.append(t * 1000.0 - st.tx_time_us_echo / 1000.0)
    return out


def pctl(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(p / 100.0 * len(xs) + 0.5)) - 1))
    return xs[k]


def fmt_stats(name, xs, unit):
    if not xs:
        return "%s: n=0" % name
    return "%s: n=%d min %.2f mean %.2f p99 %.2f max %.2f %s" % (
        name, len(xs), min(xs), statistics.mean(xs), pctl(xs, 99), max(xs), unit)


# ---------------------------------------------------------------- main

def fault_desc(args):
    parts = []
    if args.drop:
        parts.append("drop %.0f%%" % (args.drop * 100))
    if args.burst_loss:
        parts.append("burst-loss %.0fms@%.2fs" % (args.burst_loss[0] * 1000, args.burst_loss[1]))
    if args.corrupt_crc:
        parts.append("corrupt-crc %.0f%%" % (args.corrupt_crc * 100))
    if args.reorder:
        parts.append("reorder %.0f%%" % (args.reorder * 100))
    if args.duplicate:
        parts.append("duplicate %.0f%%" % (args.duplicate * 100))
    if args.stop_at is not None:
        parts.append("stop-at %.2fs" % args.stop_at)
    if args.rate != 30:
        parts.append("rate %g Hz" % args.rate)
    return ", ".join(parts) or "none"


def make_socket(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.bind, wp.PORT_STATUS))
    except OSError as e:
        sys.exit("cannot bind UDP :%d (%s). Is perception.py or another player running?" %
                 (wp.PORT_STATUS, e))
    sock.settimeout(0.2)
    return sock


class RunArgs(object):
    """Per-run copy of the CLI args, so --matrix can vary the fault settings."""
    def __init__(self, base, **over):
        self.__dict__.update(vars(base))
        self.__dict__.update(over)


def matrix_cases(base):
    rows = []
    for name in ["approach", "stationary", "cut_in", "lateral_pass", "follow", "multi"]:
        rows.append((name, RunArgs(base)))
    rows += [
        ("approach", RunArgs(base, drop=0.1)),
        ("approach", RunArgs(base, corrupt_crc=0.05)),
        ("approach", RunArgs(base, reorder=0.05)),
        ("approach", RunArgs(base, duplicate=0.05)),
        ("approach", RunArgs(base, rate=15.0)),
        ("follow", RunArgs(base, burst_loss=(0.2, 1.0))),
        ("follow", RunArgs(base, burst_loss=(0.06, 1.0))),   # shorter than heartbeat: no FAILSAFE
        ("follow", RunArgs(base, stop_at=2.0)),
    ]
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="192.168.10.2", help="RT1170 IP (default 192.168.10.2)")
    ap.add_argument("--port", type=int, default=wp.PORT_PERCEPTION)
    ap.add_argument("--bind", default="0.0.0.0", help="local address for :5006")
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="approach")
    ap.add_argument("--matrix", action="store_true", help="run every scenario + fault variant")
    ap.add_argument("--rate", type=float, default=30.0, help="frames per second")
    ap.add_argument("--drop", type=float, default=0.0, help="random frame loss probability")
    ap.add_argument("--burst-loss", type=parse_burst, default=None, help="e.g. 200ms@1s")
    ap.add_argument("--corrupt-crc", type=float, default=0.0, help="probability of a flipped byte")
    ap.add_argument("--reorder", type=float, default=0.0, help="probability of swapping with the next frame")
    ap.add_argument("--duplicate", type=float, default=0.0, help="probability of sending a frame twice")
    ap.add_argument("--stop-at", type=parse_duration, default=None,
                    help="stop sending at this scenario time (simulates a Jetson crash)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--csv", help="append every status packet to this CSV")
    ap.add_argument("--md", help="write the result table as markdown (with --matrix)")
    ap.add_argument("--mirror", default=None, metavar="HOST:PORT",
                    help="forward every status packet here, e.g. 127.0.0.1:5007 for dashboard.py")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every state transition")
    args = ap.parse_args()
    for name in ("drop", "corrupt_crc", "reorder", "duplicate"):
        v = getattr(args, name)
        if not 0.0 <= v <= 1.0:
            ap.error("--%s must be a probability 0..1" % name.replace("_", "-"))

    sock = make_socket(args)
    mirror = None
    if args.mirror:
        host, _, port = args.mirror.rpartition(":")
        mirror = (host or "127.0.0.1", int(port))
    rx = Receiver(sock, mirror)
    rx.start()
    rng = random.Random(args.seed)
    seq = rng.randrange(0, 1 << 16)

    cases = matrix_cases(args) if args.matrix else [(args.scenario, args)] * args.repeat
    results = []
    all_lat, all_rtt, all_failover = [], [], []
    csv_file = open(args.csv, "a", newline="") if args.csv else None
    writer = csv.writer(csv_file) if csv_file else None
    if writer and csv_file.tell() == 0:
        writer.writerow(["run", "scenario", "faults", "t_s", "state", "seq_echo", "ttc_ms",
                         "threat_id", "fault_flags", "decision_latency_ns", "imu_ax_mg",
                         "imu_ay_mg", "maneuver"])

    try:
        for run_i, (name, ra) in enumerate(cases):
            sc = SCENARIOS[name]
            res, events, rtts, counts, seq = run_once(sc, ra, sock, rx, rng, seq)
            lat = res["metrics"].get("lat_us", [])
            all_lat += lat
            all_rtt += rtts
            if "failover_ms" in res["metrics"]:
                all_failover.append(res["metrics"]["failover_ms"])
            results.append((name, fault_desc(ra), res, counts))

            tag = "PASS" if res["ok"] else "FAIL"
            extra = []
            if "failover_ms" in res["metrics"]:
                extra.append("failover %.1f ms" % res["metrics"]["failover_ms"])
            if "recovery_ms" in res["metrics"]:
                extra.append("recovered %.0f ms after outage" % res["metrics"]["recovery_ms"])
            if lat:
                extra.append("RT1170 latency mean %.2f us max %.2f us" % (statistics.mean(lat), max(lat)))
            print("%s  %-12s faults: %-28s %s" % (tag, name, fault_desc(ra), "; ".join(extra)))
            for r in res["reasons"]:
                print("        - " + r)
            if args.verbose:
                prev = None
                for t, st in events:
                    if st.state != prev:
                        print("        t=%+.3fs %-9s ttc=%s id=%s faults=%s" % (
                            t, wp.state_name(st.state),
                            st.ttc_ms if st.ttc_ms != wp.TTC_NONE else "-",
                            st.threat_id if st.threat_id != wp.THREAT_NONE else "-",
                            wp.fault_str(st.fault_flags)))
                        prev = st.state
            if writer:
                for t, st in events:
                    writer.writerow([run_i, name, fault_desc(ra), "%.4f" % t, wp.state_name(st.state),
                                     st.seq_echo, st.ttc_ms, st.threat_id, st.fault_flags,
                                     st.decision_latency_ns, st.imu_ax_mg, st.imu_ay_mg, st.maneuver])
            if run_i + 1 < len(cases) and (ra.stop_at is not None):
                time.sleep(0.2)
    finally:
        rx.running = False
        if csv_file:
            csv_file.close()

    n_pass = sum(1 for r in results if r[2]["ok"])
    print()
    print("%d/%d passed" % (n_pass, len(results)))
    print(fmt_stats("RT1170 decision latency (self-reported)", all_lat, "us"))
    print(fmt_stats("round trip host->RT1170->host", all_rtt, "ms"))
    if all_failover:
        print(fmt_stats("failover (last frame sent -> FAILSAFE status received)", all_failover, "ms"))

    if args.md:
        with open(args.md, "w") as f:
            f.write("| # | Scenario | Injected faults | Result | Notes |\n|---|---|---|---|---|\n")
            for i, (name, faults, res, counts) in enumerate(results, 1):
                notes = []
                m = res["metrics"]
                if "failover_ms" in m:
                    notes.append("failover %.1f ms" % m["failover_ms"])
                if "recovery_ms" in m:
                    notes.append("recovery %.0f ms" % m["recovery_ms"])
                if m.get("faults"):
                    notes.append("faults reported: " + wp.fault_str(m["faults"]))
                notes += res["reasons"]
                f.write("| %d | %s | %s | %s | %s |\n" % (
                    i, name, faults, "PASS" if res["ok"] else "**FAIL**", "; ".join(notes)))
        print("wrote " + args.md)

    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == "__main__":
    main()
