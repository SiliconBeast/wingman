#!/usr/bin/env python3
"""Live plot of the RT1170's verdicts: state, TTC, decision latency (for video footage).

Listens for status packets on UDP :5007. Start the sender with a mirror:
  python3 tools/dashboard.py &
  python3 tools/scenario_player.py --scenario approach --mirror 127.0.0.1:5007
  python3 tx2/perception.py ... --mirror 127.0.0.1:5007

Or listen on :5006 directly when nothing else is (you only see what arrives):
  python3 tools/dashboard.py --port 5006

Headless capture for the write-up (collect N seconds, save a PNG, exit):
  python3 tools/dashboard.py --snapshot docs/approach.png --seconds 8

Needs matplotlib (pip install matplotlib).
"""
import argparse
import collections
import os
import socket
import sys
import time

import matplotlib

if "--snapshot" in sys.argv:
    matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.animation import FuncAnimation  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tx2"))
import wingman_proto as wp  # noqa: E402

STATE_COLORS = {wp.BOOT: "#888888", wp.WAIT_LINK: "#6c8ebf", wp.NOMINAL: "#2e9d4a",
                wp.CAUTION: "#e0a100", wp.WARNING: "#d62728", wp.FAILSAFE: "#7a0010"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=5007)
    ap.add_argument("--window", type=float, default=20.0, help="seconds of history shown")
    ap.add_argument("--snapshot", metavar="PNG", help="collect --seconds of data, save a PNG, exit")
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", args.port))
    sock.setblocking(False)

    maxlen = 5000
    t_hist = collections.deque(maxlen=maxlen)
    st_hist = collections.deque(maxlen=maxlen)
    ttc_hist = collections.deque(maxlen=maxlen)
    lat_t = collections.deque(maxlen=maxlen)
    lat_hist = collections.deque(maxlen=maxlen)
    last = {"st": None, "n": 0}
    t0 = time.monotonic()

    fig, (ax_st, ax_ttc, ax_lat) = plt.subplots(3, 1, sharex=True, figsize=(10, 7))
    fig.canvas.manager.set_window_title("Wingman - RT1170 safety supervisor")
    title = fig.suptitle("waiting for status packets on :%d ..." % args.port, fontsize=14)

    ax_st.set_yticks(range(len(wp.STATE_NAMES)))
    ax_st.set_yticklabels(wp.STATE_NAMES)
    ax_st.set_ylim(-0.5, len(wp.STATE_NAMES) - 0.5)
    ax_st.set_ylabel("state")
    (st_line,) = ax_st.step([], [], where="post", color="#333333")
    st_scatter = ax_st.scatter([], [], s=8)

    ax_ttc.set_ylabel("TTC (s)")
    ax_ttc.set_ylim(0, 5)
    ax_ttc.axhline(2.5, color=STATE_COLORS[wp.CAUTION], ls="--", lw=1)
    ax_ttc.axhline(1.2, color=STATE_COLORS[wp.WARNING], ls="--", lw=1)
    (ttc_line,) = ax_ttc.plot([], [], ".", ms=3, color="#1f77b4")

    ax_lat.set_ylabel("decision latency (us)")
    ax_lat.set_xlabel("time (s)")
    (lat_line,) = ax_lat.plot([], [], ".", ms=3, color="#9467bd")

    def poll():
        while True:
            try:
                data, _ = sock.recvfrom(256)
            except (BlockingIOError, OSError):
                return
            s = wp.unpack_status(data)
            if s is None:
                continue
            t = time.monotonic() - t0
            t_hist.append(t)
            st_hist.append(s.state)
            ttc_hist.append(s.ttc_ms / 1000.0 if s.ttc_ms >= 0 else float("nan"))
            if s.decision_latency_ns:
                lat_t.append(t)
                lat_hist.append(s.decision_latency_ns / 1000.0)
            last["st"] = s
            last["n"] += 1

    def update(_frame):
        poll()
        if not t_hist:
            return st_line, ttc_line, lat_line, title
        now = time.monotonic() - t0
        lo = max(0.0, now - args.window)
        ts, ss = list(t_hist), list(st_hist)
        st_line.set_data(ts, ss)
        st_scatter.set_offsets(list(zip(ts, ss)))
        st_scatter.set_color([STATE_COLORS.get(x, "#000000") for x in ss])
        ttc_line.set_data(ts, list(ttc_hist))
        lat_line.set_data(list(lat_t), list(lat_hist))
        ax_lat.set_xlim(lo, max(now, lo + 1))
        vis = [v for tt, v in zip(lat_t, lat_hist) if tt >= lo]
        if vis:
            ax_lat.set_ylim(0, max(vis) * 1.3 + 1)
        s = last["st"]
        ttc = "%.2f s" % (s.ttc_ms / 1000.0) if s.ttc_ms >= 0 else "-"
        title.set_text("RT1170: %s   TTC %s   threat %s   faults %s   latency %s" % (
            wp.state_name(s.state), ttc, s.threat_id if s.threat_id != wp.THREAT_NONE else "-",
            wp.fault_str(s.fault_flags),
            "%.1f us" % (s.decision_latency_ns / 1000.0) if s.decision_latency_ns else "-"))
        title.set_color(STATE_COLORS.get(s.state, "#000000"))
        return st_line, ttc_line, lat_line, title

    if args.snapshot:
        t_stop = time.monotonic() + args.seconds
        while time.monotonic() < t_stop:
            poll()
            time.sleep(0.01)
        args.window = args.seconds
        update(None)
        plt.tight_layout()
        fig.savefig(args.snapshot, dpi=120)
        print("wrote %s (%d status packets)" % (args.snapshot, last["n"]))
        return

    fig.wingman_anim = FuncAnimation(fig, update, interval=50, blit=False, cache_frame_data=False)
    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
